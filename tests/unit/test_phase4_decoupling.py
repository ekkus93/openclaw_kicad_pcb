"""Phase 4: decoupling cap detection and co-location tests."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import classify_circuit
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    GRID_COL_MM,
)
from kicad_pcb.lint import LintSeverity
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WARN = LintSeverity.WARNING
_ERR = LintSeverity.ERROR


def _minimal_ir(
    *,
    refs: list[str] | None = None,
    nets: list[dict] | None = None,
) -> CircuitIR:
    """Build a minimal CircuitIR for testing.

    *refs* defaults to ["R1", "R2"].
    *nets* is a list of dicts with keys ``name`` and ``pins`` (list of
    ``{"ref": ..., "pin": ...}`` dicts).
    """
    if refs is None:
        refs = ["R1", "R2"]
    if nets is None:
        nets = [
            {"name": "NET1", "pins": [{"ref": refs[0], "pin": "1"}, {"ref": refs[1], "pin": "1"}]}
        ]

    components = [ComponentIR(ref=r, symbol="Device:R", value="1k") for r in refs]
    ir_nets = [NetIR(name=n["name"], pins=[PinRefIR(**p) for p in n["pins"]]) for n in nets]
    return CircuitIR(version="1", components=components, nets=ir_nets)


def _sch(body: str = "") -> ListNode:
    """Parse a minimal kicad_sch document with optional *body*."""
    return parse(
        "(kicad_sch (version 20230121) (generator test)\n"
        "  (lib_symbols)\n"
        f"  {body}\n"
        '  (sheet_instances (path "/" (page "1")))\n'
        ")"
    )


def _wire(x1: float, y1: float, x2: float, y2: float) -> str:
    """Return an S-expression wire snippet."""
    return f"(wire (pts (xy {x1} {y1}) (xy {x2} {y2})))"


def _symbol_at(x: float, y: float) -> str:
    """Return a minimal symbol snippet at (x, y)."""
    return (
        f'(symbol (lib_id "Device:R") (at {x} {y} 0) (uuid "00000000-0000-0000-0000-000000000001"))'
    )


def _label(name: str, x: float = 10.0, y: float = 10.0) -> str:
    return f'(label "{name}" (at {x} {y} 0))'


def _codes(issues: list) -> list[str]:
    return [i.code for i in issues]


def _sev(issues: list, code: str) -> LintSeverity | None:
    for i in issues:
        if i.code == code:
            return i.severity
    return None


def _make_ir(
    components: list[tuple[str, str]],
    nets: list[tuple[str, list[tuple[str, str]]]],
    *,
    version: str = "1",
) -> CircuitIR:
    """Minimal CircuitIR factory.

    *components* is ``[(ref, symbol), ...]``.
    *nets* is ``[(net_name, [(ref, pin), ...]), ...]``.
    """
    ir_components = [ComponentIR(ref=ref, symbol=sym) for ref, sym in components]
    ir_nets = [
        NetIR(name=name, pins=[PinRefIR(ref=r, pin=p) for r, p in pins]) for name, pins in nets
    ]
    if not ir_components:
        ir_components = [ComponentIR(ref="_DUMMY", symbol="_")]
    if not ir_nets:
        ir_nets = [NetIR(name="_NC", pins=[PinRefIR(ref=ir_components[0].ref, pin="1")])]
    return CircuitIR(version=version, components=ir_components, nets=ir_nets)


# ---------------------------------------------------------------------------
# 4.1 / 4.2  LayoutEngine factory — NoneLayoutEngine
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 3 — Decoupling cap co-location (Rule §5)
# ---------------------------------------------------------------------------


def _decoupling_ir() -> CircuitIR:
    """Return an IR with C1 as a decoupling cap (one signal net, one power net).

    Topology:
    * J1 -- IN_SIG --> R1 -- OUT_SIG --> U1
    * C1: pin1 on VCC_LOCAL (signal, shared with U1), pin2 on GND (power)
    * VCC_LOCAL is NOT in the power-net pattern so it is treated as a signal net.
    """
    components = [
        ComponentIR(ref="J1", symbol="Device:Conn", value="Input"),
        ComponentIR(ref="R1", symbol="Device:R", value="10k"),
        ComponentIR(ref="U1", symbol="Device:IC", value="OpAmp"),
        ComponentIR(ref="C1", symbol="Device:C", value="100n"),
    ]
    nets = [
        NetIR(
            name="IN_SIG",
            pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")],
        ),
        NetIR(
            name="OUT_SIG",
            pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="U1", pin="1")],
        ),
        # VCC_LOCAL: shared between U1's power pin and C1's signal pin.
        NetIR(
            name="VCC_LOCAL",
            pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="C1", pin="1")],
        ),
        # GND: power net — C1's second pin and J1's return.
        NetIR(
            name="GND",
            pins=[PinRefIR(ref="J1", pin="2"), PinRefIR(ref="C1", pin="2")],
        ),
    ]
    return CircuitIR(version="1", components=components, nets=nets)


class TestFindDecouplingCaps:
    """Unit tests for _find_decoupling_caps()."""

    def test_cap_with_one_signal_pin_detected(self) -> None:
        """C1 with VCC_LOCAL (signal) + GND (power) → detected as decoupling cap for U1."""
        ir = _decoupling_ir()
        result = _gv_mod.find_decoupling_caps(ir)
        assert result == {"C1": "U1"}, (
            f"Expected C1 to be mapped to U1 as decoupling cap, got: {result}"
        )

    def test_cap_with_shared_local_rail_prefers_ic_over_passive(self) -> None:
        """A local rail cap should anchor to the active stage, not the first passive on that net."""
        components = [
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="In"),
            ComponentIR(ref="R1", symbol="Device:R", value="1k"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
        ]
        nets = [
            NetIR(
                name="IN_SIG",
                pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")],
            ),
            NetIR(
                name="LOCAL_BIAS",
                pins=[
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="U1", pin="7"),
                    PinRefIR(ref="C1", pin="1"),
                ],
            ),
            NetIR(
                name="OUT_SIG",
                pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(name="GND", pins=[PinRefIR(ref="C1", pin="2")]),
        ]

        ir = CircuitIR(version="1", components=components, nets=nets)
        result = _gv_mod.find_decoupling_caps(ir)
        assert result == {"C1": "U1"}, (
            "Expected decoupling cap to prefer the active IC anchor over the upstream resistor, "
            f"got: {result}"
        )

    def test_true_bypass_cap_not_detected(self) -> None:
        """C1 with both VCC and GND (both power nets) → not treated as decoupling cap."""
        components = [
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
        ]
        nets = [
            NetIR(name="VCC", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="C1", pin="1")]),
            NetIR(name="GND", pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="C1", pin="2")]),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)
        result = _gv_mod.find_decoupling_caps(ir)
        assert result == {}, f"Expected empty map for true bypass cap, got: {result}"

    def test_true_bypass_cap_on_active_rail_detected(self) -> None:
        """A rail-to-ground bypass cap should anchor to the active IC on that rail."""
        components = [
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="In"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
        ]
        nets = [
            NetIR(
                name="IN_SIG",
                pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="U1", pin="3")],
            ),
            NetIR(
                name="OUT_SIG",
                pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="VCC",
                pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="C1", pin="1")],
            ),
            NetIR(
                name="GND",
                pins=[PinRefIR(ref="U1", pin="4"), PinRefIR(ref="C1", pin="2")],
            ),
        ]

        ir = CircuitIR(version="1", components=components, nets=nets)
        result = _gv_mod.find_decoupling_caps(ir)
        assert result == {"C1": "U1"}, (
            f"Expected power-only bypass cap C1 to anchor to active IC U1, got: {result}"
        )

    def test_charge_pump_power_output_caps_stay_out_of_decoupling_map(self) -> None:
        """Caps on IC power-output nets (e.g. MAX232 VS+/VS-) are not bypass decouplers."""
        components = [
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="TTL_TX"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="RS232_TX"),
            ComponentIR(ref="U2", symbol="Interface_UART:MAX232", value="MAX232"),
            ComponentIR(ref="C64", symbol="Device:C", value="1u"),
            ComponentIR(ref="C71", symbol="Device:C", value="1u"),
            ComponentIR(ref="C72", symbol="Device:C", value="1u"),
        ]
        nets = [
            NetIR(
                name="TTL_0_TX",
                pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="U2", pin="11")],
            ),
            NetIR(
                name="RS232_0_TX",
                pins=[PinRefIR(ref="U2", pin="14"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="+5V",
                pins=[
                    PinRefIR(ref="C64", pin="1"),
                    PinRefIR(ref="C71", pin="2"),
                    PinRefIR(ref="U2", pin="16"),
                ],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C64", pin="2"),
                    PinRefIR(ref="C72", pin="1"),
                    PinRefIR(ref="U2", pin="15"),
                ],
            ),
            NetIR(
                name="Net-(U2-VS+)",
                pins=[PinRefIR(ref="C71", pin="1"), PinRefIR(ref="U2", pin="2")],
            ),
            NetIR(
                name="Net-(U2-VS-)",
                pins=[PinRefIR(ref="C72", pin="2"), PinRefIR(ref="U2", pin="6")],
            ),
        ]

        ir = CircuitIR(version="1", components=components, nets=nets)

        assert _gv_mod.find_decoupling_caps(ir) == {"C64": "U2"}

        from kicad_pcb.layout import _find_decoupling_caps_layout  # noqa: PLC0415

        assert _find_decoupling_caps_layout(ir) == {"C64": "U2"}

    def test_shared_negative_rail_refinement_prefers_device_above_decoupler(self) -> None:
        """Shared negative rails should refine to the active device above the capacitor."""
        components = [
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="UpperActive"),
            ComponentIR(ref="U2", symbol="Amplifier_Operational:TL071", value="LowerActive"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="Signal1"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Signal2"),
        ]
        nets = [
            NetIR(
                name="VEE",
                pins=[
                    PinRefIR(ref="U1", pin="1"),
                    PinRefIR(ref="U2", pin="1"),
                    PinRefIR(ref="C1", pin="1"),
                ],
            ),
            NetIR(
                name="SIG_A",
                pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="J1", pin="1")],
            ),
            NetIR(
                name="SIG_B",
                pins=[PinRefIR(ref="U2", pin="2"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="J1", pin="2"),
                    PinRefIR(ref="J2", pin="2"),
                ],
            ),
        ]

        ir = CircuitIR(version="1", components=components, nets=nets)
        initial_map = _gv_mod.find_decoupling_caps(ir)
        assert initial_map == {"C1": "U2"}, (
            f"Expected stable pre-refinement anchor U2, got: {initial_map}"
        )

        refined_map = _gv_mod.refine_shared_rail_decoupling_map(
            ir,
            {
                "U1": (88.9, 30.48, 0.0),
                "U2": (96.52, 83.82, 0.0),
                "C1": (76.2, 76.2, 0.0),
                "J1": (30.48, 30.48, 0.0),
                "J2": (30.48, 83.82, 0.0),
            },
            initial_map,
        )
        assert refined_map == {"C1": "U1"}, (
            "Expected the shared negative-rail decoupler to refine to the upper active device, "
            f"got: {refined_map}"
        )

    def test_true_bypass_cap_stays_out_of_cluster_power_with_block_layout(self) -> None:
        """Block-classified decouplers should not be dumped into cluster_power."""
        components = [
            ComponentIR(ref="J1", symbol="Device:Conn", value="Input"),
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Device:IC", value="OpAmp"),
            ComponentIR(ref="J3", symbol="Connector:Conn_01x02", value="Power"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
        ]
        nets = [
            NetIR(
                name="IN_SIG",
                pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")],
            ),
            NetIR(
                name="OUT_SIG",
                pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="U1", pin="1")],
            ),
            NetIR(
                name="VCC",
                pins=[
                    PinRefIR(ref="J3", pin="1"),
                    PinRefIR(ref="U1", pin="2"),
                    PinRefIR(ref="C1", pin="1"),
                ],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="J3", pin="2"),
                    PinRefIR(ref="U1", pin="3"),
                    PinRefIR(ref="C1", pin="2"),
                ],
            ),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)

        block_layout = classify_circuit(ir)
        src = _gv_mod.build_dot_source(
            ir,
            block_layout=block_layout,
            sds_cols={"J1": 0, "R1": 1, "U1": 2, "J3": 0, "C1": 2},
        )

        cluster_start = src.index("cluster_power")
        cluster_end = src.index("}", cluster_start)
        cluster_body = src[cluster_start:cluster_end]

        assert "J3" in cluster_body
        assert "C1" not in cluster_body, (
            "True bypass decoupling cap should stay out of cluster_power when "
            "block classification marks it as DECOUPLING"
        )

    def test_cap_with_only_connector_neighbour_not_detected(self) -> None:
        """If only connectors share C1's signal net, no IC is associated → not detected."""
        components = [
            ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
            ComponentIR(ref="C1", symbol="Device:C", value="10n"),
        ]
        nets = [
            # VCC_EXT is not a power net by regex, so it's a signal net.
            NetIR(
                name="VCC_EXT",
                pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="C1", pin="1")],
            ),
            NetIR(name="GND", pins=[PinRefIR(ref="J1", pin="2"), PinRefIR(ref="C1", pin="2")]),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)
        result = _gv_mod.find_decoupling_caps(ir)
        assert result == {}, (
            f"Expected empty map when only connector shares signal net, got: {result}"
        )

    def test_non_capacitor_ref_not_detected(self) -> None:
        """R1 (resistor) with one signal pin and one power pin → NOT detected as decoupling."""
        components = [
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Device:IC", value="OpAmp"),
        ]
        nets = [
            NetIR(
                name="VBIAS",
                pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="U1", pin="1")],
            ),
            NetIR(name="GND", pins=[PinRefIR(ref="R1", pin="2")]),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)
        result = _gv_mod.find_decoupling_caps(ir)
        assert result == {}, f"Expected empty map for resistor (not a capacitor), got: {result}"


class TestDecouplingCapCoLocation:
    """Integration tests for decoupling cap co-location in DOT source and post-snap."""

    def test_invisible_edge_in_dot_source(self) -> None:
        """DOT source must contain an invisible edge from the decoupling cap to its IC."""
        ir = _decoupling_ir()
        decoupling_map = _gv_mod.find_decoupling_caps(ir)
        assert decoupling_map, "pre-condition: decoupling_map should not be empty"

        src = _gv_mod.build_dot_source(ir, decoupling_map=decoupling_map)
        assert "C1 -> U1 [style=invis, weight=10, constraint=false];" in src, (
            f"invisible edge C1->U1 missing from DOT source.\nFull source:\n{src}"
        )

    def test_rank_same_subgraph_for_decoupling_pair(self) -> None:
        """DOT source must contain a rank=same subgraph grouping the IC and its bypass cap."""
        ir = _decoupling_ir()
        decoupling_map = _gv_mod.find_decoupling_caps(ir)
        src = _gv_mod.build_dot_source(ir, decoupling_map=decoupling_map)
        # Look for the rank=same block that contains both U1 and C1.
        assert "rank=same" in src, "rank=same directive missing"
        # The pair U1 + C1 must appear inside a rank=same block.
        lines = src.splitlines()
        in_same = False
        found_u1 = found_c1 = False
        for line in lines:
            stripped = line.strip()
            if stripped == "rank=same;":
                in_same = True
                found_u1 = found_c1 = False
            elif stripped == "}" and in_same:
                if found_u1 and found_c1:
                    break
                in_same = False
            elif in_same:
                if stripped == "U1;":
                    found_u1 = True
                if stripped == "C1;":
                    found_c1 = True
        assert found_u1 and found_c1, (
            "No rank=same subgraph containing both U1 and C1 found in DOT source.\n"
            f"Full source:\n{src}"
        )

    def test_rank_same_subgraph_skips_cross_tier_decoupling_pair(self) -> None:
        """Explicit tier constraints should not be contradicted by decoupling rank=same blocks."""
        ir = _decoupling_ir()
        decoupling_map = _gv_mod.find_decoupling_caps(ir)
        src = _gv_mod.build_dot_source(
            ir,
            decoupling_map=decoupling_map,
            tiers={"J1": 0, "R1": 1, "U1": 2, "C1": 0},
        )

        assert "C1 -> U1 [style=invis, weight=10, constraint=false];" in src
        assert "{\n    rank=same;\n    U1;\n    C1;\n  }" not in src

    def test_post_snap_sets_cap_x_equal_to_ic_x(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After compute_symbol_positions, decoupling cap x must equal its IC's x."""
        ir = _decoupling_ir()

        # Fake dot output: C1 at a different column than U1.
        # Keys are safe_ids (same as refs for these component names).
        fake_positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 50.80, None),
            "R1": (55.0, 50.80, None),
            "U1": (80.0, 60.0, None),
            "C1": (35.0, 40.0, None),  # different x than U1 initially
        }

        def fake_run_dot(
            self_engine: object, dot_source: str
        ) -> dict[str, tuple[float, float, float | None]]:
            return fake_positions

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")
        result = engine.compute_symbol_positions(ir)

        u1_x = result["U1"][0]
        c1_x = result["C1"][0]
        assert c1_x == pytest.approx(u1_x), (
            f"C1.x ({c1_x}) should equal U1.x ({u1_x}) after decoupling-cap snap"
        )

    def test_post_snap_sets_cap_y_above_ic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After compute_symbol_positions, decoupling cap y = IC.y - GRID_ROW_MM."""
        ir = _decoupling_ir()

        fake_positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 50.80, None),
            "R1": (55.0, 50.80, None),
            "U1": (80.0, 60.0, None),
            "C1": (35.0, 40.0, None),
        }

        def fake_run_dot(
            self_engine: object, dot_source: str
        ) -> dict[str, tuple[float, float, float | None]]:
            return fake_positions

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")
        result = engine.compute_symbol_positions(ir)

        u1_y = result["U1"][1]
        c1_y = result["C1"][1]
        expected_y = u1_y - _gv_mod.GRID_ROW_MM
        assert c1_y == pytest.approx(expected_y), (
            f"C1.y ({c1_y}) should be U1.y - GRID_ROW_MM ({expected_y}), but got {c1_y}"
        )

    def test_post_snap_keeps_mixed_polarity_decoupling_bank_compact(self) -> None:
        """Mixed-polarity decouplers should stay in a compact symmetric bank around the IC lane."""
        positions = {
            "U1": (100.0, 100.0, None),
            "C1": (40.0, 40.0, None),
            "C2": (45.0, 45.0, None),
            "C3": (50.0, 50.0, None),
            "C4": (55.0, 55.0, None),
            "C5": (60.0, 60.0, None),
            "C6": (65.0, 65.0, None),
            "C7": (70.0, 70.0, None),
            "C8": (75.0, 75.0, None),
        }

        result = _gv_mod.post_snap_decoupling_caps(
            positions,
            {
                "C1": "U1",
                "C2": "U1",
                "C3": "U1",
                "C4": "U1",
                "C5": "U1",
                "C6": "U1",
                "C7": "U1",
                "C8": "U1",
            },
            rail_polarities={
                "C1": "positive",
                "C2": "positive",
                "C3": "positive",
                "C4": "positive",
                "C5": "negative",
                "C6": "negative",
                "C7": "negative",
                "C8": "negative",
            },
        )

        ux, uy, _ = result["U1"]
        positive_refs = ("C1", "C2", "C3", "C4")
        negative_refs = ("C5", "C6", "C7", "C8")
        expected_xs = {
            round(ux, 2),
            round(ux - GRID_COL_MM, 2),
            round(ux + GRID_COL_MM, 2),
        }

        positive_xs = {round(result[ref][0], 2) for ref in positive_refs}
        negative_xs = {round(result[ref][0], 2) for ref in negative_refs}

        assert positive_xs == expected_xs, (
            f"Positive overflow bank should use compact symmetric x lanes: {result}"
        )
        assert negative_xs == expected_xs, (
            f"Negative overflow bank should mirror the same compact x lanes: {result}"
        )

    def test_post_snap_adds_extra_clearance_for_second_same_lane_decoupler(self) -> None:
        """Second same-column decouplers need extra y clearance to avoid pin-stub overlap."""
        positions = {
            "U1": (100.0, 100.0, None),
            "C1": (40.0, 40.0, None),
            "C2": (45.0, 45.0, None),
        }

        result = _gv_mod.post_snap_decoupling_caps(
            positions,
            {
                "C1": "U1",
                "C2": "U1",
            },
            rail_polarities={
                "C1": "positive",
                "C2": "positive",
            },
        )

        assert result["C1"][0] == pytest.approx(100.0)
        assert result["C1"][1] == pytest.approx(100.0 - _gv_mod.GRID_ROW_MM)
        assert result["C1"][2] is None
        assert result["C2"][0] == pytest.approx(100.0)
        assert result["C2"][1] == pytest.approx(100.0 - (4 * _gv_mod.GRID_ROW_MM))
        assert result["C2"][2] is None

    def test_post_snap_centers_split_unit_decoupling_bank_on_family_x(self) -> None:
        """Split-unit decouplers should align to the visible device family, not one sibling lane."""
        positions = {
            "U1A": (91.44, 99.06, None),
            "U1B": (121.92, 99.06, None),
            "U1P": (106.68, 129.54, None),
            "C1": (40.0, 40.0, None),
            "C2": (45.0, 45.0, None),
            "C3": (50.0, 50.0, None),
            "C4": (55.0, 55.0, None),
        }

        result = _gv_mod.post_snap_decoupling_caps(
            positions,
            {
                "C1": "U1A",
                "C2": "U1A",
                "C3": "U1A",
                "C4": "U1A",
            },
            rail_polarities={
                "C1": "positive",
                "C2": "negative",
                "C3": "positive",
                "C4": "negative",
            },
        )

        family_center_x = round((positions["U1A"][0] + positions["U1B"][0]) / 2.0, 2)
        decoupling_xs = {round(result[ref][0], 2) for ref in ("C1", "C2", "C3", "C4")}
        positive_refs = ("C1", "C3")
        negative_refs = ("C2", "C4")
        uy = positions["U1A"][1]

        assert family_center_x in decoupling_xs, (
            "Split-unit decoupling bank should keep its primary lane on the family centerline: "
            f"family_center_x={family_center_x}, decoupling_xs={sorted(decoupling_xs)}"
        )
        assert round(positions["U1A"][0], 2) not in decoupling_xs, (
            "Split-unit decoupling bank should not stay pinned to only the refined anchor lane: "
            f"anchor_x={positions['U1A'][0]:.2f}, decoupling_xs={sorted(decoupling_xs)}"
        )
        assert all(result[ref][1] < uy for ref in positive_refs), (
            f"Positive decouplers should stay above the IC row: {result}"
        )
        assert all(result[ref][1] > uy for ref in negative_refs), (
            f"Negative decouplers should stay below the IC row: {result}"
        )
        assert max(result[ref][1] for ref in positive_refs) < min(
            result[ref][1] for ref in negative_refs
        ), f"Mixed-polarity banks should stay vertically separated around the IC: {result}"

    def test_compute_symbol_positions_refines_shared_negative_rail_anchor(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The engine should re-anchor ambiguous negative-rail decouplers after raw layout."""
        components = [
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="UpperActive"),
            ComponentIR(ref="U2", symbol="Amplifier_Operational:TL071", value="LowerActive"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="Signal1"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Signal2"),
        ]
        nets = [
            NetIR(
                name="VEE",
                pins=[
                    PinRefIR(ref="U1", pin="1"),
                    PinRefIR(ref="U2", pin="1"),
                    PinRefIR(ref="C1", pin="1"),
                ],
            ),
            NetIR(
                name="SIG_A",
                pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="J1", pin="1")],
            ),
            NetIR(
                name="SIG_B",
                pins=[PinRefIR(ref="U2", pin="2"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="J1", pin="2"),
                    PinRefIR(ref="J2", pin="2"),
                ],
            ),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)

        def fake_run_dot(
            self_engine: object, dot_source: str
        ) -> dict[str, tuple[float, float, float | None]]:
            return {
                _gv_mod._safe_id("U1"): (88.9, 30.48, 0.0),
                _gv_mod._safe_id("U2"): (96.52, 83.82, 0.0),
                _gv_mod._safe_id("C1"): (76.2, 76.2, 0.0),
                _gv_mod._safe_id("J1"): (30.48, 30.48, 0.0),
                _gv_mod._safe_id("J2"): (30.48, 83.82, 0.0),
            }

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")

        result = engine.compute_symbol_positions(ir)
        assert round(result["C1"][0], 2) == round(result["U1"][0], 2), (
            f"Expected C1 to re-anchor to U1 after shared negative-rail refinement, got: {result}"
        )

    def test_shared_negative_rail_refinement_uses_signal_siblings_when_only_power_unit_touches_rail(
        self,
    ) -> None:
        """Shared rails should refine through sibling signal units when the rail only hits U1P."""
        components = [
            ComponentIR(ref="U1A", symbol="Amplifier_Operational:TL071", value="UpperActive"),
            ComponentIR(ref="U1B", symbol="Amplifier_Operational:TL071", value="LowerActive"),
            ComponentIR(ref="U1P", symbol="Amplifier_Operational:TL071", value="PowerUnit"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="Signal1"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Signal2"),
        ]
        nets = [
            NetIR(
                name="VEE",
                pins=[PinRefIR(ref="U1P", pin="1"), PinRefIR(ref="C1", pin="1")],
            ),
            NetIR(
                name="SIG_A",
                pins=[PinRefIR(ref="U1A", pin="2"), PinRefIR(ref="J1", pin="1")],
            ),
            NetIR(
                name="SIG_B",
                pins=[PinRefIR(ref="U1B", pin="2"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="J1", pin="2"),
                    PinRefIR(ref="J2", pin="2"),
                    PinRefIR(ref="U1P", pin="2"),
                ],
            ),
        ]

        ir = CircuitIR(version="1", components=components, nets=nets)
        refined_map = _gv_mod.refine_shared_rail_decoupling_map(
            ir,
            {
                "U1A": (88.9, 30.48, 0.0),
                "U1B": (96.52, 83.82, 0.0),
                "U1P": (92.71, 57.15, 0.0),
                "C1": (76.2, 76.2, 0.0),
                "J1": (30.48, 30.48, 0.0),
                "J2": (30.48, 83.82, 0.0),
            },
            {"C1": "U1B"},
        )
        assert refined_map == {"C1": "U1A"}, (
            "Expected sibling signal units to participate in shared negative-rail refinement "
            f"when only the power unit touches the rail, got: {refined_map}"
        )

    def test_compute_symbol_positions_refines_shared_negative_rail_anchor_through_power_unit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The engine should refine split-unit shared rails even when only U1P is on the rail."""
        components = [
            ComponentIR(ref="U1A", symbol="Amplifier_Operational:TL071", value="UpperActive"),
            ComponentIR(ref="U1B", symbol="Amplifier_Operational:TL071", value="LowerActive"),
            ComponentIR(ref="U1P", symbol="Amplifier_Operational:TL071", value="PowerUnit"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="Signal1"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Signal2"),
        ]
        nets = [
            NetIR(
                name="VEE",
                pins=[PinRefIR(ref="U1P", pin="1"), PinRefIR(ref="C1", pin="1")],
            ),
            NetIR(
                name="SIG_A",
                pins=[PinRefIR(ref="U1A", pin="2"), PinRefIR(ref="J1", pin="1")],
            ),
            NetIR(
                name="SIG_B",
                pins=[PinRefIR(ref="U1B", pin="2"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="J1", pin="2"),
                    PinRefIR(ref="J2", pin="2"),
                    PinRefIR(ref="U1P", pin="2"),
                ],
            ),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)

        def fake_run_dot(
            self_engine: object, dot_source: str
        ) -> dict[str, tuple[float, float, float | None]]:
            return {
                _gv_mod._safe_id("U1A"): (88.9, 30.48, 0.0),
                _gv_mod._safe_id("U1B"): (96.52, 83.82, 0.0),
                _gv_mod._safe_id("U1P"): (92.71, 57.15, 0.0),
                _gv_mod._safe_id("C1"): (76.2, 76.2, 0.0),
                _gv_mod._safe_id("J1"): (30.48, 30.48, 0.0),
                _gv_mod._safe_id("J2"): (30.48, 83.82, 0.0),
            }

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")

        result = engine.compute_symbol_positions(ir)
        family_center_x = round((result["U1A"][0] + result["U1B"][0]) / 2.0, 2)
        assert round(result["C1"][0], 2) == family_center_x, (
            "Expected the split-unit shared-rail decoupler to stay centered on the final "
            f"signal-family span after sibling refinement, got: {result}"
        )
        assert round(result["C1"][0], 2) == round(result["U1P"][0], 2), (
            "Expected the split-unit shared-rail decoupler to follow the recentered power unit "
            f"over the sibling signal span, got: {result}"
        )

    def test_dot_source_unchanged_without_decoupling_map(self) -> None:
        """build_dot_source without decoupling_map must not co-locate C1 via invisible edge."""
        ir = _decoupling_ir()
        src = _gv_mod.build_dot_source(ir)  # no decoupling_map kwarg
        # Structural tier-anchor invisible elements are always present; the test
        # ensures that no invisible *co-location* edge is added for C1 when no
        # decoupling_map is supplied.
        invis_lines = [ln for ln in src.splitlines() if "style=invis" in ln and "C1" in ln]
        assert not invis_lines, (
            "C1 should not have invisible co-location edges without decoupling_map; "
            f"found: {invis_lines}"
        )


# ---------------------------------------------------------------------------
# Phase 2 — Vertical grouping / affinity clustering (Rule §3)
# ---------------------------------------------------------------------------


def _affinity_ir() -> CircuitIR:
    """Return an IR suitable for testing affinity grouping.

    Topology (4 components, 3 tiers):
    * Tier 0: J1 (connector seed)
    * Tier 1: R1, R2
    * Tier 2: U1

    Nets:
    * IN   : J1 pin1 ← → R1 pin1  (J1 and R1 share IN)
    * STAGE: R1 pin2 ← → R2 pin1 ← → U1 pin1 (R1, R2, U1 share STAGE)
    * BIAS : R2 pin2 ← → U1 pin2  (R2 and U1 share BIAS; so R2+U1 share 2 nets)
    * GND  : J1 pin2 → power only
    """
    components = [
        ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
        ComponentIR(ref="R1", symbol="Device:R", value="10k"),
        ComponentIR(ref="R2", symbol="Device:R", value="47k"),
        ComponentIR(ref="U1", symbol="Device:IC", value="OpAmp"),
    ]
    nets = [
        NetIR(name="IN", pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")]),
        NetIR(
            name="STAGE",
            pins=[
                PinRefIR(ref="R1", pin="2"),
                PinRefIR(ref="R2", pin="1"),
                PinRefIR(ref="U1", pin="1"),
            ],
        ),
        NetIR(name="BIAS", pins=[PinRefIR(ref="R2", pin="2"), PinRefIR(ref="U1", pin="2")]),
        NetIR(
            name="GND",
            pins=[PinRefIR(ref="J1", pin="2"), PinRefIR(ref="R1", pin="2")],
        ),
    ]
    return CircuitIR(version="1", components=components, nets=nets)
