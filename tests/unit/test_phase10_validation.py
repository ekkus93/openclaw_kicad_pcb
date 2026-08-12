"""Phase 10 integration tests: golden readability and validation safeguards."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from kicad_pcb.adapters import KicadCliAdapter, RunResult
from kicad_pcb.block_detection import BlockRole, classify_circuit
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.lint import LintError
from kicad_pcb.pipeline import ValidationMode, mutate_and_validate_sch
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import (
    average_symbol_spacing,
    compute_block_separation,
    compute_local_density,
    count_global_labels,
    count_power_symbols,
    count_short_wire_segments,
    page_region_density,
)
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode
from tests import NE5532_LEFT_CURRENT_READABILITY_FIXTURE, SYMBOLS_FIXTURE_DIR

_FIXTURE_10 = NE5532_LEFT_CURRENT_READABILITY_FIXTURE
_CIRCUIT_IR_PATH_10 = _FIXTURE_10.circuit_ir_path
_BASELINE_METRICS_PATH_10 = _FIXTURE_10.baseline_metrics_path
_SYMBOLS_DIR_10 = SYMBOLS_FIXTURE_DIR


class _FakeCli(KicadCliAdapter):
    """Minimal adapter stub used to exercise KICAD validation mode."""

    def __init__(self) -> None:
        super().__init__()
        self.erc_call_count = 0

    def erc(self, _sch: Path, _report: Path) -> tuple[RunResult, dict | None]:  # noqa: ARG002
        self.erc_call_count += 1
        return RunResult(0, "", ""), None


@pytest.mark.skipif(
    not _CIRCUIT_IR_PATH_10.exists() or not _BASELINE_METRICS_PATH_10.exists(),
    reason="Phase 10 readability fixture files missing",
)
class TestPhase10Validation:
    """Phase 10.1 and 10.3 regression-resistant validation checks."""

    @pytest.fixture
    def generated_schematic_path(self, tmp_path: Path) -> Path:
        result = cmd_new_from_netlist(
            Namespace(
                name="phase10_amp",
                out_dir=str(tmp_path),
                description="",
                netlist=str(_CIRCUIT_IR_PATH_10),
                symbols_dir=str(_SYMBOLS_DIR_10),
                mode="internal",
            )
        )
        return result.managed_schematic_path

    @pytest.fixture
    def generated_doc(self, generated_schematic_path: Path) -> SchematicDoc:
        return SchematicDoc.load(generated_schematic_path)

    def test_generates_headphone_amp_from_ir(self, generated_schematic_path: Path) -> None:
        """Golden test fixture must be generated from canonical IR in tests."""
        assert generated_schematic_path.exists()
        parsed = SchematicDoc.load(generated_schematic_path)
        assert len(parsed.list_symbols()) > 0

    def test_golden_readability_metrics_targets(self, generated_doc: SchematicDoc) -> None:
        """Readability metrics must stay within tolerant golden thresholds."""
        baseline = json.loads(_BASELINE_METRICS_PATH_10.read_text(encoding="utf-8"))

        current_spacing = average_symbol_spacing(generated_doc)
        assert current_spacing >= float(baseline["avg_spacing"]) - 0.5, (
            f"Average symbol spacing regressed: current={current_spacing:.3f}, "
            f"baseline={baseline['avg_spacing']:.3f}"
        )

        gnd_labels = count_global_labels(generated_doc, text="GND")
        assert gnd_labels <= int(baseline["gnd_labels"]) + 1, (
            f"GND clutter regressed: current={gnd_labels}, baseline={baseline['gnd_labels']}"
        )

        power_symbols = count_power_symbols(generated_doc)
        assert power_symbols <= int(baseline["power_symbols"]) + 1, (
            "Power symbol clutter regressed: "
            f"current={power_symbols}, baseline={baseline['power_symbols']}"
        )

        short_wires = count_short_wire_segments(generated_doc)
        # Directly wiring connector-to-passive edge nets can trade a couple of
        # local labels for a small increase in short orthogonal segments.
        assert short_wires <= int(baseline["short_wires"]) + 10, (
            f"Short-wire clutter regressed: current={short_wires}, "
            f"baseline={baseline['short_wires']}"
        )

        baseline_density: dict[str, float] = baseline["region_density"]
        baseline_imbalance = max(baseline_density.values()) - min(baseline_density.values())
        current_density = page_region_density(generated_doc)
        current_imbalance = max(current_density.values()) - min(current_density.values())
        symbol_granularity = 1.0 / max(int(baseline["symbol_count"]), 1)
        assert current_imbalance <= baseline_imbalance + max(0.05, symbol_granularity), (
            f"Page balance regressed: current={current_imbalance:.4f}, "
            f"baseline={baseline_imbalance:.4f}"
        )

        local_density = compute_local_density(generated_doc, radius_mm=30.0)
        max_density = max(local_density.values(), default=0.0)
        baseline_local_density = float(baseline.get("local_density_max", 6.0))
        assert max_density <= baseline_local_density + 1.0, (
            f"Local crowding regressed: current={max_density}, baseline={baseline_local_density}"
        )

    def test_block_separation_targets(self, generated_doc: SchematicDoc) -> None:
        """Major stage groups should keep readable spacing between blocks."""
        ir = CircuitIR(**json.loads(_CIRCUIT_IR_PATH_10.read_text(encoding="utf-8")))
        block_layout = classify_circuit(ir)

        positions: dict[str, tuple[float, float, float | None]] = {}
        for sym in generated_doc.list_symbols():
            ref = sym["ref"]
            x = sym["x"]
            y = sym["y"]
            if isinstance(ref, str) and isinstance(x, float) and isinstance(y, float):
                positions[ref] = (x, y, None)

        separation = compute_block_separation(positions, block_layout)

        def _sep(role_a: BlockRole, role_b: BlockRole) -> float:
            pair = (role_a, role_b)
            reverse = (role_b, role_a)
            if pair in separation:
                return separation[pair]
            if reverse in separation:
                return separation[reverse]
            pytest.fail(f"Missing block-separation pair: {role_a.name} vs {role_b.name}")

        assert _sep(BlockRole.INPUT, BlockRole.OUTPUT) >= 40.0
        assert _sep(BlockRole.INPUT, BlockRole.POWER_ENTRY) >= 10.0
        assert _sep(BlockRole.OUTPUT, BlockRole.POWER_ENTRY) >= 10.0

    def test_syntax_and_structural_lints_remain_valid(
        self,
        generated_schematic_path: Path,
    ) -> None:
        """Phase 10.3: readability changes must preserve syntax and lint validity."""
        mutate_and_validate_sch(
            generated_schematic_path,
            lambda _doc: None,
            mode=ValidationMode.LINT,
            dry_run=True,
        )

    def test_erc_path_runs_when_supported_via_adapter(
        self,
        generated_schematic_path: Path,
    ) -> None:
        """Phase 10.3: KICAD mode should execute ERC path when adapter is supplied."""
        fake_cli = _FakeCli()
        mutate_and_validate_sch(
            generated_schematic_path,
            lambda _doc: None,
            mode=ValidationMode.KICAD,
            cli=fake_cli,
            dry_run=True,
        )
        assert fake_cli.erc_call_count == 1

    def test_transactional_no_overwrite_guarantee(
        self,
        generated_schematic_path: Path,
    ) -> None:
        """Phase 10.3: failed validation must not overwrite the original file."""
        original = generated_schematic_path.read_text(encoding="utf-8")

        def _inject_out_of_bounds_symbol(doc: SchematicDoc) -> None:
            symbol_node = parse("(symbol (at 450 100 0))")
            assert isinstance(symbol_node, ListNode)
            doc.root = ListNode(doc.root.items + (symbol_node,), doc.root.pos)

        with pytest.raises(LintError):
            mutate_and_validate_sch(
                generated_schematic_path,
                _inject_out_of_bounds_symbol,
                mode=ValidationMode.LINT,
            )

        assert generated_schematic_path.read_text(encoding="utf-8") == original
