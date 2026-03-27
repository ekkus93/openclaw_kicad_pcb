---
name: push-master
description: Check in the current changes and push them to master on GitHub
agent: agent
---

Check in the current changes and push them to `master` on GitHub for the current repository only.

Requirements:
- Work only in the current repository.
- Inspect `git status` first.
- Stage the intended changes.
- Create a concise commit message that matches the actual change.
- Pull with rebase from `origin master` before pushing.
- Push to `origin master`.
- If pre-commit hooks fail, fix the reported issue and retry.
- If there are unrelated or unexpected changes that make the commit unsafe, stop and explain what is blocking the push.

Before pushing, make sure the relevant lint/tests for the current repository have passed.

At the end, report:
- the commit SHA
- the commit message
- whether the push to `origin master` succeeded
