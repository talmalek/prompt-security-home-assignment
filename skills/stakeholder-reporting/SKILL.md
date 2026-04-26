---
name: stakeholder-reporting
description: Maintain the Notion "Test Runs" stakeholder dashboard and its API wrapper. Use when modifying the publish flow, adding columns, debugging missing rows, rotating tokens, or curating the stakeholder page. Triggers include "Notion", "stakeholder report", "push_to_notion", "reshape_notion_page", "smoke_notion", "Test Runs database", "SecretStr".
---

# Stakeholder reporting (Notion)

The Notion reporter is the repo's **API-wrapper example**. It publishes one summary row per CI run to a `Test Runs` database, linked back to Allure on GitHub Pages for drill-down. It is opt-in and fail-open.

## When to use

- Adding or renaming a column in the `Test Runs` database.
- Debugging a missing / duplicated / miscolored Notion row after a CI run.
- Rotating `NOTION_TOKEN` or moving to a customer workspace.
- Re-curating the stakeholder page layout after a template/Notion UI change.
- Mirroring this pattern for a new external-API integration.

## Component map

| Role | File | Contract |
|---|---|---|
| Settings (SecretStr) | `config/settings.py` → `NotionConfig` | Token in `SecretStr`, `runs_database_id` plain, `enabled` property gates everything downstream. |
| Pytest plugin | `utils/pytest_summary.py` | Writes `reports/summary.json`. No network, never raises. |
| API wrapper | `utils/notion_client.py` | httpx + tenacity + pydantic. Narrow surface: `retrieve_database`, `create_run_row`. No pytest/env coupling. |
| CI publisher | `scripts/push_to_notion.py` | Reads summary + `GITHUB_*`, builds `TestRunRow`, publishes. **Always exits 0.** |
| Schema pre-flight | `scripts/smoke_notion.py` | Read-only token + schema check. |
| Page curator | `scripts/reshape_notion_page.py` | One-off, idempotent. Preserves the `child_database` block. Do not run in CI. |
| CI wiring | `.github/workflows/ci.yml` | Summary upload artifact + Notion publish step gated by `env.NOTION_TOKEN != ''` + `continue-on-error: true`. |

## How to use

1. **Before touching code**, run `uv run python scripts/smoke_notion.py` with a valid `NOTION_TOKEN` to confirm token + schema + integration access to the parent page.
2. **Adding a column**:
   - Update `TestRunRow` and `to_notion_properties` in `utils/notion_client.py`.
   - Update the Notion database schema (UI or API) to match the new property type.
   - Re-run `scripts/smoke_notion.py`; fix any property-type mismatches before merging.
3. **Debugging a missing row**:
   - Check the CI job logs for the "Publish summary to Notion" step — `continue-on-error` means it can fail silently green.
   - Confirm `NOTION_TOKEN` (Secret) and `NOTION_RUNS_DATABASE_ID` (Variable) are set: `gh secret list` / `gh variable list`.
   - Confirm the integration is connected to the **parent page** containing the database (Notion API returns 404 otherwise, even with a valid token).
4. **Rotating a leaked token**:
   - Revoke and reissue at [Notion → Integrations](https://www.notion.so/profile/integrations).
   - `gh secret set NOTION_TOKEN` in the repo.
   - Re-run the smoke script locally if you need to debug.
5. **Re-curating the page**: `NOTION_TOKEN=... uv run python scripts/reshape_notion_page.py` — idempotent. Preserves the `Test Runs` child database and all its rows.

## Output format options

- For status-column changes, update the legend in `scripts/reshape_notion_page.py::_new_narrative` and re-run it so stakeholders see the new meaning.
- For mid-suite changes, use the `TestRunRow` pydantic model as the single source of truth and mirror fields into the Notion database schema.

## Reference files

- Full design narrative: [README.md → Stakeholder dashboard (Notion)](../../README.md#stakeholder-dashboard-notion).
- Scoped Cursor rule: [`.cursor/rules/notion.mdc`](../../.cursor/rules/notion.mdc).
- Repo-wide secrets policy: [AGENTS.md → Secrets policy](../../AGENTS.md#secrets-policy-non-negotiable).

## Common pitfalls

- **Leaking the token in a terminal or chat.** Rotate, don't "just be careful next time".
- **Turning CI red on Notion failure.** `push_to_notion.py` must always exit 0; the CI step keeps `continue-on-error: true`.
- **Archiving the `child_database` block** in `reshape_notion_page.py` — that would delete all historical rows. The script preserves it explicitly; keep that invariant if you refactor.
- **Coupling the wrapper to pytest / `GITHUB_*` env.** Keep that concern in `scripts/push_to_notion.py`; the wrapper stays reusable.
- **Adding a `TestRunRow` field without updating the Notion schema.** Notion will reject the payload and the row will be silently skipped (because `continue-on-error`). Run `smoke_notion.py` after every schema change.
