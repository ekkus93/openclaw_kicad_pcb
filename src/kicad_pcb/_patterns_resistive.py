"""Resistive patterns: resistor-divider and LED-resistor."""

from __future__ import annotations

from pathlib import Path

from ._patterns_base import (
    PIN_OFFSET,
    V_SPACING,
    PatternOutcome,
    PlacedComponent,
    _place_component,
    _place_label,
)
from .preflight import (
    check_footprints_assigned,
    check_net_names_valid,
    check_no_duplicate_refs,
    check_refs_unique_in_request,
    check_symbol_accessible,
    collect_existing_refs,
)
from .sch_doc import SchematicDoc


def pattern_resistor_divider(  # noqa: PLR0913
    doc: SchematicDoc,
    origin_x: float,
    origin_y: float,
    *,
    r1_ref: str = "R1",
    r2_ref: str = "R2",
    r1_value: str = "10k",
    r2_value: str = "10k",
    vin_net: str = "VIN",
    vout_net: str = "VOUT",
    gnd_net: str = "GND",
    footprint: str = "",
    symbols_dir: Path | None = None,
    project_name: str = "project",
    require_footprints: bool = False,
) -> PatternOutcome:
    """Place a voltage-divider sub-circuit on *doc*.

    Layout::

        VIN label  (x, y0 - PIN_OFFSET)
        ────────────────────────────────
               [R1]   centre (x, y0)
        ────────────────────────────────
        VOUT label  (x, y0 + PIN_OFFSET)
        ────────────────────────────────
               [R2]   centre (x, y0 + V_SPACING)
        ────────────────────────────────
        GND label  (x, y0 + V_SPACING + PIN_OFFSET)

    Parameters
    ----------
    doc:
        Mutable schematic document to modify.
    origin_x, origin_y:
        Placement origin (centre of R1).
    r1_ref, r2_ref:
        Reference designators.
    r1_value, r2_value:
        Component values (e.g. ``"10k"``, ``"4.7k"``).
    vin_net, vout_net, gnd_net:
        Net names for the supply, tap, and ground.
    footprint:
        Optional footprint string applied to both resistors.
    symbols_dir:
        Override KiCad symbol library search path.
    project_name:
        KiCad project name (embedded in symbol instances section).
    require_footprints:
        When ``True``, raise :class:`~kicad_pcb.errors.UserError` if either
        resistor has no footprint assigned.
    """
    # --- Preflight checks ---
    check_refs_unique_in_request([r1_ref, r2_ref])
    check_no_duplicate_refs([r1_ref, r2_ref], collect_existing_refs(doc))
    check_net_names_valid([vin_net, vout_net, gnd_net])
    check_symbol_accessible("Device:R", symbols_dir=symbols_dir)
    check_footprints_assigned(
        [(r1_ref, footprint), (r2_ref, footprint)], require=require_footprints
    )
    # --- Placement ---
    x = origin_x
    y0 = origin_y
    y_mid = y0 + PIN_OFFSET  # shared connection point: R1-pin2 / R2-pin1
    y_bot = y0 + V_SPACING  # R2 centre
    y_gnd = y_bot + PIN_OFFSET  # GND pin

    r1 = _place_component(
        doc,
        "Device:R",
        r1_ref,
        r1_value,
        footprint,
        x,
        y0,
        symbols_dir=symbols_dir,
        project_name=project_name,
    )
    r2 = _place_component(
        doc,
        "Device:R",
        r2_ref,
        r2_value,
        footprint,
        x,
        y_bot,
        symbols_dir=symbols_dir,
        project_name=project_name,
    )

    _place_label(doc, vin_net, x, y0 - PIN_OFFSET)
    _place_label(doc, vout_net, x, y_mid)
    _place_label(doc, gnd_net, x, y_gnd)

    return PatternOutcome(
        components=(r1, r2),
        nets=(vin_net, vout_net, gnd_net),
    )


def pattern_led_resistor(  # noqa: PLR0913
    doc: SchematicDoc,
    origin_x: float,
    origin_y: float,
    *,
    r_ref: str = "R1",
    d_ref: str = "D1",
    r_value: str = "330",
    led_value: str = "LED",
    vcc_net: str = "VCC",
    gnd_net: str = "GND",
    r_footprint: str = "",
    led_footprint: str = "",
    symbols_dir: Path | None = None,
    project_name: str = "project",
    require_footprints: bool = False,
) -> PatternOutcome:
    """Place an LED + current-limiting resistor sub-circuit on *doc*.

    Layout (vertical, anode at top):

    .. code-block::

        VCC label   (x, y0 − PIN_OFFSET)
        [R1]        centre (x, y0)
        [D1]        centre (x, y0 + V_SPACING)
        GND label   (x, y0 + V_SPACING + PIN_OFFSET)

    Parameters
    ----------
    doc:
        Mutable schematic document.
    origin_x, origin_y:
        Placement origin (centre of R1).
    r_ref, d_ref:
        Reference designators for the resistor and LED.
    r_value, led_value:
        Values displayed on the schematic.
    vcc_net, gnd_net:
        Supply and ground net names.
    r_footprint, led_footprint:
        Optional footprint strings.
    symbols_dir:
        Override KiCad symbol library path.
    project_name:
        KiCad project name.
    require_footprints:
        When ``True``, raise :class:`~kicad_pcb.errors.UserError` if the
        resistor or LED has no footprint assigned.
    """
    # --- Preflight checks ---
    check_refs_unique_in_request([r_ref, d_ref])
    check_no_duplicate_refs([r_ref, d_ref], collect_existing_refs(doc))
    check_net_names_valid([vcc_net, gnd_net])
    check_symbol_accessible("Device:R", symbols_dir=symbols_dir)
    check_symbol_accessible("Device:LED", symbols_dir=symbols_dir)
    check_footprints_assigned(
        [(r_ref, r_footprint), (d_ref, led_footprint)], require=require_footprints
    )
    # --- Placement ---
    x = origin_x
    y0 = origin_y
    y_d = y0 + V_SPACING
    y_gnd = y_d + PIN_OFFSET

    r: PlacedComponent = _place_component(
        doc,
        "Device:R",
        r_ref,
        r_value,
        r_footprint,
        x,
        y0,
        symbols_dir=symbols_dir,
        project_name=project_name,
    )
    d: PlacedComponent = _place_component(
        doc,
        "Device:LED",
        d_ref,
        led_value,
        led_footprint,
        x,
        y_d,
        symbols_dir=symbols_dir,
        project_name=project_name,
    )

    _place_label(doc, vcc_net, x, y0 - PIN_OFFSET)
    _place_label(doc, gnd_net, x, y_gnd)

    return PatternOutcome(
        components=(r, d),
        nets=(vcc_net, gnd_net),
    )
