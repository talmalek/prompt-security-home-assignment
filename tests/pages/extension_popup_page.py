"""Prompt Security extension popup — API Domain + API Key (html/popup.html)."""

from __future__ import annotations

from playwright.async_api import Page, expect
from pydantic import SecretStr

from tests.pages.base_page import BasePage
from utils.logger import logger


class ExtensionPopupPage(BasePage):
    """Configure extension via its toolbar popup."""

    POPUP_PATH = "html/popup.html"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    @property
    def api_domain_input(self):
        return self.page.locator("#apiDomain")

    @property
    def api_key_input(self):
        return self.page.locator("#apiKey")

    @property
    def save_button(self):
        return self.page.locator("#saveButton")

    def popup_url(self, chrome_extension_id: str) -> str:
        return f"chrome-extension://{chrome_extension_id}/{self.POPUP_PATH}"

    async def open(self, chrome_extension_id: str) -> None:
        url = self.popup_url(chrome_extension_id)
        logger.info("Opening extension popup", url_host=f"chrome-extension://{chrome_extension_id}")
        await self.page.goto(url, wait_until="domcontentloaded")

    async def set_api_domain(self, value: str) -> None:
        await expect(self.api_domain_input).to_be_visible(timeout=15_000)
        await self.api_domain_input.fill(value)

    async def set_api_key(self, secret: SecretStr) -> None:
        await expect(self.api_key_input).to_be_visible(timeout=15_000)
        await self.api_key_input.fill(secret.get_secret_value())

    async def save(self) -> None:
        await expect(self.save_button).to_be_enabled()
        await self.save_button.click()

    async def configure(self, chrome_extension_id: str, domain: str, api_key: SecretStr) -> None:
        """Open the popup, fill credentials, click Save, and verify the domain persisted.

        Verifying the round-trip here (rather than only in the calling fixture) means
        every caller — production, demo, future tenants — gets the same fail-fast
        behaviour when the popup form selectors drift in a new extension release. A
        silent ``Save`` no-op would otherwise surface 30+ seconds later as an opaque
        "expected block, got allow" failure with no clue that the popup itself was
        the root cause.
        """
        await self.open(chrome_extension_id)
        await self.set_api_domain(domain)
        await self.set_api_key(api_key)
        await self.save()
        saved_domain = await self.read_api_domain()
        if saved_domain.strip() != domain.strip():
            msg = (
                "Extension popup did not persist API domain after Save "
                f"(expected={domain!r}, got={saved_domain!r}). "
                "The popup form selectors in tests/pages/extension_popup_page.py "
                "likely changed in a new extension release."
            )
            logger.error(msg)
            raise RuntimeError(msg)

    async def read_api_domain(self) -> str:
        await expect(self.api_domain_input).to_be_visible(timeout=10_000)
        return await self.api_domain_input.input_value()
