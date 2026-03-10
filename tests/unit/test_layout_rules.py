"""Tests for the four layout-quality rules.

Rule 1  — Connector direction: input connectors left, output connectors right.
          Implemented via ``_choose_seed_connector`` in ``tier.py``.

Rule 2  — Connector Y-alignment: each connector's y is snapped to the median y
          of its signal-net neighbours.
          Implemented via ``_snap_connectors_to_ic_y`` in
          ``kicad_pcb.graphviz_layout.snap``.

Rule 3  — Wider DOT spacing: ``nodesep=0.8``, ``ranksep=2.5``.
          Verified via ``_build_dot_source`` output inspection.

Rule 4  — Larger layout scale: ``SCALE_MM_PER_GV = 24.0``.
          Verified via the exported constant value.
"""

from __future__ import annotations

import pytest
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.graphviz_layout.dot_builder import _build_dot_source
from kicad_pcb.graphviz_layout.snap import (
    SCALE_MM_PER_GV,
    _snap_connectors_to_ic_y,
)
from kicad_pcb.layout import compute_affinity_groups
from kicad_pcb.tier import (
    _choose_seed_connector,
    assign_tiers,
    identify_main_signal_path,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ir(
    components: list[tuple[str, str]], nets: list[tuple[str, list[tuple[str, str]]]]
) -> CircuitIR:
    """Build a ``CircuitIR`` from compact ``(ref, symbol)`` and ``(net, [(ref, pin)])`` specs."""
    ir_comps = [ComponentIR(ref=ref, symbol=sym, value="x") for ref, sym in components]
    ir_nets = [
        NetIR(name=name, pins=[PinRefIR(ref=r, pin=p) for r, p in pins]) for name, pins in nets
    ]
    return CircuitIR(version="1", components=ir_comps, nets=ir_nets)


def _amp_ir() -> CircuitIR:
    """Minimal headphone-amp-like circuit.

    Signal flow: J2 (audio in) → C2 (coupling cap) → R1 (bias) → U1A (opamp)
                               → RV1 (volume pot) → J1 (headphone out)

    Hop distances (undirected) to nearest IC (U1A):

    * ``J1`` (headphone out): J1 → RV1 → U1A = **2 hops**
    * ``J2`` (audio in):      J2 → C2 → R1 → U1A = **3 hops**

    ``J2`` has MORE hops to the IC → ``_choose_seed_connector`` should prefer
    ``J2`` as the BFS seed (input connector).  With ``J1`` first alphabetically,
    the old "alphabetically-first" strategy would wrongly choose ``J1`` (output)
    as the seed.
    """
    return _ir(
        components=[
            ("C2", "Device:C"),  # input coupling cap
            ("J1", "Connector:J"),  # headphone out (alphabetically first!)
            ("J2", "Connector:J"),  # audio in
            ("R1", "Device:R"),  # input bias resistor
            ("RV1", "Device:R_Potentiometer"),  # volume pot (passive, ref RV*)
            ("U1A", "Amplifier_Operational:NE5532"),
        ],
        nets=[
            # Input chain: J2 → C2 → R1 → U1A  (3 hops to IC)
            ("in_jack", [("J2", "T"), ("C2", "1")]),
            ("bias_net", [("C2", "2"), ("R1", "1")]),
            ("opamp_in", [("R1", "2"), ("U1A", "3")]),
            # Output chain: U1A → RV1 → J1  (2 hops to IC)
            ("opamp_out", [("U1A", "1"), ("RV1", "1")]),
            ("out_jack", [("RV1", "2"), ("J1", "T")]),
        ],
    )


# ---------------------------------------------------------------------------
# Rule 1 — _choose_seed_connector: input connector preferred as BFS seed
# ---------------------------------------------------------------------------


class TestChooseSeedConnector:
    def test_returns_string(self) -> None:
        ir = _amp_ir()
        refs = [c.ref for c in ir.components]
        nets = [n for n in ir.nets if len(n.pins) >= 2]
        seed = _choose_seed_connector(refs, nets)
        assert isinstance(seed, str)
        assert seed in refs

    def test_prefers_input_connector_over_output(self) -> None:
        """J2 (audio in, 3 hops to opamp) beats J1 (headphone out, 2 hops)."""
        ir = _amp_ir()
        refs = [c.ref for c in ir.components]
        nets = [n for n in ir.nets if len(n.pins) >= 2]
        seed = _choose_seed_connector(refs, nets)
        # J2 is farther from U1A (3 hops) than J1 (2 hops) → should be preferred
        assert seed == "J2", (
            f"Expected J2 (audio-in) as seed, got {seed!r}. "
            "The seed should be the connector FARTHEST from the nearest IC."
        )

    def test_falls_back_to_alphabetical_without_ics(self) -> None:
        """No ICs → fall back to alphabetically-first connector."""
        ir = _ir(
            components=[("J2", "Connector:J"), ("J1", "Connector:J"), ("R1", "Device:R")],
            nets=[("net1", [("J1", "T"), ("R1", "1")]), ("net2", [("J2", "T"), ("R1", "2")])],
        )
        refs = [c.ref for c in ir.components]
        nets = [n for n in ir.nets if len(n.pins) >= 2]
        seed = _choose_seed_connector(refs, nets)
        assert seed == "J1"  # alphabetically first

    def test_strict_raises_without_ics(self) -> None:
        """Strict mode fails fast when connector seed would fall back without ICs."""
        ir = _ir(
            components=[("J2", "Connector:J"), ("J1", "Connector:J"), ("R1", "Device:R")],
            nets=[("net1", [("J1", "T"), ("R1", "1")]), ("net2", [("J2", "T"), ("R1", "2")])],
        )
        refs = [c.ref for c in ir.components]
        nets = [n for n in ir.nets if len(n.pins) >= 2]
        with pytest.raises(UserError, match="without IC components") as exc_info:
            _choose_seed_connector(refs, nets, strict=True)
        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID

    def test_no_connectors_returns_any_ref(self) -> None:
        """With no connectors, returns the only ref available."""
        ir = _ir(
            components=[("R1", "Device:R"), ("R2", "Device:R")],
            nets=[("net", [("R1", "1"), ("R2", "1")])],
        )
        refs = [c.ref for c in ir.components]
        nets_list = [n for n in ir.nets if len(n.pins) >= 2]
        seed = _choose_seed_connector(refs, nets_list)
        assert seed in refs


# ---------------------------------------------------------------------------
# Rule 1 — assign_tiers: output connector ends up at last tier
# ---------------------------------------------------------------------------


class TestAssignTiersDirectionality:
    def test_input_connector_gets_tier_zero(self) -> None:
        """After assign_tiers on the amp IR, J2 (input) must be at tier 0."""
        ir = _amp_ir()
        tiers = assign_tiers(ir)
        assert tiers.get("J2") == 0, (
            f"J2 (audio-in) should be at tier 0 but got tier {tiers.get('J2')}. Full tiers: {tiers}"
        )

    def test_output_connector_gets_last_tier(self) -> None:
        """After assign_tiers on the amp IR, J1 (headphone out) must be at max tier."""
        ir = _amp_ir()
        tiers = assign_tiers(ir)
        max_tier = max(tiers.values())
        assert tiers.get("J1") == max_tier, (
            f"J1 (headphone-out) should be at tier {max_tier} but got {tiers.get('J1')}. "
            f"Full tiers: {tiers}"
        )

    def test_ic_is_between_connectors(self) -> None:
        """U1A must be at a tier strictly between the two connectors."""
        ir = _amp_ir()
        tiers = assign_tiers(ir)
        j1_tier = tiers["J1"]
        j2_tier = tiers["J2"]
        u1a_tier = tiers["U1A"]
        lo, hi = sorted([j1_tier, j2_tier])
        assert lo < u1a_tier < hi, (
            f"U1A (opamp) should be between J1 tier={j1_tier} and J2 tier={j2_tier}, "
            f"but U1A is at tier={u1a_tier}. Full tiers: {tiers}"
        )

    def test_assign_tiers_strict_raises_without_ics(self) -> None:
        """Strict assign_tiers fails fast when no IC exists for connector-seed inference."""
        ir = _ir(
            components=[("J2", "Connector:J"), ("J1", "Connector:J"), ("R1", "Device:R")],
            nets=[("net1", [("J1", "T"), ("R1", "1")]), ("net2", [("J2", "T"), ("R1", "2")])],
        )
        with pytest.raises(UserError, match="without IC components") as exc_info:
            assign_tiers(ir, strict=True)
        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID


class TestMainSignalPathIdentification:
    def test_identify_main_signal_path_amp_chain(self) -> None:
        """Main path should follow input -> conditioning -> op-amp -> output."""
        ir = _amp_ir()
        tiers = assign_tiers(ir)

        path = identify_main_signal_path(ir, tiers=tiers)
        assert path == ["J2", "C2", "R1", "U1A", "RV1", "J1"], (
            f"Expected deterministic main signal chain for amp IR; got {path}"
        )

    def test_affinity_groups_prioritize_main_path_over_side_branch(self) -> None:
        """Tier ordering should keep main-path refs ahead of support side branches."""
        ir = _ir(
            components=[
                ("C2", "Device:C"),
                ("C9", "Device:C"),
                ("J1", "Connector:J"),
                ("J2", "Connector:J"),
                ("R1", "Device:R"),
                ("RV1", "Device:R_Potentiometer"),
                ("U1A", "Amplifier_Operational:NE5532"),
            ],
            nets=[
                ("in_jack", [("J2", "T"), ("C2", "1")]),
                ("bias_net", [("C2", "2"), ("R1", "1")]),
                ("opamp_in", [("R1", "2"), ("U1A", "3")]),
                # Main output chain plus support branch C9.
                ("opamp_out", [("U1A", "1"), ("RV1", "1"), ("C9", "1")]),
                ("out_jack", [("RV1", "2"), ("J1", "T")]),
                ("GND", [("C9", "2")]),
            ],
        )

        tiers = assign_tiers(ir)
        path = identify_main_signal_path(ir, tiers=tiers)
        assert path[-2:] == ["RV1", "J1"], f"Unexpected main path suffix: {path}"

        groups = compute_affinity_groups(ir, tiers)
        tier_of_rv1 = tiers["RV1"]
        ordered = groups[tier_of_rv1]
        assert ordered.index("RV1") < ordered.index("C9"), (
            "Main-path component RV1 should be prioritized before side-branch C9 "
            f"within tier {tier_of_rv1}: {ordered}"
        )


# ---------------------------------------------------------------------------
# Rule 2 — _snap_connectors_to_ic_y: y-alignment to neighbour median
# ---------------------------------------------------------------------------


class TestSnapConnectorsToIcY:
    def _positions(
        self, refs_and_y: dict[str, float]
    ) -> dict[str, tuple[float, float, float | None]]:
        """Build a simple positions dict from ref → y; x is 0 for all."""
        return {ref: (0.0, y, None) for ref, y in refs_and_y.items()}

    def test_connector_y_set_to_median_of_neighbours(self) -> None:
        """J1's y is snapped to the median y of its signal-net neighbours."""
        ir = _ir(
            components=[("J1", "Connector:J"), ("R1", "Device:R"), ("R2", "Device:R")],
            nets=[
                ("jnet", [("J1", "T"), ("R1", "1"), ("R2", "1")]),
            ],
        )
        # J1 at y=50, R1 at y=80, R2 at y=100
        positions = self._positions({"J1": 50.0, "R1": 80.0, "R2": 100.0})
        result = _snap_connectors_to_ic_y(positions, ir)
        # median of [80, 100] = 100 (lower-median: index len//2 = 1 → 100);
        # actually sorted = [80, 100], index 2//2=1 → 100. Snapped to 1.27 grid.

        expected_y = round(round(100.0 / 1.27) * 1.27, 4)
        assert result["J1"][1] == pytest.approx(expected_y, abs=0.01)

    def test_connector_y_snapped_to_single_neighbour(self) -> None:
        """When there is only one signal neighbour, connector y = that neighbour's y."""
        ir = _ir(
            components=[("J1", "Connector:J"), ("R1", "Device:R")],
            nets=[("net1", [("J1", "T"), ("R1", "1")])],
        )
        positions = self._positions({"J1": 30.0, "R1": 75.0})
        result = _snap_connectors_to_ic_y(positions, ir)
        expected_y = round(round(75.0 / 1.27) * 1.27, 4)
        assert result["J1"][1] == pytest.approx(expected_y, abs=0.01)

    def test_non_connector_y_unchanged(self) -> None:
        """Passives and ICs must not be moved."""
        ir = _ir(
            components=[("J1", "Connector:J"), ("R1", "Device:R")],
            nets=[("net1", [("J1", "T"), ("R1", "1")])],
        )
        positions = self._positions({"J1": 50.0, "R1": 75.0})
        result = _snap_connectors_to_ic_y(positions, ir)
        assert result["R1"][1] == 75.0  # R1's y must not change

    def test_no_op_when_no_connectors(self) -> None:
        """Returns positions unchanged when no connector refs exist."""
        ir = _ir(
            components=[("R1", "Device:R"), ("R2", "Device:R")],
            nets=[("net", [("R1", "1"), ("R2", "1")])],
        )
        positions = self._positions({"R1": 60.0, "R2": 90.0})
        result = _snap_connectors_to_ic_y(positions, ir)
        assert result == positions

    def test_other_connectors_excluded_from_neighbour_list(self) -> None:
        """Connectors are not used as y-anchors for each other."""
        # J1 and J2 share a net. Only R1 (non-connector neighbour) should anchor J1.
        ir = _ir(
            components=[("J1", "Connector:J"), ("J2", "Connector:J"), ("R1", "Device:R")],
            nets=[
                ("net1", [("J1", "T"), ("R1", "1")]),
                ("net2", [("J2", "T"), ("R1", "2")]),
            ],
        )
        positions = self._positions({"J1": 30.0, "J2": 150.0, "R1": 80.0})
        result = _snap_connectors_to_ic_y(positions, ir)
        # J1's only non-connector neighbour is R1 at y=80.
        expected_j1_y = round(round(80.0 / 1.27) * 1.27, 4)
        assert result["J1"][1] == pytest.approx(expected_j1_y, abs=0.01)
        # J2 similarly snaps to R1 at y=80.
        assert result["J2"][1] == pytest.approx(expected_j1_y, abs=0.01)

    def test_connector_x_and_rotation_preserved(self) -> None:
        """Only y is changed; x and rotation remain untouched."""
        ir = _ir(
            components=[("J1", "Connector:J"), ("R1", "Device:R")],
            nets=[("net1", [("J1", "T"), ("R1", "1")])],
        )
        positions = {"J1": (40.0, 30.0, 90.0), "R1": (100.0, 75.0, None)}
        result = _snap_connectors_to_ic_y(positions, ir)  # type: ignore[arg-type]
        assert result["J1"][0] == 40.0  # x unchanged
        assert result["J1"][2] == 90.0  # rotation unchanged


# ---------------------------------------------------------------------------
# Rule 3 — DOT spacing: nodesep=0.8  ranksep=2.5
# ---------------------------------------------------------------------------


class TestDotSpacing:
    def _get_dot_source(self) -> str:
        ir = _ir(
            components=[("J1", "Connector:J"), ("R1", "Device:R"), ("U1", "Amplifier:IC")],
            nets=[("n1", [("J1", "1"), ("R1", "1")]), ("n2", [("R1", "2"), ("U1", "1")])],
        )
        return _build_dot_source(ir)

    def test_nodesep_is_increased(self) -> None:
        src = self._get_dot_source()
        assert "nodesep=0.8" in src, "Rule 3: nodesep should be 0.8"

    def test_ranksep_is_increased(self) -> None:
        src = self._get_dot_source()
        assert "ranksep=2.5" in src, "Rule 3: ranksep should be 2.5"


# ---------------------------------------------------------------------------
# Rule 4 — Layout scale: SCALE_MM_PER_GV = 24.0
# ---------------------------------------------------------------------------


class TestLayoutScale:
    def test_scale_constant_value(self) -> None:
        """Rule 4: scale should be 24.0 mm/inch (increased from 20.0)."""
        assert pytest.approx(24.0) == SCALE_MM_PER_GV, (
            f"SCALE_MM_PER_GV should be 24.0 but is {SCALE_MM_PER_GV}. "
            "Rule 4 requires an increase from 20.0 to spread the layout."
        )
