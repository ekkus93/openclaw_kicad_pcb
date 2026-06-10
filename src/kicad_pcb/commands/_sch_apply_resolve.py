"""Schematic apply: mode/layout/profile/policy resolution and managed-sheet lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from ..errors import ErrorCode, UserError
from ..fs import _atomic_write
from ..layout_engine import make_layout_engine
from ..models import ProjectRef
from ..pipeline import ValidationMode, mutate_and_validate_sch
from ..router import (
    DEFAULT_LABEL_POLICY,
    LABEL_MODE_POLICIES,
    LabelModeName,
    LabelPolicy,
)
from ..sch_doc import SchematicDoc
from ._project import minimal_schematic_text
from ._sch_apply_types import (
    DEFAULT_SCHEMATIC_HEURISTIC_PROFILE,
    SCHEMATIC_HEURISTIC_PROFILES,
    SchematicHeuristicProfile,
)


def _resolve_mode(mode_name: str | None, *, default: ValidationMode) -> ValidationMode:
    """Map a mode/validate flag string to a :class:`~kicad_pcb.pipeline.ValidationMode`.

    Accepted values
    ---------------
    ``none``                 → :attr:`~ValidationMode.NONE`
    ``syntax``               → :attr:`~ValidationMode.SYNTAX`
    ``lint`` / ``internal``  → :attr:`~ValidationMode.LINT`  (``internal`` is legacy)
    ``kicad``                → :attr:`~ValidationMode.KICAD`
    ``full``                 → :attr:`~ValidationMode.FULL`
    """
    if mode_name is None:
        return default
    raw = mode_name.strip().lower()
    if raw == "none":
        return ValidationMode.NONE
    if raw == "syntax":
        return ValidationMode.SYNTAX
    if raw in {"internal", "syntax_lint", "lint"}:
        return ValidationMode.LINT
    if raw == "kicad":
        return ValidationMode.KICAD
    if raw == "full":
        return ValidationMode.FULL
    raise UserError(
        f"Unknown mode '{mode_name}'",
        code=ErrorCode.USER_ERROR,
        details={"allowed": ["none", "syntax", "lint", "kicad", "full"]},
    )


def _resolve_layout(
    layout_name: str | None,
    *,
    cache_path: Path | None = None,
    debug_dump_path: Path | None = None,
    heuristic_profile: SchematicHeuristicProfile = DEFAULT_SCHEMATIC_HEURISTIC_PROFILE,
    strict: bool = False,
):
    """Return the layout engine requested by *layout_name*.

    Accepted values
    ---------------
    ``None`` / ``graphviz`` — :func:`make_layout_engine`; raises if ``dot`` is absent.
    """

    name = (layout_name or "graphviz").strip().lower()
    if name == "graphviz":
        return make_layout_engine(
            cache_path=cache_path,
            debug_dump_path=debug_dump_path,
            heuristic_profile_name=heuristic_profile.name,
            layout_heuristic_policy=heuristic_profile.layout_policy,
            strict=strict,
        )
    raise UserError(
        f"Unknown layout engine '{layout_name}'",
        code=ErrorCode.USER_ERROR,
        details={"allowed": ["graphviz"]},
    )


def _resolve_heuristic_profile(
    profile_name: str | None,
    *,
    default: SchematicHeuristicProfile = DEFAULT_SCHEMATIC_HEURISTIC_PROFILE,
) -> SchematicHeuristicProfile:
    """Return the bundled heuristic profile requested by *profile_name*."""
    if profile_name is None:
        return default
    name = profile_name.strip().lower()
    try:
        return SCHEMATIC_HEURISTIC_PROFILES[name]
    except KeyError as exc:
        raise UserError(
            f"Unknown heuristic profile '{profile_name}'",
            code=ErrorCode.USER_ERROR,
            details={"allowed": sorted(SCHEMATIC_HEURISTIC_PROFILES)},
        ) from exc


def _resolve_label_policy(
    label_mode_name: str | None,
    *,
    default: LabelPolicy = DEFAULT_LABEL_POLICY,
) -> LabelPolicy:
    """Return the bundled label policy requested by *label_mode_name*."""

    if label_mode_name is None:
        return default
    name = label_mode_name.strip().lower()
    if name not in LABEL_MODE_POLICIES:
        raise UserError(
            f"Unknown label mode '{label_mode_name}'",
            code=ErrorCode.USER_ERROR,
            details={"allowed": sorted(LABEL_MODE_POLICIES)},
        )
    return LABEL_MODE_POLICIES[cast(LabelModeName, name)]


def _resolve_routing(routing_name: str | None) -> bool:
    """Return ``use_bus`` bool from *routing_name*.

    Accepted values
    ---------------
    ``bus`` / ``hub`` / ``None``  — spine/hub routing (``use_bus=True``, default).
    ``labels``                    — label-stub routing (``use_bus=False``).
    """
    name = (routing_name or "bus").strip().lower()
    if name in {"bus", "hub"}:
        return True
    if name == "labels":
        return False
    raise UserError(
        f"Unknown routing style '{routing_name}'",
        code=ErrorCode.USER_ERROR,
        details={"allowed": ["bus", "hub", "labels"]},
    )


def _ensure_managed_file_exists(path: Path, *, dry_run: bool) -> None:
    if path.exists():
        return
    if dry_run:
        raise UserError(
            "Managed schematic file does not exist for dry-run",
            code=ErrorCode.IO_ERROR,
            details={
                "path": str(path),
                "hint": "Run once without --dry-run to initialise OpenClaw managed sheet",
            },
        )
    _atomic_write(
        path,
        minimal_schematic_text(),
        root="kicad_sch",
        operation="create-managed-sheet",
    )


def _ensure_project_root_owned(project: ProjectRef, *, force: bool, dry_run: bool) -> None:
    """Ensure the root schematic carries the OpenClaw ownership marker.

    In flat mode the circuit is written directly into the root schematic, so
    no sub-sheet reference is created.  The ownership marker is still written
    so that ``apply-netlist`` can safely detect user-authored files and refuse
    to overwrite them when ``force=False``.
    """

    def _mutate(doc: SchematicDoc) -> None:
        if not doc.has_openclaw_marker() and not force:
            raise UserError(
                "Schematic is not OpenClaw-managed. Use --force to adopt.",
                code=ErrorCode.NOT_OWNED,
                details={"path": str(project.sch_file)},
            )
        doc.ensure_openclaw_marker()

    mutate_and_validate_sch(
        project.sch_file,
        _mutate,
        mode=ValidationMode.LINT,
        operation="prepare-root-schematic",
        dry_run=dry_run,
    )
