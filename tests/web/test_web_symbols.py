"""Symbol search endpoint tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kicad_pcb_web.main import app


def test_web_symbols_search_returns_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.get("/api/symbols/search?q=resistor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "resistor"
    assert isinstance(payload["results"], list)


def test_web_symbols_empty_query_returns_structured_400(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.get("/api/symbols/search?q=")

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "user_error"
