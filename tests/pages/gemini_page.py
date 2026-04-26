"""Google Gemini — negative policy flow (blocked site).

The Prompt Security extension blocks this site by redirecting the navigation to
its own ``html/pageOverlay.html`` page (``chrome-extension://<runtime-id>/...``)
with ``type=blockPage`` and ``domain=gemini.google.com`` query parameters.

We require this URL signature as the primary signal because it is:

* deterministic — the extension generates it; no third party can spoof a
  ``chrome-extension://`` URL on the user's profile, and
* unique to enforcement — the same URL never appears on a passing run for an
  allowed domain.

DOM markers (``Access Denied`` heading, "Powered by: prompt.security" footer
link) are collected best-effort and attached to ``BlockEvidence.detail`` for
human review in Allure, but they are *not* required for the assertion — the
overlay JS populates them asynchronously and timing-flake on those should not
turn a real block into a test failure.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from tests.pages.base_page import BasePage
from utils.logger import logger


@dataclass(frozen=True)
class BlockEvidence:
    """Why we consider the session blocked by the extension."""

    reason: str
    detail: str


_OVERLAY_PATH = "/html/pageOverlay.html"
_EXPECTED_BLOCK_TYPE = "blockPage"
_EXPECTED_DOMAIN = "gemini.google.com"


class GeminiPage(BasePage):
    """Assert that navigation to gemini.google.com lands on the extension's block overlay."""

    GEMINI_URL = "https://gemini.google.com/"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    async def navigate(self) -> None:
        """Navigate to Gemini; tolerate the redirect being delivered as a net::ERR_ABORTED."""
        logger.info("Navigating to Gemini", url=self.GEMINI_URL)
        try:
            await self.page.goto(self.GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightError as exc:
            logger.info(
                "Gemini navigation raised; will inspect final URL anyway",
                error=str(exc).splitlines()[0][:200],
            )

    async def _wait_for_block_overlay(self, timeout_s: float = 15.0) -> bool:
        """Poll page.url until the extension's pageOverlay is the active document."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            parsed = urlparse(self.page.url)
            if parsed.scheme == "chrome-extension" and parsed.path.endswith(_OVERLAY_PATH):
                qs = parse_qs(parsed.query)
                if qs.get("type", [""])[0] == _EXPECTED_BLOCK_TYPE:
                    return True
            await asyncio.sleep(0.25)
        return False

    async def _collect_dom_markers(self) -> tuple[str, str]:
        """Read overlay title text and 'Powered by' link (best effort)."""
        title_text = ""
        powered_by = ""
        try:
            title_loc = self.page.locator("#title-text")
            if await title_loc.count():
                title_text = (await title_loc.first.inner_text()).strip()
        except PlaywrightError:
            pass
        try:
            link_loc = self.page.locator("a[href*='prompt.security']")
            if await link_loc.count():
                powered_by = await link_loc.first.get_attribute("href") or ""
        except PlaywrightError:
            pass
        return title_text, powered_by

    async def assess_block(self) -> BlockEvidence | None:
        """Return extension-attributable block evidence, or ``None`` if Gemini loaded normally."""
        if not await self._wait_for_block_overlay():
            return None

        parsed = urlparse(self.page.url)
        qs = parse_qs(parsed.query)
        ext_id = parsed.netloc
        block_type = qs.get("type", [""])[0]
        domain = qs.get("domain", [""])[0]
        original_url = qs.get("originalUrl", [""])[0]

        title_text, powered_by_href = await self._collect_dom_markers()
        detail_parts = [
            f"extension_id={ext_id}",
            f"type={block_type}",
            f"domain={domain}",
        ]
        if original_url:
            detail_parts.append(f"originalUrl={original_url}")
        if title_text:
            detail_parts.append(f"title={title_text!r}")
        if powered_by_href:
            detail_parts.append(f"powered_by={powered_by_href}")
        if domain != _EXPECTED_DOMAIN:
            logger.warning("Block overlay domain mismatch", expected=_EXPECTED_DOMAIN, got=domain)

        return BlockEvidence(reason="extension_overlay_block", detail=" | ".join(detail_parts))
