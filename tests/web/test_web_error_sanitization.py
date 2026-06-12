"""Tests for public error-payload sanitization in errors.py."""

from __future__ import annotations

import json as _json

import pytest
from fastapi.testclient import TestClient

from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb_web.errors import kicad_error_to_payload
from kicad_pcb_web.main import app

_INVALID_NETLIST = {"version": "1"}  # missing components and nets


# ── kicad_error_to_payload sanitization ──────────────────────────────────────


def _make_user_error(message: str, **details: object) -> UserError:
    return UserError(message, code=ErrorCode.IR_SCHEMA_INVALID, details=dict(details))


def _payload_message(exc: UserError) -> str:
    return str(kicad_error_to_payload(exc)["error"]["message"])  # type: ignore[index]


def _payload_details(exc: UserError) -> dict[str, object]:
    return kicad_error_to_payload(exc)["error"]["details"]  # type: ignore[index,return-value]


def test_error_payload_redacts_tmp_path_in_message() -> None:
    exc = _make_user_error("Invalid JSON in /tmp/kicad-pcb-web-prepare-abc/circuit_ir.json")
    assert "/tmp/" not in _payload_message(exc)
    assert "<redacted-path>" in _payload_message(exc)


def test_error_payload_redacts_home_path_in_message() -> None:
    exc = _make_user_error("Could not read /home/alice/private/project/file.kicad_sch")
    assert "/home/alice/" not in _payload_message(exc)
    assert "<redacted-path>" in _payload_message(exc)


def test_error_payload_redacts_users_path_in_message() -> None:
    exc = _make_user_error("Could not read /Users/alice/private/project/file.kicad_sch")
    assert "/Users/alice/" not in _payload_message(exc)
    assert "<redacted-path>" in _payload_message(exc)


def test_error_payload_redacts_var_folders_in_message() -> None:
    exc = _make_user_error("Failure under /var/folders/xyz/private-dir/file")
    assert "/var/folders/" not in _payload_message(exc)
    assert "<redacted-path>" in _payload_message(exc)


def test_error_payload_redacts_private_var_in_message() -> None:
    exc = _make_user_error("Failure under /private/var/folders/xyz/file")
    assert "/private/var/" not in _payload_message(exc)
    assert "<redacted-path>" in _payload_message(exc)


def test_error_payload_preserves_normal_message() -> None:
    exc = _make_user_error("Component R1 has no matching symbol in Device:R")
    msg = _payload_message(exc)
    assert msg == "Component R1 has no matching symbol in Device:R"


def test_error_payload_redacts_string_detail() -> None:
    exc = _make_user_error("bad", path="/tmp/kicad-pcb-web-prepare-abc/circuit_ir.json")
    details = _payload_details(exc)
    assert "/tmp/" not in str(details["path"])
    assert "<redacted-path>" in str(details["path"])


def test_error_payload_redacts_list_detail() -> None:
    exc = _make_user_error(
        "bad",
        searched=["/tmp/abc/", "/home/alice/symbols/"],
    )
    details = _payload_details(exc)
    searched = details["searched"]
    assert isinstance(searched, list)
    for item in searched:
        assert "/tmp/" not in str(item)
        assert "/home/" not in str(item)


def test_error_payload_redacts_dict_detail() -> None:
    exc = _make_user_error(
        "bad",
        files={
            "input": "/home/alice/private/circuit_ir.json",
            "output": "/tmp/kicad-pcb-web-prepare-abc/circuit_ir.json",
        },
    )
    details = _payload_details(exc)
    files = details["files"]
    assert isinstance(files, dict)
    for v in files.values():
        assert "/home/" not in str(v)
        assert "/tmp/" not in str(v)
        assert "<redacted-path>" in str(v)


def test_error_payload_does_not_redact_component_refs() -> None:
    exc = _make_user_error("bad", hint="Check R1, U2, C3")
    details = _payload_details(exc)
    assert "R1" in str(details["hint"])
    assert "U2" in str(details["hint"])


def test_error_payload_does_not_redact_kicad_library_ids() -> None:
    exc = _make_user_error("bad", hint="Use Device:R or Connector_Generic:Conn_01x01")
    details = _payload_details(exc)
    assert "Device:R" in str(details["hint"])


# ── Integration: validation route inherits sanitization ──────────────────────


def test_validate_netlist_errors_do_not_contain_private_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))

    client = TestClient(app)
    response = client.post("/api/netlists/validate", json={"netlist_json": _INVALID_NETLIST})

    assert response.status_code == 200
    payload = response.json()
    # symbols_dirs_used legitimately contains library filesystem paths.
    # Only the structured errors must not leak private temp/home paths.
    errors_text = _json.dumps(payload.get("errors", []))
    assert "/tmp/" not in errors_text
    assert "kicad-pcb-web-prepare" not in errors_text


@pytest.mark.parametrize(
    "embedded_path",
    [
        "/tmp/kicad-pcb-web-prepare-abc/circuit_ir.json",
        "/home/alice/private/project/file.kicad_sch",
        "/Users/alice/private/project/file.kicad_sch",
        "/var/folders/abc/private/file",
        "/private/var/folders/abc/private/file",
    ],
)
def test_kicad_error_to_payload_redacts_embedded_path(embedded_path: str) -> None:
    exc = _make_user_error(f"Error involving {embedded_path} during processing")
    msg = _payload_message(exc)
    assert embedded_path not in msg
    assert "<redacted-path>" in msg
