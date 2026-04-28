"""Soft assertions with Allure step integration (non-blocking checks until assert_all).

Two severities are supported:

* **Soft errors** (``check_*`` / ``fail``) — collected during the test, attached
  to the corresponding Allure step as red evidence, and surfaced as a single
  ``pytest.fail`` from :meth:`assert_all`. Subsequent steps inside the test
  body **do** run, so the report shows the full failure surface — not just
  the first one.

* **Best-effort warnings** (``note``) — collected during the test, attached
  to the Allure report as yellow evidence the next time :meth:`assert_all`
  runs, but **never** fail the test. Use for rendering-evidence checks where
  a regression is informational (the page didn't render quite the way we
  expected) but doesn't change the pass/fail verdict.

When :meth:`assert_all` is called
---------------------------------
The test helpers in :mod:`tests.ui.test_policy_enforcement`
(``_open_and_expect_blocked`` / ``_open_and_expect_unblocked``) call
:meth:`assert_all` themselves at the end of the test body — i.e. during the
pytest **call** phase, while the page is still alive — so the failure
screenshot fixture in ``tests/conftest.py`` can capture the live page before
the browser context tears down.  The class-level ``teardown_method`` hooks
also call :meth:`assert_all` as a safety net; that second call is an
idempotent no-op because the errors / warnings have already been drained.

Unexpected exceptions raised inside :meth:`step` (e.g. Playwright timeouts,
network errors) still abort the test — they mean the check itself couldn't
run, which is materially different from a check that ran and returned False.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import allure
import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


class SoftAssert:
    """Collect assertion failures (and best-effort warnings) and report them together."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    @contextmanager
    def step(self, step_name: str) -> Iterator[None]:
        """Allure step that records soft-check failures without aborting the test.

        - Soft check failures recorded inside the block are attached to *this*
          Allure step as a TEXT attachment (so the timeline still shows
          *which* step contributed which errors), and the test continues
          running subsequent steps.
        - Unexpected exceptions inside the block are recorded as failures and
          re-raised — those mean the check itself couldn't run, which is a
          hard error.
        """
        errors_before = len(self.errors)
        with allure.step(step_name):
            try:
                yield
            except Exception as e:
                self.fail(f"{step_name} crashed: {e!s}")
                raise
            new_errors = self.errors[errors_before:]
            if new_errors:
                allure.attach(
                    "\n".join(new_errors),
                    name=f"{step_name} — soft failures",
                    attachment_type=allure.attachment_type.TEXT,
                )

    def check(self, condition: bool, msg: str) -> None:
        if not condition:
            self.errors.append(msg)

    def check_equal(self, *, actual: object, expected: object, message: str) -> None:
        if actual != expected:
            self.errors.append(f"{message}: {actual!r} != {expected!r}")

    def check_not_equal(self, *, a: object, b: object, msg: str) -> None:
        if a == b:
            self.errors.append(f"{msg}: {a!r} == {b!r}")

    def check_in(self, *, item: object, container: object, msg: str) -> None:
        if item not in container:  # type: operator contains
            self.errors.append(f"{msg}: {item!r} not in {container!r}")

    def check_not_in(self, *, item: object, container: object, msg: str) -> None:
        if item in container:  # type: operator contains
            self.errors.append(f"{msg}: {item!r} in {container!r}")

    def check_true(self, condition: bool, msg: str) -> None:
        if not condition:
            self.errors.append(msg)

    def check_false(self, condition: bool, msg: str) -> None:
        if condition:
            self.errors.append(msg)

    def fail(self, msg: str) -> None:
        self.errors.append(msg)

    def note(self, condition: bool, msg: str) -> None:
        """Best-effort check: record as warning evidence, never fail the test.

        Use for assertions where a regression is informational (e.g. an overlay
        DOM marker that the rendering team owns and may revise) rather than a
        signal that the system-under-test is broken. The collected warnings are
        surfaced in Allure at teardown so triage can still see *what* drifted.
        """
        if not condition:
            self.warnings.append(msg)

    def assert_all(self) -> None:
        """Attach warnings, fail with all collected errors, and clear state.

        Idempotent: a second call after the first one fired (or after the
        errors / warnings were already drained) is a no-op. This lets the
        helper functions call ``assert_all()`` at the end of the test body
        — so the failure happens in the **call** phase while the page is
        still alive (enabling the failure screenshot fixture to fire) —
        without the class-level ``teardown_method`` hook double-raising the
        same errors as a teardown ERROR.
        """
        warnings = self.warnings
        errors = self.errors
        self.warnings = []
        self.errors = []
        if warnings:
            allure.attach(
                "\n".join(warnings),
                name="Best-effort checks (rendering evidence)",
                attachment_type=allure.attachment_type.TEXT,
            )
        if errors:
            msg = "Soft assert failures:\n" + "\n".join(errors)
            pytest.fail(msg, pytrace=False)
