"""CLI adapter for the same configured schematic-refinement mutation path."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TextIO

from pydantic import ValidationError

from kicad_pcb.errors import UserError

from .services.configured_refinement import run_configured_refinement_request
from .services.refinement_api import RefinementRunRequest
from .services.refinement_config import RefinementFeatureConfig
from .services.schematic_refinement import RefinementRuntime


@dataclass(frozen=True)
class RefinementCliContext:
    """Server-owned CLI dependencies that command-line flags cannot override."""

    accepted_path: Path
    runtime: RefinementRuntime
    config: RefinementFeatureConfig
    stdout: TextIO
    stderr: TextIO


class _CliArgumentError(ValueError):
    pass


class _RefinementArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _CliArgumentError(message)


def execute_refinement_cli(
    argv: Sequence[str],
    context: RefinementCliContext,
) -> int:
    """Run one sanitized CLI request without exposing paths, providers, or limits as flags."""

    parser = _parser()
    try:
        namespace = parser.parse_args(list(argv))
        request = RefinementRunRequest(session_id=namespace.session_id)
        response = run_configured_refinement_request(
            accepted_path=context.accepted_path,
            runtime=context.runtime,
            request=request,
            config=context.config,
        )
    except (_CliArgumentError, ValidationError):
        _write_error(
            context.stderr,
            code="REFINEMENT_CLI_INVALID_ARGUMENTS",
            message="Invalid schematic refinement CLI arguments.",
        )
        return 2
    except UserError as exc:
        _write_error(
            context.stderr,
            code=_error_code(exc),
            message=str(exc),
        )
        return 2

    context.stdout.write(json.dumps(response.model_dump(mode="json"), sort_keys=True) + "\n")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = _RefinementArgumentParser(prog="kicad-refine")
    parser.add_argument("--session-id", required=True)
    return parser


def _write_error(stream: TextIO, *, code: str, message: str) -> None:
    payload = {"status": "error", "code": code, "message": message}
    stream.write(json.dumps(payload, sort_keys=True) + "\n")


def _error_code(exc: UserError) -> str:
    value = exc.code
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)
