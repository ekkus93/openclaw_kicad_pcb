"""Web error-handling regression tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from kicad_pcb.errors import ErrorCode, ToolError
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


def test_explicit_kicad_validation_returns_structured_tool_error(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    def raise_missing_kicad(*_args, **_kwargs):
        raise ToolError(
            "kicad-cli is required for --mode kicad",
            code=ErrorCode.KICAD_CLI_MISSING,
            details={"hint": "Install KiCad or use validation=internal"},
        )

    monkeypatch.setattr(
        "kicad_pcb_web.services.netlists._apply_netlist_to_project",
        raise_missing_kicad,
    )

    client = TestClient(app)
    response = client.post(
        "/api/jobs/from-netlist",
        json={
            "project_name": "NeedsKiCad",
            "netlist_json": _VALID_NETLIST,
            "validation": "kicad",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["error"]["type"] == "tool_error"
    assert payload["error"]["code"] == "KICAD_CLI_MISSING"
    assert payload["error"]["code"] != "INTERNAL_SERVER_ERROR"


def test_route_level_kicad_error_uses_structured_payload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    def raise_tool_error(*_args, **_kwargs):
        raise ToolError(
            "kicad-cli is required for --mode kicad",
            code=ErrorCode.KICAD_CLI_MISSING,
            details={"hint": "Install KiCad or use validation=internal"},
        )

    monkeypatch.setattr("kicad_pcb_web.routes.api_netlists.validate_netlist_dict", raise_tool_error)

    client = TestClient(app)
    response = client.post("/api/netlists/validate", json={"netlist_json": _VALID_NETLIST})

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "tool_error"
    assert payload["error"]["code"] == "KICAD_CLI_MISSING"
