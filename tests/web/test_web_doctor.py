"""Doctor endpoint tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kicad_pcb_web.main import app


def test_web_doctor_returns_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.get("/api/doctor")

    assert response.status_code == 200
    payload = response.json()
    assert "ok" in payload
    assert isinstance(payload["checks"], list)
    check_names = {check["name"] for check in payload["checks"]}
    assert {"llm_provider", "llm_configuration", "preview_tooling"} <= check_names
    assert "llm_network_probe" not in check_names
