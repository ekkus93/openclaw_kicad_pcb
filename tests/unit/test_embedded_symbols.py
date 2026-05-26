from __future__ import annotations

from pathlib import Path

from kicad_pcb.corpus.embedded_symbols import (
    extract_embedded_symbol_defs,
    write_embedded_symbol_library,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.parser import parse_file

MODEL_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "model_kicad_files"
    / "mcp2551-can-transciever.kicad_sch"
)


def test_extract_embedded_symbols_from_real_source() -> None:
    doc = SchematicDoc.load(MODEL_FIXTURE)
    symbols = extract_embedded_symbol_defs(doc)
    assert "Device:R" in symbols
    assert "Device:C" in symbols


def test_extract_embedded_symbols_empty_when_no_lib_symbols(tmp_path: Path) -> None:
    path = tmp_path / "empty.kicad_sch"
    path.write_text("(kicad_sch (version 20230121) (lib_symbols))", encoding="utf-8")
    doc = SchematicDoc.load(path)
    assert extract_embedded_symbol_defs(doc) == {}


def test_write_embedded_symbols_is_deterministic_and_parseable(tmp_path: Path) -> None:
    doc = SchematicDoc.load(MODEL_FIXTURE)
    symbols = extract_embedded_symbol_defs(doc)
    output_file = tmp_path / "source_embedded_symbols.sexpr"
    write_embedded_symbol_library(symbols, output_file)
    first = output_file.read_text(encoding="utf-8")
    write_embedded_symbol_library(symbols, output_file)
    assert output_file.read_text(encoding="utf-8") == first
    parsed = parse_file(output_file)
    assert parsed.key == "lib_symbols"
