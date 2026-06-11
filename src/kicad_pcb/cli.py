"""CLI entry-point: argument parsing and sub-command dispatch."""

from __future__ import annotations

import argparse
import json
import sys

from ._cli_subcommands_design import _register_design_subcommands
from ._cli_subcommands_hardware import _register_hardware_subcommands
from ._cli_subcommands_netlist import _register_netlist_subcommands
from .errors import KiCadError
from .formatting import format_result, format_result_json
from .lint import LINT_SUGGESTIONS, LintError, LintSeverity
from .results import DoctorResult, LintFileResult, ValidateFileResult


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Extracted from :func:`main` so the parser structure can be verified in
    unit tests without spawning a subprocess or touching ``sys.argv``.
    """
    parser = argparse.ArgumentParser(
        prog="kicad_pcb",
        description="🔧 KiCad PCB Automation — Design to Manufacturing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        default=False,
        help="Output result as JSON",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        dest="backup",
        default=False,
        help="Write <file>.bak before overwriting any .kicad_sch / .kicad_pcb file",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    _register_design_subcommands(subparsers)
    _register_netlist_subcommands(subparsers)
    _register_hardware_subcommands(subparsers)

    return parser


def build_parser() -> argparse.ArgumentParser:
    """Public alias for :func:`_build_parser`; used by tests and tooling."""
    return _build_parser()


def main() -> None:  # noqa: PLR0912
    """Parse CLI arguments and dispatch to the appropriate command handler."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    try:
        result = args.func(args)
        if getattr(args, "output_json", False):
            result_payload = json.loads(format_result_json(result))
            print(json.dumps({"ok": True, "result": result_payload, "warnings": []}, indent=2))
        else:
            for line in format_result(result):
                print(line)
        # Doctor exits non-zero when checks fail
        if isinstance(result, DoctorResult) and not result.overall_ok:
            sys.exit(1)
        # Lint/validate exits non-zero when issues found
        if isinstance(result, (LintFileResult, ValidateFileResult)) and not result.ok:
            sys.exit(1)
    except LintError as exc:
        if getattr(args, "output_json", False):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": "VALIDATION_FAILED",
                            "message": str(exc),
                            "details": {
                                "issues": [
                                    {
                                        "code": i.code,
                                        "severity": i.severity.value,
                                        "message": i.message,
                                        "path": i.path,
                                        "suggestion": LINT_SUGGESTIONS.get(i.code),
                                    }
                                    for i in exc.issues
                                ]
                            },
                        },
                    },
                    indent=2,
                )
            )
        else:
            print(f"❌ Validation failed: {len(exc.issues)} issue(s)")
            for issue in exc.issues:
                icon = "❌" if issue.severity is LintSeverity.ERROR else "⚠️ "
                print(f"  {icon} [{issue.code}] {issue.message}")
                if issue.path:
                    print(f"       path: {issue.path}")
                suggestion = LINT_SUGGESTIONS.get(issue.code)
                if suggestion:
                    print(f"       💡 {suggestion}")
            # If any layout issue (LAY*) was reported, add a generic layout hint.
            lay_codes = {i.code for i in exc.issues if i.code.startswith("LAY")}
            if lay_codes:
                print(
                    "  💡 Tip: layout issues are fail-fast; "
                    "fix the reported issue and re-run apply-netlist/new-from-netlist."
                )
        sys.exit(1)
    except KiCadError as exc:
        if getattr(args, "output_json", False):
            print(json.dumps({"ok": False, "error": exc.as_dict()}, indent=2))
        else:
            print(f"❌ {exc}")
            hint = exc.details.get("hint") if exc.details else None
            if hint:
                print(f"   💡 {hint}")
        sys.exit(1)


if __name__ == "__main__":
    main()
