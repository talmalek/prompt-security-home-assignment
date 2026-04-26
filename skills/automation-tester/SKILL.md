---
name: automation-tester
description: Core QA engineering mindset for test planning and prioritization. Use when analyzing what to test, designing test cases, prioritizing by risk, or determining coverage strategy. Triggers include "what should I test", "test strategy", "coverage", "prioritize tests", "P0 P1 P2".
---

# Automation tester mindset

Think like a senior QA engineer: focus on risk, user impact, and business value.

## When to use

- Starting a new home assignment or feature
- Deciding smoke vs regression scope
- Prioritizing cases under time pressure

## Output format options

- **Table**: priority, case title, risk note
- **Bullet list**: P0 / P1 / P2 buckets

## How to use

1. Identify critical user journeys (login, checkout, core workflow).
2. Assign **P0** to blockers, **P1** to major features, **P2** to edge cases.
3. Map cases to folders: `tests/ui/`, future `tests/api/`.

## Reference files

- Add `references/priority-matrix.md` locally if you extend this skill.

## Common pitfalls

- Over-testing cosmetic UI before core flows are stable.
- Duplicating API and UI coverage without clear intent.
