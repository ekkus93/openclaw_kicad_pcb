"""FastAPI app entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from kicad_pcb.errors import KiCadError, UserError

from .deps import STATIC_DIR
from .errors import (
    handle_kicad_error,
    handle_request_validation_error,
    handle_unexpected_error,
    handle_user_error,
)
from .routes import api_doctor, api_jobs, api_netlists, api_symbols, ui

app = FastAPI(title="KiCad PCB Web App")

app.add_exception_handler(UserError, handle_user_error)
app.add_exception_handler(KiCadError, handle_kicad_error)
app.add_exception_handler(RequestValidationError, handle_request_validation_error)
app.add_exception_handler(Exception, handle_unexpected_error)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(ui.router)
app.include_router(api_doctor.router, prefix="/api")
app.include_router(api_symbols.router, prefix="/api")
app.include_router(api_netlists.router, prefix="/api")
app.include_router(api_jobs.router, prefix="/api")
