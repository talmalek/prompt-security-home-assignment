---
name: test-fixer
description: Diagnose failing or flaky tests. Use when CI is red, a step times out, or assertions are ambiguous. Triggers include "flaky", "timeout", "element not found", "fix test".
---

# Test fixer

## When to use

- Intermittent failures in CI
- Locator works locally but not in headless Linux

## How to use

1. Read trace/video under `reports/` and Allure attachments.
2. Check **headless vs headed**, viewport, and throttling.
3. Replace dumb waits with state checks (`expect`, `wait_for_url`).
4. Re-run with `uv run pytest path::test -v`.

## Common pitfalls

- Increasing timeout without fixing the root race.
- Sharing mutable state across tests without isolation.
