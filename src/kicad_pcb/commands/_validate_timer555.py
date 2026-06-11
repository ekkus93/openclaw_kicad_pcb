"""Advisory lint checks for 555-timer PWM circuits.

Implementation is split across:
* :mod:`kicad_pcb.commands._validate_timer555_context`  — dataclass + context builders
* :mod:`kicad_pcb.commands._validate_timer555_pin_ctrl` — pin-role, control/timing, steering
* :mod:`kicad_pcb.commands._validate_timer555_load_freq` — gate/load, frequency, PWM loop
"""

from __future__ import annotations

from ..circuit_ir import CircuitIR
from ..symbol_index import SymbolIndex
from ._validate_timer555_context import _TIMER555_HARD_FAIL_CODES  # noqa: F401
from ._validate_timer555_load_freq import _timer555_pwm_warnings


def _timer555_warnings(
    ir: CircuitIR,
    _symbol_index: SymbolIndex | None,
) -> list[dict[str, object]]:
    return _timer555_pwm_warnings(ir)


__all__ = [
    "_TIMER555_HARD_FAIL_CODES",
    "_timer555_warnings",
]
