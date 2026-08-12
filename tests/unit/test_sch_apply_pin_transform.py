"""Unit tests for extracted helpers in commands/_sch_apply.py and commands/_validate.py.

Covers:
* ``_transform_pin_at``   — pure coordinate transformation
* ``advisory_warnings``   — non-blocking IR health checks
* ``full_validate``        — 3-layer file-based validation
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from pytest import approx

from kicad_pcb._router_geometry_basic import _stub_end
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply import (
    _expand_generation_ir,
    _PlacedSymbolSpec,
    _resolve_placed_symbol_pin_at,
    _transform_pin_at,
    _write_symbols,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse
from kicad_pcb.symbol_index import SymbolIndex

pytestmark = pytest.mark.unit

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
_KICAD_SYSTEM_SYMBOLS = Path("/usr/share/kicad/symbols")
_REAL_NE5532_REVIEW_NETLIST = (
    Path(__file__).resolve().parents[2] / "docs" / "circuits" / "ne5532_headphone_amp_netlist.json"
)


def _make_ir(
    *,
    components: list[ComponentIR],
    nets: list[NetIR],
) -> CircuitIR:
    return CircuitIR(version="1", components=components, nets=nets)


# ---------------------------------------------------------------------------
# _transform_pin_at
# ---------------------------------------------------------------------------


class TestTransformPinAt:
    """_transform_pin_at(pin_at, origin_x, origin_y, rotation) -> transformed map."""

    def test_identity_rotation(self) -> None:
        """rotation=0 preserves local position while translating to the symbol origin."""
        pin_at: dict[str, tuple[float, float, float]] = {
            "1": (10.0, 20.0, 90.0),
            "2": (-5.0, 3.0, 180.0),
        }
        result = _transform_pin_at(pin_at, 100.0, 200.0, rotation=0)
        assert result == {
            "1": (approx(110.0), approx(220.0), approx(270.0)),
            "2": (approx(95.0), approx(203.0), approx(180.0)),
        }

    def test_rotation_90(self) -> None:
        """90° placement applies KiCad's direct symbol transform."""
        pin_at: dict[str, tuple[float, float, float]] = {"1": (3.0, 4.0, 45.0)}
        result = _transform_pin_at(pin_at, 0.0, 0.0, rotation=90)
        rx, ry, ra = result["1"]
        assert rx == approx(-4.0, abs=1e-9)
        assert ry == approx(3.0, abs=1e-9)
        assert ra == pytest.approx((90 - 45) % 360)

    def test_rotation_180(self) -> None:
        """180° placement negates both local position axes."""
        pin_at: dict[str, tuple[float, float, float]] = {"1": (3.0, 4.0, 30.0)}
        result = _transform_pin_at(pin_at, 0.0, 0.0, rotation=180)
        rx, ry, ra = result["1"]
        assert rx == approx(-3.0, abs=1e-9)
        assert ry == approx(-4.0, abs=1e-9)
        assert ra == pytest.approx((180 - 30) % 360)

    def test_angle_wraps_below_360(self) -> None:
        """Resulting angle is always in [0, 360)."""
        pin_at: dict[str, tuple[float, float, float]] = {"1": (0.0, 0.0, 270.0)}
        result = _transform_pin_at(pin_at, 0.0, 0.0, rotation=180)
        angle = result["1"][2]
        assert 0.0 <= angle < 360.0
        assert angle == pytest.approx((180 - 270) % 360)  # 270°

    def test_origin_applied_correctly(self) -> None:
        """Non-zero origin is added after KiCad's direct symbol transform."""
        pin_at: dict[str, tuple[float, float, float]] = {"1": (3.0, 4.0, 0.0)}
        result = _transform_pin_at(pin_at, 10.0, 20.0, rotation=90)
        rx, ry, _ = result["1"]
        assert rx == approx(10.0 + (-4.0), abs=1e-9)
        assert ry == approx(20.0 + 3.0, abs=1e-9)

    def test_vertical_power_pin_angle_uses_same_transform_as_position(self) -> None:
        """Vertical library pins keep their bodyward direction after a 90° placement."""
        pin_at = {
            "4": (2.54, 2.54, 270.0),
            "8": (2.54, -5.08, 90.0),
        }

        result = _transform_pin_at(pin_at, 100.0, 50.0, rotation=90)

        assert result["4"][2] == pytest.approx(180.0)
        assert result["8"][2] == pytest.approx(0.0)

    def test_rotated_resistor_stubs_do_not_terminate_on_opposite_pins(self) -> None:
        """A routing stub must extend away from the resistor body, never across it."""
        resistor_pins = {
            "1": (0.0, 0.0, 0.0),
            "2": (5.08, 0.0, 180.0),
        }
        r1 = _transform_pin_at(resistor_pins, 130.81, 105.41, rotation=270)
        r2 = _transform_pin_at(resistor_pins, 130.48, 120.65, rotation=90)

        assert r1["2"] == pytest.approx((130.81, 100.33, 90.0))
        assert r2["1"] == pytest.approx((130.48, 120.65, 90.0))

        r1_stub = _stub_end(*r1["2"])
        r2_stub = _stub_end(*r2["1"])
        assert math.dist(r1_stub, r1["1"][:2]) > 0.01
        assert math.dist(r2_stub, r2["2"][:2]) > 0.01

    def test_empty_pin_map(self) -> None:
        """An empty pin map returns an empty dict without error."""
        result = _transform_pin_at({}, 0.0, 0.0, rotation=45)
        assert result == {}

    def test_preserves_all_pins(self) -> None:
        """All entries in the input map appear in the output."""
        pin_at: dict[str, tuple[float, float, float]] = {
            f"{i}": (float(i), float(i), 0.0) for i in range(1, 6)
        }
        result = _transform_pin_at(pin_at, 0.0, 0.0, rotation=0)
        assert set(result.keys()) == set(pin_at.keys())


def test_write_symbols_flips_two_pin_passive_to_match_pin_nets() -> None:
    doc = SchematicDoc(parse('(kicad_sch (version 20231120) (generator "test"))'))
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J1", symbol="TestLib:Conn3", value="LEFT"),
            ComponentIR(ref="R1", symbol="TestLib:R", value="10k"),
            ComponentIR(ref="J2", symbol="TestLib:Conn3", value="RIGHT"),
        ],
        nets=[
            NetIR(
                name="LEFT_NET",
                pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="2")],
            ),
            NetIR(
                name="RIGHT_NET",
                pins=[PinRefIR(ref="J2", pin="1"), PinRefIR(ref="R1", pin="1")],
            ),
        ],
    )
    stats = {
        "symbols": 0,
        "wires": 0,
        "labels": 0,
        "global_labels": 0,
        "junctions": 0,
        "binding_markers": 0,
    }

    class _StaticLayoutEngine:
        def compute_symbol_positions(
            self,
            _ir: CircuitIR,
        ) -> dict[str, tuple[float, float, float | None]]:
            return {
                "J1": (0.0, 0.0, 0.0),
                "R1": (10.0, 0.0, 0.0),
                "J2": (20.0, 0.0, 0.0),
            }

    _positions, pin_endpoints, _pin_anchors, _missing, _raw_layout = _write_symbols(
        doc=doc,
        ir=ir,
        symbol_index=SymbolIndex(symbols_dir=_FIXTURES_DIR),
        project_name="test",
        stats=stats,
        engine=_StaticLayoutEngine(),
    )

    assert pin_endpoints[("R1", "1")][0] > pin_endpoints[("R1", "2")][0]


class TestExpandGenerationIr:
    def test_splits_fixture_dual_op_amp_into_explicit_units(self) -> None:
        ir = _make_ir(
            components=[
                ComponentIR(ref="U1", symbol="TestLib:DualOpAmp", value="DualOpAmp"),
                ComponentIR(ref="R1", symbol="TestLib:R", value="10k"),
                ComponentIR(ref="R2", symbol="TestLib:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="IN_A",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="R1", pin="1")],
                ),
                NetIR(
                    name="OUT_A",
                    pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="R1", pin="2")],
                ),
                NetIR(
                    name="IN_B",
                    pins=[PinRefIR(ref="U1", pin="5"), PinRefIR(ref="R2", pin="1")],
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="R2", pin="2")],
                ),
                NetIR(name="VCC", pins=[PinRefIR(ref="U1", pin="8")]),
                NetIR(name="GND", pins=[PinRefIR(ref="U1", pin="4")]),
            ],
        )

        expanded_ir, placed_symbols = _expand_generation_ir(
            ir,
            SymbolIndex(symbols_dir=_FIXTURES_DIR),
        )

        refs = [component.ref for component in expanded_ir.components]
        assert refs == ["U1A", "U1B", "U1P", "R1", "R2"]
        assert placed_symbols["U1A"].unit == 1
        assert placed_symbols["U1A"].pin_nums == ("1", "2", "3")
        assert placed_symbols["U1B"].unit == 2
        assert placed_symbols["U1B"].pin_nums == ("5", "6", "7")
        assert placed_symbols["U1P"].unit == 3
        assert placed_symbols["U1P"].pin_nums == ("4", "8")

        bindings = {(pin.ref, pin.pin): net.name for net in expanded_ir.nets for pin in net.pins}
        assert bindings[("U1A", "1")] == "IN_A"
        assert bindings[("U1A", "3")] == "OUT_A"
        assert bindings[("U1B", "5")] == "IN_B"
        assert bindings[("U1B", "7")] == "OUT_B"
        assert bindings[("U1P", "4")] == "GND"
        assert bindings[("U1P", "8")] == "VCC"


class TestResolvePlacedSymbolPinAt:
    def test_returns_unit_local_geometry_for_signal_unit(self) -> None:
        pin_at = _resolve_placed_symbol_pin_at(
            "TestLib:DualOpAmp",
            _PlacedSymbolSpec(unit=1, pin_nums=("1", "2", "3"), logical_ref="U1"),
            SymbolIndex(symbols_dir=_FIXTURES_DIR),
        )

        assert pin_at == {
            "1": (0.0, 0.0, 0.0),
            "2": (0.0, -2.54, 0.0),
            "3": (5.08, -1.27, 180.0),
        }

    def test_returns_unit_local_geometry_for_power_unit(self) -> None:
        pin_at = _resolve_placed_symbol_pin_at(
            "TestLib:DualOpAmp",
            _PlacedSymbolSpec(unit=3, pin_nums=("4", "8"), logical_ref="U1"),
            SymbolIndex(symbols_dir=_FIXTURES_DIR),
        )

        assert pin_at == {
            "4": (2.54, 2.54, 270.0),
            "8": (2.54, -5.08, 90.0),
        }
