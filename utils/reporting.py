"""Allure attachments for failures and debugging.

These helpers are called from the failure-capture path in
``tests/conftest.py::_capture_failure_artifacts`` which runs inside the
browser-context fixture's ``finally`` block — i.e. during pytest's *teardown*
phase.

Why we don't just call ``allure.attach``
----------------------------------------
``allure-pytest`` wraps each fixture finalizer via
``allure_commons.fixture(...)`` so any ``allure.attach`` call inside a
finalizer is appended to the *finalizer's* ``after_fixture.attachments`` (i.e.
the container's ``afters[].attachments``) rather than the
``TestResult.attachments``.  In the Allure HTML UI that places the failure
screenshot deep inside the test's "Tear down" panel — easy to miss and looks
like the screenshot is gone, even though the PNG is on disk.

Concretely, ``allure_commons/reporter.py::_attach`` resolves the parent via
``self._last_executable()`` which returns the most-recently-pushed
``ExecutableItem`` — during teardown that's the ``after_fixture``, not the
``TestResult``.

The helpers below resolve the active ``TestResult`` from the
``allure-pytest`` listener and attach to it explicitly via the
``parent_uuid=`` kwarg of ``AllureReporter.attach_data``.  That puts the
attachment back on the test's main attachments list (where it appeared
*before* the move from ``pytest_runtest_makereport`` to the fixture
``finally`` block).

If the listener / active ``TestResult`` can't be resolved (e.g. these helpers
are called from outside a test), we fall back to plain ``allure.attach`` so
the data still reaches *somewhere* in the report.
"""

from __future__ import annotations

import allure
from allure_commons._core import plugin_manager
from allure_commons.model2 import TestResult
from allure_commons.reporter import AllureReporter
from allure_commons.types import AttachmentType
from allure_commons.utils import uuid4


def _active_allure_reporter() -> AllureReporter | None:
    """Locate the ``AllureReporter`` exposed by the ``allure-pytest`` listener.

    The listener (``allure_pytest.listener.AllureListener``) registers itself
    with ``allure_commons``'s plugin manager and exposes its reporter as the
    ``allure_logger`` attribute. We don't import the listener class directly
    to keep this helper resilient to allure-pytest internals that aren't part
    of its public API.
    """
    for plugin in plugin_manager.get_plugins():
        reporter = getattr(plugin, "allure_logger", None)
        if isinstance(reporter, AllureReporter):
            return reporter
    return None


def _attach_to_active_test(body: bytes | str, name: str, attachment_type: AttachmentType) -> None:
    """Attach to the active ``TestResult`` regardless of fixture/teardown context.

    Falls back to ``allure.attach`` (which targets the most-recent executable
    item) when the reporter or active ``TestResult`` can't be resolved — that
    branch is unlikely under pytest but keeps the helper safe to call from
    one-off scripts.
    """
    reporter = _active_allure_reporter()
    test = reporter.get_last_item(TestResult) if reporter is not None else None
    if reporter is None or test is None:
        allure.attach(body, name=name, attachment_type=attachment_type)
        return
    reporter.attach_data(
        uuid4(),
        body,
        name=name,
        attachment_type=attachment_type,
        parent_uuid=test.uuid,
    )


def attach_png_bytes(data: bytes, name: str = "screenshot") -> None:
    """Attach raw PNG bytes to the active Allure ``TestResult``.

    Used by the failure-capture path; see module docstring for why we bypass
    the default ``_last_executable()`` resolution.
    """
    _attach_to_active_test(data, name=name, attachment_type=AttachmentType.PNG)


def attach_text(body: str, name: str = "details") -> None:
    _attach_to_active_test(body, name=name, attachment_type=AttachmentType.TEXT)


def attach_page_source(html: str, name: str = "page_source") -> None:
    _attach_to_active_test(html, name=name, attachment_type=AttachmentType.HTML)
