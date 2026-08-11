from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.critic import CriticResponse, validate_critic_response
from kicad_pcb.refinement.vision_context import (
    VisionComponentObject,
    VisionObjectMap,
)


def _context() -> VisionObjectMap:
    return VisionObjectMap(
        schema_version="1.0",
        source_schematic_hash="a" * 64,
        render_png_hash="b" * 64,
        sheet_id="1",
        page_mm=(297.0, 210.0),
        image_px=(2970, 2100),
        components=(
            VisionComponentObject(
                "component:uuid-r1",
                "uuid-r1",
                "R1",
                "1",
                "Device:R",
                "10k",
                25.4,
                25.4,
                0,
                254.0,
                254.0,
            ),
        ),
        pins=(),
        wires=(),
        labels=(),
        junctions=(),
        nets=(),
        deterministic_metrics={},
    )


def _payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "source_schematic_hash": "a" * 64,
        "render_png_hash": "b" * 64,
        "issues": [
            {
                "issue_id": "align-r1",
                "category": "component_alignment",
                "severity": "warning",
                "confidence": 0.9,
                "affected_object_ids": ["component:uuid-r1"],
                "observation": "R1 is visibly out of column.",
                "desired_outcome": "Align R1 to the nearby resistor column.",
                "evidence": "R1 center is offset relative to adjacent components.",
                "constraints": ["preserve electrical connectivity"],
            }
        ],
        "rubric": {"overall_readability": 6.0},
    }


def test_valid_critic_response_binds_to_exact_context() -> None:
    response = CriticResponse.model_validate(_payload())
    assert validate_critic_response(response, _context()) is response


def test_critic_rejects_stale_hashes() -> None:
    response = CriticResponse.model_validate(_payload())
    with pytest.raises(UserError, match="stale"):
        validate_critic_response(response, replace(_context(), render_png_hash="c" * 64))


def test_critic_rejects_unknown_object_reference() -> None:
    payload = _payload()
    payload["issues"][0]["affected_object_ids"] = ["component:invented"]  # type: ignore[index]
    response = CriticResponse.model_validate(payload)
    with pytest.raises(UserError, match="unknown schematic objects"):
        validate_critic_response(response, _context())


def test_critic_schema_rejects_duplicate_issue_ids() -> None:
    payload = _payload()
    payload["issues"] = [payload["issues"][0], payload["issues"][0]]  # type: ignore[index]
    with pytest.raises(ValidationError, match="issue_id values must be unique"):
        CriticResponse.model_validate(payload)


def test_critic_schema_rejects_duplicate_affected_objects() -> None:
    payload = _payload()
    payload["issues"][0]["affected_object_ids"] = [  # type: ignore[index]
        "component:uuid-r1",
        "component:uuid-r1",
    ]
    with pytest.raises(ValidationError, match="affected_object_ids must be unique"):
        CriticResponse.model_validate(payload)


def test_critic_schema_rejects_extra_fields_and_non_finite_scores() -> None:
    extra = _payload()
    extra["silent_fallback"] = True
    with pytest.raises(ValidationError):
        CriticResponse.model_validate(extra)

    nan_payload = _payload()
    nan_payload["issues"][0]["confidence"] = float("nan")  # type: ignore[index]
    with pytest.raises(ValidationError):
        CriticResponse.model_validate(nan_payload)
