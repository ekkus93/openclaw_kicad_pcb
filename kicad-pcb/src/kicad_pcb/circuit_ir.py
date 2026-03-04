"""Circuit IR schema (Pydantic v2) for deterministic netlist compilation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .component_types import normalize_gnd_net_name as _normalize_gnd_net_name
from .errors import ErrorCode, UserError


class PinRefIR(BaseModel):
    """Structured pin reference in a net membership list."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ref: str = Field(min_length=1)
    pin: str = Field(min_length=1)
    unit: str | None = None


class NetIR(BaseModel):
    """Single named net with pin members."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    pins: list[PinRefIR] = Field(min_length=1)

    @field_validator("name", mode="after")
    @classmethod
    def _normalize_gnd_alias(cls, v: str) -> str:
        """Collapse GND / 0V / GROUND / … aliases to the canonical ``"GND"``.

        This runs after Pydantic has stripped whitespace (``str_strip_whitespace=True``)
        and validated that the string is non-empty.  The normalisation happens
        at the earliest possible IR-construction boundary so every consumer
        (tier assignment, layout engine, router, schematic writer) always
        receives ``"GND"`` instead of ``"0V"`` or other aliases.
        """
        return _normalize_gnd_net_name(v)


class ComponentIR(BaseModel):
    """Single component instance in the circuit netlist IR."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ref: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    value: str | None = None
    footprint: str | None = None
    fields: dict[str, str] | None = None


class OptionsIR(BaseModel):
    """Optional, low-level compilation hints for deterministic generation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tech: Literal["THT", "SMD"] | None = None
    default_res_package: str | None = None
    default_cap_package: str | None = None
    power_net_names: list[str] = Field(default_factory=lambda: ["0V", "GND"])


class CircuitIR(BaseModel):
    """Top-level machine-checkable circuit representation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: str = Field(min_length=1)
    components: list[ComponentIR] = Field(min_length=1)
    nets: list[NetIR] = Field(min_length=1)
    options: OptionsIR | None = None

    @classmethod
    def load(cls, path: Path) -> CircuitIR:
        """Load Circuit IR JSON from *path* with actionable errors."""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise UserError(
                f"Invalid JSON in {path}: {exc.msg}",
                code=ErrorCode.IR_SCHEMA_INVALID,
                details={
                    "path": str(path),
                    "line": exc.lineno,
                    "column": exc.colno,
                },
            ) from exc
        except OSError as exc:
            raise UserError(
                f"Failed to read IR file: {path}",
                code=ErrorCode.IO_ERROR,
                details={"path": str(path), "reason": str(exc)},
            ) from exc

        try:
            return cls.model_validate(payload)
        except ValidationError as exc:
            raise UserError(
                "Circuit IR schema validation failed",
                code=ErrorCode.IR_SCHEMA_INVALID,
                details={"path": str(path), "errors": exc.errors()},
            ) from exc

    def dumps(self) -> str:
        """Dump canonical JSON for this Circuit IR instance."""
        return self.model_dump_json(indent=2)
