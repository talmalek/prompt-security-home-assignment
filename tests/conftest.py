"""Root pytest configuration: extension-backed Playwright, Allure attachments, fixtures."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from config.settings import settings
from tests.pages.extension_popup_page import ExtensionPopupPage
from utils.logger import logger
from utils.reporting import attach_page_source, attach_png_bytes, attach_text
from utils.soft_assert import SoftAssert

pytest_plugins = ["utils.pytest_summary"]

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
SCREENSHOTS_DIR = REPORTS_DIR / "screenshots"
VIDEOS_DIR = REPORTS_DIR / "videos"
TRACES_DIR = REPORTS_DIR / "traces"


def _ensure_report_dirs() -> None:
    for d in (REPORTS_DIR, SCREENSHOTS_DIR, VIDEOS_DIR, TRACES_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _run_async_on_playwright_loop(cls: type | None, coro):
    """Run Playwright async calls on the loop that owns `page` (not `asyncio.run()`'s new loop)."""
    loop = getattr(cls, "_playwright_loop", None) if cls is not None else None
    if loop is None:
        return asyncio.run(coro)
    try:
        if loop.is_running():
            logger.warning("Playwright event loop is still running in makereport; failure screenshot may be unreliable")
            return asyncio.run(coro)
        return loop.run_until_complete(coro)
    except RuntimeError as e:
        logger.warning("Falling back to asyncio.run for failure artifacts", error=str(e))
        return asyncio.run(coro)


async def _detect_chrome_extension_id_from_context(context, timeout_s: float = 90.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for sw in context.service_workers:
            u = sw.url
            if u.startswith("chrome-extension://"):
                ext_id = u.replace("chrome-extension://", "").split("/")[0]
                if ext_id:
                    return ext_id
        await asyncio.sleep(0.25)
    msg = "Timed out waiting for extension service worker (could not resolve chrome-extension id)"
    raise TimeoutError(msg)


@pytest.fixture(scope="session", autouse=True)
def _report_dirs() -> None:
    _ensure_report_dirs()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> None:
    outcome = yield
    rep = outcome.get_result()
    setattr(item, "rep_" + rep.when, rep)
    if call.when != "call" or not rep.failed:
        return
    cls = getattr(item, "cls", None)
    page = getattr(cls, "page", None) if cls else None
    if page is None:
        return

    async def _failure_artifacts() -> bytes | None:
        png = await asyncio.wait_for(
            page.screenshot(full_page=False, timeout=5000),
            timeout=8,
        )
        attach_png_bytes(png, name="failure_screenshot")
        try:
            page_html = await asyncio.wait_for(page.content(), timeout=5)
            attach_page_source(page_html, name="page_source")
        except Exception as e:
            attach_text(str(e), name="page_source_error")
        return png

    try:
        png = _run_async_on_playwright_loop(cls, _failure_artifacts())
        if png:
            extras = getattr(rep, "extras", None)
            if not extras:
                rep.extras = []
                extras = rep.extras
            extras.append(
                SimpleNamespace(
                    name="Failure screenshot",
                    format="png",
                    format_type="image",
                    extension="png",
                    content=png,
                )
            )
            path = SCREENSHOTS_DIR / f"{item.nodeid.replace('::', '_').replace('/', '_')}.png"
            path.write_bytes(png)
            logger.info("Saved failure screenshot", path=str(path))
    except Exception:
        logger.warning("Could not capture failure screenshot / Allure attachments", exc_info=True)


@pytest.fixture(scope="class")
async def browser_context(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """Chromium persistent context with unpacked Prompt Security extension + configured API credentials."""
    _ensure_report_dirs()
    from scripts.fetch_extension import fetch_and_unpack

    if settings.extension.api_key is None:
        pytest.fail(
            "PROMPT_SECURITY_API_KEY is required for extension-backed UI tests. "
            "Set it in `.env` (see `.env.example`) or GitHub Actions secret `PROMPT_SECURITY_API_KEY`."
        )
    api_key = settings.extension.api_key

    ext_dir = settings.extension.resolved_extension_dir()
    if not (ext_dir / "manifest.json").is_file():
        fetch_and_unpack(
            extension_id=settings.extension.chrome_store_extension_id,
            dest_dir=ext_dir,
            force=False,
        )
    abs_ext = str(ext_dir.resolve())
    cls_name = request.cls.__name__ if request.cls else "session"
    udd = REPORTS_DIR / ".user-data" / cls_name
    udd.mkdir(parents=True, exist_ok=True)
    trace_path = TRACES_DIR / f"{cls_name}.zip"

    launch_args = [
        f"--disable-extensions-except={abs_ext}",
        f"--load-extension={abs_ext}",
    ]
    for a in settings.test.browser_args:
        if a not in launch_args:
            launch_args.append(a)

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(udd),
            headless=False,
            slow_mo=settings.test.slow_mo_ms,
            args=launch_args,
            viewport={"width": 1920, "height": 900},
            ignore_https_errors=True,
        )
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        ext_id = await _detect_chrome_extension_id_from_context(context, timeout_s=120.0)
        logger.info("Chrome extension id resolved", extension_id=ext_id)

        page = await context.new_page()
        page.set_default_timeout(settings.test.default_timeout_ms)
        page.set_default_navigation_timeout(120_000)
        request.cls._playwright_loop = asyncio.get_running_loop()

        popup = ExtensionPopupPage(page)
        await popup.configure(ext_id, settings.extension.api_domain, api_key)
        saved_domain = await popup.read_api_domain()
        if saved_domain.strip() != settings.extension.api_domain.strip():
            logger.warning(
                "API domain in popup differs from expected after save",
                expected=settings.extension.api_domain,
                got=saved_domain,
            )

        request.cls.playwright = pw
        request.cls.browser = None
        request.cls.context = context
        request.cls.page = page
        request.cls.chrome_extension_id = ext_id

        yield

        try:
            await context.tracing.stop(path=str(trace_path))
        except Exception:
            logger.warning("Could not stop tracing", exc_info=True)
        await context.close()
        logger.info("Persistent browser context closed", trace_path=str(trace_path))


@pytest.fixture
def soft_assert() -> SoftAssert:
    return SoftAssert()
