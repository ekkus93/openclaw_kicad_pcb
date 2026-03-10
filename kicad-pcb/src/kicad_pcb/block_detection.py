"""Functional block detection for schematic readability improvements.

This module classifies circuit components into distinct functional blocks
(input, op-amp stage, output, power, etc.) using heuristics based on:
- Component reference prefixes (J, U, R, C, etc.)
- Component values and roles
- Net name patterns (IN, OUT, GND, VCC, etc.)
- Graph proximity to active devices and I/O connectors

Block classification enables layout constraints that enforce visual
separation of functional blocks, improving schematic readability.

Phase 1.1 of CODE_REVIEW6 readability improvements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR


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

    OUTPUT = "output"
    """Output coupling or output-stage connector."""

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


# Heuristics for block classification


def _classify_by_reference(ref: str) -> BlockRole | None:
    """Classify based on component reference prefix.

    Heuristic: Power connectors (J+P/JP) and supply jacks (often J3)
    are likely power entry. Jacks J1, J2 are likely audio input.
    Op-amps are typically U*.
    """
    prefix = ref.rstrip("0123456789")
    suffix = ref[len(prefix) :]

    if not suffix:
        return None

    # Power entry: power supply jack (often third jack in audio circuits)
    if prefix in ("J", "P", "JP"):
        num = int(suffix) if suffix.isdigit() else 0
        if num == 3:
            return BlockRole.POWER_ENTRY
        # Jacks 1-2 are typically audio in
        if num in (1, 2):
            return BlockRole.INPUT
        # Jacks 4+ are typically audio out
        if num >= 4:
            return BlockRole.OUTPUT

    # Op-amps and active devices
    if prefix in ("U", "IC"):
        return BlockRole.OPAMP_CORE

    return None


def _classify_by_value(ref: str, value: str) -> BlockRole | None:
    """Classify based on component value and reference type.

    Heuristic: Common feedback resistor values (47k, 100k, 220k) combined
    with reference near op-amp or feedback nets suggest feedback role.
    Small resistors on op-amp outputs suggest output protection.
    """
    prefix = ref.rstrip("0123456789")

    # Resistor classifications
    if prefix == "R" and value in ("47k", "47K", "100k", "100K", "220k", "220K", "10k", "10K"):
        # Common op-amp feedback resistors (Rf typically 47k–220k)
        # Will be refined by net proximity later
        return BlockRole.FEEDBACK  # tentative

    # Capacitor classifications
    if prefix == "C":
        # Large value power supply caps (100µ, 47µ, 10µ) → decoupling
        if any(val in value.lower() for val in ("100u", "100µ", "47u", "47µ", "10u", "10µ")):
            return BlockRole.DECOUPLING
        # Smaller coupling caps → might be output coupling
        if any(val in value.lower() for val in ("10n", "100n", "1u", "2.2u")):
            return BlockRole.OUTPUT  # tentative

    return None


def _classify_by_net_names(ref: str, connected_nets: list[str]) -> BlockRole | None:
    """Classify based on connected net name patterns.

    Heuristic: Components connected to IN_*, INPUT nets → input block.
    Components on OUT_*, OUTPUT nets → output block.
    Parts on VCC, V+, V- power rails → power/decoupling (if capacitors).
    Parts connected only to power/GND → PRECONDITIONING (resistors) or DECOUPLING (caps).
    """
    net_str = " ".join(connected_nets).upper()

    if any(keyword in net_str for keyword in ("IN_", "INPUT", "AUDIO_IN", "AUDIO IN")):
        return BlockRole.INPUT

    if any(keyword in net_str for keyword in ("OUT_", "OUTPUT", "AUDIO_OUT", "AUDIO OUT")):
        return BlockRole.OUTPUT

    # Power entry: explicitly on power supply rail
    if any(keyword in net_str for keyword in ("VCC", "V+", "POWER", "SUPPLY")):
        return BlockRole.POWER_ENTRY

    return None


def _get_component_nets(ir: CircuitIR, ref: str) -> list[str]:
    """Retrieve all net names connected to a component."""
    nets = []
    for net in ir.nets:
        if any(pin.ref == ref for pin in net.pins):
            nets.append(net.name)
    return nets


def classify_circuit(ir: CircuitIR) -> BlockLayout:
    """Classify all components in a circuit into functional blocks.

    Uses a cascade of heuristics:
    1. Reference-based classification (J/U/R/C prefixes)
    2. Value-based classification (common values for feedback, decoupling)
    3. Net name analysis (IN/OUT/VCC patterns)
    4. Graph proximity refinement (future: distance-based classification)

    Args:
        ir: Circuit IR to classify

    Returns:
        BlockLayout with all assignments and zone definitions
    """
    layout = BlockLayout()

    for component in ir.components:
        ref = component.ref
        value = component.value or ""

        # Try classification in order of specificity
        role = None
        reason = ""
        confidence = 1.0

        # First: net name patterns (most specific)
        connected_nets = _get_component_nets(ir, ref)
        role_from_nets = _classify_by_net_names(ref, connected_nets)
        if role_from_nets:
            role = role_from_nets
            reason = f"Net names: {', '.join(connected_nets)}"
            confidence = 0.95

        # Second: reference-based (common and reliable)
        if not role:
            role_from_ref = _classify_by_reference(ref)
            if role_from_ref:
                role = role_from_ref
                reason = f"Reference pattern: {ref}"
                confidence = 0.9

        # Third: value-based (specific but lower confidence)
        if not role:
            role_from_val = _classify_by_value(ref, value)
            if role_from_val:
                role = role_from_val
                reason = f"Value heuristic: {value}"
                confidence = 0.7

        # Default: treat unknown components as preconditioning/support
        if not role:
            role = BlockRole.PRECONDITIONING
            reason = "Default: support/passive component"
            confidence = 0.5

        layout.add_assignment(ref, role, confidence=confidence, reason=reason)

    # Define default page zones (can be overridden by layout engine)
    _set_default_zones(layout)

    return layout


def _set_default_zones(layout: BlockLayout) -> None:
    """Set default page zones for block placement.

    A standard schematic page is ~254mm wide × 203mm tall (letter landscape).
    Divide into regions: left (input), center (op-amp), right (output),
    top (power/supply).
    """
    # Page dimensions (letter landscape in mm)
    page_width = 254.0
    page_height = 203.0
    margin = 10.0

    # Regions
    left_x_max = page_width * 0.35
    center_x_min = page_width * 0.25
    center_x_max = page_width * 0.75
    right_x_min = page_width * 0.65

    power_y_min = page_height * 0.7

    layout.zones = {
        BlockRole.INPUT: (margin, margin, left_x_max, page_height - margin),
        BlockRole.PRECONDITIONING: (margin, margin, left_x_max, page_height - margin),
        BlockRole.OPAMP_CORE: (center_x_min, margin, center_x_max, page_height * 0.7),
        BlockRole.FEEDBACK: (center_x_min, margin, center_x_max, page_height * 0.7),
        BlockRole.OUTPUT: (right_x_min, margin, page_width - margin, page_height - margin),
        BlockRole.POWER_ENTRY: (margin, power_y_min, page_width - margin, page_height - margin),
        BlockRole.DECOUPLING: (margin, power_y_min, page_width - margin, page_height - margin),
    }


def debug_dump(layout: BlockLayout) -> str:
    """Generate human-readable debug output of block assignments.

    Returns:
        Multi-line string with component-to-block mappings and confidence scores
    """
    lines = ["Block Assignments Debug Dump", "=" * 50]

    # Group by role
    by_role: dict[BlockRole, list[BlockAssignment]] = {}
    for assignment in layout.assignments.values():
        by_role.setdefault(assignment.role, []).append(assignment)

    # Sort by role name
    for role in sorted(by_role.keys(), key=lambda r: r.value):
        lines.append(f"\n{role.value.upper()}")
        lines.append("-" * len(role.value))

        for assignment in sorted(by_role[role], key=lambda a: a.ref):
            filled = int(assignment.confidence * 10)
            empty = 10 - filled
            conf_bar = "█" * filled + "░" * empty
            lines.append(f"  {assignment.ref:8} [{conf_bar}] {assignment.reason}")

    return "\n".join(lines)
