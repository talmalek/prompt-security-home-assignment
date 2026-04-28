"""Intentional-failure demo: extension loaded with an OPEN-POLICY API key.

Purpose
-------
Prove the full failure-reporting pipeline end-to-end:

    * Allure step diffs show *which* assertion failed and *why*.
    * ``pytest_runtest_makereport`` hook fires and attaches:
        - ``failure_screenshot`` (PNG of the live page at the moment of failure).
        - ``page_source`` (raw HTML, helps diff the real site against the
          expected block overlay).
    * Trace ZIP lands in ``reports/traces/<test-id>.zip`` for
      Playwright Inspector post-mortem.

How it works
------------
The ``browser_context_with_open_extension`` fixture is identical to
``browser_context_with_extension`` except:

* The popup is configured with a **hardcoded open-policy key** — a real
  Prompt Security API key that has no block rules on its tenant.  The
  extension authenticates successfully but receives an empty policy, so it
  defaults to "allow all" and never shows the block overlay.
* The lifecycle helper is invoked with ``wait_for_block_active=False``.
  The production fixture uses a probe-until-blocked barrier to defeat the
  per-test policy-fetch race; with an open-policy tenant that probe would
  *never* see a block and would therefore reload Gemini until its 30 s
  timeout for nothing.

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
All tests in this file carry ``@pytest.mark.demo``.  CI runs pytest in
**two separate steps**:

* ``Pytest (production)`` — ``-m "not demo"``.  No ``continue-on-error`` —
  a real regression in the 6 production tests turns the workflow red.
* ``Pytest (demo / intentional failures)`` — ``-m "demo"`` with
  ``continue-on-error: true`` and ``PYTEST_SUMMARY_APPEND=1`` (so
  ``utils.pytest_summary`` merges the demo counts into the production
  step's ``reports/summary.json`` instead of clobbering it).

A ``::warning::`` annotation is emitted in the CI log when the demo step
records any failure; the overall workflow stays green.

Run locally (intentional failures + failure-screenshot evidence)::

    uv run pytest tests/ui/test_failure_demo.py -v
    uv run allure serve reports/allure-results

Removal checklist
-----------------
To remove the demo entirely:

1.  ``git rm tests/ui/test_failure_demo.py``
2.  In ``tests/ui/test_policy_enforcement.py``: remove the ``run_block_assertion``
    module-level alias at the bottom (one line).
3.  In ``tests/conftest.py``: delete the ``api_key_override`` and
    ``wait_for_block_active`` keyword arguments and the open-policy probe
    skip branch (≈ 10-line diff).
4.  In ``pyproject.toml``: remove the ``demo: …`` marker line.

Nothing else changes — the production test classes are completely unaffected.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import allure
import pytest
from pydantic import SecretStr

from tests.conftest import _capture_failure_artifacts, _instance_id_for, _persistent_context_lifecycle
from tests.pages.web_app_page import CLAUDE, GEMINI
from tests.ui.test_policy_enforcement import run_block_assertion
from utils.logger import logger
from utils.soft_assert import SoftAssert

# ---------------------------------------------------------------------------
# DEMO ONLY — Home Assignment scope
#
# This open-policy API key is intentionally hardcoded for the home assignment
# demo of the failure-reporting pipeline. The key authenticates successfully
# but has *no block rules* configured on its tenant, so the extension defaults
# to "allow all" — Gemini and Claude AI load normally, which makes the block
# assertions fail and exercises the full failure-reporting pipeline (Allure
# step diff, failure screenshot, page source, Playwright trace).
#
# Why this is committed deliberately:
# * Scope is the home-assignment demo only; this test file will not ship as
#   part of any production codebase.
# * The key has no sensitive policy data attached (open-policy tenant).
# * Reviewers can run the demo without provisioning anything.
#
# Production hardening (NOT done here, intentional):
# * In production this would move to env (e.g.
#   `PROMPT_SECURITY_DEMO_OPEN_POLICY_API_KEY`), be documented in
#   `.env.example`, and the file would `pytest.skip(allow_module_level=True)`
#   when the env var is absent.
# * Do **not** copy this hardcoded pattern into any other test file.
# ---------------------------------------------------------------------------
_OPEN_POLICY_API_KEY = SecretStr("cc6a6cfc-9570-4e5a-b6ea-92d2adac90e4")
_OPEN_POLICY_API_DOMAIN = "eu.prompt.security"


@pytest.fixture
async def browser_context_with_open_extension(
    request: pytest.FixtureRequest,
) -> AsyncIterator[None]:
    """Extension loaded + popup configured with the *open-policy* API key.

    Shares the full lifecycle with ``browser_context_with_extension``
    (persistent context, Xvfb compatibility, tracing, per-test wiped user-data
    dir), with two demo-specific differences:

    * The API key passed to the popup is the open-policy key.
    * ``wait_for_block_active=False`` skips the policy-activation probe — the
      production fixture waits until the extension actually intercepts a
      known-blocked site, which would never happen with an open policy and
      would otherwise reload the probe page until the 30 s timeout.

    To test a *different* policy tenant in a future test class, create a
    similar fixture and pass its key via ``api_key_override``.
    """
    instance_id = _instance_id_for(request)
    async with _persistent_context_lifecycle(
        instance_id=instance_id,
        with_extension=True,
        api_key_override=_OPEN_POLICY_API_KEY,
        wait_for_block_active=False,
    ) as (ctx, ext_id):
        request.instance.context = ctx
        request.instance.chrome_extension_id = ext_id
        try:
            yield
        finally:
            await _capture_failure_artifacts(request)


@allure.epic("Prompt Security")
@allure.feature("Web GenAI Access Policy Enforcement")
@allure.story("DEMO — open-policy extension (intentional failures for pipeline verification)")
@pytest.mark.demo
@pytest.mark.ui
@pytest.mark.asyncio
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

    Like the production classes, these tests run with **per-test browsers** —
    every test launches its own Chromium with the open-policy popup
    configured.  Failure-evidence attachments (``failure_screenshot``,
    ``page_source``, the Playwright trace ZIP) are produced per-test by the
    :func:`tests.conftest._capture_failure_artifacts` helper, which is invoked
    from the ``finally`` block of :func:`browser_context_with_open_extension`
    — that ordering keeps the page alive on the test's own asyncio loop while
    the screenshot is taken.  The ``pytest_runtest_makereport`` hook only
    stashes the per-phase test report on the item; it does not attach
    artifacts itself.

    Expected Allure outcome
    ~~~~~~~~~~~~~~~~~~~~~~~
    Each test should show:

    * A red ``<site>: final URL scheme is 'chrome-extension'`` step with the
      message *"block expected: final URL should be served by the extension,
      got 'https://…'"*.
    * A ``failure_screenshot`` attachment showing the real site (Gemini / Claude
      AI login page) instead of the Access Denied overlay.
    * A ``page_source`` attachment with the live HTML.
    * A ``<site>_landing.json`` attachment confirming ``scheme=https``.
    """

    def setup_method(self) -> None:
        self.checker = SoftAssert()

    def teardown_method(self) -> None:
        self.checker.assert_all()

    @allure.title("[DEMO / EXPECTED FAIL] Open-policy extension — Gemini loads despite block assertion")
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
    async def test_gemini_blocked_open_policy(self) -> None:
        """[EXPECTED FAIL] Open-policy extension: Gemini loads instead of showing block overlay.

        Steps:
        1. Open a page in the per-test browser — extension loaded with open-policy
           API key (no block rules); the production policy-activation probe is
           skipped via `wait_for_block_active=False`
        2. Navigate to https://gemini.google.com/ — extension authenticates but has
           no block rules, so Gemini loads normally (final URL: `https://...`)
        3. Wait for post-navigation state to settle
        4. Assert final URL scheme is `chrome-extension` (INTENTIONALLY WRONG —
           records a soft failure via `SoftAssert.check_equal`)
        5. Assert overlay snapshot present (also soft-fails via `check_in`)
        6. The two soft failures are collected without aborting; the helper
           short-circuits at `if overlay is None: return` after explicitly
           calling `inst.checker.assert_all()`
        7. `assert_all()` raises (during the **call** phase, while the page
           is still alive). That triggers the `finally` block of
           `browser_context_with_open_extension`, which awaits
           `_capture_failure_artifacts(request)` — attaching
           `failure_screenshot`, `page_source`, and the Playwright trace ZIP
           to the Allure result. `teardown_method`'s second `assert_all()`
           is then an idempotent no-op (errors / warnings already drained).
        """
        logger.info("Running failure demo: Gemini block assertion (expecting failure)")
        await run_block_assertion(self, GEMINI)

    @allure.title("[DEMO / EXPECTED FAIL] Open-policy extension — Claude AI loads despite block assertion")
    @allure.description(
        "**Purpose:** Verify failure-reporting pipeline: Allure step diff, failure screenshot, page source.\n\n"
        "**Extension API key:** Open-policy key (`cc6a6cfc-…`) — authenticates successfully but has no "
        "block rules configured on its tenant, so the extension defaults to 'allow all' — Claude AI loads normally.\n\n"
        "**Action:** Navigate to https://claude.ai/.\n\n"
        "**Expected by assertion (intentionally wrong):** Extension overlay at "
        "`chrome-extension://…/pageOverlay.html?type=blockPage&domain=claude.ai`.\n\n"
        "**Actual:** Site loads normally — final URL scheme is `https`, not `chrome-extension`.\n\n"
        "**⚠ This test MUST fail.** A pass would indicate the open-policy key now has a block rule, "
        "which requires investigation."
    )
    async def test_claude_blocked_open_policy(self) -> None:
        """[EXPECTED FAIL] Open-policy extension: Claude AI loads instead of showing block overlay.

        Steps:
        1. Open a page in the per-test browser — extension loaded with open-policy
           API key (no block rules); the production policy-activation probe is
           skipped via `wait_for_block_active=False`
        2. Navigate to https://claude.ai/ — extension authenticates but has no
           block rules, so Claude loads normally (final URL: `https://...`)
        3. Wait for post-navigation state to settle
        4. Assert final URL scheme is `chrome-extension` (INTENTIONALLY WRONG —
           records a soft failure via `SoftAssert.check_equal`)
        5. Assert overlay snapshot present (also soft-fails via `check_in`)
        6. The two soft failures are collected without aborting; the helper
           short-circuits at `if overlay is None: return` after explicitly
           calling `inst.checker.assert_all()`
        7. `assert_all()` raises (during the **call** phase, while the page
           is still alive). That triggers the `finally` block of
           `browser_context_with_open_extension`, which awaits
           `_capture_failure_artifacts(request)` — attaching
           `failure_screenshot`, `page_source`, and the Playwright trace ZIP
           to the Allure result. `teardown_method`'s second `assert_all()`
           is then an idempotent no-op (errors / warnings already drained).
        """
        logger.info("Running failure demo: Claude AI block assertion (expecting failure)")
        await run_block_assertion(self, CLAUDE)
