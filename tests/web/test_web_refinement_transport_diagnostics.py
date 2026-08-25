from __future__ import annotations

from kicad_pcb.errors import ToolError, UserError
from kicad_pcb_web.refinement_evaluation_cli import _safe_error_details
from kicad_pcb_web.services.refinement_evaluation_corpus import _exception_diagnostics


def test_transport_diagnostics_survive_safe_n3_error_projection() -> None:
    transport_error = ToolError(
        "ollama request failed before a terminal response was received.",
        details={
            "provider": "ollama",
            "endpoint": "/api/chat",
            "retryable": False,
            "error_type": "RemoteProtocolError",
            "elapsed_ms": 223456.7,
            "ambiguous_delivery": True,
            "automatic_retry": False,
            "raw_response": "must-not-leak",
        },
    )

    diagnostics = _exception_diagnostics(transport_error)

    assert diagnostics["cause_error_type"] == "RemoteProtocolError"
    assert diagnostics["cause_elapsed_ms"] == 223456.7
    assert diagnostics["cause_ambiguous_delivery"] is True
    assert diagnostics["cause_automatic_retry"] is False
    assert "cause_raw_response" not in diagnostics

    fixture_error = UserError(
        "Refinement evaluation corpus fixture failed.",
        code="REFINEMENT_EVALUATION_FIXTURE_FAILED",
        details={"fixture_id": "fixture-1", **diagnostics, "raw_response": "must-not-leak"},
    )

    safe = _safe_error_details(fixture_error)

    assert safe is not None
    assert safe["fixture_id"] == "fixture-1"
    assert safe["cause_error_type"] == "RemoteProtocolError"
    assert safe["cause_elapsed_ms"] == 223456.7
    assert safe["cause_ambiguous_delivery"] is True
    assert safe["cause_automatic_retry"] is False
    assert "raw_response" not in safe
