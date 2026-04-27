"""Root pytest configuration: persistent-context fixtures (plain / with extension), failure artifacts.

Two **function-scoped** fixtures expose a Chromium ``BrowserContext`` to test
methods via ``self.context``:

* ``browser_context_plain`` — vanilla persistent context; no extension loaded.
* ``browser_context_with_extension`` — persistent context with the unpacked
  Prompt Security extension loaded *and the popup pre-configured* with API
  domain + key from ``settings.extension``.

Both fixtures share their entire lifecycle (launch, tracing, teardown) via
:func:`_persistent_context_lifecycle`; the only differences are
``--load-extension`` flags and a one-time popup configuration step.

Browser-per-test isolation
--------------------------
Each test gets its own freshly-launched browser on a freshly-wiped user-data
directory.  This trades a few seconds of extension-popup re-configuration for
two strong properties:

1. **Cloudflare resilience** — accumulated ``__cf_bm`` / ``cf_clearance``
   cookies and bot-score reputation cannot leak between tests, so a Cloudflare
   challenge tripped by one site can't bias the next.
2. **True isolation** — no shared cookies, ``localStorage``, or
   ``IndexedDB`` between scenarios.  Every test starts as a first-time visitor.

The user-data directory and Playwright trace zip are uniquely keyed by pytest
``node.name`` so parallel runs (``pytest -n auto``) won't collide.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import BrowserContext, async_playwright
from pydantic import SecretStr

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


def _run_async_on_playwright_loop(loop_owner: object | None, coro):
    """Run Playwright async calls on the loop that owns ``page`` (not asyncio.run()'s new loop).

    ``loop_owner`` is the test instance (function-scoped fixture stores the loop
    on ``self``); falls back to ``asyncio.run`` if the loop isn't reachable or
    is already running.
    """
    loop = getattr(loop_owner, "_playwright_loop", None) if loop_owner is not None else None
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


async def _detect_chrome_extension_id_from_context(context: BrowserContext, timeout_s: float = 90.0) -> str:
    """Resolve the runtime ``chrome-extension://<id>`` from the extension's MV3 service worker URL.

    Unpacked extensions get a runtime id that's distinct from the Chrome Web
    Store id, so we can't hardcode it.
    """
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


@asynccontextmanager
async def _persistent_context_lifecycle(
    *,
    instance_id: str,
    with_extension: bool,
    api_key_override: SecretStr | None = None,
    wait_for_block_active: bool = True,
) -> AsyncIterator[tuple[BrowserContext, str | None]]:
    """Single source of truth for launching a per-test persistent Chromium context.

    Yields ``(context, ext_id)``. ``ext_id`` is the runtime extension id when
    ``with_extension=True``, otherwise ``None``.

    Handles tracing start/stop, the unpacked-extension materialisation, and (for
    the with-extension case) the one-time popup configuration. Both fixtures
    delegate the entire lifecycle here so the only behavioural delta between
    "plain" and "with-extension" lives in this one place.

    Args:
        instance_id:     Unique key used to name the ``user-data`` dir and trace
                         file (typically the pytest ``node.name``).  Function-scoped
                         fixtures pass the per-test name so parallel runs cannot
                         collide on the same on-disk profile.
        with_extension:  Whether to load the unpacked extension and configure the popup.
        api_key_override: When provided, the popup is configured with *this* key
                          instead of ``settings.extension.api_key``.  Use it to test
                          different policy tenants without forking the fixture.  The
                          override value is intentionally not logged (it's a SecretStr).
        wait_for_block_active: When ``True`` (default), and only meaningful for
                          ``with_extension=True``, after configuring the popup the
                          fixture probes a known-blocked site until the extension
                          actually intercepts it — this eliminates a per-test
                          policy-fetch race against fast-redirecting targets.
                          Set to ``False`` for fixtures whose API key has *no*
                          block policy (e.g. the failure-demo fixture); otherwise
                          the probe would loop reloading until its 30 s timeout.
    """
    _ensure_report_dirs()

    # Per-test fresh user-data dir.  Even with function-scoped fixtures the dir
    # name is derived from pytest's node id, which is stable across runs — so we
    # explicitly wipe at the start of each test in case a previous run aborted
    # mid-way and left state behind.  Cloudflare drops tracking cookies
    # (``__cf_bm`` etc.) and bot-score state on every visit; nuking the profile
    # guarantees every test looks like a first-time visitor regardless of what
    # the previous test or run did.
    udd = REPORTS_DIR / ".user-data" / instance_id
    shutil.rmtree(udd, ignore_errors=True)
    udd.mkdir(parents=True, exist_ok=True)
    trace_path = TRACES_DIR / f"{instance_id}.zip"

    launch_args: list[str] = []
    abs_ext: str | None = None
    # Resolved once here so both the pre-launch guard and the post-launch popup
    # configuration use the same value.
    api_key: SecretStr | None = None

    if with_extension:
        from scripts.fetch_extension import fetch_and_unpack

        api_key = api_key_override if api_key_override is not None else settings.extension.api_key
        if api_key is None:
            pytest.fail(
                "PROMPT_SECURITY_API_KEY is required for with-extension tests. "
                "Set it in `.env` (see `.env.example`) or as the GitHub secret `PROMPT_SECURITY_API_KEY`."
            )
        ext_dir = settings.extension.resolved_extension_dir()
        if not (ext_dir / "manifest.json").is_file():
            fetch_and_unpack(
                extension_id=settings.extension.chrome_store_extension_id,
                dest_dir=ext_dir,
                force=False,
            )
        abs_ext = str(ext_dir.resolve())
        launch_args = [f"--disable-extensions-except={abs_ext}", f"--load-extension={abs_ext}"]

    for a in settings.test.browser_args:
        if a not in launch_args:
            launch_args.append(a)

    # === Cloudflare bot-detection bypass ===
    # ChatGPT and Claude.ai are both fronted by a Cloudflare "Verify you are
    # human" Managed Challenge that detects Playwright via several signals.
    # We layer three complementary mitigations:
    #
    #   1. Per-test fresh persistent context (see fixture scope below)
    #         Each test runs in a brand-new browser launched on a freshly
    #         wiped user-data dir, so accumulated ``__cf_bm`` /
    #         ``cf_clearance`` cookies and bot-score reputation from prior
    #         runs cannot raise our challenge tier.  This is the single
    #         most impactful mitigation against day-over-day flakiness.
    #
    #   2. ``--disable-blink-features=AutomationControlled``
    #         Removes the ``navigator.webdriver`` flag at the browser
    #         process level (Cloudflare's first-pass bot check).
    #
    #   3. ``user_agent`` override + ``add_init_script`` JS patches
    #         Pin the UA string to a stable Chrome-version, and patch the
    #         JS surface simple bot-checks read: ``navigator.webdriver``,
    #         ``plugins``, ``languages``, ``hardwareConcurrency``,
    #         ``deviceMemory``, the ``chrome.runtime`` stub, and the
    #         Permissions API.
    #
    # When Playwright bumps its bundled Chromium major version, update the UA
    # below to match (the bundled version is printed by ``playwright install
    # chromium`` and visible in ``--version`` of the downloaded binary).
    if "--disable-blink-features=AutomationControlled" not in launch_args:
        launch_args.append("--disable-blink-features=AutomationControlled")
    real_chrome_user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    )
    stealth_init_script = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'PDF Viewer', filename: 'internal-pdf-viewer' },
                { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer' },
                { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer' },
            ],
        });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        if (!window.chrome) { window.chrome = {}; }
        if (!window.chrome.runtime) { window.chrome.runtime = {}; }
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (params) => (
            params.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(params)
        );
    """
    # === END Cloudflare bypass block ===

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(udd),
            headless=False,
            slow_mo=settings.test.slow_mo_ms,
            args=launch_args,
            viewport={"width": 1920, "height": 900},
            ignore_https_errors=True,
            user_agent=real_chrome_user_agent,
        )
        await context.add_init_script(stealth_init_script)
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        ext_id: str | None = None
        if with_extension:
            ext_id = await _detect_chrome_extension_id_from_context(context, timeout_s=120.0)
            logger.info("Chrome extension id resolved", extension_id=ext_id)

            config_page = await context.new_page()
            config_page.set_default_timeout(settings.test.default_timeout_ms)
            config_page.set_default_navigation_timeout(120_000)
            popup = ExtensionPopupPage(config_page)
            assert api_key is not None  # narrowed above  # noqa: S101
            await popup.configure(ext_id, settings.extension.api_domain, api_key)
            saved_domain = await popup.read_api_domain()
            if saved_domain.strip() != settings.extension.api_domain.strip():
                logger.warning(
                    "API domain in popup differs from expected after save",
                    expected=settings.extension.api_domain,
                    got=saved_domain,
                )
            await config_page.close()

            # === Policy-activation barrier ==================================
            # Saving credentials in the popup is *not* enough — the MV3 service
            # worker still has to call back to ``settings.extension.api_domain``
            # to fetch the tenant policy.  Until that round-trip completes the
            # extension lets requests through, which races fast-redirecting
            # sites (e.g. Claude → /login in ~300 ms) and produces flaky
            # "expected block, got allow" failures.
            #
            # Class-scoped fixtures masked this race because the policy was
            # fetched once at class setup and was always cached by the time
            # individual tests ran.  Per-test fixtures resurface the race on
            # every test, so we explicitly wait for the policy to go live by
            # probing a known-blocked site (``gemini.google.com`` — required
            # to be blocked per the assignment) and confirming the extension
            # redirects us to its overlay.  When that happens, the policy is
            # fully loaded for *every* downstream navigation in this context.
            #
            # Skipped for fixtures whose tenant has *no* block policy (e.g.
            # the failure-demo fixture); without a block rule the probe would
            # never get intercepted and we'd reload the page until the 30 s
            # timeout for nothing.
            if wait_for_block_active:
                probe = await context.new_page()
                try:
                    deadline = time.monotonic() + 30.0
                    ok = False
                    while time.monotonic() < deadline:
                        try:
                            await probe.goto(
                                "https://gemini.google.com/",
                                wait_until="domcontentloaded",
                                timeout=15_000,
                            )
                        except Exception:
                            pass
                        if probe.url.startswith(f"chrome-extension://{ext_id}/"):
                            ok = True
                            break
                        await asyncio.sleep(0.5)
                    if not ok:
                        logger.warning(
                            "Extension policy did not activate within 30 s; downstream block tests may flake",
                            final_probe_url=probe.url,
                        )
                    else:
                        logger.info("Extension policy active — block tests safe to run")
                finally:
                    await probe.close()
            # === END policy-activation barrier ===============================

        try:
            yield context, ext_id
        finally:
            try:
                await context.tracing.stop(path=str(trace_path))
            except Exception:
                logger.warning("Could not stop tracing", exc_info=True)
            try:
                await context.close()
            except Exception:
                logger.warning("Could not close context cleanly", exc_info=True)
            logger.info(
                "Browser context closed",
                with_extension=with_extension,
                trace_path=str(trace_path),
            )


def _resolve_failure_page(item: pytest.Item):
    """Best-effort: find the page most likely to capture meaningful failure context.

    With function-scoped browser fixtures, the active page lives on the test
    instance (``self.page``); the active context lives on ``self.context``.
    """
    instance = getattr(item, "instance", None)
    if instance is None:
        return None
    page = getattr(instance, "page", None)
    if page is not None:
        return page
    ctx = getattr(instance, "context", None)
    pages = getattr(ctx, "pages", []) if ctx else []
    if pages:
        return pages[-1]
    return None


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
    page = _resolve_failure_page(item)
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
        png = _run_async_on_playwright_loop(getattr(item, "instance", None), _failure_artifacts())
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


def _instance_id_for(request: pytest.FixtureRequest) -> str:
    """Build a unique, filesystem-safe id for the user-data dir + trace path.

    Uses the pytest ``node.name`` so each test gets its own profile, prefixed
    with the class name (when applicable) for easier human navigation in
    ``reports/.user-data/`` and ``reports/traces/``.  Falls back gracefully for
    module-level / session fixtures.
    """
    node_name = request.node.name if request.node is not None else "session"
    cls_prefix = request.cls.__name__ + "_" if request.cls is not None else ""
    raw = f"{cls_prefix}{node_name}"
    return raw.replace("/", "_").replace("::", "_").replace(" ", "_")


@pytest.fixture
async def browser_context_plain(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """Vanilla persistent Chromium context, no extension loaded — baseline behaviour.

    Function-scoped: every test gets a freshly-launched Chromium on a wiped
    user-data dir.  The active context, the asyncio loop, and a placeholder
    ``chrome_extension_id = None`` are written to ``self`` so the test methods
    (and the failure-report hook) can reach them via ``request.instance``.
    """
    instance_id = _instance_id_for(request)
    async with _persistent_context_lifecycle(instance_id=instance_id, with_extension=False) as (ctx, _):
        request.instance._playwright_loop = asyncio.get_running_loop()
        request.instance.context = ctx
        request.instance.chrome_extension_id = None
        yield


@pytest.fixture
async def browser_context_with_extension(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """Persistent Chromium with the Prompt Security extension loaded + popup configured.

    Function-scoped — every test launches a fresh browser, loads the unpacked
    extension, configures the popup, then yields.  The popup re-configuration
    cost (~3-5 s per test) is the deliberate price for full per-test isolation
    against Cloudflare bot-score accumulation; see module docstring.

    Lifecycle on top of the plain fixture:
        1. Loads the unpacked extension via ``--load-extension`` /
           ``--disable-extensions-except``.
        2. Resolves its runtime ``chrome-extension://<id>``.
        3. Opens the popup once and saves the API domain + key.
    """
    instance_id = _instance_id_for(request)
    async with _persistent_context_lifecycle(instance_id=instance_id, with_extension=True) as (ctx, ext_id):
        request.instance._playwright_loop = asyncio.get_running_loop()
        request.instance.context = ctx
        request.instance.chrome_extension_id = ext_id
        yield


@pytest.fixture
def soft_assert() -> SoftAssert:
    return SoftAssert()
