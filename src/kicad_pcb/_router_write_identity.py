"""Routing writer wrapper that emits hidden electrical identity labels."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ._router_identity_labels import write_identity_labels
from ._router_types import NetRouting
from ._router_write import write_routing as _write_routing

if TYPE_CHECKING:
    from .sch_doc import SchematicDoc


def write_routing(  # noqa: PLR0913
    *,
    doc: SchematicDoc,
    routing: NetRouting,
    new_uuid: Callable[[], str],
    stats: dict[str, int],
    symbols_dir: Path | None = None,
    project_name: str = "project",
    strict: bool = False,
) -> None:
    """Emit routing plus hidden labels required only for authored net identity."""
    _write_routing(
        doc=doc,
        routing=routing,
        new_uuid=new_uuid,
        stats=stats,
        symbols_dir=symbols_dir,
        project_name=project_name,
        strict=strict,
    )
    write_identity_labels(doc=doc, routing=routing, new_uuid=new_uuid)
