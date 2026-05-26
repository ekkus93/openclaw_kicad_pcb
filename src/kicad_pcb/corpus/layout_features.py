"""Source schematic layout feature extraction for the model corpus."""

from __future__ import annotations

import dataclasses
import math
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from kicad_pcb.lint.sch import LintIssue
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import (
    average_symbol_spacing,
    count_distinct_x_columns,
    count_power_symbols,
    run_layout_lints,
    wire_stub_ratio,
)
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.utils import walk

from .reports import write_json_report

RelativeRelation = Literal["left_of", "right_of", "above", "below", "near"]


class LayoutFeatureSource(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fixture_id: str
    file: str


class SymbolLayoutFeature(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ref: str
    symbol_id: str
    value: str
    x: float
    y: float
    rotation: float = 0.0
    unit: str = "1"
    role_guess: str
    is_power_symbol: bool
    is_connector: bool
    is_passive: bool
    is_major_ic: bool


class RelativePositionFeature(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    a: str
    b: str
    relation: RelativeRelation


class LayoutFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str = "1.0"
    source: LayoutFeatureSource
    counts: dict[str, int]
    symbols: dict[str, SymbolLayoutFeature]
    role_counts: dict[str, int]
    net_label_strategy: dict[str, int]
    geometry: dict[str, float | int]
    relative_positions: list[RelativePositionFeature] = Field(default_factory=list)
    intrinsic_lints: list[dict[str, object]] = Field(default_factory=list)


def extract_layout_features(
    doc: SchematicDoc,
    *,
    fixture_id: str,
    source_file: str,
) -> LayoutFeatures:
    """Extract normalized layout/style features from *doc*."""

    symbol_entries: dict[str, SymbolLayoutFeature] = {}
    role_counter: Counter[str] = Counter()

    for symbol in doc.list_symbols():
        ref = str(symbol.get("ref", ""))
        symbol_id = str(symbol.get("symbol_id", ""))
        value = str(symbol.get("value", ""))
        x = _as_float(symbol.get("x", 0.0))
        y = _as_float(symbol.get("y", 0.0))
        rotation = _as_float(symbol.get("rotation", 0.0))
        unit = str(symbol.get("unit", "1"))
        is_power_symbol = _is_power_symbol_instance(symbol, doc=doc)
        role_guess = guess_symbol_role(ref=ref, symbol_id=symbol_id, value=value)
        is_connector = role_guess == "connector"
        is_passive = role_guess == "passive"
        is_major_ic = role_guess in {
            "major_ic",
            "interface_ic",
            "display",
            "memory_card",
            "led_chain",
        }
        feature = SymbolLayoutFeature(
            ref=ref,
            symbol_id=symbol_id,
            value=value,
            x=x,
            y=y,
            rotation=rotation,
            unit=unit,
            role_guess=role_guess,
            is_power_symbol=is_power_symbol,
            is_connector=is_connector,
            is_passive=is_passive,
            is_major_ic=is_major_ic,
        )
        symbol_entries[ref] = feature
        role_counter[role_guess] += 1

    features = LayoutFeatures(
        source=LayoutFeatureSource(fixture_id=fixture_id, file=source_file),
        counts=_count_root_nodes(doc=doc, symbols=symbol_entries),
        symbols=dict(sorted(symbol_entries.items())),
        role_counts=dict(sorted(role_counter.items())),
        net_label_strategy={
            "local_label_count": doc.count_nodes("label"),
            "global_label_count": doc.count_nodes("global_label"),
            "power_symbol_count": count_power_symbols(doc),
        },
        geometry=_geometry_summary(doc=doc, symbols=symbol_entries),
        relative_positions=_relative_positions(symbol_entries),
        intrinsic_lints=_serialize_lints(run_layout_lints(doc)),
    )
    return features


def write_layout_features(features: LayoutFeatures, path: Path) -> None:
    """Write *features* as stable JSON."""

    payload = features.model_dump(mode="json")
    payload["symbols"] = {
        ref: payload["symbols"][ref]
        for ref in sorted(payload["symbols"])
    }
    payload["relative_positions"] = sorted(
        payload["relative_positions"],
        key=lambda item: (item["a"], item["b"], item["relation"]),
    )
    write_json_report(path, payload)


def guess_symbol_role(ref: str, symbol_id: str, value: str) -> str:
    """Guess a coarse source-schematic role from symbol metadata."""

    ref_upper = ref.upper()
    haystack = f"{symbol_id} {value} {ref}".lower()
    role = "support"
    if ref_upper.startswith("#PWR") or symbol_id.lower().startswith("power:"):
        role = "power_symbol"
    elif ref_upper.startswith(("J", "P", "CN")) or _contains_any(
        haystack,
        ("connector", "conn_", "terminal", "header", "jack", "usb", "microsd"),
    ):
        role = "connector"
    elif ref_upper.startswith(("R", "C", "L", "FB", "RV", "VR")):
        role = "passive"
    elif _contains_any(haystack, ("st7789", "lcd", "tft", "oled", "display")):
        role = "display"
    elif _contains_any(haystack, ("microsd", "sd_", "sd card", "memory card")):
        role = "memory_card"
    elif _contains_any(haystack, ("ws2812", "neopixel", "addressable led", "led chain")):
        role = "led_chain"
    elif ref_upper.startswith("U") and _contains_any(
        haystack,
        ("can", "lin", "uart", "rs232", "spi", "i2c", "interface", "transceiver"),
    ):
        role = "interface_ic"
    elif ref_upper.startswith("U"):
        role = "major_ic"
    return role


def _count_root_nodes(
    *,
    doc: SchematicDoc,
    symbols: dict[str, SymbolLayoutFeature],
) -> dict[str, int]:
    power_symbols = sum(1 for symbol in symbols.values() if symbol.is_power_symbol)
    return {
        "symbols": len(symbols),
        "non_power_symbols": sum(1 for symbol in symbols.values() if not symbol.is_power_symbol),
        "power_symbols": power_symbols,
        "wires": doc.count_nodes("wire"),
        "labels": doc.count_nodes("label"),
        "global_labels": doc.count_nodes("global_label"),
        "junctions": doc.count_nodes("junction"),
        "no_connects": doc.count_nodes("no_connect"),
    }


def _geometry_summary(
    *,
    doc: SchematicDoc,
    symbols: dict[str, SymbolLayoutFeature],
) -> dict[str, float | int]:
    if symbols:
        xs = [symbol.x for symbol in symbols.values()]
        ys = [symbol.y for symbol in symbols.values()]
        min_x = min(xs)
        max_x = max(xs)
        min_y = min(ys)
        max_y = max(ys)
    else:
        min_x = max_x = min_y = max_y = 0.0
    return {
        "min_x": min_x,
        "max_x": max_x,
        "min_y": min_y,
        "max_y": max_y,
        "distinct_x_columns": count_distinct_x_columns(doc),
        "average_symbol_spacing_mm": round(average_symbol_spacing(doc), 3),
        "wire_stub_ratio": round(wire_stub_ratio(doc), 3),
    }


def _relative_positions(symbols: dict[str, SymbolLayoutFeature]) -> list[RelativePositionFeature]:
    important = [
        symbol
        for symbol in symbols.values()
        if not symbol.is_power_symbol and (symbol.is_connector or symbol.is_major_ic)
    ]
    passives = [
        symbol
        for symbol in symbols.values()
        if not symbol.is_power_symbol and symbol.is_passive
    ]
    relations: set[tuple[str, str, RelativeRelation]] = set()

    for index, symbol in enumerate(important):
        for other in important[index + 1 :]:
            relation = _infer_relation(symbol, other)
            if relation is not None:
                relations.add((symbol.ref, other.ref, relation))
                relations.add((other.ref, symbol.ref, _inverse_relation(relation)))
        nearest = sorted(passives, key=lambda candidate: _distance(symbol, candidate))[:2]
        for passive in nearest:
            relation = _infer_relation(symbol, passive)
            if relation is not None:
                relations.add((symbol.ref, passive.ref, relation))
                relations.add((passive.ref, symbol.ref, _inverse_relation(relation)))

    return [
        RelativePositionFeature(a=a, b=b, relation=relation)
        for a, b, relation in sorted(relations)
    ]


def _infer_relation(
    a: SymbolLayoutFeature,
    b: SymbolLayoutFeature,
) -> RelativeRelation | None:
    dx = b.x - a.x
    dy = b.y - a.y
    if abs(dx) >= 10.0 and abs(dx) >= abs(dy) * 1.5:
        return "left_of" if dx > 0 else "right_of"
    if abs(dy) >= 10.0 and abs(dy) >= abs(dx) * 1.5:
        return "below" if dy > 0 else "above"
    if math.hypot(dx, dy) <= 30.0:
        return "near"
    return None


def _inverse_relation(relation: RelativeRelation) -> RelativeRelation:
    inverses: dict[RelativeRelation, RelativeRelation] = {
        "left_of": "right_of",
        "right_of": "left_of",
        "above": "below",
        "below": "above",
        "near": "near",
    }
    return inverses[relation]


def _distance(a: SymbolLayoutFeature, b: SymbolLayoutFeature) -> float:
    return math.hypot(b.x - a.x, b.y - a.y)


def _serialize_lints(lints: list[LintIssue]) -> list[dict[str, object]]:
    serialized: list[dict[str, object]] = []
    for issue in lints:
        if dataclasses.is_dataclass(issue):
            payload = dataclasses.asdict(issue)
        else:
            payload = {"message": str(issue)}
        serialized.append(payload)
    return serialized


def _is_power_symbol_instance(symbol: dict[str, object], *, doc: SchematicDoc) -> bool:
    ref = str(symbol.get("ref", ""))
    symbol_id = str(symbol.get("symbol_id", ""))
    if ref.upper().startswith("#PWR") or symbol_id.lower().startswith("power:"):
        return True

    for node in walk(doc.root):
        if not isinstance(node, ListNode) or node.key != "symbol":
            continue
        if _symbol_reference(node) != ref:
            continue
        if _has_yes_marker(node, "power"):
            return True
        return _has_no_marker(node, "in_bom") and _has_no_marker(node, "on_board")
    return False


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(token in haystack for token in needles)


def _as_float(value: object) -> float:
    if isinstance(value, (float, int)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    return 0.0


def _symbol_reference(node: ListNode) -> str:
    for child in node.items:
        if not isinstance(child, ListNode) or child.key != "property" or len(child.items) < 3:
            continue
        if (
            isinstance(child.items[1], StringNode)
            and isinstance(child.items[2], StringNode)
            and child.items[1].value == "Reference"
        ):
            return child.items[2].value
    return ""


def _has_yes_marker(node: ListNode, key: str) -> bool:
    return any(
        isinstance(child, ListNode)
        and child.key == key
        and len(child.items) >= 2
        and isinstance(child.items[1], StringNode)
        and child.items[1].value == "yes"
        for child in node.items
    )


def _has_no_marker(node: ListNode, key: str) -> bool:
    return any(
        isinstance(child, ListNode)
        and child.key == key
        and len(child.items) >= 2
        and child.items[1].__class__.__name__ in {"AtomNode", "StringNode"}
        and getattr(child.items[1], "value", None) == "no"
        for child in node.items
    )
