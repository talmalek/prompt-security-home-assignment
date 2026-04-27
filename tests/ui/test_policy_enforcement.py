"""Web-based GenAI applications — block-connection policy enforcement.

Six end-to-end scenarios in two classes (one per browser fixture):

* :class:`TestWithoutExtension` — vanilla Chromium, **no extension loaded**.
  Three sites (ChatGPT / Gemini / Claude AI) must each reach a real web origin
  in their own tab. This is the baseline that proves blocks observed in the
  *with-extension* class are caused by the extension and not by the network /
  test environment.

* :class:`TestWithExtension` — Chromium with the Prompt Security extension
  loaded **and the popup pre-configured** (API domain + key). The policy in
  effect for the configured tenant is *allow chatgpt.com, block everything
  else*, so the same three sites give:

  * ChatGPT — loads normally (no overlay).
  * Gemini — blocked: lands on
    ``chrome-extension://<runtime-id>/html/pageOverlay.html?type=blockPage&domain=gemini.google.com&...``
    with the *Access Denied* DOM and a *Powered by: prompt.security* footer.
  * Claude AI — blocked: same overlay, ``domain=claude.ai``.

Each test opens its own tab via :func:`_open_tab` so the visible browser
window literally contains *tab1 / tab2 / tab3* as required by the assignment.
The first tab reuses Chromium's startup ``about:blank`` page (a persistent
context always boots with one) so tab numbering aligns with the visual
position of the tabs in the window.

Helper organisation
-------------------
The two assertion bodies live as **module-level helpers** —
:func:`_open_in_tab_and_expect_unblocked` and
:func:`_open_in_tab_and_expect_blocked` — each accepting a test instance
(``inst``) that exposes ``context`` (the active ``BrowserContext``),
``checker`` (a :class:`SoftAssert`), an optional ``TAB_OFFSET`` int, and an
optional ``chrome_extension_id`` string.  Both ``TestWithoutExtension`` and
``TestWithExtension`` simply forward ``self`` to these helpers, which keeps
exactly one implementation of each assertion body and leaves the test
classes as thin orchestration shells.

The unblock helper accepts a ``context`` kwarg (a short human string such
as ``"no extension installed"`` or ``"allow policy"``) that is woven into
Allure step labels and failure messages so the diagnostic in the report
makes the *why* explicit without forking the helper.

For backward compatibility ``run_block_assertion`` is exported as an alias
of :func:`_open_in_tab_and_expect_blocked` — used by
:mod:`tests.ui.test_failure_demo` so that file requires no churn.
"""

from __future__ import annotations

import json

import allure
import pytest

from config.settings import settings
from tests.pages.web_app_page import CHATGPT, CLAUDE, GEMINI, GenAiAppSite, WebGenAiAppPage
from utils.logger import logger
from utils.soft_assert import SoftAssert


def _attach_snapshot(snap: dict, *, name: str) -> None:
    allure.attach(
        json.dumps(snap, indent=2, default=str),
        name=name,
        attachment_type=allure.attachment_type.JSON,
    )


async def _open_tab(context, *, tab_index: int):
    """Open a tab in the shared persistent context and apply project-wide timeouts.

    ``launch_persistent_context`` always boots Chromium with a single
    ``about:blank`` page — there is no flag to suppress it.  To keep the visible
    tab numbering aligned with the Allure step labels (so "tab 1" really is the
    leftmost tab in the window), the first call reuses that initial blank page
    instead of opening a new one.  Subsequent calls open fresh tabs as usual.
    """
    label = f"[tab {tab_index}] Open a new browser tab in the shared context"
    with allure.step(label):
        logger.info(f"[tab {tab_index}] Opening new browser tab")
        page = None
        for existing in context.pages:
            if existing.url == "about:blank":
                page = existing
                break
        if page is None:
            page = await context.new_page()
        page.set_default_timeout(settings.test.default_timeout_ms)
        page.set_default_navigation_timeout(60_000)
    return page


# ---------------------------------------------------------------------------
# Module-level assertion bodies
#
# Both ``TestWithoutExtension`` and ``TestWithExtension`` (and the failure-demo
# class in ``tests.ui.test_failure_demo``) delegate to these helpers, so the
# assertion code lives in exactly one place.
#
# Each helper takes a test instance (``inst``) and reads:
#   - inst.context              — the active BrowserContext
#   - inst.checker              — a SoftAssert instance
#   - inst.page                 — written by the helper
#   - inst.chrome_extension_id  — optional; used to verify overlay origin
#   - inst.TAB_OFFSET           — optional int; defaults to 0
# ---------------------------------------------------------------------------


async def _open_in_tab_and_expect_unblocked(
    inst,
    site: GenAiAppSite,
    *,
    tab_index: int,
    context: str = "",
) -> None:
    """Open ``site`` in a fresh tab; assert the navigation reached a real web origin.

    Structural assertions (always identical):

    * Final URL scheme is **not** ``chrome-extension`` (the extension did not
      hijack the navigation).
    * No block-overlay snapshot was recorded.

    The ``context`` string is interpolated into Allure step labels and failure
    messages to disambiguate *why* the site is expected to load — e.g.
    ``"no extension installed"`` (baseline) vs
    ``"extension installed; ChatGPT is on the allow list"`` (allow policy).
    It does **not** change the structural assertions, only the diagnostic text.
    """
    tab = tab_index + getattr(inst, "TAB_OFFSET", 0)
    suffix = f" — {context}" if context else ""

    logger.info(
        f"[tab {tab}] Opening {site.name} - expecting ALLOW (no block){suffix}",
        site_url=site.url,
    )
    page = await _open_tab(inst.context, tab_index=tab)
    inst.page = page

    with allure.step(f"[tab {tab}] Navigate to {site.name} ({site.url}){suffix}"):
        logger.info(f"[tab {tab}] Navigating to {site.name} for allow check")
        wp = WebGenAiAppPage(page, site)
        await wp.navigate()

    with allure.step(f"[tab {tab}] Capture post-navigation state"):
        logger.info(f"[tab {tab}] Capturing post-navigation state")
        snap = await wp.assess_state()
        _attach_snapshot(snap, name=f"{site.name.replace(' ', '_')}_landing.json")

        logger.info(
            f"[tab {tab}] {site.name} state assessed",
            final_url=snap["final_url"],
            scheme=snap["scheme"],
        )

    with inst.checker.step(
        f"[tab {tab}] {site.name}: final scheme is web (https/http), NOT 'chrome-extension'{suffix}"
    ):
        logger.info(
            f"[tab {tab}] Assert {site.name} final scheme is web — "
            f"expected='NOT chrome-extension', found={snap['scheme']!r}"
        )
        inst.checker.check_not_equal(
            a=snap["scheme"],
            b="chrome-extension",
            msg=(
                f"{site.name} unexpectedly served by an extension at {snap['final_url']!r}; "
                f"expected a normal web origin{suffix}"
            ),
        )

    with inst.checker.step(f"[tab {tab}] {site.name}: no block-overlay snapshot recorded"):
        overlay_state = "present" if "overlay" in snap else "absent"
        logger.info(
            f"[tab {tab}] Assert {site.name} has no overlay snapshot — expected='absent', found={overlay_state!r}"
        )
        inst.checker.check_not_in(
            item="overlay",
            container=snap,
            msg=f"{site.name} unexpectedly produced an extension overlay snapshot ({snap.get('overlay')!r})",
        )

    logger.info(
        "Site loaded unblocked",
        app=site.name,
        tab=tab,
        final_url=snap["final_url"],
        context=context or "<no-context>",
    )


async def _open_in_tab_and_expect_blocked(
    inst,
    site: GenAiAppSite,
    *,
    tab_index: int,
) -> None:
    """Open ``site`` in a fresh tab; assert the extension's block overlay rendered.

    Structural assertions:

    * Final URL scheme is ``chrome-extension``.
    * An ``overlay`` snapshot was recorded by :class:`WebGenAiAppPage`.
    * Overlay query params: ``type=blockPage``, ``domain=<site.block_domain>``,
      ``canBypass=Prevent``.
    * Overlay DOM markers: ``body.ai-site``, non-empty ``.title`` containing
      *Denied*, ``.description`` mentioning *blocked*, branding container
      (``#poweredBy`` / ``.powered-by``).

    If ``inst.chrome_extension_id`` is set, additionally asserts the overlay
    was served by that exact extension id — making cross-test contamination
    immediately visible in the failure message.
    """
    tab = tab_index + getattr(inst, "TAB_OFFSET", 0)
    logger.info(f"[tab {tab}] Opening {site.name} - expecting BLOCK policy", site_url=site.url)
    page = await _open_tab(inst.context, tab_index=tab)
    inst.page = page

    with allure.step(f"[tab {tab}] Navigate to {site.name} ({site.url}) — expecting BLOCK policy"):
        logger.info(f"[tab {tab}] Navigating to {site.name} for block check")
        wp = WebGenAiAppPage(page, site)
        await wp.navigate()

    with allure.step(f"[tab {tab}] Wait for the extension's pageOverlay.html to settle"):
        logger.info(f"[tab {tab}] Waiting for extension overlay to settle")
        snap = await wp.assess_state(settle_seconds=2.0)
        _attach_snapshot(snap, name=f"{site.name.replace(' ', '_')}_landing.json")

        logger.info(
            f"[tab {tab}] {site.name} state assessed",
            final_url=snap["final_url"],
            is_blocked=(snap["scheme"] == "chrome-extension"),
        )

    with inst.checker.step(f"[tab {tab}] Final URL scheme is 'chrome-extension'"):
        logger.info(f"[tab {tab}] Assert final URL scheme — expected='chrome-extension', found={snap['scheme']!r}")
        inst.checker.check_equal(
            actual=snap["scheme"],
            expected="chrome-extension",
            message=(
                f"{site.name} block expected: final URL should be served by the extension, got {snap['final_url']!r}"
            ),
        )

    with inst.checker.step(f"[tab {tab}] Final URL is the extension's pageOverlay.html (block snapshot recorded)"):
        overlay_state = "present" if "overlay" in snap else "absent"
        logger.info(f"[tab {tab}] Assert overlay snapshot recorded — expected='present', found={overlay_state!r}")
        inst.checker.check_in(
            item="overlay",
            container=snap,
            msg=(f"{site.name} did not land on a recognised pageOverlay.html (final URL was {snap['final_url']!r})"),
        )

    overlay = snap.get("overlay")
    if overlay is None:
        return

    with inst.checker.step(f"[tab {tab}] Overlay query: type=blockPage"):
        logger.info(f"[tab {tab}] Assert overlay query type — expected='blockPage', found={overlay.get('type')!r}")
        inst.checker.check_equal(
            actual=overlay.get("type"),
            expected="blockPage",
            message=f"{site.name} overlay declares unexpected type {overlay.get('type')!r}",
        )

    with inst.checker.step(f"[tab {tab}] Overlay query: domain={site.block_domain}"):
        logger.info(
            f"[tab {tab}] Assert overlay query domain — expected={site.block_domain!r}, found={overlay.get('domain')!r}"
        )
        inst.checker.check_equal(
            actual=overlay.get("domain"),
            expected=site.block_domain,
            message=(
                f"{site.name} overlay declares wrong blocked domain "
                f"(expected {site.block_domain!r}, got {overlay.get('domain')!r})"
            ),
        )

    ext_id = getattr(inst, "chrome_extension_id", None)
    if ext_id:
        with inst.checker.step(f"[tab {tab}] Overlay served by the loaded extension id ({ext_id})"):
            logger.info(
                f"[tab {tab}] Assert overlay extension id — expected={ext_id!r}, found={overlay.get('extension_id')!r}"
            )
            inst.checker.check_equal(
                actual=overlay.get("extension_id"),
                expected=ext_id,
                message=(
                    f"{site.name} overlay served by an unexpected extension id "
                    f"(expected {ext_id!r}, got {overlay.get('extension_id')!r})"
                ),
            )

    with inst.checker.step(f"[tab {tab}] Overlay body has block-page class marker (body.ai-site)"):
        body_class = overlay.get("body_class") or ""
        logger.info(
            f"[tab {tab}] Assert body class contains block marker — "
            f"expected=\"'ai-site' in body.class\", found={body_class!r}"
        )
        inst.checker.check_in(
            item="ai-site",
            container=body_class,
            msg=f"{site.name} overlay body missing 'ai-site' class (got body.class={body_class!r})",
        )

    with inst.checker.step(f"[tab {tab}] Overlay shows 'Access Denied' title (.title)"):
        title = (overlay.get("title_text") or "").strip()
        logger.info(
            f"[tab {tab}] Assert overlay title contains 'Denied' — "
            f"expected=\"non-empty .title containing 'Denied'\", found={title!r}"
        )
        inst.checker.check_true(
            bool(title),
            msg=f"{site.name} overlay missing .title element / text in DOM",
        )
        if title:
            inst.checker.check_in(
                item="Denied",
                container=title,
                msg=f"{site.name} overlay title is not 'Access Denied' (got {title!r})",
            )

    with inst.checker.step(f"[tab {tab}] Overlay description states the administrator blocked access (.description)"):
        description = (overlay.get("description") or "").strip()
        logger.info(
            f"[tab {tab}] Assert overlay description mentions 'blocked' — "
            f"expected=\"non-empty .description containing 'blocked'\", found={description!r}"
        )
        inst.checker.check_true(
            bool(description),
            msg=f"{site.name} overlay missing .description element / text in DOM",
        )
        if description:
            inst.checker.check_in(
                item="blocked",
                container=description.lower(),
                msg=(f"{site.name} overlay description does not mention administrator block (got {description!r})"),
            )

    with inst.checker.step(f"[tab {tab}] Overlay carries Prompt Security / SentinelOne branding (#poweredBy)"):
        branding_state = "present" if overlay.get("has_branding") else "absent"
        logger.info(
            f"[tab {tab}] Assert overlay branding container — "
            f'expected="#poweredBy / .powered-by present", found={branding_state!r}'
        )
        inst.checker.check_true(
            bool(overlay.get("has_branding")),
            msg=f"{site.name} overlay missing Prompt Security branding container (#poweredBy / .powered-by)",
        )

    with inst.checker.step(f"[tab {tab}] Overlay query: canBypass=Prevent (no per-user override on this policy)"):
        logger.info(
            f"[tab {tab}] Assert overlay query canBypass — expected='Prevent', found={overlay.get('can_bypass')!r}"
        )
        inst.checker.check_equal(
            actual=overlay.get("can_bypass"),
            expected="Prevent",
            message=(f"{site.name} overlay declares unexpected canBypass value (got {overlay.get('can_bypass')!r})"),
        )

    allure.attach(
        body=" | ".join(f"{k}={v}" for k, v in overlay.items()),
        name=f"{site.name.replace(' ', '_')}_block_evidence",
        attachment_type=allure.attachment_type.TEXT,
    )
    logger.info(
        "Site blocked by extension policy",
        app=site.name,
        tab=tab,
        extension_id=overlay.get("extension_id"),
        domain=overlay.get("domain"),
        type=overlay.get("type"),
    )


# Backward-compatible alias used by tests.ui.test_failure_demo.  Kept as a
# plain alias (no wrapper function) so there is exactly one callable to
# maintain — same docstring, same signature, same behaviour.
run_block_assertion = _open_in_tab_and_expect_blocked


@allure.epic("Prompt Security")
@allure.feature("Web GenAI Access Policy Enforcement")
@allure.story("Baseline (no extension installed)")
@pytest.mark.ui
@pytest.mark.smoke
@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.usefixtures("browser_context_plain")
class TestWithoutExtension:
    """No extension → no policy: every GenAI host must reach an unfiltered web origin.

    These three tests share a single Chromium ``BrowserContext`` (class-scoped
    fixture); each test opens its own tab. The pass criterion is intentionally
    minimal — *the navigation was not intercepted by an extension overlay* —
    because vendor login walls and regional redirects can change the final
    host. The actual landing URL is captured in Allure for review.
    """

    # Class-level tab offset applied to every tab label in Allure steps and logs.
    # Set to a non-zero integer on a subclass to shift all tab numbers — useful
    # when multiple browser classes run in the same session and you want the tab
    # numbering in the report to be globally unique (e.g. class-A uses tabs 1-3,
    # class-B sets TAB_OFFSET = 3 to label its tabs 4-6).
    TAB_OFFSET: int = 0

    def setup_method(self) -> None:
        self.checker = SoftAssert()

    def teardown_method(self) -> None:
        self.checker.assert_all()

    @allure.title("Without extension — ChatGPT loads in tab 1 (Result: No Block)")
    @allure.description(
        "**Scenario:** Vanilla Chromium, no extension loaded.\n\n"
        "**Action:** Open a new tab (tab 1) → navigate to https://chatgpt.com/.\n\n"
        "**Expected:** The browser reaches a normal web origin "
        "(scheme `https`, *not* `chrome-extension://…/pageOverlay.html`). "
        "The exact final host may vary if ChatGPT shows a login wall or regional "
        "redirect, but the navigation must NOT be intercepted by an extension overlay — "
        "this proves the host is reachable in the absence of any policy.\n\n"
        "**Why this matters:** This is the *baseline* — it ensures any block "
        "observed in the with-extension class is attributable to the extension's "
        "policy enforcement, not to the network or the test environment."
    )
    async def test_chatgpt_loads_unblocked_in_tab1(self) -> None:
        """Baseline: ChatGPT is reachable without the extension installed (tab 1).

        Steps:
        1. Open a new browser tab (tab 1) — plain Chromium, no extension loaded
        2. Navigate to https://chatgpt.com/
        3. Capture post-navigation state (final URL, scheme)
        4. Assert final URL scheme is https/http, NOT chrome-extension (no block overlay)
        5. Assert no block-overlay snapshot was recorded
        """
        await _open_in_tab_and_expect_unblocked(
            self,
            CHATGPT,
            tab_index=1,
            context="no extension installed in this fixture",
        )

    @allure.title("Without extension — Gemini loads in tab 2 (Result: No Block)")
    @allure.description(
        "**Scenario:** Vanilla Chromium, no extension loaded.\n\n"
        "**Action:** Open a new tab (tab 2) → navigate to https://gemini.google.com/.\n\n"
        "**Expected:** Final URL is a normal web origin (often `accounts.google.com` "
        "if the user is signed-out — that's still 'site reachable'). "
        "The navigation must NOT land on `chrome-extension://…/pageOverlay.html`."
    )
    async def test_gemini_loads_unblocked_in_tab2(self) -> None:
        """Baseline: Gemini is reachable without the extension installed (tab 2).

        Steps:
        1. Open a new browser tab (tab 2) — plain Chromium, no extension loaded
        2. Navigate to https://gemini.google.com/
        3. Capture post-navigation state (final URL may be accounts.google.com if signed out)
        4. Assert final URL scheme is https/http, NOT chrome-extension (no block overlay)
        5. Assert no block-overlay snapshot was recorded
        """
        await _open_in_tab_and_expect_unblocked(
            self,
            GEMINI,
            tab_index=2,
            context="no extension installed in this fixture",
        )

    @allure.title("Without extension — Claude AI loads in tab 3 (Result: No Block)")
    @allure.description(
        "**Scenario:** Vanilla Chromium, no extension loaded.\n\n"
        "**Action:** Open a new tab (tab 3) → navigate to https://claude.ai/.\n\n"
        "**Expected:** Final URL is a normal web origin (often Claude's login page). "
        "The navigation must NOT land on `chrome-extension://…/pageOverlay.html`."
    )
    async def test_claude_loads_unblocked_in_tab3(self) -> None:
        """Baseline: Claude AI is reachable without the extension installed (tab 3).

        Steps:
        1. Open a new browser tab (tab 3) — plain Chromium, no extension loaded
        2. Navigate to https://claude.ai/
        3. Capture post-navigation state (final URL is often Claude's login page)
        4. Assert final URL scheme is https/http, NOT chrome-extension (no block overlay)
        5. Assert no block-overlay snapshot was recorded
        """
        await _open_in_tab_and_expect_unblocked(
            self,
            CLAUDE,
            tab_index=3,
            context="no extension installed in this fixture",
        )


@allure.epic("Prompt Security")
@allure.feature("Web GenAI Access Policy Enforcement")
@allure.story("With Prompt Security extension installed and configured")
@pytest.mark.ui
@pytest.mark.smoke
@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.usefixtures("browser_context_with_extension")
class TestWithExtension:
    """Extension installed + popup configured → policy = allow ChatGPT, block Gemini & Claude AI.

    The fixture has already loaded the unpacked extension and saved API
    domain + key in the popup. These three tests share that single
    configured browser context and each opens its own tab. The block
    assertions check the structured query parameters of the extension's
    overlay URL (``type=blockPage``, ``domain=<expected>``) and the runtime
    extension id — making the failure messages precise and actionable.
    """

    # Class-level tab offset — see TestWithoutExtension.TAB_OFFSET for full docs.
    TAB_OFFSET: int = 0

    def setup_method(self) -> None:
        self.checker = SoftAssert()

    def teardown_method(self) -> None:
        self.checker.assert_all()

    @allure.title("With extension — ChatGPT loads in tab 1 (Result: No Block — allow policy)")
    @allure.description(
        "**Scenario:** Chromium with the Prompt Security extension loaded and configured.\n\n"
        "**Policy:** ChatGPT is on the *allow* list for the configured tenant.\n\n"
        "**Action:** Open a new tab (tab 1) → navigate to https://chatgpt.com/.\n\n"
        "**Expected:** Final URL is a normal web origin under `chatgpt.com` "
        "(or vendor login wall) — NOT `chrome-extension://…/pageOverlay.html`. "
        "This proves the extension applies the *allow* rule rather than blocking everything."
    )
    async def test_chatgpt_loads_unblocked_in_tab1(self) -> None:
        """With extension + allow policy: ChatGPT loads normally (tab 1).

        Steps:
        1. Open a new browser tab (tab 1) — extension is loaded and configured
        2. Navigate to https://chatgpt.com/ (tenant policy: allow)
        3. Capture post-navigation state (final URL, scheme)
        4. Assert final URL scheme is https/http (NOT the extension block overlay)
        5. Assert no block-overlay snapshot was recorded
        """
        await _open_in_tab_and_expect_unblocked(
            self,
            CHATGPT,
            tab_index=1,
            context="extension installed; ChatGPT is on the allow list",
        )

    @allure.title("With extension — Gemini blocked in tab 2 (Result: Block — Access Denied overlay)")
    @allure.description(
        "**Scenario:** Chromium with the Prompt Security extension loaded and configured.\n\n"
        "**Policy:** Gemini is on the *block* list for the configured tenant.\n\n"
        "**Action:** Open a new tab (tab 2) → navigate to https://gemini.google.com/.\n\n"
        "**Expected:** The extension intercepts the navigation and the user lands on\n"
        "`chrome-extension://<runtime-id>/html/pageOverlay.html?type=blockPage&domain=gemini.google.com&originalUrl=…`.\n\n"
        "We verify, in order:\n"
        "1. Final URL scheme is `chrome-extension`.\n"
        "2. The overlay was served by the **same** extension id resolved by the fixture (`self.chrome_extension_id`).\n"
        "3. Query parameter `type=blockPage`.\n"
        "4. Query parameter `domain=gemini.google.com`.\n"
        "5. DOM markers `Access Denied` (`.title`) and the `Powered by: prompt.security` footer link "
        "are present (best-effort; recorded as Allure detail).\n\n"
        "Failing on parsed query params (vs. fragile DOM heuristics) makes the error message itself the diagnosis."
    )
    async def test_gemini_blocked_in_tab2(self) -> None:
        """With extension + block policy: Gemini is intercepted by the Access Denied overlay (tab 2).

        Steps:
        1. Open a new browser tab (tab 2) — extension is loaded with the block-policy key
        2. Navigate to https://gemini.google.com/ (tenant policy: block)
        3. Wait for the extension's pageOverlay.html to settle (up to 2 s)
        4. Assert final URL scheme is chrome-extension and path ends with /html/pageOverlay.html
        5. Assert overlay served by the resolved runtime extension id (self.chrome_extension_id)
        6. Assert query param type=blockPage
        7. Assert query param domain=gemini.google.com
        8. Assert DOM markers: .title contains "Denied", Powered by: prompt.security link
        """
        await _open_in_tab_and_expect_blocked(self, GEMINI, tab_index=2)

    @allure.title("With extension — Claude AI blocked in tab 3 (Result: Block — Access Denied overlay)")
    @allure.description(
        "**Scenario:** Chromium with the Prompt Security extension loaded and configured.\n\n"
        "**Policy:** Claude AI is on the *block* list for the configured tenant.\n\n"
        "**Action:** Open a new tab (tab 3) → navigate to https://claude.ai/.\n\n"
        "**Expected:** The extension intercepts the navigation and the user lands on\n"
        "`chrome-extension://<runtime-id>/html/pageOverlay.html?type=blockPage&domain=claude.ai&originalUrl=…`.\n\n"
        "Same checks as the Gemini case (URL scheme + extension id + query params + DOM markers), "
        "with `domain=claude.ai`."
    )
    async def test_claude_blocked_in_tab3(self) -> None:
        """With extension + block policy: Claude AI is intercepted by the Access Denied overlay (tab 3).

        Steps:
        1. Open a new browser tab (tab 3) — extension is loaded with the block-policy key
        2. Navigate to https://claude.ai/ (tenant policy: block)
        3. Wait for the extension's pageOverlay.html to settle (up to 2 s)
        4. Assert final URL scheme is chrome-extension and path ends with /html/pageOverlay.html
        5. Assert overlay served by the resolved runtime extension id (self.chrome_extension_id)
        6. Assert query param type=blockPage
        7. Assert query param domain=claude.ai
        8. Assert DOM markers: .title contains "Denied", Powered by: prompt.security link
        """
        await _open_in_tab_and_expect_blocked(self, CLAUDE, tab_index=3)
