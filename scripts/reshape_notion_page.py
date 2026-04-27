"""One-off: curate the Notion QA Status Update page end-to-end.

What it does (idempotent — safe to re-run):
    1. Renames the page to a professional title and sets an explicit icon.
    2. Ensures the `Test Runs` database `Status` select property has the three
       new labels (`✅ Done`, `⚠️ Known failures`, `❌ Failed`) with the right
       colors, then migrates any historical rows from the old
       `passed`/`partial`/`failed` values to the new labels and finally strips
       the legacy options.
    3. Replaces the template's placeholder blocks with a stakeholder-friendly
       narrative (colored section headings, a collapsed "how to read" toggle,
       a divider before the database). Preserves the `Test Runs` child
       database at all costs.

This script is deliberately separate from CI — page content is curated once,
CI only adds rows to the Test Runs database.

Run:
    export NOTION_TOKEN=ntn_...
    uv run python scripts/reshape_notion_page.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.notion_client import (  # noqa: E402
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_KNOWN_FAILURES,
    STATUS_LEGACY_MAP,
)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
PAGE_ID = "34e30027-9170-80b2-be80-efea0c3c55ec"

PAGE_TITLE = "QA Automation Test Runs (Prompt Security)"
PAGE_ICON_EMOJI = "🛡️"

REPO_URL = "https://github.com/talmalek/prompt-security-home-assignment"
ALLURE_URL = "https://talmalek.github.io/prompt-security-home-assignment/"
CI_URL = f"{REPO_URL}/actions/workflows/ci.yml"

STATUS_OPTION_COLORS: dict[str, str] = {
    STATUS_DONE: "green",
    STATUS_KNOWN_FAILURES: "yellow",
    STATUS_FAILED: "red",
}

# Marker chosen to appear ONLY in the latest narrative version.  Changing this
# string forces a full re-insertion on the next run (old blocks are archived by
# _archive_stale_blocks since they no longer match our_ids).
NARRATIVE_MARKER = "6 production tests + 2 intentional-failure demos"


def _rt(text: str, *, bold: bool = False, link: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "type": "text",
        "text": {"content": text, "link": {"url": link} if link else None},
    }
    if bold:
        data["annotations"] = {"bold": True}
    return data


def _paragraph(rich: list[dict[str, Any]], *, color: str = "default") -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": rich, "color": color},
    }


def _heading_2(text: str, *, color: str = "default") -> dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [_rt(text)], "color": color},
    }


def _bullet(rich: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": rich},
    }


def _toggle(rich: list[dict[str, Any]], children: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {"rich_text": rich, "children": children},
    }


def _divider() -> dict[str, Any]:
    return {"object": "block", "type": "divider", "divider": {}}


def _new_narrative() -> list[dict[str, Any]]:
    return [
        _paragraph(
            [
                _rt("Automated UI test results for "),
                _rt("Prompt Security browser extension policy enforcement", bold=True),
                _rt(
                    " — 8 test scenarios total: 6 production tests + 2 intentional-failure demos. "
                    "Each row below is one CI run, with direct links to Allure and "
                    "GitHub Actions for engineer drill-down."
                ),
            ],
            color="gray",
        ),
        _heading_2("🔎 Scope", color="blue_background"),
        _bullet(
            [
                _rt("Application under test: the "),
                _rt(
                    "Prompt Security Chrome extension",
                    bold=True,
                    link="https://chromewebstore.google.com/detail/prompt-security-browser-e/iidnankcocecmgpcafggbgbmkbcldmno",
                ),
                _rt(" enforcing administrator policy on web GenAI apps."),
            ]
        ),
        _bullet(
            [
                _rt("Policy under test: "),
                _rt("chatgpt.com", bold=True, link="https://chatgpt.com/"),
                _rt(" allowed · "),
                _rt("gemini.google.com", bold=True, link="https://gemini.google.com/"),
                _rt(" blocked · "),
                _rt("claude.ai", bold=True, link="https://claude.ai/"),
                _rt(" blocked."),
            ]
        ),
        _bullet(
            [
                _rt("Browser isolation: "),
                _rt("one fresh Chromium per test", bold=True),
                _rt(
                    " — every test launches its own browser on a freshly-wiped user-data "
                    "directory and opens the target site in a single page (no shared tabs, "
                    "no shared cookies, no shared bot-score state). This eliminates "
                    "Cloudflare reputation carry-over and cross-test contamination."
                ),
            ]
        ),
        _bullet(
            [
                _rt("Extension version: "),
                _rt("always the latest published CRX", bold=True),
                _rt(
                    " — every pytest session (local and CI alike) force-fetches the "
                    "currently published Chrome Web Store extension before any browser "
                    "launches.  No local/CI version drift; the suite always validates "
                    "the latest released extension automatically."
                ),
            ]
        ),
        _bullet(
            [
                _rt(
                    "Runner: GitHub Actions · Ubuntu · Python 3.12 · headed Chromium under Xvfb "
                    "(extension load requires headed mode)."
                )
            ]
        ),
        _bullet(
            [
                _rt(
                    "Framework: Pytest + async Playwright (function-scoped "
                    "`launch_persistent_context` with `--load-extension`) + "
                    "Page Object Model + Allure."
                )
            ]
        ),
        _heading_2("✅ Coverage", color="green_background"),
        _bullet(
            [
                _rt("Baseline (3 tests, no extension): "),
                _rt("ChatGPT · Gemini · Claude AI", bold=True),
                _rt(
                    " — each launches its own clean Chromium, navigates to the host, and "
                    "asserts the navigation reached a real web origin (no `chrome-extension://` "
                    "redirect, no overlay snapshot).  Proves blocks observed in the next class "
                    "are caused by the extension, not the environment."
                ),
            ]
        ),
        _bullet(
            [
                _rt("Extension installed — allow (1 test): "),
                _rt("chatgpt.com", bold=True),
                _rt(
                    " — fresh Chromium launched with the latest extension and tenant policy "
                    "active, navigates to ChatGPT and verifies the extension does NOT redirect "
                    "to its block overlay.  Confirms the allow-list policy is respected."
                ),
            ]
        ),
        _bullet(
            [
                _rt("Extension installed — block (2 tests): "),
                _rt("gemini.google.com", bold=True),
                _rt(" and "),
                _rt("claude.ai", bold=True),
                _rt(
                    " — each in its own fresh Chromium with the latest extension. "
                    "Navigation lands on the Prompt Security Access Denied overlay "
                    "(`chrome-extension://<id>/html/pageOverlay.html?…`). Assertions verify "
                    "BOTH the URL query (`type=blockPage`, correct `domain`, "
                    "`canBypass=Prevent`, `useBackendHtml=true`, non-empty `popupToken`) AND "
                    "the rendered DOM of the v7.1.0 backend-rendered overlay "
                    "(`body.ai-site`, `h1.title='Access Denied'`, `p.description` mentioning "
                    "administrator + blocked, `p.guidelines`, `.barrier-illustration` SVG, "
                    "`.powered-by` branding)."
                ),
            ]
        ),
        _bullet(
            [
                _rt("Failure pipeline demo (2 tests, "),
                _rt("expected to fail", bold=True),
                _rt(
                    "): same block assertions run against an extension configured with an "
                    "open-policy API key (no block rules). The sites load normally so the "
                    "assertions fail — triggering failure screenshots, page source, and "
                    "Playwright trace attachments in Allure. CI stays green via "
                    "`continue-on-error: true`."
                ),
            ]
        ),
        _toggle(
            [_rt("📖 How to read the Status column", bold=True)],
            [
                _bullet([_rt(STATUS_DONE, bold=True), _rt(" — all 6 production tests passed")]),
                _bullet(
                    [
                        _rt(STATUS_KNOWN_FAILURES, bold=True),
                        _rt(
                            " — one or more production tests failed (third-party UI drift / "
                            "network issue). The 2 intentional-failure demo tests always appear "
                            "as failures and are expected — they demonstrate the reporting pipeline."
                        ),
                    ]
                ),
                _bullet(
                    [
                        _rt(STATUS_FAILED, bold=True),
                        _rt(
                            " — no tests passed (extension/runner infrastructure problem or "
                            "major regression — investigate immediately)"
                        ),
                    ]
                ),
            ],
        ),
        _heading_2("🔗 Links", color="default"),
        _bullet([_rt("Engineer-facing Allure report", link=ALLURE_URL)]),
        _bullet([_rt("GitHub repository", link=REPO_URL)]),
        _bullet([_rt("CI workflow runs", link=CI_URL)]),
        _heading_2("👥 Maintainers", color="gray_background"),
        _bullet(
            [
                _rt("Tal Malek — "),
                _rt("@talmalek", link="https://github.com/talmalek"),
            ]
        ),
        _divider(),
    ]


def _list_children(client: httpx.Client, block_id: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = client.get(f"/blocks/{block_id}/children", params=params)
        r.raise_for_status()
        data = r.json()
        results.extend(data["results"])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return results


def _is_our_narrative_start(block: dict[str, Any]) -> bool:
    if block["type"] != "paragraph":
        return False
    rich = block["paragraph"].get("rich_text", [])
    text = "".join(x.get("plain_text", "") for x in rich)
    return NARRATIVE_MARKER in text


def _update_page_title_and_icon(client: httpx.Client) -> None:
    print(f"Setting page title → {PAGE_TITLE!r} and icon → {PAGE_ICON_EMOJI}")
    payload = {
        "icon": {"type": "emoji", "emoji": PAGE_ICON_EMOJI},
        "properties": {
            "title": {"title": [{"type": "text", "text": {"content": PAGE_TITLE}}]},
        },
    }
    r = client.patch(f"/pages/{PAGE_ID}", json=payload)
    r.raise_for_status()


def _find_child_database(client: httpx.Client, page_children: list[dict[str, Any]]) -> dict[str, Any]:
    db_blocks = [b for b in page_children if b["type"] == "child_database"]
    if not db_blocks:
        raise RuntimeError("No child_database found on the page — nothing to preserve.")
    return db_blocks[0]


def _sync_status_options(client: httpx.Client, database_id: str) -> None:
    """Make sure the three new labels exist with correct colors.

    Keeps pre-existing options intact on this call so historical rows don't
    lose their value. Legacy options are removed later, *after* migration.
    """
    print("Syncing Test Runs database Status options...")
    r = client.get(f"/databases/{database_id}")
    r.raise_for_status()
    db = r.json()
    current_options = db["properties"]["Status"]["select"]["options"]
    current_names = {o["name"] for o in current_options}

    target_options = list(current_options)
    added: list[str] = []
    for name, color in STATUS_OPTION_COLORS.items():
        if name not in current_names:
            target_options.append({"name": name, "color": color})
            added.append(name)

    if not added:
        print("  all three new options already present")
        return

    r = client.patch(
        f"/databases/{database_id}",
        json={"properties": {"Status": {"select": {"options": target_options}}}},
    )
    r.raise_for_status()
    print(f"  added options: {added}")


def _migrate_status_rows(client: httpx.Client, database_id: str) -> int:
    """Rewrite any row whose Status still holds a legacy value."""
    print("Scanning Test Runs database for legacy Status values...")
    migrated = 0
    cursor: str | None = None
    while True:
        body: dict[str, Any] = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = client.post(f"/databases/{database_id}/query", json=body)
        r.raise_for_status()
        data = r.json()
        for row in data["results"]:
            status_prop = row["properties"].get("Status", {})
            select = status_prop.get("select") or {}
            current = select.get("name")
            if current in STATUS_LEGACY_MAP:
                new_value = STATUS_LEGACY_MAP[current]
                r2 = client.patch(
                    f"/pages/{row['id']}",
                    json={"properties": {"Status": {"select": {"name": new_value}}}},
                )
                r2.raise_for_status()
                print(f"  migrated row {row['id']}: {current!r} → {new_value!r}")
                migrated += 1
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    if migrated == 0:
        print("  no legacy-valued rows found")
    return migrated


def _drop_legacy_status_options(client: httpx.Client, database_id: str) -> None:
    """After migration, keep only the three new labels on the schema."""
    r = client.get(f"/databases/{database_id}")
    r.raise_for_status()
    db = r.json()
    current_options = db["properties"]["Status"]["select"]["options"]
    kept = [o for o in current_options if o["name"] in STATUS_OPTION_COLORS]
    if len(kept) == len(current_options):
        return
    dropped = [o["name"] for o in current_options if o["name"] not in STATUS_OPTION_COLORS]
    print(f"Dropping legacy Status options: {dropped}")
    r = client.patch(
        f"/databases/{database_id}",
        json={"properties": {"Status": {"select": {"options": kept}}}},
    )
    r.raise_for_status()


def _ensure_narrative(client: httpx.Client, page_children: list[dict[str, Any]]) -> None:
    if any(_is_our_narrative_start(b) for b in page_children):
        print("Narrative already present — skipping insertion.")
        return
    anchor_id = page_children[0]["id"]
    print(f"Inserting narrative after: {anchor_id} (type={page_children[0]['type']})")
    r = client.patch(
        f"/blocks/{PAGE_ID}/children",
        json={"children": _new_narrative(), "after": anchor_id},
    )
    r.raise_for_status()
    print("Narrative inserted.")


def _archive_stale_blocks(client: httpx.Client, db_id: str) -> None:
    current = _list_children(client, PAGE_ID)
    narrative_len = len(_new_narrative())
    our_ids: set[str] = set()
    for idx, blk in enumerate(current):
        if _is_our_narrative_start(blk):
            for j in range(idx, min(idx + narrative_len, len(current))):
                our_ids.add(current[j]["id"])
            break

    preserved_ids = {db_id} | our_ids
    to_archive = [b for b in current if b["id"] not in preserved_ids]

    print(f"Archiving {len(to_archive)} placeholder/stale blocks...")
    for blk in to_archive:
        preview = ""
        if blk["type"] in (
            "paragraph",
            "heading_1",
            "heading_2",
            "heading_3",
            "bulleted_list_item",
            "callout",
            "toggle",
        ):
            key = blk["type"]
            rt = blk[key].get("rich_text", [])
            preview = "".join(x.get("plain_text", "") for x in rt)[:60]
        r = client.delete(f"/blocks/{blk['id']}")
        r.raise_for_status()
        print(f"  archived [{blk['type']}] {preview!r}")


def main() -> int:
    token = os.environ.get("NOTION_TOKEN", "").strip()
    if not token:
        print("NOTION_TOKEN is not set", file=sys.stderr)
        return 1

    client = httpx.Client(
        base_url=NOTION_API,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        timeout=20,
    )

    _update_page_title_and_icon(client)

    children = _list_children(client, PAGE_ID)
    db_block = _find_child_database(client, children)
    db_id = db_block["id"]
    print(f"Preserving Test Runs database block: {db_id}")

    _sync_status_options(client, db_id)
    _migrate_status_rows(client, db_id)
    _drop_legacy_status_options(client, db_id)

    _ensure_narrative(client, children)
    _archive_stale_blocks(client, db_id)

    print("\nDone. Refresh the Notion page to see the reshaped layout.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
