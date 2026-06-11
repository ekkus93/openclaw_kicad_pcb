"""Passive patterns: connector-breakout and decoupling-cap."""

from __future__ import annotations

from pathlib import Path

from ._patterns_base import (
    PIN_OFFSET,
    PatternOutcome,
    _place_component,
    _place_label,
)
from .errors import UserError
from .preflight import (
    check_footprints_assigned,
    check_net_names_valid,
    check_no_duplicate_refs,
    check_refs_unique_in_request,
    check_symbol_accessible,
    collect_existing_refs,
)
from .sch_doc import SchematicDoc


def pattern_connector_breakout(  # noqa: PLR0913
    doc: SchematicDoc,
    origin_x: float,
    origin_y: float,
    *,
    conn_ref: str = "J1",
    n_pins: int = 4,
    net_prefix: str = "IO",
    footprint: str = "",
    symbols_dir: Path | None = None,
    project_name: str = "project",
    require_footprints: bool = False,
) -> PatternOutcome:
    """Place an N-pin connector with individually labelled nets.

    One ``Device:Conn_01x{n_pins}`` symbol is placed and each pin receives a
    net label ``{net_prefix}{pin_number}`` (e.g. ``IO1``, ``IO2``, …).

    The connector centre is placed at *origin_x*, *origin_y*.  Vertical pin
    spacing is ``PIN_OFFSET`` mm matching KiCad's Conn_01xN default.

    Parameters
    ----------
    doc:
        Mutable schematic document.
    origin_x, origin_y:
        Placement origin (connector centre).
    conn_ref:
        Reference designator (e.g. ``"J1"``).
    n_pins:
        Number of pins (2–10 are well-tested KiCad library values).
    net_prefix:
        Net label prefix.  Pin N gets net ``{net_prefix}{N}``.
    footprint:
        Optional footprint override.
    symbols_dir:
        Override KiCad symbol library path.
    project_name:
        KiCad project name.
    require_footprints:
        When ``True``, raise :class:`~kicad_pcb.errors.UserError` if the
        connector has no footprint assigned.
    """
    if n_pins < 1:
        raise ValueError(f"n_pins must be ≥ 1, got {n_pins}")
    if not net_prefix:
        raise UserError(
            "net_prefix must not be empty. "
            "Provide a non-empty prefix (e.g. --net-prefix IO) so generated "
            "net names are meaningful (IO1, IO2, …)."
        )
    lib_sym = f"Connector_Generic:Conn_01x{n_pins:02d}"

    # --- Preflight checks ---
    check_refs_unique_in_request([conn_ref])
    check_no_duplicate_refs([conn_ref], collect_existing_refs(doc))
    check_net_names_valid([f"{net_prefix}{i + 1}" for i in range(n_pins)])
    check_symbol_accessible(lib_sym, symbols_dir=symbols_dir)
    check_footprints_assigned([(conn_ref, footprint)], require=require_footprints)
    # --- Placement ---
    x = origin_x
    y_center = origin_y

    conn = _place_component(
        doc,
        lib_sym,
        conn_ref,
        f"Conn_01x{n_pins:02d}",
        footprint,
        x,
        y_center,
        symbols_dir=symbols_dir,
        project_name=project_name,
    )

    # Place one net label per pin on the right side of the connector.
    # Conn_01xN has all pins on the right; pin 1 is topmost, at
    # y_center − (N-1)/2 × PIN_OFFSET, each successive pin PIN_OFFSET lower.
    top_y = y_center - ((n_pins - 1) / 2) * PIN_OFFSET
    nets: list[str] = []
    for i in range(n_pins):
        net = f"{net_prefix}{i + 1}"
        pin_y = top_y + i * PIN_OFFSET
        _place_label(doc, net, x + PIN_OFFSET, pin_y)
        nets.append(net)

    return PatternOutcome(
        components=(conn,),
        nets=tuple(nets),
    )


def pattern_decoupling_cap(  # noqa: PLR0913
    doc: SchematicDoc,
    origin_x: float,
    origin_y: float,
    *,
    c_ref: str = "C1",
    c_value: str = "100nF",
    vcc_net: str = "VCC",
    gnd_net: str = "GND",
    footprint: str = "",
    symbols_dir: Path | None = None,
    project_name: str = "project",
    require_footprints: bool = False,
) -> PatternOutcome:
    """Place a bypass / decoupling capacitor between a supply rail and GND.

    Layout::

        VCC label  (x, y0 − PIN_OFFSET)
        [C1]       centre (x, y0)
        GND label  (x, y0 + PIN_OFFSET)

    Parameters
    ----------
    doc:
        Mutable schematic document.
    origin_x, origin_y:
        Placement origin (capacitor centre).
    c_ref:
        Reference designator.
    c_value:
        Component value (e.g. ``"100nF"``, ``"10uF"``).
    vcc_net, gnd_net:
        Supply and ground net names.
    footprint:
        Optional footprint override.
    symbols_dir:
        Override KiCad symbol library path.
    project_name:
        KiCad project name.
    require_footprints:
        When ``True``, raise :class:`~kicad_pcb.errors.UserError` if the
        capacitor has no footprint assigned.
    """
    # --- Preflight checks ---
    check_refs_unique_in_request([c_ref])
    check_no_duplicate_refs([c_ref], collect_existing_refs(doc))
    check_net_names_valid([vcc_net, gnd_net])
    check_symbol_accessible("Device:C", symbols_dir=symbols_dir)
    check_footprints_assigned([(c_ref, footprint)], require=require_footprints)
    # --- Placement ---
    x = origin_x
    y0 = origin_y

    c = _place_component(
        doc,
        "Device:C",
        c_ref,
        c_value,
        footprint,
        x,
        y0,
        symbols_dir=symbols_dir,
        project_name=project_name,
    )

    _place_label(doc, vcc_net, x, y0 - PIN_OFFSET)
    _place_label(doc, gnd_net, x, y0 + PIN_OFFSET)

    return PatternOutcome(
        components=(c,),
        nets=(vcc_net, gnd_net),
    )
