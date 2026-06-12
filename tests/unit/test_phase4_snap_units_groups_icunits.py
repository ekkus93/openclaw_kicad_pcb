"""Phase 4 snap-units tests — IC unit groups, stereo channels, stereo split."""

from __future__ import annotations

import re

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.tier import (
    IcUnitGroup,
    assign_ic_units_to_tiers,
    assign_tiers,
    build_ic_unit_sibling_constraints,
)


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


def _multi_unit_ir() -> CircuitIR:
    """Minimal dual-op-amp circuit with a multi-unit IC.

    Signal path: J1 --[NET_IN]--> U1A --[NET_OUT]--> J2.
    Power unit: U1B connected only to VCC and GND.
    """
    return CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "J1", "symbol": "Connector:Conn_01x01", "value": ""},
                {"ref": "U1A", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "U1B", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "J2", "symbol": "Connector:Conn_01x01", "value": ""},
            ],
            "nets": [
                {"name": "NET_IN", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "U1A", "pin": "3"}]},
                {
                    "name": "NET_OUT",
                    "pins": [{"ref": "U1A", "pin": "1"}, {"ref": "J2", "pin": "1"}],
                },
                {"name": "VCC", "pins": [{"ref": "U1B", "pin": "8"}]},
                {"name": "GND", "pins": [{"ref": "U1B", "pin": "4"}]},
            ],
        }
    )


def _multi_stage_unit_ir() -> CircuitIR:
    """Three-unit op-amp example with two signal stages and one power unit."""
    return CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "J1", "symbol": "Connector:Conn_01x01", "value": ""},
                {"ref": "U1A", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "U1B", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "U1P", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "J2", "symbol": "Connector:Conn_01x01", "value": ""},
            ],
            "nets": [
                {"name": "NET_IN", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "U1A", "pin": "3"}]},
                {
                    "name": "NET_STAGE",
                    "pins": [{"ref": "U1A", "pin": "1"}, {"ref": "U1B", "pin": "5"}],
                },
                {
                    "name": "NET_OUT",
                    "pins": [{"ref": "U1B", "pin": "7"}, {"ref": "J2", "pin": "1"}],
                },
                {"name": "VCC", "pins": [{"ref": "U1P", "pin": "8"}]},
                {"name": "GND", "pins": [{"ref": "U1P", "pin": "4"}]},
            ],
        }
    )


class TestIcUnitGroups:
    """Phase 6 — assign_ic_units_to_tiers and per-unit DOT placement."""

    def test_multi_unit_ref_detected(self) -> None:
        """U1A and U1B are grouped under base ref U1."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        assert "U1" in groups
        assert groups["U1"].units == ["U1A", "U1B"]
        assert groups["U1"].base_ref == "U1"

    def test_power_unit_detected(self) -> None:
        """U1B (only VCC/GND nets) is identified as the power unit."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        assert groups["U1"].power_unit == "U1B"

    def test_single_unit_ic_excluded(self) -> None:
        """A plain 'U1' ref (no letter suffix) produces no group entry."""
        ir = _make_ir(
            [("J1", "Connector"), ("U1", "Amp:TL071"), ("J2", "Connector")],
            [("NET_IN", [("J1", "1"), ("U1", "3")]), ("NET_OUT", [("U1", "1"), ("J2", "1")])],
        )
        groups = assign_ic_units_to_tiers(ir, assign_tiers(ir))
        assert groups == {}

    def test_passive_ref_with_letter_suffix_excluded(self) -> None:
        """R1A is a passive prefix; it must not be treated as a multi-unit IC."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1A", "Device:R"), ("J2", "Connector")],
            [("NET", [("J1", "1"), ("R1A", "1"), ("J2", "1")])],
        )
        groups = assign_ic_units_to_tiers(ir, assign_tiers(ir))
        assert groups == {}

    def test_ic_unit_group_dataclass_defaults(self) -> None:
        """IcUnitGroup default values are correct."""
        g = IcUnitGroup(base_ref="U2")
        assert g.units == []
        assert g.power_unit is None

    def test_multi_unit_ic_power_unit_in_power_cluster(self) -> None:  # spec test
        """DOT source places U1B (power unit) inside cluster_power subgraph."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}
        dot = _gv_mod._build_dot_source(ir, power_unit_refs=power_unit_refs)
        # cluster_power must exist and contain U1B.
        assert "cluster_power" in dot
        cluster_start = dot.index("cluster_power")
        cluster_end = dot.index("}", cluster_start)
        cluster_body = dot[cluster_start:cluster_end]
        assert "U1B" in cluster_body

    def test_multi_unit_ic_signal_units_in_signal_tiers(self) -> None:  # spec test
        """DOT source places U1A (signal unit) in a rank=same tier subgraph."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}
        dot = _gv_mod._build_dot_source(ir, power_unit_refs=power_unit_refs)
        # U1A must appear in a rank=... subgraph (rank=source, rank=same, or rank=sink).
        rank_blocks = re.findall(r"\{[^{}]*rank=(?:same|source|sink)[^{}]*\}", dot, re.DOTALL)
        assert any("U1A" in block for block in rank_blocks), (
            f"U1A not found in any rank subgraph.\nDOT:\n{dot}"
        )

    def test_power_unit_not_in_signal_tiers(self) -> None:
        """U1B must not appear in any rank=same/source/sink subgraph."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}
        dot = _gv_mod._build_dot_source(ir, power_unit_refs=power_unit_refs)
        rank_blocks = re.findall(r"\{[^{}]*rank=(?:same|source|sink)[^{}]*\}", dot, re.DOTALL)
        assert not any("U1B" in block for block in rank_blocks), (
            f"U1B must not be in a tier subgraph.\nDOT:\n{dot}"
        )

    def test_power_unit_excluded_from_tiers_even_with_affinity_order(self) -> None:
        """Affinity ordering must not reinsert power units into rank subgraphs."""
        ir = _multi_stage_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}

        dot = _gv_mod._build_dot_source(
            ir,
            power_unit_refs=power_unit_refs,
            tiers=tiers,
            affinity_order={0: ["J1", "U1P"], 1: ["U1A", "U1B"]},
        )

        rank_blocks = re.findall(r"\{[^{}]*rank=(?:same|source|sink)[^{}]*\}", dot, re.DOTALL)
        assert not any("U1P" in block for block in rank_blocks), (
            f"U1P leaked back into a tier subgraph via affinity ordering.\nDOT:\n{dot}"
        )

    def test_signal_sibling_constraints_skip_power_units(self) -> None:
        """Only signal units participate in sibling-order constraints."""
        ir = _multi_stage_unit_ir()
        groups = assign_ic_units_to_tiers(ir, assign_tiers(ir))

        assert groups["U1"].signal_units == ["U1A", "U1B"]
        assert build_ic_unit_sibling_constraints(groups) == [("U1A", "U1B")]

    def test_signal_sibling_constraint_emitted_in_dot(self) -> None:
        """DOT source adds an invisible U1A->U1B constraint but excludes U1P."""
        ir = _multi_stage_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}
        sibling_pairs = build_ic_unit_sibling_constraints(groups)

        dot = _gv_mod._build_dot_source(
            ir,
            power_unit_refs=power_unit_refs,
            unit_sibling_pairs=sibling_pairs,
            tiers=tiers,
        )

        assert "U1A -> U1B [style=invis, weight=4, constraint=false];" in dot
        assert "U1P -> U1A" not in dot
        assert "U1A -> U1P" not in dot
        assert "U1P -> U1B" not in dot
        assert "U1B -> U1P" not in dot


# ---------------------------------------------------------------------------
# Phase 7 — Stereo symmetry
# ---------------------------------------------------------------------------
