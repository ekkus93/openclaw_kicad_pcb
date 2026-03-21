"""Unit tests for kicad_pcb.tier — classify_connector_roles and assign_tiers.

Covers Rule 0 changes:
  - R0-1: _classify_connector_roles correctly labels input / output / unknown
  - R0-2: assign_tiers forces connectors to tier 0 / max_tier
"""

from __future__ import annotations

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.tier import assign_tiers, classify_connector_roles

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ir(
    components: list[tuple[str, str, str]],
    nets: list[tuple[str, list[tuple[str, str]]]],
) -> CircuitIR:
    """Build a minimal CircuitIR from lightweight tuples.

    Parameters
    ----------
    components:
        List of ``(ref, symbol, value)`` triples.
    nets:
        List of ``(net_name, [(ref, pin), ...])`` pairs.
    """
    return CircuitIR(
        version="1",
        components=[ComponentIR(ref=ref, symbol=sym, value=val) for ref, sym, val in components],
        nets=[
            NetIR(
                name=name,
                pins=[PinRefIR(ref=ref, pin=pin, unit=None) for ref, pin in pins],
            )
            for name, pins in nets
        ],
        options=None,
    )


# ---------------------------------------------------------------------------
# Tests: _classify_connector_roles
# ---------------------------------------------------------------------------


class TestClassifyConnectorRoles:
    def test_simple_chain_input_and_output(self) -> None:
        """J2 → R1 → U1 → J1: J2 is input (tier 0), J1 is output (max tier)."""
        ir = _make_ir(
            components=[
                ("J1", "Connector", "Jack"),
                ("J2", "Connector", "Jack"),
                ("R1", "R", "10k"),
                ("U1", "TL072", "TL072"),
            ],
            nets=[
                ("IN", [("J2", "1"), ("R1", "1")]),
                ("MID", [("R1", "2"), ("U1", "2")]),
                ("OUT", [("U1", "6"), ("J1", "1")]),
            ],
        )
        tiers = assign_tiers(ir)
        roles = classify_connector_roles(list(tiers.keys()), tiers)

        assert roles.get("J2") == "input", f"Expected J2=input, got {roles}"
        assert roles.get("J1") == "output", f"Expected J1=output, got {roles}"

    def test_no_connectors_returns_empty(self) -> None:
        """A circuit with only passives and ICs has no connector roles."""
        ir = _make_ir(
            components=[
                ("R1", "R", "10k"),
                ("C1", "C", "100n"),
                ("U1", "TL072", "TL072"),
            ],
            nets=[
                ("SIG", [("R1", "1"), ("U1", "2")]),
                ("FB", [("U1", "6"), ("R1", "2")]),
                ("GND", [("C1", "2")]),
            ],
        )
        tiers = assign_tiers(ir)
        roles = classify_connector_roles(list(tiers.keys()), tiers)

        # No connector prefixes in refs → empty dict
        assert roles == {}

    def test_single_connector_is_input(self) -> None:
        """A lone connector with nothing downstream is classified as input (tier 0)."""
        ir = _make_ir(
            components=[
                ("J1", "Connector", "Jack"),
                ("R1", "R", "10k"),
            ],
            nets=[
                ("SIG", [("J1", "1"), ("R1", "1")]),
            ],
        )
        tiers = assign_tiers(ir)
        roles = classify_connector_roles(list(tiers.keys()), tiers)

        assert roles.get("J1") == "input"

    def test_intermediate_connector_is_unknown(self) -> None:
        """A connector at an intermediate tier (between other connectors) is unknown."""
        ir = _make_ir(
            components=[
                ("J1", "Connector", "In"),
                ("J2", "Connector", "Mid"),
                ("J3", "Connector", "Out"),
                ("R1", "R", "1k"),
                ("R2", "R", "1k"),
            ],
            nets=[
                ("N1", [("J1", "1"), ("R1", "1")]),
                ("N2", [("R1", "2"), ("J2", "1")]),
                ("N3", [("J2", "2"), ("R2", "1")]),
                ("N4", [("R2", "2"), ("J3", "1")]),
            ],
        )
        tiers = assign_tiers(ir)
        roles = classify_connector_roles(list(tiers.keys()), tiers)

        # J1 must be input, J3 must be output, J2 in the middle must be unknown
        assert roles.get("J1") == "input", f"J1 roles: {roles}"
        assert roles.get("J3") == "output", f"J3 roles: {roles}"
        assert roles.get("J2") == "unknown", f"J2 roles: {roles}"

    def test_ir_hints_classify_output_and_power_connectors(self) -> None:
        """IR metadata should upgrade obvious output and power connectors."""
        ir = _make_ir(
            components=[
                ("J1", "Connector:AudioJack3", "3.5mm TRS IN"),
                ("J2", "Connector:AudioJack3", "3.5mm TRS OUT"),
                ("J3", "Connector_Generic:Conn_01x03", "+15V / 0V / -15V"),
                ("U1", "Amplifier_Operational:NE5532", "NE5532"),
                ("R1", "R", "10k"),
                ("C1", "C", "10u"),
            ],
            nets=[
                ("LEFT_IN", [("J1", "T"), ("R1", "1")]),
                ("MID", [("R1", "2"), ("U1", "3")]),
                ("HP_L_OUT", [("U1", "1"), ("C1", "1"), ("J2", "T")]),
                ("0V", [("J1", "S"), ("J2", "S"), ("J3", "2"), ("C1", "2")]),
                ("VPLUS15", [("J3", "1"), ("U1", "8")]),
                ("VMINUS15", [("J3", "3"), ("U1", "4")]),
            ],
        )

        tiers = assign_tiers(ir)
        roles = classify_connector_roles(list(tiers.keys()), tiers, ir=ir)

        assert roles.get("J1") == "input", f"Expected J1=input, got {roles}"
        assert roles.get("J2") == "output", f"Expected J2=output, got {roles}"
        assert roles.get("J3") == "power", f"Expected J3=power, got {roles}"

    def test_ir_hints_treat_extended_shared_rail_aliases_as_power(self) -> None:
        """Connector-only shared aliases such as VPOS/AVEE should classify as power."""
        ir = _make_ir(
            components=[
                ("J3", "Connector_Generic:Conn_01x03", "+15V / 0V / -15V"),
                ("U1", "Amplifier_Operational:NE5532", "NE5532"),
            ],
            nets=[
                ("VPOS15", [("J3", "1"), ("U1", "8")]),
                ("0V", [("J3", "2")]),
                ("AVEE15", [("J3", "3"), ("U1", "4")]),
            ],
        )

        tiers = assign_tiers(ir)
        roles = classify_connector_roles(list(tiers.keys()), tiers, ir=ir)

        assert roles.get("J3") == "power", f"Expected J3=power, got {roles}"


# ---------------------------------------------------------------------------
# Tests: assign_tiers (Rule 0 tier-forcing)
# ---------------------------------------------------------------------------


class TestAssignTiersForcing:
    def test_output_connector_reaches_max_tier(self) -> None:
        """assign_tiers must place the output connector at the maximum tier."""
        ir = _make_ir(
            components=[
                ("J1", "Connector", "Out"),
                ("J2", "Connector", "In"),
                ("R1", "R", "10k"),
                ("U1", "TL072", "TL072"),
            ],
            nets=[
                ("IN", [("J2", "1"), ("R1", "1")]),
                ("MID", [("R1", "2"), ("U1", "2")]),
                ("OUT", [("U1", "6"), ("J1", "1")]),
            ],
        )
        tiers = assign_tiers(ir)

        # All refs must be present
        assert "J1" in tiers
        assert "J2" in tiers

        max_tier = max(tiers.values())
        assert tiers["J1"] == max_tier, (
            f"Output connector J1 should be at max_tier={max_tier}, got {tiers['J1']}"
        )
        assert tiers["J2"] == 0, f"Input connector J2 should be at tier 0, got {tiers['J2']}"

    def test_assign_tiers_no_connectors_unchanged(self) -> None:
        """assign_tiers with no connectors produces the same result as raw DP."""
        ir = _make_ir(
            components=[
                ("R1", "R", "10k"),
                ("R2", "R", "10k"),
                ("U1", "TL072", "TL072"),
            ],
            nets=[
                ("A", [("R1", "1"), ("U1", "2")]),
                ("B", [("U1", "6"), ("R2", "1")]),
            ],
        )
        # Should not raise; result should be a valid tier dict
        tiers = assign_tiers(ir)
        assert set(tiers.keys()) == {"R1", "R2", "U1"}
        assert all(isinstance(v, int) and v >= 0 for v in tiers.values())
