"""KiCad schematic document wrapper — AST-based editing for ``.kicad_sch`` files.

This module is the public entry point to the schematic editing layer.  The
implementation is split across sibling modules:

* :mod:`kicad_pcb.sch_nodes`         — pure AST emitter functions (``make_*``).
* :mod:`kicad_pcb.lib_symbol`        — ``.kicad_sym`` library reader functions.
* :mod:`kicad_pcb.sch_doc._sch_doc_helpers`  — module-level parsing helpers.
* :mod:`kicad_pcb.sch_doc._sch_doc_mutation` — embedding + element-addition mixin.
* :mod:`kicad_pcb.sch_doc`           — (this module) :class:`SchematicDoc` wrapper
  and backward-compatible re-exports of the public API.

All symbols listed in ``__all__`` remain importable from ``kicad_pcb.sch_doc``
so existing callers require no changes.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import ParseError
from ..fs import _atomic_write
from ..lib_symbol import (  # noqa: F401
    read_lib_symbol_def,
    read_lib_symbol_def_chain,
    read_lib_symbol_def_flat,
    read_lib_symbol_pin_at,
    read_lib_symbol_pins,
    read_lib_symbol_power_unit,
    read_lib_symbol_unit_pin_at,
    read_lib_symbol_unit_pins,
)
from ..sexpr.builder import string
from ..sexpr.nodes import ListNode, Node, StringNode
from ..sexpr.parser import parse_file
from ..sexpr.serializer import serialize
from ._sch_doc_helpers import (  # noqa: F401
    _BIND_PREFIXES,
    _get_sheet_uuid,
    _parse_binding_marker,
    _sheet_property_value,
    _symbol_metadata,
)
from ._sch_doc_mutation import _SchDocMixin
from .nodes import (
    ManagedSheetSpec,
    make_global_label_node,  # noqa: F401
    make_junction_node,  # noqa: F401
    make_label_node,  # noqa: F401
    make_managed_sheet_node,
    make_no_connect_node,  # noqa: F401
    make_power_symbol_node,  # noqa: F401
    make_symbol_node,  # noqa: F401
    make_text_node,
    make_wire_node,  # noqa: F401
)

__all__ = [
    "SchematicDoc",
    "ManagedSheetSpec",
    "make_global_label_node",
    "make_junction_node",
    "make_label_node",
    "make_managed_sheet_node",
    "make_no_connect_node",
    "make_power_symbol_node",
    "make_symbol_node",
    "make_text_node",
    "make_wire_node",
    "read_lib_symbol_def",
    "read_lib_symbol_def_chain",
    "read_lib_symbol_def_flat",
    "read_lib_symbol_pin_at",
    "read_lib_symbol_pins",
    "read_lib_symbol_power_unit",
    "read_lib_symbol_unit_pin_at",
    "read_lib_symbol_unit_pins",
]


class SchematicDoc(_SchDocMixin):
    """Mutable wrapper around a parsed KiCad schematic (``.kicad_sch``) AST.

    All mutation methods update ``self.root`` in-place by constructing
    replacement :class:`~kicad_pcb.sexpr.nodes.ListNode` instances (the
    underlying AST nodes remain immutable).

    Typical usage::

        doc = SchematicDoc.load(path)
        for sym_def in read_lib_symbol_def_chain("Device", "R"):
            doc.embed_lib_symbol(sym_def)
        pins = read_lib_symbol_pins("Device", "R") or ["1", "2"]
        pin_uuids = [new_uuid() for _ in pins]
        doc.add_symbol("Device:R", "R1", "10k", "", *doc.next_component_position(),
                       new_uuid(), pins, pin_uuids, project.name)
        doc.save(path)
    """

    def __init__(self, root: ListNode) -> None:
        if root.key != "kicad_sch":
            raise ParseError(f"Expected kicad_sch root node, got {root.key!r}")
        self.root = root

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> SchematicDoc:
        """Parse *path* and return a :class:`SchematicDoc`.

        Raises :class:`~kicad_pcb.errors.ParseError` if the file is malformed
        or the root node is not ``kicad_sch``.
        """
        return cls(parse_file(path))

    def save(self, path: Path, *, backup: bool = False) -> None:
        """Serialize ``self.root`` and write atomically to *path*.

        *backup* — if ``True`` and *path* exists, copy original to
        ``<path>.bak`` before overwriting.
        """
        text = serialize(self.root)
        _atomic_write(path, text, "kicad_sch", backup=backup, operation="save-schematic")

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def has_openclaw_marker(self) -> bool:
        """Return ``True`` when a generation ownership marker text exists.

        Accepts both the legacy ``OpenClaw:generated=`` prefix and the current
        ``kicad-pcb:generated=`` prefix so that older projects are still recognised.
        """
        for item in self.root.items:
            if (
                isinstance(item, ListNode)
                and item.key == "text"
                and len(item.items) >= 2
                and isinstance(item.items[1], StringNode)
                and (
                    item.items[1].value.startswith("kicad-pcb:generated=")
                    or item.items[1].value.startswith("OpenClaw:generated=")
                )
            ):
                return True
        return False

    def ensure_openclaw_marker(self) -> bool:
        """Ensure off-canvas generation marker text nodes are present.

        Returns ``True`` when at least one marker is inserted, ``False`` when
        markers already exist.  Writes the current ``kicad-pcb:`` prefix;
        also recognises the legacy ``OpenClaw:`` prefix on existing files.
        """
        has_generated = False
        has_region = False
        for item in self.root.items:
            if (
                isinstance(item, ListNode)
                and item.key == "text"
                and len(item.items) >= 2
                and isinstance(item.items[1], StringNode)
            ):
                value = item.items[1].value
                if value in ("kicad-pcb:generated=v1", "OpenClaw:generated=v1"):
                    has_generated = True
                elif value in ("kicad-pcb:region=managed", "OpenClaw:region=managed"):
                    has_region = True

        inserted = False
        if not has_generated:
            self._insert_before_sheet_instances(
                make_text_node("kicad-pcb:generated=v1", -1000.0, -1000.0, hidden=True)
            )
            inserted = True
        if not has_region:
            self._insert_before_sheet_instances(
                make_text_node("kicad-pcb:region=managed", -1000.0, -1010.0, hidden=True)
            )
            inserted = True
        return inserted

    def list_symbols(self) -> list[dict[str, object]]:
        """Return a deterministic list of placed symbol metadata."""
        symbols: list[dict[str, object]] = []
        for item in self.root.items:
            if not (isinstance(item, ListNode) and item.key == "symbol"):
                continue
            symbols.append(_symbol_metadata(item))

        return sorted(symbols, key=lambda entry: str(entry.get("ref", "")))

    def count_nodes(self, key: str) -> int:
        """Count direct children of root with the given S-expression key.

        Useful for post-mutation invariant checks querying the live AST
        (e.g. ``count_nodes("symbol")`` or ``count_nodes("label")``).
        """
        return sum(1 for item in self.root.items if isinstance(item, ListNode) and item.key == key)

    def extract_pin_label_bindings(self) -> list[dict[str, str]]:
        """Return pin→net bindings for generated schematics.

        The writer records deterministic hidden marker text nodes with the
        prefix ``OpenClaw:bind=`` and a JSON payload containing
        ``{"ref": ..., "pin": ..., "net_name": ...}``.
        """
        bindings: list[dict[str, str]] = []
        for item in self.root.items:
            if not (
                isinstance(item, ListNode)
                and item.key == "text"
                and len(item.items) >= 2
                and isinstance(item.items[1], StringNode)
            ):
                continue

            marker = item.items[1].value
            if not any(marker.startswith(p) for p in _BIND_PREFIXES):
                continue

            parsed = _parse_binding_marker(marker)
            if parsed is not None:
                bindings.append(parsed)

        return sorted(bindings, key=lambda entry: (entry["ref"], entry["pin"], entry["net_name"]))

    # ------------------------------------------------------------------
    # Sheet management
    # ------------------------------------------------------------------

    def has_managed_sheet(self, *, sheet_name: str = "OpenClaw_Managed") -> bool:
        """Return ``True`` if a sheet with ``Sheetname=<sheet_name>`` exists."""
        for item in self.root.items:
            if (
                isinstance(item, ListNode)
                and item.key == "sheet"
                and _get_sheet_uuid(item) is not None
                and _sheet_property_value(item, "Sheetname") == sheet_name
            ):
                return True
        return False

    def ensure_managed_sheet(
        self,
        *,
        sheet_name: str = "OpenClaw_Managed",
        sheet_file: str = "OpenClaw_Managed.kicad_sch",
        sheet_uuid: str,
    ) -> str:
        """Ensure a top-level managed sheet exists; return its UUID.

        When the sheet already exists, returns its actual UUID from the AST.
        When a new sheet is inserted, returns *sheet_uuid*.
        """
        for item in self.root.items:
            if (
                isinstance(item, ListNode)
                and item.key == "sheet"
                and _sheet_property_value(item, "Sheetname") == sheet_name
            ):
                existing = _get_sheet_uuid(item)
                return existing if existing is not None else sheet_uuid
        self._insert_before_sheet_instances(
            make_managed_sheet_node(
                ManagedSheetSpec(
                    sheet_name=sheet_name,
                    sheet_file=sheet_file,
                    sheet_uuid=sheet_uuid,
                )
            )
        )
        return sheet_uuid

    def update_managed_path(self, sheet_uuid: str) -> None:
        """Qualify managed-sheet root paths to ``(path "/{uuid}/" …)``.

        KiCad requires sub-schematic ``(sheet_instances ...)`` and symbol
        ``(instances ...)`` paths to reference the parent sheet's UUID so that
        KiCad can resolve the hierarchy and assign correct reference
        annotations.  This method walks the entire AST and replaces both bare
        root paths ``"/"`` and paths already qualified with this schematic's
        own root UUID with ``"/{sheet_uuid}/"``.

        Call this on the managed ``SchematicDoc`` after writing all symbols and
        nets, before saving.
        """
        current_root_paths = {"/"}
        for item in self.root.items:
            if (
                isinstance(item, ListNode)
                and item.key == "uuid"
                and len(item.items) >= 2
                and isinstance(item.items[1], StringNode)
                and item.items[1].value
            ):
                root_uuid = item.items[1].value
                current_root_paths.update({f"/{root_uuid}", f"/{root_uuid}/"})
                break

        def _fix(node: Node) -> Node:  # noqa: PLR0911 — recursive walk helper
            if not isinstance(node, ListNode):
                return node
            if (
                node.key == "path"
                and len(node.items) >= 2
                and isinstance(node.items[1], StringNode)
                and node.items[1].value in current_root_paths
            ):
                new_items = list(node.items)
                new_items[1] = string(f"/{sheet_uuid}/")
                return ListNode(tuple(new_items), node.pos)
            new_children = tuple(_fix(child) for child in node.items)
            if new_children == node.items:
                return node
            return ListNode(new_children, node.pos)

        result = _fix(self.root)
        assert isinstance(result, ListNode)
        self.root = result
