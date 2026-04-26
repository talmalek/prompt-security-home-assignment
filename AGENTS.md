# Agent instructions (shared)

This repository automates **Prompt Security Browser Extension** policy checks: async **Playwright** (Chromium **persistent context** with unpacked extension), **pytest**, **Allure**, **POM**, **uv**, **ruff**, plus the same optional **Notion** stakeholder dashboard as the parent boilerplate.

## When editing code
1. Follow [.cursorrules](.cursorrules) and rules under [.cursor/rules/](.cursor/rules/).
2. Keep page objects in `tests/pages/`; tests in `tests/ui/` or `tests/api/`.
3. Run `uv run ruff check .` and `uv run ruff format .` before finishing.
4. Run `uv run pytest` to validate changes.

## Secrets policy (non-negotiable)
- **Never** hardcode credentials, tokens, or API keys in source. `PROMPT_SECURITY_API_KEY` is a `pydantic.SecretStr` in `config/settings.py` (optional at import so `scripts/push_to_notion.py` can run without it; **required** when executing UI tests via the `browser_context_with_extension` fixture).
- Local development reads from a gitignored `.env`; [`.env.example`](.env.example) lists only placeholders.
- CI reads from **GitHub Secrets** (masked) and **Repository Variables** (non-sensitive):
  - `PROMPT_SECURITY_API_KEY` → Secret (Prompt Security API key from the extension vendor).
  - `NOTION_TOKEN` → Secret (Notion integration token, `ntn_…`).
  - `NOTION_RUNS_DATABASE_ID` → Variable (database ID alone is useless without the token).
  - `ALLURE_PAGES_URL` → Variable (public URL).
- `utils/notion_client.py` keeps the token in a `SecretStr` all the way to the `Authorization` header; logs only record method + path.
- If a token leaks to a terminal or a chat log, **rotate it immediately** via [Notion → Integrations](https://www.notion.so/profile/integrations) and update the GitHub Secret with `gh secret set NOTION_TOKEN`.

## Reports
- **Allure**: `reports/allure-results/` → `uv run allure serve reports/allure-results` (requires [Allure CLI](https://docs.qameta.io/allure/#_installing_a_commandline)). Published to **GitHub Pages** on pushes to the default branch.
- **HTML**: `reports/report.html` from [pytest-reporter-html1](https://github.com/christiansandberg/pytest-reporter-html1) (Jinja2 `html1` template; self-contained by default).
- **Notion stakeholder dashboard**: summary row per CI run posted by `scripts/push_to_notion.py` (see below). Opt-in; skipped silently when `NOTION_TOKEN` is unset.

## Notion reporter (opt-in)
The reporter doubles as an **API-wrapper example** alongside the POM layer — agents should maintain the same separation of concerns.

| File | Responsibility | Keep it narrow |
|---|---|---|
| [`utils/notion_client.py`](utils/notion_client.py) | Async `httpx` + `tenacity` wrapper. Pydantic models (`TestRunRow`, `TestRunRowBuilder`). Only exposes `retrieve_database`, `create_run_row`, context-manager lifecycle. | **No** pytest imports, **no** `GITHUB_*` env reads, **no** payload building from environment. |
| [`utils/pytest_summary.py`](utils/pytest_summary.py) | Pytest plugin (`pytest_sessionstart`, `pytest_runtest_logreport`, `pytest_sessionfinish`). Writes `reports/summary.json`. | **No** network calls. **No** failure modes that break the session. Uses module-level state for safety. |
| [`scripts/push_to_notion.py`](scripts/push_to_notion.py) | CI orchestration. Reads `reports/summary.json` + `GITHUB_*` env, builds a `TestRunRow`, publishes via the wrapper. | **Always exits 0** — never breaks CI, even on HTTP errors or schema drift. Settings-gated (`settings.notion.enabled`). |
| [`scripts/smoke_notion.py`](scripts/smoke_notion.py) | Pre-flight: verifies token + database schema without creating rows. Safe to run locally to debug connectivity/permissions before wiring CI. | Read-only against the Notion API. |
| [`scripts/reshape_notion_page.py`](scripts/reshape_notion_page.py) | One-off: archives template placeholder blocks and installs a curated stakeholder narrative above the `Test Runs` database. Idempotent. | **Do not run in CI.** This is content-as-code, executed deliberately (like a DB migration). Preserves the `child_database` block at all costs. |

When editing any of the above:
- Maintain the fail-open contract of `push_to_notion.py`. A failing Notion call must never turn a green test run red.
- If you change the `TestRunRow` schema, update the Notion database (or document manual steps) and re-run `scripts/smoke_notion.py` to verify property types match before pushing CI.
- Keep the Notion step in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) gated by `if: env.NOTION_TOKEN != ''` with `continue-on-error: true`.

See the full design rationale under [Stakeholder dashboard (Notion)](README.md#stakeholder-dashboard-notion) in the README.

## Skills
Progressive-disclosure skills live in [`skills/`](skills/). Relevant to this codebase:
- [`automation-tester`](skills/automation-tester/SKILL.md) — prioritization & coverage.
- [`site-discovery`](skills/site-discovery/SKILL.md) — first-touch exploration.
- [`test-generation`](skills/test-generation/SKILL.md) — spec → test outline.
- [`test-fixer`](skills/test-fixer/SKILL.md) — triage flaky/failing runs.
- [`stakeholder-reporting`](skills/stakeholder-reporting/SKILL.md) — when and how to modify the Notion reporter.

## MCP (optional)
See [.cursor/mcp.json](.cursor/mcp.json) for suggested MCP servers (Playwright + documentation). Enable in Cursor settings as needed.
