#!/usr/bin/env python3
"""Reject known stale or incomplete CI configuration."""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(".github/workflows/ci.yml")


def main() -> int:
    text = WORKFLOW.read_text(encoding="utf-8")
    required = (
        "branches: [webapp]",
        "uv sync --frozen --extra dev --extra web",
        "uv run mypy src/kicad_pcb src/kicad_pcb_web",
        "npm run lint",
        "npm run test:run",
        "npm run build",
        "git diff --exit-code -- src/kicad_pcb_web/static/spa",
        "python scripts/package_smoke_test.py",
        "npm run test:e2e",
    )
    forbidden = ("mypy kicad-pcb/src", 'pip install -e ".[dev]"')

    problems = [f"missing required CI fragment: {item}" for item in required if item not in text]
    problems.extend(f"stale CI fragment remains: {item}" for item in forbidden if item in text)
    if problems:
        raise SystemExit("\n".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
