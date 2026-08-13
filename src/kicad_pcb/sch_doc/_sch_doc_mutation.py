"""SchematicDoc mixin: library embedding and element-addition methods."""

from __future__ import annotations

import contextlib
from pathlib import Path

from ..lib_symbol import _symbol_id, read_lib_symbol_def_flat
from ..sexpr.builder import L, atom
from ..sexpr.nodes import AtomNode, ListNode, StringNode
from ..sexpr.utils import find_first, replace_section
from .nodes import (
    make_global_label_node,
    make_junction_node,
    make_label_node,
    make_no_connect_node,
    make_power_symbol_node,
    make_symbol_node,
    make_text_node,
    make_wire_node,
)


def _qualify_bare_instance_paths(node: ListNode, qualified_path: str) -> ListNode:
    """Replace bare ``/`` path values below an ``instances`` subtree."""
    changed = False
    items = []
    for item in node.items:
        replacement = item
        if isinstance(item, ListNode):
            if (
                item.key == "path"
                and len(item.items) >= 2
                and isinstance(item.items[1], StringNode)
                and item.items[1].value == "/"
            ):
                path_items = list(item.items)
                path_items[1] = StringNode(qualified_path, item.items[1].pos)
                replacement = ListNode(tuple(path_items), item.pos)
            else:
                replacement = _qualify_bare_instance_paths(item, qualified_path)
        changed = changed or replacement is not item
        items.append(replacement)
    if not changed:
        return node
    return ListNode(tuple(items), node.pos)


class _SchDocMixin:
    """Mixin providing embedding and element-addition methods for SchematicDoc."""

    root: ListNode  # set by SchematicDoc.__init__

    # ------------------------------------------------------------------
    # Library symbol embedding
    # ------------------------------------------------------------------

    def ensure_lib_symbols_section(self) -> None:
        """Add an empty ``(lib_symbols)`` section if none exists."""
        if find_first(self.root, "lib_symbols") is None:
            new_section = L(atom("lib_symbols"))
            self.root = ListNode(self.root.items + (new_section,), self.root.pos)

    def embed_lib_symbol(self, sym_def_node: ListNode) -> bool:
        """Embed *sym_def_node* into ``(lib_symbols)``, skipping duplicates.

        The symbol id is determined from the second item of *sym_def_node*
        (expected to be a :class:`~kicad_pcb.sexpr.nodes.StringNode`).

        Returns
        -------
        bool
            ``True``  — symbol added or was already present.
            ``False`` — symbol id could not be determined.
        """
        full_id = _symbol_id(sym_def_node)
        if full_id is None:
            return False

        self.ensure_lib_symbols_section()
        lib_symbols = find_first(self.root, "lib_symbols")
        if lib_symbols is None:
            return False  # shouldn't happen after ensure_lib_symbols_section

        # Check for existing embedding.
        for item in lib_symbols.items:
            if isinstance(item, ListNode) and item.key == "symbol" and _symbol_id(item) == full_id:
                return True  # Already embedded.

        new_lib_symbols = ListNode(lib_symbols.items + (sym_def_node,), lib_symbols.pos)
        self.root = replace_section(self.root, "lib_symbols", new_lib_symbols)
        return True

    # ------------------------------------------------------------------
    # Element additions
    # ------------------------------------------------------------------

    def add_symbol(  # noqa: PLR0913
        self,
        lib_sym: str,
        ref: str,
        value: str,
        footprint: str,
        x: float,
        y: float,
        sym_uuid: str,
        pin_nums: list[str],
        pin_uuids: list[str],
        project_name: str,
        *,
        unit: int = 1,
        rotation: int = 0,
    ) -> None:
        """Append a placed symbol instance to the schematic.

        Parameters mirror :func:`make_symbol_node`.
        """
        node = make_symbol_node(
            lib_sym,
            ref,
            value,
            footprint,
            x,
            y,
            sym_uuid,
            pin_nums,
            pin_uuids,
            project_name,
            unit=unit,
            rotation=rotation,
        )
        self._insert_before_sheet_instances(node)

    def add_wire(self, x1: float, y1: float, x2: float, y2: float, wire_uuid: str) -> None:
        """Append a wire segment to the schematic."""
        self._insert_before_sheet_instances(make_wire_node(x1, y1, x2, y2, wire_uuid))

    def add_label(self, name: str, x: float, y: float, label_uuid: str, *, angle: int = 0) -> None:
        """Append a net label to the schematic.

        Parameters
        ----------
        angle:
            Label orientation in degrees (0=right, 90=down, 180=left, 270=up).
            Pass the outward direction of the pin the label will attach to so
            the label visually extends away from the symbol body.
        """
        self._insert_before_sheet_instances(make_label_node(name, x, y, label_uuid, angle=angle))

    def add_text(self, text: str, x: float, y: float, *, hidden: bool = False) -> None:
        """Append a text node to the schematic."""
        self._insert_before_sheet_instances(make_text_node(text, x, y, hidden=hidden))

    def add_junction(self, x: float, y: float, junction_uuid: str) -> None:
        """Append a junction node at *(x, y)* to the schematic.

        Junctions are required wherever wire segments meet at a T- or
        X-intersection so KiCad treats them as electrically connected.
        """
        self._insert_before_sheet_instances(make_junction_node(x, y, junction_uuid))

    def add_no_connect(self, x: float, y: float, no_connect_uuid: str) -> None:
        """Append a KiCad no-connect marker at *(x, y)* to the schematic."""
        self._insert_before_sheet_instances(make_no_connect_node(x, y, no_connect_uuid))

    def add_global_label(  # noqa: PLR0913
        self,
        name: str,
        x: float,
        y: float,
        label_uuid: str,
        *,
        angle: int = 0,
        shape: str = "input",
    ) -> None:
        """Append a global label node to the schematic.

        Global labels are used for power nets (GND, VCC, …) and for any net
        that should cross sheet boundaries without fragmented local labels.
        """
        self._insert_before_sheet_instances(
            make_global_label_node(name, x, y, label_uuid, angle=angle, shape=shape)
        )

    def add_power_symbol(  # noqa: PLR0913
        self,
        net_name: str,
        x: float,
        y: float,
        sym_uuid: str,
        pin_uuid: str,
        ref: str,
        project_name: str,
        *,
        angle: int = 0,
        symbols_dir: Path | None = None,
    ) -> bool:
        """Embed and place a KiCad power symbol (e.g. ``power:GND``).

        Looks up ``power:<net_name>`` in the KiCad symbol library, embeds the
        definition in ``lib_symbols``, and adds a placed instance at *(x, y)*.

        Parameters
        ----------
        net_name:     Net name, e.g. ``"GND"`` — also determines the lib lookup
                      (``power:GND``) and the placed symbol's ``Value``.
        x, y:         Placement coordinates in mm.  The symbol's pin is here;
                      a stub wire should end at this point.
        sym_uuid:     UUID for the placed symbol instance.
        pin_uuid:     UUID for the pin node inside the placed instance.
        ref:          Reference string, typically ``"#PWRnn"``.
        project_name: KiCad project name (for the ``(instances …)`` block).
        angle:        Symbol rotation in degrees CCW (default 0).
        symbols_dir:  Path to ``.kicad_sym`` files.  ``None`` uses the system
                      default (``/usr/share/kicad/symbols``).

        Returns
        -------
        bool
            ``True`` when the symbol was found and placed successfully.
            ``False`` when the symbol is not in the library — caller should
            fall back to a ``global_label``.
        """
        sym_def = read_lib_symbol_def_flat("power", net_name, symbols_dir=symbols_dir)
        if sym_def is None:
            return False
        self.embed_lib_symbol(sym_def)
        node = make_power_symbol_node(
            f"power:{net_name}",
            net_name,
            ref,
            x,
            y,
            sym_uuid,
            pin_uuid,
            project_name,
            angle=angle,
        )
        self._insert_before_sheet_instances(node)
        return True

    def qualify_root_symbol_instance_paths(self) -> None:
        """Qualify bare placed-symbol instance paths with the root sheet UUID.

        KiCad root schematics use ``/<root-uuid>`` for each symbol's
        ``instances/project/path``.  The root ``sheet_instances`` entry itself
        remains ``/`` and is intentionally outside this normalization pass.
        """
        root_uuid: str | None = None
        for item in self.root.items:
            if (
                isinstance(item, ListNode)
                and item.key == "uuid"
                and len(item.items) >= 2
                and isinstance(item.items[1], StringNode)
                and item.items[1].value
            ):
                root_uuid = item.items[1].value
                break
        if root_uuid is None:
            raise ValueError("Schematic root UUID is missing or invalid")

        qualified_path = f"/{root_uuid}"
        root_items = []
        for item in self.root.items:
            replacement = item
            if isinstance(item, ListNode) and item.key == "symbol":
                symbol_items = []
                for symbol_item in item.items:
                    symbol_replacement = symbol_item
                    if isinstance(symbol_item, ListNode) and symbol_item.key == "instances":
                        symbol_replacement = _qualify_bare_instance_paths(
                            symbol_item, qualified_path
                        )
                    symbol_items.append(symbol_replacement)
                replacement = ListNode(tuple(symbol_items), item.pos)
            root_items.append(replacement)
        self.root = ListNode(tuple(root_items), self.root.pos)

    # ------------------------------------------------------------------
    # Layout query
    # ------------------------------------------------------------------

    def next_component_position(self) -> tuple[float, float]:
        """Return ``(x, y)`` coordinates for the next component placement.

        Steps 25.4 mm right of the rightmost existing symbol ``(at X Y 0)``
        on the root schematic level.  Defaults to ``(50.8, 76.2)`` for empty
        schematics.
        """
        xs: list[float] = []
        for item in self.root.items:
            if isinstance(item, ListNode) and item.key == "symbol":
                at_node = find_first(item, "at")
                if at_node is not None and len(at_node.items) >= 4:
                    x_node = at_node.items[1]
                    angle_node = at_node.items[3]
                    if (
                        isinstance(x_node, AtomNode)
                        and isinstance(angle_node, AtomNode)
                        and angle_node.value == "0"
                    ):
                        with contextlib.suppress(ValueError):
                            xs.append(float(x_node.value))
        base_x = (max(xs) + 25.4) if xs else 50.8
        return (base_x, 76.2)

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _insert_before_sheet_instances(self, node: ListNode) -> None:
        """Insert *node* in root items just before ``(sheet_instances …)``."""
        items = list(self.root.items)
        insertion_idx = len(items)
        for i, item in enumerate(items):
            if isinstance(item, ListNode) and item.key == "sheet_instances":
                insertion_idx = i
                break
        items.insert(insertion_idx, node)
        self.root = ListNode(tuple(items), self.root.pos)
