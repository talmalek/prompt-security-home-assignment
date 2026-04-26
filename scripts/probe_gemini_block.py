"""Diagnostic probe: launch Chromium with the Prompt Security extension, configure popup,
verify chrome.storage state, navigate to Gemini, watch network for backend calls,
and capture how (or whether) the extension blocks.

Run locally with:
    PYTHONPATH=. uv run python scripts/probe_gemini_block.py

Outputs to /tmp/ps-probe/.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from playwright.async_api import async_playwright

from config.settings import settings
from scripts.fetch_extension import fetch_and_unpack
from tests.pages.extension_popup_page import ExtensionPopupPage

OUT = Path("/tmp/ps-probe")
OUT.mkdir(parents=True, exist_ok=True)


async def _resolve_extension_id(context, timeout_s: float = 90.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for sw in context.service_workers:
            if sw.url.startswith("chrome-extension://"):
                return sw.url.replace("chrome-extension://", "").split("/")[0]
        await asyncio.sleep(0.25)
    raise TimeoutError("No extension service worker")


async def _read_chrome_storage(context, ext_id: str) -> dict:
    page = await context.new_page()
    await page.goto(f"chrome-extension://{ext_id}/html/popup.html", wait_until="domcontentloaded")
    storage = await page.evaluate(
        """async () => {
            const local = await new Promise(r => chrome.storage.local.get(null, r));
            const sync = await new Promise(r => chrome.storage.sync.get(null, r));
            return { local, sync };
        }"""
    )
    await page.close()
    return storage


async def main() -> None:
    if settings.extension.api_key is None:
        raise SystemExit("PROMPT_SECURITY_API_KEY missing in .env")

    ext_dir = settings.extension.resolved_extension_dir()
    if not (ext_dir / "manifest.json").is_file():
        fetch_and_unpack(extension_id=settings.extension.chrome_store_extension_id, dest_dir=ext_dir, force=False)
    abs_ext = str(ext_dir.resolve())

    udd = OUT / "user-data"
    udd.mkdir(parents=True, exist_ok=True)

    requests_log: list[dict] = []
    responses_log: list[dict] = []

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(udd),
            headless=False,
            args=[
                f"--disable-extensions-except={abs_ext}",
                f"--load-extension={abs_ext}",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            viewport={"width": 1920, "height": 900},
        )

        def on_request(req):
            url = req.url
            if "prompt.security" in url or "prompt-security" in url:
                requests_log.append({"method": req.method, "url": url, "headers": dict(req.headers)})

        def on_response(resp):
            if "prompt.security" in resp.url or "prompt-security" in resp.url:
                responses_log.append({"status": resp.status, "url": resp.url})

        ctx.on("request", on_request)
        ctx.on("response", on_response)

        ext_id = await _resolve_extension_id(ctx)
        print(f"extension id: {ext_id}")

        page = await ctx.new_page()
        popup = ExtensionPopupPage(page)
        await popup.configure(ext_id, settings.extension.api_domain, settings.extension.api_key)
        print("popup configured. waiting 3s for settings to persist + extension to bootstrap")
        await asyncio.sleep(3)

        storage = await _read_chrome_storage(ctx, ext_id)
        print("chrome.storage state:")
        for area, data in storage.items():
            redacted = {}
            for k, v in (data or {}).items():
                if isinstance(v, str) and len(v) > 80:
                    redacted[k] = f"<{len(v)} chars omitted>"
                elif isinstance(v, str) and ("key" in k.lower() or "token" in k.lower()) and len(v) > 8:
                    redacted[k] = f"<redacted {len(v)} chars>"
                else:
                    redacted[k] = v
            print(f"  {area}: {redacted}")

        await page.goto("https://gemini.google.com/", wait_until="domcontentloaded", timeout=60_000)
        await asyncio.sleep(8)

        print(f"\nrequests to prompt.security so far: {len(requests_log)}")
        for r in requests_log[:5]:
            print(f"  {r['method']} {r['url']}")
        print(f"responses from prompt.security so far: {len(responses_log)}")
        for r in responses_log[:5]:
            print(f"  {r['status']} {r['url']}")

        # Try to send a prompt anyway
        try:
            ta = page.locator("rich-textarea").first
            await ta.click(timeout=5000)
            await page.keyboard.type("Hello, just say the single word: pong", delay=10)
            await page.keyboard.press("Enter")
            await asyncio.sleep(8)
            print(f"\nafter prompt: requests={len(requests_log)} responses={len(responses_log)}")
        except Exception as e:
            print(f"send_prompt error: {e}")

        snap = {
            "url": page.url,
            "ps_modal": await page.locator(".ps-modal").count(),
            "title_text": await page.locator("#title-text").count(),
            "powered_by": await page.locator(".powered-by-text").count(),
            "iframe_count": await page.locator("iframe").count(),
            "storage": storage,
            "ps_requests": requests_log,
            "ps_responses": responses_log,
        }
        (OUT / "snapshot.json").write_text(json.dumps(snap, indent=2, default=str))
        await page.screenshot(path=str(OUT / "gemini_final.png"), full_page=True)

        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
