"""Typed exception hierarchy for the kicad-pcb skill."""
from __future__ import annotations


class KiCadError(RuntimeError):
    """Base class for all kicad-pcb errors."""


class UserError(KiCadError):
    """Invalid user input or missing project."""


class ToolError(KiCadError):
    """External tool (kicad-cli, Java, …) failed or is unavailable."""


class ParseError(KiCadError):
    """KiCad S-expression file is malformed."""
