from __future__ import annotations

import json
import math
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands._sch_apply import _cleanup_new_managed_file
from kicad_pcb.commands.netlist import (
    cmd_apply_netlist,
    cmd_fix_netlist,
    cmd_info_sch,
    cmd_new_from_netlist,
    resolve_schematic_paths,
)
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.layout import compute_orientations
from kicad_pcb.models import ProjectRef
from kicad_pcb.sch_doc import SchematicDoc, read_lib_symbol_pin_at
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.utils import find_first, walk


def _write_minimal_sch(path: Path) -> None:
    path.write_text(
        """(kicad_sch (version 20230121) (generator eeschema)
  (uuid "12345678-1234-1234-1234-123456789012")
  (paper "A4")
  (lib_symbols)
  (sheet_instances
    (path "/" (page "1"))
  )
)
""",
        encoding="utf-8",
    )


def _write_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
        "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_fix_netlist_raises_when_symbol_index_init_fails(tmp_path: Path, monkeypatch) -> None:
    """A5: fix-netlist must fail fast if SymbolIndex construction fails."""
    netlist_path = tmp_path / "ir.json"
    _write_ir(netlist_path)
    output_path = tmp_path / "ir.fixed.json"

    class _BrokenSymbolIndex:
        def __init__(
            self,
            *,
            symbols_dir: Path | None = None,
            fallback_dirs: list[Path] | None = None,
        ) -> None:
            del symbols_dir, fallback_dirs
            raise UserError(
                "symbol index init failed",
                code=ErrorCode.SYMBOL_DIR_MISSING,
            )

    monkeypatch.setattr("kicad_pcb.commands.netlist.SymbolIndex", _BrokenSymbolIndex)

    with pytest.raises(UserError) as exc_info:
        cmd_fix_netlist(
            Namespace(
                netlist=str(netlist_path),
                symbols_dir=str(tmp_path / "symbols"),
                output=str(output_path),
            )
        )

    assert exc_info.value.code == ErrorCode.SYMBOL_DIR_MISSING
    assert not output_path.exists()


# ---------------------------------------------------------------------------
# P0 — resolve_schematic_paths helper
# ---------------------------------------------------------------------------


def test_resolve_schematic_paths_returns_both_paths(tmp_path: Path) -> None:
    """P0: resolve_schematic_paths returns root and managed paths for a project."""
    project = ProjectRef(name="myproj", path=tmp_path, created=datetime.now().isoformat())

    root_sch, managed_sch = resolve_schematic_paths(project)

    assert root_sch == project.sch_file
    assert managed_sch == tmp_path / "OpenClaw_Managed.kicad_sch"
    # Paths are deterministic and do not need to exist on disk
    assert root_sch.name == "myproj.kicad_sch"
    assert managed_sch.name == "OpenClaw_Managed.kicad_sch"


def test_cmd_apply_netlist_creates_managed_schematic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "ir.json"
    _write_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    result = cmd_apply_netlist(
        Namespace(
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
            force=True,
            dry_run=False,
        )
    )

    assert result.symbols_added == 1
    assert result.nets_applied == 1
    assert result.managed_schematic_path.exists()
    assert result.symbols_dirs_used  # non-empty tuple of resolved dirs

    root_doc = SchematicDoc.load(sch_path)
    assert root_doc.has_openclaw_marker() is True
    assert root_doc.has_managed_sheet(sheet_name="OpenClaw_Managed") is True

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    symbols = managed_doc.list_symbols()
    assert len(symbols) == 1
    assert symbols[0]["ref"] == "R1"


def test_cmd_new_from_netlist_creates_project_and_applies(tmp_path: Path) -> None:
    ir_path = tmp_path / "ir.json"
    _write_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="NetlistProj",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    assert result.path.exists()
    assert result.schematic_path.exists()
    assert result.managed_schematic_path.exists()
    assert result.symbols_added == 1
    assert result.nets_applied == 1


# ---------------------------------------------------------------------------
# P7.2 — Integration test: new-from-netlist in internal mode
# ---------------------------------------------------------------------------


def test_new_from_netlist_schematic_parses_and_ownership_marker_present(
    tmp_path: Path,
) -> None:
    """P7.2: the generated schematic must parse cleanly and carry ownership markers."""
    ir_path = tmp_path / "ir.json"
    _write_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="P72Proj",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    # Root schematic must exist and parse without exception.
    root_doc = SchematicDoc.load(result.schematic_path)
    assert root_doc is not None

    # Root must carry OpenClaw ownership marker.
    assert root_doc.has_openclaw_marker() is True

    # Root must reference the managed sheet.
    assert root_doc.has_managed_sheet(sheet_name="OpenClaw_Managed") is True

    # Managed sheet must exist, parse, and contain the generated component.
    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    symbols = managed_doc.list_symbols()
    refs = [s["ref"] for s in symbols]
    assert "R1" in refs


def test_new_from_netlist_info_sch_returns_owned_and_symbols(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """P7.2: info-sch for generated projects returns ownership, symbols, and pin bindings."""
    ir_path = tmp_path / "ir.json"
    _write_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="P72InfoProj",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    project = ProjectRef(
        name=result.name,
        path=result.path,
        created=datetime.now().isoformat(),
    )
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    info = cmd_info_sch(Namespace())
    assert info.owned_by_openclaw is True
    assert len(info.symbols) == 1
    assert info.symbols[0]["ref"] == "R1"
    assert info.pin_net_bindings == ({"ref": "R1", "pin": "1", "net_name": "N1"},)
    assert info.schematic_path == result.schematic_path
    # P5/P7 new fields
    assert info.managed_schematic_path is not None
    assert info.managed_symbol_count >= 1
    assert info.managed_label_count >= 1
    assert info.symbol_count == 0  # root is thin (no placed symbols)


# ---------------------------------------------------------------------------
# P7.4 — Idempotency test (structural/semantic)
# ---------------------------------------------------------------------------


def _extract_managed_model(managed_path: Path) -> tuple[list[str], list[str]]:
    """Return normalized (sorted refs, sorted symbol_ids) for the managed sheet."""
    doc = SchematicDoc.load(managed_path)
    symbols = doc.list_symbols()
    refs: list[str] = sorted(cast(str, s["ref"]) for s in symbols)
    symbol_ids: list[str] = sorted(cast(str, s["symbol_id"]) for s in symbols)
    return refs, symbol_ids


def test_apply_netlist_idempotent_apply_twice(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """P7.4: applying the same IR twice yields the same managed region."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "ir.json"
    _write_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    args = Namespace(
        netlist=str(ir_path),
        symbols_dir=str(fixtures_dir),
        mode="internal",
        force=True,
        dry_run=False,
    )

    result1 = cmd_apply_netlist(args)
    model1 = _extract_managed_model(result1.managed_schematic_path)

    result2 = cmd_apply_netlist(args)
    model2 = _extract_managed_model(result2.managed_schematic_path)

    # Structural idempotency: same refs and symbol IDs after two applications.
    assert model1 == model2

    # No duplicates: after two applications the ref list should be unique.
    refs, _ = model2
    assert len(refs) == len(set(refs))


def test_new_from_netlist_idempotency_via_two_projects(tmp_path: Path) -> None:
    """P7.4: two new-from-netlist calls with the same IR produce equivalent managed regions."""
    ir_path = tmp_path / "ir.json"
    _write_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    args_a = Namespace(
        name="Idem_A",
        out_dir=str(tmp_path),
        description="",
        netlist=str(ir_path),
        symbols_dir=str(fixtures_dir),
        mode="internal",
    )
    args_b = Namespace(
        name="Idem_B",
        out_dir=str(tmp_path),
        description="",
        netlist=str(ir_path),
        symbols_dir=str(fixtures_dir),
        mode="internal",
    )

    result_a = cmd_new_from_netlist(args_a)
    result_b = cmd_new_from_netlist(args_b)

    model_a = _extract_managed_model(result_a.managed_schematic_path)
    model_b = _extract_managed_model(result_b.managed_schematic_path)

    assert model_a == model_b


# ---------------------------------------------------------------------------
# P6.4 — Regression: empty generation triggers EMPTY_GENERATION error
# ---------------------------------------------------------------------------


def test_empty_generation_invariant_raises_coded_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """P6.4: if the IR is non-empty but no symbols are placed, EMPTY_GENERATION is raised.

    Simulates the bug path: _write_symbols runs without error (symbol found in
    fixture lib) but add_symbol is a no-op, leaving the managed AST empty.
    The post-mutation invariant must then raise UserError(EMPTY_GENERATION).
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "ir.json"
    _write_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    # Patch add_symbol to a no-op so the AST stays empty while the rest of the
    # pipeline (pin resolution, position tracking, wire/label writing) still runs.
    monkeypatch.setattr(SchematicDoc, "add_symbol", lambda *args, **kwargs: None)

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    with pytest.raises(UserError) as exc_info:
        cmd_apply_netlist(
            Namespace(
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
                mode="internal",
                force=True,
                dry_run=False,
            )
        )

    assert exc_info.value.code == ErrorCode.EMPTY_GENERATION
    assert "expected_components" in exc_info.value.details
    assert "found_symbols" in exc_info.value.details
    assert exc_info.value.details["found_symbols"] == 0


# ---------------------------------------------------------------------------
# P1.2 — DRY_RUN_NO_WRITE warning
# ---------------------------------------------------------------------------


def test_apply_netlist_dry_run_emits_no_write_warning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """P1.2: dry-run must include DRY_RUN_NO_WRITE warning; managed schematic unchanged."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")

    # Pre-create the managed schematic so _ensure_managed_file_exists doesn't
    # raise when called with dry_run=True (it only creates the file non-dry-run).
    managed_sch_path = project_dir / "OpenClaw_Managed.kicad_sch"
    _write_minimal_sch(managed_sch_path)
    content_before = managed_sch_path.read_text(encoding="utf-8")

    ir_path = project_dir / "ir.json"
    _write_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_apply_netlist(
        Namespace(
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
            force=True,
            dry_run=True,
        )
    )

    assert result.dry_run is True

    warning_codes = [w["code"] for w in result.warnings]
    assert "DRY_RUN_NO_WRITE" in warning_codes, f"Expected DRY_RUN_NO_WRITE in {warning_codes}"

    no_write_w = next(w for w in result.warnings if w["code"] == "DRY_RUN_NO_WRITE")
    details_no_write = no_write_w["details"]
    assert isinstance(details_no_write, dict)
    assert "symbols_validated" in details_no_write
    assert details_no_write["symbols_validated"] == 1
    assert "nets_validated" in details_no_write

    # Pipeline must NOT have modified the managed schematic file in dry-run mode.
    assert managed_sch_path.read_text(encoding="utf-8") == content_before


def test_apply_netlist_cleanup_suppresses_race_unlink_error_and_reraises_original(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Cleanup suppresses race-like missing-file unlink errors, then re-raises original failure."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "ir.json"
    _write_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("apply exploded")

    monkeypatch.setattr("kicad_pcb.commands._sch_apply.mutate_and_validate_sch", _boom)

    path_cls = type(project_dir)
    original_unlink = path_cls.unlink

    def _unlink_race(self: Path, *, missing_ok: bool = False):
        if self.name == "OpenClaw_Managed.kicad_sch":
            raise FileNotFoundError("simulated concurrent delete")
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(path_cls, "unlink", _unlink_race)

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    with pytest.raises(RuntimeError, match="apply exploded") as exc_info:
        cmd_apply_netlist(
            Namespace(
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
                mode="internal",
                force=True,
                dry_run=False,
            )
        )

    assert getattr(exc_info.value, "__notes__", []) == []


def test_cleanup_new_managed_file_non_race_error_is_noted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Non-race unlink failures are attached to the original exception as notes."""
    managed_sch = tmp_path / "OpenClaw_Managed.kicad_sch"
    managed_sch.write_text("(kicad_sch)", encoding="utf-8")
    original_error = RuntimeError("apply exploded")

    path_cls = type(managed_sch)
    original_unlink = path_cls.unlink

    def _unlink_permission(self: Path, *, missing_ok: bool = False):
        if self.name == "OpenClaw_Managed.kicad_sch":
            raise PermissionError("simulated permission denied")
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(path_cls, "unlink", _unlink_permission)

    _cleanup_new_managed_file(managed_sch, original_error)

    notes = getattr(original_error, "__notes__", [])
    cleanup_note = getattr(original_error, "cleanup_note", "")
    assert any("Managed-sheet cleanup failed" in note for note in notes) or (
        "Managed-sheet cleanup failed" in cleanup_note
    )


def test_apply_netlist_requires_at_least_80_percent_components_placed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Fail when fewer than 80% of IR components are placed into the managed sheet."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "ir.json"

    payload = {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "1k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "2k"},
            {"ref": "R3", "symbol": "TestLib:R", "value": "3k"},
            {"ref": "R4", "symbol": "TestLib:R", "value": "4k"},
            {"ref": "R5", "symbol": "TestLib:R", "value": "5k"},
        ],
        "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]}],
    }
    ir_path.write_text(json.dumps(payload), encoding="utf-8")

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    original_add_symbol = SchematicDoc.add_symbol

    def _add_symbol_with_drops(self, symbol, ref, *args, **kwargs):
        if ref in {"R4", "R5"}:
            return None
        return original_add_symbol(self, symbol, ref, *args, **kwargs)

    monkeypatch.setattr(SchematicDoc, "add_symbol", _add_symbol_with_drops)

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    with pytest.raises(UserError) as exc_info:
        cmd_apply_netlist(
            Namespace(
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
                mode="internal",
                force=True,
                dry_run=False,
            )
        )

    assert exc_info.value.code == ErrorCode.EMPTY_GENERATION
    assert exc_info.value.details["expected_components"] == 5
    assert exc_info.value.details["found_symbols"] == 3
    assert exc_info.value.details["min_component_placement_ratio"] == 0.8
    assert exc_info.value.details["min_required_symbols"] == 4


# ---------------------------------------------------------------------------
# Extends chain — lib_symbols embedding, pin correctness, net binding
#
# These tests guard against the NE5532 regression where (extends "BaseName")
# caused three failures:
#   1. Only the derived node was embedded — base absent → KiCad blank box.
#   2. read_lib_symbol_pins returned [] → fallback to ["1","2"] → wrong wiring.
#   3. validate_ir_symbols raised SYMBOL_NOT_FOUND for all derived symbols.
# ---------------------------------------------------------------------------


def _get_lib_symbol_ids(doc: SchematicDoc) -> list[str]:
    """Return sorted list of symbol IDs embedded in (lib_symbols)."""
    lib_syms = find_first(doc.root, "lib_symbols")
    if lib_syms is None:
        return []
    ids: list[str] = []
    for item in lib_syms.items:
        if (
            isinstance(item, ListNode)
            and item.key == "symbol"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ):
            ids.append(item.items[1].value)
    return sorted(ids)


def _get_instance_pin_numbers(doc: SchematicDoc, ref: str) -> list[str]:
    """Return sorted pin numbers declared on the placed symbol instance for *ref*."""
    pins: list[str] = []
    for item in doc.root.items:
        if not (isinstance(item, ListNode) and item.key == "symbol"):
            continue
        # Match instance by Reference property value.
        instance_ref: str | None = None
        for child in item.items:
            if (
                isinstance(child, ListNode)
                and child.key == "property"
                and len(child.items) >= 3
                and isinstance(child.items[1], StringNode)
                and child.items[1].value == "Reference"
                and isinstance(child.items[2], StringNode)
            ):
                instance_ref = child.items[2].value
        if instance_ref != ref:
            continue
        for child in item.items:
            if (
                isinstance(child, ListNode)
                and child.key == "pin"
                and len(child.items) >= 2
                and isinstance(child.items[1], StringNode)
            ):
                pins.append(child.items[1].value)
    return sorted(pins)


def test_extends_symbol_embeds_flat_derived_in_lib_symbols(tmp_path: Path) -> None:
    """Extends chain: only the derived symbol is embedded, as a fully flat node.

    The flattening fix (read_lib_symbol_def_flat) merges the parent's geometry
    sub-symbols into the derived node, renames them, and removes the
    (extends ...) attribute.  Only the derived node is written to lib_symbols —
    KiCad can render it without needing the base symbol separately.

    Regression guard for the NE5532 bug: the old code embedded only the derived
    node WITHOUT parent geometry → KiCad renders a blank box with no pins.
    The current code merges parent geometry in so KiCad sees a complete symbol
    with no unresolved extends reference.
    """
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
                ],
                "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1"}]}],
            }
        ),
        encoding="utf-8",
    )
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="ExtendsEmbed",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    embedded_ids = _get_lib_symbol_ids(managed_doc)

    # Only the derived symbol is embedded — the base was merged in, not kept separately.
    assert "TestLib:DerivedOpAmp" in embedded_ids, (
        f"Derived 'TestLib:DerivedOpAmp' missing from lib_symbols; got: {embedded_ids}"
    )
    assert "TestLib:OpAmp" not in embedded_ids, (
        f"Base 'TestLib:OpAmp' should not be embedded separately (geometry was merged "
        f"into DerivedOpAmp by read_lib_symbol_def_flat); got: {embedded_ids}"
    )

    # The embedded derived symbol must be flat — no (extends ...) attribute.
    lib_syms = find_first(managed_doc.root, "lib_symbols")
    assert lib_syms is not None
    derived_node = next(
        (
            item
            for item in lib_syms.items
            if isinstance(item, ListNode)
            and item.key == "symbol"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "TestLib:DerivedOpAmp"
        ),
        None,
    )
    assert derived_node is not None
    extends_found = any(isinstance(c, ListNode) and c.key == "extends" for c in derived_node.items)
    assert not extends_found, (
        "Flattened derived symbol still contains (extends ...) — "
        "read_lib_symbol_def_flat should have removed it"
    )


def test_extends_symbol_instance_carries_all_inherited_pins(tmp_path: Path) -> None:
    """Extends chain: the placed instance must declare all pins inherited from the base.

    With the old code, read_lib_symbol_pins returned [] for a derived symbol,
    causing the placed instance to record no pins (or a wrong two-pin fallback).
    The result was a schematic where U1 had no net connections and was
    electrically wrong even though it opened without errors in KiCad.
    """
    # DerivedOpAmp inherits pins 1, 2, 3, 6 from OpAmp in TestLib.kicad_sym.
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
                ],
                "nets": [{"name": "IN_P", "pins": [{"ref": "U1", "pin": "1"}]}],
            }
        ),
        encoding="utf-8",
    )
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="ExtendsPins",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    pin_numbers = _get_instance_pin_numbers(managed_doc, "U1")

    # Old broken code produced [] or ["1", "2"]; correct code gives all four.
    assert pin_numbers == ["1", "2", "3", "6"], (
        f"Expected inherited pins ['1','2','3','6']; got {pin_numbers}"
    )


def test_extends_symbol_nets_on_inherited_pins_validate_and_bind(tmp_path: Path) -> None:
    """Extends chain: nets on inherited pins must pass validation and produce bindings.

    Pin '6' exists only on the base OpAmp — not declared on DerivedOpAmp directly.
    The old code raised SYMBOL_NOT_FOUND before writing anything because pin
    resolution returned [] for the derived symbol.  The fixed code must:
    1) accept pin '6' as valid for DerivedOpAmp during IR validation,
    2) record an OpenClaw:bind= marker for each net connection.
    """
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
                ],
                "nets": [
                    {"name": "IN_P", "pins": [{"ref": "U1", "pin": "1"}]},
                    {"name": "IN_N", "pins": [{"ref": "U1", "pin": "2"}]},
                    {"name": "OUT", "pins": [{"ref": "U1", "pin": "6"}]},  # only on base OpAmp
                ],
            }
        ),
        encoding="utf-8",
    )
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="ExtendsNets",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    assert result.nets_applied == 3

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    bindings = managed_doc.extract_pin_label_bindings()
    bound_pins = {b["pin"] for b in bindings if b["ref"] == "U1"}
    net_by_pin = {b["pin"]: b["net_name"] for b in bindings if b["ref"] == "U1"}

    # All three nets must be bound; inherited pin '6' is the critical one.
    assert "1" in bound_pins, f"Pin '1' not bound; bindings: {bindings}"
    assert "2" in bound_pins, f"Pin '2' not bound; bindings: {bindings}"
    assert "6" in bound_pins, (
        f"Pin '6' (inherited from base OpAmp) not bound; bound_pins: {bound_pins}"
    )
    assert net_by_pin["6"] == "OUT", f"Pin '6' bound to wrong net: {net_by_pin}"


def test_broken_extends_chain_raises_symbol_has_no_pins(tmp_path: Path) -> None:
    """Broken extends chain must abort with SYMBOL_HAS_NO_PINS before writing anything.

    A symbol that IS declared in the library file but whose extends base does
    not exist resolves to 0 pins.  The error must be SYMBOL_HAS_NO_PINS (not
    SYMBOL_NOT_FOUND) to distinguish "symbol present but broken" from "symbol
    absent entirely".
    """
    # Library with a derived symbol whose base is intentionally absent.
    broken_lib = tmp_path / "BrokenLib.kicad_sym"
    broken_lib.write_text(
        """\
(kicad_symbol_lib (version 20230121) (generator test)
  (symbol "Orphan" (extends "NonExistentBase")
    (property "Reference" "U" (at 0 5.08 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "Orphan" (at 0 -5.08 0)
      (effects (font (size 1.27 1.27)))
    )
  )
)
""",
        encoding="utf-8",
    )
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [{"ref": "U1", "symbol": "BrokenLib:Orphan", "value": "Orphan"}],
                "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1"}]}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(UserError) as exc_info:
        cmd_new_from_netlist(
            Namespace(
                name="BrokenChainProj",
                out_dir=str(tmp_path),
                description="",
                netlist=str(ir_path),
                symbols_dir=str(tmp_path),
                mode="internal",
            )
        )

    # Broken chain → symbol in file but 0 pins → SYMBOL_HAS_NO_PINS.
    assert exc_info.value.code == ErrorCode.SYMBOL_HAS_NO_PINS
    # Error details must identify the offending symbol.
    assert "BrokenLib:Orphan" in str(exc_info.value)


def test_apply_netlist_aborts_on_invalid_pin_ref(tmp_path: Path) -> None:
    """Invalid pin reference must abort with PIN_INVALID before writing anything.

    `TestLib:R` only has pins '1' and '2'.  Referencing non-existent pin '99'
    must raise at `validate_ir_symbols` time — before the managed schematic is
    created or mutated — so partial / corrupt output is never written to disk.
    """
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
                "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "99"}]}],
            }
        ),
        encoding="utf-8",
    )
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    managed_sch = tmp_path / "InvalidPinProj" / "OpenClaw_Managed.kicad_sch"

    with pytest.raises(UserError) as exc_info:
        cmd_new_from_netlist(
            Namespace(
                name="InvalidPinProj",
                out_dir=str(tmp_path),
                description="",
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
                mode="internal",
            )
        )

    assert exc_info.value.code == ErrorCode.PIN_INVALID
    assert "99" in str(exc_info.value)
    assert "TestLib:R" in str(exc_info.value)
    # Confirm the managed schematic was NOT written (preflight fired pre-write).
    assert not managed_sch.exists(), (
        "Managed schematic must not be created when preflight validation fails"
    )


# ---------------------------------------------------------------------------
# Circuit fidelity — general helper + tests
#
# These tests answer the core question: "Given a Circuit IR, does the
# generated schematic actually represent that circuit?"
#
# Checks performed by _check_circuit_fidelity:
#   1. Every component ref in the IR is present as a placed symbol instance.
#   2. Every (ref, pin, net_name) triple in the IR has a matching
#      OpenClaw:bind= marker in the managed schematic — meaning the tool
#      recorded the net connection for that specific pin.
#
# This is stricter than the EMPTY_GENERATION guard: a schematic could have
# the right number of symbols but wire them to wrong nets, or omit a pin.
# ---------------------------------------------------------------------------

# Path to the system KiCad symbol libraries (installed by kicad package).
_KICAD_SYSTEM_SYMBOLS = Path("/usr/share/kicad/symbols")

_skip_no_system_symbols = pytest.mark.skipif(
    not (_KICAD_SYSTEM_SYMBOLS / "Amplifier_Operational.kicad_sym").exists(),
    reason="KiCad system symbol libraries not installed at /usr/share/kicad/symbols",
)


def _check_circuit_fidelity(ir_data: dict, managed_doc: SchematicDoc) -> None:
    """Assert that *managed_doc* faithfully represents *ir_data*.

    Raises ``AssertionError`` with a descriptive message on the first mismatch.

    Parameters
    ----------
    ir_data:
        Parsed Circuit IR dict (``version``, ``components``, ``nets`` keys).
    managed_doc:
        The generated managed schematic loaded as a :class:`SchematicDoc`.
    """
    # --- component placement check ---
    placed_refs = {str(s["ref"]) for s in managed_doc.list_symbols()}
    for component in ir_data["components"]:
        ref = component["ref"]
        assert ref in placed_refs, (
            f"Component {ref!r} (symbol {component['symbol']!r}) "
            f"is missing from the generated schematic. "
            f"Placed refs: {sorted(placed_refs)}"
        )

    # --- net binding check ---
    # Build a lookup: (ref, pin) → net_name from the generated binding markers.
    binding_index: dict[tuple[str, str], str] = {
        (b["ref"], b["pin"]): b["net_name"] for b in managed_doc.extract_pin_label_bindings()
    }
    for net in ir_data["nets"]:
        net_name = net["name"]
        for pin_ref in net["pins"]:
            key = (pin_ref["ref"], pin_ref["pin"])
            assert key in binding_index, (
                f"No OpenClaw:bind= marker found for {pin_ref['ref']} pin {pin_ref['pin']!r} "
                f"(expected net {net_name!r}). "
                f"Bindings present: {sorted(binding_index.keys())}"
            )
            actual_net = binding_index[key]
            assert actual_net == net_name, (
                f"{pin_ref['ref']} pin {pin_ref['pin']!r}: "
                f"expected net {net_name!r} but schematic records {actual_net!r}"
            )


def test_circuit_fidelity_multi_component_testlib(tmp_path: Path) -> None:
    """Circuit fidelity: a 3-component, 4-net circuit with TestLib symbols.

    Uses R, OpAmp (flat), and DerivedOpAmp (extends OpAmp) together.
    Verifies that every component is placed and every net/pin binding is
    recorded correctly — including pins inherited by DerivedOpAmp.

    This test runs without system KiCad libraries and is always executed in CI.
    """
    ir_data = {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "22k"},
            {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
        ],
        "nets": [
            # IN+ (pin 1 of DerivedOpAmp, inherited from OpAmp) through R1
            {"name": "IN_P", "pins": [{"ref": "U1", "pin": "1"}, {"ref": "R1", "pin": "1"}]},
            # IN- (pin 2, inherited) through R2
            {"name": "IN_N", "pins": [{"ref": "U1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
            # Feedback: OUT (pin 6, inherited) back to IN- via R2
            {"name": "OUT", "pins": [{"ref": "U1", "pin": "6"}, {"ref": "R2", "pin": "2"}]},
            # Input bias
            {"name": "GND", "pins": [{"ref": "R1", "pin": "2"}]},
        ],
    }
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="FidelityTestLib",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    assert result.symbols_added == 3
    assert result.nets_applied == 4

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    _check_circuit_fidelity(ir_data, managed_doc)


def test_wires_connect_at_pin_endpoints(tmp_path: Path) -> None:  # noqa: PLR0912
    """P1: wires in the managed schematic start at the actual library pin endpoints.

    Before the P1 fix, _write_nets used arbitrary symbol-relative offsets
    (sym_x + 5.08, sym_y + 2.54*index) regardless of which pin was being
    wired.  After the fix, each wire must start at the exact (x, y) derived
    from the pin's ``(at X Y angle)`` in the library, translated by the
    symbol placement position.

    Circuit: R1 and R2 in series (VCC→R1→MID→R2→GND).
    TestLib:R pin positions:
      pin 1 at (at 0 0 0)   → endpoint at symbol_origin + (0, 0)
      pin 2 at (at 5.08 0 180) → endpoint at symbol_origin + (5.08, 0)
    """
    ir_data = {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "4.7k"},
        ],
        "nets": [
            {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
            {"name": "MID", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
            {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]},
        ],
    }
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="WireTest",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    # Collect all wire start points from the AST.
    wire_starts: set[tuple[float, float]] = set()
    for node in walk(managed_doc.root):
        if not (isinstance(node, ListNode) and node.key == "wire"):
            continue
        pts = find_first(node, "pts")
        if pts is None:
            continue
        # items: [atom("pts"), ListNode("xy", x1, y1), ListNode("xy", x2, y2)]
        xy1 = pts.items[1]
        if isinstance(xy1, ListNode) and xy1.key == "xy" and len(xy1.items) >= 3:
            try:
                x = round(float(xy1.items[1].value), 2)  # type: ignore[union-attr]
                y = round(float(xy1.items[2].value), 2)  # type: ignore[union-attr]
                wire_starts.add((x, y))
            except (ValueError, AttributeError):
                pass

    # Compute expected pin endpoints from the ACTUAL symbol positions in the
    # generated schematic, applying the same rotation logic as _write_symbols.
    pin_at = read_lib_symbol_pin_at("TestLib", "R", symbols_dir=fixtures_dir)
    assert pin_at, "TestLib:R pin positions not found in fixture library"

    ir = CircuitIR.load(ir_path)
    layout: dict[str, tuple[float, float]] = {
        str(sym["ref"]): (cast(float, sym["x"]), cast(float, sym["y"]))
        for sym in managed_doc.list_symbols()
    }
    orientations = compute_orientations(ir, layout)

    expected_endpoints: dict[tuple[str, str], tuple[float, float]] = {}
    for sym in managed_doc.list_symbols():
        ref = str(sym["ref"])
        sx, sy = cast(float, sym["x"]), cast(float, sym["y"])
        rotation = orientations.get(ref, 0)
        if rotation == 0:
            for pin_num, (px, py, _pa) in pin_at.items():
                expected_endpoints[(ref, pin_num)] = (round(sx + px, 2), round(sy + py, 2))
        else:
            theta = math.radians(rotation)
            cos_t = math.cos(theta)
            sin_t = math.sin(theta)
            for pin_num, (px, py, _pa) in pin_at.items():
                rpx = cos_t * px - sin_t * py
                rpy = sin_t * px + cos_t * py
                expected_endpoints[(ref, pin_num)] = (round(sx + rpx, 2), round(sy + rpy, 2))

    # Verify every expected pin endpoint has a wire starting there.
    # Skip power symbols (#PWR* refs) — they are placed at stub ends and do
    # not need outgoing wires of their own.
    missing: list[str] = []
    for (ref, pin), (ex, ey) in sorted(expected_endpoints.items()):
        if ref.startswith("#"):
            continue  # power symbol — no outgoing wire expected
        if (ex, ey) not in wire_starts:
            missing.append(f"{ref} pin {pin}: expected wire start at ({ex}, {ey})")

    assert not missing, (
        "Wire(s) do not start at pin endpoints — wiring is disconnected:\n"
        + "\n".join(f"  {m}" for m in missing)
        + f"\nActual wire starts: {sorted(wire_starts)}"
    )


def test_direct_wiring_not_all_label_only(tmp_path: Path) -> None:
    """Router: a 2-pin net within routing range must be wired directly, not via label.

    A two-resistor voltage-divider (VCC→R1→MID→R2→GND) has three nets:
     - VCC  (power)  → gets a power:VCC symbol (Phase 3 strategy)
     - MID  (2-pin)  → R1-pin2 and R2-pin1 are adjacent-tier (tier distance=1)
                       and within the 200 mm manhattan cap; router must emit
                       an L-shaped wire, NOT a net label
     - GND  (power)  → gets a power:GND symbol (Phase 3 strategy)

    Assertions
    ----------
    1. No ``(label "MID" …)`` node exists in the managed schematic.
    2. At least 5 wire segments are present (4 pin stubs + ≥1 L-route bridge) —
       a direct-wire bridge was actually generated between R1 and R2.
    3. ``power:VCC`` and ``power:GND`` symbol instances exist (Phase 3) —
       power net routing uses symbols, not global labels.
    """
    ir_path = tmp_path / "divider.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
                    {"ref": "R2", "symbol": "TestLib:R", "value": "4.7k"},
                ],
                "nets": [
                    {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
                    {
                        "name": "MID",
                        "pins": [
                            {"ref": "R1", "pin": "2"},
                            {"ref": "R2", "pin": "1"},
                        ],
                    },
                    {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]},
                ],
            }
        ),
        encoding="utf-8",
    )

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    result = cmd_new_from_netlist(
        Namespace(
            name="DirectWireTest",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )
    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    # Collect all net-label text values from the schematic AST.
    label_names: set[str] = set()
    for node in walk(managed_doc.root):
        if (
            isinstance(node, ListNode)
            and node.key == "label"
            and len(node.items) >= 2  # noqa: PLR2004
            and isinstance(node.items[1], StringNode)
        ):
            label_names.add(node.items[1].value)

    # Assertion 1: MID must NOT appear as a label — it must be directly wired.
    assert "MID" not in label_names, (
        f"Net 'MID' found as a schematic label; expected direct wire routing. "
        f"All labels present: {sorted(label_names)}"
    )

    # Assertion 2: a direct-wire bridge between R1 and R2 must have been emitted.
    # For direct routing the router adds 2 stubs per MID pin + 1–2 L-route bridge
    # segments.  For only stub fallback it would have added a local net label for
    # MID (caught by assertion 1).  We verify that at least one bridge wire
    # exists in addition to the 4 pin-stub wires (VCC, GND, R1-pin2, R2-pin1).
    all_wire_segments: list[tuple[float, float, float, float]] = []
    for node in walk(managed_doc.root):
        if not (isinstance(node, ListNode) and node.key == "wire"):
            continue
        pts = find_first(node, "pts")
        if pts is None or len(pts.items) < 3:  # noqa: PLR2004
            continue
        xy1, xy2 = pts.items[1], pts.items[2]
        if isinstance(xy1, ListNode) and isinstance(xy2, ListNode):
            try:
                x1 = float(xy1.items[1].value)  # type: ignore[union-attr]
                y1 = float(xy1.items[2].value)  # type: ignore[union-attr]
                x2 = float(xy2.items[1].value)  # type: ignore[union-attr]
                y2 = float(xy2.items[2].value)  # type: ignore[union-attr]
                all_wire_segments.append((x1, y1, x2, y2))
            except (ValueError, AttributeError, IndexError):
                pass

    # 4 stub wires (VCC stub, GND stub, R1-pin2 stub, R2-pin1 stub) + at least
    # one L-route bridge = minimum 5 wire segments for a direct-wire routing.
    assert len(all_wire_segments) >= 5, (  # noqa: PLR2004
        f"Expected ≥5 wire segments for direct-wire routing; found {len(all_wire_segments)}. "
        "The router may not have emitted an L-route bridge between R1 and R2."
    )

    # Assertion 3 (Phase 3): Single-pin power nets must get power symbol nodes
    # (power:VCC / power:GND), not local labels or global labels.
    power_lib_ids: set[str] = set()
    for node in walk(managed_doc.root):
        if not (isinstance(node, ListNode) and node.key == "symbol"):
            continue
        lib_id_node = find_first(node, "lib_id")
        if (
            lib_id_node is not None
            and len(lib_id_node.items) >= 2  # noqa: PLR2004
            and isinstance(lib_id_node.items[1], StringNode)
        ):
            power_lib_ids.add(lib_id_node.items[1].value)
    assert "power:VCC" in power_lib_ids, (
        f"Expected power:VCC symbol for power net 'VCC'; lib_ids found: {sorted(power_lib_ids)}"
    )
    assert "power:GND" in power_lib_ids, (
        f"Expected power:GND symbol for power net 'GND'; lib_ids found: {sorted(power_lib_ids)}"
    )


@_skip_no_system_symbols
def test_ne5532_full_circuit_fidelity_with_system_libraries(tmp_path: Path) -> None:
    """Circuit fidelity: NE5532 op-amp circuit using real KiCad system libraries.

    NE5532 uses (extends "LM2904") in the KiCad library.  This test verifies
    the complete pipeline on a realistic circuit:

    - U1 NE5532 (dual op-amp, 8 pins, all inherited from LM2904)
    - R1-R4 Device:R

    The circuit exercises BOTH op-amp units inside U1:
      Unit A: pins 3 (IN+), 2 (IN-), 1 (OUT)
      Unit B: pins 5 (IN+), 6 (IN-), 7 (OUT)
      Power:  pins 8 (V+), 4 (V-)

    Fidelity assertions:
    1. All 5 components present as placed symbol instances.
    2. All 8 nets have correct OpenClaw:bind= markers.
    3. NE5532 is embedded as a flat (non-extends) symbol — read_lib_symbol_def_flat
       merges LM2904's geometry into the NE5532 node so KiCad renders it correctly
       without needing a separate LM2904 entry in lib_symbols.
    """
    ir_data = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "Amplifier_Operational:NE5532", "value": "NE5532"},
            {"ref": "R1", "symbol": "Device:R", "value": "10k"},
            {"ref": "R2", "symbol": "Device:R", "value": "100k"},
            {"ref": "R3", "symbol": "Device:R", "value": "10k"},
            {"ref": "R4", "symbol": "Device:R", "value": "100k"},
        ],
        "nets": [
            # Power rails
            {"name": "VCC", "pins": [{"ref": "U1", "pin": "8"}]},
            {
                "name": "GND",
                "pins": [
                    {"ref": "U1", "pin": "4"},
                    {"ref": "R1", "pin": "2"},
                    {"ref": "R3", "pin": "2"},
                ],
            },
            # Unit A: inverting amplifier (pins 1, 2, 3)
            {"name": "IN_A", "pins": [{"ref": "U1", "pin": "3"}, {"ref": "R1", "pin": "1"}]},
            {"name": "IN_N_A", "pins": [{"ref": "U1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
            {"name": "OUT_A", "pins": [{"ref": "U1", "pin": "1"}, {"ref": "R2", "pin": "2"}]},
            # Unit B: inverting amplifier (pins 5, 6, 7)
            {"name": "IN_B", "pins": [{"ref": "U1", "pin": "5"}, {"ref": "R3", "pin": "1"}]},
            {"name": "IN_N_B", "pins": [{"ref": "U1", "pin": "6"}, {"ref": "R4", "pin": "1"}]},
            {"name": "OUT_B", "pins": [{"ref": "U1", "pin": "7"}, {"ref": "R4", "pin": "2"}]},
        ],
    }
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="NE5532Circuit",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
            mode="internal",
        )
    )

    assert result.symbols_added == 5
    assert result.nets_applied == 8

    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    # Core fidelity: all components placed and all net bindings recorded correctly.
    _check_circuit_fidelity(ir_data, managed_doc)

    # Extends-chain specific: NE5532 is embedded as a flat symbol; LM2904 is NOT
    # embedded separately — its geometry was merged into the NE5532 node.
    embedded_ids = _get_lib_symbol_ids(managed_doc)
    assert "Amplifier_Operational:NE5532" in embedded_ids, (
        f"Derived symbol NE5532 missing from lib_symbols. Embedded: {embedded_ids}"
    )
    assert "Amplifier_Operational:LM2904" not in embedded_ids, (
        f"Base symbol LM2904 should not be embedded separately (geometry was merged "
        f"into NE5532 by read_lib_symbol_def_flat); got: {embedded_ids}"
    )

    # The embedded NE5532 node must be flat — no (extends ...) attribute.
    lib_syms = find_first(managed_doc.root, "lib_symbols")
    assert lib_syms is not None
    ne5532_node = next(
        (
            item
            for item in lib_syms.items
            if isinstance(item, ListNode)
            and item.key == "symbol"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "Amplifier_Operational:NE5532"
        ),
        None,
    )
    assert ne5532_node is not None
    extends_found = any(isinstance(c, ListNode) and c.key == "extends" for c in ne5532_node.items)
    assert not extends_found, (
        "NE5532 lib_symbols entry still contains (extends ...) — "
        "read_lib_symbol_def_flat should have removed it"
    )


# ---------------------------------------------------------------------------
# search-symbols / debug-symbol tests
# ---------------------------------------------------------------------------

from kicad_pcb.commands.search import cmd_debug_symbol, cmd_search_symbols  # noqa: E402
from kicad_pcb.results import DebugSymbolResult, SearchSymbolsResult  # noqa: E402

_TESTLIB_SYMBOLS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"


def test_search_symbols_finds_exact_match() -> None:
    """Exact symbol name in query returns that symbol."""
    args = Namespace(query="OpAmp", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    assert isinstance(result, SearchSymbolsResult)
    ids = [m.symbol_id for m in result.matches]
    assert "TestLib:OpAmp" in ids


def test_search_symbols_finds_derived_symbol() -> None:
    """Searching 'DerivedOpAmp' finds both derived and possibly base symbol."""
    args = Namespace(query="DerivedOpAmp", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    ids = [m.symbol_id for m in result.matches]
    assert "TestLib:DerivedOpAmp" in ids


def test_search_symbols_no_match_returns_empty() -> None:
    """Query with no matches returns an empty matches tuple."""
    args = Namespace(query="zzz_no_such_thing_xyz", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    assert result.matches == ()


def test_search_symbols_empty_query_returns_empty() -> None:
    """Blank query returns empty results without error."""
    args = Namespace(query="   ", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    assert result.matches == ()


def test_search_symbols_result_fields() -> None:
    """SymbolMatch fields are correctly populated."""
    args = Namespace(query="R", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    r_match = next((m for m in result.matches if m.symbol_id == "TestLib:R"), None)
    assert r_match is not None, "TestLib:R not found in results"
    assert isinstance(r_match.pin_count, int)
    assert r_match.pin_count >= 0
    assert isinstance(r_match.description, str)


def test_search_symbols_limit_respected() -> None:
    """--limit caps the number of results returned."""
    args = Namespace(query="Op", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=1)
    result = cmd_search_symbols(args)
    assert len(result.matches) <= 1


def test_search_symbols_searched_dirs_reported() -> None:
    """symbols_dirs field reflects the directory that was searched."""
    args = Namespace(query="R", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    assert any(str(_TESTLIB_SYMBOLS_DIR) in d for d in result.symbols_dirs)


@_skip_no_system_symbols
def test_search_symbols_kicad9_renamed_symbols() -> None:
    """Verify KiCad 9 renames: C_Polarized and R_Potentiometer exist; legacy names do not."""
    args_cp = Namespace(
        query="polarized capacitor",
        symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
        limit=20,
    )
    result_cp = cmd_search_symbols(args_cp)
    ids_cp = [m.symbol_id for m in result_cp.matches]
    assert "Device:C_Polarized" in ids_cp, f"C_Polarized missing; got {ids_cp}"
    assert "Device:CP" not in ids_cp, "KiCad-8 legacy Device:CP should not appear in KiCad 9"

    args_pot = Namespace(
        query="potentiometer",
        symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
        limit=5,
    )
    result_pot = cmd_search_symbols(args_pot)
    ids_pot = [m.symbol_id for m in result_pot.matches]
    assert "Device:R_Potentiometer" in ids_pot, f"R_Potentiometer missing; got {ids_pot}"


# ---------------------------------------------------------------------------
# debug-symbol tests (P2)
# ---------------------------------------------------------------------------


def test_debug_symbol_standalone() -> None:
    """A symbol with its own pins reports correct list and no extends_base."""
    args = Namespace(symbol="TestLib:R", symbols_dir=str(_TESTLIB_SYMBOLS_DIR))
    result = cmd_debug_symbol(args)
    assert isinstance(result, DebugSymbolResult)
    assert result.symbol_id == "TestLib:R"
    assert result.extends_base is None
    assert result.pin_count == 2
    assert set(result.pin_numbers) == {"1", "2"}


def test_debug_symbol_extends() -> None:
    """An extends symbol reports extends_base and inherits parent pin count."""
    args = Namespace(symbol="TestLib:DerivedOpAmp", symbols_dir=str(_TESTLIB_SYMBOLS_DIR))
    result = cmd_debug_symbol(args)
    assert isinstance(result, DebugSymbolResult)
    assert result.symbol_id == "TestLib:DerivedOpAmp"
    assert result.extends_base == "TestLib:OpAmp"
    assert result.pin_count == 4  # inherited from OpAmp
    assert set(result.pin_numbers) == {"1", "2", "3", "6"}


def test_debug_symbol_not_found() -> None:
    """A non-existent symbol raises UserError with SYMBOL_NOT_FOUND code."""
    args = Namespace(symbol="TestLib:NoSuchSymbol", symbols_dir=str(_TESTLIB_SYMBOLS_DIR))
    with pytest.raises(UserError) as exc_info:
        cmd_debug_symbol(args)
    assert exc_info.value.code == ErrorCode.SYMBOL_NOT_FOUND


# ---------------------------------------------------------------------------
# Hierarchy path tests (fix: sheet_instances and symbol instances use parent UUID)
# ---------------------------------------------------------------------------


def test_update_managed_path_qualifies_sheet_instances(tmp_path: Path) -> None:
    """update_managed_path replaces '/' with '/{uuid}/' in sheet_instances."""
    sch_path = tmp_path / "managed.kicad_sch"
    sch_path.write_text(
        """(kicad_sch (version 20230121) (generator eeschema)
  (uuid "aaaa-bbbb-cccc")
  (paper "A4")
  (lib_symbols)
  (sheet_instances
    (path "/" (page "2"))
  )
)
""",
        encoding="utf-8",
    )
    doc = SchematicDoc.load(sch_path)
    test_uuid = "11111111-2222-3333-4444-555555555555"
    doc.update_managed_path(test_uuid)

    si = find_first(doc.root, "sheet_instances")
    assert si is not None
    path_node = find_first(si, "path")
    assert path_node is not None
    assert isinstance(path_node.items[1], StringNode)
    assert path_node.items[1].value == f"/{test_uuid}/"


def test_update_managed_path_qualifies_symbol_instances(tmp_path: Path) -> None:
    """update_managed_path replaces '/' with '/{uuid}/' in symbol instance paths."""
    sch_path = tmp_path / "managed.kicad_sch"
    sch_path.write_text(
        """(kicad_sch (version 20230121) (generator eeschema)
  (uuid "aaaa")
  (paper "A4")
  (lib_symbols)
  (symbol (lib_id "Device:R") (at 50.80 76.20 0) (unit 1)
    (uuid "sym-uuid-1")
    (instances
      (project "myproj" (path "/" (reference "R1") (unit 1)))
    )
  )
  (sheet_instances (path "/" (page "1")))
)
""",
        encoding="utf-8",
    )
    doc = SchematicDoc.load(sch_path)
    test_uuid = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
    doc.update_managed_path(test_uuid)

    # All (path ...) nodes in the document must be qualified — none may remain "/".
    for node in walk(doc.root):
        if (
            isinstance(node, ListNode)
            and node.key == "path"
            and len(node.items) >= 2
            and isinstance(node.items[1], StringNode)
        ):
            assert node.items[1].value != "/", (
                "Found unqualified '/' path after update_managed_path"
            )
            assert node.items[1].value == f"/{test_uuid}/", (
                f"Expected /{test_uuid}/, got {node.items[1].value!r}"
            )


def test_managed_schematic_hierarchy_paths_match_parent_sheet_uuid(tmp_path: Path) -> None:
    """Integration: managed schematic sheet_instances and symbol instances
    carry the UUID of the (sheet ...) entry in the parent root schematic.
    """
    ir_data = {
        "version": "1",
        "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
        "nets": [{"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]}],
    }
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="HierTest",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    # Extract the managed-sheet UUID from the parent (root) schematic.
    parent_doc = SchematicDoc.load(result.schematic_path)
    sheet_uuid: str | None = None
    for item in parent_doc.root.items:
        if not (isinstance(item, ListNode) and item.key == "sheet"):
            continue
        for sub in item.items:
            if (
                isinstance(sub, ListNode)
                and sub.key == "uuid"
                and len(sub.items) >= 2
                and isinstance(sub.items[1], StringNode)
            ):
                sheet_uuid = sub.items[1].value
                break
        if sheet_uuid:
            break
    assert sheet_uuid is not None, "Parent schematic has no (sheet (uuid ...)) node"

    expected_path = f"/{sheet_uuid}/"

    # Check the managed schematic: all (path ...) nodes must be qualified.
    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    unqualified: list[str] = []
    wrong_uuid: list[str] = []
    for node in walk(managed_doc.root):
        if not (
            isinstance(node, ListNode)
            and node.key == "path"
            and len(node.items) >= 2
            and isinstance(node.items[1], StringNode)
        ):
            continue
        val = node.items[1].value
        if val == "/":
            unqualified.append(repr(node))
        elif val != expected_path:
            wrong_uuid.append(f"{val!r} (expected {expected_path!r})")

    assert not unqualified, f"Unqualified '/' paths remain: {unqualified}"
    assert not wrong_uuid, f"Paths with wrong UUID: {wrong_uuid}"
