"""SPA shell routes for the React frontend."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from ..deps import STATIC_DIR

router = APIRouter()

_SPA_DIR = STATIC_DIR / "spa"
_SPA_INDEX = _SPA_DIR / "index.html"


def _spa_index_response() -> FileResponse:
    if not _SPA_INDEX.is_file():
        raise HTTPException(
            status_code=503,
            detail=(
                "Frontend bundle is missing. Run `npm run build` in `frontend/` "
                "to generate the React app."
            ),
        )
    return FileResponse(_SPA_INDEX, media_type="text/html")


@router.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    """Serve the SPA entrypoint."""

    return _spa_index_response()


@router.get("/wizard", response_class=HTMLResponse)
async def wizard_root() -> FileResponse:
    """Serve the SPA wizard landing route."""

    return _spa_index_response()


@router.get("/wizard/{session_id}", response_class=HTMLResponse)
async def wizard_session(session_id: str) -> FileResponse:
    """Serve one SPA wizard session route."""

    del session_id
    return _spa_index_response()


@router.get("/wizard/{session_id}/{step}", response_class=HTMLResponse)
async def wizard_step(session_id: str, step: str) -> FileResponse:
    """Serve one SPA wizard step route."""

    del session_id
    del step
    return _spa_index_response()


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(job_id: str) -> FileResponse:
    """Serve one SPA job-detail route."""

    del job_id
    return _spa_index_response()
