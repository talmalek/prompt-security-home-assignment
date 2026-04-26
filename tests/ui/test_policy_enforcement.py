"""GenAI access policy: allow chatgpt.com, block gemini.google.com (Prompt Security extension)."""

from __future__ import annotations

import allure
import pytest
from playwright.async_api import expect

from tests.pages.chatgpt_page import ChatGPTPage
from tests.pages.gemini_page import GeminiPage
from utils.logger import logger
from utils.soft_assert import SoftAssert


@allure.epic("Prompt Security")
@allure.feature("Access Policy Enforcement")
@pytest.mark.ui
@pytest.mark.smoke
@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.usefixtures("browser_context")
class TestGenAiPolicyEnforcement:
    """Black-box validation with extension loaded via persistent Chromium context."""

    def setup_method(self) -> None:
        self.checker = SoftAssert()

    def teardown_method(self) -> None:
        self.checker.assert_all()

    @allure.title("ChatGPT — allowed site is usable")
    @allure.description(
        "With the extension configured, chatgpt.com should load and accept a prompt; "
        "an assistant reply surface appears (content not asserted — anti-flake)."
    )
    async def test_chatgpt_is_allowed(self) -> None:
        chat = ChatGPTPage(self.page)
        with allure.step("Open ChatGPT"):
            await chat.navigate()
            await chat.dismiss_popups()

        with self.checker.step("Host is chatgpt"):
            host = chat.page.url.lower()
            self.checker.check_true(
                "chatgpt.com" in host or "chat.openai.com" in host, f"Unexpected URL: {chat.page.url}"
            )

        with self.checker.step("Prompt input visible and enabled"):
            await expect(chat.prompt_input).to_be_visible(timeout=90_000)
            self.checker.check_true(await chat.prompt_input.is_enabled(), "Prompt input should be enabled when allowed")

        with allure.step("Send minimal prompt"):
            await chat.send_prompt("Reply with the single word: pong")

        with self.checker.step("Assistant response appears"):
            got = await chat.is_response_received()
            self.checker.check_true(got, "Expected an assistant message after prompt on allowed site")

        logger.info("ChatGPT allowed flow completed")

    @allure.title("Gemini — blocked site shows enforcement")
    @allure.description(
        "Adaptive detection: block page / extension copy / disabled input / no model response after prompt."
    )
    async def test_gemini_is_blocked(self) -> None:
        gem = GeminiPage(self.page)
        with allure.step("Open Gemini"):
            await gem.navigate()

        with allure.step("Assess block patterns"):
            evidence = await gem.assess_block()

        assert evidence is not None, (
            f"Expected Gemini to be blocked by policy; no block heuristics matched. URL was: {gem.page.url}"
        )
        allure.attach(
            body=f"{evidence.reason}: {evidence.detail}",
            name="block_evidence",
            attachment_type=allure.attachment_type.TEXT,
        )
        logger.info("Gemini block asserted", reason=evidence.reason, detail=evidence.detail)
