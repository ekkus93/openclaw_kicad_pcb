from __future__ import annotations

from pathlib import Path

from kicad_pcb.corpus.layout_features import (
    extract_layout_features,
    guess_symbol_role,
    write_layout_features,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse as parse_sexpr

MODEL_FIXTURE = (
    Path(__file__).resolve().parents[2] / "model_kicad_files" / "mcp2551-can-transciever.kicad_sch"
)


def _doc(body: str) -> SchematicDoc:
    return SchematicDoc(parse_sexpr(f"(kicad_sch {body})"))


def test_guess_symbol_role_handles_key_categories() -> None:
    assert guess_symbol_role("U1", "Interface_CAN_LIN:MCP2551", "MCP2551") == "interface_ic"
    assert guess_symbol_role("J1", "Connector:Conn_01x04", "HEADER") == "connector"
    assert guess_symbol_role("R1", "Device:R", "10k") == "passive"
    assert guess_symbol_role("#PWR01", "power:GND", "GND") == "power_symbol"


def test_extract_layout_features_from_synthetic_doc(tmp_path: Path) -> None:
    doc = _doc(
        " ".join(
            [
                (
                    '(symbol (lib_id "Connector:Conn_01x02") (at 10 10 0) (unit 1) '
                    '(property "Reference" "J1" (at 10 10 0)) '
                    '(property "Value" "Conn" (at 10 12 0)))'
                ),
                (
                    '(symbol (lib_id "Interface_CAN_LIN:MCP2551") (at 40 10 0) (unit 1) '
                    '(property "Reference" "U1" (at 40 10 0)) '
                    '(property "Value" "MCP2551" (at 40 12 0)))'
                ),
                (
                    '(symbol (lib_id "Device:R") (at 25 25 0) (unit 1) '
                    '(property "Reference" "R1" (at 25 25 0)) '
                    '(property "Value" "10k" (at 25 27 0)))'
                ),
                (
                    '(symbol (lib_id "power:GND") (at 40 30 0) (unit 1) '
                    "(in_bom no) (on_board no) "
                    '(property "Reference" "#PWR01" (at 40 30 0)) '
                    '(property "Value" "GND" (at 40 32 0)))'
                ),
                "(wire (pts (xy 10 10) (xy 20 10)))",
                '(label "SIG" (at 20 10 0) (uuid "123"))',
            ]
        )
    )

    features = extract_layout_features(doc, fixture_id="synthetic", source_file="source.kicad_sch")
    assert features.counts["symbols"] == 4
    assert features.counts["power_symbols"] == 1
    assert features.symbols["J1"].is_connector is True
    assert features.symbols["U1"].is_major_ic is True
    assert features.symbols["R1"].is_passive is True
    assert any(rel.a == "J1" and rel.b == "U1" for rel in features.relative_positions)

    output_path = tmp_path / "features.json"
    write_layout_features(features, output_path)
    first = output_path.read_text(encoding="utf-8")
    write_layout_features(features, output_path)
    assert output_path.read_text(encoding="utf-8") == first


def test_extract_layout_features_from_real_model_fixture() -> None:
    doc = SchematicDoc.load(MODEL_FIXTURE)
    features = extract_layout_features(
        doc,
        fixture_id="mcp2551-can-transciever",
        source_file="source.kicad_sch",
    )
    assert features.counts["symbols"] > 0
    assert features.counts["power_symbols"] > 0
    assert "U1" in features.symbols
