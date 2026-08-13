"""Production CLI for trusted wizard-owned schematic refinement."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NoReturn, TextIO

from pydantic import ValidationError

from kicad_pcb.errors import UserError

from .errors import WebServiceError
from .services.llm import LlmClient, build_llm_client
from .services.refinement_api import RefinementRunRequest
from .services.refinement_config import (
    RefinementFeatureConfig,
    load_refinement_feature_config,
)
from .services.wizard_refinement import run_wizard_refinement_request
from .settings import WebSettings, load_settings


@dataclass(frozen=True)
class RefinementCliContext:
    """Process-owned dependencies that command-line flags cannot override."""

    settings: WebSettings
    llm_client: LlmClient | None
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
    """Run one sanitized CLI request through the trusted wizard/current-job boundary."""

    parser = _parser()
    try:
        namespace = parser.parse_args(list(argv))
        request = RefinementRunRequest(session_id=namespace.session_id)
        response = run_wizard_refinement_request(
            settings=context.settings,
            llm_client=context.llm_client,
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
    except (UserError, WebServiceError) as exc:
        _write_error(
            context.stderr,
            code=_error_code(exc),
            message="Schematic refinement request could not be completed.",
        )
        return 2

    context.stdout.write(json.dumps(response.model_dump(mode="json"), sort_keys=True) + "\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Load validated process configuration and execute the production refinement CLI."""

    arguments = sys.argv[1:] if argv is None else list(argv)
    try:
        settings = load_settings()
        config = load_refinement_feature_config()
        llm_client = build_llm_client(settings)
    except (ValueError, UserError):
        _write_error(
            sys.stderr,
            code="REFINEMENT_CLI_CONFIGURATION_INVALID",
            message="Schematic refinement CLI configuration is invalid.",
        )
        return 2

    try:
        return execute_refinement_cli(
            arguments,
            RefinementCliContext(
                settings=settings,
                llm_client=llm_client,
                config=config,
                stdout=sys.stdout,
                stderr=sys.stderr,
            ),
        )
    finally:
        close = getattr(llm_client, "close", None)
        if callable(close):
            close()


def _parser() -> argparse.ArgumentParser:
    parser = _RefinementArgumentParser(prog="kicad-refine")
    parser.add_argument("--session-id", required=True)
    return parser


def _write_error(stream: TextIO, *, code: str, message: str) -> None:
    payload = {"status": "error", "code": code, "message": message}
    stream.write(json.dumps(payload, sort_keys=True) + "\n")


def _error_code(exc: UserError | WebServiceError) -> str:
    value = exc.code
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


if __name__ == "__main__":
    raise SystemExit(main())
