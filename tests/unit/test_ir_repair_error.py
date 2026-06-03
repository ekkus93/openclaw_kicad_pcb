"""Tests for _format_ir_repair_error in kicad_pcb_web.services.wizard."""

from __future__ import annotations

from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb_web.services.wizard import _format_ir_repair_error


def _err(message: str, code: str = "IR_SEMANTIC_INVALID", details: object = None) -> UserError:
    return UserError(message, code=code, details=details or {})


# ---------------------------------------------------------------------------
# 2.1 Baseline — message and code always appear
# ---------------------------------------------------------------------------


def test_format_bare_error_contains_message_and_code() -> None:
    exc = _err("Something went wrong", code=ErrorCode.IR_SEMANTIC_INVALID)
    result = _format_ir_repair_error(exc)
    assert "Something went wrong" in result
    assert ErrorCode.IR_SEMANTIC_INVALID in result


def test_format_none_details_does_not_raise() -> None:
    exc = UserError("msg", code=ErrorCode.IR_SEMANTIC_INVALID, details=None)
    result = _format_ir_repair_error(exc)
    assert "msg" in result


def test_format_non_dict_details_does_not_raise() -> None:
    exc = UserError("msg", code=ErrorCode.IR_SEMANTIC_INVALID, details="unexpected string")
    result = _format_ir_repair_error(exc)
    assert "msg" in result


# ---------------------------------------------------------------------------
# 2.2 pin_collisions branch
# ---------------------------------------------------------------------------


def test_format_pin_collision_names_ref_pin_and_nets() -> None:
    exc = _err(
        "A pin appears in multiple nets",
        details={
            "pin_collisions": [
                {"ref": "D1", "pin": "2", "nets": ["GND", "RED_NODE"]},
            ]
        },
    )
    result = _format_ir_repair_error(exc)
    assert "D1" in result
    assert "pin 2" in result
    assert "GND" in result
    assert "RED_NODE" in result


def test_format_pin_collision_instructs_keep_one_net() -> None:
    exc = _err(
        "pin collision",
        details={"pin_collisions": [{"ref": "R1", "pin": "1", "nets": ["A", "B"]}]},
    )
    result = _format_ir_repair_error(exc)
    assert "ONE" in result or "one" in result


def test_format_pin_collision_lists_all_entries() -> None:
    collisions = [{"ref": f"R{i}", "pin": "1", "nets": ["A", "B"]} for i in range(5)]
    exc = _err("collision", details={"pin_collisions": collisions})
    result = _format_ir_repair_error(exc)
    for i in range(5):
        assert f"R{i}" in result


def test_format_pin_collision_truncates_at_12() -> None:
    collisions = [{"ref": f"X{i}", "pin": "1", "nets": ["A", "B"]} for i in range(20)]
    exc = _err("collision", details={"pin_collisions": collisions})
    result = _format_ir_repair_error(exc)
    # First 12 entries should be mentioned, entries 13-20 should not
    assert "X0" in result
    assert "X11" in result
    assert "X19" not in result


def test_format_pin_collision_non_dict_entry_skipped() -> None:
    exc = _err(
        "collision",
        details={"pin_collisions": ["not a dict", {"ref": "R1", "pin": "1", "nets": ["A"]}]},
    )
    # Should not raise; the dict entry is formatted
    result = _format_ir_repair_error(exc)
    assert "R1" in result


# ---------------------------------------------------------------------------
# 2.3 unqualified_symbols branch
# ---------------------------------------------------------------------------


def test_format_unqualified_symbol_names_ref_and_symbol() -> None:
    exc = _err(
        "unqualified",
        details={"unqualified_symbols": [{"ref": "U1", "symbol": "CD4017"}]},
    )
    result = _format_ir_repair_error(exc)
    assert "U1" in result
    assert "CD4017" in result


def test_format_unqualified_symbol_suggests_prefix() -> None:
    exc = _err(
        "unqualified",
        details={"unqualified_symbols": [{"ref": "U1", "symbol": "CD4017"}]},
    )
    result = _format_ir_repair_error(exc)
    assert "library prefix" in result.lower() or "LibraryName" in result or "prefix" in result


def test_format_unqualified_symbol_lists_multiple() -> None:
    exc = _err(
        "unqualified",
        details={
            "unqualified_symbols": [
                {"ref": "U1", "symbol": "CD4017"},
                {"ref": "U2", "symbol": "NE555"},
            ]
        },
    )
    result = _format_ir_repair_error(exc)
    assert "U1" in result
    assert "U2" in result


def test_format_unqualified_symbol_truncates_at_12() -> None:
    entries = [{"ref": f"U{i}", "symbol": f"PART{i}"} for i in range(20)]
    exc = _err("unqualified", details={"unqualified_symbols": entries})
    result = _format_ir_repair_error(exc)
    assert "U0" in result
    assert "U11" in result
    assert "U19" not in result


# ---------------------------------------------------------------------------
# 2.4 duplicate_refs and duplicate_nets branches
# ---------------------------------------------------------------------------


def test_format_duplicate_refs() -> None:
    exc = _err("dup", details={"duplicate_refs": ["R1", "R1"]})
    result = _format_ir_repair_error(exc)
    assert "R1" in result
    assert "Duplicate" in result or "duplicate" in result


def test_format_duplicate_nets() -> None:
    exc = _err("dup nets", details={"duplicate_nets": ["GND", "GND"]})
    result = _format_ir_repair_error(exc)
    assert "GND" in result


def test_format_both_duplicate_refs_and_nets() -> None:
    exc = _err("dup", details={"duplicate_refs": ["R1"], "duplicate_nets": ["VCC"]})
    result = _format_ir_repair_error(exc)
    assert "R1" in result
    assert "VCC" in result


# ---------------------------------------------------------------------------
# 2.5 missing_component_refs branch
# ---------------------------------------------------------------------------


def test_format_missing_ref_names_net_ref_and_pin() -> None:
    exc = _err(
        "missing ref",
        details={"missing_component_refs": [{"net": "NET1", "ref": "U99", "pin": "3"}]},
    )
    result = _format_ir_repair_error(exc)
    assert "NET1" in result
    assert "U99" in result
    assert "3" in result


def test_format_missing_ref_truncates_at_8() -> None:
    entries = [{"net": f"N{i}", "ref": f"U{i}", "pin": "1"} for i in range(15)]
    exc = _err("missing", details={"missing_component_refs": entries})
    result = _format_ir_repair_error(exc)
    assert "U0" in result
    assert "U7" in result
    assert "U14" not in result


def test_format_missing_ref_non_dict_entry_skipped() -> None:
    exc = _err(
        "missing",
        details={"missing_component_refs": ["bad", {"net": "N1", "ref": "U1", "pin": "1"}]},
    )
    result = _format_ir_repair_error(exc)
    assert "U1" in result


# ---------------------------------------------------------------------------
# 2.6 errors (schema-level) branch
# ---------------------------------------------------------------------------


def test_format_schema_errors_with_loc_and_msg() -> None:
    exc = _err(
        "schema",
        details={"errors": [{"loc": ["components", 0, "symbol"], "msg": "field required"}]},
    )
    result = _format_ir_repair_error(exc)
    assert "symbol" in result
    assert "field required" in result


def test_format_schema_errors_without_loc() -> None:
    exc = _err("schema", details={"errors": [{"msg": "value is not valid"}]})
    result = _format_ir_repair_error(exc)
    assert "value is not valid" in result


def test_format_schema_errors_truncated_at_12() -> None:
    entries = [{"loc": [f"field{i}"], "msg": "bad"} for i in range(20)]
    exc = _err("schema", details={"errors": entries})
    result = _format_ir_repair_error(exc)
    assert "field0" in result
    assert "field11" in result
    assert "field19" not in result


def test_format_schema_errors_non_dict_entry_skipped() -> None:
    exc = _err(
        "schema",
        details={"errors": ["not a dict", {"loc": ["x"], "msg": "bad value"}]},
    )
    result = _format_ir_repair_error(exc)
    assert "bad value" in result


# ---------------------------------------------------------------------------
# 2.7 Combined details — multiple sections appear
# ---------------------------------------------------------------------------


def test_format_combined_pin_collision_and_unqualified() -> None:
    exc = _err(
        "combined",
        details={
            "pin_collisions": [{"ref": "D1", "pin": "2", "nets": ["GND", "LED"]}],
            "unqualified_symbols": [{"ref": "U1", "symbol": "CD4017"}],
        },
    )
    result = _format_ir_repair_error(exc)
    assert "D1" in result
    assert "CD4017" in result
