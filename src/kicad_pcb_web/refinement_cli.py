"""CLI adapter for the same configured schematic-refinement mutation path."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from kicad_pcb.errors import UserError

from .services.configured_refinement import run_configured_refinement_request
from .services.refinement_api import RefinementRunRequest
from .services.refinement_config import RefinementFeatureConfig
from .services.schematic_refinement import RefinementRuntime


def execute_refinement_cli(
    argv: Sequence[str],
    *,
    accepted_path: Path,
    runtime: RefinementRuntime,
    config: RefinementFeatureConfig,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Run one sanitized CLI request without exposing paths, providers, or limits as flags."""

    parser = _parser()
    try:
        namespace = parser.parse_args(list(argv))
        request = RefinementRunRequest(session_id=namespace.session_id)
        response = run_configured_refinement_request(
            accepted_path=accepted_path,
            runtime=runtime,
            request=request,
            config=config,
        )
    except UserError as exc:
        payload = {
            "status": "error",
            "code": _error_code(exc),
            "message": str(exc),
        }
        stderr.write(json.dumps(payload, sort_keys=True) + "\n")
        return 2

    stdout.write(json.dumps(response.model_dump(mode="json"), sort_keys=True) + "\n")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kicad-refine")
    parser.add_argument("--session-id", required=True)
    return parser


def _error_code(exc: UserError) -> str:
    value = exc.code
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)
