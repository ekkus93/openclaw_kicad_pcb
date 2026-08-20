#!/usr/bin/env python3
"""Run the real first-fixture N3 critic request as a fail-fast smoke gate."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.errors import UserError
from kicad_pcb.runner import find_kicad_cli
from kicad_pcb_web.services.llm import (
    LlmClient,
    build_llm_client,
    get_llm_provider_capabilities,
)
from kicad_pcb_web.services.refinement_config import (
    RefinementFeatureConfig,
    load_refinement_feature_config,
    require_refinement_enabled,
)
from kicad_pcb_web.services.refinement_evaluation_corpus import (
    RefinementCorpusEvaluationRequest,
    prepare_refinement_evaluation_corpus,
)
from kicad_pcb_web.services.schematic_refinement import (
    RefinementIterationLimits,
    RefinementProvenance,
    RefinementRuntime,
    analyze_schematic_refinement,
)
from kicad_pcb_web.settings import WebSettings, load_settings

_FIRST_FIXTURE_ID = "n1-crowded-power-regulator"
_DEFAULT_MANIFEST = Path("tests/fixtures/refinement/evaluation_corpus/manifest.json")


@dataclass(frozen=True)
class SmokeContext:
    settings: WebSettings
    config: RefinementFeatureConfig
    adapter: KicadCliAdapter
    llm_client: LlmClient


@dataclass(frozen=True)
class SmokeRequest:
    repo_root: Path
    manifest_path: Path
    expectations_path: Path | None
    work_root: Path
    implementation_sha: str | None


def run_smoke(request: SmokeRequest, context: SmokeContext) -> dict[str, object]:
    """Execute the production analyze/critic path for the certified first N3 fixture."""

    require_refinement_enabled(context.config)
    provider, model = _require_vision_model(context)
    loop_limits = context.config.to_loop_limits()
    iteration_limits = RefinementIterationLimits(
        max_critic_repairs=loop_limits.max_critic_repairs,
        max_planner_repairs=loop_limits.max_planner_repairs,
        max_operations=loop_limits.max_operations_per_round,
    )
    provenance = RefinementProvenance(
        provider=provider,
        model=model,
        implementation_sha=request.implementation_sha,
    )
    preparation = prepare_refinement_evaluation_corpus(
        RefinementCorpusEvaluationRequest(
            repo_root=request.repo_root,
            manifest_path=request.manifest_path,
            adapter=context.adapter,
            llm_client=context.llm_client,
            provenance=provenance,
            iteration_limits=iteration_limits,
            loop_limits=loop_limits,
            fixture_ids=(_FIRST_FIXTURE_ID,),
            expectations_path=request.expectations_path,
        )
    )
    if len(preparation.fixtures) != 1:
        raise UserError(
            "N3 first-fixture smoke preflight did not resolve exactly one fixture.",
            code="REFINEMENT_EVALUATION_SMOKE_SELECTION_INVALID",
        )

    prepared = preparation.fixtures[0]
    request.work_root.mkdir(parents=True, exist_ok=True)
    smoke_tmp = Path(
        tempfile.mkdtemp(
            prefix=f".{_FIRST_FIXTURE_ID}-critic-smoke-",
            dir=request.work_root,
        )
    )
    try:
        accepted_path = smoke_tmp / "accepted.kicad_sch"
        shutil.copyfile(prepared.baseline_schematic, accepted_path)
        analysis = analyze_schematic_refinement(
            accepted_path=accepted_path,
            runtime=RefinementRuntime(
                authoritative_ir=prepared.authoritative_ir,
                adapter=context.adapter,
                llm_client=context.llm_client,
                work_dir=smoke_tmp / "runtime",
                evidence_root=smoke_tmp / "evidence",
                provenance=provenance,
            ),
            max_critic_repairs=iteration_limits.max_critic_repairs,
        )
    finally:
        shutil.rmtree(smoke_tmp, ignore_errors=True)

    return {
        "status": "smoke_passed",
        "fixture_id": _FIRST_FIXTURE_ID,
        "provider": provider,
        "model": model,
        "accepted_hash": analysis.accepted_hash,
        "render_png_hash": analysis.context.render_png_hash,
        "issue_count": len(analysis.critic.issues),
    }


def _require_vision_model(context: SmokeContext) -> tuple[str, str]:
    llm = context.settings.llm
    if not llm.enabled or llm.model is None:
        raise UserError(
            "N3 first-fixture smoke requires an explicitly configured LLM provider and model.",
            code="REFINEMENT_EVALUATION_LLM_REQUIRED",
        )
    capabilities = get_llm_provider_capabilities(llm.provider)
    if not llm.vision_enabled or not capabilities.supports_image_input:
        raise UserError(
            "N3 first-fixture smoke requires explicit vision capability and vision_enabled=true.",
            code="REFINEMENT_EVALUATION_VISION_REQUIRED",
        )
    return llm.provider, llm.model


def _error_code(exc: Exception) -> str:
    value = getattr(exc, "code", None)
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value or type(exc).__name__)


def _safe_failure(exc: Exception) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "error",
        "fixture_id": _FIRST_FIXTURE_ID,
        "code": _error_code(exc),
        "cause_type": type(exc).__name__,
        "message": str(exc),
    }
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        for key in ("provider", "status_code", "endpoint", "retryable"):
            value = details.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                payload[f"cause_{key}"] = value
    return payload


def _close_llm_client(llm_client: LlmClient | None) -> None:
    close = getattr(llm_client, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "warning",
                    "message": "N3 smoke LLM client cleanup failed.",
                    "cause_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    parser.add_argument("--expectations", type=Path)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--implementation-sha")
    return parser


def main() -> int:
    args = _parser().parse_args()
    llm_client: LlmClient | None = None
    try:
        settings = load_settings()
        config = load_refinement_feature_config()
        llm_client = build_llm_client(settings)
        if llm_client is None:
            raise UserError(
                "N3 first-fixture smoke requires an enabled LLM provider.",
                code="REFINEMENT_EVALUATION_LLM_REQUIRED",
            )
        payload = run_smoke(
            SmokeRequest(
                repo_root=args.repo_root,
                manifest_path=args.manifest,
                expectations_path=args.expectations,
                work_root=args.work_root,
                implementation_sha=args.implementation_sha or os.environ.get("GITHUB_SHA"),
            ),
            SmokeContext(
                settings=settings,
                config=config,
                adapter=KicadCliAdapter(kicad_cli=find_kicad_cli()),
                llm_client=llm_client,
            ),
        )
    except (ValueError, UserError) as exc:
        print(json.dumps(_safe_failure(exc), sort_keys=True), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps(_safe_failure(exc), sort_keys=True), file=sys.stderr)
        return 1
    finally:
        _close_llm_client(llm_client)

    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
