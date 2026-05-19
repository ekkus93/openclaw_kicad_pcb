"""Path safety tests for artifact downloads."""

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


def test_web_artifact_parent_traversal_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    job_response = client.post(
        "/api/jobs/from-netlist",
        json={
            "project_name": "SafetyJob",
            "netlist_json": _VALID_NETLIST,
            "validation": "internal",
        },
    )
    job_id = job_response.json()["id"]

    response = client.get(f"/api/jobs/{job_id}/artifacts/..%2Fjob.json")
    assert response.status_code == 400


def test_web_artifact_nested_path_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    job_response = client.post(
        "/api/jobs/from-netlist",
        json={
            "project_name": "SafetyJobNested",
            "netlist_json": _VALID_NETLIST,
            "validation": "internal",
        },
    )
    job_id = job_response.json()["id"]

    response = client.get(f"/api/jobs/{job_id}/artifacts/foo/bar")
    assert response.status_code == 400
