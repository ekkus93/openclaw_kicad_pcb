"""Unit tests for kicad_pcb.lint.sch.lint_layout_wire_crossings() (LAY007 — R6-3)."""

from __future__ import annotations

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.lint import LINT_SUGGESTIONS, lint_layout_wire_crossings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ir(
    components: list[tuple[str, str, str]],
    nets: list[tuple[str, list[tuple[str, str]]]],
) -> CircuitIR:
    """Build a minimal CircuitIR for testing.

    *components*: ``[(ref, symbol, value), ...]``
    *nets*: ``[(net_name, [(ref, pin), ...]), ...]``
    """
    comps = [ComponentIR(ref=ref, symbol=sym, value=val) for ref, sym, val in components]
    net_objs: list[NetIR] = []
    for net_name, pins in nets:
        pin_objs = [PinRefIR(ref=r, pin=p, unit=None) for r, p in pins]
        net_objs.append(NetIR(name=net_name, pins=pin_objs))
    return CircuitIR(version="1", components=comps, nets=net_objs)


# ---------------------------------------------------------------------------
# LAY007 tests
# ---------------------------------------------------------------------------


class TestLintLayoutWireCrossings:
    """Tests for lint_layout_wire_crossings() — LAY007 (R6-3)."""

    def test_lay007_suggestion_registered(self) -> None:
        """LAY007 must have a suggestion string in LINT_SUGGESTIONS."""
        assert "LAY007" in LINT_SUGGESTIONS
        assert len(LINT_SUGGESTIONS["LAY007"]) > 10

    def test_no_issues_when_no_signal_nets(self) -> None:
        """IR with only isolated (single-component) nets → adjacency empty → no LAY007."""
        # Each net has only one component pin, so _build_signal_adjacency
        # produces no edges.  total_wires == 0 triggers the early-return path.
        ir = _make_ir(
            [("R1", "Device:R", "1k"), ("R2", "Device:R", "1k")],
            [("SOLO", [("R1", "1")]), ("SOLO2", [("R2", "1")])],
        )
        positions: dict[str, tuple[float, float]] = {"R1": (0.0, 0.0), "R2": (30.0, 0.0)}
        issues = lint_layout_wire_crossings(positions, ir)
        assert issues == []

    def test_no_issues_when_crossing_ratio_at_or_below_50_percent(self) -> None:
        """Two signal wires, one crossing → ratio == 50 % (not > 50 %) → no LAY007.

        Wire 1: A(0,0) → B(40,10)
        Wire 2: C(10,10) → D(50,0)

        These cross once.  total_wires=2, crossings=1, ratio=50 % which is
        *not* strictly greater than 50 %, so LAY007 must not fire.
        """
        ir = _make_ir(
            [
                ("A", "Device:R", "1k"),
                ("B", "Device:R", "1k"),
                ("C", "Device:R", "1k"),
                ("D", "Device:R", "1k"),
            ],
            [
                ("SIG_AB", [("A", "1"), ("B", "1")]),
                ("SIG_CD", [("C", "1"), ("D", "1")]),
            ],
        )
        positions: dict[str, tuple[float, float]] = {
            "A": (0.0, 0.0),
            "B": (40.0, 10.0),
            "C": (10.0, 10.0),
            "D": (50.0, 0.0),
        }
        issues = lint_layout_wire_crossings(positions, ir)
        assert all(i.code != "LAY007" for i in issues), (
            f"Expected no LAY007 at 50 % ratio; got: {issues}"
        )

    def test_lay007_fires_when_crossing_ratio_above_50_percent(self) -> None:
        """Three signal wires, three crossings → ratio 100 % > 50 % → LAY007 fires.

        Wire 1 (A→B): left(0, 0)   right(60, 20)
        Wire 2 (C→D): left(10, 10) right(50, 5)
        Wire 3 (E→F): left(20, 20) right(40, 0)

        All three pairs exchange vertical order → 3 crossings, 3 wires,
        ratio = 100 % > 50 %.
        """
        ir = _make_ir(
            [
                ("A", "Device:R", "1k"),
                ("B", "Device:R", "1k"),
                ("C", "Device:R", "1k"),
                ("D", "Device:R", "1k"),
                ("E", "Device:R", "1k"),
                ("F", "Device:R", "1k"),
            ],
            [
                ("SIG_AB", [("A", "1"), ("B", "1")]),
                ("SIG_CD", [("C", "1"), ("D", "1")]),
                ("SIG_EF", [("E", "1"), ("F", "1")]),
            ],
        )
        positions: dict[str, tuple[float, float]] = {
            "A": (0.0, 0.0),
            "B": (60.0, 20.0),
            "C": (10.0, 10.0),
            "D": (50.0, 5.0),
            "E": (20.0, 20.0),
            "F": (40.0, 0.0),
        }
        issues = lint_layout_wire_crossings(positions, ir)
        lay007 = [i for i in issues if i.code == "LAY007"]
        assert len(lay007) == 1, f"Expected exactly one LAY007; got: {issues}"
        assert lay007[0].severity.name == "WARNING"
        assert "crossing" in lay007[0].message.lower()

    def test_lay007_absent_for_parallel_layout(self) -> None:
        """Three co-directional (non-crossing) wires → no LAY007.

        Wire 1: A(0,0)  → B(30,0)
        Wire 2: C(10,0) → D(40,0)    (shifted right, same row)
        Wire 3: E(20,0) → F(50,0)

        No vertical order exchange → 0 crossings, ratio = 0 % → no issue.
        """
        ir = _make_ir(
            [
                ("A", "Device:R", "1k"),
                ("B", "Device:R", "1k"),
                ("C", "Device:R", "1k"),
                ("D", "Device:R", "1k"),
                ("E", "Device:R", "1k"),
                ("F", "Device:R", "1k"),
            ],
            [
                ("SIG_AB", [("A", "1"), ("B", "1")]),
                ("SIG_CD", [("C", "1"), ("D", "1")]),
                ("SIG_EF", [("E", "1"), ("F", "1")]),
            ],
        )
        positions: dict[str, tuple[float, float]] = {
            "A": (0.0, 0.0),
            "B": (30.0, 0.0),
            "C": (10.0, 0.0),
            "D": (40.0, 0.0),
            "E": (20.0, 0.0),
            "F": (50.0, 0.0),
        }
        issues = lint_layout_wire_crossings(positions, ir)
        assert all(i.code != "LAY007" for i in issues), (
            f"Expected no LAY007 for parallel layout; got: {issues}"
        )
