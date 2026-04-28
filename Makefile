.PHONY: install extension test smoke headed lint format report trace clean

install:
	uv sync --all-groups
	uv run playwright install --with-deps chromium

extension:
	uv run python scripts/fetch_extension.py --force

test:
	uv run pytest

smoke:
	uv run pytest -m smoke

headed:
	TEST_HEADLESS=false uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check . --fix
	uv run ruff format .

report:
	uv run allure serve reports/allure-results

trace:
	@ls reports/traces/*.zip 2>/dev/null | head -1 | xargs -I {} uv run playwright show-trace {}

clean:
	rm -rf reports/allure-results reports/report.html reports/videos reports/traces reports/screenshots reports/logs .pytest_cache .ruff_cache
