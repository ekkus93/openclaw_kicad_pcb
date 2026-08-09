"""Post-review hardening regressions for wizard and synchronous job semantics."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from kicad_pcb_web.deps import get_llm_client
from kicad_pcb_web.main import app
from kicad_pcb_web.services.llm import LlmCompletion, LlmRequest

_VALID_NETLIST = {
    "version": "1",
    "components": [
        {"ref": "J1", "symbol": "Connector_Generic:Conn_01x01", "value": "In"},
        {"ref": "R1", "symbol": "Device:R", "value": "10k"},
        {"ref": "J2", "symbol": "Connector_Generic:Conn_01x01", "value": "Out"},
    ],
    "nets": [
        {"name": "IN", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "R1", "pin": "1"}]},
        {"name": "OUT", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "J2", "pin": "1"}]},
    ],
}

_SPEC_RESPONSE = json.dumps(
    {
        "assistant_message": "Drafted a reviewable specification.",
        "next_state": "spec_ready_for_review",
        "spec": {
            "project_name": "CheckpointAmp",
            "purpose": "A simple passive attenuation stage.",
        },
        "assumptions": ["Passive attenuation only."],
        "open_questions": [],
        "unsupported_reasons": [],
    }
)

_IR_RESPONSE = json.dumps(
    {
        "assistant_message": "Converted the approved spec into Circuit IR.",
        "netlist_json": _VALID_NETLIST,
        "assumptions": [],
    }
)


class ScriptedThenExplodingLlmClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmCompletion:
        self.requests.append(request)
        if not self.responses:
            raise RuntimeError("transient provider failure /tmp/private-provider-body.json")
        return LlmCompletion(
            provider="test",
            model="test-model",
            content=self.responses.pop(0),
            finish_reason="stop",
        )


def _prepare_valid_ir_session(client: TestClient) -> tuple[str, dict[str, object]]:
    created = client.post(
        "/api/wizard/sessions",
        json={"message": "I want a simple passive attenuator."},
    )
    assert created.status_code == 200
    session_id = created.json()["id"]

    approved = client.post(f"/api/wizard/sessions/{session_id}/approve-spec")
    assert approved.status_code == 200

    ir_response = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert ir_response.status_code == 200
    assert ir_response.json()["status"] == "ir_ready_for_generation"
    return session_id, ir_response.json()


def test_failed_spec_revision_preserves_checkpoint_but_blocks_stale_generation(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    scripted = ScriptedThenExplodingLlmClient([_SPEC_RESPONSE, _IR_RESPONSE])
    app.dependency_overrides[get_llm_client] = lambda: scripted
    client = TestClient(app)

    session_id, checkpoint = _prepare_valid_ir_session(client)

    revision = client.post(
        f"/api/wizard/sessions/{session_id}/messages",
        json={"message": "Change the attenuation ratio."},
    )
    assert revision.status_code == 500

    persisted = client.get(f"/api/wizard/sessions/{session_id}")
    assert persisted.status_code == 200
    failed = persisted.json()
    assert failed["status"] == "failed"
    assert failed["error"]["details"]["operation"] == "revise_spec"
    assert failed["spec"] == checkpoint["spec"]
    assert failed["spec_approved"] is True
    assert failed["ir_json"] == checkpoint["ir_json"]
    assert failed["ir_validation"] == checkpoint["ir_validation"]

    project = client.post(f"/api/wizard/sessions/{session_id}/generate-project")
    assert project.status_code == 409
    regenerate_ir = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert regenerate_ir.status_code == 409

    app.dependency_overrides.clear()


def test_failed_ir_regeneration_preserves_checkpoint_and_requires_explicit_ir_retry(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    scripted = ScriptedThenExplodingLlmClient([_SPEC_RESPONSE, _IR_RESPONSE])
    app.dependency_overrides[get_llm_client] = lambda: scripted
    client = TestClient(app)

    session_id, checkpoint = _prepare_valid_ir_session(client)

    failed_regeneration = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert failed_regeneration.status_code == 500

    persisted = client.get(f"/api/wizard/sessions/{session_id}")
    assert persisted.status_code == 200
    failed = persisted.json()
    assert failed["status"] == "failed"
    assert failed["error"]["details"]["operation"] == "generate_ir"
    assert failed["ir_json"] == checkpoint["ir_json"]
    assert failed["ir_validation"] == checkpoint["ir_validation"]

    stale_project = client.post(f"/api/wizard/sessions/{session_id}/generate-project")
    assert stale_project.status_code == 409

    retry_client = ScriptedThenExplodingLlmClient([_IR_RESPONSE])
    app.dependency_overrides[get_llm_client] = lambda: retry_client
    retry = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert retry.status_code == 200
    assert retry.json()["status"] == "ir_ready_for_generation"

    app.dependency_overrides.clear()


def test_synchronous_direct_job_failure_returns_non_2xx_and_preserves_job(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    app.dependency_overrides.clear()
    client = TestClient(app)

    response = client.post(
        "/api/jobs/from-netlist",
        json={
            "project_name": "InvalidDirectJob",
            "netlist_json": {},
            "validation": "internal",
        },
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "PROJECT_GENERATION_FAILED"
    job_id = error["details"]["job_id"]

    persisted = client.get(f"/api/jobs/{job_id}")
    assert persisted.status_code == 200
    job = persisted.json()
    assert job["status"] == "failed"
    assert job["error"] is not None
