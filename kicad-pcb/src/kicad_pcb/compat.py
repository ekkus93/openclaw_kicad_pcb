"""KiCad CLI version detection and capability compatibility layer.

This module answers two questions at runtime:

1. **What version of kicad-cli is installed?**
   :func:`parse_version` converts the ``kicad-cli --version`` output into a
   comparable :class:`KiCadVersion` triple.

2. **Does that version support the feature we need?**
   :data:`CAPABILITY_MAP` maps each :class:`CliCapability` flag to the minimum
   :class:`KiCadVersion` that introduced it.  :func:`require_capability` raises
   an informative :class:`~kicad_pcb.errors.ToolError` when the installed
   version is too old, rather than letting the CLI fail with a cryptic error.

Usage::

    from kicad_pcb.compat import (
        CliCapability,
        KiCadVersion,
        MINIMUM_VERSION,
        parse_version,
        require_capability,
    )

    version = parse_version("9.0.7")
    require_capability(version, CliCapability.PCB_EXPORT_STEP_NO_UNSPECIFIED)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .errors import ToolError

# ---------------------------------------------------------------------------
# KiCadVersion — comparable version triple
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class KiCadVersion:
    """Immutable, comparable KiCad version triple (major, minor, patch).

    Supports all standard comparison operators via ``order=True``, so::

        KiCadVersion(9, 0, 7) > KiCadVersion(8, 0, 0)  # True
        KiCadVersion(7, 0, 0) >= KiCadVersion(7, 0, 0)  # True
    """

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


#: Oldest kicad-cli version the skill officially supports.
#: KiCad 7.0 introduced the fully reworked ``kicad-cli`` tool with support for
#: structured JSON output from DRC/ERC, ``sch export bom``, and the other
#: sub-commands the skill depends on.
MINIMUM_VERSION: KiCadVersion = KiCadVersion(7, 0, 0)


# ---------------------------------------------------------------------------
# Version string parsing
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(version_string: str) -> KiCadVersion:
    """Parse a kicad-cli version string into a :class:`KiCadVersion`.

    Accepts bare ``"9.0.7"``, multi-line banner text (extracts the first
    ``X.Y.Z`` triple found), and any string that contains a digit-dot-digit
    sequence.

    Raises :class:`ValueError` if no version triple can be extracted.

    Examples::

        parse_version("9.0.7")                   # → KiCadVersion(9, 0, 7)
        parse_version("Application: kicad-cli\\n9.0.7 release")  # → same
    """
    m = _VERSION_RE.search(version_string)
    if not m:
        raise ValueError(f"Cannot extract version from {version_string!r}; expected X.Y.Z format")
    return KiCadVersion(int(m.group(1)), int(m.group(2)), int(m.group(3)))


# ---------------------------------------------------------------------------
# CliCapability — named feature flags
# ---------------------------------------------------------------------------


class CliCapability(StrEnum):
    """Named kicad-cli features keyed to the minimum supported version.

    The string value of each member is a human-readable description of the
    feature / option used in error messages.  Look up the corresponding
    minimum version in :data:`CAPABILITY_MAP`.
    """

    # Validation sub-commands
    DRC_JSON_REPORT = "pcb drc --format json"
    ERC_JSON_REPORT = "sch erc --format json"

    # Schematic export sub-commands
    SCH_EXPORT_BOM = "sch export bom"
    SCH_EXPORT_NETLIST_KICADXML = "sch export netlist --format kicadxml"
    SCH_EXPORT_SVG = "sch export svg"

    # PCB export sub-commands
    PCB_EXPORT_GERBERS = "pcb export gerbers"
    PCB_EXPORT_DRILL = "pcb export drill"
    PCB_EXPORT_POS = "pcb export pos"
    PCB_EXPORT_SVG = "pcb export svg"
    PCB_EXPORT_STEP_NO_UNSPECIFIED = "pcb export step --no-unspecified"
    PCB_EXPORT_GLB = "pcb export glb"
    PCB_EXPORT_SPECCTRA_DSN = "pcb export specctra"
    PCB_IMPORT_SPECCTRA_SES = "pcb import specctra"


#: Minimum :class:`KiCadVersion` required for each :class:`CliCapability`.
#:
#: Any capability absent from this map is assumed to be available in all
#: supported KiCad versions (>= :data:`MINIMUM_VERSION`).
CAPABILITY_MAP: dict[CliCapability, KiCadVersion] = {
    # KiCad 7.0 — initial reworked kicad-cli release with JSON output support
    CliCapability.DRC_JSON_REPORT: KiCadVersion(7, 0, 0),
    CliCapability.ERC_JSON_REPORT: KiCadVersion(7, 0, 0),
    CliCapability.SCH_EXPORT_BOM: KiCadVersion(7, 0, 0),
    CliCapability.SCH_EXPORT_NETLIST_KICADXML: KiCadVersion(7, 0, 0),
    CliCapability.SCH_EXPORT_SVG: KiCadVersion(7, 0, 0),
    CliCapability.PCB_EXPORT_GERBERS: KiCadVersion(7, 0, 0),
    CliCapability.PCB_EXPORT_DRILL: KiCadVersion(7, 0, 0),
    CliCapability.PCB_EXPORT_POS: KiCadVersion(7, 0, 0),
    CliCapability.PCB_EXPORT_SVG: KiCadVersion(7, 0, 0),
    CliCapability.PCB_EXPORT_SPECCTRA_DSN: KiCadVersion(7, 0, 0),
    CliCapability.PCB_IMPORT_SPECCTRA_SES: KiCadVersion(7, 0, 0),
    # KiCad 8.0 — added --no-unspecified flag to pcb export step
    CliCapability.PCB_EXPORT_STEP_NO_UNSPECIFIED: KiCadVersion(8, 0, 0),
    # KiCad 8.0 — added pcb export glb
    CliCapability.PCB_EXPORT_GLB: KiCadVersion(8, 0, 0),
}


# ---------------------------------------------------------------------------
# Capability gating helper
# ---------------------------------------------------------------------------


def require_capability(version: KiCadVersion | None, cap: CliCapability) -> None:
    """Raise :class:`~kicad_pcb.errors.ToolError` when *version* is below the
    minimum required for *cap*.

    Passes when:

    * *cap* has no entry in :data:`CAPABILITY_MAP` (assumed always available).
    * *version* meets or exceeds the minimum.

    Fails fast when *version* is ``None`` so capability checks are never
    silently skipped due to undetected tool versions.

    Args:
        version: The detected :class:`KiCadVersion`, or ``None`` if unknown.
        cap: The :class:`CliCapability` being requested.

    Raises:
        ToolError: With a human-readable message including the minimum version
            and a download link, if *version* is known to be too old.
    """
    if version is None:
        raise ToolError(
            f"Cannot verify kicad-cli capability '{cap.value}' because the installed version "
            "could not be detected.\n"
            "Run `kicad-cli --version` and ensure it returns a valid X.Y.Z version."
        )
    min_ver = CAPABILITY_MAP.get(cap)
    if min_ver is None:
        return  # not in map → assumed available in all supported versions
    if version < min_ver:
        raise ToolError(
            f"kicad-cli {version} does not support '{cap.value}'.\n"
            f"Minimum required version: {min_ver}.\n"
            "Please update KiCad: https://www.kicad.org/download/"
        )
