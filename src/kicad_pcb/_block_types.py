"""Block detection types, enums, and public role helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class BlockRole(Enum):
    """Functional block role assignment for components."""

    INPUT = "input"
    """Input connector or input-stage component (jacks, input coupling)."""

    PRECONDITIONING = "preconditioning"
    """Input conditioning / volume control / biasing stage."""

    OPAMP_CORE = "opamp_core"
    """Operational amplifier or core active gain stage."""

    FEEDBACK = "feedback"
    """Feedback network around op-amp (resistors, caps)."""

    INTERSTAGE = "interstage"
    """Coupling and handoff members that bridge one active stage into the next."""

    BUFFER_STAGE = "buffer_stage"
    """A downstream op-amp stage acting primarily as a follower or output driver."""

    OUTPUT = "output"
    """Output coupling or output-stage connector."""

    OUTPUT_CONDITIONING = "output_conditioning"
    """Output-side support chain such as isolation, coupling, bleed, and load parts."""

    POWER_ENTRY = "power_entry"
    """Power supply connector and entry point."""

    DECOUPLING = "decoupling"
    """Power supply filter and decoupling components."""


@dataclass
class BlockAssignment:
    """Assignment of a single component to a block role."""

    ref: str
    """Component reference (e.g., 'J1', 'U1', 'R5')."""

    role: BlockRole
    """Assigned functional block."""

    confidence: float = 1.0
    """Confidence score (0.0–1.0). 1.0 = certain, <1.0 = heuristic."""

    reason: str = ""
    """Human-readable explanation of why this block was chosen."""


@dataclass
class BlockLayout:
    """Layout-engine-ready block assignments with zone definitions."""

    assignments: dict[str, BlockAssignment] = field(default_factory=dict)
    """Mapping from component ref to block assignment."""

    zones: dict[BlockRole, tuple[float, float, float, float]] = field(default_factory=dict)
    """Page zones for each block: (x_min, y_min, x_max, y_max) in mm.
    Used to constrain layout engine placement."""

    def add_assignment(
        self, ref: str, role: BlockRole, confidence: float = 1.0, reason: str = ""
    ) -> None:
        """Register a component-to-block assignment."""
        self.assignments[ref] = BlockAssignment(
            ref=ref, role=role, confidence=confidence, reason=reason
        )

    def get_role(self, ref: str) -> BlockRole | None:
        """Retrieve the block role for a component."""
        assignment = self.assignments.get(ref)
        return assignment.role if assignment else None

    def components_by_role(self, role: BlockRole) -> list[str]:
        """List all components assigned to a given block role."""
        return [ref for ref, assignment in self.assignments.items() if assignment.role == role]


@dataclass(frozen=True)
class _DetectionContext:
    nets_by_ref: dict[str, list[str]]
    refs_by_net: dict[str, set[str]]
    net_pins: dict[str, list[tuple[str, str]]]
    connector_roles: Mapping[str, str]
    path_index: dict[str, int]
    first_opamp_index: int | None
    opamp_dist: dict[str, int]
    input_dist: dict[str, int]
    output_dist: dict[str, int]
    motif_roles: dict[str, tuple[BlockRole, str, float]]


@dataclass(frozen=True)
class _MotifInputs:
    nets_by_ref: Mapping[str, list[str]]
    refs_by_net: Mapping[str, set[str]]
    net_pins: Mapping[str, list[tuple[str, str]]]
    net_pin_units: Mapping[str, list[tuple[str, str, str | None]]]
    active_refs: set[str]
    connector_roles: Mapping[str, str]


def is_input_like_role(role: BlockRole | None) -> bool:
    """Return True when *role* belongs to the input-side group."""
    return role in {BlockRole.INPUT, BlockRole.PRECONDITIONING}


def is_core_like_role(role: BlockRole | None) -> bool:
    """Return True when *role* belongs to the active-stage core group."""
    return role in {
        BlockRole.OPAMP_CORE,
        BlockRole.FEEDBACK,
        BlockRole.INTERSTAGE,
        BlockRole.BUFFER_STAGE,
    }


def is_output_like_role(role: BlockRole | None) -> bool:
    """Return True when *role* belongs to the output-side group."""
    return role in {BlockRole.OUTPUT, BlockRole.OUTPUT_CONDITIONING}


def is_power_like_role(role: BlockRole | None) -> bool:
    """Return True when *role* belongs to the power-support group."""
    return role in {BlockRole.POWER_ENTRY, BlockRole.DECOUPLING}


# Heuristics for block classification

_INPUT_NET_HINTS = ("IN", "INPUT", "AUDIO_IN", "LEFT_IN", "RIGHT_IN", "VOL")
_OUTPUT_NET_HINTS = ("OUT", "OUTPUT", "HP", "HEADPHONE", "BUF")
_FEEDBACK_NET_HINTS = ("FB", "INV", "NFB")
_SUPPLY_NET_HINTS = (
    "SUPPLY",
    "POWER",
)
_DECOUPLING_VALUE_HINTS = ("100N", "10U", "22U", "47U", "100U", "220U")
