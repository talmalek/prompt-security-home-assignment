"""ChatGPT — positive policy flow (allowed site)."""

from __future__ import annotations

from playwright.async_api import Page, expect

from tests.pages.base_page import BasePage
from utils.logger import logger


class ChatGPTPage(BasePage):
    """Black-box checks: page loads, prompt usable, assistant reply appears."""

    CHAT_URL = "https://chatgpt.com/"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    @property
    def prompt_input(self):
        return self.page.locator("#prompt-textarea").or_(self.page.get_by_role("textbox").first)

    @property
    def send_trigger(self):
        return (
            self.page.get_by_role("button", name="Send prompt")
            .or_(self.page.get_by_test_id("send-button"))
            .or_(self.page.locator('button[data-testid="send-button"]'))
        )

    def assistant_messages(self):
        return self.page.locator('[data-message-author-role="assistant"]')

    async def navigate(self) -> None:
        logger.info("Navigating to ChatGPT", url=self.CHAT_URL)
        await self.page.goto(self.CHAT_URL, wait_until="domcontentloaded")

    async def dismiss_popups(self) -> None:
        """Best-effort: cookies, regional banners."""
        for name in ("Accept all", "Accept", "Dismiss", "Not now", "Stay logged out"):
            btn = self.page.get_by_role("button", name=name)
            if await btn.count() > 0 and await btn.first.is_visible():
                await btn.first.click(timeout=2000)
                break

    async def send_prompt(self, text: str) -> None:
        await expect(self.prompt_input).to_be_visible(timeout=60_000)
        await self.prompt_input.click()
        await self.prompt_input.fill(text)
        if await self.send_trigger.count() > 0 and await self.send_trigger.first.is_enabled():
            await self.send_trigger.first.click()
        else:
            await self.prompt_input.press("Enter")

    async def wait_for_response(self, timeout_ms: int = 120_000) -> None:
        before = await self.assistant_messages().count()
        await expect(self.assistant_messages().nth(before)).to_be_visible(timeout=timeout_ms)

    async def is_response_received(self) -> bool:
        try:
            await self.wait_for_response(timeout_ms=120_000)
        except Exception:
            return False
        return True
