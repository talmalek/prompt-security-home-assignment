"""Google Gemini — negative policy flow (blocked site)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from playwright.async_api import Page

from tests.pages.base_page import BasePage
from utils.logger import logger


@dataclass(frozen=True)
class BlockEvidence:
    """Why we consider the session blocked (extension or policy layer)."""

    reason: str
    detail: str


class GeminiPage(BasePage):
    """Adaptive block detection: hard block UI, extension modal, or disabled input."""

    GEMINI_URL = "https://gemini.google.com/"

    _BLOCK_TEXT_SNIPPETS = (
        "Access Denied",
        "access denied",
        "Prompt Security",
        "prompt.security",
        "blocked",
    )

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    @property
    def prompt_input(self):
        return (
            self.page.get_by_role("textbox", name="Enter a prompt")
            .or_(self.page.locator("rich-textarea").first)
            .or_(self.page.locator("textarea").first)
        )

    async def navigate(self) -> None:
        logger.info("Navigating to Gemini", url=self.GEMINI_URL)
        await self.page.goto(self.GEMINI_URL, wait_until="domcontentloaded", timeout=90_000)

    async def _evidence_block_url(self) -> BlockEvidence | None:
        url = self.page.url.lower()
        if "prompt" in url and "block" in url:
            return BlockEvidence("redirect_block_url", url)
        return None

    async def _evidence_dom_text(self) -> BlockEvidence | None:
        for snippet in self._BLOCK_TEXT_SNIPPETS:
            loc = self.page.get_by_text(snippet, exact=False)
            try:
                if await loc.first.is_visible(timeout=3000):
                    return BlockEvidence("block_ui_text", snippet)
            except Exception:
                continue
        return None

    async def _evidence_title_access_denied(self) -> BlockEvidence | None:
        title = self.page.locator("#title-text")
        try:
            if await title.is_visible(timeout=2000):
                t = (await title.inner_text()).strip()
                if "access denied" in t.lower():
                    return BlockEvidence("block_modal_title", t)
        except Exception:
            pass
        return None

    async def _evidence_input_disabled_or_missing(self) -> BlockEvidence | None:
        try:
            inp = self.prompt_input
            if await inp.count() == 0:
                return BlockEvidence("input_missing", "No prompt textbox found")
            first = inp.first
            if not await first.is_visible(timeout=5000):
                return BlockEvidence("input_not_visible", "Prompt area not visible")
            if not await first.is_enabled():
                return BlockEvidence("input_disabled", "Prompt textbox disabled")
        except Exception as e:
            return BlockEvidence("input_check_error", str(e))
        return None

    async def send_prompt(self, text: str) -> None:
        inp = self.prompt_input
        if await inp.count() == 0:
            return
        await inp.first.click(timeout=5000)
        await inp.first.fill(text, timeout=5000)
        await inp.first.press("Enter")

    async def _evidence_no_response_after_prompt(self) -> BlockEvidence | None:
        """Functional block: prompt sent but no model output surface."""
        before = await self.page.locator('[data-test-id="model-response"], .model-response-text').count()
        try:
            await self.send_prompt("Say hello in one word.")
        except Exception as e:
            return BlockEvidence("prompt_send_failed", str(e))
        await asyncio.sleep(15)
        after = await self.page.locator('[data-test-id="model-response"], .model-response-text').count()
        if after <= before:
            return BlockEvidence("no_model_response", "No new response surface after prompt")
        return None

    async def assess_block(self) -> BlockEvidence | None:
        """Return first matching block evidence, or None if no block pattern matched."""
        await asyncio.sleep(2)
        for fn in (
            self._evidence_block_url,
            self._evidence_title_access_denied,
            self._evidence_dom_text,
            self._evidence_input_disabled_or_missing,
        ):
            ev = await fn()
            if ev:
                logger.info("Gemini block signal", reason=ev.reason, detail=ev.detail)
                return ev
        return await self._evidence_no_response_after_prompt()
