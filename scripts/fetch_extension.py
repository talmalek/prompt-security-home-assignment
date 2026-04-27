"""Download Prompt Security extension CRX from Chrome Web Store and unpack to extension/.

Hardening over the original byte-scan implementation:

* **Tenacity-backed retries** — the Chrome Web Store update endpoint is
  externally hosted and occasionally serves transient 5xx / DNS / TLS errors.
  We retry up to 5 times with exponential-jitter backoff before giving up.
* **Proper CRX header parsing** — instead of scanning bytes for the ZIP magic
  (which can match inside the CRX3 public-key blob), we parse the real CRX2 /
  CRX3 header and skip exactly the right number of bytes.
  Format reference: https://chromium.googlesource.com/chromium/src/+/main/components/crx_file/
* **Post-unpack validation** — after extraction we parse ``manifest.json`` and
  fail fast if it's malformed, has the wrong ``manifest_version``, or
  references files that aren't on disk. The popup-configuration step then
  can't be the first place a broken download surfaces.
* **Externalised URL template + prodversion** — both live in ``config.settings``
  (``CHROME_STORE_CRX_URL_TEMPLATE`` / ``CHROME_STORE_CRX_PRODVERSION``) so
  a future endpoint rotation is a one-line env change.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import struct
import zipfile
from pathlib import Path

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from config.settings import settings
from utils.logger import logger

CRX_MAGIC = b"Cr24"


def _crx_payload(crx_bytes: bytes) -> bytes:
    """Strip the CRX2 or CRX3 header and return the embedded ZIP payload bytes.

    Replaces the previous ``find(b"PK\\x03\\x04")`` byte-scan, which could match
    arbitrary bytes inside a CRX3 public-key / signature blob.

    CRX3 layout (little-endian)::

        magic        4 bytes   = b"Cr24"
        version      uint32    = 3
        header_size  uint32    = N
        header       N bytes   (protobuf with public keys + signatures)
        zip          rest      (the actual extension bundle)

    CRX2 layout (legacy)::

        magic        4 bytes   = b"Cr24"
        version      uint32    = 2
        pubkey_len   uint32    = K
        sig_len      uint32    = S
        pubkey       K bytes
        signature    S bytes
        zip          rest
    """
    if len(crx_bytes) < 16 or crx_bytes[:4] != CRX_MAGIC:
        msg = f"Not a CRX file (missing 'Cr24' magic, first 4 bytes = {crx_bytes[:4]!r})"
        raise ValueError(msg)
    (version,) = struct.unpack("<I", crx_bytes[4:8])
    if version == 3:
        (header_size,) = struct.unpack("<I", crx_bytes[8:12])
        offset = 12 + header_size
    elif version == 2:
        (pubkey_len, sig_len) = struct.unpack("<II", crx_bytes[8:16])
        offset = 16 + pubkey_len + sig_len
    else:
        msg = f"Unsupported CRX version {version} (only CRX2 and CRX3 are recognised)"
        raise ValueError(msg)
    if offset >= len(crx_bytes):
        msg = f"CRX header claims length {offset} bytes but file is only {len(crx_bytes)} bytes"
        raise ValueError(msg)
    return crx_bytes[offset:]


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential_jitter(initial=1, max=15),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.RequestError)),
    reraise=True,
)
def _download_crx(url: str) -> bytes:
    """GET the CRX URL with retries on transient network / 5xx errors."""
    logger.info("Downloading CRX", url_host=httpx.URL(url).host)
    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content


def _validate_unpacked_extension(out: Path) -> dict:
    """Parse ``manifest.json`` and verify referenced files exist on disk.

    Returns the parsed manifest. Raises ``RuntimeError`` if the unpack produced
    something the test fixture won't be able to use — making the failure
    visible *here* rather than 30 s later inside the popup-configuration step.
    """
    manifest_path = out / "manifest.json"
    if not manifest_path.is_file():
        msg = f"Unpack did not produce manifest.json under {out}"
        raise RuntimeError(msg)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"manifest.json is not valid JSON: {exc}"
        raise RuntimeError(msg) from exc

    if manifest.get("manifest_version") != 3:
        msg = (
            f"Expected MV3 extension, got manifest_version={manifest.get('manifest_version')!r}. "
            "The fixture is built for the MV3 service-worker pattern."
        )
        raise RuntimeError(msg)
    if not manifest.get("version"):
        msg = "manifest.json is missing a non-empty 'version' field"
        raise RuntimeError(msg)

    referenced: list[str] = []
    sw = manifest.get("background", {}).get("service_worker")
    if sw:
        referenced.append(sw)
    action_popup = manifest.get("action", {}).get("default_popup")
    if action_popup:
        referenced.append(action_popup)
    for cs in manifest.get("content_scripts", []) or []:
        for js in cs.get("js", []) or []:
            referenced.append(js)
        for css in cs.get("css", []) or []:
            referenced.append(css)

    missing = [r for r in referenced if not (out / r).is_file()]
    if missing:
        msg = (
            "Unpacked extension is missing files referenced by manifest.json: "
            f"{missing!r}. The CRX may be corrupt or partially extracted."
        )
        raise RuntimeError(msg)

    return manifest


def fetch_and_unpack(
    *,
    extension_id: str | None = None,
    dest_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Download CRX, strip header, unzip into dest_dir, validate manifest.

    Returns absolute path to ``dest_dir``. On success the directory contains
    a fully-extracted, manifest-validated MV3 extension.
    """
    repo_root = Path(__file__).resolve().parent.parent
    out = (dest_dir or (repo_root / "extension")).resolve()
    manifest = out / "manifest.json"
    if manifest.is_file() and not force:
        return out

    if out.exists() and force:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    ext_id = extension_id or settings.extension.chrome_store_extension_id
    url = settings.extension.crx_url_template.format(
        id=ext_id,
        prodversion=settings.extension.crx_prodversion,
    )

    try:
        crx_bytes = _download_crx(url)
    except RetryError as exc:
        msg = f"CRX download failed after retries: {exc.last_attempt.exception()!r}"
        raise RuntimeError(msg) from exc

    zip_bytes = _crx_payload(crx_bytes)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(out)

    manifest_data = _validate_unpacked_extension(out)
    logger.info(
        "CRX unpacked + validated",
        version=manifest_data.get("version"),
        manifest_version=manifest_data.get("manifest_version"),
        dest_dir=str(out),
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and unpack Chrome extension CRX")
    parser.add_argument("--force", action="store_true", help="Re-download even if extension/manifest.json exists")
    parser.add_argument(
        "--id",
        default=None,
        help="Chrome Web Store extension id (default: from settings.extension.chrome_store_extension_id)",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Output directory (default: <repo>/extension)",
    )
    args = parser.parse_args()
    path = fetch_and_unpack(extension_id=args.id, dest_dir=args.dest, force=args.force)
    print(path)


if __name__ == "__main__":
    main()
