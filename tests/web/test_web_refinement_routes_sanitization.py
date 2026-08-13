from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kicad_pcb.errors import UserError
from kicad_pcb_web import refinement_routes
from kicad_pcb_web.deps import get_llm_client, get_settings
from kicad_pcb_web.errors import PersistedStateError, ResourceNotFoundError
from kicad_pcb_web.services.refinement_api import RefinementRunResponse
from kicad_pcb_web.services.refinement_config import RefinementFeatureConfig
from kicad_pcb_web.settings import LlmSettings, WebSettings


def _settings(tmp_path: Path) -> WebSettings:
    return WebSettings(
        data_dir=tmp_path / "data",
        jobs_dir=tmp_path / "data" / "jobs",
        llm=LlmSettings(
            provider="openai",
            model="vision-model",
            vision_enabled=True,
        ),
    )


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    def fake_run(**kwargs):
        request = kwargs["request"]
        return RefinementRunResponse(
            status="stopped",
            stop_reason="REFINEMENT_STOP_NO_OPERATIONS",
            session_id=request.session_id,
            starting_hash="a" * 64,
            final_accepted_hash="a" * 64,
            best_accepted_hash="a" * 64,
            latest_attempted_hash=None,
            starting_layout_fingerprint="b" * 64,
            final_layout_fingerprint="b" * 64,
            rounds_attempted=1,
            accepted_rounds=0,
            rejected_rounds=0,
            accepted_operations=0,
            model_calls_made=1,
            model_call_limit=6,
            evidence_available=True,
        )

    monkeypatch.setattr(refinement_routes, "run_wizard_refinement_request", fake_run)
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: _settings(tmp_path)
    app.dependency_overrides[get_llm_client] = object
    refinement_routes.install_refinement_routes(
        app,
        config=RefinementFeatureConfig(enabled=True),
    )
    return TestClient(app)


def test_refinement_invalid_body_is_generic_and_does_not_echo_secret_or_path(
    monkeypatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)
    private_path = "/tmp/private/design.kicad_sch"
    secret = "super-secret-api-key"

    response = client.post(
        "/api/refinement/run",
        json={
            "session_id": "session-001",
            "accepted_path": private_path,
            "max_rounds": 99,
            "api_key": secret,
        },
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "REFINEMENT_INVALID_REQUEST",
            "message": "Invalid schematic refinement request.",
        }
    }
    assert private_path not in response.text
    assert secret not in response.text
    assert "accepted_path" not in response.text
    assert "api_key" not in response.text


def test_refinement_malformed_json_is_generic(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/refinement/run",
        content=b'{"session_id":',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "REFINEMENT_INVALID_REQUEST",
            "message": "Invalid schematic refinement request.",
        }
    }
    assert "session_id" not in response.text


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (
            ResourceNotFoundError(
                "Missing wizard canary-session-secret.",
                code="RESOURCE_NOT_FOUND",
                details={"session_id": "canary-session-secret"},
            ),
            404,
            "RESOURCE_NOT_FOUND",
        ),
        (
            PersistedStateError(
                "Unsafe /home/operator/private/board.kicad_sch for canary-session-secret.",
                code="REFINEMENT_TARGET_PATH_INVALID",
                details={
                    "session_id": "canary-session-secret",
                    "private_path": "/home/operator/private/board.kicad_sch",
                    "credential": "provider-secret-token",
                },
            ),
            500,
            "REFINEMENT_TARGET_PATH_INVALID",
        ),
    ],
)
def test_refinement_web_service_errors_are_route_sanitized(
    monkeypatch,
    tmp_path: Path,
    error,
    expected_status: int,
    expected_code: str,
) -> None:
    def fail_run(**kwargs):
        raise error

    monkeypatch.setattr(refinement_routes, "run_wizard_refinement_request", fail_run)
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: _settings(tmp_path)
    app.dependency_overrides[get_llm_client] = object
    refinement_routes.install_refinement_routes(
        app,
        config=RefinementFeatureConfig(enabled=True),
    )
    client = TestClient(app)

    response = client.post("/api/refinement/run", json={"session_id": "session-001"})

    assert response.status_code == expected_status
    assert response.json() == {
        "detail": {
            "code": expected_code,
            "message": "Schematic refinement request could not be completed.",
        }
    }
    assert "canary-session-secret" not in response.text
    assert "/home/operator" not in response.text
    assert "provider-secret-token" not in response.text


def test_refinement_service_error_is_generic_and_does_not_echo_internal_data(
    monkeypatch, tmp_path: Path
) -> None:
    private_path = "/tmp/private/design.kicad_sch"
    secret = "super-secret-api-key"

    def fail_run(**kwargs):
        raise UserError(
            f"provider failed for {private_path} using {secret}",
            code="REFINEMENT_PROVIDER_FAILED",
            details={"path": private_path, "api_key": secret},
        )

    monkeypatch.setattr(refinement_routes, "run_wizard_refinement_request", fail_run)
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: _settings(tmp_path)
    app.dependency_overrides[get_llm_client] = object
    refinement_routes.install_refinement_routes(
        app,
        config=RefinementFeatureConfig(enabled=True),
    )
    client = TestClient(app)

    response = client.post("/api/refinement/run", json={"session_id": "session-001"})

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "REFINEMENT_PROVIDER_FAILED",
            "message": "Schematic refinement request could not be completed.",
        }
    }
    assert private_path not in response.text
    assert secret not in response.text


def test_refinement_safe_request_still_dispatches(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/refinement/run",
        json={"session_id": "session-001"},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "session-001"
