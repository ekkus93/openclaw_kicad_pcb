"""Shared test helpers and canonical fixture locations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TESTS_ROOT.parent
FIXTURES_DIR = TESTS_ROOT / "fixtures"
SYMBOLS_FIXTURE_DIR = FIXTURES_DIR / "symbols"
READABILITY_FIXTURES_DIR = FIXTURES_DIR / "readability"
CODE_REVIEW_DIR = REPO_ROOT / "code_review"


@dataclass(frozen=True)
class ReadabilityFixture:
    """Resolved paths for a named readability regression fixture."""

    name: str
    fixture_dir: Path
    circuit_ir_path: Path
    baseline_metrics_path: Path
    readme_path: Path
    baseline_schematic_path: Path | None = None
    regressed_schematic_path: Path | None = None


@dataclass(frozen=True)
class ReviewNetlistFixture:
    """Resolved path for a named source netlist used in regression tests."""

    name: str
    netlist_path: Path


def _readability_fixture(
    name: str,
    *,
    baseline_schematic_name: str | None = None,
    regressed_schematic_name: str | None = None,
) -> ReadabilityFixture:
    fixture_dir = READABILITY_FIXTURES_DIR / name
    return ReadabilityFixture(
        name=name,
        fixture_dir=fixture_dir,
        circuit_ir_path=fixture_dir / "circuit_ir.json",
        baseline_metrics_path=fixture_dir / "baseline_metrics.json",
        readme_path=fixture_dir / "README.md",
        baseline_schematic_path=(
            fixture_dir / baseline_schematic_name if baseline_schematic_name is not None else None
        ),
        regressed_schematic_path=(
            fixture_dir / regressed_schematic_name if regressed_schematic_name is not None else None
        ),
    )


NE5532_LEFT_CURRENT_READABILITY_FIXTURE = _readability_fixture(
    "ne5532_headphone_amp_left_current",
    baseline_schematic_name="baseline_generated.kicad_sch",
)
NE5532_LEFT_REGRESSED_READABILITY_FIXTURE = _readability_fixture(
    "ne5532_headphone_amp_left_regressed",
    regressed_schematic_name="regressed_generated.kicad_sch",
)
TIMER555_PWM_READABILITY_FIXTURE = _readability_fixture("timer555_pwm_dimmer")

READABILITY_FIXTURES: dict[str, ReadabilityFixture] = {
    fixture.name: fixture
    for fixture in (
        NE5532_LEFT_CURRENT_READABILITY_FIXTURE,
        NE5532_LEFT_REGRESSED_READABILITY_FIXTURE,
        TIMER555_PWM_READABILITY_FIXTURE,
    )
}


def readability_fixture(name: str) -> ReadabilityFixture:
    """Return the canonical named readability fixture."""

    return READABILITY_FIXTURES[name]


NE5532_HEADPHONE_REVIEW_FIXTURE = ReviewNetlistFixture(
    name="real_ne5532_headphone_amp",
    netlist_path=CODE_REVIEW_DIR / "ne5532_headphone_amp_netlist.json",
)
