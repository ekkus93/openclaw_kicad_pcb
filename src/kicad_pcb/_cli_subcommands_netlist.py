"""Register netlist/IR, schematic introspection, and model-corpus CLI subcommands."""

from __future__ import annotations

import argparse

from .commands._sch_apply import SCHEMATIC_HEURISTIC_PROFILES
from .commands.model_corpus import (
    cmd_model_corpus_evaluate,
    cmd_model_corpus_ingest,
    cmd_model_corpus_list,
)
from .commands.netlist import (
    cmd_apply_netlist,
    cmd_fix_netlist,
    cmd_info_sch,
    cmd_new_from_netlist,
    cmd_validate_netlist,
)
from .router import LABEL_MODE_POLICIES


def _register_netlist_subcommands(  # noqa: PLR0915
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register netlist/IR, schematic info, and model-corpus subcommands."""
    # info-sch
    p_info_sch = subparsers.add_parser(
        "info-sch",
        help="Show schematic introspection data (root + managed sheet)",
        description=(
            "Introspect the current project schematic. "
            "The root schematic (<name>.kicad_sch) is intentionally thin and contains "
            "only a sheet reference; all generated symbols, wires, and net labels live "
            "in OpenClaw_Managed.kicad_sch. Both paths and their AST counts are returned."
        ),
    )
    p_info_sch.set_defaults(func=cmd_info_sch)

    # validate-netlist
    p_val_netlist = subparsers.add_parser(
        "validate-netlist",
        help="Validate a Circuit IR JSON netlist without writing any files",
        description=(
            "Validate a Circuit IR JSON file through all three layers: "
            "(1) Pydantic schema — correct keys, no extra fields, version string; "
            "(2) semantic — no duplicate refs/nets, no zero-pin nets, all pin refs resolve; "
            "(3) symbol + pin — every symbol exists in the library index, every pin is valid. "
            "No files are written. Use this to check a netlist before calling new-from-netlist."
        ),
    )
    p_val_netlist.add_argument("--netlist", required=True, help="Path to Circuit IR JSON file")
    p_val_netlist.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_val_netlist.set_defaults(func=cmd_validate_netlist)

    # apply-netlist
    p_apply = subparsers.add_parser(
        "apply-netlist",
        help="Apply Circuit IR JSON to OpenClaw managed schematic",
        description=(
            "Apply a Circuit IR JSON netlist to the current project. "
            "Generated content is written to OpenClaw_Managed.kicad_sch (the managed sheet); "
            "the root schematic (<name>.kicad_sch) stays thin and references the managed sheet. "
            "Use --dry-run to validate without writing."
        ),
    )
    p_apply.add_argument("--netlist", required=True, help="Path to Circuit IR JSON file")
    p_apply.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_apply.add_argument(
        "--mode",
        choices=["internal", "kicad"],
        default="internal",
        help="Validation mode — deprecated, prefer --validate (default: internal)",
    )
    p_apply.add_argument(
        "--validate",
        choices=["none", "syntax", "lint", "kicad", "full"],
        default=None,
        help="Validation level; overrides --mode when given (default: lint)",
    )
    p_apply.add_argument(
        "--routing",
        choices=["bus", "hub", "labels"],
        default="bus",
        help="Routing style: bus/hub = spine routing, labels = label stubs (default: bus)",
    )
    p_apply.add_argument(
        "--heuristic-profile",
        choices=sorted(SCHEMATIC_HEURISTIC_PROFILES),
        default=None,
        help=(
            "Named schematic heuristic profile for bundled layout and routing policies "
            "(default: analog_audio)"
        ),
    )
    p_apply.add_argument(
        "--label-mode",
        choices=sorted(LABEL_MODE_POLICIES),
        default=None,
        help=(
            "Visible-label mode: minimal keeps sparse labels, "
            "debug promotes broad signal labeling, "
            "always-show-important-labels preserves labels on key stage seams"
        ),
    )
    p_apply.add_argument(
        "--debug-dump",
        help="Optional path for a JSON debug dump covering layout, unit splitting, and routing",
    )
    p_apply.add_argument("--force", action="store_true", help="Adopt non-owned schematic")
    p_apply.add_argument("--dry-run", action="store_true", help="Validate without writing")
    p_apply.add_argument("--strict", action="store_true", help="Treat lint warnings as errors")
    p_apply.set_defaults(func=cmd_apply_netlist)

    # new-from-netlist
    p_new_netlist = subparsers.add_parser(
        "new-from-netlist",
        help="Create a project and compile Circuit IR deterministically",
        description=(
            "Create a new KiCad project and compile a Circuit IR JSON netlist into it. "
            "The root schematic (<name>.kicad_sch) is thin and contains a sheet reference; "
            "all generated symbols, wires, and net labels are written to "
            "OpenClaw_Managed.kicad_sch (the managed sheet)."
        ),
    )
    p_new_netlist.add_argument("--name", required=True, help="Project name")
    p_new_netlist.add_argument("--netlist", required=True, help="Path to Circuit IR JSON file")
    p_new_netlist.add_argument("--out-dir", help="Project output base directory")
    p_new_netlist.add_argument("--description", help="Optional project description")
    p_new_netlist.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_new_netlist.add_argument(
        "--mode",
        choices=["internal", "kicad"],
        default="kicad",
        help="Validation mode — deprecated, prefer --validate (default: kicad)",
    )
    p_new_netlist.add_argument(
        "--validate",
        choices=["none", "syntax", "lint", "kicad", "full"],
        default=None,
        help="Validation level; overrides --mode when given (default: kicad)",
    )
    p_new_netlist.add_argument(
        "--routing",
        choices=["bus", "hub", "labels"],
        default="bus",
        help="Routing style: bus/hub = spine routing, labels = label stubs (default: bus)",
    )
    p_new_netlist.add_argument(
        "--heuristic-profile",
        choices=sorted(SCHEMATIC_HEURISTIC_PROFILES),
        default=None,
        help=(
            "Named schematic heuristic profile for bundled layout and routing policies "
            "(default: analog_audio)"
        ),
    )
    p_new_netlist.add_argument(
        "--label-mode",
        choices=sorted(LABEL_MODE_POLICIES),
        default=None,
        help=(
            "Visible-label mode: minimal keeps sparse labels, "
            "debug promotes broad signal labeling, "
            "always-show-important-labels preserves labels on key stage seams"
        ),
    )
    p_new_netlist.add_argument(
        "--debug-dump",
        help="Optional path for a JSON debug dump covering layout, unit splitting, and routing",
    )
    p_new_netlist.add_argument(
        "--no-auto-fix",
        action="store_false",
        dest="auto_fix",
        default=True,
        help="Disable deterministic auto-fix on validation failure",
    )
    p_new_netlist.add_argument(
        "--strict", action="store_true", help="Treat lint warnings as errors"
    )
    p_new_netlist.set_defaults(func=cmd_new_from_netlist)

    # fix-netlist
    p_fix_netlist = subparsers.add_parser(
        "fix-netlist",
        help="Auto-fix a Circuit IR JSON file and write the corrected version",
        description=(
            "Apply deterministic fixes to a Circuit IR JSON file without any LLM calls. "
            "Fixes are applied in layers: "
            "(1) schema structure (version, wrappers, forbidden keys); "
            "(2) component fields (removes 'type', inline 'pins'); "
            "(3) net pin types (integer values → strings); "
            "(4) pin aliases ('+'→'1', '-'→'2', 'TIP'→'T', etc. via library lookup). "
            "The corrected JSON is always written even when errors remain, so you can inspect "
            "progress or pass it directly to new-from-netlist."
        ),
    )
    p_fix_netlist.add_argument("--netlist", required=True, help="Path to Circuit IR JSON file")
    p_fix_netlist.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_fix_netlist.add_argument(
        "--output", help="Path for the fixed JSON (default: <stem>.fixed.json)"
    )
    p_fix_netlist.set_defaults(func=cmd_fix_netlist)

    # model-corpus
    p_model_corpus = subparsers.add_parser(
        "model-corpus",
        help="Ingest and inspect deterministic model corpus fixtures",
    )
    model_corpus_subparsers = p_model_corpus.add_subparsers(dest="model_corpus_command")

    p_model_corpus_ingest = model_corpus_subparsers.add_parser(
        "ingest",
        help="Ingest raw KiCad schematics into corpus fixtures",
    )
    p_model_corpus_ingest.add_argument(
        "--source-dir",
        default="model_kicad_files",
        help="Directory of raw .kicad_sch source files",
    )
    p_model_corpus_ingest.add_argument(
        "--out-dir",
        default="tests/fixtures/model_corpus",
        help="Directory for normalized corpus fixtures",
    )
    p_model_corpus_ingest.add_argument(
        "--refresh",
        action="store_true",
        help="Overwrite generated fixture artifacts in existing fixture dirs",
    )
    p_model_corpus_ingest.add_argument(
        "--require-kicad",
        action="store_true",
        help="Fail if kicad-cli is unavailable instead of creating partial fixtures",
    )
    p_model_corpus_ingest.set_defaults(func=cmd_model_corpus_ingest)

    p_model_corpus_list = model_corpus_subparsers.add_parser(
        "list",
        help="List ingested corpus fixtures",
    )
    p_model_corpus_list.add_argument(
        "--corpus-dir",
        default="tests/fixtures/model_corpus",
        help="Directory containing corpus fixture metadata",
    )
    p_model_corpus_list.set_defaults(func=cmd_model_corpus_list)

    p_model_corpus_evaluate = model_corpus_subparsers.add_parser(
        "evaluate",
        help="Generate and score corpus fixtures that have circuit_ir.json",
    )
    p_model_corpus_evaluate.add_argument(
        "--corpus-dir",
        default="tests/fixtures/model_corpus",
        help="Directory containing corpus fixtures",
    )
    p_model_corpus_evaluate.add_argument(
        "--out-dir",
        default="code_review/generated/model_eval",
        help="Directory for generated evaluation artifacts",
    )
    p_model_corpus_evaluate.add_argument(
        "--fixture",
        help="Optional single fixture id to evaluate",
    )
    p_model_corpus_evaluate.add_argument(
        "--require-kicad",
        action="store_true",
        help=(
            "Fail if repo-compatible kicad-cli is unavailable "
            "instead of producing partial evaluation"
        ),
    )
    p_model_corpus_evaluate.add_argument(
        "--heuristic-profile",
        choices=sorted(SCHEMATIC_HEURISTIC_PROFILES),
        default=None,
        help="Optional generation heuristic profile to reuse during evaluation",
    )
    p_model_corpus_evaluate.add_argument(
        "--label-mode",
        choices=sorted(LABEL_MODE_POLICIES),
        default=None,
        help="Optional label mode to reuse during evaluation",
    )
    p_model_corpus_evaluate.set_defaults(func=cmd_model_corpus_evaluate)

    # compile-netlist (alias for new-from-netlist)
    p_compile = subparsers.add_parser(
        "compile-netlist",
        help="Alias for new-from-netlist: create project from Circuit IR (same args)",
        description=(
            "Alias for new-from-netlist. "
            "Creates a new KiCad project and compiles a Circuit IR JSON netlist into it. "
            "The root schematic (<name>.kicad_sch) is thin and contains a sheet reference; "
            "all generated symbols, wires, and net labels are written to "
            "OpenClaw_Managed.kicad_sch (the managed sheet)."
        ),
    )
    p_compile.add_argument("--name", required=True, help="Project name")
    p_compile.add_argument("--netlist", required=True, help="Path to Circuit IR JSON file")
    p_compile.add_argument("--out-dir", help="Project output base directory")
    p_compile.add_argument("--description", help="Optional project description")
    p_compile.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_compile.add_argument(
        "--mode",
        choices=["internal", "kicad"],
        default="kicad",
        help="Validation mode (default: kicad)",
    )
    p_compile.add_argument("--strict", action="store_true", help="Treat lint warnings as errors")
    p_compile.set_defaults(func=cmd_new_from_netlist)
