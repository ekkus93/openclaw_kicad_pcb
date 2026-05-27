from __future__ import annotations

from pathlib import Path

from kicad_pcb.corpus.embedded_symbols import (
    extract_embedded_symbol_defs,
    materialize_embedded_symbol_libraries,
    write_embedded_symbol_library,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.parser import parse_file
from kicad_pcb.symbol_index import SymbolIndex

MODEL_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "model_kicad_files"
    / "mcp2551-can-transciever.kicad_sch"
)
CUSTOM_SYMBOL_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "model_kicad_files"
    / "4-channel-switched-constant-current-source.kicad_sch"
)


def test_extract_embedded_symbols_from_real_source() -> None:
    doc = SchematicDoc.load(MODEL_FIXTURE)
    symbols = extract_embedded_symbol_defs(doc)
    assert "Device:R" in symbols
    assert "Device:C" in symbols


def test_extract_embedded_symbols_qualifies_custom_symbol_ids_from_placed_symbols() -> None:
    doc = SchematicDoc.load(CUSTOM_SYMBOL_FIXTURE)
    symbols = extract_embedded_symbol_defs(doc)
    assert "SamacSys_Parts:NCV317MBSTT3G" in symbols


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


def test_materialize_embedded_symbol_libraries_creates_kicad_sym_files(tmp_path: Path) -> None:
    doc = SchematicDoc.load(MODEL_FIXTURE)
    symbols = extract_embedded_symbol_defs(doc)
    source_artifact = tmp_path / "source_embedded_symbols.sexpr"
    write_embedded_symbol_library(symbols, source_artifact)

    output_dir = materialize_embedded_symbol_libraries(
        source_artifact,
        output_dir=tmp_path / "fixture_symbols",
    )

    assert output_dir is not None
    device_lib = output_dir / "Device.kicad_sym"
    assert device_lib.exists()
    parsed = parse_file(device_lib)
    assert parsed.key == "kicad_symbol_lib"
    index = SymbolIndex(symbols_dir=output_dir)
    assert index.get_pins("Device:R") == {"1", "2"}
