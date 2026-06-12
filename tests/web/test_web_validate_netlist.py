"""Netlist validation endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb_web.main import app
from kicad_pcb_web.services.netlists import _safe_symbols_dirs_used

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


def test_web_safe_symbols_dirs_used_swallows_user_error(monkeypatch) -> None:
    """UserError from SymbolIndex is expected; _safe_symbols_dirs_used returns []."""

    def raise_user_error(*_a, **_kw) -> None:
        raise UserError("No libraries found", code=ErrorCode.SYMBOL_DIR_MISSING)

    monkeypatch.setattr("kicad_pcb_web.services.netlists.SymbolIndex", raise_user_error)
    assert _safe_symbols_dirs_used(None) == []


def test_web_safe_symbols_dirs_used_propagates_unexpected_error(monkeypatch) -> None:
    """Unexpected exceptions must not be silently swallowed."""

    def raise_runtime(*_a, **_kw) -> None:
        raise RuntimeError("disk failure")

    monkeypatch.setattr("kicad_pcb_web.services.netlists.SymbolIndex", raise_runtime)
    with pytest.raises(RuntimeError, match="disk failure"):
        _safe_symbols_dirs_used(None)


def test_web_validate_netlist_invalid_ir_does_not_leak_temp_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.post("/api/netlists/validate", json={"netlist_json": _INVALID_NETLIST})

    text = response.text
    assert "/tmp/" not in text
    assert "kicad-pcb-web-prepare" not in text
