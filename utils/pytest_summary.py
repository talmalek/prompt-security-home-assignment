"""Tiny pytest plugin that writes `reports/summary.json`.

One machine-readable summary per run, consumed by:
    - scripts/push_to_notion.py (publishes a stakeholder row)
    - future Slack/Teams notifications
    - GitHub Actions step summaries

Always safe to enable: no network calls, no external deps, no failure modes
that could break a pytest run.

Append mode
-----------
When ``PYTEST_SUMMARY_APPEND=1`` is set (typically on a follow-up CI step),
the plugin loads the summary written by a previous pytest invocation and
*merges* the new run's counts into it instead of overwriting. This keeps a
single coherent summary across the workflow's split production/demo steps so
downstream consumers (Notion, Slack, GitHub step summaries) see the full
8-test picture rather than only the second invocation's 2 demo tests.

Merge semantics:
* counts (``passed``/``failed``/``skipped``/``errors``): summed across runs
  (the new run's outcomes are added on top of the hydrated prior totals).
* ``markers``: **re-collected fresh on every invocation** — pytest collects
  the full test superset on every run and only filters via ``-m`` at
  execution, so hydrating marker_counts from the prior summary would
  double-count every marker. ``pytest_collection_modifyitems`` rebuilds
  the counter from scratch each session; the prior ``markers`` dict is
  discarded.
* ``failed_tests``: hydrated list from the prior summary plus appends from
  the new run. The CI workflow only merges disjoint test sets (production
  uses ``-m "not demo"``, demo uses ``-m "demo"``), so the result is a
  union; if you ever merge overlapping sets the same nodeid could appear
  twice.
* ``started_at``: kept from the earliest run (so duration covers both).
* ``finished_at``: taken from the most recent run.
* ``duration_seconds``: derived from earliest start to latest finish.
* ``pytest_exit_code``: ``max`` of all runs (worst non-zero wins).
* ``exit_status``: re-derived from cumulative counts (``passed`` /
  ``failed`` / ``partial`` / ``no-tests``).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
_SUMMARY_PATH = _REPORTS_DIR / "summary.json"
_APPEND_ENV_VAR = "PYTEST_SUMMARY_APPEND"


@dataclass
class _State:
    started_at: datetime | None = None
    outcomes: Counter[str] = field(default_factory=Counter)
    marker_counts: Counter[str] = field(default_factory=Counter)
    failed_tests: list[str] = field(default_factory=list)
    seen_nodeids: set[str] = field(default_factory=set)
    # Worst pytest_exit_code observed across merged runs. ``0`` until a real
    # exit code is recorded (in ``pytest_sessionfinish``). Only populated when
    # we hydrate state from an existing on-disk summary in append mode.
    prior_exit_code: int = 0


_state = _State()


def _append_mode() -> bool:
    """Return True iff append-mode is enabled via ``PYTEST_SUMMARY_APPEND=1``."""
    return os.environ.get(_APPEND_ENV_VAR, "").strip() in {"1", "true", "True", "yes"}


def _hydrate_from_existing_summary() -> _State:
    """Load a previous run's summary and reconstruct ``_State`` for merging.

    Tolerant of any read/parse failure — append mode degrades gracefully to
    "fresh run" when the prior summary is missing or malformed (matches the
    rest of this plugin's never-fail-the-session contract).
    """
    if not _SUMMARY_PATH.is_file():
        return _State(started_at=datetime.now(UTC))
    try:
        prev = json.loads(_SUMMARY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _State(started_at=datetime.now(UTC))

    started_iso = prev.get("started_at")
    try:
        started_at = datetime.fromisoformat(started_iso) if started_iso else datetime.now(UTC)
    except ValueError:
        started_at = datetime.now(UTC)

    outcomes: Counter[str] = Counter()
    for key in ("passed", "failed", "skipped", "errors"):
        value = int(prev.get(key, 0) or 0)
        if value:
            # Match pytest's outcome vocabulary used by `_record`:
            # "passed" / "failed" / "skipped" / "error" (singular).
            outcomes[key.rstrip("s") if key == "errors" else key] = value

    failed_tests = list(prev.get("failed_tests") or [])
    # Intentionally NOT hydrated: ``marker_counts``. Pytest *collects* every
    # test (then deselects via ``-m``), so each invocation in the split CI
    # workflow re-collects the full superset of tests and re-populates the
    # marker Counter from scratch. Hydrating from the prior summary would
    # therefore double-count every marker. The new run's
    # ``pytest_collection_modifyitems`` will rebuild this Counter with the
    # correct totals.

    return _State(
        started_at=started_at,
        outcomes=outcomes,
        marker_counts=Counter(),
        failed_tests=failed_tests,
        # ``seen_nodeids`` is reset because the *new* run may legitimately re-run
        # the same node-ids; we de-dupe within a single session, not across.
        seen_nodeids=set(),
        prior_exit_code=int(prev.get("pytest_exit_code", 0) or 0),
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Initialise per-session state.

    In append mode we hydrate state from the prior run's summary so this
    session's counts merge into it. In normal mode we start fresh — same as
    the original behaviour.
    """
    global _state
    if _append_mode():
        _state = _hydrate_from_existing_summary()
    else:
        _state = _State(started_at=datetime.now(UTC))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        for marker in item.iter_markers():
            _state.marker_counts[marker.name] += 1


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    # Record each test once: on 'call' for normal outcomes, or on setup/teardown failures.
    if report.when == "call":
        _record(report)
    elif report.when in {"setup", "teardown"} and report.failed:
        _record(report)


def _record(report: pytest.TestReport) -> None:
    if report.nodeid in _state.seen_nodeids:
        return
    _state.seen_nodeids.add(report.nodeid)
    outcome = report.outcome  # "passed" | "failed" | "skipped"
    _state.outcomes[outcome] += 1
    if outcome == "failed":
        _state.failed_tests.append(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    finished_at = datetime.now(UTC)
    duration = (finished_at - _state.started_at).total_seconds() if _state.started_at else 0.0

    passed = _state.outcomes.get("passed", 0)
    failed = _state.outcomes.get("failed", 0)
    skipped = _state.outcomes.get("skipped", 0)
    errors = _state.outcomes.get("error", 0)
    total = passed + failed + skipped + errors

    if total == 0:
        exit_status = "no-tests"
    elif failed == 0 and errors == 0:
        exit_status = "passed"
    elif passed == 0 and (failed + errors) > 0:
        exit_status = "failed"
    else:
        exit_status = "partial"

    # In append mode the worst exit code across all merged runs wins, so a
    # green follow-up run can't mask a failed earlier run.
    combined_exit_code = max(int(exitstatus), _state.prior_exit_code)

    summary = {
        "started_at": _state.started_at.isoformat() if _state.started_at else None,
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(duration, 2),
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
        "exit_status": exit_status,
        "pytest_exit_code": combined_exit_code,
        "markers": dict(_state.marker_counts),
        "failed_tests": _state.failed_tests,
    }

    try:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        _SUMMARY_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        # Never fail the session because the summary couldn't be written.
        pass
