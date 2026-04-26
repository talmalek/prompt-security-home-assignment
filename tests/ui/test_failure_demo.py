"""Intentional-failure demo: extension loaded with an OPEN-POLICY API key.

Purpose
-------
Prove the full failure-reporting pipeline end-to-end:

    * Allure step diffs show *which* assertion failed and *why*.
    * ``pytest_runtest_makereport`` hook fires and attaches:
        - ``failure_screenshot`` (PNG of the live page at the moment of failure).
        - ``page_source`` (raw HTML, helps diff the real site against the
          expected block overlay).
    * Trace ZIP lands in ``reports/traces/TestFailureDemo.zip`` for
      Playwright Inspector post-mortem.

How it works
------------
The ``browser_context_with_open_extension`` fixture is identical to
``browser_context_with_extension`` except the popup is configured with a
**hardcoded open-policy key** — a real Prompt Security API key that has no
block rules on its tenant.  The extension authenticates successfully but
receives an empty policy, so it defaults to "allow all" and never shows the
block overlay.

``TestFailureDemo`` is a **standalone** class (no inheritance from
``TestWithExtension``).  It calls :func:`run_block_assertion` — the
module-level helper exported from ``test_policy_enforcement`` — which
delegates to the same assertion body used by the production tests.  This
keeps the assertion code in exactly one place while avoiding the pytest
fixture-marker accumulation problem that arises with class inheritance
(pytest collects *all* ``usefixtures`` markers up the MRO, which would
launch two browser contexts for the same user-data directory and cause a
``SingletonLock`` collision).

Both tests are expected to fail at the ``scheme == 'chrome-extension'``
step: without a block policy the sites load normally (``https://…``), so the
block-overlay assertion fires and produces the failure evidence.

Marker & CI
-----------
All tests in this file carry ``@pytest.mark.demo``.  CI runs the full
suite including these tests; the pytest step uses ``continue-on-error: true``
so intentional failures never turn the overall workflow red.  A
``::warning::`` annotation is emitted in the CI log when any test fails.

Run locally (intentional failures + failure-screenshot evidence)::

    uv run pytest tests/ui/test_failure_demo.py -v
    uv run allure serve reports/allure-results

Removal checklist
-----------------
To remove the demo entirely:

1.  ``git rm tests/ui/test_failure_demo.py``
2.  In ``tests/ui/test_policy_enforcement.py``: remove the ``run_block_assertion``
    module-level function at the bottom (≈ 20 lines).
3.  In ``tests/conftest.py``: delete the ``api_key_override`` keyword argument
    and its surrounding lines (≈ 5-line diff).
4.  In ``pyproject.toml``: remove the ``demo: …`` marker line.

Nothing else changes — the production test classes are completely unaffected.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import allure
import pytest
from pydantic import SecretStr

from tests.conftest import _persistent_context_lifecycle
from tests.pages.web_app_page import CLAUDE, GEMINI
from tests.ui.test_policy_enforcement import run_block_assertion
from utils.logger import logger
from utils.soft_assert import SoftAssert

# Intentional hard-code: a real Prompt Security API key that has *no block rules*
# configured on its tenant.  The extension authenticates successfully but receives
# an empty policy, so it defaults to "allow all" — Gemini and Claude AI load
# normally, which is exactly what makes the block assertions fail.
# This key is intentionally committed for demo/interview purposes (open repo).
# It has no sensitive policy data associated with it.
_OPEN_POLICY_API_KEY = SecretStr("cc6a6cfc-9570-4e5a-b6ea-92d2adac90e4")
_OPEN_POLICY_API_DOMAIN = "eu.prompt.security"


@pytest.fixture(scope="class")
async def browser_context_with_open_extension(
    request: pytest.FixtureRequest,
) -> AsyncIterator[None]:
    """Extension loaded + popup configured with the *open-policy* API key.

    Shares the full lifecycle with ``browser_context_with_extension``
    (persistent context, Xvfb compatibility, tracing, user-data dir) — the
    only difference is the API key passed to the extension popup.

    To test a *different* policy tenant in a future test class, create a
    similar fixture and pass its key via ``api_key_override``.
    """
    cls_name = request.cls.__name__ if request.cls else "session"
    async with _persistent_context_lifecycle(
        cls_name=cls_name,
        with_extension=True,
        api_key_override=_OPEN_POLICY_API_KEY,
    ) as (ctx, ext_id):
        request.cls._playwright_loop = asyncio.get_running_loop()
        request.cls.context = ctx
        request.cls.chrome_extension_id = ext_id
        yield


@allure.epic("Prompt Security")
@allure.feature("Web GenAI Access Policy Enforcement")
@allure.story("DEMO — open-policy extension (intentional failures for pipeline verification)")
@pytest.mark.demo
@pytest.mark.ui
@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.usefixtures("browser_context_with_open_extension")
class TestFailureDemo:
    """Intentional failures: block assertions with an extension that has no block rules.

    Uses :func:`run_block_assertion` (module-level helper from
    ``test_policy_enforcement``) instead of inheriting from
    ``TestWithExtension``.  This avoids the pytest fixture-marker accumulation
    problem (inherited ``usefixtures`` causing two browser contexts to fight
    over the same user-data directory).

    Both tests fail because the open-policy key lets the sites load normally
    instead of showing the ``chrome-extension://…/pageOverlay.html`` overlay.

    Expected Allure outcome
    ~~~~~~~~~~~~~~~~~~~~~~~
    Each test should show:

    * A red ``[tab N] Final URL scheme is 'chrome-extension'`` step with the
      message *"block expected: final URL should be served by the extension,
      got 'https://…'"*.
    * A ``failure_screenshot`` attachment showing the real site (Gemini / Claude
      AI login page) instead of the Access Denied overlay.
    * A ``page_source`` attachment with the live HTML.
    * A ``<site>_landing.json`` attachment confirming ``scheme=https``.
    """

    # Tab numbering starts at 1 (no ChatGPT tab occupies tab 1 in this class).
    TAB_OFFSET: int = 0

    def setup_method(self) -> None:
        self.checker = SoftAssert()

    def teardown_method(self) -> None:
        self.checker.assert_all()

    @allure.title("[DEMO / EXPECTED FAIL] Open-policy extension — Gemini loads despite block assertion (tab 1)")
    @allure.description(
        "**Purpose:** Verify failure-reporting pipeline: Allure step diff, failure screenshot, page source.\n\n"
        "**Extension API key:** Open-policy key (`cc6a6cfc-…`) — authenticates successfully but has no "
        "block rules configured on its tenant, so the extension defaults to 'allow all' — Gemini loads normally.\n\n"
        "**Expected by assertion (intentionally wrong):** Extension overlay at "
        "`chrome-extension://…/pageOverlay.html?type=blockPage&domain=gemini.google.com`.\n\n"
        "**Actual:** Site loads normally — final URL scheme is `https`, not `chrome-extension`.\n\n"
        "**⚠ This test MUST fail.** A pass would indicate the open-policy key now has a block rule, "
        "which requires investigation."
    )
    async def test_gemini_blocked_open_policy_in_tab1(self) -> None:
        """[EXPECTED FAIL] Open-policy extension: Gemini loads instead of showing block overlay (tab 1).

        Steps:
        1. Open a new browser tab (tab 1) — extension loaded with open-policy API key (no block rules)
        2. Navigate to https://gemini.google.com/ — extension authenticates but has no block rules
        3. Wait for post-navigation state to settle
        4. Assert final URL scheme is chrome-extension (INTENTIONALLY WRONG — causes this test to fail)
        5. Actual: scheme is https — site loaded normally; block-overlay never appeared
        """
        logger.info("Running failure demo: Gemini block assertion (expecting failure)")
        await run_block_assertion(self, GEMINI, tab_index=1)

    @allure.title("[DEMO / EXPECTED FAIL] Open-policy extension — Claude AI loads despite block assertion (tab 2)")
    @allure.description(
        "**Purpose:** Verify failure-reporting pipeline: Allure step diff, failure screenshot, page source.\n\n"
        "**Extension API key:** Open-policy key (`cc6a6cfc-…`) — authenticates successfully but has no "
        "block rules configured on its tenant, so the extension defaults to 'allow all' — Claude AI loads normally.\n\n"
        "**Action:** Open tab 2 → navigate to https://claude.ai/.\n\n"
        "**Expected by assertion (intentionally wrong):** Extension overlay at "
        "`chrome-extension://…/pageOverlay.html?type=blockPage&domain=claude.ai`.\n\n"
        "**Actual:** Site loads normally — final URL scheme is `https`, not `chrome-extension`.\n\n"
        "**⚠ This test MUST fail.** A pass would indicate the open-policy key now has a block rule, "
        "which requires investigation."
    )
    async def test_claude_blocked_open_policy_in_tab2(self) -> None:
        """[EXPECTED FAIL] Open-policy extension: Claude AI loads instead of showing block overlay (tab 2).

        Steps:
        1. Open a new browser tab (tab 2) — extension loaded with open-policy API key (no block rules)
        2. Navigate to https://claude.ai/ — extension authenticates but has no block rules
        3. Wait for post-navigation state to settle
        4. Assert final URL scheme is chrome-extension (INTENTIONALLY WRONG — causes this test to fail)
        5. Actual: scheme is https — site loaded normally; block-overlay never appeared
        """
        logger.info("Running failure demo: Claude AI block assertion (expecting failure)")
        await run_block_assertion(self, CLAUDE, tab_index=2)
