"""Artifact endpoint tests."""

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


def test_web_artifacts_list_and_download_project_zip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    job_response = client.post(
        "/api/jobs/from-netlist",
        json={
            "project_name": "ArtifactJob",
            "netlist_json": _VALID_NETLIST,
            "validation": "internal",
        },
    )
    job_payload = job_response.json()

    list_response = client.get(f"/api/jobs/{job_payload['id']}/artifacts")
    assert list_response.status_code == 200
    artifacts_payload = list_response.json()
    assert "project.zip" in artifacts_payload["artifacts"]

    download_response = client.get(f"/api/jobs/{job_payload['id']}/artifacts/project.zip")
    assert download_response.status_code == 200
    assert download_response.headers["content-type"] == "application/zip"
