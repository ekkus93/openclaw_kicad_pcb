from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kicad_pcb_web import refinement_routes
from kicad_pcb_web.refinement_routes import RefinementRouteDependencies
from kicad_pcb_web.services.refinement_api import RefinementRunResponse
from kicad_pcb_web.services.refinement_config import RefinementFeatureConfig


def _dependencies(tmp_path: Path) -> RefinementRouteDependencies:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"accepted")
    return RefinementRouteDependencies(
        accepted_path=lambda: accepted,
        runtime=object,  # type: ignore[arg-type]
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

    monkeypatch.setattr(refinement_routes, "run_configured_refinement_request", fake_run)
    app = FastAPI()
    refinement_routes.install_refinement_routes(
        app,
        config=RefinementFeatureConfig(enabled=True),
        dependencies=_dependencies(tmp_path),
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


def test_refinement_safe_request_still_dispatches(monkeypatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/refinement/run",
        json={"session_id": "session-001"},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "session-001"
