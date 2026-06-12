from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.commands._sch_apply_artifacts import _cleanup_new_managed_file
from kicad_pcb.commands.netlist import (
    cmd_apply_netlist,
    cmd_new_from_netlist,
)
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.models import ProjectRef
from kicad_pcb.sch_doc import SchematicDoc


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


def test_apply_netlist_returns_generated_schematic_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Successful apply exposes structured post-generation diagnostics."""
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

    diagnostics = result.generated_schematic_diagnostics
    assert diagnostics is not None
    assert diagnostics.symbol_count == 1
    assert diagnostics.binding_marker_count == 1
    assert diagnostics.unresolved_refs == ()
    assert diagnostics.hard_failures == ()


def test_apply_netlist_missing_expected_wires_raises_structural_validation_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """If routing expected wires but none are emitted, structural validation must fail."""
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
        ],
        "nets": [
            {
                "name": "N1",
                "pins": [
                    {"ref": "R1", "pin": "1"},
                    {"ref": "R2", "pin": "1"},
                ],
            }
        ],
    }
    ir_path.write_text(json.dumps(payload), encoding="utf-8")

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)
    monkeypatch.setattr(SchematicDoc, "add_wire", lambda *args, **kwargs: None)

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

    diagnostics = exc_info.value.details["generated_schematic_diagnostics"]
    assert exc_info.value.code == ErrorCode.EMPTY_GENERATION
    assert any(failure["code"] == "MISSING_WIRES" for failure in diagnostics["hard_failures"])
    assert not (project_dir / "OpenClaw_Managed.kicad_sch").exists()


def test_apply_netlist_missing_bind_markers_raises_structural_validation_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A pseudo-populated schematic without bind markers must not be treated as success."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "ir.json"
    _write_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    original_add_text = SchematicDoc.add_text

    def _drop_bind_markers(self, text, *args, **kwargs):
        if isinstance(text, str) and (
            text.startswith("kicad-pcb:bind=") or text.startswith("OpenClaw:bind=")
        ):
            return None
        return original_add_text(self, text, *args, **kwargs)

    monkeypatch.setattr(SchematicDoc, "add_text", _drop_bind_markers)

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

    diagnostics = exc_info.value.details["generated_schematic_diagnostics"]
    assert exc_info.value.code == ErrorCode.EMPTY_GENERATION
    assert any(failure["code"] == "MISSING_BINDINGS" for failure in diagnostics["hard_failures"])
    assert not (project_dir / "OpenClaw_Managed.kicad_sch").exists()
