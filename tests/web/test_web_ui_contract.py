"""SPA shell and frontend bootstrap contract tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kicad_pcb_web.main import app


def test_root_serves_react_spa_shell(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="root"' in response.text
    assert "/static/spa/assets/" in response.text


def test_wizard_and_job_routes_serve_same_spa_shell(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)

    for path in (
        "/wizard",
        "/wizard/demo-session",
        "/wizard/demo-session/spec",
        "/jobs/demo-job",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert 'id="root"' in response.text
        assert "/static/spa/assets/" in response.text


def test_ui_bootstrap_reports_provider_and_example_netlist(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.get("/api/ui/bootstrap")

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_provider"]
    assert isinstance(payload["llm_enabled"], bool)
    assert payload["example_netlist_json"]["version"] == "1"
    assert len(payload["example_netlist_json"]["components"]) == 3