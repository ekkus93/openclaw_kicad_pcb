"""Structured, redacted observability regressions for wizard LLM providers."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from kicad_pcb_web.errors import LlmCompletionTruncatedError
from kicad_pcb_web.services.llm import LlmMessage, LlmRequest, build_llm_client
from kicad_pcb_web.settings import LlmSettings, WebSettings


def _settings(tmp_path: Path) -> WebSettings:
    return WebSettings(
        data_dir=tmp_path,
        jobs_dir=tmp_path / "jobs",
        llm=LlmSettings(
            provider="openai",
            model="model-under-test",
            api_key="API-KEY-SHOULD-NOT-APPEAR",
            retry_max_attempts=1,
        ),
    )


def _record_text(records: list[logging.LogRecord]) -> str:
    safe_records = [
        {
            key: value
            for key, value in record.__dict__.items()
            if isinstance(value, (str, int, float, bool, type(None)))
        }
        for record in records
    ]
    return json.dumps(safe_records, sort_keys=True, default=str)


def test_successful_completion_logs_structured_metadata_without_raw_content(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    prompt_secret = "PROMPT-SECRET-DO-NOT-LOG"
    response_secret = "RESPONSE-SECRET-DO-NOT-LOG"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-safe-id",
                "model": "model-under-test",
                "choices": [{"message": {"content": response_secret}, "finish_reason": "stop"}],
            },
        )

    client = build_llm_client(_settings(tmp_path), transport=httpx.MockTransport(handler))
    assert client is not None
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        completion = client.complete(
            LlmRequest(messages=[LlmMessage(role="user", content=prompt_secret)])
        )
    client.close()  # type: ignore[attr-defined]

    assert completion.content == response_secret
    completion_record = next(
        record for record in caplog.records if record.getMessage() == "llm completion normalized"
    )
    assert completion_record.provider == "openai"
    assert completion_record.model == "model-under-test"
    assert completion_record.completion_outcome == "completed"
    assert completion_record.finish_reason == "stop"
    assert completion_record.provider_request_id == "request-safe-id"
    assert completion_record.response_chars == len(response_secret)

    logged = _record_text(caplog.records)
    assert prompt_secret not in logged
    assert response_secret not in logged
    assert "API-KEY-SHOULD-NOT-APPEAR" not in logged
    assert "/tmp/" not in logged


def test_terminal_completion_logs_normalized_outcome_without_provider_body(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    response_secret = "TRUNCATED-BODY-SECRET"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-truncated",
                "model": "model-under-test",
                "choices": [{"message": {"content": response_secret}, "finish_reason": "length"}],
            },
        )

    client = build_llm_client(_settings(tmp_path), transport=httpx.MockTransport(handler))
    assert client is not None
    with (
        caplog.at_level(logging.INFO, logger="uvicorn.error"),
        pytest.raises(LlmCompletionTruncatedError),
    ):
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="safe")]))
    client.close()  # type: ignore[attr-defined]

    completion_record = next(
        record for record in caplog.records if record.getMessage() == "llm completion normalized"
    )
    assert completion_record.provider == "openai"
    assert completion_record.model == "model-under-test"
    assert completion_record.completion_outcome == "truncated"
    assert completion_record.finish_reason == "length"

    logged = _record_text(caplog.records)
    assert response_secret not in logged
    assert "API-KEY-SHOULD-NOT-APPEAR" not in logged
