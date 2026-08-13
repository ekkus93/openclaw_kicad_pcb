"""Job-generation endpoint tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from kicad_pcb_web.main import app
from kicad_pcb_web.services.jobs import JobRecord
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


@requires_generation_pipeline
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


def test_job_detail_hides_internal_wizard_owner_metadata(tmp_path: Path) -> None:
    work_dir = tmp_path / "jobs" / "job-001"
    record = JobRecord(
        id="job-001",
        status="succeeded",
        project_name="Project",
        created_at="2026-08-13T00:00:00Z",
        updated_at="2026-08-13T00:00:00Z",
        work_dir=work_dir,
        input_path=work_dir / "input" / "circuit_ir.json",
        project_dir=work_dir / "project" / "Project",
        artifacts_dir=work_dir / "artifacts",
        request={
            "project_name": "Project",
            "netlist_json": _VALID_NETLIST,
            "_wizard_session_id": "wiz_private_owner",
        },
        result={"schematic_path": "project/Project/Project.kicad_sch"},
    )

    detail = record.to_detail()

    assert detail.request["project_name"] == "Project"
    assert "_wizard_session_id" not in detail.request
    assert "wiz_private_owner" not in detail.model_dump_json()
    assert record.request["_wizard_session_id"] == "wiz_private_owner"
