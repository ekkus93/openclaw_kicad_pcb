from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime
from pathlib import Path

import pytest
from kicad_pcb.commands.netlist import (
    cmd_apply_netlist,
    cmd_info_sch,
    cmd_new_from_netlist,
    resolve_schematic_paths,
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
    refs = sorted(s["ref"] for s in symbols)
    symbol_ids = sorted(s["symbol_id"] for s in symbols)
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
