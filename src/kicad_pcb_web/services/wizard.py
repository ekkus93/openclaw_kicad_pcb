"""Wizard orchestration for LLM-assisted circuit design sessions."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from kicad_pcb.errors import UserError

from ..errors import kicad_error_to_payload
from ..schemas import CreateJobFromNetlistRequest
from ..settings import WebSettings
from ..wizard_models import (
    CreateWizardSessionRequest,
    IrGenerationOutput,
    SpecConversationOutput,
    WizardGenerateProjectResponse,
    WizardIrValidation,
    WizardMessage,
    WizardMessageRequest,
    WizardMessageRole,
    WizardSessionDetail,
)
from .llm import LlmClient, LlmMessage, LlmRequest
from .netlists import generate_project_from_netlist_job, prepare_netlist_dict

LOGGER = logging.getLogger("uvicorn.error")

_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MODEL_T = TypeVar("_MODEL_T", bound=BaseModel)


def _ir_contract_text() -> str:
    return (
        "Canonical Circuit IR rules:\n"
        "- netlist_json must use only top-level keys: version, components, nets, "
        "optional options.\n"
        "- every component object must include ref and symbol.\n"
        "- symbol must be an exact KiCad library id like Device:R, Device:C, "
        "Device:LED, or Timer:NE555.\n"
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _wizard_root(settings: WebSettings) -> Path:
    path = settings.data_dir / "wizard_sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _new_session_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"wiz_{stamp}_{uuid4().hex[:8]}"


def _session_dir(settings: WebSettings, session_id: str) -> Path:
    if not _SAFE_SESSION_ID_RE.fullmatch(session_id):
        raise UserError(f"Unsafe wizard session id: {session_id!r}")
    return _wizard_root(settings) / session_id


def _session_json_path(settings: WebSettings, session_id: str) -> Path:
    return _session_dir(settings, session_id) / "wizard.json"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _persist_session(settings: WebSettings, session: WizardSessionDetail) -> WizardSessionDetail:
    session_dir = _session_dir(settings, session.id)
    session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(_session_json_path(settings, session.id), session.model_dump(mode="json"))
    if session.spec is not None:
        _write_json(session_dir / "spec.json", session.spec.model_dump(mode="json"))
    if session.ir_json is not None:
        _write_json(session_dir / "circuit_ir.json", session.ir_json)
    return session


def _debug_artifact_dir(settings: WebSettings, session_id: str) -> Path:
    return _session_dir(settings, session_id) / "debug_artifacts"


def _make_debug_artifact_writer(
    settings: WebSettings,
    session_id: str,
    *,
    stage: str,
) -> Callable[[dict[str, object]], None] | None:
    if not settings.llm.debug_artifact_capture:
        return None

    artifact_dir = _debug_artifact_dir(settings, session_id)

    def writer(payload: dict[str, object]) -> None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        index = len(list(artifact_dir.glob(f"{stage}_*.json"))) + 1
        _write_json(artifact_dir / f"{stage}_{index:02d}.json", payload)

    return writer


def read_wizard_session(settings: WebSettings, session_id: str) -> WizardSessionDetail:
    path = _session_json_path(settings, session_id)
    if not path.is_file():
        raise FileNotFoundError(path)
    return WizardSessionDetail.model_validate_json(path.read_text(encoding="utf-8"))


def update_wizard_session_metadata(
    *,
    settings: WebSettings,
    session_id: str,
    project_name: str | None,
    symbols_dir: str | None,
) -> WizardSessionDetail:
    session = read_wizard_session(settings, session_id)
    updated = session.model_copy(
        update={
            "project_name": project_name,
            "symbols_dir": symbols_dir,
            "updated_at": _utc_now(),
        }
    )
    return _persist_session(settings, updated)


def _append_message(
    session: WizardSessionDetail,
    *,
    role: WizardMessageRole,
    content: str,
) -> WizardSessionDetail:
    return session.model_copy(
        update={
            "messages": [*session.messages, WizardMessage(role=role, content=content)],
            "updated_at": _utc_now(),
        }
    )


def _set_error(session: WizardSessionDetail, exc: Exception) -> WizardSessionDetail:
    if isinstance(exc, UserError):
        error_payload = kicad_error_to_payload(exc)["error"]
    else:
        error_payload = {
            "type": "internal_error",
            "code": "INTERNAL_SERVER_ERROR",
            "message": str(exc),
            "details": {},
        }
    return session.model_copy(
        update={"status": "failed", "error": error_payload, "updated_at": _utc_now()}
    )


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
    if prior_ir_json is not None and repair_error is not None:
        messages.append(
            LlmMessage(
                role="user",
                content=(
                    "The previous Circuit IR draft failed validation. "
                    f"Repair it using this exact error context:\n{repair_error}\n\n"
                    + _ir_contract_text()
                    + "\n\n"
                    "Previous netlist JSON:\n" + json.dumps(prior_ir_json, indent=2)
                ),
            )
        )
    return messages


def _call_llm_for_json(
    *,
    llm_client: LlmClient,
    messages: list[LlmMessage],
    response_model: type[_MODEL_T],
    max_repairs: int,
    debug_artifact_writer: Callable[[dict[str, object]], None] | None = None,
) -> _MODEL_T:
    schema_json = json.dumps(response_model.model_json_schema(), indent=2)
    current_messages = list(messages)
    for attempt in range(max_repairs + 1):
        request = LlmRequest(messages=current_messages, response_format="json")
        prompt_chars = sum(len(message.content) for message in request.messages)
        started_at = time.perf_counter()
        LOGGER.info(
            "wizard structured json attempt started",
            extra={
                "response_model": response_model.__name__,
                "attempt": attempt + 1,
                "max_attempts": max_repairs + 1,
                "prompt_message_count": len(request.messages),
                "prompt_chars": prompt_chars,
            },
        )
        completion = llm_client.complete(request)
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
        response_chars = len(completion.content)
        serialized_messages = [
            {"role": message.role, "content": message.content} for message in request.messages
        ]
        try:
            parsed = json.loads(completion.content)
            LOGGER.info(
                "wizard structured json attempt completed",
                extra={
                    "response_model": response_model.__name__,
                    "attempt": attempt + 1,
                    "prompt_message_count": len(request.messages),
                    "prompt_chars": prompt_chars,
                    "response_chars": response_chars,
                    "elapsed_ms": elapsed_ms,
                    "provider": completion.provider,
                    "finish_reason": completion.finish_reason,
                    "request_id": completion.request_id,
                },
            )
            if debug_artifact_writer is not None:
                debug_artifact_writer(
                    {
                        "attempt": attempt + 1,
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
            return response_model.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as exc:
            LOGGER.warning(
                "wizard structured json attempt failed",
                extra={
                    "response_model": response_model.__name__,
                    "attempt": attempt + 1,
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
            if debug_artifact_writer is not None:
                debug_artifact_writer(
                    {
                        "attempt": attempt + 1,
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
            if attempt >= max_repairs:
                raise UserError(
                    f"LLM returned invalid structured JSON after {attempt + 1} attempt(s): {exc}"
                ) from exc
            current_messages.extend(
                [
                    LlmMessage(role="assistant", content=completion.content),
                    LlmMessage(
                        role="user",
                        content=(
                            "The previous response did not match the required JSON contract. "
                            f"Return corrected JSON only. Error: {exc}\nJSON schema:\n{schema_json}"
                        ),
                    ),
                ]
            )
    raise UserError("Failed to obtain structured JSON from the configured LLM.")


def _require_llm_client(llm_client: LlmClient | None) -> LlmClient:
    if llm_client is None:
        raise UserError("The LLM wizard is disabled in the current web-app configuration.")
    return llm_client


def create_wizard_session(
    *,
    settings: WebSettings,
    request: CreateWizardSessionRequest,
    llm_client: LlmClient | None,
) -> WizardSessionDetail:
    client = _require_llm_client(llm_client)
    now = _utc_now()
    session = WizardSessionDetail(
        id=_new_session_id(),
        status="drafting_spec",
        created_at=now,
        updated_at=now,
        project_name=request.project_name,
        symbols_dir=request.symbols_dir,
        llm_provider=settings.llm.provider,
        prompt_version=settings.llm.system_prompt_version,
        messages=[WizardMessage(role="user", content=request.message)],
    )
    started_at = time.perf_counter()
    LOGGER.info(
        "wizard spec draft started",
        extra={
            "session_id": session.id,
            "provider": settings.llm.provider,
            "prompt_version": settings.llm.system_prompt_version,
            "message_count": len(session.messages),
        },
    )
    try:
        output = _call_llm_for_json(
            llm_client=client,
            messages=_build_spec_messages(settings, session),
            response_model=SpecConversationOutput,
            max_repairs=settings.llm.spec_max_repair_rounds,
            debug_artifact_writer=_make_debug_artifact_writer(
                settings,
                session.id,
                stage="spec",
            ),
        )
        session = _append_message(session, role="assistant", content=output.assistant_message)
        session = session.model_copy(
            update={
                "status": output.next_state,
                "spec": output.spec,
                "assumptions": output.assumptions,
                "open_questions": output.open_questions,
                "unsupported_reasons": output.unsupported_reasons,
                "error": None,
                "updated_at": _utc_now(),
            }
        )
        LOGGER.info(
            "wizard session created",
            extra={
                "session_id": session.id,
                "status": session.status,
                "provider": session.llm_provider,
                "prompt_version": session.prompt_version,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
            },
        )
        return _persist_session(settings, session)
    except Exception as exc:
        LOGGER.warning(
            "wizard spec draft failed",
            extra={
                "session_id": session.id,
                "provider": settings.llm.provider,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
                "error_type": type(exc).__name__,
            },
        )
        failed = _set_error(session, exc)
        return _persist_session(settings, failed)


def post_wizard_message(
    *,
    settings: WebSettings,
    session_id: str,
    request: WizardMessageRequest,
    llm_client: LlmClient | None,
) -> WizardSessionDetail:
    client = _require_llm_client(llm_client)
    session = read_wizard_session(settings, session_id)
    session = session.model_copy(
        update={
            "status": "drafting_spec",
            "spec_approved": False,
            "spec_approved_at": None,
            "ir_json": None,
            "ir_validation": None,
            "latest_job_id": None,
            "error": None,
            "unsupported_reasons": [],
        }
    )
    session = _append_message(session, role="user", content=request.message)
    started_at = time.perf_counter()
    LOGGER.info(
        "wizard spec revision started",
        extra={
            "session_id": session.id,
            "provider": settings.llm.provider,
            "message_count": len(session.messages),
        },
    )
    try:
        output = _call_llm_for_json(
            llm_client=client,
            messages=_build_spec_messages(settings, session),
            response_model=SpecConversationOutput,
            max_repairs=settings.llm.spec_max_repair_rounds,
            debug_artifact_writer=_make_debug_artifact_writer(
                settings,
                session.id,
                stage="spec",
            ),
        )
        session = _append_message(session, role="assistant", content=output.assistant_message)
        session = session.model_copy(
            update={
                "status": output.next_state,
                "spec": output.spec,
                "assumptions": output.assumptions,
                "open_questions": output.open_questions,
                "unsupported_reasons": output.unsupported_reasons,
                "updated_at": _utc_now(),
            }
        )
        LOGGER.info(
            "wizard session updated",
            extra={
                "session_id": session.id,
                "status": session.status,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
            },
        )
        return _persist_session(settings, session)
    except Exception as exc:
        LOGGER.warning(
            "wizard spec revision failed",
            extra={
                "session_id": session.id,
                "provider": settings.llm.provider,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
                "error_type": type(exc).__name__,
            },
        )
        failed = _set_error(session, exc)
        return _persist_session(settings, failed)


def approve_wizard_spec(*, settings: WebSettings, session_id: str) -> WizardSessionDetail:
    session = read_wizard_session(settings, session_id)
    if session.spec is None:
        raise UserError("Cannot approve a wizard session before a spec draft exists.")
    if session.unsupported_reasons:
        raise UserError("Cannot approve a spec that the wizard marked as unsupported.")
    approved_at = _utc_now()
    session = session.model_copy(
        update={
            "status": "spec_approved",
            "spec_approved": True,
            "spec_approved_at": approved_at,
            "updated_at": approved_at,
            "error": None,
        }
    )
    LOGGER.info("wizard spec approved", extra={"session_id": session.id})
    return _persist_session(settings, session)


def clear_wizard_ir(*, settings: WebSettings, session_id: str) -> WizardSessionDetail:
    session = read_wizard_session(settings, session_id)
    if not session.spec_approved:
        raise UserError("Cannot clear Circuit IR on a session whose spec is not approved.")
    session = session.model_copy(
        update={
            "status": "spec_approved",
            "ir_json": None,
            "ir_validation": None,
            "latest_job_id": None,
            "error": None,
            "updated_at": _utc_now(),
        }
    )
    LOGGER.info("wizard circuit IR cleared", extra={"session_id": session.id})
    return _persist_session(settings, session)


def generate_wizard_ir(
    *,
    settings: WebSettings,
    session_id: str,
    llm_client: LlmClient | None,
) -> WizardSessionDetail:
    client = _require_llm_client(llm_client)
    session = read_wizard_session(settings, session_id)
    if session.spec is None or not session.spec_approved:
        raise UserError("Approve the circuit spec before generating Circuit IR.")

    session = session.model_copy(
        update={
            "status": "drafting_ir",
            "latest_job_id": None,
            "error": None,
            "updated_at": _utc_now(),
        }
    )
    last_error: str | None = None
    prior_ir_json: dict[str, object] | None = None
    try:
        for _ in range(settings.llm.ir_max_repair_rounds + 1):
            output = _call_llm_for_json(
                llm_client=client,
                messages=_build_ir_messages(
                    settings,
                    session,
                    prior_ir_json=prior_ir_json,
                    repair_error=last_error,
                ),
                response_model=IrGenerationOutput,
                max_repairs=0,
                debug_artifact_writer=_make_debug_artifact_writer(
                    settings,
                    session.id,
                    stage="ir",
                ),
            )
            try:
                prepared = prepare_netlist_dict(
                    netlist_json=output.netlist_json,
                    symbols_dir=(
                        Path(session.symbols_dir).expanduser().resolve()
                        if session.symbols_dir
                        else None
                    ),
                    auto_fix=True,
                )
                session = _append_message(
                    session,
                    role="assistant",
                    content=output.assistant_message,
                )
                session = session.model_copy(
                    update={
                        "status": "ir_ready_for_generation",
                        "ir_json": prepared.netlist_json,
                        "ir_validation": WizardIrValidation(
                            valid=True,
                            auto_fixed=prepared.auto_fixed,
                            component_count=prepared.component_count,
                            net_count=prepared.net_count,
                            warnings=prepared.warnings,
                            fixes_applied=prepared.fixes_applied,
                            symbols_dirs_used=prepared.symbols_dirs_used,
                        ),
                        "assumptions": sorted(
                            {*(session.assumptions or []), *(output.assumptions or [])}
                        ),
                        "error": None,
                        "updated_at": _utc_now(),
                    }
                )
                LOGGER.info(
                    "wizard ir ready",
                    extra={
                        "session_id": session.id,
                        "component_count": prepared.component_count,
                        "net_count": prepared.net_count,
                        "auto_fixed": prepared.auto_fixed,
                    },
                )
                return _persist_session(settings, session)
            except UserError as exc:
                last_error = _format_ir_repair_error(exc)
                prior_ir_json = output.netlist_json

        session = session.model_copy(
            update={
                "status": "ir_needs_repair",
                "ir_json": prior_ir_json,
                "ir_validation": WizardIrValidation(valid=False, error_message=last_error),
                "updated_at": _utc_now(),
            }
        )
        return _persist_session(settings, session)
    except Exception as exc:
        failed = _set_error(session, exc)
        return _persist_session(settings, failed)


def generate_wizard_project(
    *,
    settings: WebSettings,
    session_id: str,
) -> WizardGenerateProjectResponse:
    session = read_wizard_session(settings, session_id)
    if session.ir_json is None or session.ir_validation is None or not session.ir_validation.valid:
        raise UserError("Generate valid Circuit IR before starting project generation.")

    session = session.model_copy(
        update={"status": "generation_started", "updated_at": _utc_now(), "error": None}
    )
    session = _persist_session(settings, session)

    project_name = (
        session.project_name
        or (session.spec.project_name if session.spec is not None else None)
        or "WizardProject"
    )
    netlist_json = session.ir_json
    assert netlist_json is not None
    job = generate_project_from_netlist_job(
        settings=settings,
        request=CreateJobFromNetlistRequest(
            project_name=project_name,
            netlist_json=netlist_json,
            symbols_dir=session.symbols_dir,
            validation="internal",
            auto_fix=False,
        ),
    )
    session = session.model_copy(
        update={
            "status": "completed" if job.status == "succeeded" else "failed",
            "latest_job_id": job.id,
            "updated_at": _utc_now(),
        }
    )
    LOGGER.info(
        "wizard project generation finished",
        extra={"session_id": session.id, "job_id": job.id, "job_status": job.status},
    )
    session = _persist_session(settings, session)
    return WizardGenerateProjectResponse(session=session, job=job.model_dump(mode="json"))
