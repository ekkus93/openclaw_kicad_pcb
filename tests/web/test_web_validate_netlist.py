"""Netlist validation endpoint tests."""

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


def test_web_validate_netlist_accepts_valid_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.post("/api/netlists/validate", json={"netlist_json": _VALID_NETLIST})

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["component_count"] == 3
    assert payload["net_count"] == 2


def test_web_validate_netlist_returns_structured_400_for_invalid_ir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.post("/api/netlists/validate", json={"netlist_json": {"version": "1"}})

    assert response.status_code == 400
    payload = response.json()
    assert "error" in payload
