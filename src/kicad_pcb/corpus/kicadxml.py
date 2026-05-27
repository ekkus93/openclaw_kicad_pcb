"""KiCad XML netlist parsing and CircuitIR conversion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.errors import ErrorCode, ParseError, UserError
from kicad_pcb.sch_doc import SchematicDoc


@dataclass(frozen=True)
class KicadXmlComponent:
    ref: str
    value: str | None = None
    footprint: str | None = None
    symbol: str | None = None
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class KicadXmlNetPin:
    ref: str
    pin: str
    unit: str | None = None


@dataclass(frozen=True)
class KicadXmlNet:
    code: str
    name: str
    pins: tuple[KicadXmlNetPin, ...]


@dataclass(frozen=True)
class KicadXmlNetlist:
    design_source: str | None
    components: tuple[KicadXmlComponent, ...]
    nets: tuple[KicadXmlNet, ...]
    warnings: tuple[str, ...] = ()


def parse_kicadxml_netlist(path: Path) -> KicadXmlNetlist:
    """Parse KiCad XML netlist file at *path*."""

    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except ET.ParseError as exc:
        raise ParseError(
            f"Malformed KiCad XML netlist: {path}",
            code=ErrorCode.PARSE_ERROR,
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    except OSError as exc:
        raise UserError(
            f"Failed to read KiCad XML netlist: {path}",
            code=ErrorCode.IO_ERROR,
            details={"path": str(path), "reason": str(exc)},
        ) from exc

    if root.tag != "export":
        raise ParseError(
            f"Expected KiCad XML root <export>, got <{root.tag}>",
            code=ErrorCode.PARSE_ERROR,
            details={"path": str(path), "root_tag": root.tag},
        )

    design_source = _child_text(root.find("design/source"))
    components = tuple(_parse_component(node) for node in root.findall("components/comp"))
    nets = tuple(_parse_net(node) for node in root.findall("nets/net"))
    return KicadXmlNetlist(design_source=design_source, components=components, nets=nets)


def kicadxml_to_circuit_ir(
    netlist: KicadXmlNetlist,
    *,
    fallback_symbols_by_ref: Mapping[str, str] | None = None,
) -> CircuitIR:
    """Convert parsed KiCad XML netlist into canonical CircuitIR."""

    components = [
        ComponentIR(
            ref=component.ref,
            symbol=(
                component.symbol
                or (fallback_symbols_by_ref or {}).get(component.ref)
                or "Unknown:Unknown"
            ),
            value=component.value,
            footprint=component.footprint,
            fields=component.fields or None,
        )
        for component in netlist.components
    ]
    nets: list[NetIR] = []
    for net in netlist.nets:
        if not net.pins:
            continue
        nets.append(
            NetIR(
                name=net.name,
                pins=[
                    PinRefIR(ref=pin.ref, pin=pin.pin, unit=pin.unit)
                    for pin in net.pins
                ],
            )
        )
    return canonicalize_circuit_ir(
        CircuitIR(
            version="1.0",
            components=components,
            nets=nets,
        )
    )


def schematic_symbols_by_ref(doc: SchematicDoc) -> dict[str, str]:
    """Return a deterministic ``{ref: symbol_id}`` map from a schematic doc."""

    return {
        str(symbol["ref"]): str(symbol["symbol_id"])
        for symbol in doc.list_symbols()
        if symbol.get("ref") and symbol.get("symbol_id")
    }


def canonicalize_circuit_ir(ir: CircuitIR) -> CircuitIR:
    """Return deterministically sorted CircuitIR."""

    components = sorted(ir.components, key=lambda component: component.ref)
    nets = [
        NetIR(
            name=net.name,
            pins=sorted(
                net.pins,
                key=lambda pin: (pin.ref, pin.pin, pin.unit or ""),
            ),
        )
        for net in sorted(ir.nets, key=lambda net: net.name)
    ]
    return CircuitIR(version=ir.version, components=components, nets=nets, options=ir.options)


def _parse_component(node: ET.Element) -> KicadXmlComponent:
    ref = node.attrib.get("ref", "").strip()
    if not ref:
        raise ParseError("KiCad XML component is missing required ref attribute")
    libsource = node.find("libsource")
    symbol = None
    if libsource is not None:
        lib_name = libsource.attrib.get("lib", "").strip()
        part_name = libsource.attrib.get("part", "").strip()
        if lib_name and part_name:
            symbol = f"{lib_name}:{part_name}"
    fields = {
        field.attrib["name"]: (field.text or "").strip()
        for field in node.findall("fields/field")
        if field.attrib.get("name")
    }
    return KicadXmlComponent(
        ref=ref,
        value=_child_text(node.find("value")),
        footprint=_child_text(node.find("footprint")),
        symbol=symbol,
        fields=fields,
    )


def _parse_net(node: ET.Element) -> KicadXmlNet:
    return KicadXmlNet(
        code=node.attrib.get("code", "").strip(),
        name=node.attrib.get("name", "").strip(),
        pins=tuple(_parse_net_pin(child) for child in node.findall("node")),
    )


def _parse_net_pin(node: ET.Element) -> KicadXmlNetPin:
    ref = node.attrib.get("ref", "").strip()
    pin = node.attrib.get("pin", "").strip()
    if not ref or not pin:
        raise ParseError("KiCad XML net node is missing required ref/pin attributes")
    return KicadXmlNetPin(ref=ref, pin=pin, unit=node.attrib.get("unit"))


def _child_text(node: ET.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    text = node.text.strip()
    return text or None
