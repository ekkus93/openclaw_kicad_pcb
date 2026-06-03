"""Tests for _validate_with_optional_autofix in kicad_pcb_web.services.netlists."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.ir.validate import validate_circuit_ir
from kicad_pcb_web.services.netlists import _validate_with_optional_autofix


def _write_ir(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_ir() -> dict:
    """Minimal valid Circuit IR that passes all validation layers."""
    return {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "Device:R", "value": "10k"},
            {"ref": "R2", "symbol": "Device:R", "value": "10k"},
        ],
        "nets": [
            {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
            {"name": "GND", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "2"}]},
            {"name": "MID", "pins": [{"ref": "R2", "pin": "1"}]},
        ],
    }


def _ir_with_integer_pin() -> dict:
    """IR that autofix can repair (integer pin → string)."""
    return {
        "version": "1",
        "components": [{"ref": "R1", "symbol": "Device:R", "value": "10k"}],
        "nets": [
            {"name": "A", "pins": [{"ref": "R1", "pin": 1}]},
            {"name": "B", "pins": [{"ref": "R1", "pin": 2}]},
        ],
    }


def _ir_with_duplicate_refs() -> dict:
    """IR with duplicate refs — autofix cannot fix this."""
    return {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "Device:R", "value": "10k"},
            {"ref": "R1", "symbol": "Device:R", "value": "1k"},
        ],
        "nets": [{"name": "A", "pins": [{"ref": "R1", "pin": "1"}]}],
    }


# ---------------------------------------------------------------------------
# 4.1 Valid IR — passes through untouched
# ---------------------------------------------------------------------------


def test_valid_ir_passes_with_auto_fix_false(tmp_path: Path) -> None:
    raw = _valid_ir()
    netlist_path = _write_ir(tmp_path / "ir.json", raw)
    _path, _idx, ir, fixes = _validate_with_optional_autofix(
        netlist_path=netlist_path,
        raw_netlist_json=raw,
        symbols_dir=Path("tests/fixtures/symbols"),
        auto_fix=False,
    )
    assert fixes == []
    assert str(_path) == str(netlist_path)


def test_valid_ir_passes_with_auto_fix_true(tmp_path: Path) -> None:
    raw = _valid_ir()
    netlist_path = _write_ir(tmp_path / "ir.json", raw)
    _path, _idx, ir, fixes = _validate_with_optional_autofix(
        netlist_path=netlist_path,
        raw_netlist_json=raw,
        symbols_dir=Path("tests/fixtures/symbols"),
        auto_fix=True,
    )
    assert fixes == []
    assert str(_path) == str(netlist_path)


# ---------------------------------------------------------------------------
# 4.2 auto_fix=False — raises original error immediately
# ---------------------------------------------------------------------------


def test_invalid_ir_raises_with_auto_fix_false(tmp_path: Path) -> None:
    raw = _ir_with_duplicate_refs()
    netlist_path = _write_ir(tmp_path / "ir.json", raw)
    with pytest.raises(UserError) as exc_info:
        _validate_with_optional_autofix(
            netlist_path=netlist_path,
            raw_netlist_json=raw,
            symbols_dir=Path("tests/fixtures/symbols"),
            auto_fix=False,
        )
    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID


# ---------------------------------------------------------------------------
# 4.3 auto_fix=True — autofix corrects the error
# ---------------------------------------------------------------------------


def test_fixable_ir_is_repaired_and_fixes_reported(tmp_path: Path) -> None:
    raw = _ir_with_integer_pin()
    netlist_path = _write_ir(tmp_path / "ir.json", raw)
    _path, _idx, ir, fixes = _validate_with_optional_autofix(
        netlist_path=netlist_path,
        raw_netlist_json=raw,
        symbols_dir=Path("tests/fixtures/symbols"),
        auto_fix=True,
    )
    assert len(fixes) > 0
    assert "autofix" in str(_path)


def test_fixable_ir_returned_ir_is_valid(tmp_path: Path) -> None:
    raw = _ir_with_integer_pin()
    netlist_path = _write_ir(tmp_path / "ir.json", raw)
    _path, _idx, ir, fixes = _validate_with_optional_autofix(
        netlist_path=netlist_path,
        raw_netlist_json=raw,
        symbols_dir=Path("tests/fixtures/symbols"),
        auto_fix=True,
    )
    validate_circuit_ir(ir)  # should not raise


# ---------------------------------------------------------------------------
# 4.4 auto_fix=True — autofix applies nothing → re-raises original error
# ---------------------------------------------------------------------------


def test_unfixable_ir_reraises_original_error(tmp_path: Path) -> None:
    raw = _ir_with_duplicate_refs()
    netlist_path = _write_ir(tmp_path / "ir.json", raw)
    with pytest.raises(UserError) as exc_info:
        _validate_with_optional_autofix(
            netlist_path=netlist_path,
            raw_netlist_json=raw,
            symbols_dir=Path("tests/fixtures/symbols"),
            auto_fix=True,
        )
    # Must be the original error, not a wrapped "auto-fix applied" error
    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
    assert "Auto-fix applied" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4.5 auto_fix=True — autofix changed something but result still invalid
# ---------------------------------------------------------------------------


def test_partial_fix_raises_auto_fix_applied_message(tmp_path: Path) -> None:
    """Autofix changes the dict but the repaired IR still fails validation."""
    # An IR with an integer pin (autofix will fix it) but also a pin collision
    # (autofix cannot fix that) — so autofix applies a fix but final validation fails.
    raw = {
        "version": "1",
        "components": [{"ref": "R1", "symbol": "Device:R", "value": "10k"}],
        "nets": [
            # Integer pin (fixable by autofix)
            {"name": "A", "pins": [{"ref": "R1", "pin": 1}]},
            # Same pin as a collision (not fixable by autofix alone)
            {"name": "B", "pins": [{"ref": "R1", "pin": "1"}]},
        ],
    }
    netlist_path = _write_ir(tmp_path / "ir.json", raw)
    with pytest.raises(UserError) as exc_info:
        _validate_with_optional_autofix(
            netlist_path=netlist_path,
            raw_netlist_json=raw,
            symbols_dir=Path("tests/fixtures/symbols"),
            auto_fix=True,
        )
    assert "Auto-fix applied" in str(exc_info.value)
