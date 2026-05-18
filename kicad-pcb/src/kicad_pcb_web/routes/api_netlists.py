"""Netlist API routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from ..schemas import ValidateNetlistRequest, ValidateNetlistResponse
from ..services.netlists import validate_netlist_dict

router = APIRouter()


@router.post("/netlists/validate", response_model=ValidateNetlistResponse)
def validate_netlist(request: ValidateNetlistRequest) -> ValidateNetlistResponse:
    """Validate a Circuit IR payload without creating a project."""

    return validate_netlist_dict(
        netlist_json=request.netlist_json,
        symbols_dir=Path(request.symbols_dir).expanduser().resolve()
        if request.symbols_dir
        else None,
    )
