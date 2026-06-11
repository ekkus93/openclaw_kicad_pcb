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

Implementation is split across focused sub-modules:

- :mod:`._patterns_base`      — constants, dataclasses, placement helpers
- :mod:`._patterns_resistive` — resistor-divider, LED-resistor
- :mod:`._patterns_passive`   — connector-breakout, decoupling-cap
"""

from __future__ import annotations

from ._patterns_base import (  # noqa: F401
    H_SPACING,
    PIN_OFFSET,
    V_SPACING,
    PatternOutcome,
    PlacedComponent,
    _new_uuid,
    _place_component,
    _place_label,
)
from ._patterns_passive import (  # noqa: F401
    pattern_connector_breakout,
    pattern_decoupling_cap,
)
from ._patterns_resistive import (  # noqa: F401
    pattern_led_resistor,
    pattern_resistor_divider,
)

#: Maps pattern names (as used on the CLI / by LLM callers) to callables.
PATTERNS: dict[str, object] = {
    "resistor-divider": pattern_resistor_divider,
    "led-resistor": pattern_led_resistor,
    "connector-breakout": pattern_connector_breakout,
    "decoupling-cap": pattern_decoupling_cap,
}
