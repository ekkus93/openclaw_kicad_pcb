"""CLI for Phase N3 model-directed refinement corpus evaluation."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TextIO

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.errors import UserError
from kicad_pcb.runner import find_kicad_cli

from .services.llm import LlmClient, build_llm_client, get_llm_provider_capabilities
from .services.refinement_config import (
    RefinementFeatureConfig,
    load_refinement_feature_config,
    require_refinement_enabled,
)
from .services.refinement_evaluation_corpus import (
    RefinementCorpusEvaluationRequest,
    run_refinement_evaluation_corpus,
)
from .services.schematic_refinement import (
    RefinementIterationLimits,
    RefinementLoopLimits,
    RefinementProvenance,
)
from .settings import WebSettings, load_settings

LOGGER = logging.getLogger("uvicorn.error")
_DEFAULT_MANIFEST = Path("tests/fixtures/refinement/evaluation_corpus/manifest.json")


@dataclass(frozen=True)
class RefinementEvaluationCliContext:
    """Process-owned dependencies for experimental corpus evaluation."""

    settings: WebSettings
    llm_client: LlmClient | None
    config: RefinementFeatureConfig
    adapter: KicadCliAdapter
    stdout: TextIO
    stderr: TextIO
    implementation_sha: str | None = None


class _CliArgumentError(ValueError):
    pass


class _EvaluationArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _CliArgumentError(message)


def execute_refinement_evaluation_cli(
    argv: Sequence[str],
    context: RefinementEvaluationCliContext,
) -> int:
    """Validate CLI/configuration inputs and execute one bounded corpus evaluation."""

    try:
        namespace = _parser().parse_args(list(argv))
        require_refinement_enabled(context.config)
        provider, model, llm_client = _require_vision_model(context)
        iteration_limits, loop_limits = _limits(namespace, context.config)
        implementation_sha = namespace.implementation_sha or context.implementation_sha
        request = RefinementCorpusEvaluationRequest(
            repo_root=namespace.repo_root,
            manifest_path=namespace.manifest,
            adapter=context.adapter,
            llm_client=llm_client,
            provenance=RefinementProvenance(
                provider=provider,
                model=model,
                implementation_sha=implementation_sha,
            ),
            iteration_limits=iteration_limits,
            loop_limits=loop_limits,
            fixture_ids=tuple(namespace.fixture_id),
            expectations_path=namespace.expectations,
        )
        result = run_refinement_evaluation_corpus(
            request,
            output_root=namespace.output_root,
            work_root=namespace.work_root,
        )
    except (_CliArgumentError, ValueError):
        _write_error(
            context.stderr,
            code="REFINEMENT_EVALUATION_CLI_INVALID_ARGUMENTS",
            message="Invalid refinement evaluation CLI arguments.",
        )
        return 2
    except UserError as exc:
        _write_error(
            context.stderr,
            code=_error_code(exc),
            message="Refinement evaluation could not be completed.",
        )
        return 2
    except Exception as exc:
        LOGGER.error(
            "unexpected refinement evaluation CLI failure",
            extra={"error_type": type(exc).__name__},
        )
        _write_error(
            context.stderr,
            code="REFINEMENT_EVALUATION_CLI_INTERNAL_ERROR",
            message="Refinement evaluation failed unexpectedly.",
        )
        return 1

    payload = {
        "status": "completed",
        "fixture_count": len(result.fixture_ids),
        "fixture_ids": list(result.fixture_ids),
        "summary_path": str(result.summary_path),
        "provider": provider,
        "model": model,
    }
    context.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Load process configuration and execute the Phase N3 corpus evaluation CLI."""

    arguments = sys.argv[1:] if argv is None else list(argv)
    llm_client: LlmClient | None = None
    try:
        settings = load_settings()
        config = load_refinement_feature_config()
        llm_client = build_llm_client(settings)
        adapter = KicadCliAdapter(kicad_cli=find_kicad_cli())
    except (ValueError, UserError):
        _write_error(
            sys.stderr,
            code="REFINEMENT_EVALUATION_CLI_CONFIGURATION_INVALID",
            message="Refinement evaluation CLI configuration is invalid.",
        )
        return 2
    except Exception as exc:
        LOGGER.error(
            "unexpected refinement evaluation CLI configuration failure",
            extra={"error_type": type(exc).__name__},
        )
        _write_error(
            sys.stderr,
            code="REFINEMENT_EVALUATION_CLI_INTERNAL_ERROR",
            message="Refinement evaluation CLI failed unexpectedly.",
        )
        return 1

    try:
        return execute_refinement_evaluation_cli(
            arguments,
            RefinementEvaluationCliContext(
                settings=settings,
                llm_client=llm_client,
                config=config,
                adapter=adapter,
                stdout=sys.stdout,
                stderr=sys.stderr,
                implementation_sha=os.environ.get("GITHUB_SHA"),
            ),
        )
    finally:
        _close_llm_client(llm_client)


def _require_vision_model(
    context: RefinementEvaluationCliContext,
) -> tuple[str, str, LlmClient]:
    llm = context.settings.llm
    if not llm.enabled or llm.model is None or context.llm_client is None:
        raise UserError(
            "Refinement evaluation requires an explicitly configured LLM provider and model.",
            code="REFINEMENT_EVALUATION_LLM_REQUIRED",
        )
    capabilities = get_llm_provider_capabilities(llm.provider)
    if not llm.vision_enabled or not capabilities.supports_image_input:
        raise UserError(
            "Refinement evaluation requires explicit vision capability and vision_enabled=true.",
            code="REFINEMENT_EVALUATION_VISION_REQUIRED",
        )
    return llm.provider, llm.model, context.llm_client


def _limits(
    namespace: argparse.Namespace,
    config: RefinementFeatureConfig,
) -> tuple[RefinementIterationLimits, RefinementLoopLimits]:
    loop = RefinementLoopLimits(
        max_rounds=_override(namespace.max_rounds, config.max_rounds),
        max_operations_per_round=_override(
            namespace.max_operations_per_round,
            config.max_operations_per_round,
        ),
        max_total_accepted_operations=_override(
            namespace.max_total_accepted_operations,
            config.max_total_accepted_operations,
        ),
        max_candidate_rejections=_override(
            namespace.max_candidate_rejections,
            config.max_candidate_rejections,
        ),
        max_critic_repairs=_override(namespace.max_critic_repairs, config.max_critic_repairs),
        max_planner_repairs=_override(namespace.max_planner_repairs, config.max_planner_repairs),
    )
    iteration = RefinementIterationLimits(
        max_critic_repairs=loop.max_critic_repairs,
        max_planner_repairs=loop.max_planner_repairs,
        max_operations=loop.max_operations_per_round,
    )
    return iteration, loop


def _override(value: int | None, default: int) -> int:
    return default if value is None else value


def _parser() -> argparse.ArgumentParser:
    parser = _EvaluationArgumentParser(prog="kicad-refine-eval")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    parser.add_argument("--expectations", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--fixture-id", action="append", default=[])
    parser.add_argument("--implementation-sha")
    parser.add_argument("--max-rounds", type=int)
    parser.add_argument("--max-operations-per-round", type=int)
    parser.add_argument("--max-total-accepted-operations", type=int)
    parser.add_argument("--max-candidate-rejections", type=int)
    parser.add_argument("--max-critic-repairs", type=int)
    parser.add_argument("--max-planner-repairs", type=int)
    return parser


def _close_llm_client(llm_client: LlmClient | None) -> None:
    close = getattr(llm_client, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception as exc:
        LOGGER.warning(
            "refinement evaluation CLI LLM client cleanup failed",
            extra={"error_type": type(exc).__name__},
        )


def _write_error(stream: TextIO, *, code: str, message: str) -> None:
    payload = {"status": "error", "code": code, "message": message}
    stream.write(json.dumps(payload, sort_keys=True) + "\n")


def _error_code(exc: UserError) -> str:
    value = exc.code
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


if __name__ == "__main__":
    raise SystemExit(main())
