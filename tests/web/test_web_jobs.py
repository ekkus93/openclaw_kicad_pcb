"""Job-generation endpoint tests."""

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


def test_web_jobs_from_netlist_creates_job_directory(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(data_dir))

    client = TestClient(app)
    response = client.post(
        "/api/jobs/from-netlist",
        json={
            "project_name": "WebTest",
            "netlist_json": _VALID_NETLIST,
            "validation": "internal",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert (data_dir / "jobs" / payload["id"]).is_dir()
    assert "project.zip" in payload["artifacts"]


def test_web_jobs_default_to_internal_validation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.post(
        "/api/jobs/from-netlist",
        json={
            "project_name": "DefaultValidationJob",
            "netlist_json": _VALID_NETLIST,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["request"]["validation"] == "internal"


def test_web_jobs_unknown_job_id_returns_404(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.get("/api/jobs/not-a-real-job")

    assert response.status_code == 404
