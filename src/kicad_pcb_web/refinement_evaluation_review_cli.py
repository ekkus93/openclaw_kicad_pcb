"""CLI for producing an objective Phase N4 review packet from accepted N3 evidence."""

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
from .services.refinement_evaluation_review import build_phase_n4_review_packet

_DEFAULT_MANIFEST = Path("tests/fixtures/refinement/evaluation_corpus/manifest.json")
_DEFAULT_EXPECTATIONS = Path(
    "tests/fixtures/refinement/evaluation_corpus/baseline_expectations.json"
)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate N3 evidence, then write the objective Phase N4 review packet."""

    namespace = _parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    try:
        acceptance = validate_phase_n3_evidence(
            namespace.evidence_root,
            PhaseN3AcceptanceExpectation(
                implementation_sha=namespace.implementation_sha,
                provider=namespace.provider,
                model=namespace.model,
                source_manifest=namespace.manifest,
                baseline_expectations=namespace.expectations,
            ),
        )
        packet = build_phase_n4_review_packet(namespace.evidence_root, acceptance)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"Phase N4 review packet rejected: {exc}\n")
        return 2

    namespace.output.parent.mkdir(parents=True, exist_ok=True)
    namespace.output.write_text(
        json.dumps(packet, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sys.stdout.write(str(namespace.output) + "\n")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kicad-refine-eval-review")
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--implementation-sha", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    parser.add_argument("--expectations", type=Path, default=_DEFAULT_EXPECTATIONS)
    parser.add_argument("--output", type=Path, required=True)
    return parser
