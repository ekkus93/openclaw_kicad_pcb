"""Phase 4: component orientations, tier assignment, and dot source signal flow."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    compute_orientations,
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


class TestComputeOrientations:
    """Unit tests for :func:`compute_orientations`."""

    def test_connector_always_zero(self) -> None:
        """Connector refs (J/CON/P/SJ/TJ) always get 0° regardless of neighbours."""
        ir = _make_ir(
            [("J1", "Connector_Generic:Conn_01x02"), ("R1", "Device:R")],
            [("SIG", [("J1", "1"), ("R1", "1")])],
        )
        positions = {"J1": (0.0, 0.0), "R1": (30.0, 0.0)}
        result = compute_orientations(ir, positions)
        assert result["J1"] == 0

    def test_opamp_always_zero(self) -> None:
        """Op-amp / IC refs always get 0°."""
        ir = _make_ir(
            [("U1", "Amplifier_Operational:TL071"), ("R1", "Device:R")],
            [("SIG", [("U1", "3"), ("R1", "1")])],
        )
        positions = {"U1": (0.0, 0.0), "R1": (0.0, 30.0)}
        result = compute_orientations(ir, positions)
        # U1 has a vertical neighbour but must stay 0° (IC rule)
        assert result["U1"] == 0

    def test_passive_horizontal_neighbours_gives_zero(self) -> None:
        """Passive with horizontally-offset neighbours → 0° (horizontal orientation)."""
        # J1 --- R1 --- R2, all in a horizontal line
        ir = _make_ir(
            [("J1", "Connector_Generic:Conn_01x02"), ("R1", "Device:R"), ("R2", "Device:R")],
            [
                ("A", [("J1", "1"), ("R1", "1")]),
                ("B", [("R1", "2"), ("R2", "1")]),
            ],
        )
        positions = {"J1": (0.0, 30.0), "R1": (30.0, 30.0), "R2": (60.0, 30.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 0

    def test_passive_vertical_neighbours_gives_90(self) -> None:
        """Passive with vertically-offset neighbours → 90°."""
        ir = _make_ir(
            [
                ("U1", "Amplifier_Operational:TL071"),
                ("R1", "Device:R"),
                ("U2", "Amplifier_Operational:TL071"),
            ],
            [
                ("A", [("U1", "1"), ("R1", "1")]),
                ("B", [("R1", "2"), ("U2", "1")]),
            ],
        )
        # U1 above R1 above U2 — vertical arrangement
        positions = {"U1": (30.0, 0.0), "R1": (30.0, 20.0), "U2": (30.0, 40.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 90

    def test_default_zero_for_unknown_prefix(self) -> None:
        """Components with unrecognised prefix default to 0°."""
        ir = _make_ir([("XYZ1", "Some:Lib")], [])
        result = compute_orientations(ir, {"XYZ1": (0.0, 0.0)})
        assert result["XYZ1"] == 0

    def test_power_nets_excluded_from_neighbour_calc(self) -> None:
        """Power/GND connections do not influence passive rotation.

        R1 only connects to VCC and GND (power nets) — no signal neighbours
        → 0° (neither horizontal nor vertical bias).
        """
        ir = _make_ir(
            [("R1", "Device:R")],
            [
                ("VCC", [("R1", "1")]),
                ("GND", [("R1", "2")]),
            ],
        )
        positions = {"R1": (30.0, 30.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 0

    def test_isolated_passive_defaults_zero(self) -> None:
        """Passive with no neighbours at all gets 0°."""
        ir = _make_ir([("C1", "Device:C")], [])
        result = compute_orientations(ir, {"C1": (30.0, 30.0)})
        assert result["C1"] == 0

    def test_all_components_returned(self) -> None:
        """Every component in the IR appears in the result."""
        refs = ["J1", "U1", "R1", "C1", "XYZ1"]
        comps = [(r, "Lib:sym") for r in refs]
        ir = _make_ir(comps, [])
        positions = {r: (float(i) * 10, 0.0) for i, r in enumerate(refs)}
        result = compute_orientations(ir, positions)
        assert set(result) == set(refs)


# ---------------------------------------------------------------------------
# Phase 4 — Shunt topology orientation (topology-driven, not position-based)
# ---------------------------------------------------------------------------


class TestShuntOrientations:
    """compute_orientations detects shunt topology and forces 90° rotation.

    A passive is shunt when ≥1 pin connects to a power/ground net AND ≥1 pin
    connects to a signal net.  This covers bypass capacitors and pull-up /
    pull-down resistors.  Pure-signal passives continue to use the existing
    position-based heuristic.
    """

    def test_bypass_cap_gnd_is_90(self) -> None:
        """AC-bypass capacitor: one pin on a signal net, one pin on GND → 90°.

        This covers the classic audio-stage bypass cap (AUDIO_IN to GND) where
        one pin is in the signal path and the other drains to the ground rail.
        Note: a *power-supply* decoupling cap with VCC–GND pins has *both* pins
        on power nets and is NOT detected as shunt by this rule (see
        test_both_pins_power_only_stays_zero).
        """
        ir = _make_ir(
            [("C1", "Device:C"), ("U1", "Amplifier_Operational:TL071")],
            [
                # Signal net: shared between C1 pin 1 and the op-amp input.
                ("AUDIO_IN", [("C1", "1"), ("U1", "3")]),
                # Power net: C1 pin 2 drains to GND.
                ("GND", [("C1", "2")]),
            ],
        )
        # Positions irrelevant — shunt check fires before position heuristic.
        positions = {"C1": (50.0, 50.0), "U1": (50.0, 30.0)}
        result = compute_orientations(ir, positions)
        assert result["C1"] == 90, (
            "AC-bypass cap (signal → GND) should be vertical (90°) regardless of position."
        )

    def test_pullup_resistor_vcc_is_90(self) -> None:
        """Pull-up resistor: one pin on VCC, one on a signal net → 90°."""
        ir = _make_ir(
            [("R1", "Device:R"), ("U1", "74xx:74HC74")],
            [
                ("VCC", [("R1", "1")]),
                ("nRESET", [("R1", "2"), ("U1", "4")]),
            ],
        )
        positions = {"R1": (40.0, 10.0), "U1": (60.0, 10.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 90, "Pull-up resistor (VCC → signal) should be vertical (90°)."

    def test_pulldown_resistor_gnd_is_90(self) -> None:
        """Pull-down resistor: one pin on GND, one on a signal net → 90°."""
        ir = _make_ir(
            [("R2", "Device:R"), ("U1", "74xx:74HC00")],
            [
                ("SIG", [("R2", "1"), ("U1", "1")]),
                ("GND", [("R2", "2")]),
            ],
        )
        positions = {"R2": (40.0, 20.0), "U1": (60.0, 20.0)}
        result = compute_orientations(ir, positions)
        assert result["R2"] == 90, "Pull-down resistor (signal → GND) should be vertical (90°)."

    def test_series_resistor_no_power_pin_uses_heuristic(self) -> None:
        """Series resistor with no power-pin uses the position heuristic (→ 0° when horizontal)."""
        ir = _make_ir(
            [("R1", "Device:R"), ("J1", "Connector:Conn"), ("U1", "Amplifier_Operational:TL071")],
            [
                ("IN", [("J1", "1"), ("R1", "1")]),
                ("MID", [("R1", "2"), ("U1", "3")]),
            ],
        )
        # Horizontal arrangement: position heuristic gives 0°.
        positions = {"J1": (0.0, 30.0), "R1": (30.0, 30.0), "U1": (60.0, 30.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 0, (
            "Series resistor between horizontally-spaced components should stay 0°."
        )

    def test_both_pins_power_only_stays_zero(self) -> None:
        """Passive with BOTH pins on power nets (no signal pin) keeps 0°.

        This is the existing behaviour for test_power_nets_excluded_from_neighbour_calc
        and must not regress.  A resistor between VCC and GND is NOT a shunt in
        the signal-path sense.
        """
        ir = _make_ir(
            [("R1", "Device:R")],
            [
                ("VCC", [("R1", "1")]),
                ("GND", [("R1", "2")]),
            ],
        )
        positions = {"R1": (30.0, 30.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 0, (
            "Passive with only power-net pins (no signal net) must keep 0° (no shunt detection)."
        )

    def test_shunt_fires_before_position_heuristic(self) -> None:
        """Shunt rule overrides position heuristic even when neighbours are horizontal.

        If C1 has one GND pin, it should be 90° regardless of whether the
        remaining signal-net neighbours are arranged horizontally or vertically.
        """
        ir = _make_ir(
            [
                ("C1", "Device:C"),
                ("U1", "Amplifier_Operational:TL071"),
                ("U2", "Amplifier_Operational:TL071"),
            ],
            [
                # Signal net connects C1 to two horizontally-offset op-amps.
                ("SIG", [("C1", "1"), ("U1", "6"), ("U2", "3")]),
                # Power net connects C1's second pin to GND.
                ("GND", [("C1", "2")]),
            ],
        )
        # U1 and U2 are far apart horizontally → position heuristic would give 0°.
        positions = {"C1": (50.0, 30.0), "U1": (0.0, 30.0), "U2": (100.0, 30.0)}
        result = compute_orientations(ir, positions)
        assert result["C1"] == 90, (
            "Shunt detection should override the position heuristic (GND pin present)."
        )


# ---------------------------------------------------------------------------
# Phase 4.1 — Connector orientation (tier-driven) and diode explicit 0°
# ---------------------------------------------------------------------------


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
