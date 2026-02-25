"""Preflight semantic checks for schematic generation (Phase 9.3).

These checks run *before* any document mutation to surface common mistakes
early, before any changes have been made to disk.

Available checks
----------------
``collect_existing_refs``
    Scan a loaded ``SchematicDoc`` for placed symbol references.
``collect_existing_net_names``
    Scan a ``SchematicDoc`` for net label names.
``check_no_duplicate_refs``
    Raise ``UserError`` if any requested ref collides with an existing one.
``check_refs_unique_in_request``
    Raise ``UserError`` if the same ref appears more than once in a single
    pattern call (e.g. ``r1_ref == r2_ref``).
``check_net_names_valid``
    Raise ``UserError`` for empty names, names that start with a digit, or
    names that contain characters that KiCad's netlist engine rejects.
``check_symbol_accessible``
    Raise ``UserError`` when a symbol library is available but the requested
    symbol cannot be found in it.
``check_footprints_assigned``
    Raise ``UserError`` (when *require* is ``True``) if any component is
    missing a footprint assignment.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from .errors import UserError
from .sch_doc import SchematicDoc, read_lib_symbol_pins
from .sexpr.nodes import ListNode, StringNode

# ---------------------------------------------------------------------------
# Net name validation
# ---------------------------------------------------------------------------

#: Characters that KiCad's netlist and ERC expect to be absent from net names.
_NET_FORBIDDEN_RE = re.compile(r'[\s,;"\'()]')

# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------


def collect_existing_refs(doc: SchematicDoc) -> frozenset[str]:
    """Return the set of reference designators for all placed symbols in *doc*.

    Only direct children of the root ``(kicad_sch …)`` node that have key
    ``symbol`` are scanned, matching the placement behaviour of
    :class:`~kicad_pcb.sch_doc.SchematicDoc`.

    Parameters
    ----------
    doc:
        A loaded, mutable schematic document.

    Returns
    -------
    frozenset[str]
        Every ``Reference`` property value found on placed symbols.
    """
    refs: set[str] = set()
    for item in doc.root.items:
        if isinstance(item, ListNode) and item.key == "symbol":
            ref = _get_symbol_ref(item)
            if ref is not None:
                refs.add(ref)
    return frozenset(refs)


def collect_existing_net_names(doc: SchematicDoc) -> frozenset[str]:
    """Return the set of net label names present in *doc*.

    Only direct children of the root ``(kicad_sch …)`` node with key
    ``label`` are returned.

    Parameters
    ----------
    doc:
        A loaded schematic document.

    Returns
    -------
    frozenset[str]
        Every net label name found (deduplicated as a set).
    """
    names: set[str] = set()
    for item in doc.root.items:
        if (
            isinstance(item, ListNode)
            and item.key == "label"
            # (label "NET_NAME" (at …) …)
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ):
            names.add(item.items[1].value)
    return frozenset(names)


# ---------------------------------------------------------------------------
# Reference designator checks
# ---------------------------------------------------------------------------


def check_no_duplicate_refs(
    requested_refs: Sequence[str],
    existing_refs: frozenset[str],
) -> None:
    """Raise ``UserError`` if any ref in *requested_refs* already exists.

    Parameters
    ----------
    requested_refs:
        Reference designators the caller intends to add.
    existing_refs:
        The set returned by :func:`collect_existing_refs`.

    Raises
    ------
    UserError
        When one or more refs would collide with an already-placed symbol.
    """
    conflicts = [r for r in requested_refs if r in existing_refs]
    if conflicts:
        joined = ", ".join(conflicts)
        raise UserError(
            f"Reference designator(s) already in use: {joined}. "
            "Choose different designators (e.g. --r1 R3 --r2 R4)."
        )


def check_refs_unique_in_request(requested_refs: Sequence[str]) -> None:
    """Raise ``UserError`` if the same ref appears more than once.

    Prevents patterns from accidentally placing two components with the same
    reference designator in one call (e.g. passing ``r1_ref="R1"`` and
    ``r2_ref="R1"``).

    Parameters
    ----------
    requested_refs:
        Reference designators for all components in this pattern call.

    Raises
    ------
    UserError
        When any ref value appears more than once.
    """
    seen: set[str] = set()
    dupes: list[str] = []
    for r in requested_refs:
        if r in seen and r not in dupes:
            dupes.append(r)
        seen.add(r)
    if dupes:
        joined = ", ".join(dupes)
        raise UserError(
            f"Duplicate reference designator(s) within pattern call: {joined}. "
            "Each component must have a unique reference."
        )


# ---------------------------------------------------------------------------
# Net name checks
# ---------------------------------------------------------------------------


def check_net_names_valid(net_names: Sequence[str]) -> None:
    """Raise ``UserError`` for any net name that KiCad would reject.

    Rules enforced:

    * Name must not be empty.
    * Name must not contain whitespace, commas, semicolons, quotes, or
      parentheses.

    .. note::

        Net names that start with a digit (e.g. ``"3V3"``, ``"+5V"``) are
        explicitly *allowed* because they are very common in KiCad projects.

    Parameters
    ----------
    net_names:
        All net names the caller intends to use.

    Raises
    ------
    UserError
        When one or more names violate the rules above.
    """
    errors: list[str] = []
    for name in net_names:
        if not name:
            errors.append("(empty string): net name must not be empty")
        elif _NET_FORBIDDEN_RE.search(name):
            errors.append(
                f"{name!r}: net name contains forbidden characters "
                "(whitespace, commas, semicolons, quotes, or parentheses)"
            )
    if errors:
        msg = "Invalid net name(s):\n" + "\n".join(f"  {e}" for e in errors)
        raise UserError(msg)


# ---------------------------------------------------------------------------
# Symbol library check
# ---------------------------------------------------------------------------


def check_symbol_accessible(
    lib_sym: str,
    *,
    symbols_dir: Path | None,
) -> None:
    """Raise ``UserError`` if the library is available but *lib_sym* is absent.

    The check is **skipped** when *symbols_dir* is ``None`` so that patterns
    remain usable in offline / dry-run / test environments where no KiCad
    installation is present.

    Parameters
    ----------
    lib_sym:
        Fully-qualified symbol identifier, e.g. ``"Device:R"``.
    symbols_dir:
        KiCad symbol library directory.  Pass ``None`` to skip the check.

    Raises
    ------
    UserError
        When *symbols_dir* is set but *lib_sym* cannot be found there.
    ValueError
        When *lib_sym* does not contain a ``:`` separator.
    """
    if symbols_dir is None:
        return
    if ":" not in lib_sym:
        raise ValueError(f"lib_sym must be 'LibName:SymName', got {lib_sym!r}")
    lib_name, sym_name = lib_sym.split(":", 1)
    pins = read_lib_symbol_pins(lib_name, sym_name, symbols_dir=symbols_dir)
    if not pins:
        raise UserError(
            f"Symbol {lib_sym!r} not found in library directory {symbols_dir}. "
            "Check the library name, or use --symbols-dir to specify the correct path."
        )


# ---------------------------------------------------------------------------
# Footprint check
# ---------------------------------------------------------------------------


def check_footprints_assigned(
    refs_and_footprints: Sequence[tuple[str, str]],
    *,
    require: bool = False,
) -> None:
    """Raise ``UserError`` when components are missing footprints (if required).

    By default (*require* = ``False``) this function is a no-op: footprints
    are optional for schematic-only workflows.  Pass ``require=True`` when the
    output is intended for PCB layout generation.

    Parameters
    ----------
    refs_and_footprints:
        ``(ref, footprint)`` pairs for every component in the pattern.
    require:
        When ``True``, raise :class:`~kicad_pcb.errors.UserError` if any
        component has an empty footprint string.

    Raises
    ------
    UserError
        When *require* is ``True`` and one or more footprints are missing.
    """
    if not require:
        return
    missing = [ref for ref, fp in refs_and_footprints if not fp.strip()]
    if missing:
        joined = ", ".join(missing)
        raise UserError(
            f"Component(s) are missing footprint assignments: {joined}. "
            "Footprints are required for PCB layout generation. "
            "Use the pattern's footprint flag(s) to assign one, "
            "e.g. --footprint 'Resistor_SMD:R_0402_1005Metric'."
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get_symbol_ref(sym_node: ListNode) -> str | None:
    """Extract the ``Reference`` property value from a placed (symbol …) node."""
    for item in sym_node.items:
        if (
            isinstance(item, ListNode)
            and item.key == "property"
            and len(item.items) >= 3
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "Reference"
            and isinstance(item.items[2], StringNode)
        ):
            return item.items[2].value
    return None
