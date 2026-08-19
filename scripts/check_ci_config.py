#!/usr/bin/env python3
"""Reject known stale or incomplete CI configuration."""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(".github/workflows/ci.yml")
LIVE_EVALUATION_WORKFLOW = Path(".github/workflows/refinement-live-evaluation.yml")
OLLAMA_CLIENT = Path("src/kicad_pcb_web/services/llm/ollama_client.py")
EVALUATION_CLI = Path("src/kicad_pcb_web/refinement_evaluation_cli.py")


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
    problems.extend(_live_evaluation_problems())
    problems.extend(_ollama_client_problems())
    problems.extend(_evaluation_cli_problems())
    if problems:
        raise SystemExit("\n".join(problems))
    return 0


def _live_evaluation_problems() -> list[str]:
    text = LIVE_EVALUATION_WORKFLOW.read_text(encoding="utf-8")
    required = (
        "name: Phase N3 Live Evaluation",
        "workflow_dispatch:",
        "branches:",
        "- n3-eval-run",
        "confirm_12_fixture_live_run:",
        'AUTO_PROVIDER: "ollama"',
        'AUTO_MODEL: "qwen3-vl:8b"',
        'AUTO_BASE_URL: "http://127.0.0.1:11434"',
        "runs-on: self-hosted",
        'refs/heads/n3-eval-run',
        'refs/heads/webapp',
        "KICAD_PCB_REFINEMENT_LLM_API_KEY",
        "Verify Ollama model is available",
        'f"{base_url}/api/tags"',
        "uv run kicad-refine-eval",
        "--max-rounds 3",
        "--max-operations-per-round 4",
        "--max-total-accepted-operations 8",
        "Validate complete 12-fixture evidence",
        "actions/upload-artifact@v6",
        "retention-days: 30",
    )
    forbidden = (
        "\n  pull_request:",
        "sudo apt-get",
        "install-deps",
    )
    problems = [
        f"missing required Phase N3 live-evaluation fragment: {item}"
        for item in required
        if item not in text
    ]
    problems.extend(
        f"forbidden Phase N3 live-evaluation fragment remains: {item.strip()}"
        for item in forbidden
        if item in text
    )
    return problems


def _ollama_client_problems() -> list[str]:
    text = OLLAMA_CLIENT.read_text(encoding="utf-8")
    required = (
        'if request.response_format == "json":',
        'payload["format"] = "json"',
        'payload["think"] = False',
    )
    return [
        f"missing required Ollama structured-JSON fragment: {item}"
        for item in required
        if item not in text
    ]


def _evaluation_cli_problems() -> list[str]:
    text = EVALUATION_CLI.read_text(encoding="utf-8")
    required = (
        '"fixture_id", "cause_code", "cause_type"',
        "details=_safe_error_details(exc)",
        "def _safe_error_details",
    )
    return [
        f"missing required N3 safe-diagnostic fragment: {item}"
        for item in required
        if item not in text
    ]


if __name__ == "__main__":
    raise SystemExit(main())
