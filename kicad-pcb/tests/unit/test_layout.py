"""Unit tests for kicad_pcb.layout (Rule 4 — op-amp halo; Rule 1 — SDS; Rule 2 — recursive halving).

Rule 4 tests verify the three eligibility conditions for halo membership:
  1. Passive prefix (R, C, L)
  2. feedback=True  OR  exclusive-IC signal coupling
  3. Not connected to any power net

Rule 1 tests verify compute_signal_distance_scores():
  * Linear chain SDS values 0.0 → 1.0
  * Power-only cap inherits anchor IC SDS
  * Both-distances-zero → 0.5
  * ComponentAnnotation.sds populated when roles supplied

Rule 2 tests verify _recursive_halving() and compute_signal_flow_layout(roles=...):
  * 8 components → 8 distinct monotone columns (max_per_col=1)
  * Small circuit fits in one column
  * Degenerate SDS=0.5 for all → no crash
  * Roles trigger SDS-based column assignment in full layout
  * Missing output connector → WARNING + BFS fallback
"""

from __future__ import annotations

import logging

import pytest
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    _MAX_COLS,
    GRID_COL_MM,
    ORIGIN_X,
    ComponentAnnotation,
    _compute_opamp_halo,
    _recursive_halving,
    barycentric_sort,
    build_signal_adjacency,
    compute_signal_distance_scores,
    compute_signal_flow_layout,
    count_wire_crossings,
    find_feedback_paths,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ir(
    components: list[tuple[str, str, str]],
    nets: list[tuple[str, list[tuple[str, str]]]],
) -> CircuitIR:
    """Build a minimal CircuitIR.

    *components* is a list of ``(ref, symbol, value)`` tuples.
    *nets* is a list of ``(net_name, [(ref, pin), ...])`` tuples.
    """
    comps = [ComponentIR(ref=ref, symbol=sym, value=val) for ref, sym, val in components]
    net_objs: list[NetIR] = []
    for net_name, pins in nets:
        pin_objs = [PinRefIR(ref=r, pin=p, unit=None) for r, p in pins]
        net_objs.append(NetIR(name=net_name, pins=pin_objs))
    return CircuitIR(version="1", components=comps, nets=net_objs, options=None)


def _annotations(
    *feedback_refs: str,
    all_refs: list[str],
) -> dict[str, ComponentAnnotation]:
    """Return annotations dict; listed refs get ``feedback=True``."""
    fb_set = set(feedback_refs)
    return {ref: ComponentAnnotation(feedback=(ref in fb_set)) for ref in all_refs}


# ---------------------------------------------------------------------------
# Condition 2a — feedback flag selects halo membership
# ---------------------------------------------------------------------------


class TestHaloFeedbackCondition:
    """Passive with feedback=True and an IC neighbour → halo member."""

    def test_feedback_resistor_is_halo(self):
        """Classic inverting op-amp: R_fb bridges net_inv and net_out via U1."""
        ir = _make_ir(
            [
                ("J1", "Conn_01x01", "in"),
                ("R_in", "R", "10k"),
                ("U1", "NE5532", "NE5532"),
                ("R_fb", "R", "100k"),
                ("J2", "Conn_01x01", "out"),
            ],
            [
                ("net_in", [("J1", "1"), ("R_in", "1")]),
                ("net_inv", [("R_in", "2"), ("U1", "2"), ("R_fb", "1")]),
                ("net_out", [("U1", "1"), ("J2", "1"), ("R_fb", "2")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"J1": 0, "R_in": 1, "U1": 2, "R_fb": 2, "J2": 3}
        annotations = _annotations("R_fb", all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "R_fb" in halo, "R_fb should be identified as a halo member"
        assert halo["R_fb"] == "U1", "U1 should be the anchor for R_fb"

    def test_feedback_capacitor_is_halo(self):
        """Stability cap C_comp in feedback position → also a halo member."""
        ir = _make_ir(
            [("U1", "NE5532", "NE5532"), ("C_comp", "C", "10p")],
            [
                ("net_inv", [("U1", "2"), ("C_comp", "1")]),
                ("net_out", [("U1", "1"), ("C_comp", "2")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"U1": 2, "C_comp": 2}
        annotations = _annotations("C_comp", all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "C_comp" in halo
        assert halo["C_comp"] == "U1"

    def test_series_resistor_not_halo_even_if_annotated(self):
        """R_in connects J1 and U1 — feedback=False, not exclusive IC → not halo."""
        ir = _make_ir(
            [("J1", "Conn_01x01", "in"), ("R_in", "R", "10k"), ("U1", "NE5532", "NE5532")],
            [
                ("net_in", [("J1", "1"), ("R_in", "1")]),
                ("net_inv", [("R_in", "2"), ("U1", "2")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"J1": 0, "R_in": 1, "U1": 2}
        annotations = _annotations(all_refs=all_refs)  # feedback=False for all

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "R_in" not in halo


# ---------------------------------------------------------------------------
# Condition 2b — exclusive IC coupling selects halo membership
# ---------------------------------------------------------------------------


class TestHaloExclusiveIcCondition:
    """Passive whose only signal neighbours all belong to one IC → halo member."""

    def test_exclusive_ic_passive_is_halo(self):
        """R_coupled only on nets that contain U1 → exclusive IC passive."""
        ir = _make_ir(
            [("U1", "NE5532", "NE5532"), ("R_coupled", "R", "1k")],
            [
                ("net_a", [("U1", "3"), ("R_coupled", "1")]),
                ("net_b", [("U1", "4"), ("R_coupled", "2")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"U1": 1, "R_coupled": 1}
        annotations = _annotations(all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "R_coupled" in halo
        assert halo["R_coupled"] == "U1"

    def test_capacitor_exclusive_ic_is_halo(self):
        """C_comp referenced only by U1 pins → halo member."""
        ir = _make_ir(
            [("U1", "NE5532", "NE5532"), ("C_stab", "C", "47p")],
            [
                ("net_x", [("U1", "5"), ("C_stab", "1")]),
                ("net_y", [("U1", "6"), ("C_stab", "2")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"U1": 2, "C_stab": 2}
        annotations = _annotations(all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "C_stab" in halo
        assert halo["C_stab"] == "U1"

    def test_passive_with_connector_neighbour_not_exclusive(self):
        """R also connects to J1 → not exclusive IC → not halo by condition 2b."""
        ir = _make_ir(
            [("J1", "Conn_01x01", "in"), ("R", "R", "10k"), ("U1", "NE5532", "NE5532")],
            [
                ("net_in", [("J1", "1"), ("R", "1")]),
                ("net_mid", [("R", "2"), ("U1", "2")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"J1": 0, "R": 1, "U1": 2}
        annotations = _annotations(all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "R" not in halo

    def test_passive_with_two_ic_neighbours_not_exclusive(self):
        """R bridging U1 and U2 → ic_set has two ICs → not exclusive → not halo."""
        ir = _make_ir(
            [("U1", "NE5532", "NE5532"), ("R_bridge", "R", "1k"), ("U2", "NE5532", "NE5532")],
            [
                ("net_a", [("U1", "1"), ("R_bridge", "1")]),
                ("net_b", [("R_bridge", "2"), ("U2", "2")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"U1": 0, "R_bridge": 1, "U2": 2}
        annotations = _annotations(all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "R_bridge" not in halo


# ---------------------------------------------------------------------------
# Condition 3 — power-net passives excluded
# ---------------------------------------------------------------------------


class TestHaloPowerNetExclusion:
    """Passives with any power-net pin are NOT halo members (condition 3)."""

    def test_bypass_cap_on_gnd_and_vcc_not_halo(self):
        """C_bypass connects to GND and VCC — excluded by condition 3."""
        ir = _make_ir(
            [("U1", "NE5532", "NE5532"), ("C_bypass", "C", "100n")],
            [
                ("GND", [("U1", "4"), ("C_bypass", "2")]),
                ("VCC", [("U1", "8"), ("C_bypass", "1")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"U1": 1, "C_bypass": 0}
        annotations = _annotations(all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "C_bypass" not in halo

    def test_shunt_resistor_mixed_nets_not_halo(self):
        """R_pullup connects to VCC (power) and a signal net → excluded."""
        ir = _make_ir(
            [("U1", "NE5532", "NE5532"), ("R_pullup", "R", "10k")],
            [
                ("VCC", [("U1", "8"), ("R_pullup", "2")]),
                ("net_sig", [("U1", "3"), ("R_pullup", "1")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"U1": 1, "R_pullup": 1}
        annotations = _annotations(all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "R_pullup" not in halo

    def test_feedback_passive_on_power_net_not_halo(self):
        """Even with feedback=True, power-net connection disqualifies the passive."""
        ir = _make_ir(
            [("U1", "NE5532", "NE5532"), ("R_fb", "R", "100k")],
            [
                ("GND", [("U1", "4"), ("R_fb", "2")]),
                ("net_out", [("U1", "1"), ("R_fb", "1")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"U1": 1, "R_fb": 1}
        annotations = _annotations("R_fb", all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "R_fb" not in halo


# ---------------------------------------------------------------------------
# Condition 1 — non-passives are never halo members
# ---------------------------------------------------------------------------


class TestHaloPassivePrefixCondition:
    """Only R, C, L prefix components can be halo members."""

    def test_ic_itself_not_halo(self):
        """U2 connected only to U1 — ICs are never halo members."""
        ir = _make_ir(
            [("U1", "NE5532", "NE5532"), ("U2", "NE5532", "NE5532")],
            [("net_a", [("U1", "1"), ("U2", "2")])],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"U1": 0, "U2": 1}
        annotations = _annotations(all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert halo == {}

    def test_diode_not_halo(self):
        """D1 exclusively connected to U1 — diodes (D prefix) are not halo."""
        ir = _make_ir(
            [("U1", "NE5532", "NE5532"), ("D1", "D", "1N4148")],
            [
                ("net_a", [("U1", "3"), ("D1", "A")]),
                ("net_b", [("U1", "1"), ("D1", "K")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"U1": 1, "D1": 1}
        annotations = _annotations(all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "D1" not in halo

    def test_connector_not_halo(self):
        """J1 exclusively connected to U1 — connectors are not halo."""
        ir = _make_ir(
            [("U1", "NE5532", "NE5532"), ("J1", "Conn_01x01", "")],
            [("net_a", [("U1", "1"), ("J1", "1")])],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"U1": 1, "J1": 0}
        annotations = _annotations(all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "J1" not in halo


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestHaloEdgeCases:
    """Edge cases: empty circuit, no passives, no ICs."""

    def test_no_passives_empty_halo(self):
        """Circuit with only an IC and one net — no passives → empty halo."""
        ir = _make_ir(
            [("U1", "NE5532", "NE5532")],
            [("net_a", [("U1", "1")])],
        )
        all_refs = [c.ref for c in ir.components]
        annotations = _annotations(all_refs=all_refs)
        halo = _compute_opamp_halo(ir, annotations, {"U1": 1})
        assert halo == {}

    def test_no_ics_empty_halo(self):
        """Passives between connectors only — no IC anchor → empty halo."""
        ir = _make_ir(
            [("J1", "Conn_01x01", "in"), ("R1", "R", "1k"), ("J2", "Conn_01x01", "out")],
            [
                ("net_in", [("J1", "1"), ("R1", "1")]),
                ("net_out", [("R1", "2"), ("J2", "1")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"J1": 0, "R1": 1, "J2": 2}
        annotations = _annotations(all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert halo == {}

    def test_passive_with_no_signal_nets_not_halo(self):
        """Passive only on power nets produces no signal neighbours → not halo."""
        ir = _make_ir(
            [("U1", "NE5532", "NE5532"), ("R_pwr", "R", "100")],
            [
                ("VCC", [("U1", "8"), ("R_pwr", "1")]),
                ("GND", [("U1", "4"), ("R_pwr", "2")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"U1": 1, "R_pwr": 0}
        annotations = _annotations(all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "R_pwr" not in halo


# ---------------------------------------------------------------------------
# Anchor selection
# ---------------------------------------------------------------------------


class TestHaloAnchorSelection:
    """Anchor is the IC closest in tier distance; alphabetical tiebreak."""

    def test_closer_tier_ic_selected_as_anchor(self):
        """R_fb at tier 2; U1 at tier 1 (dist 1), U2 at tier 4 (dist 2) → U1."""
        ir = _make_ir(
            [
                ("U1", "NE5532", "NE5532"),
                ("R_fb", "R", "100k"),
                ("U2", "NE5532", "NE5532"),
            ],
            [
                # R_fb bridges nets that share U1 — feedback topology w/ U1
                ("net_inv", [("U1", "2"), ("R_fb", "1")]),
                ("net_out", [("U1", "1"), ("R_fb", "2"), ("U2", "5")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"U1": 1, "R_fb": 2, "U2": 4}
        # R_fb: net_inv has U1; net_out has U1 and U2 → shared = {U1} → feedback
        annotations = _annotations("R_fb", all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "R_fb" in halo
        # U1 is closer: |2-1|=1 < |2-4|=2
        assert halo["R_fb"] == "U1"

    def test_alphabetical_tiebreak_when_equidistant(self):
        """R_fb equidistant from UA and UB (both tier 1); alphabetical → UA."""
        ir = _make_ir(
            [
                ("UA", "NE5532", "NE5532"),
                ("UB", "NE5532", "NE5532"),
                ("R_fb", "R", "100k"),
            ],
            [
                ("net_a", [("UA", "1"), ("R_fb", "1")]),
                ("net_b", [("UB", "2"), ("R_fb", "2"), ("UA", "3")]),
            ],
        )
        all_refs = [c.ref for c in ir.components]
        tiers = {"UA": 1, "UB": 1, "R_fb": 1}
        # net_a and net_b share UA → R_fb is feedback (shared component = UA)
        annotations = _annotations("R_fb", all_refs=all_refs)

        halo = _compute_opamp_halo(ir, annotations, tiers)

        assert "R_fb" in halo
        # Both equidistant → alphabetical: "UA" < "UB"
        assert halo["R_fb"] == "UA"


# ---------------------------------------------------------------------------
# Rule 1: Signal Distance Score (SDS)
# ---------------------------------------------------------------------------


class TestComputeSignalDistanceScores:
    """Unit tests for compute_signal_distance_scores() (R1-1 through R1-4)."""

    def test_linear_chain_five_components(self):
        """J1→R1→U1→R2→J2: SDS should span 0.0→1.0 in equal steps."""
        ir = _make_ir(
            [
                ("J1", "Connector", "J1"),
                ("R1", "R", "100"),
                ("U1", "TL071", "U"),
                ("R2", "R", "100"),
                ("J2", "Connector", "J2"),
            ],
            [
                ("N_in", [("J1", "1"), ("R1", "1")]),
                ("N_mid", [("R1", "2"), ("U1", "1")]),
                ("N_out", [("U1", "2"), ("R2", "1")]),
                ("N_end", [("R2", "2"), ("J2", "1")]),
            ],
        )
        roles = {"J1": "input", "J2": "output"}
        sds = compute_signal_distance_scores(ir, roles)

        # J1: d_in=0, d_out=4 → sds=0.0
        assert pytest.approx(sds["J1"], abs=1e-9) == 0.0
        # R1: d_in=1, d_out=3 → sds=0.25
        assert pytest.approx(sds["R1"], abs=1e-9) == 0.25
        # U1: d_in=2, d_out=2 → sds=0.5
        assert pytest.approx(sds["U1"], abs=1e-9) == 0.5
        # R2: d_in=3, d_out=1 → sds=0.75
        assert pytest.approx(sds["R2"], abs=1e-9) == 0.75
        # J2: d_in=4, d_out=0 → sds=1.0
        assert pytest.approx(sds["J2"], abs=1e-9) == 1.0

    def test_power_only_cap_inherits_ic_sds(self):
        """Decoupling cap (1 signal net, ≥1 power net) inherits anchor IC's SDS."""
        ir = _make_ir(
            [
                ("J1", "Connector", "J1"),
                ("U1", "TL071", "U"),
                ("J2", "Connector", "J2"),
                ("C1", "C", "100n"),
            ],
            [
                ("N_in", [("J1", "1"), ("U1", "1")]),
                ("N_out", [("U1", "2"), ("J2", "1")]),
                # C1: one signal net shared with U1, one power net → decoupling cap.
                ("N_local", [("U1", "3"), ("C1", "1")]),
                ("VCC", [("C1", "2")]),
            ],
        )
        roles = {"J1": "input", "J2": "output"}
        sds = compute_signal_distance_scores(ir, roles)

        # U1 is mid-chain → SDS=0.5
        assert pytest.approx(sds["U1"], abs=1e-9) == 0.5
        # C1 is a decoupling cap anchored to U1 → inherits SDS=0.5
        assert pytest.approx(sds["C1"], abs=1e-9) == 0.5

    def test_no_connectors_returns_half_for_all(self):
        """No input/output connectors → sentinel distances → SDS=0.5 for all."""
        ir = _make_ir(
            [("R1", "R", "1k"), ("R2", "R", "1k")],
            [("NET1", [("R1", "1"), ("R2", "1")])],
        )
        roles: dict[str, str] = {}
        sds = compute_signal_distance_scores(ir, roles)

        # Both d_in and d_out are _SDS_SENTINEL=1000 → 1000/(1000+1000) = 0.5
        for ref in ("R1", "R2"):
            assert pytest.approx(sds[ref], abs=1e-9) == 0.5

    def test_annotation_sds_populated_when_roles_supplied(self):
        """find_feedback_paths populates ComponentAnnotation.sds when roles given."""
        ir = _make_ir(
            [
                ("J1", "Connector", "J1"),
                ("R1", "R", "100"),
                ("J2", "Connector", "J2"),
            ],
            [
                ("N_a", [("J1", "1"), ("R1", "1")]),
                ("N_b", [("R1", "2"), ("J2", "1")]),
            ],
        )
        tiers: dict[str, int] = {"J1": 0, "R1": 1, "J2": 2}
        roles = {"J1": "input", "J2": "output"}
        annotations = find_feedback_paths(ir, tiers, roles=roles)

        # J1: d_in=0, d_out=2 → sds=0.0
        assert pytest.approx(annotations["J1"].sds, abs=1e-9) == 0.0
        # R1: d_in=1, d_out=1 → sds=0.5
        assert pytest.approx(annotations["R1"].sds, abs=1e-9) == 0.5
        # J2: d_in=2, d_out=0 → sds=1.0
        assert pytest.approx(annotations["J2"].sds, abs=1e-9) == 1.0

    def test_annotation_sds_defaults_without_roles(self):
        """find_feedback_paths leaves sds=0.5 for all when roles not provided."""
        ir = _make_ir(
            [("R1", "R", "1k"), ("R2", "R", "1k")],
            [("NET1", [("R1", "1"), ("R2", "1")])],
        )
        annotations = find_feedback_paths(ir, tiers={})

        for ann in annotations.values():
            assert ann.sds == 0.5


# ---------------------------------------------------------------------------
# Rule 2: Recursive halving column assignment
# ---------------------------------------------------------------------------


class TestRecursiveHalving:
    """Unit tests for _recursive_halving() (R2-1, R2-5)."""

    def test_eight_components_distinct_monotone_columns(self):
        """8 refs with distinct SDS values, max_per_col=1 → 8 distinct monotone cols."""
        refs = [f"R{i}" for i in range(8)]
        sds = {r: i / 7.0 for i, r in enumerate(refs)}  # SDS: 0, 1/7, ..., 1

        cols = _recursive_halving(
            refs,
            sds,
            ORIGIN_X,
            ORIGIN_X + _MAX_COLS * GRID_COL_MM,
            max_per_col=1,
            grid_col_mm=GRID_COL_MM,
        )

        col_vals = [cols[r] for r in refs]  # refs already in SDS order
        assert len(set(col_vals)) == 8, f"Expected 8 distinct columns, got: {col_vals}"
        assert col_vals == sorted(col_vals), f"Columns not monotone: {col_vals}"

    def test_small_group_fits_one_column(self):
        """3 refs with max_per_col=7 → all assigned column 0 (fits in base case)."""
        refs = ["R1", "R2", "R3"]
        sds = {"R1": 0.0, "R2": 0.5, "R3": 1.0}

        cols = _recursive_halving(
            refs,
            sds,
            ORIGIN_X,
            ORIGIN_X + _MAX_COLS * GRID_COL_MM,
            max_per_col=7,
            grid_col_mm=GRID_COL_MM,
        )

        assert set(cols.values()) == {0}, f"Expected all in col 0, got: {cols}"

    def test_all_same_sds_no_crash(self):
        """10 refs all with SDS=0.5 and max_per_col=1 → split still works."""
        refs = [f"C{i}" for i in range(10)]
        sds = {r: 0.5 for r in refs}

        cols = _recursive_halving(
            refs,
            sds,
            ORIGIN_X,
            ORIGIN_X + _MAX_COLS * GRID_COL_MM,
            max_per_col=1,
            grid_col_mm=GRID_COL_MM,
        )

        assert set(cols.keys()) == set(refs)

    def test_empty_refs_returns_empty(self):
        """Empty refs list → empty result dict."""
        cols = _recursive_halving(
            [],
            {},
            ORIGIN_X,
            ORIGIN_X + _MAX_COLS * GRID_COL_MM,
            max_per_col=7,
            grid_col_mm=GRID_COL_MM,
        )
        assert cols == {}


class TestComputeSignalFlowLayoutWithRoles:
    """Integration tests for compute_signal_flow_layout(roles=...) (R2-2, R2-4)."""

    def test_roles_preserve_left_to_right_order(self):
        """Providing roles causes SDS-based columns; x(input) ≤ x(mid) ≤ x(output)."""
        ir = _make_ir(
            [
                ("J1", "Connector", "J1"),
                ("U1", "TL071", "U"),
                ("J2", "Connector", "J2"),
            ],
            [
                ("N_in", [("J1", "1"), ("U1", "1")]),
                ("N_out", [("U1", "2"), ("J2", "1")]),
            ],
        )
        roles = {"J1": "input", "J2": "output"}
        positions = compute_signal_flow_layout(ir, roles=roles)

        assert set(positions.keys()) == {"J1", "U1", "J2"}
        x_j1, _ = positions["J1"]
        x_u1, _ = positions["U1"]
        x_j2, _ = positions["J2"]
        assert x_j1 <= x_u1 <= x_j2, f"Expected x(J1)={x_j1} ≤ x(U1)={x_u1} ≤ x(J2)={x_j2}"

    def test_fallback_warning_without_output_connector(self, caplog: pytest.LogCaptureFixture):
        """Missing output connector in roles → WARNING logged, layout still produced."""
        ir = _make_ir(
            [
                ("J1", "Connector", "J1"),
                ("R1", "R", "1k"),
            ],
            [("N1", [("J1", "1"), ("R1", "1")])],
        )
        roles = {"J1": "input"}  # no "output" connector

        with caplog.at_level(logging.WARNING, logger="kicad_pcb.layout"):
            positions = compute_signal_flow_layout(ir, roles=roles)

        assert any("SDS fallback" in rec.message for rec in caplog.records)
        assert set(positions.keys()) == {"J1", "R1"}


# ---------------------------------------------------------------------------
# Rule 3: Two-pass barycentric vertical sort
# ---------------------------------------------------------------------------


class TestBarycentricSort:
    """Unit tests for _barycentric_sort() (R3-1, R3-4)."""

    def test_eliminates_simple_crossing(self):
        """Two adjacent columns, naive order creates an A-D / B-C crossing.

        Col 0: [A, B]   (A at row 0, B at row 1)
        Col 1: [C, D]   initial alphabetical order
        Connections: A→D and B→C

        Naive (alpha) ordering in col 1 gives C at row 0, D at row 1:
            A(0) → D(1) and B(1) → C(0) — a crossing.
        After barycentric: D’s weight = row(A in col 0) = 0,
                           C’s weight = row(B in col 0) = 1.
        Col 1 → [D, C] → A(0)→D(0) and B(1)→C(1): no crossing.
        """
        by_col: dict[int, list[str]] = {0: ["A", "B"], 1: ["C", "D"]}
        adj: dict[str, set[str]] = {
            "A": {"D"},
            "D": {"A"},
            "B": {"C"},
            "C": {"B"},
        }
        result = barycentric_sort(by_col, adj, passes=1)

        # Col 0 is the L→R anchor (not sorted in L→R pass); unchanged.
        assert result[0] == ["A", "B"]
        # Col 1 sorted so D (weight 0) precedes C (weight 1).
        assert result[1] == ["D", "C"]

    def test_correct_order_not_disturbed(self):
        """Already-correct ordering is not changed by barycentric sort."""
        by_col: dict[int, list[str]] = {0: ["A", "B"], 1: ["C", "D"]}
        # A-C and B-D: [A, B] in col 0 → C should be at row 0, D at row 1.
        adj: dict[str, set[str]] = {
            "A": {"C"},
            "C": {"A"},
            "B": {"D"},
            "D": {"B"},
        }
        result = barycentric_sort(by_col, adj, passes=1)

        assert result[0] == ["A", "B"]
        assert result[1] == ["C", "D"]

    def test_empty_by_col_returns_empty(self):
        """Empty input produces empty output."""
        assert barycentric_sort({}, {}, passes=2) == {}

    def test_single_column_unchanged(self):
        """A single column has no adjacent reference; list is not reordered."""
        by_col: dict[int, list[str]] = {0: ["C", "A", "B"]}
        adj: dict[str, set[str]] = {"A": {"B"}, "B": {"A"}}
        result = barycentric_sort(by_col, adj, passes=2)

        # L→R skips col 0 (no left neighbour).
        # R→L: range(max-2, -1) = range(-1, -1) is empty — nothing sorts col 0.
        assert result[0] == ["C", "A", "B"]

    def test_idempotent_after_one_sweep(self):
        """Applying barycentric sort again on the already-sorted result is a no-op."""
        by_col: dict[int, list[str]] = {0: ["A", "B"], 1: ["C", "D"]}
        adj: dict[str, set[str]] = {
            "A": {"D"},
            "D": {"A"},
            "B": {"C"},
            "C": {"B"},
        }
        result1 = barycentric_sort(by_col, adj, passes=1)
        result2 = barycentric_sort(result1, adj, passes=1)

        assert result2[0] == result1[0]
        assert result2[1] == result1[1]

    def test_second_sweep_refines_via_rl_sorted_col0(self):
        """Second sweep improves col 1 ordering when col 0 was updated by R→L.

        Setup:  col 0 = [B, A]  (non-canonical start for B before A)
                col 1 = [C, D]
                col 2 = [E, F]
        Connections: B→C, A→D, D→E, C→F

        Sweep 1 L→R (col 0 skipped as anchor):
          col 1 by col 0=[B,A]: C weight=row(B)=0, D weight=row(A)=1 → [C, D]
          col 2 by col 1=[C,D]: E weight=row(D)=1, F weight=row(C)=0 → [F, E]
        Sweep 1 R→L (col 2 is right anchor, skipped in R→L):
          col 1 by col 2=[F,E]: C weight=row(F)=0, D weight=row(E)=1 → [C, D]
          col 0 by col 1=[C,D]: B weight=row(C)=0, A weight=row(D)=1 → [B, A]
        All crossings already 0 after sweep 1.  Sweep 2 is idempotent.
        """
        by_col: dict[int, list[str]] = {0: ["B", "A"], 1: ["C", "D"], 2: ["E", "F"]}
        # B→C (B↔C), A→D (A↔D), D→E (D↔E), C→F (C↔F)
        adj: dict[str, set[str]] = {
            "A": {"D"},
            "B": {"C"},
            "C": {"B", "F"},
            "D": {"A", "E"},
            "E": {"D"},
            "F": {"C"},
        }
        result = barycentric_sort(by_col, adj, passes=2)

        # Col 0: B at row 0, A at row 1 (R→L preserves this).
        assert result[0] == ["B", "A"]
        # Col 1: C at row 0, D at row 1 (C weight from B=row0, D weight from A=row1).
        assert result[1] == ["C", "D"]
        # Col 2: F at row 0, E at row 1 (F weight from C=row0, E weight from D=row1).
        assert result[2] == ["F", "E"]

    def test_three_adjacent_columns_no_crossings(self):
        """Three-column circuit is sorted so that no wires cross."""
        # col 0 = [A, B], col 1 = [C, D], col 2 = [E, F]
        # Connections: A–C, B–D (col0→col1); C–E, D–F (col1→col2)
        # Correct order: col0=[A,B], col1=[C,D], col2=[E,F]
        by_col: dict[int, list[str]] = {0: ["A", "B"], 1: ["D", "C"], 2: ["F", "E"]}
        adj: dict[str, set[str]] = {
            "A": {"C"},
            "B": {"D"},
            "C": {"A", "E"},
            "D": {"B", "F"},
            "E": {"C"},
            "F": {"D"},
        }
        result = barycentric_sort(by_col, adj, passes=2)

        # Col 1: C's weight = row(A in col0) = 0; D's weight = row(B in col0) = 1.
        assert result[1] == ["C", "D"]
        # Col 2: E's weight = row(C in col1) = 0; F's weight = row(D in col1) = 1.
        assert result[2] == ["E", "F"]


# ---------------------------------------------------------------------------
# R6 — Wire Crossing Budget
# ---------------------------------------------------------------------------


class TestCountWireCrossings:
    """Unit tests for count_wire_crossings() and build_signal_adjacency() (R6-1)."""

    def test_empty_adjacency_returns_zero(self) -> None:
        """No adjacency edges → 0 crossings regardless of positions."""
        positions = {"A": (0.0, 0.0), "B": (30.0, 10.0)}
        assert count_wire_crossings(positions, {}) == 0

    def test_single_edge_returns_zero(self) -> None:
        """A single edge has no crossing partner → 0."""
        positions = {"A": (0.0, 0.0), "B": (30.0, 10.0)}
        adj: dict[str, set[str]] = {"A": {"B"}, "B": {"A"}}
        assert count_wire_crossings(positions, adj) == 0

    def test_parallel_wires_no_crossing(self) -> None:
        """Two wires with the same vertical trend do not cross.

        Wire 1: A(0, 0) → B(30, 10)
        Wire 2: C(10, 0) → D(40, 10)

        Left x: 0 < 10.  yl=0 > ycl=0? No.  yr=10 > ycr=10? No. → 0.
        """
        positions = {
            "A": (0.0, 0.0),
            "B": (30.0, 10.0),
            "C": (10.0, 0.0),
            "D": (40.0, 10.0),
        }
        adj: dict[str, set[str]] = {
            "A": {"B"},
            "B": {"A"},
            "C": {"D"},
            "D": {"C"},
        }
        assert count_wire_crossings(positions, adj) == 0

    def test_crossed_wires_returns_one(self) -> None:
        """Two wires that exchange vertical order → count = 1.

        Wire 1: A(0, 0)  → B(40, 10)   left-low, right-high
        Wire 2: C(10, 10) → D(50, 0)   left-high, right-low

        Left x=0 < 10.  yr=10 > ycr=0? Yes → crossings = 1.
        """
        positions = {
            "A": (0.0, 0.0),
            "B": (40.0, 10.0),
            "C": (10.0, 10.0),
            "D": (50.0, 0.0),
        }
        adj: dict[str, set[str]] = {
            "A": {"B"},
            "B": {"A"},
            "C": {"D"},
            "D": {"C"},
        }
        assert count_wire_crossings(positions, adj) == 1

    def test_same_column_edges_excluded(self) -> None:
        """Both edges share left-endpoint x → excluded per spec → 0.

        Even though the endpoints 'cross' geometrically, the spec skips
        same-column pairs because within a column vertical order is
        determined by the row sorter, not by wire crossings.
        """
        positions = {
            "A": (0.0, 0.0),
            "B": (30.0, 10.0),
            "C": (0.0, 10.0),
            "D": (30.0, 0.0),
        }
        adj: dict[str, set[str]] = {
            "A": {"B"},
            "B": {"A"},
            "C": {"D"},
            "D": {"C"},
        }
        assert count_wire_crossings(positions, adj) == 0

    def test_ref_missing_from_positions_skipped(self) -> None:
        """Edges referencing refs absent from *positions* are silently skipped."""
        positions = {"A": (0.0, 0.0)}  # B is intentionally missing
        adj: dict[str, set[str]] = {"A": {"B"}, "B": {"A"}}
        assert count_wire_crossings(positions, adj) == 0

    def test_three_crossing_wires(self) -> None:
        """Three mutually-crossing wires → 3 crossing pairs.

        Wire 1 (A→B): left-low  at x=0, right-low  at x=60
        Wire 2 (C→D): left-mid  at x=10, right-mid at x=50
        Wire 3 (E→F): left-high at x=20, right-low at x=40

        Each pair exchanges vertical order, so all three pairs cross.
        """
        positions = {
            "A": (0.0, 0.0),
            "B": (60.0, 20.0),
            "C": (10.0, 10.0),
            "D": (50.0, 5.0),
            "E": (20.0, 20.0),
            "F": (40.0, 0.0),
        }
        adj: dict[str, set[str]] = {
            "A": {"B"},
            "B": {"A"},
            "C": {"D"},
            "D": {"C"},
            "E": {"F"},
            "F": {"E"},
        }
        assert count_wire_crossings(positions, adj) == 3

    def test_build_signal_adjacency_is_public_wrapper(self) -> None:
        """build_signal_adjacency() delegates to the internal function.

        A two-component IR connected on a non-power net produces a
        symmetric adjacency mapping with both refs present.
        """
        ir = _make_ir(
            [("R1", "Device:R", "10k"), ("R2", "Device:R", "10k")],
            [("SIG", [("R1", "1"), ("R2", "1")])],
        )
        adj = build_signal_adjacency(ir)
        assert "R2" in adj.get("R1", set())
        assert "R1" in adj.get("R2", set())
