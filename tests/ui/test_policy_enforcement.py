"""Web-based GenAI applications — block-connection policy enforcement.

Six end-to-end scenarios in two classes (one per browser fixture):

* :class:`TestWithoutExtension` — vanilla Chromium, **no extension loaded**.
  Three sites (ChatGPT / Gemini / Claude AI) must each reach a real web origin.
  This is the baseline that proves blocks observed in the *with-extension*
  class are caused by the extension and not by the network / test environment.

* :class:`TestWithExtension` — Chromium with the Prompt Security extension
  loaded **and the popup pre-configured** (API domain + key). The policy in
  effect for the configured tenant is *allow chatgpt.com, block everything
  else*, so the same three sites give:

  * ChatGPT — loads normally (no overlay).
  * Gemini — blocked: lands on
    ``chrome-extension://<runtime-id>/html/pageOverlay.html?type=blockPage&domain=gemini.google.com&...``
    with the *Access Denied* DOM and a *Powered by: prompt.security* footer.
  * Claude AI — blocked: same overlay, ``domain=claude.ai``.

Browser isolation
-----------------
Both classes use **function-scoped** browser fixtures (see
:mod:`tests.conftest`): every test launches its own Chromium on a freshly
wiped user-data directory and opens the target site in a single page.  This
costs a few seconds of extension popup re-configuration per test but
eliminates Cloudflare bot-score accumulation between scenarios — the
pragmatic answer to challenge-page flakiness.

Helper organisation
-------------------
The two assertion bodies live as **module-level helpers** —
:func:`_open_and_expect_unblocked` and :func:`_open_and_expect_blocked` —
each accepting a test instance (``inst``) that exposes ``context`` (the
active ``BrowserContext``), ``checker`` (a :class:`SoftAssert`), and an
optional ``chrome_extension_id`` string.  Both ``TestWithoutExtension`` and
``TestWithExtension`` simply forward ``self`` to these helpers, which keeps
exactly one implementation of each assertion body and leaves the test
classes as thin orchestration shells.

The unblock helper accepts a ``context`` kwarg (a short human string such
as ``"no extension installed"`` or ``"allow policy"``) that is woven into
Allure step labels and failure messages so the diagnostic in the report
makes the *why* explicit without forking the helper.

For backward compatibility ``run_block_assertion`` is exported as an alias
of :func:`_open_and_expect_blocked` — used by
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


async def _open_page(context):
    """Return the page the test will navigate in, with project-wide timeouts applied.

    ``launch_persistent_context`` always boots Chromium with a single
    ``about:blank`` page — there is no flag to suppress it.  To keep the
    visible window at exactly one page per test (instead of an empty default
    page plus a second page where the test runs), this helper reuses the
    startup ``about:blank`` page if it's still present.  If the context has
    no idle ``about:blank`` (e.g. the with-extension fixture's policy probe
    closed it) a fresh page is opened as a fallback.
    """
    with allure.step("Open page in the test browser"):
        logger.info("Opening page in the test browser")
        page = None
        for existing in context.pages:
            if existing.url == "about:blank":
                page = existing
                logger.info("Reusing Chromium startup about:blank page")
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
# ---------------------------------------------------------------------------


async def _open_and_expect_unblocked(
    inst,
    site: GenAiAppSite,
    *,
    context: str = "",
) -> None:
    """Open ``site`` in a fresh page; assert the navigation reached a real web origin.

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
    suffix = f" — {context}" if context else ""

    logger.info(f"Opening {site.name} - expecting ALLOW (no block){suffix}", site_url=site.url)
    page = await _open_page(inst.context)
    inst.page = page

    with allure.step(f"Navigate to {site.name} ({site.url}){suffix}"):
        logger.info(f"Navigating to {site.name} for allow check")
        wp = WebGenAiAppPage(page, site)
        await wp.navigate()

    with allure.step(f"Capture post-navigation state for {site.name}"):
        logger.info(f"Capturing {site.name} post-navigation state")
        snap = await wp.assess_state()
        _attach_snapshot(snap, name=f"{site.name.replace(' ', '_')}_landing.json")

        logger.info(
            f"{site.name} state assessed",
            final_url=snap["final_url"],
            scheme=snap["scheme"],
        )

    with inst.checker.step(f"{site.name}: final scheme is web (https/http), NOT 'chrome-extension'{suffix}"):
        logger.info(
            f"Assert {site.name} final scheme is web — expected='NOT chrome-extension', found={snap['scheme']!r}"
        )
        inst.checker.check_not_equal(
            a=snap["scheme"],
            b="chrome-extension",
            msg=(
                f"{site.name} unexpectedly served by an extension at {snap['final_url']!r}; "
                f"expected a normal web origin{suffix}"
            ),
        )

    with inst.checker.step(f"{site.name}: no block-overlay snapshot recorded"):
        overlay_state = "present" if "overlay" in snap else "absent"
        logger.info(f"Assert {site.name} has no overlay snapshot — expected='absent', found={overlay_state!r}")
        inst.checker.check_not_in(
            item="overlay",
            container=snap,
            msg=f"{site.name} unexpectedly produced an extension overlay snapshot ({snap.get('overlay')!r})",
        )

    logger.info(
        "Site loaded unblocked",
        app=site.name,
        final_url=snap["final_url"],
        context=context or "<no-context>",
    )


async def _open_and_expect_blocked(
    inst,
    site: GenAiAppSite,
) -> None:
    """Open ``site`` in a fresh page; assert the extension's block overlay rendered.

    The contract here describes the **v7.1.0+ backend-rendered overlay** —
    the only version the suite supports, since ``_ensure_latest_extension``
    in :mod:`tests.conftest` force-refreshes ``extension/`` to the currently
    published Chrome Web Store CRX on every pytest session.

    Structural assertions (URL contract):

    * Final URL scheme is ``chrome-extension``.
    * An ``overlay`` snapshot was recorded by :class:`WebGenAiAppPage`.
    * Overlay query params: ``type=blockPage``, ``domain=<site.block_domain>``,
      ``canBypass=Prevent``, ``useBackendHtml=true``, and a non-empty
      ``popupToken`` (the latest tenant always issues one — its presence is
      what triggers backend HTML rendering instead of the legacy static
      template).

    Visual assertions (DOM contract on the rendered overlay):

    * ``body`` carries the ``ai-site`` class — the unambiguous marker of the
      v7.1.0 backend-rendered overlay; if it's missing the extension served
      a different / older UI and this whole block of assertions is invalid.
    * ``h1.title`` text contains *Denied* (the *Access Denied* headline).
    * ``p.description`` text mentions *administrator* and *blocked* (the
      reason copy).
    * ``p.guidelines`` is present and mentions *guidelines* or *information*
      (the policy hint copy).
    * ``.barrier-illustration`` is present (the roadblock SVG).
    * ``.powered-by`` is present (Prompt Security branding container).

    If ``inst.chrome_extension_id`` is set, additionally asserts the overlay
    was served by that exact extension id — making cross-test contamination
    immediately visible in the failure message.
    """
    logger.info(f"Opening {site.name} - expecting BLOCK policy", site_url=site.url)
    page = await _open_page(inst.context)
    inst.page = page

    with allure.step(f"Navigate to {site.name} ({site.url}) — expecting BLOCK policy"):
        logger.info(f"Navigating to {site.name} for block check")
        wp = WebGenAiAppPage(page, site)
        await wp.navigate()

    with allure.step(f"Wait for the extension's pageOverlay.html to settle on {site.name}"):
        logger.info(f"Waiting for {site.name} extension overlay to settle")
        snap = await wp.assess_state(settle_seconds=2.0)
        _attach_snapshot(snap, name=f"{site.name.replace(' ', '_')}_landing.json")

        logger.info(
            f"{site.name} state assessed",
            final_url=snap["final_url"],
            is_blocked=(snap["scheme"] == "chrome-extension"),
        )

    with inst.checker.step(f"{site.name}: final URL scheme is 'chrome-extension'"):
        logger.info(f"Assert {site.name} final URL scheme — expected='chrome-extension', found={snap['scheme']!r}")
        inst.checker.check_equal(
            actual=snap["scheme"],
            expected="chrome-extension",
            message=(
                f"{site.name} block expected: final URL should be served by the extension, got {snap['final_url']!r}"
            ),
        )

    with inst.checker.step(f"{site.name}: final URL is the extension's pageOverlay.html (block snapshot recorded)"):
        overlay_state = "present" if "overlay" in snap else "absent"
        logger.info(f"Assert {site.name} overlay snapshot recorded — expected='present', found={overlay_state!r}")
        inst.checker.check_in(
            item="overlay",
            container=snap,
            msg=(f"{site.name} did not land on a recognised pageOverlay.html (final URL was {snap['final_url']!r})"),
        )

    overlay = snap.get("overlay")
    if overlay is None:
        return

    with inst.checker.step(f"{site.name}: overlay query type=blockPage"):
        logger.info(f"Assert {site.name} overlay query type — expected='blockPage', found={overlay.get('type')!r}")
        inst.checker.check_equal(
            actual=overlay.get("type"),
            expected="blockPage",
            message=f"{site.name} overlay declares unexpected type {overlay.get('type')!r}",
        )

    with inst.checker.step(f"{site.name}: overlay query domain={site.block_domain}"):
        logger.info(
            f"Assert {site.name} overlay query domain — expected={site.block_domain!r}, found={overlay.get('domain')!r}"
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
        with inst.checker.step(f"{site.name}: overlay served by the loaded extension id ({ext_id})"):
            logger.info(
                f"Assert {site.name} overlay extension id — expected={ext_id!r}, found={overlay.get('extension_id')!r}"
            )
            inst.checker.check_equal(
                actual=overlay.get("extension_id"),
                expected=ext_id,
                message=(
                    f"{site.name} overlay served by an unexpected extension id "
                    f"(expected {ext_id!r}, got {overlay.get('extension_id')!r})"
                ),
            )

    with inst.checker.step(f"{site.name}: overlay query canBypass=Prevent (no per-user override on this policy)"):
        logger.info(
            f"Assert {site.name} overlay query canBypass — expected='Prevent', found={overlay.get('can_bypass')!r}"
        )
        inst.checker.check_equal(
            actual=overlay.get("can_bypass"),
            expected="Prevent",
            message=(f"{site.name} overlay declares unexpected canBypass value (got {overlay.get('can_bypass')!r})"),
        )

    with inst.checker.step(f"{site.name}: overlay rendered via backend HTML (useBackendHtml=true + popupToken)"):
        backend_flag = (overlay.get("use_backend_html") or "").lower()
        token_present = bool(overlay.get("popup_token_present"))
        logger.info(
            f"Assert {site.name} overlay uses backend HTML — "
            f"expected='useBackendHtml=true & popupToken=<set>', "
            f"found=useBackendHtml={backend_flag!r}, popupToken_present={token_present!r}"
        )
        inst.checker.check_equal(
            actual=backend_flag,
            expected="true",
            message=(
                f"{site.name} overlay URL did not request backend HTML rendering "
                f"(expected useBackendHtml=true, got {overlay.get('use_backend_html')!r}); "
                "the latest extension should always set this flag."
            ),
        )
        inst.checker.check_true(
            token_present,
            msg=(
                f"{site.name} overlay URL is missing popupToken; the latest extension "
                "should always issue one when fetching backend-rendered overlay HTML."
            ),
        )

    with inst.checker.step(f"{site.name}: overlay body has the v7.1.0 marker class 'ai-site'"):
        body_class = (overlay.get("body_class") or "").strip()
        logger.info(f"Assert {site.name} overlay body class — expected=\"contains 'ai-site'\", found={body_class!r}")
        inst.checker.check_in(
            item="ai-site",
            container=body_class,
            msg=(
                f"{site.name} overlay body class does not contain 'ai-site' "
                f"(got {body_class!r}); expected the v7.1.0 backend-rendered overlay."
            ),
        )

    with inst.checker.step(f"{site.name}: overlay shows 'Access Denied' headline (h1.title)"):
        title = (overlay.get("title") or "").strip()
        logger.info(
            f"Assert {site.name} overlay title contains 'Denied' — "
            f"expected=\"non-empty h1.title containing 'Denied'\", found={title!r}"
        )
        inst.checker.check_true(
            bool(title),
            msg=f"{site.name} overlay missing h1.title element / text in DOM",
        )
        if title:
            inst.checker.check_in(
                item="Denied",
                container=title,
                msg=f"{site.name} overlay headline is not 'Access Denied' (got {title!r})",
            )

    with inst.checker.step(f"{site.name}: overlay description states the administrator blocked access (p.description)"):
        description = (overlay.get("description") or "").strip()
        description_lower = description.lower()
        logger.info(
            f"Assert {site.name} overlay description mentions 'administrator' + 'blocked' — "
            f"expected=\"non-empty p.description containing 'administrator' and 'blocked'\", "
            f"found={description!r}"
        )
        inst.checker.check_true(
            bool(description),
            msg=f"{site.name} overlay missing p.description element / text in DOM",
        )
        if description:
            inst.checker.check_in(
                item="administrator",
                container=description_lower,
                msg=(f"{site.name} overlay description does not mention 'administrator' (got {description!r})"),
            )
            inst.checker.check_in(
                item="blocked",
                container=description_lower,
                msg=(f"{site.name} overlay description does not mention 'blocked' (got {description!r})"),
            )

    with inst.checker.step(f"{site.name}: overlay shows guidelines hint (p.guidelines)"):
        guidelines = (overlay.get("guidelines") or "").strip()
        guidelines_lower = guidelines.lower()
        logger.info(
            f"Assert {site.name} overlay guidelines is non-empty — "
            f"expected=\"non-empty p.guidelines mentioning 'guidelines' or 'information'\", "
            f"found={guidelines!r}"
        )
        inst.checker.check_true(
            bool(guidelines),
            msg=f"{site.name} overlay missing p.guidelines element / text in DOM",
        )
        if guidelines:
            inst.checker.check_true(
                ("guidelines" in guidelines_lower) or ("information" in guidelines_lower),
                msg=(
                    f"{site.name} overlay guidelines copy does not mention 'guidelines' "
                    f"or 'information' (got {guidelines!r})"
                ),
            )

    with inst.checker.step(f"{site.name}: overlay shows the roadblock illustration (.barrier-illustration)"):
        illustration_state = "present" if overlay.get("has_illustration") else "absent"
        logger.info(
            f"Assert {site.name} overlay roadblock illustration — "
            f'expected=".barrier-illustration present", found={illustration_state!r}'
        )
        inst.checker.check_true(
            bool(overlay.get("has_illustration")),
            msg=(f"{site.name} overlay missing .barrier-illustration / #illustrationBlock SVG in DOM"),
        )

    with inst.checker.step(f"{site.name}: overlay carries Prompt Security branding (.powered-by)"):
        branding_state = "present" if overlay.get("has_branding") else "absent"
        logger.info(
            f'Assert {site.name} overlay branding container — expected=".powered-by present", found={branding_state!r}'
        )
        inst.checker.check_true(
            bool(overlay.get("has_branding")),
            msg=f"{site.name} overlay missing Prompt Security branding container (.powered-by)",
        )

    allure.attach(
        body=" | ".join(f"{k}={v}" for k, v in overlay.items()),
        name=f"{site.name.replace(' ', '_')}_block_evidence",
        attachment_type=allure.attachment_type.TEXT,
    )
    logger.info(
        "Site blocked by extension policy",
        app=site.name,
        extension_id=overlay.get("extension_id"),
        domain=overlay.get("domain"),
        type=overlay.get("type"),
    )


# Backward-compatible alias used by tests.ui.test_failure_demo.  Kept as a
# plain alias (no wrapper function) so there is exactly one callable to
# maintain — same docstring, same signature, same behaviour.
run_block_assertion = _open_and_expect_blocked


@allure.epic("Prompt Security")
@allure.feature("Web GenAI Access Policy Enforcement")
@allure.story("Baseline (no extension installed)")
@pytest.mark.ui
@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.usefixtures("browser_context_plain")
class TestWithoutExtension:
    """No extension → no policy: every GenAI host must reach an unfiltered web origin.

    Each test runs in its own freshly-launched Chromium ``BrowserContext``
    (function-scoped fixture, see :mod:`tests.conftest`); the test opens the
    target site directly in that fresh browser.  The pass criterion is
    intentionally minimal — *the navigation was not intercepted by an
    extension overlay* — because vendor login walls and regional redirects
    can change the final host. The actual landing URL is captured in Allure
    for review.
    """

    def setup_method(self) -> None:
        self.checker = SoftAssert()

    def teardown_method(self) -> None:
        self.checker.assert_all()

    @allure.title("Without extension — ChatGPT loads (Result: No Block)")
    @allure.description(
        "**Scenario:** Vanilla Chromium, no extension loaded.\n\n"
        "**Action:** Navigate to https://chatgpt.com/.\n\n"
        "**Expected:** The browser reaches a normal web origin "
        "(scheme `https`, *not* `chrome-extension://…/pageOverlay.html`). "
        "The exact final host may vary if ChatGPT shows a login wall or regional "
        "redirect, but the navigation must NOT be intercepted by an extension overlay — "
        "this proves the host is reachable in the absence of any policy.\n\n"
        "**Why this matters:** This is the *baseline* — it ensures any block "
        "observed in the with-extension class is attributable to the extension's "
        "policy enforcement, not to the network or the test environment."
    )
    async def test_chatgpt_loads_unblocked(self) -> None:
        """Baseline: ChatGPT is reachable without the extension installed.

        Steps:
        1. Open a page in the per-test browser — plain Chromium, no extension loaded
        2. Navigate to https://chatgpt.com/
        3. Capture post-navigation state (final URL, scheme)
        4. Assert final URL scheme is https/http, NOT chrome-extension (no block overlay)
        5. Assert no block-overlay snapshot was recorded
        """
        await _open_and_expect_unblocked(
            self,
            CHATGPT,
            context="no extension installed in this fixture",
        )

    @allure.title("Without extension — Gemini loads (Result: No Block)")
    @allure.description(
        "**Scenario:** Vanilla Chromium, no extension loaded.\n\n"
        "**Action:** Navigate to https://gemini.google.com/.\n\n"
        "**Expected:** Final URL is a normal web origin (often `accounts.google.com` "
        "if the user is signed-out — that's still 'site reachable'). "
        "The navigation must NOT land on `chrome-extension://…/pageOverlay.html`."
    )
    async def test_gemini_loads_unblocked(self) -> None:
        """Baseline: Gemini is reachable without the extension installed.

        Steps:
        1. Open a page in the per-test browser — plain Chromium, no extension loaded
        2. Navigate to https://gemini.google.com/
        3. Capture post-navigation state (final URL may be accounts.google.com if signed out)
        4. Assert final URL scheme is https/http, NOT chrome-extension (no block overlay)
        5. Assert no block-overlay snapshot was recorded
        """
        await _open_and_expect_unblocked(
            self,
            GEMINI,
            context="no extension installed in this fixture",
        )

    @allure.title("Without extension — Claude AI loads (Result: No Block)")
    @allure.description(
        "**Scenario:** Vanilla Chromium, no extension loaded.\n\n"
        "**Action:** Navigate to https://claude.ai/.\n\n"
        "**Expected:** Final URL is a normal web origin (often Claude's login page). "
        "The navigation must NOT land on `chrome-extension://…/pageOverlay.html`."
    )
    async def test_claude_loads_unblocked(self) -> None:
        """Baseline: Claude AI is reachable without the extension installed.

        Steps:
        1. Open a page in the per-test browser — plain Chromium, no extension loaded
        2. Navigate to https://claude.ai/
        3. Capture post-navigation state (final URL is often Claude's login page)
        4. Assert final URL scheme is https/http, NOT chrome-extension (no block overlay)
        5. Assert no block-overlay snapshot was recorded
        """
        await _open_and_expect_unblocked(
            self,
            CLAUDE,
            context="no extension installed in this fixture",
        )


@allure.epic("Prompt Security")
@allure.feature("Web GenAI Access Policy Enforcement")
@allure.story("With Prompt Security extension installed and configured")
@pytest.mark.ui
@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.usefixtures("browser_context_with_extension")
class TestWithExtension:
    """Extension installed + popup configured → policy = allow ChatGPT, block Gemini & Claude AI.

    Each test launches its own Chromium browser, loads the unpacked extension,
    configures the popup with the API domain + key, runs the
    policy-activation barrier, then opens the target site and runs its
    assertion. The block assertions check the structured query parameters of
    the extension's overlay URL (``type=blockPage``, ``domain=<expected>``)
    and the runtime extension id — making the failure messages precise and
    actionable.

    The per-test browser launch (~3-5 s extra per test) is the deliberate cost
    for resilience against Cloudflare's bot-score accumulation: every test
    appears as a first-time visitor with no shared cookies / fingerprint state.
    """

    def setup_method(self) -> None:
        self.checker = SoftAssert()

    def teardown_method(self) -> None:
        self.checker.assert_all()

    @allure.title("With extension — ChatGPT loads (Result: No Block — allow policy)")
    @allure.description(
        "**Scenario:** Chromium with the Prompt Security extension loaded and configured.\n\n"
        "**Policy:** ChatGPT is on the *allow* list for the configured tenant.\n\n"
        "**Action:** Navigate to https://chatgpt.com/.\n\n"
        "**Expected:** Final URL is a normal web origin under `chatgpt.com` "
        "(or vendor login wall) — NOT `chrome-extension://…/pageOverlay.html`. "
        "This proves the extension applies the *allow* rule rather than blocking everything."
    )
    async def test_chatgpt_loads_unblocked(self) -> None:
        """With extension + allow policy: ChatGPT loads normally.

        Steps:
        1. Open a page in the per-test browser — extension is loaded and configured
        2. Navigate to https://chatgpt.com/ (tenant policy: allow)
        3. Capture post-navigation state (final URL, scheme)
        4. Assert final URL scheme is https/http (NOT the extension block overlay)
        5. Assert no block-overlay snapshot was recorded
        """
        await _open_and_expect_unblocked(
            self,
            CHATGPT,
            context="extension installed; ChatGPT is on the allow list",
        )

    @allure.title("With extension — Gemini blocked (Result: Block — Access Denied overlay)")
    @allure.description(
        "**Scenario:** Chromium with the Prompt Security extension loaded and configured.\n\n"
        "**Policy:** Gemini is on the *block* list for the configured tenant.\n\n"
        "**Action:** Navigate to https://gemini.google.com/.\n\n"
        "**Expected:** The extension intercepts the navigation and the user lands on\n"
        "`chrome-extension://<runtime-id>/html/pageOverlay.html?type=blockPage&domain=gemini.google.com&originalUrl=…`.\n\n"
        "We verify, in order:\n"
        "1. Final URL scheme is `chrome-extension`.\n"
        "2. The overlay was served by the **same** extension id resolved by the fixture (`self.chrome_extension_id`).\n"
        "3. Query parameter `type=blockPage`.\n"
        "4. Query parameter `domain=gemini.google.com`.\n"
        "5. DOM markers `Access Denied` (`.title-text`) and the `Powered by: prompt.security` footer link "
        "(`.powered-by`) are present (best-effort; recorded as Allure detail).\n\n"
        "Failing on parsed query params (vs. fragile DOM heuristics) makes the error message itself the diagnosis."
    )
    async def test_gemini_blocked(self) -> None:
        """With extension + block policy: Gemini is intercepted by the Access Denied overlay.

        Steps:
        1. Open a page in the per-test browser — extension is loaded with the block-policy key
        2. Navigate to https://gemini.google.com/ (tenant policy: block)
        3. Wait for the extension's pageOverlay.html bundle to populate the static template
        4. Assert final URL scheme is chrome-extension and path ends with /html/pageOverlay.html
        5. Assert overlay served by the resolved runtime extension id (self.chrome_extension_id)
        6. Assert query param type=blockPage
        7. Assert query param domain=gemini.google.com
        8. Assert DOM markers: .title-text contains "Denied", .message-title mentions "blocked",
           .powered-by branding container present
        """
        await _open_and_expect_blocked(self, GEMINI)

    @allure.title("With extension — Claude AI blocked (Result: Block — Access Denied overlay)")
    @allure.description(
        "**Scenario:** Chromium with the Prompt Security extension loaded and configured.\n\n"
        "**Policy:** Claude AI is on the *block* list for the configured tenant.\n\n"
        "**Action:** Navigate to https://claude.ai/.\n\n"
        "**Expected:** The extension intercepts the navigation and the user lands on\n"
        "`chrome-extension://<runtime-id>/html/pageOverlay.html?type=blockPage&domain=claude.ai&originalUrl=…`.\n\n"
        "Same checks as the Gemini case (URL scheme + extension id + query params + DOM markers), "
        "with `domain=claude.ai`."
    )
    async def test_claude_blocked(self) -> None:
        """With extension + block policy: Claude AI is intercepted by the Access Denied overlay.

        Steps:
        1. Open a page in the per-test browser — extension is loaded with the block-policy key
        2. Navigate to https://claude.ai/ (tenant policy: block)
        3. Wait for the extension's pageOverlay.html bundle to populate the static template
        4. Assert final URL scheme is chrome-extension and path ends with /html/pageOverlay.html
        5. Assert overlay served by the resolved runtime extension id (self.chrome_extension_id)
        6. Assert query param type=blockPage
        7. Assert query param domain=claude.ai
        8. Assert DOM markers: .title-text contains "Denied", .message-title mentions "blocked",
           .powered-by branding container present
        """
        await _open_and_expect_blocked(self, CLAUDE)
