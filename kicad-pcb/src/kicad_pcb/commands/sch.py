"""Schematic editing helpers and commands: add-component, add-net, connect."""
from __future__ import annotations

import re
from pathlib import Path

from ..config import get_current_project
from ..errors import UserError
from ..fs import _atomic_write, _new_uuid
from ..models import ComponentSpec, NetLabelSpec, WireSegment
from ..results import AddComponentResult, AddNetResult, ConnectResult

# KiCad symbol library path (system default; override via KICAD_SYMBOLS_DIR env if needed).
KICAD_SYMBOLS_DIR = Path("/usr/share/kicad/symbols")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_balanced(text: str, start: int) -> str:
    """Extract the balanced S-expression block beginning at index *start*."""
    depth = 0
    i = start
    in_string = False
    while i < len(text):
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_string = not in_string
        elif not in_string:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        i += 1
    return text[start:]


def _find_symbol_def(lib_name: str, sym_name: str) -> str | None:
    """Extract the full symbol block from library and rename it to lib:sym."""
    lib_file = KICAD_SYMBOLS_DIR / f"{lib_name}.kicad_sym"
    if not lib_file.exists():
        return None
    text = lib_file.read_text(errors="replace")
    m = re.search(r'\(symbol "' + re.escape(sym_name) + r'"', text)
    if not m:
        return None
    block = _extract_balanced(text, m.start())
    # Rename only the ROOT symbol: "R" → "Device:R".
    # Sub-symbols ("R_0_1", "R_1_1") inside lib_symbols must keep their SHORT
    # names — kicad-cli rejects the schematic if they are prefixed with "Lib:".
    block = re.sub(
        r'\(symbol "' + re.escape(sym_name) + r'"',
        f'(symbol "{lib_name}:{sym_name}"',
        block,
        count=1,
    )
    # KiCad 9 schematics do not use the old (id N) property format from library
    # files; strip it so kicad-cli can load the schematic without errors.
    block = re.sub(r"\s*\(id \d+\)", "", block)
    return block


def _find_symbol_pins(lib_name: str, sym_name: str) -> list[str]:
    """Return list of pin numbers for a symbol from KiCad symbol library."""
    lib_file = KICAD_SYMBOLS_DIR / f"{lib_name}.kicad_sym"
    if not lib_file.exists():
        return []
    text = lib_file.read_text(errors="replace")
    m = re.search(r'\(symbol "' + re.escape(sym_name) + r'"', text)
    if not m:
        return []
    segment = text[m.start() : m.start() + 12000]
    pin_nums = re.findall(r'\(number "([^"]+)"', segment)
    seen: set[str] = set()
    result: list[str] = []
    for p in pin_nums:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def _embed_lib_symbol(sch_file: Path, lib_name: str, sym_name: str) -> bool:
    """Embed the symbol definition into (lib_symbols) of the schematic."""
    full_id = f"{lib_name}:{sym_name}"
    text = sch_file.read_text()

    # Check if already embedded
    if f'(symbol "{full_id}"' in text and "(lib_symbols" in text:
        lb_idx = text.index("(lib_symbols")
        if f'(symbol "{full_id}"' in _extract_balanced(text, lb_idx):
            return True

    sym_def = _find_symbol_def(lib_name, sym_name)
    if not sym_def:
        return False

    # Symbol definition indented 4 spaces (2 base + 2 for content inside lib_symbols).
    indented = "    " + sym_def.replace("\n", "\n    ")

    if "(lib_symbols)" in text:
        # Replace the self-closing token; closing paren stays inside (kicad_sch …).
        text = text.replace("(lib_symbols)", f"(lib_symbols\n{indented}\n  )", 1)
    elif "(lib_symbols" in text:
        idx = text.index("(lib_symbols")
        block = _extract_balanced(text, idx)
        # block[-1] is ')' — strip it and add new symbol before re-closing.
        new_block = block[:-1] + f"\n{indented}\n  )"
        text = text[:idx] + new_block + text[idx + len(block) :]
    else:
        return False

    _atomic_write(sch_file, text, "kicad_sch", operation="embed-lib-symbol")
    return True


def _next_component_position(sch_file: Path) -> tuple[float, float]:
    """Return (x, y) for the next component, step right of existing ones."""
    if not sch_file.exists():
        return (50.8, 76.2)
    text = sch_file.read_text()
    xs = [float(m) for m in re.findall(r"\(at ([\d.]+) [\d.]+ 0\)", text)]
    base_x = (max(xs) + 25.4) if xs else 50.8
    return (base_x, 76.2)


def _append_to_schematic(sch_file: Path, s_expr: str) -> None:
    """Insert an S-expression block just before the sheet_instances section."""
    text = sch_file.read_text()
    marker = "  (sheet_instances"
    if marker in text:
        text = text.replace(marker, s_expr + "\n" + marker, 1)
    else:
        last = text.rfind(")")
        text = text[:last] + s_expr + "\n)\n"
    _atomic_write(sch_file, text, "kicad_sch", operation="append-to-schematic")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_add_component(args) -> AddComponentResult:
    """Add a component symbol to the schematic.

    Usage: add-component <LIB:SYM> <REF> [--value V] [--footprint FP]
    Example: add-component Device:R R1 --value 10k --footprint Resistor_SMD:R_0402
    """
    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic not found: {sch_file}")

    spec = ComponentSpec.from_args(args)
    pin_nums = _find_symbol_pins(spec.lib_name, spec.sym_name)
    if not pin_nums:
        print(f"⚠️  Symbol '{spec.lib_sym}' not found in {KICAD_SYMBOLS_DIR}")
        print("   Using default pins [1, 2]. Edit footprint assignment in KiCad.")
        pin_nums = ["1", "2"]

    x, y = _next_component_position(sch_file)
    sym_uuid = _new_uuid()
    pin_entries = "\n".join(
        f'    (pin "{p}" (uuid "{_new_uuid()}"))' for p in pin_nums
    )
    sym_entry = (
        f'  (symbol (lib_id "{spec.lib_sym}") (at {x:.2f} {y:.2f} 0) (unit 1)\n'
        f'    (exclude_from_sim yes) (in_bom yes) (on_board yes)\n'
        f'    (uuid "{sym_uuid}")\n'
        f'    (property "Reference" "{spec.ref}" (at {x + 1.27:.2f} {y - 1.27:.2f} 0)\n'
        f"      (effects (font (size 1.27 1.27)))\n"
        f"    )\n"
        f'    (property "Value" "{spec.value}" (at {x + 1.27:.2f} {y + 1.27:.2f} 0)\n'
        f"      (effects (font (size 1.27 1.27)))\n"
        f"    )\n"
        f'    (property "Footprint" "{spec.footprint}" (at {x:.2f} {y:.2f} 0)\n'
        f"      (effects (font (size 1.27 1.27)) hide)\n"
        f"    )\n"
        f'    (property "Datasheet" "~" (at {x:.2f} {y:.2f} 0)\n'
        f"      (effects (font (size 1.27 1.27)) hide)\n"
        f"    )\n"
        f"{pin_entries}\n"
        f'    (instances\n'
        f'      (project "{project.name}"\n'
        f'        (path "/"\n'
        f'          (reference "{spec.ref}")\n'
        f"          (unit 1)\n"
        f"        )\n"
        f"      )\n"
        f"    )\n"
        f"  )"
    )
    _append_to_schematic(sch_file, sym_entry)
    # Embed the symbol definition so kicad-cli can generate library part info
    # for netlist/BOM export.  _embed_lib_symbol strips (id N) and uses short
    # sub-symbol names (e.g. "R_0_1" not "Device:R_0_1") for compatibility.
    _embed_lib_symbol(sch_file, spec.lib_name, spec.sym_name)

    return AddComponentResult(
        ref=spec.ref,
        lib_sym=spec.lib_sym,
        value=spec.value,
        x=x,
        y=y,
        pins=tuple(pin_nums),
        has_footprint=bool(spec.footprint),
    )


def cmd_add_net(args) -> AddNetResult:
    """Add a named net label to the schematic.

    Usage: add-net <NAME> [--x X] [--y Y]   (coordinates in mm)
    Example: add-net VCC --x 60 --y 50
    """
    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic not found: {sch_file}")

    label = NetLabelSpec.from_args(args)
    label_entry = (
        f'  (label "{label.name}" (at {label.x:.2f} {label.y:.2f} 0) (fields_autoplaced yes)\n'
        f"    (effects (font (size 1.27 1.27)) (justify left bottom))\n"
        f'    (uuid "{_new_uuid()}")\n'
        f'    (property "Intersheet References" "${{INTERSHEET_REFS}}" (at 0 0 0)\n'
        f"      (effects (font (size 1.27 1.27)) (hide yes))\n"
        f"    )\n"
        f"  )"
    )
    _append_to_schematic(sch_file, label_entry)
    return AddNetResult(name=label.name, x=label.x, y=label.y)


def cmd_connect(args) -> ConnectResult:
    """Add a wire segment between two coordinates in the schematic.

    Usage: connect --from X1,Y1 --to X2,Y2   (coordinates in mm)
    Example: connect --from 50.8,76.2 --to 76.2,76.2

    Tip: use `preview-schematic` to read pin coordinates after placing components.
    """
    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic not found: {sch_file}")

    wire = WireSegment.from_args(args)
    wire_entry = (
        f"  (wire (pts (xy {wire.x1:.2f} {wire.y1:.2f}) (xy {wire.x2:.2f} {wire.y2:.2f}))\n"
        f"    (stroke (width 0) (type default))\n"
        f'    (uuid "{_new_uuid()}")\n'
        f"  )"
    )
    _append_to_schematic(sch_file, wire_entry)
    return ConnectResult(x1=wire.x1, y1=wire.y1, x2=wire.x2, y2=wire.y2)
