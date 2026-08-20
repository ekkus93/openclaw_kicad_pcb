#!/usr/bin/env python3
"""Reject known stale or incomplete CI configuration."""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(".github/workflows/ci.yml")
LIVE_EVALUATION_WORKFLOW = Path(".github/workflows/refinement-live-evaluation.yml")
OLLAMA_PREFLIGHT = Path("scripts/preflight_n3_ollama.py")
OLLAMA_CLIENT = Path("src/kicad_pcb_web/services/llm/ollama_client.py")
EVALUATION_CLI = Path("src/kicad_pcb_web/refinement_evaluation_cli.py")
REFINEMENT_RENDERING = Path("src/kicad_pcb/refinement/rendering.py")
REFINEMENT_LLM = Path("src/kicad_pcb_web/services/refinement_llm.py")


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
    problems.extend(_ollama_preflight_problems())
    problems.extend(_ollama_client_problems())
    problems.extend(_evaluation_cli_problems())
    problems.extend(_refinement_context_budget_problems())
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
        'AUTO_MODEL: "qwen3-vl:8b-instruct-n3-64k"',
        'AUTO_BASE_URL: "http://127.0.0.1:11434"',
        'N3_OLLAMA_SOURCE_MODEL: "qwen3-vl:8b-instruct"',
        'N3_OLLAMA_MODEL: "qwen3-vl:8b-instruct-n3-64k"',
        'N3_OLLAMA_NUM_CTX: "65536"',
        'KICAD_PCB_WEB_LLM_TIMEOUT_S: "300"',
        "runs-on: self-hosted",
        "refs/heads/n3-eval-run",
        "refs/heads/webapp",
        "KICAD_PCB_REFINEMENT_LLM_API_KEY",
        "Verify Ollama N3 vision capability",
        "scripts/preflight_n3_ollama.py",
        "--probe-width 1260",
        "--probe-height 891",
        "--source-model",
        "--num-ctx",
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
        'AUTO_MODEL: "qwen3-vl:8b-instruct-n3-32k"',
        'N3_OLLAMA_MODEL: "qwen3-vl:8b-instruct-n3-32k"',
        'N3_OLLAMA_NUM_CTX: "32768"',
        "--probe-width 3360",
        "--probe-height 2376",
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


def _ollama_preflight_problems() -> list[str]:
    text = OLLAMA_PREFLIGHT.read_text(encoding="utf-8")
    required = (
        '"/api/create"',
        '"parameters": {"num_ctx": config.num_ctx}',
        'message["images"] = [image_b64]',
        "_solid_white_png",
        "Ollama A3 vision capability probe",
        'parser.add_argument("--probe-width", type=_positive_int, default=1260)',
        'parser.add_argument("--probe-height", type=_positive_int, default=891)',
    )
    forbidden = (
        "default=3360",
        "default=2376",
    )
    problems = [
        f"missing required N3 Ollama preflight fragment: {item}"
        for item in required
        if item not in text
    ]
    problems.extend(
        f"stale N3 Ollama preflight fragment remains: {item}"
        for item in forbidden
        if item in text
    )
    return problems


def _ollama_client_problems() -> list[str]:
    text = OLLAMA_CLIENT.read_text(encoding="utf-8")
    required = (
        'if request.response_format == "json":',
        'payload["format"] = request.json_schema if request.json_schema is not None else "json"',
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
        '"cause_message"',
        '"cause_status_code"',
        '"cause_endpoint"',
        '"cause_retryable"',
        '"cause_provider_error"',
        "details=_safe_error_details(exc)",
        "def _safe_error_details",
        "_SAFE_DETAIL_VALUE_TYPES",
    )
    return [
        f"missing required N3 safe-diagnostic fragment: {item}"
        for item in required
        if item not in text
    ]


def _refinement_context_budget_problems() -> list[str]:
    rendering = REFINEMENT_RENDERING.read_text(encoding="utf-8")
    llm = REFINEMENT_LLM.read_text(encoding="utf-8")
    required_rendering = (
        "REVIEW_PIXELS_PER_MM = 3.0",
        "REVIEW_TILING_REFERENCE_PIXELS_PER_MM = 8.0",
        "MAX_REVIEW_REGION_DIMENSION_PX / REVIEW_TILING_REFERENCE_PIXELS_PER_MM",
    )
    required_llm = (
        '"vision_object_map": _critic_context_payload(context)',
        "def _critic_context_payload(context: VisionObjectMap)",
        '"positions_mm": item.positions_mm',
        '"points_mm": item.points_mm',
        '"nets": [{"object_id": item.object_id, "name": item.name} for item in context.nets]',
    )
    forbidden_llm = (
        '"symbol_id": item.symbol_id',
        '"value": item.value',
        '"terminals": item.terminals',
        '"row": item.row',
        '"column": item.column',
    )
    problems = [
        f"missing required refinement render-budget fragment: {item}"
        for item in required_rendering
        if item not in rendering
    ]
    problems.extend(
        f"missing required refinement critic-budget fragment: {item}"
        for item in required_llm
        if item not in llm
    )
    problems.extend(
        f"forbidden redundant refinement critic fragment remains: {item}"
        for item in forbidden_llm
        if item in llm
    )
    return problems


if __name__ == "__main__":
    raise SystemExit(main())
