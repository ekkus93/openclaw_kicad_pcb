"""Unit tests for extracted helpers in commands/_sch_apply.py and commands/_validate.py.

Covers:
* ``_transform_pin_at``   — pure coordinate transformation
* ``advisory_warnings``   — non-blocking IR health checks
* ``full_validate``        — 3-layer file-based validation
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply import (
    _expand_generation_ir,
    _PlacedSymbolSpec,
    _resolve_placed_symbol_pin_at,
    _transform_pin_at,
    _write_symbols,
)
from kicad_pcb.commands._validate import advisory_warnings, full_validate
from kicad_pcb.errors import UserError
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode
from kicad_pcb.symbol_index import SymbolIndex
from pytest import approx

pytestmark = pytest.mark.unit

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
_KICAD_SYSTEM_SYMBOLS = Path("/usr/share/kicad/symbols")
_REAL_NE5532_REVIEW_NETLIST = (
    Path(__file__).resolve().parents[2] / "code_review" / "ne5532_headphone_amp_netlist.json"
)


# ---------------------------------------------------------------------------
# _transform_pin_at
# ---------------------------------------------------------------------------


class TestTransformPinAt:
    """_transform_pin_at(pin_at, origin_x, origin_y, rotation) -> transformed map."""

    def test_identity_rotation(self) -> None:
        """rotation=0: result is a pure translation by (origin_x, origin_y)."""
        pin_at: dict[str, tuple[float, float, float]] = {
            "1": (10.0, 20.0, 90.0),
            "2": (-5.0, 3.0, 180.0),
        }
        result = _transform_pin_at(pin_at, 100.0, 200.0, rotation=0)
        assert result == {
            "1": (approx(110.0), approx(220.0), approx(90.0)),
            "2": (approx(95.0), approx(203.0), approx(180.0)),
        }

    def test_rotation_90(self) -> None:
        """90° rotation: (px, py) → (−py, px); angle incremented by 90."""
        # cos(90°) = 0, sin(90°) = 1  ⟹  rx = −py, ry = px
        pin_at: dict[str, tuple[float, float, float]] = {"1": (3.0, 4.0, 45.0)}
        result = _transform_pin_at(pin_at, 0.0, 0.0, rotation=90)
        rx, ry, ra = result["1"]
        assert rx == approx(-4.0, abs=1e-9)
        assert ry == approx(3.0, abs=1e-9)
        assert ra == pytest.approx((45 + 90) % 360)

    def test_rotation_180(self) -> None:
        """180° rotation: (px, py) → (−px, −py); angle incremented by 180."""
        # cos(180°) = −1, sin(180°) = 0  ⟹  rx = −px, ry = −py
        pin_at: dict[str, tuple[float, float, float]] = {"1": (3.0, 4.0, 30.0)}
        result = _transform_pin_at(pin_at, 0.0, 0.0, rotation=180)
        rx, ry, ra = result["1"]
        assert rx == approx(-3.0, abs=1e-9)
        assert ry == approx(-4.0, abs=1e-9)
        assert ra == pytest.approx((30 + 180) % 360)

    def test_angle_wraps_below_360(self) -> None:
        """Resulting angle is always in [0, 360)."""
        pin_at: dict[str, tuple[float, float, float]] = {"1": (0.0, 0.0, 270.0)}
        result = _transform_pin_at(pin_at, 0.0, 0.0, rotation=180)
        angle = result["1"][2]
        assert 0.0 <= angle < 360.0
        assert angle == pytest.approx((270 + 180) % 360)  # 90°

    def test_origin_applied_correctly(self) -> None:
        """Non-zero origin is added *after* the rotation transform."""
        # rotation=90: rx = -py, ry = px; then add origin (10, 20)
        pin_at: dict[str, tuple[float, float, float]] = {"1": (3.0, 4.0, 0.0)}
        result = _transform_pin_at(pin_at, 10.0, 20.0, rotation=90)
        rx, ry, _ = result["1"]
        assert rx == approx(10.0 + (-4.0), abs=1e-9)
        assert ry == approx(20.0 + 3.0, abs=1e-9)

    def test_empty_pin_map(self) -> None:
        """An empty pin map returns an empty dict without error."""
        result = _transform_pin_at({}, 0.0, 0.0, rotation=45)
        assert result == {}

    def test_preserves_all_pins(self) -> None:
        """All entries in the input map appear in the output."""
        pin_at: dict[str, tuple[float, float, float]] = {
            f"{i}": (float(i), float(i), 0.0) for i in range(1, 6)
        }
        result = _transform_pin_at(pin_at, 0.0, 0.0, rotation=0)
        assert set(result.keys()) == set(pin_at.keys())


class TestExpandGenerationIr:
    def test_splits_fixture_dual_op_amp_into_explicit_units(self) -> None:
        ir = _make_ir(
            components=[
                ComponentIR(ref="U1", symbol="TestLib:DualOpAmp", value="DualOpAmp"),
                ComponentIR(ref="R1", symbol="TestLib:R", value="10k"),
                ComponentIR(ref="R2", symbol="TestLib:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="IN_A",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="R1", pin="1")],
                ),
                NetIR(
                    name="OUT_A",
                    pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="R1", pin="2")],
                ),
                NetIR(
                    name="IN_B",
                    pins=[PinRefIR(ref="U1", pin="5"), PinRefIR(ref="R2", pin="1")],
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="R2", pin="2")],
                ),
                NetIR(name="VCC", pins=[PinRefIR(ref="U1", pin="8")]),
                NetIR(name="GND", pins=[PinRefIR(ref="U1", pin="4")]),
            ],
        )

        expanded_ir, placed_symbols = _expand_generation_ir(
            ir,
            SymbolIndex(symbols_dir=_FIXTURES_DIR),
        )

        refs = [component.ref for component in expanded_ir.components]
        assert refs == ["U1A", "U1B", "U1P", "R1", "R2"]
        assert placed_symbols["U1A"].unit == 1
        assert placed_symbols["U1A"].pin_nums == ("1", "2", "3")
        assert placed_symbols["U1B"].unit == 2
        assert placed_symbols["U1B"].pin_nums == ("5", "6", "7")
        assert placed_symbols["U1P"].unit == 3
        assert placed_symbols["U1P"].pin_nums == ("4", "8")

        bindings = {(pin.ref, pin.pin): net.name for net in expanded_ir.nets for pin in net.pins}
        assert bindings[("U1A", "1")] == "IN_A"
        assert bindings[("U1A", "3")] == "OUT_A"
        assert bindings[("U1B", "5")] == "IN_B"
        assert bindings[("U1B", "7")] == "OUT_B"
        assert bindings[("U1P", "4")] == "GND"
        assert bindings[("U1P", "8")] == "VCC"


class TestResolvePlacedSymbolPinAt:
    def test_returns_unit_local_geometry_for_signal_unit(self) -> None:
        pin_at = _resolve_placed_symbol_pin_at(
            "TestLib:DualOpAmp",
            _PlacedSymbolSpec(unit=1, pin_nums=("1", "2", "3")),
            SymbolIndex(symbols_dir=_FIXTURES_DIR),
        )

        assert pin_at == {
            "1": (0.0, 0.0, 0.0),
            "2": (0.0, -2.54, 0.0),
            "3": (5.08, -1.27, 180.0),
        }

    def test_returns_unit_local_geometry_for_power_unit(self) -> None:
        pin_at = _resolve_placed_symbol_pin_at(
            "TestLib:DualOpAmp",
            _PlacedSymbolSpec(unit=3, pin_nums=("4", "8")),
            SymbolIndex(symbols_dir=_FIXTURES_DIR),
        )

        assert pin_at == {
            "4": (2.54, 2.54, 270.0),
            "8": (2.54, -5.08, 90.0),
        }


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
            ) -> dict[str, tuple[float, float, float]]:
                return {"U1A": (10.0, 20.0, 0.0)}

        _positions, pin_endpoints, pin_anchors, _missing, _raw_layout = _write_symbols(
            doc=doc,
            ir=ir,
            symbol_index=SymbolIndex(symbols_dir=_FIXTURES_DIR),
            placed_symbol_specs={"U1A": _PlacedSymbolSpec(unit=1, pin_nums=("1", "2", "3"))},
            project_name="test",
            stats=stats,
            engine=_StaticLayoutEngine(),
        )

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
    return sorted(
        (
            warning["code"],
            tuple(sorted((warning["details"] or {}).items()))
            if isinstance(warning.get("details"), dict)
            else (),
        )
        for warning in warnings
    )


_skip_no_system_symbols = pytest.mark.skipif(
    not (_KICAD_SYSTEM_SYMBOLS / "Amplifier_Operational.kicad_sym").exists(),
    reason="KiCad system symbol libraries not installed at /usr/share/kicad/symbols",
)


class TestPhase1WarningSuite:
    @pytest.mark.parametrize(
        ("ir", "expected_codes"),
        [
            (
                _make_ir(
                    components=[
                        ComponentIR(ref="J1", symbol="Lib:J"),
                        ComponentIR(ref="C5", symbol="Device:C"),
                        ComponentIR(ref="R1", symbol="Device:R"),
                        ComponentIR(ref="RV1", symbol="Lib:P"),
                    ],
                    nets=[
                        NetIR(
                            name="LEFT_IN",
                            pins=[
                                PinRefIR(ref="J1", pin="1"),
                                PinRefIR(ref="C5", pin="1"),
                                PinRefIR(ref="R1", pin="1"),
                            ],
                        ),
                        NetIR(
                            name="IN_L_AC",
                            pins=[
                                PinRefIR(ref="C5", pin="2"),
                                PinRefIR(ref="R1", pin="2"),
                                PinRefIR(ref="RV1", pin="1"),
                            ],
                        ),
                    ],
                ),
                {"INPUT_COUPLING_BYPASSED_BY_RESISTOR"},
            ),
            (
                _make_ir(
                    components=[
                        ComponentIR(ref="U1", symbol="Lib:U"),
                        ComponentIR(ref="C7", symbol="Device:C"),
                        ComponentIR(ref="R8", symbol="Device:R"),
                        ComponentIR(ref="J2", symbol="Lib:J"),
                    ],
                    nets=[
                        NetIR(
                            name="OUT_L_STAGE2_RAW",
                            pins=[
                                PinRefIR(ref="U1", pin="1"),
                                PinRefIR(ref="C7", pin="1"),
                                PinRefIR(ref="R8", pin="1"),
                            ],
                        ),
                        NetIR(
                            name="HP_L_OUT",
                            pins=[
                                PinRefIR(ref="C7", pin="2"),
                                PinRefIR(ref="R8", pin="2"),
                                PinRefIR(ref="J2", pin="1"),
                            ],
                        ),
                    ],
                ),
                {"OUTPUT_COUPLING_BYPASSED_BY_RESISTOR"},
            ),
            (
                _make_ir(
                    components=[ComponentIR(ref="J1", symbol="TestLib:Conn3")],
                    nets=[
                        NetIR(name="IN", pins=[PinRefIR(ref="J1", pin="1")]),
                        NetIR(name="GND", pins=[PinRefIR(ref="J1", pin="2")]),
                    ],
                ),
                {"CONNECTOR_UNUSED_PINS_AMBIGUOUS"},
            ),
            (
                _make_ir(
                    components=[
                        ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp"),
                        ComponentIR(ref="R1", symbol="TestLib:R"),
                        ComponentIR(ref="J1", symbol="TestLib:R"),
                    ],
                    nets=[
                        NetIR(
                            name="VIN",
                            pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="R1", pin="1")],
                        ),
                        NetIR(
                            name="U1_INV",
                            pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="R1", pin="2")],
                        ),
                        NetIR(
                            name="U1_OUT",
                            pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="J1", pin="1")],
                        ),
                    ],
                ),
                {"OPAMP_FEEDBACK_MISSING_OR_NONLOCAL"},
            ),
            (
                _make_ir(
                    components=[
                        ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp"),
                        ComponentIR(ref="R1", symbol="TestLib:R"),
                    ],
                    nets=[
                        NetIR(
                            name="VIN",
                            pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="R1", pin="1")],
                        ),
                        NetIR(
                            name="U1_INV",
                            pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="R1", pin="2")],
                        ),
                    ],
                ),
                {"OPAMP_OUTPUT_FLOATING"},
            ),
            (
                _make_ir(
                    components=[
                        ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp"),
                        ComponentIR(ref="R1", symbol="TestLib:R"),
                        ComponentIR(ref="P1", symbol="TestLib:R"),
                    ],
                    nets=[
                        NetIR(
                            name="VIN",
                            pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="R1", pin="1")],
                        ),
                        NetIR(
                            name="U1_INV",
                            pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="R1", pin="2")],
                        ),
                        NetIR(
                            name="VPLUS15",
                            pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="P1", pin="1")],
                        ),
                        NetIR(name="BIAS", pins=[PinRefIR(ref="P1", pin="2")]),
                    ],
                ),
                {"OPAMP_OUTPUT_SHORTED_TO_RAIL"},
            ),
        ],
        ids=[
            "input-coupling",
            "output-coupling",
            "connector-ambiguity",
            "missing-feedback",
            "output-floating",
            "output-shorted-to-rail",
        ],
    )
    def test_synthetic_warning_fixtures_cover_each_phase1_family(
        self,
        ir: CircuitIR,
        expected_codes: set[str],
    ) -> None:
        codes = {
            warning["code"]
            for warning in advisory_warnings(ir, SymbolIndex(symbols_dir=_FIXTURES_DIR))
        }
        assert expected_codes <= codes

    @_skip_no_system_symbols
    def test_real_ne5532_fixture_warning_set_does_not_drift(self) -> None:
        ir = CircuitIR.load(_REAL_NE5532_REVIEW_NETLIST)
        warnings = advisory_warnings(ir, SymbolIndex(symbols_dir=_KICAD_SYSTEM_SYMBOLS))

        assert _normalize_warning_entries(warnings) == [
            (
                "CONNECTOR_UNUSED_PINS_AMBIGUOUS",
                (
                    ("ref", "J1"),
                    ("symbol", "Connector:AudioJack3"),
                    ("unused_pins", ["R"]),
                    ("used_pins", ["S", "T"]),
                ),
            ),
            (
                "CONNECTOR_UNUSED_PINS_AMBIGUOUS",
                (
                    ("ref", "J2"),
                    ("symbol", "Connector:AudioJack3"),
                    ("unused_pins", ["R"]),
                    ("used_pins", ["S", "T"]),
                ),
            ),
            (
                "INPUT_COUPLING_BYPASSED_BY_RESISTOR",
                (
                    ("bridge_component_refs", ["C5", "R1"]),
                    ("capacitor_refs", ["C5"]),
                    ("nets", ["IN_L_AC", "LEFT_IN"]),
                    ("resistor_refs", ["R1"]),
                ),
            ),
        ]

    def test_feedback_warning_not_emitted_for_local_feedback_bridge(self) -> None:
        """A direct output-to-inverting-input feedback bridge should not warn."""
        ir = _make_ir(
            components=[
                ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp"),
                ComponentIR(ref="R1", symbol="TestLib:R"),
                ComponentIR(ref="R2", symbol="TestLib:R"),
            ],
            nets=[
                NetIR(
                    name="VIN",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="R1", pin="1")],
                ),
                NetIR(
                    name="U1_INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="R2", pin="1"),
                    ],
                ),
                NetIR(
                    name="U1_OUT",
                    pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="R2", pin="2")],
                ),
            ],
        )

        codes = {
            warning["code"]
            for warning in advisory_warnings(ir, SymbolIndex(symbols_dir=_FIXTURES_DIR))
        }
        assert "OPAMP_FEEDBACK_MISSING_OR_NONLOCAL" not in codes
        assert "OPAMP_OUTPUT_FLOATING" not in codes


# ---------------------------------------------------------------------------
# full_validate
# ---------------------------------------------------------------------------


class TestFullValidate:
    """full_validate(path, symbol_index) -> CircuitIR."""

    def _write_ir(self, path: Path, data: object) -> Path:
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_passes_valid_ir(self, tmp_path: Path) -> None:
        """A well-formed IR JSON passes all three layers and returns a CircuitIR."""
        data = {
            "version": "1",
            "components": [{"ref": "R1", "symbol": "Device:R"}],
            "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]}],
        }
        p = self._write_ir(tmp_path / "valid.json", data)
        ir = full_validate(p, SymbolIndex(symbols_dir=None))
        assert len(ir.components) == 1
        assert ir.components[0].ref == "R1"

    def test_raises_on_schema_error(self, tmp_path: Path) -> None:
        """A file missing required fields raises UserError at schema layer."""
        # Missing 'components' and 'nets' → Pydantic validation error
        data = {"version": "1"}
        p = self._write_ir(tmp_path / "schema_err.json", data)
        with pytest.raises(UserError):
            full_validate(p, SymbolIndex(symbols_dir=None))

    def test_raises_on_semantic_error(self, tmp_path: Path) -> None:
        """A net referencing an undefined component ref raises UserError at layer 2."""
        data = {
            "version": "1",
            "components": [{"ref": "R1", "symbol": "Device:R"}],
            "nets": [
                {
                    "name": "N1",
                    "pins": [
                        {"ref": "R1", "pin": "1"},
                        {"ref": "UNDEFINED", "pin": "2"},  # unknown component
                    ],
                }
            ],
        }
        p = self._write_ir(tmp_path / "semantic_err.json", data)
        with pytest.raises(UserError):
            full_validate(p, SymbolIndex(symbols_dir=None))

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        """A corrupt JSON file raises UserError (not a bare json.JSONDecodeError)."""
        p = tmp_path / "corrupt.json"
        p.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(UserError):
            full_validate(p, SymbolIndex(symbols_dir=None))
