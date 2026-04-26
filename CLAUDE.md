# Claude / Codex context

Project-specific guidance for AI assistants lives in **[AGENTS.md](AGENTS.md)**.

Summary:
- **Stack**: Python 3.12, pytest + pytest-asyncio, async Playwright, Allure, httpx + tenacity, pydantic-settings, uv, ruff.
- **Patterns**: POM under `tests/pages/`, `SoftAssert` in `utils/soft_assert.py`, settings in `config/settings.py`.
- **Secrets**: `PROMPT_SECURITY_API_KEY` and Notion token use `pydantic.SecretStr`; the Prompt Security key is required for UI tests but optional at import so Notion scripts can run without it. Never hardcode. If a token leaks, rotate immediately.
- **Reporting**: Allure (engineers, GitHub Pages) + `pytest-reporter-html1` (offline HTML) + optional **Notion** `Test Runs` dashboard for non-engineers.
- **Notion reporter**: async API wrapper at `utils/notion_client.py`, pytest summary plugin at `utils/pytest_summary.py`, CI publisher `scripts/push_to_notion.py` (always exits 0), schema smoke test `scripts/smoke_notion.py`, and one-off page curator `scripts/reshape_notion_page.py` (do not run in CI). Opt-in via `NOTION_TOKEN`; skipped silently when unset.
- **Quality bar**: run ruff + pytest before concluding work. Never break the fail-open contract of the Notion publish step.

If this file is symlinked from a global agents directory, prefer the linked **AGENTS.md** in this repo root.
