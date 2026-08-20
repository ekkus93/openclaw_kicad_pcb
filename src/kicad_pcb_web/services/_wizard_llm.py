"""Wizard LLM helpers: error formatting, message builders, JSON calling."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NoReturn, TypeVar

from pydantic import BaseModel, ValidationError

from kicad_pcb.errors import UserError

from ..errors import (
    LlmCompletionRefusedError,
    LlmCompletionTruncatedError,
    LlmInvalidStructuredOutputError,
    LlmNoUsableContentError,
    ProviderUnavailableError,
)
from ..settings import WebSettings
from ..wizard_models import WizardSessionDetail
from .llm import LlmClient, LlmImage, LlmMessage, LlmRequest

LOGGER = logging.getLogger("uvicorn.error")

_MODEL_T = TypeVar("_MODEL_T", bound=BaseModel)


@dataclass(frozen=True)
class StructuredJsonCallOptions:
    max_repairs: int = 0
    debug_artifact_writer: Callable[[dict[str, object]], None] | None = None
    images: tuple[LlmImage, ...] = ()


class _RepairableStructuredOutputError(Exception):
    def __init__(
        self,
        *,
        kind: str,
        repair_message: str,
        error_type: str,
        assistant_content: str | None = None,
    ) -> None:
        super().__init__(repair_message)
        self.kind = kind
        self.repair_message = repair_message
        self.error_type = error_type
        self.assistant_content = assistant_content


def _ir_contract_text() -> str:
    return (
        "Canonical Circuit IR rules:\n"
        "- netlist_json must use only top-level keys: version, components, nets, "
        "optional options.\n"
        "- every component object must include ref and symbol.\n"
        "- symbol must be an exact KiCad library id in the format LibraryName:PartName "
        "(e.g. Device:R, Device:C, Device:LED, Timer:NE555, 4xxx:CD4017BE). "
        "Never use an unqualified name like 'CD4017' without the library prefix.\n"
        "- every net object must include name and pins.\n"
        "- pins must be an array of objects with ref and pin, plus optional unit.\n"
        "- each (ref, pin) pair must appear in exactly ONE net across the entire "
        "netlist. Never list the same physical component pin in two or more nets.\n"
        "Pin numbers must be the numeric KiCad library pin numbers, not schematic "
        "pin name strings. Critical pin number rules for common symbols:\n"
        "- Device:R and Device:C: pins are '1' and '2'. Never use 'A', 'B', '+', '-'.\n"
        "- Device:LED: pins are '1' (anode/+) and '2' (cathode/-). "
        "Never use 'A', 'K', 'anode', 'cathode'.\n"
        "- Device:D (generic diode): pins are 'A' (anode) and 'K' (cathode).\n"
        "- Device:Q_NPN_BCE and Device:Q_PNP_BCE transistors: "
        "pins are 'B' (base), 'C' (collector), 'E' (emitter).\n"
        "- Timer:NE555: pin '1'=GND, '2'=TRIG, '3'=OUT, '4'=RESET, "
        "'5'=CTRL, '6'=THR, '7'=DIS, '8'=VCC.\n"
        "- Connector_Generic:Conn_01xNN: pins are '1' through 'N' numerically.\n"
        "- when the design uses a dual timer such as a 556, use the canonical "
        "symbol Timer:NE556 instead of inventing separate 555 packages.\n"
        "- for a 556, keep one component ref such as U1 and use the optional unit "
        "field on pins to distinguish timer A vs timer B when needed.\n"
        "- for a 556, describe each timer half with its own trigger/threshold/"
        "discharge/output topology rather than merging both timing sections onto one node.\n"
        "- never use nodes instead of pins.\n"
        "- never omit component symbol fields.\n"
        "- never wrap the netlist in metadata, data, or other outer objects."
    )


def _format_error_location(location: Any) -> str:
    if not isinstance(location, (list, tuple)):
        return str(location)
    parts: list[str] = []
    for item in location:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}.{item}"
            else:
                parts.append(str(item))
        else:
            parts.append(str(item))
    return ".".join(parts)


def _format_ir_repair_error(exc: UserError) -> str:
    lines = [str(exc), f"Error code: {exc.code}"]
    details = exc.details if isinstance(exc.details, dict) else {}

    # Pin-in-multiple-nets collisions — most actionable detail to expose
    pin_collisions = details.get("pin_collisions")
    if isinstance(pin_collisions, list) and pin_collisions:
        lines.append(
            "Each physical pin must belong to exactly ONE net. "
            "Remove the pin from all but one net for each collision below:"
        )
        for collision in pin_collisions[:12]:
            if not isinstance(collision, dict):
                continue
            ref = collision.get("ref", "?")
            pin = collision.get("pin", "?")
            nets = collision.get("nets", [])
            lines.append(
                f"- {ref} pin {pin} is listed in {len(nets)} nets: "
                + ", ".join(str(n) for n in nets)
                + ". Keep it in exactly ONE of these nets."
            )

    # Unqualified symbol IDs (missing library prefix)
    unqualified_syms = details.get("unqualified_symbols")
    if isinstance(unqualified_syms, list) and unqualified_syms:
        lines.append(
            "Symbol IDs must use 'LibraryName:PartName' format. "
            "Add the correct library prefix to each of these:"
        )
        for entry in unqualified_syms[:12]:
            if isinstance(entry, dict):
                lines.append(
                    f"- {entry.get('ref')}: '{entry.get('symbol')}' is missing a library "
                    f"prefix (e.g. 'Device:{entry.get('symbol')}' or '4xxx:{entry.get('symbol')}')"
                )

    # Duplicate ref or net name errors
    for key, label in (
        ("duplicate_refs", "Duplicate component refs"),
        ("duplicate_nets", "Duplicate net names"),
    ):
        values = details.get(key)
        if isinstance(values, list) and values:
            lines.append(f"{label} (each must be unique): {', '.join(str(v) for v in values)}")

    # Invalid pin references
    missing_refs = details.get("missing_component_refs")
    if isinstance(missing_refs, list) and missing_refs:
        lines.append("Pin references to unknown component refs:")
        for entry in missing_refs[:8]:
            if isinstance(entry, dict):
                lines.append(
                    f"- net {entry.get('net')} references {entry.get('ref')} "
                    f"pin {entry.get('pin')}, but {entry.get('ref')} is not in components"
                )

    # Schema-level field errors
    errors = details.get("errors")
    if isinstance(errors, list) and errors:
        lines.append("Field-level validation errors:")
        for entry in errors[:12]:
            if not isinstance(entry, dict):
                continue
            location = _format_error_location(entry.get("loc", []))
            message = str(entry.get("msg", "invalid value"))
            if location:
                lines.append(f"- {location}: {message}")
            else:
                lines.append(f"- {message}")

    return "\n".join(lines)


def _build_spec_messages(settings: WebSettings, session: WizardSessionDetail) -> list[LlmMessage]:
    system_prompt = (
        "You are designing an electronic circuit specification for a KiCad wizard. "
        "Stay within these supported v1 categories: passive RC networks, op-amp stages, "
        "timer-based circuits, simple transistor/FET stages, connectors, power rails, "
        "and decoupling. "
        "Do not generate Circuit IR or KiCad output directly. "
        f"Prompt version: {settings.llm.system_prompt_version}. "
        "Return JSON only with keys: assistant_message, next_state, spec, assumptions, "
        "open_questions, unsupported_reasons. "
        "Use next_state=awaiting_user_clarification when required details are missing, "
        "next_state=spec_ready_for_review when the spec is coherent enough for explicit "
        "approval, "
        "or next_state=failed when the requested design is unsupported. "
        "Keep unsupported_reasons explicit instead of guessing.\n"
        "Block specification rules:\n"
        "- Every block must name at least one specific, real component in "
        "required_components (e.g. 'NE555', 'CD4017', 'LM358', '2N3904'). "
        "Vague descriptions like 'counter/decoder' or 'sequencing logic' are not "
        "acceptable without a named part.\n"
        "- Only use block_type='custom' when no standard block_type fits AND the "
        "required_components list already names the specific IC or component that "
        "implements the block. Never use block_type='custom' with an empty or vague "
        "required_components — if you do not know which specific component to use, "
        "add a question to open_questions asking the user to specify one, and set "
        "next_state=awaiting_user_clarification instead of spec_ready_for_review.\n"
        "- If a block requires an IC that may not be in the standard KiCad symbol "
        "library (e.g. CD4017, 74HC595, shift registers), name it explicitly in "
        "required_components so the user can verify availability before approving."
    )
    messages = [LlmMessage(role="system", content=system_prompt)]
    if session.spec is not None:
        messages.append(
            LlmMessage(
                role="system",
                content="Current spec draft JSON:\n" + session.spec.model_dump_json(indent=2),
            )
        )
    messages.extend(
        LlmMessage(role=message.role, content=message.content) for message in session.messages
    )
    return messages


def _build_ir_messages(
    settings: WebSettings,
    session: WizardSessionDetail,
    *,
    prior_ir_json: dict[str, object] | None = None,
    repair_error: str | None = None,
) -> list[LlmMessage]:
    assert session.spec is not None
    system_prompt = (
        "Convert the approved circuit specification into canonical Circuit IR JSON "
        "for the KiCad web wizard. "
        "Return JSON only with keys: assistant_message, netlist_json, assumptions. "
        + _ir_contract_text()
        + " "
        "Do not include prose outside the JSON object. "
        f"Prompt version: {settings.llm.system_prompt_version}."
    )
    messages = [LlmMessage(role="system", content=system_prompt)]
    messages.append(
        LlmMessage(
            role="user",
            content=(
                "Approved circuit specification JSON:\n" + session.spec.model_dump_json(indent=2)
            ),
        )
    )
    if repair_error is not None:
        repair_content = (
            "The previous Circuit IR attempt failed validation. "
            f"Repair it using this exact error context:\n{repair_error}\n\n" + _ir_contract_text()
        )
        if prior_ir_json is not None:
            repair_content += "\n\nPrevious netlist JSON:\n" + json.dumps(prior_ir_json, indent=2)
        messages.append(LlmMessage(role="user", content=repair_content))
    return messages


def _raise_structured_output_exhausted(
    error: _RepairableStructuredOutputError,
    *,
    response_model: type[BaseModel],
    attempts: int,
) -> NoReturn:
    details: dict[str, object] = {
        "response_model": response_model.__name__,
        "attempts": attempts,
        "error_type": error.error_type,
    }
    if error.kind == "no_usable_content":
        raise LlmNoUsableContentError(
            "The configured LLM returned no usable content after all repair attempts.",
            details=details,
        ) from error
    raise LlmInvalidStructuredOutputError(
        "The configured LLM returned invalid structured output after all repair attempts.",
        details=details,
    ) from error


def _call_llm_for_json_once(
    *,
    llm_client: LlmClient,
    messages: list[LlmMessage],
    response_model: type[_MODEL_T],
    attempt: int,
    options: StructuredJsonCallOptions = StructuredJsonCallOptions(),
) -> _MODEL_T:
    request = LlmRequest(
        messages=list(messages),
        response_format="json",
        json_schema=response_model.model_json_schema(),
        images=options.images,
    )
    prompt_chars = sum(len(message.content) for message in request.messages)
    serialized_messages = [
        {"role": message.role, "content": message.content} for message in request.messages
    ]
    started_at = time.perf_counter()
    LOGGER.info(
        "wizard structured json attempt started",
        extra={
            "response_model": response_model.__name__,
            "attempt": attempt,
            "prompt_message_count": len(request.messages),
            "prompt_chars": prompt_chars,
        },
    )
    try:
        completion = llm_client.complete(request)
    except LlmNoUsableContentError as exc:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
        LOGGER.warning(
            "wizard structured json attempt returned no usable content",
            extra={
                "response_model": response_model.__name__,
                "attempt": attempt,
                "elapsed_ms": elapsed_ms,
                "error_type": type(exc).__name__,
            },
        )
        if options.debug_artifact_writer is not None:
            options.debug_artifact_writer(
                {
                    "attempt": attempt,
                    "response_model": response_model.__name__,
                    "messages": serialized_messages,
                    "completion_error": {"code": exc.code, "message": str(exc)},
                }
            )
        raise _RepairableStructuredOutputError(
            kind="no_usable_content",
            repair_message=str(exc),
            error_type=type(exc).__name__,
        ) from exc

    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
    # OpenAI-compatible clients classify these terminal reasons before returning.
    # Keep this fallback as intentional interim fail-closed protection for Ollama
    # and any other self-unclassified client until provider-owned semantics exist.
    normalized_reason = (completion.finish_reason or "").strip().lower()
    if normalized_reason == "length":
        raise LlmCompletionTruncatedError(
            "The configured LLM response was truncated; increase llm.max_tokens.",
            details={"provider": completion.provider, "finish_reason": completion.finish_reason},
        )
    if normalized_reason in {"content_filter", "refusal"}:
        raise LlmCompletionRefusedError(
            "The configured LLM refused or content-filtered the response.",
            details={"provider": completion.provider, "finish_reason": completion.finish_reason},
        )
    if not completion.content.strip():
        raise _RepairableStructuredOutputError(
            kind="no_usable_content",
            repair_message="The configured LLM provider returned no usable content.",
            error_type="EmptyCompletionContent",
        )

    response_chars = len(completion.content)
    try:
        parsed = json.loads(completion.content)
        validated = response_model.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        LOGGER.warning(
            "wizard structured json attempt failed",
            extra={
                "response_model": response_model.__name__,
                "attempt": attempt,
                "prompt_message_count": len(request.messages),
                "prompt_chars": prompt_chars,
                "response_chars": response_chars,
                "elapsed_ms": elapsed_ms,
                "provider": completion.provider,
                "finish_reason": completion.finish_reason,
                "request_id": completion.request_id,
                "error_type": type(exc).__name__,
            },
        )
        if options.debug_artifact_writer is not None:
            options.debug_artifact_writer(
                {
                    "attempt": attempt,
                    "response_model": response_model.__name__,
                    "messages": serialized_messages,
                    "completion": {
                        "provider": completion.provider,
                        "model": completion.model,
                        "content": completion.content,
                        "finish_reason": completion.finish_reason,
                        "request_id": completion.request_id,
                    },
                    "parse_error": str(exc),
                }
            )
        raise _RepairableStructuredOutputError(
            kind="invalid_structured_output",
            repair_message=(
                f"The previous response did not match the required JSON contract. Error: {exc}"
            ),
            error_type=type(exc).__name__,
            assistant_content=completion.content,
        ) from exc

    LOGGER.info(
        "wizard structured json attempt completed",
        extra={
            "response_model": response_model.__name__,
            "attempt": attempt,
            "prompt_message_count": len(request.messages),
            "prompt_chars": prompt_chars,
            "response_chars": response_chars,
            "elapsed_ms": elapsed_ms,
            "provider": completion.provider,
            "finish_reason": completion.finish_reason,
            "request_id": completion.request_id,
        },
    )
    if options.debug_artifact_writer is not None:
        options.debug_artifact_writer(
            {
                "attempt": attempt,
                "response_model": response_model.__name__,
                "messages": serialized_messages,
                "completion": {
                    "provider": completion.provider,
                    "model": completion.model,
                    "content": completion.content,
                    "finish_reason": completion.finish_reason,
                    "request_id": completion.request_id,
                },
                "parsed": parsed,
            }
        )
    return validated


def _call_llm_for_json(
    *,
    llm_client: LlmClient,
    messages: list[LlmMessage],
    response_model: type[_MODEL_T],
    options: StructuredJsonCallOptions = StructuredJsonCallOptions(),
) -> _MODEL_T:
    schema_json = json.dumps(response_model.model_json_schema(), indent=2)
    current_messages = list(messages)
    max_attempts = options.max_repairs + 1
    for attempt in range(1, max_attempts + 1):
        try:
            return _call_llm_for_json_once(
                llm_client=llm_client,
                messages=current_messages,
                response_model=response_model,
                attempt=attempt,
                options=options,
            )
        except _RepairableStructuredOutputError as exc:
            if attempt >= max_attempts:
                _raise_structured_output_exhausted(
                    exc, response_model=response_model, attempts=attempt
                )
            if exc.assistant_content:
                current_messages.append(LlmMessage(role="assistant", content=exc.assistant_content))
            current_messages.append(
                LlmMessage(
                    role="user",
                    content=(
                        f"{exc.repair_message} Return corrected JSON only.\n"
                        f"JSON schema:\n{schema_json}"
                    ),
                )
            )
    raise AssertionError("structured-output attempt loop exhausted unexpectedly")


def _require_llm_client(llm_client: LlmClient | None) -> LlmClient:
    if llm_client is None:
        raise ProviderUnavailableError(
            "The LLM wizard is disabled in the current web-app configuration."
        )
    return llm_client
