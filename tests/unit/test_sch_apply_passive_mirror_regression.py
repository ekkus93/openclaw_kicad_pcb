"""Regression coverage for two-pin passive mirror refinement."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb._router_geometry_basic import _stub_end
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply_write import (
    _refine_two_pin_passive_mirrors,
    _transform_pin_at,
)
from kicad_pcb.symbol_index import SymbolIndex

pytestmark = pytest.mark.unit

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"


def test_transform_pin_at_matches_kicad9_symbol_transform() -> None:
    """Library pin coordinates must follow KiCad 9's direct symbol transform."""
    pins = {
        "1": (0.0, 0.0, 0.0),
        "2": (5.08, 0.0, 180.0),
        "3": (0.0, 2.54, 90.0),
    }

    unrotated = _transform_pin_at(pins, 100.0, 200.0, 0)
    clockwise_90 = _transform_pin_at(pins, 100.0, 200.0, 90)
    clockwise_270 = _transform_pin_at(pins, 100.0, 200.0, 270)

    assert unrotated["3"] == pytest.approx((100.0, 202.54, 270.0))
    assert clockwise_90["2"] == pytest.approx((100.0, 205.08, 270.0))
    assert clockwise_270["2"] == pytest.approx((100.0, 194.92, 90.0))


def test_divider_mirror_refinement_makes_vmid_stubs_face_each_other() -> None:
    """The exact integration-divider geometry must make both VMID stubs meet."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="TestLib:R", value="10k"),
            ComponentIR(ref="R2", symbol="TestLib:R", value="10k"),
        ],
        nets=[
            NetIR(name="VCC", pins=[PinRefIR(ref="R1", pin="1")]),
            NetIR(
                name="VMID",
                pins=[
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="R2", pin="1"),
                ],
            ),
            NetIR(name="GND", pins=[PinRefIR(ref="R2", pin="2")]),
        ],
    )
    layout = {
        "R1": (130.81, 105.41),
        "R2": (130.48, 120.65),
    }

    refined = _refine_two_pin_passive_mirrors(
        ir=ir,
        layout=layout,
        orientations={"R1": 270, "R2": 90},
        placed_symbol_specs=None,
        symbol_index=SymbolIndex(symbols_dir=_FIXTURES_DIR),
    )

    assert refined == {"R1": 90, "R2": 90}

    resistor_pins = {
        "1": (0.0, 0.0, 0.0),
        "2": (5.08, 0.0, 180.0),
    }
    r1 = _transform_pin_at(resistor_pins, *layout["R1"], refined["R1"])
    r2 = _transform_pin_at(resistor_pins, *layout["R2"], refined["R2"])

    assert r1["2"][2] == pytest.approx(270.0)
    assert r2["1"][2] == pytest.approx(90.0)
    assert _stub_end(*r1["2"]) == pytest.approx((130.81, 115.57), abs=0.01)
    assert _stub_end(*r2["1"]) == pytest.approx((130.48, 115.57), abs=0.01)
