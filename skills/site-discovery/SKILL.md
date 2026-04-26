---
name: site-discovery
description: Explore an unfamiliar application before automating. Use when landing on a new SUT, need locators strategy, or want a short risk note. Triggers include "explore the site", "what to click first", "locator strategy".
---

# Site discovery

## When to use

- First hour on a new assignment URL
- Locators break after a UI refresh

## How to use

1. Map URLs and roles (anonymous vs authenticated).
2. Prefer roles/labels from DevTools accessibility tree.
3. Record flaky areas (animations, lazy load) for smart waits.

## Common pitfalls

- Relying on auto-generated CSS classes.
- Using one huge page object; split by area instead.
