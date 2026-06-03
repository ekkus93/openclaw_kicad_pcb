from __future__ import annotations

from pathlib import Path

from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import count_power_symbols

MODEL_FIXTURE = (
    Path(__file__).resolve().parents[2] / "model_kicad_files" / "mcp2551-can-transciever.kicad_sch"
)


def test_count_power_symbols_detects_real_source_power_symbols() -> None:
    doc = SchematicDoc.load(MODEL_FIXTURE)
    assert count_power_symbols(doc) > 0
