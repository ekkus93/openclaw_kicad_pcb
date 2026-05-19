"""Phase 9 — Layout engine integration tests.

9.3  Full IR → GraphvizLayoutEngine.compute_symbol_positions returns
     ``(x, y, rotation_deg)`` with rotation already merged in.

Assertions
----------
* Tier x-ordering: x(J_IN) < x(R1) < x(U1) < x(J_OUT)  (left-to-right signal flow)
* Rotation R1 == 0°   (series resistor, x-spread to neighbours dominates in dot layout)
* Rotation C1 == 90°  (shunt/bypass cap: one signal pin, one power pin)
* Rotation J_IN == 0° (tier-0 input connector, pins face right)
* Rotation J_OUT == 180° (last-tier output connector, pins face left)
* ``make_layout_engine_with_ir`` produces identical results to ``make_layout_engine``.
"""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.graphviz_layout import GraphvizLayoutEngine
from kicad_pcb.layout_engine import make_layout_engine, make_layout_engine_with_ir

# ---------------------------------------------------------------------------
# Skip markers
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration

_dot_available = _gv_mod.find_dot_binary() is not None
requires_graphviz = pytest.mark.skipif(
    not _dot_available,
    reason="graphviz dot not found on PATH or GRAPHVIZ_DOT",
)

# ---------------------------------------------------------------------------
# Fixture IR: minimal headphone-amp signal path
#
#   J_IN --[IN_NET]--> R1 --[MID_NET]--> U1 --[OUT_NET]--> J_OUT
#                              |
#                         C1 (bypass)
#                              |
#                          [GND] (power)
# ---------------------------------------------------------------------------
#
# Component tier assignments (expected):
#   J_IN  → 0  (input connector, no upstream)
#   R1    → 1  (series passive after J_IN)
#   C1    → 1  (bypass cap: shunt from MID_NET to GND)
#   U1    → 2  (IC, downstream of R1)
#   J_OUT → 3  (output connector, downstream of U1)
#
# Orientation expectations:
#   J_IN  → 0°   (tier 0 connector)
#   R1    → 0°   (series passive, x-spread dominates in left-to-right dot layout)
#   C1    → 90°  (shunt: pin1 on signal MID_NET, pin2 on power GND)
#   U1    → 0°   (IC — not explicitly tested here)
#   J_OUT → 180° (max-tier connector)


def _build_headphone_amp_ir() -> CircuitIR:
    """Return a minimal headphone-amp topology as a CircuitIR."""
    components = [
        ComponentIR(ref="J_IN", symbol="Connector_Generic:Conn_01x01", value="IN"),
        ComponentIR(ref="R1", symbol="Device:R", value="10k"),
        ComponentIR(ref="C1", symbol="Device:C", value="100n"),
        ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
        ComponentIR(ref="J_OUT", symbol="Connector_Generic:Conn_01x01", value="OUT"),
    ]
    nets = [
        NetIR(
            name="IN_NET",
            pins=[PinRefIR(ref="J_IN", pin="1"), PinRefIR(ref="R1", pin="1")],
        ),
        NetIR(
            name="MID_NET",
            pins=[
                PinRefIR(ref="R1", pin="2"),
                PinRefIR(ref="U1", pin="2"),
                PinRefIR(ref="C1", pin="1"),  # shunt cap signal terminal
            ],
        ),
        NetIR(
            name="OUT_NET",
            pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="J_OUT", pin="1")],
        ),
        NetIR(
            name="GND",  # recognised as a power net
            pins=[PinRefIR(ref="C1", pin="2")],  # shunt cap ground terminal
        ),
    ]
    return CircuitIR(version="1", components=components, nets=nets)


_AMP_IR = _build_headphone_amp_ir()

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLayoutPipeline:
    """9.3 — GraphvizLayoutEngine produces (x, y, rotation) with correct values."""

    @requires_graphviz
    def test_returns_rotation_not_none(self) -> None:
        """All rotation values must be floats (not None) after Phase 9."""
        engine = make_layout_engine()
        result = engine.compute_symbol_positions(_AMP_IR)

        for ref, pos in result.items():
            assert pos[2] is not None, f"{ref}: expected float rotation, got None"
            assert isinstance(pos[2], float), f"{ref}: rotation must be float, got {type(pos[2])}"

    @requires_graphviz
    def test_tier_x_ordering(self) -> None:
        """Signal flow runs left-to-right: x(J_IN) < x(R1) < x(U1) < x(J_OUT)."""
        engine = make_layout_engine()
        result = engine.compute_symbol_positions(_AMP_IR)

        x_jin = result["J_IN"][0]
        x_r1 = result["R1"][0]
        x_u1 = result["U1"][0]
        x_jout = result["J_OUT"][0]

        assert x_jin < x_r1, f"J_IN ({x_jin:.2f}) should be left of R1 ({x_r1:.2f})"
        assert x_r1 < x_u1, f"R1 ({x_r1:.2f}) should be left of U1 ({x_u1:.2f})"
        assert x_u1 < x_jout, f"U1 ({x_u1:.2f}) should be left of J_OUT ({x_jout:.2f})"

    @requires_graphviz
    def test_input_connector_rotation_0(self) -> None:
        """J_IN is at tier 0 → rotation must be 0°."""
        engine = make_layout_engine()
        result = engine.compute_symbol_positions(_AMP_IR)
        assert result["J_IN"][2] == pytest.approx(0.0), (
            f"J_IN rotation: expected 0°, got {result['J_IN'][2]}"
        )

    @requires_graphviz
    def test_output_connector_rotation_180(self) -> None:
        """J_OUT is at the last tier → rotation must be 180°."""
        engine = make_layout_engine()
        result = engine.compute_symbol_positions(_AMP_IR)
        assert result["J_OUT"][2] == pytest.approx(180.0), (
            f"J_OUT rotation: expected 180°, got {result['J_OUT'][2]}"
        )

    @requires_graphviz
    def test_series_resistor_rotation_0(self) -> None:
        """R1 is a series passive — both pins on signal nets.

        In a left-to-right dot layout the x-spread to neighbours dominates,
        so the orientation heuristic returns 0°.
        """
        engine = make_layout_engine()
        result = engine.compute_symbol_positions(_AMP_IR)
        assert result["R1"][2] == pytest.approx(0.0), (
            f"R1 rotation: expected 0°, got {result['R1'][2]}"
        )

    @requires_graphviz
    def test_bypass_cap_rotation_90(self) -> None:
        """C1 is a shunt/bypass cap (pin1 on signal MID_NET, pin2 on power GND).

        Shunt topology is detected by compute_orientations → returns 90°.
        """
        engine = make_layout_engine()
        result = engine.compute_symbol_positions(_AMP_IR)
        assert result["C1"][2] == pytest.approx(90.0), (
            f"C1 rotation: expected 90°, got {result['C1'][2]}"
        )

    @requires_graphviz
    def test_positions_snapped_to_grid(self) -> None:
        """All x, y positions must lie on the 1.27 mm KiCad 50-mil grid."""
        engine = make_layout_engine()
        result = engine.compute_symbol_positions(_AMP_IR)

        grid = 1.27
        for ref, (x, y, _rot) in result.items():
            assert abs(x % grid) < 1e-6 or abs(x % grid - grid) < 1e-6, (
                f"{ref}: x={x:.4f} is not on the {grid} mm grid"
            )
            assert abs(y % grid) < 1e-6 or abs(y % grid - grid) < 1e-6, (
                f"{ref}: y={y:.4f} is not on the {grid} mm grid"
            )


class TestMakeLayoutEngineWithIr:
    """9.1 — make_layout_engine_with_ir convenience factory."""

    @requires_graphviz
    def test_produces_same_positions_as_make_layout_engine(self) -> None:
        """Both factories must produce identical (x, y, rotation) values."""
        engine_plain = make_layout_engine()
        engine_with_ir = make_layout_engine_with_ir(_AMP_IR)

        result_plain = engine_plain.compute_symbol_positions(_AMP_IR)
        result_with_ir = engine_with_ir.compute_symbol_positions(_AMP_IR)

        assert result_plain.keys() == result_with_ir.keys()
        for ref in result_plain:
            x_p, y_p, r_p = result_plain[ref]
            x_w, y_w, r_w = result_with_ir[ref]
            assert x_p == pytest.approx(x_w, abs=1e-4), f"{ref}: x mismatch"
            assert y_p == pytest.approx(y_w, abs=1e-4), f"{ref}: y mismatch"
            assert r_p == pytest.approx(r_w, abs=1e-4), f"{ref}: rotation mismatch"

    @requires_graphviz
    def test_factory_provides_tiers_correctly(self) -> None:
        """Engine created with make_layout_engine_with_ir has self._tiers set."""
        engine = make_layout_engine_with_ir(_AMP_IR)
        assert isinstance(engine, GraphvizLayoutEngine)
        assert engine._tiers is not None, "Expected pre-computed tiers on engine"
        assert "J_IN" in engine._tiers
        assert "J_OUT" in engine._tiers
        # J_IN should be tier 0 (input connector); J_OUT should be the max tier.
        assert engine._tiers["J_IN"] == 0
        assert engine._tiers["J_OUT"] == max(engine._tiers.values())
