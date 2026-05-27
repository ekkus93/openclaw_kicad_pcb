from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.corpus.kicadxml import (
    canonicalize_circuit_ir,
    kicadxml_to_circuit_ir,
    parse_kicadxml_netlist,
    schematic_symbols_by_ref,
)
from kicad_pcb.errors import ParseError
from kicad_pcb.sch_doc import SchematicDoc

XML_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "model_corpus_xml" / "minimal_netlist.xml"
)
SCHEMATIC_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "model_corpus"
    / "4-channel-switched-constant-current-source"
    / "source_normalized.kicad_sch"
)


def test_parse_kicadxml_netlist_components_and_nets() -> None:
    netlist = parse_kicadxml_netlist(XML_FIXTURE)
    assert [component.ref for component in netlist.components] == ["R1", "C1"]
    assert netlist.components[0].symbol == "Device:R"
    assert [net.name for net in netlist.nets] == ["VIN", "GND", "SIG"]


def test_kicadxml_to_circuit_ir_preserves_symbols_and_components() -> None:
    ir = kicadxml_to_circuit_ir(parse_kicadxml_netlist(XML_FIXTURE))
    assert [component.ref for component in ir.components] == ["C1", "R1"]
    assert ir.components[1].symbol == "Device:R"
    assert [net.name for net in ir.nets] == ["GND", "SIG", "VIN"]


def test_canonicalize_circuit_ir_sorts_components_nets_and_pins() -> None:
    ir = kicadxml_to_circuit_ir(parse_kicadxml_netlist(XML_FIXTURE))
    canonical = canonicalize_circuit_ir(ir)
    assert [component.ref for component in canonical.components] == ["C1", "R1"]
    assert [net.name for net in canonical.nets] == ["GND", "SIG", "VIN"]


def test_parse_kicadxml_netlist_reports_malformed_xml(tmp_path: Path) -> None:
    bad_xml = tmp_path / "bad.xml"
    bad_xml.write_text("<export><components>", encoding="utf-8")
    with pytest.raises(ParseError):
        parse_kicadxml_netlist(bad_xml)


def test_kicadxml_to_circuit_ir_backfills_missing_symbols_by_ref() -> None:
    netlist = parse_kicadxml_netlist(
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "model_corpus"
        / "4-channel-switched-constant-current-source"
        / "source_netlist.kicadxml"
    )
    fallback_symbols = schematic_symbols_by_ref(SchematicDoc.load(SCHEMATIC_FIXTURE))

    ir = kicadxml_to_circuit_ir(
        netlist,
        fallback_symbols_by_ref=fallback_symbols,
    )

    symbols_by_ref = {component.ref: component.symbol for component in ir.components}
    assert symbols_by_ref["PS3"] == "SamacSys_Parts:NCV317MBSTT3G"
