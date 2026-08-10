"""Wizard API and schema tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kicad_pcb_web.deps import get_llm_client, get_settings
from kicad_pcb_web.errors import LlmCompletionRefusedError, LlmCompletionTruncatedError
from kicad_pcb_web.main import app
from kicad_pcb_web.services.llm import LlmCompletion, LlmRequest
from kicad_pcb_web.settings import load_settings
from kicad_pcb_web.wizard_models import CircuitSpec
from tests.conftest import requires_generation_pipeline

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
    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmCompletion:
        self.requests.append(request)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
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


@requires_generation_pipeline
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
    assert ir_response.status_code == 409
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
    artifact_text = artifacts[0].read_text(encoding="utf-8")
    artifact_payload = json.loads(artifact_text)
    assert artifact_payload["response_model"] == "SpecConversationOutput"
    assert artifact_payload["provider"] == "test"
    assert artifact_payload["model"] == "test-model"
    assert artifact_payload["prompt_message_count"] >= 2
    assert artifact_payload["prompt_chars"] > len("I want a simple passive attenuator.")
    assert len(artifact_payload["prompt_fingerprint"]) == 16
    assert artifact_payload["response_chars"] > 0
    assert len(artifact_payload["response_fingerprint"]) == 16
    assert "messages" not in artifact_payload
    assert "completion" not in artifact_payload
    assert "I want a simple passive attenuator." not in artifact_text
    assert "Drafted a reviewable specification." not in artifact_text

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


def test_wizard_generate_ir_auto_fixes_common_schema_like_llm_mistakes(
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
                        "project_name": "AutoFixIR",
                        "purpose": "A two-resistor network.",
                    },
                    "assumptions": [],
                    "open_questions": [],
                    "unsupported_reasons": [],
                }
            ),
            json.dumps(
                {
                    "assistant_message": "Converted the approved spec into Circuit IR.",
                    "netlist_json": {
                        "version": "1",
                        "components": [
                            {"ref": "R1", "value": "10k"},
                            {"ref": "R2", "value": "10k"},
                        ],
                        "nets": [
                            {
                                "name": "N1",
                                "nodes": [{"ref": "R1", "pin": 1}, {"ref": "R2", "pin": 1}],
                            },
                            {
                                "name": "N2",
                                "nodes": [{"ref": "R1", "pin": 2}, {"ref": "R2", "pin": 2}],
                            },
                        ],
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
        json={"message": "I want a simple two resistor network."},
    )
    session_id = create_response.json()["id"]
    client.post(f"/api/wizard/sessions/{session_id}/approve-spec")

    ir_response = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert ir_response.status_code == 200
    ir_payload = ir_response.json()

    assert ir_payload["status"] == "ir_ready_for_generation"
    assert ir_payload["ir_validation"]["valid"] is True
    assert ir_payload["ir_validation"]["auto_fixed"] is True
    assert ir_payload["ir_json"]["components"] == [
        {"ref": "R1", "value": "10k", "symbol": "Device:R"},
        {"ref": "R2", "value": "10k", "symbol": "Device:R"},
    ]
    assert ir_payload["ir_json"]["nets"] == [
        {"name": "N1", "pins": [{"ref": "R1", "pin": "1"}, {"ref": "R2", "pin": "1"}]},
        {"name": "N2", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "2"}]},
    ]
    assert len(scripted.requests) == 2

    app.dependency_overrides.clear()


def test_wizard_ir_prompt_requires_symbol_and_pins_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    scripted = ScriptedLlmClient(
        responses=[
            json.dumps(
                {
                    "assistant_message": "Drafted a reviewable specification.",
                    "next_state": "spec_ready_for_review",
                    "spec": {
                        "project_name": "PromptContract",
                        "purpose": "A simple passive attenuation stage.",
                    },
                    "assumptions": [],
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
    session_id = create_response.json()["id"]
    client.post(f"/api/wizard/sessions/{session_id}/approve-spec")
    client.post(f"/api/wizard/sessions/{session_id}/generate-ir")

    ir_system_prompt = scripted.requests[1].messages[0].content
    assert "every component object must include ref and symbol" in ir_system_prompt
    assert "never use nodes instead of pins" in ir_system_prompt
    assert "use the canonical symbol Timer:NE556" in ir_system_prompt
    assert "use the optional unit field on pins" in ir_system_prompt

    app.dependency_overrides.clear()


def test_wizard_generate_ir_auto_fixes_compact_node_tokens_and_invalid_options(
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
                        "project_name": "CompactNodeRepair",
                        "purpose": "Connect two resistors in a simple netlist.",
                    },
                    "assumptions": [],
                    "open_questions": [],
                    "unsupported_reasons": [],
                }
            ),
            json.dumps(
                {
                    "assistant_message": "Converted the approved spec into Circuit IR.",
                    "netlist_json": {
                        "version": "1",
                        "components": [
                            {
                                "ref": "R1",
                                "value": "68k",
                                "footprint": "Resistor_SMD:R_0603_1608Metric",
                            },
                            {
                                "ref": "R2",
                                "value": "100k",
                                "footprint": "Resistor_SMD:R_0603_1608Metric",
                            },
                        ],
                        "nets": [
                            {"name": "N1", "nodes": ["R1.1", "R2.1"]},
                            {"name": "N2", "nodes": ["R1.2", "R2.2"]},
                        ],
                        "options": {"notes": ["generated by llm"]},
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
        json={"message": "I want a simple two resistor network."},
    )
    session_id = create_response.json()["id"]
    client.post(f"/api/wizard/sessions/{session_id}/approve-spec")

    ir_response = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert ir_response.status_code == 200
    ir_payload = ir_response.json()

    assert ir_payload["status"] == "ir_ready_for_generation"
    assert ir_payload["ir_validation"]["valid"] is True
    assert ir_payload["ir_validation"]["auto_fixed"] is True
    assert ir_payload["ir_json"]["nets"] == [
        {
            "name": "N1",
            "pins": [
                {"ref": "R1", "pin": "1"},
                {"ref": "R2", "pin": "1"},
            ],
        },
        {
            "name": "N2",
            "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "2"}],
        },
    ]
    assert "options" not in ir_payload["ir_json"]

    app.dependency_overrides.clear()


def test_wizard_generate_ir_accepts_simple_555_blinker_without_pwm_only_lints(
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
                        "project_name": "Simple555Blinker",
                        "purpose": (
                            "Blink a single LED on and off at approximately 1 Hz using a 555 timer."
                        ),
                    },
                    "assumptions": [],
                    "open_questions": [],
                    "unsupported_reasons": [],
                }
            ),
            json.dumps(
                {
                    "assistant_message": "Converted the approved spec into Circuit IR.",
                    "netlist_json": {
                        "version": "1",
                        "components": [
                            {
                                "ref": "U1",
                                "value": "NE555",
                                "footprint": "Package_DIP:DIP-8_W7.62mm",
                            },
                            {
                                "ref": "R1",
                                "value": "68k",
                                "footprint": "Resistor_SMD:R_0603_1608Metric",
                            },
                            {
                                "ref": "R2",
                                "value": "68k",
                                "footprint": "Resistor_SMD:R_0603_1608Metric",
                            },
                            {
                                "ref": "C1",
                                "value": "10uF",
                                "footprint": "Capacitor_SMD:C_0805_2012Metric",
                            },
                            {
                                "ref": "R3",
                                "value": "330",
                                "footprint": "Resistor_SMD:R_0603_1608Metric",
                            },
                            {
                                "ref": "D1",
                                "value": "red LED",
                                "footprint": "LED_SMD:LED_0603_1608Metric",
                            },
                            {
                                "ref": "C2",
                                "value": "100nF",
                                "footprint": "Capacitor_SMD:C_0603_1608Metric",
                            },
                            {
                                "ref": "C3",
                                "value": "10nF",
                                "footprint": "Capacitor_SMD:C_0603_1608Metric",
                            },
                        ],
                        "nets": [
                            {"name": "VCC", "nodes": ["U1.8", "U1.4", "R1.1", "C2.1"]},
                            {
                                "name": "GND",
                                "nodes": ["U1.1", "C1.2", "D1.2", "C2.2", "C3.2"],
                            },
                            {
                                "name": "NET_TRIG_THRESH",
                                "nodes": ["U1.2", "U1.6", "C1.1", "R2.2"],
                            },
                            {"name": "NET_DISCH", "nodes": ["U1.7", "R1.1", "R2.1"]},
                            {"name": "NET_OUT", "nodes": ["U1.3", "R3.1"]},
                            {"name": "NET_LED_ANODE", "nodes": ["R3.2", "D1.1"]},
                            {"name": "NET_CTRL", "nodes": ["U1.5", "C3.1"]},
                        ],
                        "options": {"notes": ["generated by llm"]},
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
        json={"message": "I want a simple 555 LED blinker."},
    )
    session_id = create_response.json()["id"]
    client.post(f"/api/wizard/sessions/{session_id}/approve-spec")

    ir_response = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert ir_response.status_code == 200
    ir_payload = ir_response.json()

    assert ir_payload["status"] == "ir_ready_for_generation"
    assert ir_payload["ir_validation"]["valid"] is True
    assert ir_payload["ir_validation"]["auto_fixed"] is True
    assert ir_payload["ir_json"]["components"][0]["symbol"] == "Timer:NE555"
    assert any(net["name"] == "GND" for net in ir_payload["ir_json"]["nets"])

    app.dependency_overrides.clear()


def test_regenerating_ir_clears_previous_generation_link(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    scripted = ScriptedLlmClient(
        responses=[
            json.dumps(
                {
                    "assistant_message": "Drafted a reviewable specification.",
                    "next_state": "spec_ready_for_review",
                    "spec": {
                        "project_name": "RegenWizardAmp",
                        "purpose": "A simple passive attenuation stage.",
                    },
                    "assumptions": [],
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
            json.dumps(
                {
                    "assistant_message": "Regenerated the Circuit IR after review.",
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
    client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    project_response = client.post(f"/api/wizard/sessions/{session_id}/generate-project")
    assert project_response.status_code == 200
    assert project_response.json()["session"]["latest_job_id"] is not None

    regenerated_response = client.post(f"/api/wizard/sessions/{session_id}/generate-ir")
    assert regenerated_response.status_code == 200
    regenerated_session = regenerated_response.json()
    assert regenerated_session["status"] == "ir_ready_for_generation"
    assert regenerated_session["latest_job_id"] is None

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
                "The requested design is unsupported because the constraints contradict each other."
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
        assert approve_response.status_code == 409
        assert "unsupported" in approve_response.json()["error"]["message"].lower()

    app.dependency_overrides.clear()


def test_wizard_create_rejects_disabled_provider_with_503(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_PROVIDER", "disabled")
    app.dependency_overrides.clear()
    client = TestClient(app)

    response = client.post(
        "/api/wizard/sessions",
        json={"message": "Design a resistor divider."},
    )

    assert response.status_code == 503
    payload = response.json()["error"]
    assert payload["code"] == "LLM_PROVIDER_UNAVAILABLE"
    assert "wizard is disabled" in payload["message"].lower()


class ExplodingLlmClient:
    def complete(self, request: LlmRequest) -> LlmCompletion:
        del request
        raise RuntimeError("secret upstream body /tmp/private-provider-response.json")


def test_wizard_unexpected_provider_error_is_sanitized_and_persisted(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    app.dependency_overrides[get_llm_client] = ExplodingLlmClient
    client = TestClient(app)

    with caplog.at_level("ERROR"):
        response = client.post(
            "/api/wizard/sessions",
            json={"message": "Design a resistor divider."},
        )

    assert response.status_code == 500
    payload = response.json()["error"]
    assert payload["code"] == "INTERNAL_SERVER_ERROR"
    assert payload["message"] == "An unexpected internal error occurred."
    assert "secret upstream" not in json.dumps(payload)
    error_id = payload["details"]["error_id"]
    session_id = payload["details"]["session_id"]
    assert error_id.startswith("err_")
    assert error_id in caplog.text
    assert "unexpected wizard operation failure" in caplog.text

    persisted = client.get(f"/api/wizard/sessions/{session_id}")
    assert persisted.status_code == 200
    session = persisted.json()
    assert session["status"] == "failed"
    assert session["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert session["error"]["details"]["error_id"] == error_id
    assert "secret upstream" not in json.dumps(session["error"])

    app.dependency_overrides.clear()


def test_wizard_invalid_structured_output_returns_502_and_persists_failure(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_SPEC_MAX_REPAIR_ROUNDS", "0")
    scripted = ScriptedLlmClient(responses=["not-json"])
    app.dependency_overrides[get_llm_client] = lambda: scripted
    client = TestClient(app)

    response = client.post(
        "/api/wizard/sessions",
        json={"message": "Design a resistor divider."},
    )

    assert response.status_code == 502
    payload = response.json()["error"]
    assert payload["code"] == "LLM_INVALID_STRUCTURED_OUTPUT"
    session_id = payload["details"]["session_id"]
    persisted = client.get(f"/api/wizard/sessions/{session_id}")
    assert persisted.status_code == 200
    session = persisted.json()
    assert session["status"] == "failed"
    assert session["failure_kind"] == "operational"
    assert session["error"]["code"] == "LLM_INVALID_STRUCTURED_OUTPUT"

    app.dependency_overrides.clear()


def test_followup_wizard_no_usable_content_returns_502_and_persists_operational_failure(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_SPEC_MAX_REPAIR_ROUNDS", "0")
    scripted = ScriptedLlmClient(responses=[""])
    app.dependency_overrides[get_llm_client] = lambda: scripted
    client = TestClient(app)

    response = client.post(
        "/api/wizard/sessions",
        json={"message": "Design a resistor divider."},
    )

    assert response.status_code == 502
    payload = response.json()["error"]
    assert payload["code"] == "LLM_NO_USABLE_CONTENT"
    assert len(scripted.requests) == 1
    session_id = payload["details"]["session_id"]
    persisted = client.get(f"/api/wizard/sessions/{session_id}")
    assert persisted.status_code == 200
    session = persisted.json()
    assert session["status"] == "failed"
    assert session["failure_kind"] == "operational"
    assert session["error"]["code"] == "LLM_NO_USABLE_CONTENT"

    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("provider_error", "expected_code"),
    [
        (LlmCompletionTruncatedError("simulated truncation"), "LLM_COMPLETION_TRUNCATED"),
        (LlmCompletionRefusedError("simulated refusal"), "LLM_COMPLETION_REFUSED"),
    ],
)
def test_followup_wizard_terminal_completion_errors_return_502_without_retry(
    tmp_path,
    monkeypatch,
    provider_error: Exception,
    expected_code: str,
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    scripted = ScriptedLlmClient(responses=[provider_error])
    app.dependency_overrides[get_llm_client] = lambda: scripted
    client = TestClient(app)

    response = client.post(
        "/api/wizard/sessions",
        json={"message": "Design a resistor divider."},
    )

    assert response.status_code == 502
    payload = response.json()["error"]
    assert payload["code"] == expected_code
    assert len(scripted.requests) == 1
    session_id = payload["details"]["session_id"]
    persisted = client.get(f"/api/wizard/sessions/{session_id}")
    assert persisted.status_code == 200
    session = persisted.json()
    assert session["status"] == "failed"
    assert session["failure_kind"] == "operational"
    assert session["error"]["code"] == expected_code

    app.dependency_overrides.clear()
