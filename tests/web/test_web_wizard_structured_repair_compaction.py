from __future__ import annotations

from pydantic import BaseModel

from kicad_pcb_web.services._wizard_llm import StructuredJsonCallOptions, _call_llm_for_json
from kicad_pcb_web.services.llm import LlmCompletion, LlmMessage, LlmRequest


class _Envelope(BaseModel):
    value: str


class _ScriptedClient:
    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []
        self._responses = ["{", '{"value":"ok"}']

    def complete(self, request: LlmRequest) -> LlmCompletion:
        self.requests.append(request)
        return LlmCompletion(
            provider="test",
            model="test-model",
            content=self._responses.pop(0),
            finish_reason="stop",
        )


def test_structured_repair_keeps_schema_out_of_conversation_prompt() -> None:
    client = _ScriptedClient()
    schema = _Envelope.model_json_schema()

    result = _call_llm_for_json(
        llm_client=client,
        messages=[LlmMessage(role="user", content="Return JSON")],
        response_model=_Envelope,
        options=StructuredJsonCallOptions(max_repairs=1),
    )

    assert result.value == "ok"
    assert len(client.requests) == 2
    assert client.requests[0].json_schema == schema
    assert client.requests[1].json_schema == schema
    repair_prompt = client.requests[1].messages[-1].content
    assert "did not match the required JSON contract" in repair_prompt
    assert "Return corrected JSON only" in repair_prompt
    assert "JSON schema:" not in repair_prompt
    assert "The same JSON schema is enforced separately" in repair_prompt
