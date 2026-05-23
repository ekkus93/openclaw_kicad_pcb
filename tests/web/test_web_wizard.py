"""Wizard API and schema tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kicad_pcb_web.deps import get_llm_client, get_settings
from kicad_pcb_web.main import app
from kicad_pcb_web.services.llm import LlmCompletion, LlmRequest
from kicad_pcb_web.settings import load_settings
from kicad_pcb_web.wizard_models import CircuitSpec

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

_GOLDEN_CASES_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "wizard" / "spec_to_ir_cases.json"
)
_WIZARD_GOLDEN_CASES = json.loads(_GOLDEN_CASES_PATH.read_text(encoding="utf-8"))


class ScriptedLlmClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmCompletion:
        self.requests.append(request)
        response = self._responses.pop(0)
        return LlmCompletion(
            provider="test",
            model="test-model",
            content=response,
            finish_reason="stop",
        )


def test_circuit_spec_rejects_pathlike_project_name() -> None:
    try:
        CircuitSpec(purpose="Headphone amp", project_name="../bad")
    except ValueError as exc:
        assert "path separators" in str(exc)
    else:
        raise AssertionError("Expected CircuitSpec project-name validation to fail.")


def test_wizard_api_happy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    scripted = ScriptedLlmClient(
        responses=[
            json.dumps(
                {
                    "assistant_message": "Drafted a reviewable specification.",
                    "next_state": "spec_ready_for_review",
                    "spec": {
                        "project_name": "WizardAmp",
                        "purpose": "A simple passive attenuation stage.",
                        "inputs": [{"name": "IN"}],
                        "outputs": [{"name": "OUT"}],
                        "blocks": [
                            {
                                "name": "Attenuator",
                                "block_type": "voltage_divider",
                                "summary": "A single resistor divider stage.",
                            }
                        ],
                        "acceptance_criteria": ["Provide one input and one output."],
                    },
                    "assumptions": ["Passive attenuation only."],
                    "open_questions": [],
                    "unsupported_reasons": [],
                }
            ),
            json.dumps(
                {
                    "assistant_message": "Converted the approved spec into Circuit IR.",
                    "netlist_json": _VALID_NETLIST,
                    "assumptions": [],
                }
            ),
        ]
    )
    app.dependency_overrides[get_llm_client] = lambda: scripted
    client = TestClient(app)

    create_response = client.post(
        "/api/wizard/sessions",
        json={"message": "I want a simple passive attenuator."},
    )
    assert create_response.status_code == 200
    session = create_response.json()
    assert session["status"] == "spec_ready_for_review"
    assert session["spec"]["project_name"] == "WizardAmp"
    assert session["messages"][-1]["role"] == "assistant"
    session_id = session["id"]

    approve_response = client.post(f"/api/wizard/sessions/{session_id}/approve-spec")
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "spec_approved"

    ir_response = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert ir_response.status_code == 200
    ir_payload = ir_response.json()
    assert ir_payload["status"] == "ir_ready_for_generation"
    assert ir_payload["ir_validation"]["valid"] is True
    assert ir_payload["ir_validation"]["component_count"] == 3

    project_response = client.post(f"/api/wizard/sessions/{session_id}/generate-project")
    assert project_response.status_code == 200
    project_payload = project_response.json()
    assert project_payload["session"]["status"] == "completed"
    assert project_payload["job"]["status"] == "succeeded"

    app.dependency_overrides.clear()


def test_wizard_generate_ir_requires_approved_spec(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    scripted = ScriptedLlmClient(
        responses=[
            json.dumps(
                {
                    "assistant_message": "Need one more detail.",
                    "next_state": "awaiting_user_clarification",
                    "spec": {
                        "purpose": "A simple passive attenuator.",
                    },
                    "assumptions": [],
                    "open_questions": ["What attenuation ratio do you want?"],
                    "unsupported_reasons": [],
                }
            )
        ]
    )
    app.dependency_overrides[get_llm_client] = lambda: scripted
    app.dependency_overrides[get_settings] = load_settings
    client = TestClient(app)

    create_response = client.post(
        "/api/wizard/sessions",
        json={"message": "I want a passive attenuator."},
    )
    session_id = create_response.json()["id"]

    ir_response = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert ir_response.status_code == 400
    assert "Approve the circuit spec" in ir_response.json()["error"]["message"]

    app.dependency_overrides.clear()


def test_wizard_debug_artifacts_are_persisted_only_when_enabled(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_DEBUG_ARTIFACT_CAPTURE", "true")
    scripted = ScriptedLlmClient(
        responses=[
            json.dumps(
                {
                    "assistant_message": "Drafted a reviewable specification.",
                    "next_state": "spec_ready_for_review",
                    "spec": {
                        "project_name": "DebugWizardAmp",
                        "purpose": "A simple passive attenuation stage.",
                    },
                    "assumptions": ["Passive attenuation only."],
                    "open_questions": [],
                    "unsupported_reasons": [],
                }
            )
        ]
    )
    app.dependency_overrides[get_llm_client] = lambda: scripted
    client = TestClient(app)

    response = client.post(
        "/api/wizard/sessions",
        json={"message": "I want a simple passive attenuator."},
    )
    assert response.status_code == 200
    session_id = response.json()["id"]

    artifact_dir = tmp_path / "data" / "wizard_sessions" / session_id / "debug_artifacts"
    artifacts = sorted(artifact_dir.glob("spec_*.json"))
    assert len(artifacts) == 1
    artifact_payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert artifact_payload["response_model"] == "SpecConversationOutput"
    assert artifact_payload["messages"][-1]["content"] == "I want a simple passive attenuator."
    assert artifact_payload["completion"]["provider"] == "test"

    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "case",
    _WIZARD_GOLDEN_CASES,
    ids=[case["name"] for case in _WIZARD_GOLDEN_CASES],
)
def test_wizard_spec_to_ir_golden_cases(tmp_path, monkeypatch, case) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    scripted = ScriptedLlmClient(
        responses=[
            json.dumps(case["spec_response"]),
            json.dumps(case["ir_response"]),
        ]
    )
    app.dependency_overrides[get_llm_client] = lambda: scripted
    client = TestClient(app)

    create_response = client.post(
        "/api/wizard/sessions",
        json={"message": case["request_message"]},
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["id"]

    approve_response = client.post(f"/api/wizard/sessions/{session_id}/approve-spec")
    assert approve_response.status_code == 200

    ir_response = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert ir_response.status_code == 200
    ir_payload = ir_response.json()

    assert ir_payload["status"] == "ir_ready_for_generation"
    assert ir_payload["spec"]["project_name"] == case["expected_project_name"]
    assert sorted(component["ref"] for component in ir_payload["ir_json"]["components"]) == sorted(
        case["expected_component_refs"]
    )
    assert sorted(net["name"] for net in ir_payload["ir_json"]["nets"]) == sorted(
        case["expected_net_names"]
    )

    app.dependency_overrides.clear()


def test_wizard_retries_ir_generation_after_invalid_pin_regression(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    scripted = ScriptedLlmClient(
        responses=[
            json.dumps(
                {
                    "assistant_message": "Drafted a reviewable specification.",
                    "next_state": "spec_ready_for_review",
                    "spec": {
                        "project_name": "RepairPins",
                        "purpose": "A passive attenuator.",
                    },
                    "assumptions": [],
                    "open_questions": [],
                    "unsupported_reasons": [],
                }
            ),
            json.dumps(
                {
                    "assistant_message": "Here is the first IR draft.",
                    "netlist_json": {
                        "version": "1",
                        "components": _VALID_NETLIST["components"],
                        "nets": [
                            {
                                "name": "IN",
                                "pins": [
                                    {"ref": "J1", "pin": "1"},
                                    {"ref": "R1", "pin": "99"},
                                ],
                            },
                            _VALID_NETLIST["nets"][1],
                        ],
                    },
                    "assumptions": [],
                }
            ),
            json.dumps(
                {
                    "assistant_message": "I repaired the invalid pin reference.",
                    "netlist_json": _VALID_NETLIST,
                    "assumptions": [],
                }
            ),
        ]
    )
    app.dependency_overrides[get_llm_client] = lambda: scripted
    client = TestClient(app)

    create_response = client.post(
        "/api/wizard/sessions",
        json={"message": "I want a simple passive attenuator."},
    )
    session_id = create_response.json()["id"]
    client.post(f"/api/wizard/sessions/{session_id}/approve-spec")

    ir_response = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert ir_response.status_code == 200
    ir_payload = ir_response.json()
    assert ir_payload["status"] == "ir_ready_for_generation"
    assert len(scripted.requests) == 3
    assert "failed validation" in scripted.requests[-1].messages[-1].content

    app.dependency_overrides.clear()


def test_wizard_surfaces_ir_needs_repair_for_hallucinated_symbol_regression(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_IR_MAX_REPAIR_ROUNDS", "1")
    scripted = ScriptedLlmClient(
        responses=[
            json.dumps(
                {
                    "assistant_message": "Drafted a reviewable specification.",
                    "next_state": "spec_ready_for_review",
                    "spec": {
                        "project_name": "BadSymbol",
                        "purpose": "A passive attenuator.",
                    },
                    "assumptions": [],
                    "open_questions": [],
                    "unsupported_reasons": [],
                }
            ),
            json.dumps(
                {
                    "assistant_message": "Here is the first IR draft.",
                    "netlist_json": {
                        "version": "1",
                        "components": [
                            {
                                "ref": "U1",
                                "symbol": "Imaginary:Nope",
                                "value": "Nope",
                            }
                        ],
                        "nets": [],
                    },
                    "assumptions": [],
                }
            ),
            json.dumps(
                {
                    "assistant_message": "I tried another repair.",
                    "netlist_json": {
                        "version": "1",
                        "components": [
                            {
                                "ref": "U1",
                                "symbol": "Imaginary:StillNope",
                                "value": "Nope",
                            }
                        ],
                        "nets": [],
                    },
                    "assumptions": [],
                }
            ),
        ]
    )
    app.dependency_overrides[get_llm_client] = lambda: scripted
    client = TestClient(app)

    create_response = client.post(
        "/api/wizard/sessions",
        json={"message": "I want a simple passive attenuator."},
    )
    session_id = create_response.json()["id"]
    client.post(f"/api/wizard/sessions/{session_id}/approve-spec")

    ir_response = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert ir_response.status_code == 200
    ir_payload = ir_response.json()
    assert ir_payload["status"] == "ir_needs_repair"
    assert ir_payload["ir_validation"]["valid"] is False
    assert ir_payload["ir_validation"]["error_message"]
    assert len(scripted.requests) == 3

    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "case",
    [
        {
            "assistant_message": (
                "I need the available supply rails before drafting the op-amp stage."
            ),
            "next_state": "awaiting_user_clarification",
            "open_questions": ["What supply rails are available for the circuit?"],
            "unsupported_reasons": [],
        },
        {
            "assistant_message": (
                "The requested design is unsupported because the constraints "
                "contradict each other."
            ),
            "next_state": "failed",
            "open_questions": [],
            "unsupported_reasons": [
                "The request demands 20 dB of gain while forbidding all active devices."
            ],
        },
    ],
)
def test_wizard_regression_cases_for_ambiguous_or_contradictory_specs(
    tmp_path,
    monkeypatch,
    case,
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    scripted = ScriptedLlmClient(
        responses=[
            json.dumps(
                {
                    "assistant_message": case["assistant_message"],
                    "next_state": case["next_state"],
                    "spec": {
                        "purpose": "An audio front-end stage.",
                    },
                    "assumptions": [],
                    "open_questions": case["open_questions"],
                    "unsupported_reasons": case["unsupported_reasons"],
                }
            )
        ]
    )
    app.dependency_overrides[get_llm_client] = lambda: scripted
    client = TestClient(app)

    create_response = client.post(
        "/api/wizard/sessions",
        json={"message": "Design an audio stage for me."},
    )
    assert create_response.status_code == 200
    session = create_response.json()
    assert session["status"] == case["next_state"]
    assert session["open_questions"] == case["open_questions"]
    assert session["unsupported_reasons"] == case["unsupported_reasons"]

    if case["unsupported_reasons"]:
        approve_response = client.post(f"/api/wizard/sessions/{session['id']}/approve-spec")
        assert approve_response.status_code == 400
        assert "unsupported" in approve_response.json()["error"]["message"].lower()

    app.dependency_overrides.clear()