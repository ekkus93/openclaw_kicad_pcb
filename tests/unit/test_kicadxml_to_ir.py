from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.corpus.kicadxml import (
    canonicalize_circuit_ir,
    kicadxml_to_circuit_ir,
    parse_kicadxml_netlist,
)
from kicad_pcb.errors import ParseError

XML_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "model_corpus_xml" / "minimal_netlist.xml"
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
