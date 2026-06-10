"""Artifact privacy regression tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kicad_pcb_web.main import app
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


@requires_generation_pipeline
def test_job_json_is_not_listed_or_downloadable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    job_response = client.post(
        "/api/jobs/from-netlist",
        json={"project_name": "PrivateJob", "netlist_json": _VALID_NETLIST},
    )
    job_payload = job_response.json()
    job_id = job_payload["id"]

    assert job_response.status_code == 200
    assert "job.json" not in job_payload["artifacts"]

    list_response = client.get(f"/api/jobs/{job_id}/artifacts")
    assert list_response.status_code == 200
    assert "job.json" not in list_response.json()["artifacts"]

    download_response = client.get(f"/api/jobs/{job_id}/artifacts/job.json")
    assert download_response.status_code == 404

    project_zip_response = client.get(f"/api/jobs/{job_id}/artifacts/project.zip")
    assert project_zip_response.status_code == 200
