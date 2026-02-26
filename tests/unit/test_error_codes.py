from __future__ import annotations

import json

from kicad_pcb.errors import ErrorCode, UserError


def test_user_error_carries_code_and_details() -> None:
    err = UserError("bad input", code=ErrorCode.IR_SCHEMA_INVALID, details={"path": "x.json"})

    assert err.code == ErrorCode.IR_SCHEMA_INVALID
    assert err.details["path"] == "x.json"

    payload = err.as_dict()
    assert payload["code"] == ErrorCode.IR_SCHEMA_INVALID
    assert payload["message"] == "bad input"

    parsed = json.loads(err.to_json())
    assert parsed["details"]["path"] == "x.json"
