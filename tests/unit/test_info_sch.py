from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path

import pytest

from kicad_pcb.commands.netlist import cmd_info_sch
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.fs import _new_uuid
from kicad_pcb.models import ProjectRef
from kicad_pcb.sch_doc import SchematicDoc


def _write_minimal_sch(path: Path) -> None:
    path.write_text(
        """(kicad_sch (version 20230121) (generator eeschema)
  (uuid \"12345678-1234-1234-1234-123456789012\")
  (paper \"A4\")
  (lib_symbols)
  (sheet_instances
    (path \"/\" (page \"1\"))
  )
)
""",
        encoding="utf-8",
    )


def test_info_sch_requires_open_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: None)

    with pytest.raises(UserError) as exc_info:
        cmd_info_sch(Namespace())

    assert exc_info.value.code == ErrorCode.PROJECT_NOT_OPEN


def test_info_sch_reports_symbols_and_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)

    project = ProjectRef(
        name="proj",
        path=project_dir,
        created=datetime.now().isoformat(),
    )
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    doc = SchematicDoc.load(sch_path)
    doc.ensure_openclaw_marker()
    doc.add_symbol(
        "TestLib:R",
        "R1",
        "10k",
        "",
        50.8,
        76.2,
        _new_uuid(),
        ["1", "2"],
        [_new_uuid(), _new_uuid()],
        "proj",
    )
    doc.save(sch_path)

    result = cmd_info_sch(Namespace())

    assert result.owned_by_openclaw is True
    assert len(result.symbols) == 1
    assert result.symbols[0]["ref"] == "R1"
    assert result.symbols[0]["symbol_id"] == "TestLib:R"
