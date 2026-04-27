"""Web GenAI app page object — navigates to a hosted GenAI site and reports its post-load state.

A single, generic page object covers all three GenAI hosts under test
(ChatGPT, Gemini, Claude AI) because the policy assertions are uniform:

    * **Allowed / no extension installed** — final URL must be a normal web origin
      (i.e. ``scheme != "chrome-extension"``). The exact host can vary across
      vendor login redirects (e.g. ``accounts.google.com``), so we only forbid
      the extension-served origin and capture the actual landing URL for review.

    * **Blocked by extension** — final URL must be the extension's block overlay
      (``chrome-extension://<runtime-id>/html/pageOverlay.html?type=blockPage&...``)
      with ``domain=<expected-block-domain>``. DOM markers from the static
      ``pageOverlay.html`` template (populated at runtime by
      ``bundle/pageOverlay.bundle.js``) are collected best-effort and surfaced
      as Allure detail:

      * ``#title-text`` / ``.title-text`` ⇒ "Access Denied"
      * ``#message-title`` / ``.message-title`` ⇒ administrator-blocked message
      * ``.powered-by`` ⇒ Prompt Security branding container

    .. note::
       The extension's overlay rendering has changed twice during this
       project:

       * **v7.0.49** — static ``pageOverlay.html`` template with id-based
         selectors (``#title-text``, ``#message-title``, ``#poweredBy``).
       * **v7.0.59** — switched to a *backend-rendered* HTML payload (URL
         query carried ``useBackendHtml=true``); selectors became class-based
         (``.title``, ``.description``) and the body gained a ``.ai-site``
         class.
       * **v7.0.591** — reverted to a static ``pageOverlay.html`` template
         populated at runtime by the bundle. ``#title-text`` /
         ``#message-title`` are present but **empty until the JS bundle
         hydrates them** (so the wait below pivots on
         ``document.querySelector('.title-text')?.textContent`` becoming
         non-empty rather than on the element merely being visible). The
         ``body`` no longer carries any policy-specific class.

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

    async def assess_state(self, *, settle_seconds: float = 2.0) -> dict[str, Any]:
        """Snapshot the post-navigation state.

        Always returns a dict; the ``overlay`` key is only present when we landed
        on the extension's block overlay.

        On the block path, instead of sleeping for the full ``settle_seconds``
        window we wait *smartly* for the overlay's ``.title-text`` element to
        be **populated by the JS bundle** (the static template ships it empty).
        ``settle_seconds`` caps how long we'll wait before giving up; the actual
        elapsed time is typically much shorter on a hot run.
        """
        await asyncio.sleep(0.5)
        url = self.page.url
        parsed = urlparse(url)
        is_overlay = parsed.scheme == "chrome-extension" and parsed.path.endswith(_OVERLAY_PATH)

        if is_overlay:
            timeout_ms = max(int(settle_seconds * 1000), 8_000)
            try:
                # The static pageOverlay.html ships .title-text empty; the
                # bundle populates it once it has parsed the URL query and
                # any backend payload.  Wait for non-empty textContent so
                # subsequent reads see the hydrated DOM.
                await self.page.wait_for_function(
                    "() => (document.querySelector('.title-text')?.textContent || '').trim().length > 0",
                    timeout=timeout_ms,
                )
            except PlaywrightError:
                pass
        else:
            await asyncio.sleep(max(0.0, settle_seconds - 0.5))

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
                "can_bypass": qs.get("canBypass", [""])[0],
                "is_enterprise_version": qs.get("isEnterpriseVersion", [""])[0],
                "use_backend_html": qs.get("useBackendHtml", [""])[0],
            }
            try:
                title_loc = self.page.locator(".title-text")
                if await title_loc.count():
                    overlay["title_text"] = (await title_loc.first.inner_text()).strip()
            except PlaywrightError:
                pass
            try:
                msg_loc = self.page.locator(".message-title")
                if await msg_loc.count():
                    overlay["message_title"] = (await msg_loc.first.inner_text()).strip()
            except PlaywrightError:
                pass
            try:
                custom_msg_loc = self.page.locator(".custom-message")
                if await custom_msg_loc.count():
                    overlay["custom_message"] = (await custom_msg_loc.first.inner_text()).strip()
            except PlaywrightError:
                pass
            try:
                guidelines_loc = self.page.locator(".guidelines")
                if await guidelines_loc.count():
                    overlay["guidelines"] = (await guidelines_loc.first.inner_text()).strip()
            except PlaywrightError:
                pass
            try:
                branding_loc = self.page.locator(".powered-by")
                overlay["has_branding"] = bool(await branding_loc.count())
            except PlaywrightError:
                overlay["has_branding"] = False
            try:
                body_class = await self.page.evaluate("document.body && document.body.className")
                overlay["body_class"] = (body_class or "").strip()
            except PlaywrightError:
                pass
            snapshot["overlay"] = overlay
        return snapshot
