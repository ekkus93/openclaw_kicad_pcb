---
name: lint-test
description: Run lint and tests for the current repository only
agent: agent
---

Run lint and tests for the current repository only.

Requirements:
- Work only in the current repository.
- Inspect the repo instructions and choose the appropriate lint/test commands for this repository.
- Run the relevant lint checks for the files or project.
- Run the full relevant test suite for the current repository unless the user asked for a narrower scope.
- If a command fails because dependencies or environment setup are missing, report the first actionable error.
- Do not run commands in sibling repositories.

At the end, report:
- which lint command(s) were run
- which test command(s) were run
- whether lint passed
- whether tests passed
- any failures, skips, or environment blockers
