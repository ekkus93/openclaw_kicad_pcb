"""CLI presentation layer: format command results into printable lines.

All formatting/rendering lives here so command modules stay pure
(return structured data, no ``print()``).  The CLI entry-point calls
:func:`format_result` and prints each returned line.
"""

from __future__ import annotations

# Import sub-modules to trigger @_register decorator calls and populate
# _FORMATTERS in _formatting_core before any format_result() call.
from . import (
    _formatting_component,  # noqa: F401
    _formatting_pcb,  # noqa: F401
    _formatting_project,  # noqa: F401
)

# Re-export public API.
from ._formatting_core import (  # noqa: F401
    _register,
    _ResultEncoder,
    format_result,
    format_result_json,
)

__all__ = ["format_result", "format_result_json"]
