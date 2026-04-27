"""Web GenAI app page object — navigates to a hosted GenAI site and reports its post-load state.

A single, generic page object covers all three GenAI hosts under test
(ChatGPT, Gemini, Claude AI) because the policy assertions are uniform:

    * **Allowed / no extension installed** — final URL must be a normal web origin
      (i.e. ``scheme != "chrome-extension"``). The exact host can vary across
      vendor login redirects (e.g. ``accounts.google.com``), so we only forbid
      the extension-served origin and capture the actual landing URL for review.

    * **Blocked by extension** — final URL must be the extension's block overlay
      (``chrome-extension://<runtime-id>/html/pageOverlay.html?type=blockPage&...``)
      with ``domain=<expected-block-domain>``. DOM markers from the
      **v7.1.0 backend-rendered overlay** are collected best-effort and surfaced
      as Allure detail:

      * ``body.ai-site`` ⇒ confirms the page is the backend-rendered overlay
        (the latest extension delivers HTML from the API rather than populating
        the static template).
      * ``h1.title``           ⇒ ``"Access Denied"`` headline.
      * ``p.description``      ⇒ administrator-blocked message text.
      * ``p.guidelines``       ⇒ "for more information, visit your company's
        guidelines" copy with a link.
      * ``.barrier-illustration`` (``#illustrationBlock``) ⇒ the roadblock SVG
        (visual signal of the block UI).
      * ``.powered-by``        ⇒ Prompt Security branding container.

    .. note::
       The extension's overlay rendering has evolved several times.  The
       project tracks **only the latest released version** (see
       ``scripts/fetch_extension.py`` + the ``_ensure_latest_extension``
       autouse fixture in ``tests/conftest.py``), so the assertions above
       describe the v7.1.0+ backend-rendered overlay served when the URL
       query carries ``useBackendHtml=true&popupToken=…``.  Older static
       ``pageOverlay.html`` selectors (``.title-text`` / ``.message-title``)
       are intentionally **not** part of the contract any more.

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
        window we wait *smartly* for the v7.1.0 backend-rendered overlay's
        ``h1.title`` element to carry non-empty text — the bundle fetches the
        backend HTML asynchronously, so this fires the moment the DOM is
        actually ready to be asserted on.  ``settle_seconds`` caps how long
        we'll wait before giving up; the actual elapsed time is typically
        much shorter on a hot run.
        """
        await asyncio.sleep(0.5)
        url = self.page.url
        parsed = urlparse(url)
        is_overlay = parsed.scheme == "chrome-extension" and parsed.path.endswith(_OVERLAY_PATH)

        if is_overlay:
            timeout_ms = max(int(settle_seconds * 1000), 8_000)
            try:
                # The v7.1.0 overlay ships an empty <body> until the bundle
                # fetches the backend HTML and writes it in.  Wait for the
                # injected ``h1.title`` to carry non-empty text so subsequent
                # reads see the hydrated DOM.
                await self.page.wait_for_function(
                    "() => (document.querySelector('h1.title')?.textContent || '').trim().length > 0",
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
                "popup_token_present": bool(qs.get("popupToken", [""])[0]),
            }
            # Body class — v7.1.0 backend-rendered overlay carries ``ai-site``,
            # which is the simplest single signal that we're looking at the
            # latest UI rather than an older static-template render.
            try:
                body_class = await self.page.evaluate("document.body && document.body.className")
                overlay["body_class"] = (body_class or "").strip()
            except PlaywrightError:
                overlay["body_class"] = ""
            # Title headline — the new overlay puts the headline in <h1 class="title">
            # (the static template's .title-text is no longer rendered).
            try:
                title_loc = self.page.locator("h1.title")
                if await title_loc.count():
                    overlay["title"] = (await title_loc.first.inner_text()).strip()
            except PlaywrightError:
                pass
            # Description copy — the new overlay's <p class="description"> carries
            # the administrator-blocked message (replaces the old .message-title).
            try:
                desc_loc = self.page.locator("p.description, .description")
                if await desc_loc.count():
                    overlay["description"] = (await desc_loc.first.inner_text()).strip()
            except PlaywrightError:
                pass
            # Guidelines hint — the new overlay's <p class="guidelines"> carries
            # the "for more information, visit your company's guidelines" copy.
            try:
                guidelines_loc = self.page.locator("p.guidelines, .guidelines")
                if await guidelines_loc.count():
                    overlay["guidelines"] = (await guidelines_loc.first.inner_text()).strip()
            except PlaywrightError:
                pass
            # Roadblock illustration — visual signal that the block UI rendered.
            # ``.barrier-illustration`` is the wrapper, ``#illustrationBlock`` the
            # id; either one matches.
            try:
                illustration_loc = self.page.locator(".barrier-illustration, #illustrationBlock")
                overlay["has_illustration"] = bool(await illustration_loc.count())
            except PlaywrightError:
                overlay["has_illustration"] = False
            # Powered-by branding — still ``.powered-by`` in the new overlay
            # (logo SVG only; no longer carries a "Powered by:" text label).
            try:
                branding_loc = self.page.locator(".powered-by")
                overlay["has_branding"] = bool(await branding_loc.count())
            except PlaywrightError:
                overlay["has_branding"] = False
            snapshot["overlay"] = overlay
        return snapshot
