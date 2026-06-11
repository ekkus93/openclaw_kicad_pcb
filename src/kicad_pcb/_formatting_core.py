"""Dispatch registry and JSON output for the CLI presentation layer."""

from __future__ import annotations

import dataclasses
import enum
import json
from pathlib import Path

# ---------------------------------------------------------------------------
# dispatch registry
# ---------------------------------------------------------------------------

_FORMATTERS: dict[type, object] = {}


def _register(cls: type):  # type: ignore[type-arg]
    """Class-keyed decorator that registers a formatter function."""

    def decorator(fn):  # type: ignore[no-untyped-def]
        _FORMATTERS[cls] = fn
        return fn

    return decorator


def format_result(result: object) -> list[str]:
    """Dispatch *result* to the appropriate formatter; return lines to print."""
    fn = _FORMATTERS.get(type(result))
    if fn is None:
        return [repr(result)]
    return fn(result)  # type: ignore[operator]


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


class _ResultEncoder(json.JSONEncoder):
    """Encode types not handled by the default JSON encoder."""

    def default(self, o: object) -> object:  # noqa: ANN001
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, enum.Enum):
            return o.value
        return super().default(o)


def format_result_json(result: object) -> str:
    """Serialise *result* as a JSON string.

    The result must be a dataclass instance.  ``Path`` objects are converted to
    strings; ``Enum`` values are stored as their ``.value``.
    """
    return json.dumps(dataclasses.asdict(result), cls=_ResultEncoder, indent=2)  # type: ignore[call-overload]
