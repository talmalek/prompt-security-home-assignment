"""Web GenAI app page object — navigates to a hosted GenAI site and reports its post-load state.

A single, generic page object covers all three GenAI hosts under test
(ChatGPT, Gemini, Claude AI) because the policy assertions are uniform:

    * **Allowed / no extension installed** — final URL must be a normal web origin
      (i.e. ``scheme != "chrome-extension"``). The exact host can vary across
      vendor login redirects (e.g. ``accounts.google.com``), so we only forbid
      the extension-served origin and capture the actual landing URL for review.

    * **Blocked by extension** — final URL must be the extension's block overlay
      (``chrome-extension://<runtime-id>/html/pageOverlay.html?type=blockPage&...``)
      with ``domain=<expected-block-domain>``. DOM markers (``Access Denied``
      title + ``Powered by:`` footer link to ``prompt.security``) are collected
      best-effort and surfaced as Allure detail.

This collapses what would otherwise be three near-identical site-specific page
objects into one parameterised class — see ``CHATGPT``/``GEMINI``/``CLAUDE`` for
the three site descriptors.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from tests.pages.base_page import BasePage
from utils.logger import logger


@dataclass(frozen=True)
class GenAiAppSite:
    """Static descriptor for a GenAI web app under test."""

    name: str
    url: str
    block_domain: str  # value the extension overlay carries in its `?domain=` query param


CHATGPT = GenAiAppSite(name="ChatGPT", url="https://chatgpt.com/", block_domain="chatgpt.com")
GEMINI = GenAiAppSite(name="Gemini", url="https://gemini.google.com/", block_domain="gemini.google.com")
CLAUDE = GenAiAppSite(name="Claude AI", url="https://claude.ai/", block_domain="claude.ai")


_OVERLAY_PATH = "/html/pageOverlay.html"
_EXPECTED_BLOCK_TYPE = "blockPage"


class WebGenAiAppPage(BasePage):
    """Navigate + describe-state helper for a single GenAI host."""

    def __init__(self, page: Page, site: GenAiAppSite) -> None:
        super().__init__(page)
        self.site = site

    async def navigate(self) -> None:
        """Open the site; tolerate the extension delivering a redirect as ``net::ERR_ABORTED``."""
        logger.info("Navigating to GenAI app", app=self.site.name, url=self.site.url)
        try:
            await self.page.goto(self.site.url, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightError as exc:
            logger.info(
                "Navigation raised — final URL will still be inspected",
                app=self.site.name,
                error=str(exc).splitlines()[0][:200],
            )
        try:
            await self.page.wait_for_load_state("load", timeout=10_000)
        except PlaywrightError:
            pass

    async def assess_state(self, *, settle_seconds: float = 1.5) -> dict[str, Any]:
        """Snapshot the post-navigation state.

        Always returns a dict; the ``overlay`` key is only present when we landed
        on the extension's block overlay.
        """
        await asyncio.sleep(settle_seconds)
        url = self.page.url
        parsed = urlparse(url)
        snapshot: dict[str, Any] = {
            "app": self.site.name,
            "target_url": self.site.url,
            "final_url": url,
            "scheme": parsed.scheme,
            "host": parsed.netloc,
        }
        if parsed.scheme == "chrome-extension" and parsed.path.endswith(_OVERLAY_PATH):
            qs = parse_qs(parsed.query)
            overlay: dict[str, Any] = {
                "extension_id": parsed.netloc,
                "type": qs.get("type", [""])[0],
                "domain": qs.get("domain", [""])[0],
                "original_url": qs.get("originalUrl", [""])[0],
            }
            try:
                title_loc = self.page.locator("#title-text")
                if await title_loc.count():
                    overlay["title_text"] = (await title_loc.first.inner_text()).strip()
            except PlaywrightError:
                pass
            try:
                msg_loc = self.page.locator("#message-title")
                if await msg_loc.count():
                    overlay["message_title"] = (await msg_loc.first.inner_text()).strip()
            except PlaywrightError:
                pass
            try:
                link_loc = self.page.locator("a[href*='prompt.security']")
                if await link_loc.count():
                    overlay["powered_by"] = await link_loc.first.get_attribute("href") or ""
            except PlaywrightError:
                pass
            snapshot["overlay"] = overlay
        return snapshot
