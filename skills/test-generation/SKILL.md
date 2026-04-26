---
name: test-generation
description: Turn requirements into executable test ideas and pytest structure. Use when you have acceptance criteria or a ticket and need test titles, steps, and marker suggestions. Triggers include "generate tests", "test cases from spec", "Given When Then".
---

# Test generation

## When to use

- New page or API under test
- Need a checklist before writing POM classes

## Output format options

- **Gherkin-style** scenarios
- **pytest** class/method outline with markers

## How to use

1. List preconditions and test data.
2. Write **happy path**, then **negative** and **edge** paths.
3. Map steps to page object methods (`tests/pages/`).

## Reference files

- See project [.cursorrules](../../.cursorrules) for POM and assertion rules.

## Common pitfalls

- Asserting implementation details instead of user-observable behavior.
- Missing cleanup or isolation when tests mutate data.
