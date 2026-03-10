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
from kicad_pcb.commands._sch_apply import _transform_pin_at
from kicad_pcb.commands._validate import advisory_warnings, full_validate
from kicad_pcb.errors import UserError
from kicad_pcb.symbol_index import SymbolIndex
from pytest import approx

pytestmark = pytest.mark.unit


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
