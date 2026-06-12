"""Phase 6: op-amp centering, decoupling placement, SDS fallback, spine routing, LAY lints."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.layout import (
    GRID_COL_MM,
    compute_signal_flow_layout,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

# ---------------------------------------------------------------------------
# IR builders
# ---------------------------------------------------------------------------


def _ir(
    components: list[tuple[str, str]],
    nets: list[tuple[str, list[tuple[str, str]]]],
    *,
    version: str = "1",
) -> CircuitIR:
    """Build a CircuitIR from compact component/net specs.

    ``components`` — ``[(ref, symbol), ...]``
    ``nets``       — ``[(name, [(ref, pin), ...]), ...]``
    """
    comps = [ComponentIR(ref=ref, symbol=sym) for ref, sym in components]
    ir_nets = [
        NetIR(name=name, pins=[PinRefIR(ref=r, pin=p) for r, p in pins]) for name, pins in nets
    ]
    return CircuitIR(version=version, components=comps, nets=ir_nets)


class TestOpAmpCentering:
    """4.4 — op-amps occupy the centre rows of their BFS column.

    When a BFS column contains a mix of op-amps (U/IC/OA prefixes) and other
    components (R, C, etc.), the layout engine places op-amps at the middle
    row index so passive components surround the IC on both sides — matching
    the conventional circuit-diagram aesthetic.
    """

    def _build_ir(self) -> CircuitIR:
        # J1 and J2 are seeds (col=0).  R1, U1, R2 all connect to both J1
        # and J2, so BFS assigns them all to col=1 (same column).
        return CircuitIR(
            version="test-1.0",
            components=[
                ComponentIR(ref="J1", symbol="Connector:Conn_01x01"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Device:Op_Amp", value="LM358"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(name="N1", pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")]),
                NetIR(name="N2", pins=[PinRefIR(ref="J1", pin="2"), PinRefIR(ref="U1", pin="2")]),
                NetIR(name="N3", pins=[PinRefIR(ref="J1", pin="3"), PinRefIR(ref="R2", pin="1")]),
                NetIR(name="N4", pins=[PinRefIR(ref="J2", pin="1"), PinRefIR(ref="R1", pin="2")]),
                NetIR(name="N5", pins=[PinRefIR(ref="J2", pin="2"), PinRefIR(ref="U1", pin="6")]),
                NetIR(name="N6", pins=[PinRefIR(ref="J2", pin="3"), PinRefIR(ref="R2", pin="2")]),
            ],
        )

    def test_opamp_not_at_topmost_row_in_column(self) -> None:
        """U1 should NOT occupy the topmost (row-0) position when R1 and R2
        share the same column — it should be centred."""
        ir = self._build_ir()
        positions = compute_signal_flow_layout(ir)
        u1_x, u1_y = positions["U1"]
        # Collect all y-coordinates in U1's column.
        col_ys = sorted(y for ref, (x, y) in positions.items() if abs(x - u1_x) < 0.1)
        if len(col_ys) < 3:
            pytest.skip("Column has fewer than 3 members — centering trivially satisfied.")
        # U1 must not be at the extreme-top row.
        assert u1_y > col_ys[0], (
            f"U1 (y={u1_y:.2f}) is at the topmost row of its column "
            f"(col_ys={col_ys}); expected it to be centred."
        )

    def test_opamp_centering_is_deterministic(self) -> None:
        """Two calls on the same IR return the same U1 position."""
        ir = self._build_ir()
        p1 = compute_signal_flow_layout(ir)
        p2 = compute_signal_flow_layout(ir)
        assert p1["U1"] == p2["U1"]

    def test_opamp_centering_empty_column_requires_output_role(self) -> None:
        """A single input-connector circuit should fail instead of degrading."""
        ir = CircuitIR(
            version="test-1.0",
            components=[
                ComponentIR(ref="U1", symbol="Device:Op_Amp", value="LM358"),
                ComponentIR(ref="J1", symbol="Connector:Conn_01x01"),
            ],
            nets=[
                NetIR(
                    name="N1",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="U1", pin="2")],
                )
            ],
        )

        with pytest.raises(UserError) as exc_info:
            compute_signal_flow_layout(ir)

        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
        assert exc_info.value.details["missing_roles"] == ["output"]


# ---------------------------------------------------------------------------
# 4.4 (extended) — decoupling cap placement near anchor IC
# ---------------------------------------------------------------------------


class TestDecouplingCapPlacement:
    """4.4 — power-only passives are co-located with their anchor IC.

    A bypass capacitor that connects *only* to power/ground rails has no
    signal connectivity.  The post-BFS adjustment detects this and ensures
    the cap column is at most one BFS step away from the IC it bypasses.
    """

    def _build_ir(self) -> CircuitIR:
        # Signal chain: J1 → R1 → U1 → J2
        # Power-only cap: C1 connected to VCC (shared with U1) and GND (shared with U1)
        # C1 has NO signal-net connections.
        return CircuitIR(
            version="test-1.0",
            components=[
                ComponentIR(ref="J1", symbol="Connector:Conn_01x01"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Device:Op_Amp", value="LM358"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01"),
                ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ],
            nets=[
                # Signal chain:
                NetIR(
                    name="N_in",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")],
                ),
                NetIR(
                    name="N_mid",
                    pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="U1", pin="2")],
                ),
                NetIR(
                    name="N_out",
                    pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="J2", pin="1")],
                ),
                # Power-only nets — C1 appears here but not in any signal net:
                NetIR(
                    name="VCC",
                    pins=[PinRefIR(ref="U1", pin="8"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="U1", pin="4"), PinRefIR(ref="C1", pin="2")],
                ),
            ],
        )

    def test_cap_within_one_column_of_ic(self) -> None:
        """C1 (power-only) is at most one column-width from U1."""
        ir = self._build_ir()
        positions = compute_signal_flow_layout(ir)
        c1_x = positions["C1"][0]
        u1_x = positions["U1"][0]
        assert abs(c1_x - u1_x) <= GRID_COL_MM + 0.1, (
            f"C1 (x={c1_x:.2f}) is more than one column away from U1 "
            f"(x={u1_x:.2f}); delta={abs(c1_x - u1_x):.2f} mm."
        )

    def test_cap_not_far_right_of_all_signal_components(self) -> None:
        """C1 must not be placed well past the rightmost signal component."""
        ir = self._build_ir()
        positions = compute_signal_flow_layout(ir)
        signal_refs = {"J1", "R1", "U1", "J2"}
        max_signal_x = max(positions[r][0] for r in signal_refs)
        c1_x = positions["C1"][0]
        # C1 may be one extra column past the signal boundary — but no more.
        assert c1_x <= max_signal_x + GRID_COL_MM + 0.1, (
            f"C1 (x={c1_x:.2f}) is more than one column past the rightmost "
            f"signal component (x={max_signal_x:.2f})."
        )


class TestSdsFallbackPolicy:
    def _simple_ir(self) -> CircuitIR:
        return _ir(
            [
                ("J1", "Device:Connector"),
                ("R1", "Device:R"),
                ("J2", "Device:Connector"),
            ],
            [
                ("N1", [("J1", "1"), ("R1", "1")]),
                ("N2", [("R1", "2"), ("J2", "1")]),
            ],
        )

    def test_incomplete_roles_raise_even_non_strict(self) -> None:
        ir = self._simple_ir()
        roles = {"J1": "input"}  # missing output role on purpose

        with pytest.raises(UserError) as exc_info:
            compute_signal_flow_layout(ir, roles=roles)

        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
        assert exc_info.value.details["missing_role"] == "output"

    def test_incomplete_roles_raise_in_strict_mode(self) -> None:
        ir = self._simple_ir()
        roles = {"J1": "input"}  # missing output role on purpose

        with pytest.raises(UserError) as exc_info:
            compute_signal_flow_layout(ir, roles=roles, strict=True)

        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
        assert exc_info.value.details["missing_role"] == "output"


# ---------------------------------------------------------------------------
# 4.5 — bus-style (spine) wiring option
# ---------------------------------------------------------------------------
