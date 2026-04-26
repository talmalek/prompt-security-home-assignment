"""Base helpers for page objects (composition over inheritance)."""

from playwright.async_api import Page


class BasePage:
    """Minimal base: holds the Playwright ``Page`` for page objects."""

    def __init__(self, page: Page) -> None:
        self.page = page
