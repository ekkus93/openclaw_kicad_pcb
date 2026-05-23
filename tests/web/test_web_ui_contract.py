"""HTML UI contract tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kicad_pcb_web.main import app

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
    assert "Wizard Session" in html
    assert "Approve Spec" in html
    assert "Generate IR" in html
    assert "Generate Project" in html
