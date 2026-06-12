"""Phase 6: bus-style spine routing and LAY lint pipeline tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.lint import LintError
from kicad_pcb.pipeline import ValidationMode, mutate_and_validate_sch
from kicad_pcb.router import _spine_route, route_nets

pytestmark = pytest.mark.unit

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"


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


class TestBusStyleSpineRoute:
    """4.5 — use_bus=True routes multi-pin nets via a straight spine rather than
    a centroid hub.

    Verifies both the internal ``_spine_route`` helper and the public
    ``route_nets(use_bus=True)`` interface.
    """

    def test_spine_horizontal_dominant(self) -> None:
        """Endpoints spread more on X → horizontal spine segment produced."""
        # Endpoints spread 40 mm on X, 20 mm on Y → horizontal dominant.
        endpoints = [(10.0, 50.0), (30.0, 30.0), (50.0, 50.0)]
        segs, junctions = _spine_route(endpoints)
        # Must include at least one horizontal segment (same y, different x).
        horiz = [s for s in segs if abs(s.y1 - s.y2) < 0.1 and abs(s.x1 - s.x2) > 1.0]
        assert horiz, f"Expected a horizontal spine segment; got {segs}"
        # Three T-junction points expected (one per endpoint).
        assert len(junctions) == 3, f"Expected 3 junctions; got {junctions}"

    def test_spine_vertical_dominant(self) -> None:
        """Endpoints spread more on Y → vertical spine segment produced."""
        # Endpoints spread 10 mm on X, 40 mm on Y → vertical dominant.
        endpoints = [(30.0, 10.0), (20.0, 30.0), (30.0, 50.0)]
        segs, junctions = _spine_route(endpoints)
        vert = [s for s in segs if abs(s.x1 - s.x2) < 0.1 and abs(s.y1 - s.y2) > 1.0]
        assert vert, f"Expected a vertical spine segment; got {segs}"
        assert len(junctions) == 3

    def test_route_nets_use_bus_produces_different_topology(self) -> None:
        """route_nets(use_bus=True) and use_bus=False yield different wire sets
        for a 3-pin hub-routeable net."""
        ir = CircuitIR(
            version="test-1.0",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
                ComponentIR(ref="R3", symbol="Device:R", value="1k"),
            ],
            nets=[
                NetIR(
                    name="BUS_NET",
                    pins=[
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="R2", pin="2"),
                        PinRefIR(ref="R3", pin="2"),
                    ],
                ),
            ],
        )
        # Endpoints roughly laid out horizontally.
        pin_endpoints = {
            ("R1", "2"): (10.0, 50.0, 180.0),
            ("R2", "2"): (30.0, 30.0, 180.0),
            ("R3", "2"): (50.0, 50.0, 180.0),
        }
        hub_routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, use_bus=False)
        bus_routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, use_bus=True)
        # Both must wire BUS_NET (non-empty wires and junctions).
        assert hub_routing.wires
        assert bus_routing.wires
        # The topologies must differ — spine has a different segment set.
        hub_wire_set = {(s.x1, s.y1, s.x2, s.y2) for s in hub_routing.wires}
        bus_wire_set = {(s.x1, s.y1, s.x2, s.y2) for s in bus_routing.wires}
        assert hub_wire_set != bus_wire_set, (
            "use_bus=True and use_bus=False produced identical wire segments; "
            "expected different topologies."
        )
        # Bus style should produce junctions (T-intersections on the spine).
        assert bus_routing.junctions, "Bus routing must produce junctions at spine T-intersections."

    def test_route_nets_use_bus_default_true(self) -> None:
        """use_bus defaults to True — spine/bus routing used by default."""
        ir = CircuitIR(
            version="test-1.0",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
                ComponentIR(ref="R3", symbol="Device:R", value="1k"),
            ],
            nets=[
                NetIR(
                    name="HUB_NET",
                    pins=[
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="R2", pin="2"),
                        PinRefIR(ref="R3", pin="2"),
                    ],
                )
            ],
        )
        pin_endpoints = {
            ("R1", "2"): (10.0, 50.0, 180.0),
            ("R2", "2"): (30.0, 30.0, 180.0),
            ("R3", "2"): (50.0, 50.0, 180.0),
        }
        default_routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)
        explicit_bus = route_nets(ir=ir, pin_endpoints=pin_endpoints, use_bus=True)
        assert {(s.x1, s.y1, s.x2, s.y2) for s in default_routing.wires} == {
            (s.x1, s.y1, s.x2, s.y2) for s in explicit_bus.wires
        }, "Default routing should match use_bus=True (spine/bus routing)."


# ---------------------------------------------------------------------------
# 4.6 — LAY lints enforced at --validate (pipeline integration)
# ---------------------------------------------------------------------------


class TestLAYLintsInPipeline:
    """4.6 — mutate_and_validate_sch runs lint_schematic_layout (LAY001–LAY005)
    in LINT mode and above, raising LintError when violations are detected.

    LAY lints have WARNING severity — they block only under ``strict=True``
    (or ``ValidationMode.FULL`` which implies strict).  Using the
    ``headphone_amp_current_layout`` regression fixture which is known to
    trigger LAY001, LAY002, and LAY005.
    """

    _FIXTURE = (
        Path(__file__).parent.parent
        / "fixtures"
        / "regressions"
        / "headphone_amp_current_layout.kicad_sch"
    )

    def test_lay_lints_raise_in_full_mode(self, tmp_path: Path) -> None:
        """FULL mode (strict) raises LintError for schematic with LAY issues."""
        dest = tmp_path / "bad_layout.kicad_sch"
        shutil.copy(self._FIXTURE, dest)

        with pytest.raises(LintError) as exc_info:
            mutate_and_validate_sch(dest, lambda doc: None, mode=ValidationMode.FULL)

        codes = {issue.code for issue in exc_info.value.issues}
        assert codes & {"LAY001", "LAY002", "LAY005"}, (
            f"Expected at least one LAY code in LintError; got codes={codes}"
        )

    def test_lay_lints_raise_in_strict_lint_mode(self, tmp_path: Path) -> None:
        """LINT + strict=True raises LintError for schematic with LAY issues."""
        dest = tmp_path / "bad_layout_strict.kicad_sch"
        shutil.copy(self._FIXTURE, dest)

        with pytest.raises(LintError) as exc_info:
            mutate_and_validate_sch(dest, lambda doc: None, mode=ValidationMode.LINT, strict=True)

        codes = {issue.code for issue in exc_info.value.issues}
        assert codes & {"LAY001", "LAY002", "LAY005"}

    def test_lay_lints_not_raised_in_lint_mode_non_strict(self, tmp_path: Path) -> None:
        """LINT mode without strict keeps LAY issues as warnings (no raise)."""
        dest = tmp_path / "bad_layout_nostrict.kicad_sch"
        shutil.copy(self._FIXTURE, dest)
        # Must NOT raise — LAY lints are WARNING; non-strict LINT tolerates them.
        mutate_and_validate_sch(dest, lambda doc: None, mode=ValidationMode.LINT)

    def test_lay_lints_not_raised_in_syntax_mode(self, tmp_path: Path) -> None:
        """SYNTAX mode skips lint checks entirely — no LintError raised."""
        dest = tmp_path / "bad_layout_syntax.kicad_sch"
        shutil.copy(self._FIXTURE, dest)
        mutate_and_validate_sch(dest, lambda doc: None, mode=ValidationMode.SYNTAX)

    def test_lay_lints_present_in_error_issues(self, tmp_path: Path) -> None:
        """LintError.issues contains individual LAY-coded findings in FULL mode."""
        dest = tmp_path / "bad_layout_issues.kicad_sch"
        shutil.copy(self._FIXTURE, dest)

        with pytest.raises(LintError) as exc_info:
            mutate_and_validate_sch(dest, lambda doc: None, mode=ValidationMode.FULL)

        lay_issues = [i for i in exc_info.value.issues if i.code.startswith("LAY")]
        assert lay_issues, "Expected at least one LAY-coded issue in LintError.issues"
        for issue in lay_issues:
            assert issue.code in {"LAY001", "LAY002", "LAY003", "LAY004", "LAY005"}
            assert issue.message
