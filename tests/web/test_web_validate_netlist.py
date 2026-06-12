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

_INVALID_NETLIST = {"version": "1"}  # missing components and nets


def test_web_validate_netlist_accepts_valid_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.post("/api/netlists/validate", json={"netlist_json": _VALID_NETLIST})

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["component_count"] == 3
    assert payload["net_count"] == 2
    assert payload["errors"] == []


def test_web_validate_netlist_returns_200_with_valid_false_for_invalid_ir(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.post("/api/netlists/validate", json={"netlist_json": _INVALID_NETLIST})

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is False


def test_web_validate_netlist_invalid_ir_has_nonempty_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.post("/api/netlists/validate", json={"netlist_json": _INVALID_NETLIST})

    payload = response.json()
    assert len(payload["errors"]) >= 1
    first = payload["errors"][0]
    assert "message" in first
    assert first["message"]


def test_web_validate_netlist_invalid_ir_has_null_counts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.post("/api/netlists/validate", json={"netlist_json": _INVALID_NETLIST})

    payload = response.json()
    assert payload["component_count"] is None
    assert payload["net_count"] is None


def test_web_validate_netlist_invalid_ir_does_not_leak_temp_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.post("/api/netlists/validate", json={"netlist_json": _INVALID_NETLIST})

    text = response.text
    assert "/tmp/" not in text
    assert "kicad-pcb-web-prepare" not in text
