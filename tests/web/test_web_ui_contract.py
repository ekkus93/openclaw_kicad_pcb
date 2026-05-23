"""HTML UI contract tests."""

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


class _ScriptedLlmClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def complete(self, request: LlmRequest) -> LlmCompletion:
        response = self._responses.pop(0)
        return LlmCompletion(
            provider="test",
            model="test-model",
            content=response,
            finish_reason="stop",
        )


def test_job_detail_page_shows_review_fix_sections(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    job_response = client.post(
        "/api/jobs/from-netlist",
        json={"project_name": "UiContractJob", "netlist_json": _VALID_NETLIST},
    )
    job_id = job_response.json()["id"]

    response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    html = response.text
    assert "Warnings" in html
    assert "Diagnostics / Debug" in html
    assert "Artifacts" in html
    assert "Result Summary" in html


def test_home_page_links_to_wizard(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "LLM Wizard" in response.text
    assert "/wizard" in response.text


def test_wizard_page_shows_key_controls(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.get("/wizard")

    assert response.status_code == 200
    html = response.text
    assert "Wizard Steps" in html
    assert "Describe Circuit" in html
    assert "Review Spec" in html
    assert "Review Circuit IR" in html
    assert "Generate Project" in html
    assert "Start Wizard" in html
    assert "Create a New Session" in html
    assert "Each step now has its own page" in html


def test_wizard_session_routes_redirect_and_render_step_pages(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
    app.dependency_overrides[get_llm_client] = lambda: _ScriptedLlmClient(
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
            )
        ]
    )
    client = TestClient(app)

    start_response = client.post(
        "/wizard/start",
        data={"message": "I want a simple passive attenuator."},
        follow_redirects=False,
    )

    assert start_response.status_code == 303
    location = start_response.headers["location"]
    assert "/wizard/" in location
    assert "/spec" in location
    session_path = location.split("?")[0]
    session_id = session_path.split("/")[-2]

    root_redirect = client.get(f"/wizard/{session_id}", follow_redirects=False)
    assert root_redirect.status_code == 303
    assert root_redirect.headers["location"] == f"/wizard/{session_id}/spec"

    blocked_redirect = client.get(
        f"/wizard/{session_id}/generate",
        follow_redirects=False,
    )
    assert blocked_redirect.status_code == 303
    assert blocked_redirect.headers["location"] == f"/wizard/{session_id}/spec"

    spec_page = client.get(f"/wizard/{session_id}/spec")
    assert spec_page.status_code == 200
    assert "Spec Review" in spec_page.text
    assert "Approve Spec" in spec_page.text
    assert "Back to Describe Circuit" in spec_page.text

    app.dependency_overrides.clear()
