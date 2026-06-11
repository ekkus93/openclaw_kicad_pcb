"""2-pin direct route and multi-pin hub route for signal nets.

Implementation is split across:
* :mod:`kicad_pcb._router_route_signal_direct` — :func:`_route_direct_net`
* :mod:`kicad_pcb._router_route_signal_hub`    — :func:`_route_hub_net`

All public names remain importable from this module unchanged.
"""

from __future__ import annotations

from ._router_route_signal_direct import _route_direct_net  # noqa: F401
from ._router_route_signal_hub import _route_hub_net  # noqa: F401
from ._router_types import _HUB_MAX_DEGREE  # noqa: F401

__all__ = [
    "_route_direct_net",
    "_route_hub_net",
    "_HUB_MAX_DEGREE",
]
