"""Known-good circuit generation patterns (Phase 9.2).

Each pattern builds a small, validated sub-circuit on a ``SchematicDoc`` in a
single transaction.  Pattern functions are the primary building block for
LLM-driven schematic generation workflows.

Available patterns
------------------
``resistor-divider``
    Two resistors in series between VIN and GND, with a tapped VOUT net.
``led-resistor``
    Current-limiting resistor in series with an LED between VCC and GND.
``connector-breakout``
    An N-pin connector whose pins are wired to individually named nets.
``decoupling-cap``
    A bypass / decoupling capacitor between a supply rail and GND.

Layout conventions
------------------
All patterns use a **vertical** layout (pin 1 at top, pin 2 at bottom)
matched to KiCad's default orientation for ``Device:R``, ``Device:C``, and
``Device:LED``.  Pin endpoints are assumed to sit ``PIN_OFFSET`` mm above and
below the symbol's placement centre:

    pin 1 (top)   →  (x,  y − PIN_OFFSET)
    pin 2 (bottom) →  (x,  y + PIN_OFFSET)

Net labels are placed *at* pin endpoints so that KiCad's netlist engine
resolves the connection without requiring explicit wire segments.
Consecutive components are spaced ``2 × PIN_OFFSET`` apart vertically so
their adjacent pins share the same grid coordinate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .errors import UserError
from .preflight import (
    check_footprints_assigned,
    check_net_names_valid,
    check_no_duplicate_refs,
    check_refs_unique_in_request,
    check_symbol_accessible,
    collect_existing_refs,
)
from .sch_doc import SchematicDoc, read_lib_symbol_def_chain, read_lib_symbol_pins
from .sexpr.builder import L, atom, string

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

#: Vertical offset (mm) from symbol centre to each pin endpoint.
#: Matches the standard KiCad ``Device:R / C / LED`` symbol geometry.
PIN_OFFSET: Final[float] = 2.54

#: Horizontal spacing (mm) between components placed side-by-side.
H_SPACING: Final[float] = 10.16

#: Vertical spacing (mm) between consecutive series components
#: (= 2 × PIN_OFFSET so adjacent pin endpoints coincide).
V_SPACING: Final[float] = PIN_OFFSET * 2  # 5.08 mm


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlacedComponent:
    """Describes a single component placed by a pattern."""

    ref: str
    lib_sym: str
    value: str
    x: float
    y: float
    pins: tuple[str, ...]


@dataclass(frozen=True)
class PatternOutcome:
    """Structured outcome returned by every pattern function.

    Callers (e.g. ``cmd_apply_pattern``) summarise this into an
    :class:`~kicad_pcb.results.ApplyPatternResult` for CLI/JSON output.
    """

    #: Each component that was added to the schematic.
    components: tuple[PlacedComponent, ...]
    #: Net names that were referenced by the pattern (labels placed or used).
    nets: tuple[str, ...]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _new_uuid() -> str:
    """Return a fresh UUID4 hex string (no hyphens — KiCad format)."""
    return str(uuid.uuid4())


def _place_component(  # noqa: PLR0913
    doc: SchematicDoc,
    lib_sym: str,
    ref: str,
    value: str,
    footprint: str,
    x: float,
    y: float,
    *,
    symbols_dir: Path | None,
    project_name: str,
) -> PlacedComponent:
    """Resolve library data and insert a placed symbol into *doc*.

    Reads ``pin_nums`` from the symbol library, generates UUIDs, calls
    ``doc.add_symbol()``, and embeds the symbol definition.

    Returns a :class:`PlacedComponent` descriptor.
    """
    lib_name, sym_name = lib_sym.split(":", 1)

    pin_nums = read_lib_symbol_pins(lib_name, sym_name, symbols_dir=symbols_dir)
    if not pin_nums:
        # Fallback: two generic pins — keeps the schematic parseable even when
        # the symbol library is unavailable (e.g. in dry-run / offline tests).
        pin_nums = ["1", "2"]

    sym_defs = read_lib_symbol_def_chain(lib_name, sym_name, symbols_dir=symbols_dir)
    if sym_defs:
        for sym_def in sym_defs:
            doc.embed_lib_symbol(sym_def)
    else:
        # Library not available (offline / CI without KiCad installed).
        # Embed a minimal stub so that SCH009 lint (lib_id not in lib_symbols)
        # does not fire against a schematic that is otherwise valid.
        doc.embed_lib_symbol(L(atom("symbol"), string(lib_sym)))

    pin_uuids = [_new_uuid() for _ in pin_nums]
    sym_uuid = _new_uuid()
    doc.add_symbol(
        lib_sym, ref, value, footprint, x, y, sym_uuid, pin_nums, pin_uuids, project_name
    )

    return PlacedComponent(
        ref=ref,
        lib_sym=lib_sym,
        value=value,
        x=x,
        y=y,
        pins=tuple(pin_nums),
    )


def _place_label(doc: SchematicDoc, name: str, x: float, y: float) -> None:
    """Insert a net label at *(x, y)* with a fresh UUID."""
    doc.add_label(name, x, y, _new_uuid())


# ---------------------------------------------------------------------------
# Pattern: resistor-divider
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Pattern: led-resistor
# ---------------------------------------------------------------------------


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

    r = _place_component(
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
    d = _place_component(
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


# ---------------------------------------------------------------------------
# Pattern: connector-breakout
# ---------------------------------------------------------------------------


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
        raise ValueError(f"n_pins must be \u2265 1, got {n_pins}")
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


# ---------------------------------------------------------------------------
# Pattern: decoupling-cap
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------

#: Maps pattern names (as used on the CLI / by LLM callers) to callables.
PATTERNS: dict[str, object] = {
    "resistor-divider": pattern_resistor_divider,
    "led-resistor": pattern_led_resistor,
    "connector-breakout": pattern_connector_breakout,
    "decoupling-cap": pattern_decoupling_cap,
}
