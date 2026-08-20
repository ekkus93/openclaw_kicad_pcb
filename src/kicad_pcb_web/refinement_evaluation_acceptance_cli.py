"""CLI for post-hoc Phase N3 evidence acceptance validation."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .services.refinement_evaluation_acceptance import (
    PhaseN3AcceptanceExpectation,
    validate_phase_n3_evidence,
)

_DEFAULT_MANIFEST = Path("tests/fixtures/refinement/evaluation_corpus/manifest.json")
_DEFAULT_EXPECTATIONS = Path(
    "tests/fixtures/refinement/evaluation_corpus/baseline_expectations.json"
)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate an unpacked Phase N3 evidence root and emit its acceptance binding."""

    namespace = _parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    try:
        result = validate_phase_n3_evidence(
            namespace.evidence_root,
            PhaseN3AcceptanceExpectation(
                implementation_sha=namespace.implementation_sha,
                provider=namespace.provider,
                model=namespace.model,
                source_manifest=namespace.manifest,
                baseline_expectations=namespace.expectations,
            ),
        )
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"Phase N3 evidence rejected: {exc}\n")
        return 2

    payload = result.to_dict()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if namespace.report is not None:
        namespace.report.parent.mkdir(parents=True, exist_ok=True)
        namespace.report.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kicad-refine-eval-validate")
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--implementation-sha", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    parser.add_argument("--expectations", type=Path, default=_DEFAULT_EXPECTATIONS)
    parser.add_argument("--report", type=Path)
    return parser
