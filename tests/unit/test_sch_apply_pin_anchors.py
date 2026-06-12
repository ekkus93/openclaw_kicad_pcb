"""Unit tests for _write_symbols pin anchors and advisory_warnings."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pytest import approx

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply import (
    _PlacedSymbolSpec,
    _write_symbols,
)
from kicad_pcb.commands._validate import advisory_warnings
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode
from kicad_pcb.symbol_index import SymbolIndex

pytestmark = pytest.mark.unit

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
_KICAD_SYSTEM_SYMBOLS = Path("/usr/share/kicad/symbols")


class TestWriteSymbolsPinAnchors:
    def test_returns_pin_anchors_with_placed_unit_metadata(self) -> None:
        root = parse(
            "(kicad_sch (version 20230121) (generator eeschema) "
            '(uuid "00000000-0000-0000-0000-000000000001") '
            '(paper "A4"))\n'
        )
        assert isinstance(root, ListNode)
        doc = SchematicDoc(root)
        ir = CircuitIR(
            version="1",
            components=[ComponentIR(ref="U1A", symbol="TestLib:DualOpAmp", value="DualOpAmp")],
            nets=[NetIR(name="IN_A", pins=[PinRefIR(ref="U1A", pin="1")])],
        )
        stats = {
            "symbols": 0,
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "junctions": 0,
            "binding_markers": 0,
        }

        class _StaticLayoutEngine:
            def compute_symbol_positions(
                self,
                _ir: CircuitIR,
            ) -> dict[str, tuple[float, float, float | None]]:
                return {"U1A": (10.0, 20.0, 0.0)}

        _positions, pin_endpoints, pin_anchors, _missing, _raw_layout = _write_symbols(
            doc=doc,
            ir=ir,
            symbol_index=SymbolIndex(symbols_dir=_FIXTURES_DIR),
            placed_symbol_specs={
                "U1A": _PlacedSymbolSpec(unit=1, pin_nums=("1", "2", "3"), logical_ref="U1")
            },
            project_name="test",
            stats=stats,
            engine=_StaticLayoutEngine(),
        )

        assert [symbol["ref"] for symbol in doc.list_symbols()] == ["U1A"]
        assert pin_anchors[("U1A", "1")].unit == 1
        assert pin_anchors[("U1A", "1")].ref == "U1A"
        assert pin_anchors[("U1A", "1")].pin == "1"
        assert (
            pin_anchors[("U1A", "1")].x,
            pin_anchors[("U1A", "1")].y,
            pin_anchors[("U1A", "1")].angle,
        ) == approx(pin_endpoints[("U1A", "1")])


# ---------------------------------------------------------------------------
# advisory_warnings
# ---------------------------------------------------------------------------


class TestAdvisoryWarnings:
    """advisory_warnings(ir) returns a list of non-blocking warning dicts."""

    def _make_valid_ir(
        self,
        *,
        components: list[ComponentIR],
        nets: list[NetIR],
    ) -> CircuitIR:
        return CircuitIR(version="1", components=components, nets=nets)

    def test_no_warnings_for_clean_ir(self) -> None:
        """All components in at least one net, all nets multi-pin → empty list."""
        ir = self._make_valid_ir(
            components=[
                ComponentIR(ref="U1", symbol="Lib:X"),
                ComponentIR(ref="U2", symbol="Lib:Y"),
            ],
            nets=[
                NetIR(
                    name="N1",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="U2", pin="2")],
                ),
            ],
        )
        assert advisory_warnings(ir) == []

    def test_tca9555_is_not_misclassified_as_timer555(self) -> None:
        ir = self._make_valid_ir(
            components=[
                ComponentIR(
                    ref="U2",
                    symbol="Interface_Expansion:TCA9555DBT",
                    value="TCA9555DBT",
                )
            ],
            nets=[
                NetIR(
                    name="I2C",
                    pins=[PinRefIR(ref="U2", pin="1"), PinRefIR(ref="U2", pin="2")],
                )
            ],
        )

        codes = {warning["code"] for warning in advisory_warnings(ir)}
        assert not {code for code in codes if code.startswith("TIMER555_")}

    def test_component_not_in_any_net(self) -> None:
        """A component absent from all nets triggers COMPONENT_NOT_IN_ANY_NET."""
        ir = self._make_valid_ir(
            components=[
                ComponentIR(ref="U1", symbol="Lib:X"),
                ComponentIR(ref="U2", symbol="Lib:Y"),  # floating
            ],
            nets=[
                NetIR(
                    name="N1",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="U1", pin="2")],
                ),
            ],
        )
        warnings = advisory_warnings(ir)
        codes = [w["code"] for w in warnings]
        assert "COMPONENT_NOT_IN_ANY_NET" in codes

        found = next(w for w in warnings if w["code"] == "COMPONENT_NOT_IN_ANY_NET")
        assert isinstance(found["details"], dict)
        assert "U2" in found["details"]["refs"]

    def test_single_pin_net(self) -> None:
        """A net with exactly one connected pin triggers SINGLE_PIN_NET."""
        ir = self._make_valid_ir(
            components=[ComponentIR(ref="U1", symbol="Lib:X")],
            nets=[NetIR(name="DANGLING", pins=[PinRefIR(ref="U1", pin="1")])],
        )
        warnings = advisory_warnings(ir)
        codes = [w["code"] for w in warnings]
        assert "SINGLE_PIN_NET" in codes

        found = next(w for w in warnings if w["code"] == "SINGLE_PIN_NET")
        details = found["details"]
        assert isinstance(details, dict)
        assert "DANGLING" in details["nets"]

    def test_both_warnings_independent(self) -> None:
        """Both COMPONENT_NOT_IN_ANY_NET and SINGLE_PIN_NET can fire together."""
        ir = self._make_valid_ir(
            components=[
                ComponentIR(ref="U1", symbol="Lib:X"),
                ComponentIR(ref="U2", symbol="Lib:Y"),  # floating
            ],
            nets=[
                # U2 not in any net; U1 lone pin → single-pin net
                NetIR(name="LONE", pins=[PinRefIR(ref="U1", pin="1")])
            ],
        )
        codes = {w["code"] for w in advisory_warnings(ir)}
        assert "COMPONENT_NOT_IN_ANY_NET" in codes
        assert "SINGLE_PIN_NET" in codes


def _make_ir(
    *,
    components: list[ComponentIR],
    nets: list[NetIR],
) -> CircuitIR:
    return CircuitIR(version="1", components=components, nets=nets)


def _normalize_warning_entries(
    warnings: list[dict[str, object]],
) -> list[tuple[str, tuple[tuple[str, object], ...]]]:
    normalized: list[tuple[str, tuple[tuple[str, object], ...]]] = []
    for warning in warnings:
        code = warning.get("code")
        if not isinstance(code, str):
            continue
        details_obj = warning.get("details")
        details = cast(dict[str, object], details_obj) if isinstance(details_obj, dict) else {}
        normalized.append((code, tuple(sorted(details.items()))))
    return sorted(normalized)


_skip_no_system_symbols = pytest.mark.skipif(
    not (_KICAD_SYSTEM_SYMBOLS / "Amplifier_Operational.kicad_sym").exists(),
    reason="KiCad system symbol libraries not installed at /usr/share/kicad/symbols",
)

_skip_no_real_ne5532_fixture_symbols = pytest.mark.skipif(
    not all(
        (
            _FIXTURES_DIR / "Amplifier_Operational.kicad_sym",
            _FIXTURES_DIR / "Connector.kicad_sym",
            _FIXTURES_DIR / "Device.kicad_sym",
            _FIXTURES_DIR / "Connector_Generic.kicad_sym",
        )
    ),
    reason="Portable NE5532 regression symbol fixtures are missing",
)
