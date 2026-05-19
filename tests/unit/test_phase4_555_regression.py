"""Phase 4 regression coverage for the canonical 555 PWM dimmer fixture."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR, NetIR, PinRefIR
from kicad_pcb.commands._validate import advisory_warnings
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.ir.validate import validate_circuit_ir
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.symbol_index import SymbolIndex
from tests import SYMBOLS_FIXTURE_DIR, TIMER555_PWM_READABILITY_FIXTURE

_FIXTURE = TIMER555_PWM_READABILITY_FIXTURE
_CIRCUIT_IR_PATH = _FIXTURE.circuit_ir_path
_SYMBOLS_DIR = SYMBOLS_FIXTURE_DIR
_EXPECTED_REFS = {
    "U1",
    "Q1",
    "RV1",
    "D1",
    "D2",
    "R1",
    "R2",
    "R3",
    "C1",
    "C2",
    "C3",
    "C4",
    "J1",
}
_TIMER555_CODES = {
    "TIMER555_GROUND_PIN_INVALID",
    "TIMER555_VCC_PIN_INVALID",
    "TIMER555_RESET_NOT_TIED_HIGH",
    "TIMER555_TIMING_NODE_SPLIT",
    "TIMER555_TIMING_CAP_NOT_TO_GROUND",
    "TIMER555_TIMING_CAP_ACROSS_SUPPLY",
    "TIMER555_CTRL_CAP_MISSING_TO_GROUND",
    "TIMER555_CTRL_CAP_WRONG_TARGET",
    "TIMER555_STEERING_NETWORK_INVALID",
    "TIMER555_GATE_RESISTOR_MISSING",
    "TIMER555_GATE_PULLDOWN_MISSING",
    "TIMER555_GATE_PULLDOWN_TOUCHES_TIMING_NODE",
    "TIMER555_LOW_SIDE_LOAD_TOPOLOGY_INVALID",
    "TIMER555_PWM_FREQUENCY_OUT_OF_RANGE",
}


def _pin_to_net_map(ir: CircuitIR) -> dict[tuple[str, str], str]:
    return {(pin.ref, pin.pin): net.name for net in ir.nets for pin in net.pins}


@pytest.mark.skipif(not _CIRCUIT_IR_PATH.exists(), reason="555 PWM fixture missing")
class TestPhase4555Regression:
    @pytest.fixture
    def fixture_ir(self) -> CircuitIR:
        return CircuitIR(**json.loads(_CIRCUIT_IR_PATH.read_text(encoding="utf-8")))

    @pytest.fixture
    def generated_schematic_path(self, tmp_path: Path) -> Path:
        result = cmd_new_from_netlist(
            Namespace(
                name="phase4_timer555",
                out_dir=str(tmp_path),
                description="",
                netlist=str(_CIRCUIT_IR_PATH),
                symbols_dir=str(_SYMBOLS_DIR),
                mode="internal",
            )
        )
        diagnostics = result.generated_schematic_diagnostics
        assert diagnostics is not None
        assert not diagnostics.hard_failures
        assert not diagnostics.unresolved_refs
        assert not diagnostics.missing_bindings
        assert not diagnostics.duplicate_bindings
        return result.managed_schematic_path

    @pytest.fixture
    def generated_doc(self, generated_schematic_path: Path) -> SchematicDoc:
        return SchematicDoc.load(generated_schematic_path)

    def test_fixture_validates_without_duplicate_pin_membership(
        self,
        fixture_ir: CircuitIR,
    ) -> None:
        validate_circuit_ir(fixture_ir)

    def test_fixture_has_no_555_advisories(self, fixture_ir: CircuitIR) -> None:
        codes = {
            warning["code"]
            for warning in advisory_warnings(fixture_ir, SymbolIndex(symbols_dir=_SYMBOLS_DIR))
        }
        assert not (_TIMER555_CODES & codes)

    def test_fixture_encodes_expected_steering_diode_direction(
        self,
        fixture_ir: CircuitIR,
    ) -> None:
        pin_to_net = _pin_to_net_map(fixture_ir)

        assert pin_to_net[("D1", "1")] == "DISCH"
        assert pin_to_net[("D1", "2")] == "POT_A"
        assert pin_to_net[("D2", "1")] == "POT_B"
        assert pin_to_net[("D2", "2")] == "DISCH"
        assert pin_to_net[("RV1", "2")] == "TIMING"

    def test_fixture_warns_when_timing_capacitor_moves_to_supply_rail(
        self,
        fixture_ir: CircuitIR,
    ) -> None:
        retimed_nets: list[NetIR] = []
        for net in fixture_ir.nets:
            filtered_pins = [pin for pin in net.pins if not (pin.ref == "C1" and pin.pin == "1")]
            if net.name == "TIMING":
                retimed_nets.append(NetIR(name=net.name, pins=filtered_pins))
                continue
            if net.name == "+12V":
                retimed_nets.append(
                    NetIR(
                        name=net.name,
                        pins=[*filtered_pins, PinRefIR(ref="C1", pin="1")],
                    )
                )
                continue
            retimed_nets.append(NetIR(name=net.name, pins=filtered_pins))

        broken_ir = fixture_ir.model_copy(update={"nets": retimed_nets})
        codes = {
            warning["code"]
            for warning in advisory_warnings(broken_ir, SymbolIndex(symbols_dir=_SYMBOLS_DIR))
        }

        assert "TIMER555_TIMING_CAP_NOT_TO_GROUND" in codes

    def test_fixture_warns_when_gate_pulldown_touches_timing_node(
        self,
        fixture_ir: CircuitIR,
    ) -> None:
        retimed_nets: list[NetIR] = []
        for net in fixture_ir.nets:
            filtered_pins = [pin for pin in net.pins if not (pin.ref == "R3" and pin.pin == "2")]
            if net.name == "GND":
                retimed_nets.append(NetIR(name=net.name, pins=filtered_pins))
                continue
            if net.name == "TIMING":
                retimed_nets.append(
                    NetIR(
                        name=net.name,
                        pins=[*filtered_pins, PinRefIR(ref="R3", pin="2")],
                    )
                )
                continue
            retimed_nets.append(NetIR(name=net.name, pins=filtered_pins))

        broken_ir = fixture_ir.model_copy(update={"nets": retimed_nets})
        codes = {
            warning["code"]
            for warning in advisory_warnings(broken_ir, SymbolIndex(symbols_dir=_SYMBOLS_DIR))
        }

        assert "TIMER555_GATE_PULLDOWN_TOUCHES_TIMING_NODE" in codes

    def test_generated_schematic_is_structurally_populated(
        self,
        generated_doc: SchematicDoc,
    ) -> None:
        refs = {
            symbol["ref"]
            for symbol in generated_doc.list_symbols()
            if isinstance(symbol.get("ref"), str)
        }

        assert refs >= _EXPECTED_REFS
        assert len(generated_doc.list_symbols()) >= len(_EXPECTED_REFS)
        assert generated_doc.count_nodes("wire") > 0

    def test_generated_layout_keeps_555_blocks_readable(
        self,
        generated_doc: SchematicDoc,
    ) -> None:
        positions = {
            symbol["ref"]: (symbol["x"], symbol["y"])
            for symbol in generated_doc.list_symbols()
            if isinstance(symbol.get("ref"), str)
            and isinstance(symbol.get("x"), float)
            and isinstance(symbol.get("y"), float)
        }

        timer_x, timer_y = positions["U1"]
        mosfet_x, mosfet_y = positions["Q1"]
        load_x, load_y = positions["J1"]
        gate_resistor_x, _gate_resistor_y = positions["R2"]
        timing_cap_x, timing_cap_y = positions["C1"]
        ctrl_cap_x, ctrl_cap_y = positions["C4"]
        decoupling_x, decoupling_y = positions["C2"]

        assert timer_x < mosfet_x < load_x
        assert abs(load_y - timer_y) <= 10.0
        assert timer_x < gate_resistor_x < mosfet_x
        assert abs(timing_cap_x - timer_x) <= 10.0
        assert abs(timing_cap_y - timer_y) <= 15.5
        assert abs(ctrl_cap_x - timer_x) <= 10.0
        assert abs(ctrl_cap_y - timer_y) <= 20.0
        assert decoupling_x <= timer_x
        assert decoupling_y < timer_y
        assert mosfet_y <= timer_y
