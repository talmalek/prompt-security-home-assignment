"""Allure attachments for failures and debugging."""

from __future__ import annotations

import allure


def attach_png_bytes(data: bytes, name: str = "screenshot") -> None:
    """Attach raw PNG bytes to the current Allure test (e.g. from a failure hook)."""
    allure.attach(
        data,
        name=name,
        attachment_type=allure.attachment_type.PNG,
    )


def attach_text(body: str, name: str = "details") -> None:
    allure.attach(body, name=name, attachment_type=allure.attachment_type.TEXT)


def attach_page_source(html: str, name: str = "page_source") -> None:
    allure.attach(html, name=name, attachment_type=allure.attachment_type.HTML)
