from __future__ import annotations

import pytest

from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.parser import parse
from kicad_pcb.sexpr.utils import find_first

_ROOT_UUID = "11111111-2222-3333-4444-555555555555"


def _doc(text: str) -> SchematicDoc:
    root = parse(text)
    assert isinstance(root, ListNode)
    return SchematicDoc(root)


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

    symbol_paths = [
        _path_value(item)
        for item in doc.root.items
        if isinstance(item, ListNode) and item.key == "symbol"
    ]
    assert symbol_paths == [f"/{_ROOT_UUID}", f"/{_ROOT_UUID}"]
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
