"""Download Prompt Security extension CRX from Chrome Web Store and unpack to extension/."""

from __future__ import annotations

import argparse
import io
import zipfile
from pathlib import Path

import httpx

# Chrome Web Store listing: Prompt Security Browser Extension
DEFAULT_EXTENSION_ID = "iidnankcocecmgpcafggbgbmkbcldmno"
CRX_URL_TEMPLATE = (
    "https://clients2.google.com/service/update2/crx"
    "?response=redirect&os=linux&arch=x86-64&prod=chromiumcrx&prodchannel=unknown"
    "&prodversion=120.0.0.0&acceptformat=crx2,crx3&x=id%3D{id}%26uc"
)
ZIP_MAGIC = b"PK\x03\x04"


def _find_zip_start(crx_bytes: bytes) -> int:
    """Locate embedded ZIP inside CRX2/CRX3 (scan for PK magic after CRX header)."""
    start = crx_bytes.find(ZIP_MAGIC, 0)
    if start == -1:
        msg = "No ZIP payload (PK header) found in CRX"
        raise ValueError(msg)
    return start


def fetch_and_unpack(
    *,
    extension_id: str = DEFAULT_EXTENSION_ID,
    dest_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Download CRX, strip header, unzip into dest_dir. Returns absolute dest_dir."""
    repo_root = Path(__file__).resolve().parent.parent
    out = (dest_dir or (repo_root / "extension")).resolve()
    manifest = out / "manifest.json"
    if manifest.is_file() and not force:
        return out

    if out.exists() and force:
        import shutil

        shutil.rmtree(out)

    out.mkdir(parents=True, exist_ok=True)
    url = CRX_URL_TEMPLATE.format(id=extension_id)
    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
        response = client.get(url)
        response.raise_for_status()
        crx_bytes = response.content

    zip_start = _find_zip_start(crx_bytes)
    zip_bytes = crx_bytes[zip_start:]
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(out)

    if not manifest.is_file():
        msg = f"Unpack did not produce manifest.json under {out}"
        raise RuntimeError(msg)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and unpack Chrome extension CRX")
    parser.add_argument("--force", action="store_true", help="Re-download even if extension/manifest.json exists")
    parser.add_argument("--id", default=DEFAULT_EXTENSION_ID, help="Chrome Web Store extension id")
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
