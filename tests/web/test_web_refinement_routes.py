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
        runtime=lambda: object(),  # type: ignore[arg-type]
    )


def test_disabled_refinement_router_has_no_routes(tmp_path: Path) -> None:
    router = refinement_routes.build_refinement_router(
        config=RefinementFeatureConfig(),
        dependencies=_dependencies(tmp_path),
    )

    assert router.routes == []


def test_enabled_refinement_router_uses_server_owned_dependencies(
    monkeypatch, tmp_path: Path
) -> None:
    dependencies = _dependencies(tmp_path)
    accepted_path = dependencies.accepted_path()
    observed: dict[str, object] = {}

    def fake_run(**kwargs):
        observed.update(kwargs)
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
    app.include_router(
        refinement_routes.build_refinement_router(
            config=RefinementFeatureConfig(enabled=True),
            dependencies=dependencies,
        )
    )
    client = TestClient(app)

    response = client.post("/api/refinement/run", json={"session_id": "session-001"})

    assert response.status_code == 200
    assert response.json()["session_id"] == "session-001"
    assert observed["accepted_path"] == accepted_path
    assert observed["runtime"] is not None
    assert observed["config"].enabled is True  # type: ignore[attr-defined]


def test_enabled_refinement_router_rejects_request_side_path_and_limit_overrides(
    tmp_path: Path,
) -> None:
    app = FastAPI()
    app.include_router(
        refinement_routes.build_refinement_router(
            config=RefinementFeatureConfig(enabled=True),
            dependencies=_dependencies(tmp_path),
        )
    )
    client = TestClient(app)

    response = client.post(
        "/api/refinement/run",
        json={
            "session_id": "session-001",
            "accepted_path": "/tmp/other.kicad_sch",
            "max_rounds": 99,
        },
    )

    assert response.status_code == 422
