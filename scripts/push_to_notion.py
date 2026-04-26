"""Push the pytest summary to the Notion 'Test Runs' database.

Designed to run as a non-blocking CI step:
    1. Reads `reports/summary.json` written by `utils.pytest_summary`.
    2. Builds a `TestRunRow` using CI env vars (`GITHUB_*`, `ALLURE_PAGES_URL`).
    3. POSTs one row via `utils.notion_client.NotionClient`.

Guarantees
    - Always exits 0 — never fails the CI pipeline even if Notion is down.
    - No-ops silently if `NOTION_TOKEN` / `NOTION_RUNS_DATABASE_ID` are unset.
    - No-ops if `reports/summary.json` is missing.

Run locally (after a pytest run) for a smoke test:
    export NOTION_TOKEN=ntn_...
    export NOTION_RUNS_DATABASE_ID=...
    uv run python scripts/push_to_notion.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
# Make this script runnable directly (`python scripts/push_to_notion.py`) — the
# repo layout uses `pythonpath = ["."]` for pytest, but that only affects test
# runs. Prepend the repo root so `config.*` / `utils.*` imports always resolve.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config.settings import settings  # noqa: E402
from utils.logger import logger  # noqa: E402
from utils.notion_client import NotionClient, TestRunRowBuilder  # noqa: E402

_SUMMARY_PATH = _REPO_ROOT / "reports" / "summary.json"


def _load_summary() -> dict[str, Any] | None:
    if not _SUMMARY_PATH.exists():
        logger.warning("No pytest summary to push", path=str(_SUMMARY_PATH))
        return None
    try:
        return json.loads(_SUMMARY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read pytest summary", error=str(exc))
        return None


def _github_ci_run_url() -> str | None:
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return None


async def _publish() -> None:
    if not settings.notion.enabled:
        logger.info("Notion reporter disabled (missing NOTION_TOKEN or NOTION_RUNS_DATABASE_ID); skipping")
        return

    summary = _load_summary()
    if summary is None:
        return

    builder = TestRunRowBuilder(
        summary=summary,
        branch=os.environ.get("GITHUB_REF_NAME", "local"),
        commit=os.environ.get("GITHUB_SHA", "local"),
        run_number=os.environ.get("GITHUB_RUN_NUMBER", "local"),
        ci_run_url=_github_ci_run_url(),
        allure_report_url=os.environ.get("ALLURE_PAGES_URL") or None,
        triggered_by=os.environ.get("GITHUB_ACTOR", ""),
    )
    row = builder.build()

    assert settings.notion.token is not None  # noqa: S101  (narrowed by `enabled`)
    assert settings.notion.runs_database_id is not None  # noqa: S101

    async with NotionClient(
        token=settings.notion.token,
        api_version=settings.notion.api_version,
        timeout_seconds=settings.notion.timeout_seconds,
    ) as client:
        page_id = await client.create_run_row(settings.notion.runs_database_id, row)
        logger.info("Published run to Notion", page_id=page_id, status=row.status, title=row.run_title)


def main() -> int:
    try:
        asyncio.run(_publish())
    except Exception as exc:
        # CI step also has continue-on-error, but belt-and-suspenders: never surface
        # an exit code — stakeholders' Notion dashboard is best-effort, not a gate.
        logger.warning("Notion publish failed (non-fatal)", error=str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
