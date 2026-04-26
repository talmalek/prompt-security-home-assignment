"""One-off smoke test: verify Notion token + database schema before wiring CI.

Usage:
    export NOTION_TOKEN=ntn_************************
    export NOTION_RUNS_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    uv run python scripts/smoke_notion.py

The script:
    1. Calls GET /v1/databases/{id} with the token.
    2. Prints the database title + a table of (property_name, property_type).
    3. Checks that all 13 required properties exist with the correct types.
    4. Exits 0 on success, 1 on any mismatch — no changes are written to Notion.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.notion_client import STATUS_DONE, STATUS_FAILED, STATUS_KNOWN_FAILURES  # noqa: E402

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

REQUIRED_PROPERTIES: dict[str, str] = {
    "Run": "title",
    "Status": "select",
    "Started": "date",
    "Duration (s)": "number",
    "Total": "number",
    "Passed": "number",
    "Failed": "number",
    "Skipped": "number",
    "Branch": "rich_text",
    "Commit": "rich_text",
    "CI Run": "url",
    "Allure Report": "url",
    "Triggered by": "rich_text",
}

# Status labels installed by scripts/reshape_notion_page.py. Sourced from the
# Notion client so renaming a status is a one-file change.
REQUIRED_SELECT_OPTIONS: set[str] = {STATUS_DONE, STATUS_KNOWN_FAILURES, STATUS_FAILED}


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")


def _ok(msg: str) -> None:
    print(f"OK    {msg}")


def main() -> int:
    token = os.environ.get("NOTION_TOKEN", "").strip()
    db_id = os.environ.get("NOTION_RUNS_DATABASE_ID", "").strip()

    if not token:
        _fail("NOTION_TOKEN is not set in the environment")
        return 1
    if not db_id:
        _fail("NOTION_RUNS_DATABASE_ID is not set in the environment")
        return 1

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    print(f"Target database: {db_id}\n")

    try:
        response = httpx.get(f"{NOTION_API}/databases/{db_id}", headers=headers, timeout=15)
    except httpx.HTTPError as exc:
        _fail(f"Transport error talking to Notion: {exc}")
        return 1

    if response.status_code == 401:
        _fail("401 Unauthorized — token is invalid or was revoked")
        return 1
    if response.status_code == 404:
        _fail(
            "404 Not found — either the database ID is wrong, "
            "or the 'qa-boilerplate' integration was not connected to the page "
            "containing this database (Page '...' → Connections → Add connections)."
        )
        return 1
    if response.status_code >= 400:
        _fail(f"HTTP {response.status_code}: {response.text[:400]}")
        return 1

    payload = response.json()
    title_parts = payload.get("title", [])
    title = "".join(part.get("plain_text", "") for part in title_parts) or "<untitled>"
    _ok(f"Reachable. Database title: {title!r}")

    properties: dict[str, dict] = payload.get("properties", {})
    print("\nProperties found:")
    for name, prop in sorted(properties.items()):
        print(f"  - {name!r:32} type={prop.get('type')}")

    print()
    problems: list[str] = []

    for name, expected_type in REQUIRED_PROPERTIES.items():
        prop = properties.get(name)
        if prop is None:
            problems.append(f"missing property {name!r}")
            continue
        actual_type = prop.get("type")
        if actual_type != expected_type:
            problems.append(f"property {name!r} has type {actual_type!r}, expected {expected_type!r}")

    status_prop: dict = properties.get("Status", {})
    if status_prop.get("type") == "select":
        options = {opt.get("name") for opt in status_prop.get("select", {}).get("options", [])}
        missing = REQUIRED_SELECT_OPTIONS - options
        if missing:
            problems.append(f"'Status' select is missing options: {sorted(missing)}")

    if problems:
        print("Schema problems:")
        for p in problems:
            _fail(p)
        return 1

    _ok("All 13 required properties exist with correct types.")
    _ok(f"'Status' select has required options: {', '.join(sorted(REQUIRED_SELECT_OPTIONS))}.")
    print("\nSmoke test passed — ready to wire CI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
