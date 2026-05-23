"""UI bootstrap API routes for the React frontend."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_settings
from ..schemas import UiBootstrapResponse
from ..settings import WebSettings

router = APIRouter()

_EXAMPLE_NETLIST = {
    "version": "1",
    "components": [
        {"ref": "J1", "symbol": "Connector_Generic:Conn_01x01", "value": "In"},
        {"ref": "R1", "symbol": "Device:R", "value": "10k"},
        {"ref": "J2", "symbol": "Connector_Generic:Conn_01x01", "value": "Out"},
    ],
    "nets": [
        {
            "name": "IN",
            "pins": [
                {"ref": "J1", "pin": "1"},
                {"ref": "R1", "pin": "1"},
            ],
        },
        {
            "name": "OUT",
            "pins": [
                {"ref": "R1", "pin": "2"},
                {"ref": "J2", "pin": "1"},
            ],
        },
    ],
}


@router.get("/ui/bootstrap", response_model=UiBootstrapResponse)
def ui_bootstrap(settings: WebSettings = Depends(get_settings)) -> UiBootstrapResponse:
    """Return initial config needed by the React frontend shell."""

    return UiBootstrapResponse(
        llm_provider=settings.llm.provider,
        llm_enabled=settings.llm.enabled,
        example_netlist_json=_EXAMPLE_NETLIST,
    )