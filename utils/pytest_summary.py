"""Tiny pytest plugin that writes `reports/summary.json`.

One machine-readable summary per run, consumed by:
    - scripts/push_to_notion.py (publishes a stakeholder row)
    - future Slack/Teams notifications
    - GitHub Actions step summaries

Always safe to enable: no network calls, no external deps, no failure modes
that could break a pytest run.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
_SUMMARY_PATH = _REPORTS_DIR / "summary.json"


@dataclass
class _State:
    started_at: datetime | None = None
    outcomes: Counter[str] = field(default_factory=Counter)
    marker_counts: Counter[str] = field(default_factory=Counter)
    failed_tests: list[str] = field(default_factory=list)
    seen_nodeids: set[str] = field(default_factory=set)


# One state per pytest process is fine: pytest runs a single session per invocation.
_state = _State()


def pytest_sessionstart(session: pytest.Session) -> None:
    # Reset in case the same process runs pytest twice (e.g. pytest-xdist worker reuse).
    global _state
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
        "pytest_exit_code": int(exitstatus),
        "markers": dict(_state.marker_counts),
        "failed_tests": _state.failed_tests,
    }

    try:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        _SUMMARY_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        # Never fail the session because the summary couldn't be written.
        pass
