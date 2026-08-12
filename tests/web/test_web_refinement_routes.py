from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kicad_pcb_web import refinement_routes
from kicad_pcb_web.deps import get_llm_client, get_settings
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


def _app(monkeypatch, tmp_path: Path, observed: dict[str, object]) -> FastAPI:
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

    async def fake_threadpool(func, *args, **kwargs):
        observed["threadpool_func"] = func
        return func(*args, **kwargs)

    monkeypatch.setattr(refinement_routes, "run_wizard_refinement_request", fake_run)
    monkeypatch.setattr(refinement_routes, "run_in_threadpool", fake_threadpool)
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: _settings(tmp_path)
    app.dependency_overrides[get_llm_client] = object
    app.include_router(
        refinement_routes.build_refinement_router(config=RefinementFeatureConfig(enabled=True))
    )
    return app


def test_disabled_refinement_router_has_no_routes() -> None:
    router = refinement_routes.build_refinement_router(config=RefinementFeatureConfig())

    assert router.routes == []


def test_enabled_refinement_router_uses_request_scoped_server_dependencies(
    monkeypatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}
    client = TestClient(_app(monkeypatch, tmp_path, observed))

    response = client.post("/api/refinement/run", json={"session_id": "session-001"})

    assert response.status_code == 200
    assert response.json()["session_id"] == "session-001"
    assert observed["threadpool_func"] is refinement_routes.run_wizard_refinement_request
    assert observed["settings"] == _settings(tmp_path)
    assert observed["llm_client"] is not None
    assert observed["request"].session_id == "session-001"  # type: ignore[attr-defined]
    assert observed["config"].enabled is True  # type: ignore[attr-defined]


def test_enabled_refinement_router_rejects_request_side_path_and_limit_overrides(
    monkeypatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}
    client = TestClient(_app(monkeypatch, tmp_path, observed))

    response = client.post(
        "/api/refinement/run",
        json={
            "session_id": "session-001",
            "accepted_path": "/tmp/other.kicad_sch",
            "max_rounds": 99,
        },
    )

    assert response.status_code == 422
    assert observed == {}
