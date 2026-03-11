---
applyTo: "**/*.py,**/pyproject.toml,**/requirements*.txt"
---

# Python-specific instructions

- Prefer pytest.
- Do not introduce mocks unless the user explicitly asks for them.
- For test databases, prefer SQLite unless the task truly requires something else.
- When reviewing code, look for import-time side effects, exception swallowing, hidden state, and weak test coverage.
- For larger changes, propose tests before implementation.
