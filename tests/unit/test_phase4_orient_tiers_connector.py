"""Phase 4: connector orientations, BFS tier assignment, and dot source signal flow."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    compute_orientations,
)

pytestmark = pytest.mark.unit


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


class TestConnectorOrientations:
    """compute_orientations uses tier info to set connector direction.

    Input connectors (tier 0) get 0° so their pins point right into the
    circuit.  Output connectors (max tier) get 180° so their pins point
    left, back toward the circuit.  Intermediate connectors (if any)
    default to 0°.  When no tiers dict is passed the old behaviour
    (always 0°) is preserved for backward compatibility.
    """

    # ------------------------------------------------------------------
    # Shared fixture: J1 → R1 → U1 → J2 linear chain
    # ------------------------------------------------------------------

    @staticmethod
    def _chain_ir_4() -> CircuitIR:
        """J1 — NET0 — R1 — NET1 — U1 — NET2 — J2."""
        return _make_ir(
            [
                ("J1", "Connector_Generic:Conn_01x02"),
                ("R1", "Device:R"),
                ("U1", "Amplifier_Operational:TL071"),
                ("J2", "Connector_Generic:Conn_01x02"),
            ],
            [
                ("NET0", [("J1", "1"), ("R1", "1")]),
                ("NET1", [("R1", "2"), ("U1", "3")]),
                ("NET2", [("U1", "6"), ("J2", "1")]),
            ],
        )

    @staticmethod
    def _tiers_4() -> dict[str, int]:
        """Tier map for the 4-component chain: J1=0, R1=1, U1=2, J2=3."""
        return {"J1": 0, "R1": 1, "U1": 2, "J2": 3}

    @staticmethod
    def _positions_4() -> dict[str, tuple[float, float]]:
        return {"J1": (0.0, 30.0), "R1": (30.0, 30.0), "U1": (60.0, 30.0), "J2": (90.0, 30.0)}

    # ------------------------------------------------------------------

    def test_input_connector_orientation_is_0(self) -> None:
        """Input connector at tier 0 must be 0° (pins point right)."""
        ir = self._chain_ir_4()
        result = compute_orientations(ir, self._positions_4(), tiers=self._tiers_4())
        assert result["J1"] == 0, f"Input connector J1 (tier 0) should be 0°, got {result['J1']}"

    def test_output_connector_orientation_is_180(self) -> None:
        """Output connector at max tier must be 180° (pins point left)."""
        ir = self._chain_ir_4()
        result = compute_orientations(ir, self._positions_4(), tiers=self._tiers_4())
        assert result["J2"] == 180, (
            f"Output connector J2 (tier 3 = max) should be 180°, got {result['J2']}"
        )

    def test_non_connector_components_unaffected_by_tiers(self) -> None:
        """Passing tiers must not change orientation of non-connector components."""
        ir = self._chain_ir_4()
        result_no_tiers = compute_orientations(ir, self._positions_4())
        result_with_tiers = compute_orientations(ir, self._positions_4(), tiers=self._tiers_4())
        for ref in ("R1", "U1"):
            assert result_no_tiers[ref] == result_with_tiers[ref], (
                f"{ref} orientation changed when tiers were added: "
                f"{result_no_tiers[ref]} → {result_with_tiers[ref]}"
            )

    def test_connector_without_tiers_defaults_to_zero(self) -> None:
        """When tiers=None (backward compat), all connectors are 0°."""
        ir = self._chain_ir_4()
        result = compute_orientations(ir, self._positions_4(), tiers=None)
        assert result["J1"] == 0, "J1 should be 0° when no tiers provided"
        assert result["J2"] == 0, "J2 should be 0° without tiers (no 180° flip)"

    def test_single_connector_circuit_stays_zero(self) -> None:
        """A circuit with only one connector (max_tier == 0) keeps 0°.

        When all connectors are at tier 0 and max_tier is 0 the output
        connector guard ``_max_tier > 0`` prevents a false 180° assignment.
        """
        ir = _make_ir(
            [("J1", "Connector_Generic:Conn_01x01"), ("R1", "Device:R")],
            [("NET", [("J1", "1"), ("R1", "1")])],
        )
        tiers = {"J1": 0, "R1": 0}
        result = compute_orientations(ir, {"J1": (0.0, 0.0), "R1": (30.0, 0.0)}, tiers=tiers)
        assert result["J1"] == 0, "Single-tier connector should never be 180°"

    def test_diode_always_zero(self) -> None:
        """Diode D* is always 0° (anode left, cathode right)."""
        ir = _make_ir(
            [("D1", "Device:D"), ("J1", "Connector_Generic:Conn_01x01")],
            [("SIGNAL", [("D1", "A"), ("J1", "1")])],
        )
        tiers = {"D1": 1, "J1": 0}
        positions = {"D1": (30.0, 30.0), "J1": (0.0, 30.0)}
        result = compute_orientations(ir, positions, tiers=tiers)
        assert result["D1"] == 0, f"Diode D1 should always be 0°, got {result['D1']}"

    def test_diode_zero_regardless_of_tiers(self) -> None:
        """Diode orientation is 0° with or without a tiers dict."""
        ir = _make_ir(
            [("D2", "Device:D_Schottky")],
            [("ANODE", [("D2", "A")])],
        )
        pos = {"D2": (30.0, 30.0)}
        assert compute_orientations(ir, pos)["D2"] == 0
        assert compute_orientations(ir, pos, tiers={"D2": 2})["D2"] == 0


# ---------------------------------------------------------------------------
# Phase 0 — BFS tier assignment + directional DOT source (regression 0.3)
# ---------------------------------------------------------------------------


def _chain_ir(refs: list[str], net_names: list[str] | None = None) -> CircuitIR:
    """Build a linear chain IR: refs[0] — N0 — refs[1] — N1 — ... — refs[-1].

    Each successive pair of refs shares a signal net.  ``net_names`` may
    override the auto-generated ``NET0``, ``NET1``, … names.
    """
    if net_names is None:
        net_names = [f"NET{i}" for i in range(len(refs) - 1)]
    components = [ComponentIR(ref=r, symbol="Lib:sym", value="x") for r in refs]
    nets = [
        NetIR(
            name=net_names[i],
            pins=[PinRefIR(ref=refs[i], pin="1"), PinRefIR(ref=refs[i + 1], pin="2")],
        )
        for i in range(len(refs) - 1)
    ]
    return CircuitIR(version="1", components=components, nets=nets)


class TestAssignBfsTiers:
    """Unit tests for the BFS tier-assignment helper."""

    def test_linear_chain_connector_to_connector(self) -> None:
        """J1 → R1 → U1 → J2 should yield ascending tiers 0,1,2,3."""
        refs = ["J1", "R1", "U1", "J2"]
        ir = _chain_ir(refs)
        signal_nets = [n for n in ir.nets if len(n.pins) >= 2]
        tiers = _gv_mod.assign_bfs_tiers(refs, signal_nets)
        # J1 (connector, seed first) must be before R1 before U1 before J2.
        assert tiers["J1"] < tiers["R1"] < tiers["U1"] < tiers["J2"]

    def test_single_component_gets_tier_zero(self) -> None:
        refs = ["R1"]
        tiers = _gv_mod.assign_bfs_tiers(refs, [])
        assert tiers["R1"] == 0

    def test_isolated_component_defaults_to_zero(self) -> None:
        """A component with no signal-net connections gets tier 0."""
        refs = ["J1", "R_isolated"]
        ir = _chain_ir(["J1", "R1"])  # R_isolated not in ir nets
        signal_nets = [n for n in ir.nets if len(n.pins) >= 2]
        tiers = _gv_mod.assign_bfs_tiers(refs, signal_nets)
        assert tiers.get("R_isolated", 0) == 0

    def test_no_connectors_all_refs_reachable(self) -> None:
        """When there are no connectors, BFS starts from all refs; all are assigned."""
        refs = ["R1", "R2", "R3"]
        ir = _chain_ir(refs)
        signal_nets = [n for n in ir.nets if len(n.pins) >= 2]
        tiers = _gv_mod.assign_bfs_tiers(refs, signal_nets)
        assert set(tiers) == set(refs)

    def test_output_connector_gets_higher_tier_than_ic(self) -> None:
        """J_IN → R1 → U1 → J_OUT: J_OUT tier must exceed U1 tier."""
        refs = ["J_IN", "R1", "U1", "J_OUT"]
        ir = _chain_ir(refs)
        signal_nets = [n for n in ir.nets if len(n.pins) >= 2]
        tiers = _gv_mod.assign_bfs_tiers(refs, signal_nets)
        assert tiers["J_OUT"] > tiers["U1"]


class TestBuildDotSourceSignalFlow:
    """Regression tests for the fixed _build_dot_source (Phase 0)."""

    def _dot(self, ir: CircuitIR) -> str:
        return _gv_mod.build_dot_source(ir)

    def test_has_directional_net_hub_edges(self) -> None:
        """DOT source for a J1→R1 chain has upstream→net AND net→downstream edges."""
        ir = _chain_ir(["J1", "R1"])
        src = self._dot(ir)
        # J1 is the alphabetically-first connector (seed tier 0).
        # R1 is tier 1.  Expected: J1 -> net_NET0; net_NET0 -> R1
        assert "J1 -> net_NET0" in src or "J1->net_NET0" in src
        assert "net_NET0 -> R1" in src or "net_NET0->R1" in src

    def test_no_edges_are_all_into_net_nodes(self) -> None:
        """In the old bipartite model every edge was comp→net with no return edges.
        After the fix, at least one net_* node must have an outgoing edge to a comp.
        """
        ir = _chain_ir(["J1", "R1", "U1", "J2"])
        src = self._dot(ir)
        # Find lines where a net_* node is the *source* of an edge.
        net_source_lines = [line for line in src.splitlines() if line.strip().startswith("net_")]
        assert net_source_lines, "No net→component edges found; old bipartite model still in use"

    def test_rank_source_subgraph_present_for_input_connector(self) -> None:
        """The first-tier group must use rank=source."""
        ir = _chain_ir(["J1", "R1", "U1", "J2"])
        src = self._dot(ir)
        assert "rank=source" in src

    def test_rank_sink_subgraph_present_for_output_connector(self) -> None:
        """The last-tier group must use rank=sink."""
        ir = _chain_ir(["J1", "R1", "U1", "J2"])
        src = self._dot(ir)
        assert "rank=sink" in src

    def test_power_only_components_in_cluster_power(self) -> None:
        """Components connected only via power nets must appear in cluster_power."""
        components = [
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
        ]
        nets = [
            NetIR(name="VCC", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="C1", pin="1")]),
            NetIR(name="GND", pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="C1", pin="2")]),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)
        src = self._dot(ir)
        assert "cluster_power" in src
        assert "rank=max" in src

    def test_tier_separation_via_rank_same_subgraphs(self) -> None:
        """For a 4-component chain, at least 3 separate rank subgraphs are emitted."""
        ir = _chain_ir(["J1", "R1", "U1", "J2"])
        src = self._dot(ir)
        rank_lines = [ln for ln in src.splitlines() if "rank=" in ln]
        # Expect at least rank=source, one rank=same (U1 or R1), rank=sink
        assert len(rank_lines) >= 3, (
            f"Expected ≥3 rank= lines for a 4-component chain, got {len(rank_lines)}: "
            + repr(rank_lines)
        )

    def test_ranksep_is_increased(self) -> None:
        """ranksep must be at least 1.5 to give adequate tier spacing."""
        ir = _chain_ir(["J1", "R1"])
        src = self._dot(ir)
        # e.g. "  ranksep=1.5;"
        ranksep_lines = [ln for ln in src.splitlines() if "ranksep" in ln]
        assert ranksep_lines, "ranksep directive missing from DOT source"
        val_str = ranksep_lines[0].split("=")[1].strip().rstrip(";")
        assert float(val_str) >= 1.5, f"ranksep too small: {val_str}"

    def test_build_dot_source_with_affinity_order_uses_specified_order(self) -> None:
        """affinity_order overrides alphabetical ordering within rank=same blocks."""
        # Three-tier circuit: J1 (tier 0, connector) → A_R and Z_R (tier 1,
        # rank=same) → J2 (tier 2, connector).  Reverse-alphabetical affinity
        # order for tier 1 should put Z_R before A_R in the DOT output.
        ir = _two_same_tier_ir()
        affinity_order = {1: ["Z_R", "A_R"]}
        src = _gv_mod.build_dot_source(ir, affinity_order=affinity_order)
        same_block_refs = _extract_rank_same_refs(src)
        assert same_block_refs == [
            "Z_R",
            "A_R",
        ], f"Expected Z_R before A_R with affinity_order; got {same_block_refs}"

    def test_build_dot_source_without_affinity_order_emits_alphabetical(self) -> None:
        """Without affinity_order the fallback sorts refs alphabetically per tier."""
        ir = _two_same_tier_ir()
        src = _gv_mod.build_dot_source(ir)
        same_block_refs = _extract_rank_same_refs(src)
        assert same_block_refs == [
            "A_R",
            "Z_R",
        ], f"Expected alphabetical A_R, Z_R without affinity_order; got {same_block_refs}"


# ---------------------------------------------------------------------------
# Helpers for TestBuildDotSourceSignalFlow (affinity_order tests)
# ---------------------------------------------------------------------------


def _two_same_tier_ir() -> CircuitIR:
    """Three-tier circuit; A_R and Z_R are both in tier 1 (rank=same).

    Topology:
      J1 → NET_A → A_R ─┐
      J1 → NET_Z → Z_R ─┴─ NET_OUT → J2
    """
    components = [
        ComponentIR(ref="J1", symbol="Device:Conn", value="Input"),
        ComponentIR(ref="A_R", symbol="Device:R", value="10k"),
        ComponentIR(ref="Z_R", symbol="Device:R", value="10k"),
        ComponentIR(ref="J2", symbol="Device:Conn", value="Output"),
    ]
    nets = [
        NetIR(
            name="NET_A",
            pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="A_R", pin="1")],
        ),
        NetIR(
            name="NET_Z",
            pins=[PinRefIR(ref="J1", pin="2"), PinRefIR(ref="Z_R", pin="1")],
        ),
        NetIR(
            name="NET_OUT",
            pins=[
                PinRefIR(ref="A_R", pin="2"),
                PinRefIR(ref="Z_R", pin="2"),
                PinRefIR(ref="J2", pin="1"),
            ],
        ),
    ]
    return CircuitIR(version="1", components=components, nets=nets)


def _extract_rank_same_refs(dot_src: str) -> list[str]:
    """Return the list of component refs inside the first rank=same subgraph block."""
    lines = dot_src.splitlines()
    same_idx: int | None = None
    for i, ln in enumerate(lines):
        if "rank=same" in ln:
            same_idx = i
            break
    assert same_idx is not None, "No rank=same block found in DOT source"
    refs: list[str] = []
    for ln in lines[same_idx + 1 :]:
        stripped = ln.strip()
        if stripped == "}":
            break
        # Exclude lines that are only directives (rank=…, etc.)
        # Also skip internal tier-anchor nodes introduced by _emit_tier_subgraphs.
        if stripped and not stripped.startswith("rank") and not stripped.startswith("__tier_"):
            refs.append(stripped.rstrip(";"))
    return refs
