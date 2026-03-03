"""Shared component-type classification constants for *kicad_pcb*.

Centralises reference-designator prefix sets, power-net matching, and
tier-spacing constants so that :mod:`layout`, :mod:`graphviz_layout`,
:mod:`tier` and :mod:`router` can all import from one place without
circular imports.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Reference-designator prefix sets
# ---------------------------------------------------------------------------

#: Connector and header prefixes — treated as signal *sources* (left edge).
CONNECTOR_PREFIXES: tuple[str, ...] = ("J", "CON", "P", "SJ", "TJ")

#: Integrated-circuit / op-amp prefixes — treated as core processing nodes.
IC_PREFIXES: tuple[str, ...] = ("U", "IC", "OA")

#: Passive component prefixes — resistors, capacitors, inductors, diodes, BJTs.
PASSIVE_PREFIXES: tuple[str, ...] = ("R", "C", "L", "D", "Q")

#: Miscellaneous component prefixes — batteries, fuses, switches, buttons.
MISC_PREFIXES: tuple[str, ...] = ("BT", "F", "S", "SW")

#: Capacitor-only subset (used for decoupling-cap detection).
CAPACITOR_PREFIXES: tuple[str, ...] = ("C",)

# ---------------------------------------------------------------------------
# Power/ground net identification
# ---------------------------------------------------------------------------

#: Top-level prefixes of power and ground rail net names.
#: Used for simple ``startswith``-style tests in layout heuristics.
POWER_NET_PREFIXES: tuple[str, ...] = (
    "GND",
    "VCC",
    "VDD",
    "VSS",
    "PWR",
    "AGND",
    "PGND",
    "DGND",
    "V+",
    "V-",
    "VBAT",
    "VREF",
)

#: Pre-compiled full-match regex for power/ground net names.
#: Covers named rails, PWR_FLAG, and numeric voltage forms (e.g. +5V, -12V, 3V3).
POWER_NET_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:GND|AGND|DGND|PGND|VCC|VDD|VSS|V\+|V-|VBAT|VREF|0V|"
    r"[+\-]?(?:\d+V\d*|\d*V\d+)|PWR_FLAG)$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Layout spacing constants (in millimetres, matching KiCad's internal grid)
# ---------------------------------------------------------------------------

#: Horizontal distance between adjacent tiers in the auto-placement grid.
TIER_SPACING_MM: float = 30.48  # 1.2 inch

#: X-origin for the first (leftmost) tier.
ORIGIN_X_MM: float = 30.48  # 1.2 inch


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def is_power_net(name: str) -> bool:
    """Return True when *name* is a power or ground rail net.

    Uses :data:`POWER_NET_PATTERN` for a full-name, case-insensitive match.
    Numeric voltage forms such as ``+5V``, ``-12V``, ``3V3``, and ``0V`` are
    recognised in addition to the named prefixes.

    Examples::

        >>> is_power_net("GND")
        True
        >>> is_power_net("VCC")
        True
        >>> is_power_net("+5V")
        True
        >>> is_power_net("0V")
        True
        >>> is_power_net("PWR_FLAG")
        True
        >>> is_power_net("SIGNAL_NET")
        False
    """
    return bool(POWER_NET_PATTERN.match(name))


def component_type(ref: str) -> str:
    """Return the component-type label for *ref*.

    Returns one of ``'connector'``, ``'ic'``, ``'passive'``, ``'misc'``,
    or ``'unknown'``.

    The check is case-insensitive and tests the **uppercase** prefix of *ref*
    against each prefix group in priority order (connector first so that a
    hypothetical ``J``-prefixed IC is still treated as a connector).

    Examples::

        >>> component_type("J1")
        'connector'
        >>> component_type("U3")
        'ic'
        >>> component_type("R12")
        'passive'
        >>> component_type("C7")
        'passive'
        >>> component_type("BT1")
        'misc'
        >>> component_type("XTAL1")
        'unknown'
    """
    upper = ref.upper()
    if any(upper.startswith(p) for p in CONNECTOR_PREFIXES):
        return "connector"
    if any(upper.startswith(p) for p in IC_PREFIXES):
        return "ic"
    if any(upper.startswith(p) for p in PASSIVE_PREFIXES):
        return "passive"
    if any(upper.startswith(p) for p in MISC_PREFIXES):
        return "misc"
    return "unknown"
