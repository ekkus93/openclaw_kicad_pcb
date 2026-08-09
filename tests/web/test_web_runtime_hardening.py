"""Runtime hardening regressions for startup, jobs, diagnostics, and error hygiene."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from kicad_pcb_web.deps import get_settings
from kicad_pcb_web.errors import (
    PersistenceError,
    WebServiceError,
    validation_error_to_payload,
    web_service_error_to_payload,
)
from kicad_pcb_web.main import app
from kicad_pcb_web.schemas import CreateJobFromNetlistRequest
from kicad_pcb_web.services.doctor import run_doctor
from kicad_pcb_web.services.jobs import (
    create_job_workspace,
    read_job,
    reconcile_interrupted_jobs,
    update_job_status,
)
from kicad_pcb_web.services.netlists import (
    _job_relative_path,
    generate_project_from_netlist_job,
)
from kicad_pcb_web.settings import WebSettings


def _settings(tmp_path: Path) -> WebSettings:
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True)
    return WebSettings(data_dir=data_dir, jobs_dir=jobs_dir)


def test_get_settings_is_process_stable_until_cache_is_explicitly_cleared(
    tmp_path: Path, monkeypatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(first))
    get_settings.cache_clear()

    first_settings = get_settings()
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(second))
    assert get_settings() is first_settings
    assert get_settings().data_dir == first.resolve()

    get_settings.cache_clear()
    assert get_settings().data_dir == second.resolve()


def test_app_lifespan_fails_before_serving_when_explicit_config_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_CONFIG_FILE", str(tmp_path / "missing.toml"))
    get_settings.cache_clear()

    with (
        pytest.raises(ValueError, match="does not exist or is not a regular file"),
        TestClient(app),
    ):
        pass


def test_reconcile_interrupted_jobs_marks_running_job_failed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    record = create_job_workspace(settings, "Interrupted", {"source": "test"})
    update_job_status(settings, record, status="running")

    assert reconcile_interrupted_jobs(settings) == [record.id]

    recovered = read_job(settings, record.id)
    assert recovered.status == "failed"
    assert recovered.error is not None
    assert recovered.error["code"] == "JOB_INTERRUPTED_BY_RESTART"


def test_persisted_job_paths_are_derived_from_canonical_workspace(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    record = create_job_workspace(settings, "CanonicalPaths", {"source": "test"})
    payload = json.loads(record.job_json_path.read_text(encoding="utf-8"))
    payload["work_dir"] = "/tmp/attacker-controlled"
    payload["input_path"] = "/tmp/secret-input"
    payload["project_dir"] = "/tmp/secret-project"
    payload["artifacts_dir"] = "/tmp/secret-artifacts"
    record.job_json_path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = read_job(settings, record.id)
    assert loaded.work_dir == record.work_dir.resolve()
    assert loaded.input_path == record.work_dir.resolve() / "input" / "circuit_ir.json"
    assert loaded.project_dir == record.work_dir.resolve() / "project" / "CanonicalPaths"
    assert loaded.artifacts_dir == record.work_dir.resolve() / "artifacts"


def test_job_relative_path_fails_closed_on_workspace_escape(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    record = create_job_workspace(settings, "Containment", {"source": "test"})
    escaped = tmp_path / "outside" / "secret.txt"

    with pytest.raises(PersistenceError) as caught:
        _job_relative_path(record, escaped)

    payload = web_service_error_to_payload(caught.value)
    serialized = json.dumps(payload)
    assert str(escaped) not in serialized
    assert payload["error"]["code"] == "JOB_PATH_CONTAINMENT_VIOLATION"


def test_unexpected_job_failure_persists_correlation_id(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)

    def explode(**_kwargs):
        raise RuntimeError("unexpected failure at /tmp/private-diagnostic.txt")

    monkeypatch.setattr(
        "kicad_pcb_web.services.netlists._validate_with_optional_autofix",
        explode,
    )
    job = generate_project_from_netlist_job(
        settings=settings,
        request=CreateJobFromNetlistRequest(project_name="Correlation", netlist_json={}),
    )

    assert job.status == "failed"
    assert job.error is not None
    assert job.error["code"] == "INTERNAL_SERVER_ERROR"
    assert str(job.error["details"]["error_id"]).startswith("err_")


def test_unexpected_job_failure_api_returns_same_correlation_id(
    tmp_path: Path, monkeypatch
) -> None:
    private_message = "unexpected failure at /tmp/private-api-diagnostic.txt"
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "api-data"))
    get_settings.cache_clear()

    def explode(**_kwargs):
        raise RuntimeError(private_message)

    monkeypatch.setattr(
        "kicad_pcb_web.services.netlists._validate_with_optional_autofix",
        explode,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/jobs/from-netlist",
            json={"project_name": "CorrelationApi", "netlist_json": {}},
        )
        assert response.status_code == 500
        payload = response.json()
        details = payload["error"]["details"]
        job_id = details["job_id"]
        error_id = details["error_id"]
        assert str(error_id).startswith("err_")

        persisted = client.get(f"/api/jobs/{job_id}")
        assert persisted.status_code == 200
        persisted_error_id = persisted.json()["error"]["details"]["error_id"]

    assert persisted_error_id == error_id
    assert private_message not in json.dumps(payload)
    assert "/tmp/private-api-diagnostic.txt" not in json.dumps(payload)


def test_validation_payload_omits_rejected_input_values() -> None:
    exc = RequestValidationError(
        [
            {
                "type": "string_type",
                "loc": ("body", "project_name"),
                "msg": "Input should be a valid string",
                "input": "TOP-SECRET-REJECTED-VALUE",
                "ctx": {"reason": "bad value at C:\\Users\\private\\secret.txt"},
            }
        ]
    )

    payload = validation_error_to_payload(exc)
    serialized = json.dumps(payload)
    assert "TOP-SECRET-REJECTED-VALUE" not in serialized
    assert '"input"' not in serialized
    assert "C:\\\\Users" not in serialized
    assert "<redacted-path>" in serialized


def test_web_service_error_redacts_windows_absolute_paths() -> None:
    payload = web_service_error_to_payload(
        WebServiceError(
            "failed at C:\\Users\\private\\secret.txt",
            details={"path": "C:\\Users\\private\\secret.txt"},
        )
    )
    serialized = json.dumps(payload)
    assert "C:\\\\Users" not in serialized
    assert "<redacted-path>" in serialized


def test_doctor_requires_exact_preview_dependencies(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)

    def only_kicad(name: str) -> str | None:
        if name == "kicad-cli":
            return "/usr/bin/kicad-cli"
        return None

    monkeypatch.setattr("kicad_pcb_web.services.doctor.shutil.which", only_kicad)
    checks = {check.name: check for check in run_doctor(settings).checks}
    assert checks["kicad_cli"].ok is True
    assert checks["rsvg_convert"].ok is False
    assert checks["preview_tooling"].ok is False


def test_doctor_preview_ready_only_when_both_tools_exist(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    paths = {"kicad-cli": "/usr/bin/kicad-cli", "rsvg-convert": "/usr/bin/rsvg-convert"}
    monkeypatch.setattr("kicad_pcb_web.services.doctor.shutil.which", paths.get)

    checks = {check.name: check for check in run_doctor(settings).checks}
    assert checks["preview_tooling"].ok is True
    assert "llm_network_probe" not in checks
