from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply_artifacts import validate_generated_schematic
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.parser import parse
from kicad_pcb.sexpr.utils import find_first

_ROOT_UUID = "11111111-2222-3333-4444-555555555555"


def _doc(text: str) -> SchematicDoc:
    root = parse(text)
    assert isinstance(root, ListNode)
    return SchematicDoc(root)


def _empty_doc() -> SchematicDoc:
    return _doc(
        f'''(kicad_sch
  (version 20230121)
  (generator eeschema)
  (uuid "{_ROOT_UUID}")
  (lib_symbols)
  (sheet_instances
    (path "/" (page "1"))))'''
    )


def _direct_child(doc: SchematicDoc, key: str) -> ListNode:
    for item in doc.root.items:
        if isinstance(item, ListNode) and item.key == key:
            return item
    raise AssertionError(f"Missing top-level {key!r} node")


def _path_value(node: ListNode) -> str:
    path = find_first(node, "path")
    assert path is not None
    assert len(path.items) >= 2
    value = path.items[1]
    assert isinstance(value, StringNode)
    return value.value


def _symbol_paths(doc: SchematicDoc) -> list[str]:
    return [
        _path_value(item)
        for item in doc.root.items
        if isinstance(item, ListNode) and item.key == "symbol"
    ]


def test_root_symbol_instance_paths_use_root_uuid_and_leave_sheet_root_bare() -> None:
    doc = _doc(
        f'''(kicad_sch
  (version 20230121)
  (generator eeschema)
  (uuid "{_ROOT_UUID}")
  (symbol
    (lib_id "TestLib:R")
    (instances
      (project "demo"
        (path "/" (reference "R1") (unit 1)))))
  (symbol
    (lib_id "power:GND")
    (instances
      (project "demo"
        (path "/" (reference "#PWR01") (unit 1)))))
  (sheet_instances
    (path "/" (page "1"))))'''
    )

    doc.qualify_root_symbol_instance_paths()

    assert _symbol_paths(doc) == [f"/{_ROOT_UUID}", f"/{_ROOT_UUID}"]
    assert _path_value(_direct_child(doc, "sheet_instances")) == "/"


def test_root_symbol_instance_path_normalization_preserves_existing_path() -> None:
    existing_path = "/parent/sheet"
    doc = _doc(
        f'''(kicad_sch
  (version 20230121)
  (generator eeschema)
  (uuid "{_ROOT_UUID}")
  (symbol
    (lib_id "TestLib:R")
    (instances
      (project "demo"
        (path "{existing_path}" (reference "R1") (unit 1)))))
  (sheet_instances
    (path "/" (page "1"))))'''
    )

    doc.qualify_root_symbol_instance_paths()

    assert _path_value(_direct_child(doc, "symbol")) == existing_path
    assert _path_value(_direct_child(doc, "sheet_instances")) == "/"


def test_root_symbol_instance_path_normalization_requires_root_uuid() -> None:
    doc = _doc(
        '''(kicad_sch
  (version 20230121)
  (generator eeschema)
  (symbol
    (lib_id "TestLib:R")
    (instances
      (project "demo"
        (path "/" (reference "R1") (unit 1)))))
  (sheet_instances
    (path "/" (page "1"))))'''
    )

    with pytest.raises(ValueError, match="root UUID is missing or invalid"):
        doc.qualify_root_symbol_instance_paths()


def test_generated_schematic_validation_finalizes_root_symbol_instance_paths() -> None:
    doc = _empty_doc()
    doc.add_symbol(
        "TestLib:R",
        "R1",
        "10k",
        "",
        100.0,
        100.0,
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        ["1"],
        ["bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"],
        "demo",
    )
    doc.add_symbol(
        "TestLib:R",
        "R2",
        "10k",
        "",
        120.0,
        100.0,
        "cccccccc-cccc-cccc-cccc-cccccccccccc",
        ["1"],
        ["dddddddd-dddd-dddd-dddd-dddddddddddd"],
        "demo",
    )
    doc.add_text(
        'kicad-pcb:bind={"net_name":"SIGNAL","pin":"1","ref":"R1"}',
        -1200.0,
        -1500.0,
        hidden=True,
    )
    doc.add_text(
        'kicad-pcb:bind={"net_name":"SIGNAL","pin":"1","ref":"R2"}',
        -1200.0,
        -1510.0,
        hidden=True,
    )
    generation_ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="TestLib:R", value="10k"),
            ComponentIR(ref="R2", symbol="TestLib:R", value="10k"),
        ],
        nets=[
            NetIR(
                name="SIGNAL",
                pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
            )
        ],
    )

    assert _symbol_paths(doc) == ["/", "/"]

    diagnostics = validate_generated_schematic(
        doc=doc,
        generation_ir=generation_ir,
        schematic_path=Path("generated.kicad_sch"),
        expected_wire_count=0,
    )

    assert diagnostics.hard_failures == ()
    assert _symbol_paths(doc) == [f"/{_ROOT_UUID}", f"/{_ROOT_UUID}"]
    assert _path_value(_direct_child(doc, "sheet_instances")) == "/"
