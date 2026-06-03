# Placeholder Symbol Feature — Design Spec

## Problem

When the Circuit IR references a component whose `symbol` field names a KiCad library
entry that is not present in any searched symbol directory (e.g. `74xx:74HC4017`), the
generation pipeline currently raises a hard `SYMBOL_NOT_FOUND` error and stops.  The
user sees an opaque error and must either fix the symbol ID manually or abandon the
session.

## Goal

When a symbol is not found, synthesise a **generic rectangular placeholder** symbol
on the fly using only the pin numbers referenced in the Circuit IR, embed it in the
generated schematic, and downgrade the failure to an advisory warning so generation
can continue.  The result is a fully valid, loadable KiCad schematic.  The placeholder
is visually distinct so the designer knows which components need real symbols before
fabrication.

---

## Guiding principles

- **No guessing about pins.**  The pin set for the placeholder is derived exclusively
  from the Circuit IR itself (the `nets` section already enumerates every `(ref, pin)`
  pair used).  We never invent pins the IR didn't ask for.
- **Keep `SymbolIndex` pure.**  It is a library-lookup service; it should not know
  about synthesis.  Fallback logic lives above it.
- **One new module.**  All synthesis logic lives in `kicad_pcb/placeholder_symbol.py`.
  No existing module grows a large new responsibility.
- **Downgrade, don't suppress.**  Every placeholder component produces a named advisory
  (`SYMBOL_PLACEHOLDER_USED`) that surfaces in the wizard UI as a warning.
- **Idempotent embedding.**  `SchematicDoc.embed_lib_symbol()` already deduplicates;
  we call it the same way we call it for real symbols.
- **KiCad-loadable output.**  A schematic containing placeholder symbols must open and
  render in KiCad without errors.  This is the acceptance test.

---

## Architecture overview

```
Circuit IR
   │
   ▼
validate_circuit_ir()          ← no change
   │
   ▼
validate_ir_symbols()          ← CHANGED: collect unknown symbols as warnings,
   │   returns IrSymbolResult    return IR-derived pin sets instead of raising
   │
   ▼
[new] build_symbol_context()   ← resolves real symbols + builds placeholder
   │   in _sch_apply.py          definitions for unknowns
   │
   ├──► SymbolIndex             ← no change to public interface
   │
   └──► PlaceholderSymbol.*    ← NEW MODULE
            synthesize_definition()  → ListNode  (for schematic embedding)
            synthesize_pin_at()      → pin-at dict  (for router)
   │
   ▼
layout engine                  ← no change (uses abstract IR graph, not pin coords)
   │
   ▼
_compute_pin_endpoints()       ← CHANGED: uses placeholder pin-at data for unknown syms
   │
   ▼
router                         ← no change
   │
   ▼
mutate_and_validate_sch()      ← CHANGED: embeds placeholder ListNodes alongside
                                  real symbols
```

---

## New module: `src/kicad_pcb/placeholder_symbol.py`

Single responsibility: given a `symbol_id` string and a set of pin number strings,
produce everything the rest of the pipeline needs.

### Public API

```python
from dataclasses import dataclass
from kicad_pcb.sexpr.nodes import ListNode


@dataclass(frozen=True)
class PlaceholderSymbol:
    """All data needed to use a synthesised stand-in symbol."""

    symbol_id: str
    """Fully-qualified id kept as-is, e.g. '74xx:74HC4017'."""

    pin_numbers: frozenset[str]
    """Pin numbers taken from the Circuit IR nets (never invented)."""

    definition: ListNode
    """Ready-to-embed (symbol "lib:name" ...) S-expression node."""

    pin_at: dict[str, dict[str, tuple[float, float, float]]]
    """unit → {pin_number → (x_mm, y_mm, angle_deg)} for _compute_pin_endpoints."""


def build(symbol_id: str, pin_numbers: frozenset[str]) -> PlaceholderSymbol:
    """Synthesise a placeholder for symbol_id using the given pin numbers."""
    ...
```

### Symbol geometry

A placeholder symbol is a **two-column rectangular box**:

- Box body: 10.16 mm wide × `max(left, right) × 2.54 + 2.54` mm tall (all on
  0.254 mm snap grid).
- Pin split: left column = pins sorted numerically (first ⌈n/2⌉), right column =
  the rest.  Single-pin case → left only.
- Stub length: 2.54 mm (standard KiCad default).
- Electrical type: `passive` for all pins (safe, no ERC conflict).
- Left-side pins: `(at  -7.62  y  0)` — stub points left.
- Right-side pins: `(at   7.62  y 180)` — stub points right.
- Pin spacing: 2.54 mm vertically, centred on the box.
- Border style: dashed stroke `(type dash)` to visually distinguish from real symbols.
- Reference property: kept from the original symbol id (e.g. `U` for ICs).
- Value property: `"<sym_name> [PLACEHOLDER]"` — clearly communicates intent inside KiCad.
- No footprint, no datasheet (empty strings).

### S-expression output (example for a 6-pin symbol)

```
(symbol "74xx:74HC4017"
  (property "Reference" "U" (at 0 8 0))
  (property "Value" "74HC4017 [PLACEHOLDER]" (at 0 -8 0))
  (property "Footprint" "" (at 0 0 0))
  (property "Datasheet" "" (at 0 0 0))
  (symbol "74HC4017_0_1"
    (rectangle (start -5.08 -7.62) (end 5.08 7.62)
      (stroke (width 0) (type dash))
      (fill (type none)))
    (pin passive (at -7.62  5.08   0) (length 2.54)
      (name "~" (effects (font (size 1.27 1.27))))
      (number "1" (effects (font (size 1.27 1.27)))))
    (pin passive (at -7.62  2.54   0) (length 2.54) ...)  ; pin 2
    (pin passive (at -7.62  0.00   0) (length 2.54) ...)  ; pin 3
    (pin passive (at  7.62  5.08 180) (length 2.54) ...)  ; pin 4
    (pin passive (at  7.62  2.54 180) (length 2.54) ...)  ; pin 5
    (pin passive (at  7.62  0.00 180) (length 2.54) ...)  ; pin 6
  )
)
```

### `pin_at` output (mirrors `SymbolIndex.get_unit_pin_at`)

```python
{
    "1": {   # unit "1" (placeholder symbols are always single-unit)
        "1": (-7.62,  5.08,   0.0),
        "2": (-7.62,  2.54,   0.0),
        "3": (-7.62,  0.00,   0.0),
        "4": ( 7.62,  5.08, 180.0),
        "5": ( 7.62,  2.54, 180.0),
        "6": ( 7.62,  0.00, 180.0),
    }
}
```

---

## Changes to `ir/validate.py`

### New return type

```python
@dataclass
class IrSymbolValidationResult:
    errors: list[UserError]          # PIN_INVALID, MULTI_UNIT_UNSUPPORTED, etc.
    unknown_symbols: dict[str, frozenset[str]]
    # symbol_id → pin_numbers_from_ir for every symbol not found in libraries
```

### Changed function signature

```python
def validate_ir_symbols(
    ir: CircuitIR,
    symbol_index: SymbolIndex,
) -> IrSymbolValidationResult:
```

Callers that previously expected this to raise now receive the result and decide
what to do with `unknown_symbols`.  Existing callers that pass `None` as the
symbol index continue to work (return empty result).

### Behaviour change

```python
for sym_id in sorted(set(component_symbol_by_ref.values())):
    try:
        symbol_pins[sym_id] = symbol_index.get_pins(sym_id)
    except UserError as exc:
        if exc.code == ErrorCode.SYMBOL_NOT_FOUND:
            # Derive the pin set from the IR itself
            ir_pins = frozenset(
                pr.pin
                for net in ir.nets
                for pr in net.pins
                if component_symbol_by_ref.get(pr.ref) == sym_id
            )
            unknown_symbols[sym_id] = ir_pins
            # Use the IR-derived set for subsequent pin-validity checks below
            symbol_pins[sym_id] = set(ir_pins)
        else:
            raise
```

Pin-validity and unit-validity checks run unchanged on `symbol_pins[sym_id]`,
which now contains the IR-derived set for unknowns.  The per-pin checks will pass
because the IR can only reference pins it declared.

---

## Changes to `commands/_sch_apply.py`

### 1. Receive `IrSymbolValidationResult` instead of `None`-on-success

```python
result = validate_ir_symbols(ir, symbol_index)
if result.errors:
    raise UserError(
        "Circuit IR has invalid pin references",
        code=ErrorCode.IR_SEMANTIC_INVALID,
        details={"errors": [e.details for e in result.errors]},
    )
```

### 2. Build placeholder symbols (new helper)

```python
placeholders: dict[str, PlaceholderSymbol] = {
    sym_id: placeholder_symbol.build(sym_id, pin_nums)
    for sym_id, pin_nums in result.unknown_symbols.items()
}
```

### 3. Emit advisories

```python
for sym_id, placeholder in placeholders.items():
    warnings.append(GenerationAdvisory(
        code=AdvisoryCode.SYMBOL_PLACEHOLDER_USED,
        message=(
            f"Symbol '{sym_id}' not found in libraries — "
            f"a generic placeholder was used. Replace with a real "
            f"symbol before fabricating."
        ),
        details={"symbol": sym_id, "pins": sorted(placeholder.pin_numbers)},
    ))
```

### 4. Supply placeholder `pin_at` data to `_compute_pin_endpoints`

`_compute_pin_endpoints` currently calls `symbol_index.get_unit_pin_at(sym_id)`.
Add a `overrides` parameter:

```python
def _compute_pin_endpoints(
    ir: CircuitIR,
    positions: ...,
    symbol_index: SymbolIndex,
    *,
    pin_at_overrides: dict[str, dict[str, dict[str, tuple[float, float, float]]]] | None = None,
) -> dict[tuple[str, str], tuple[float, float, float]]:
    ...
    for component in ir.components:
        sym_id = component.symbol
        if pin_at_overrides and sym_id in pin_at_overrides:
            unit_pin_at = pin_at_overrides[sym_id]
        else:
            unit_pin_at = symbol_index.get_unit_pin_at(sym_id)
        ...
```

Call site becomes:

```python
pin_endpoints = _compute_pin_endpoints(
    ir, positions, symbol_index,
    pin_at_overrides={sid: p.pin_at for sid, p in placeholders.items()},
)
```

### 5. Embed placeholder definitions in the schematic mutator

```python
def _mutate(doc: SchematicDoc) -> None:
    # ... existing symbol embedding logic ...
    for placeholder in placeholders.values():
        doc.embed_lib_symbol(placeholder.definition)
    # ... rest of mutation ...
```

---

## Changes to advisory/warning codes

Add to `kicad_pcb/advisories.py` (or wherever `AdvisoryCode` is defined):

```python
SYMBOL_PLACEHOLDER_USED = "SYMBOL_PLACEHOLDER_USED"
```

This code surfaces in:
- `OpenClaw_Warnings.json` alongside other generation advisories
- The wizard IR step's `ir_validation.warnings` array
- The `WarningsPanel` in the frontend

---

## Test plan

### Unit tests (all in `tests/unit/`)

| Test | What it covers |
|---|---|
| `test_placeholder_symbol.py::test_build_single_pin` | 1-pin placeholder: correct geometry, left side only |
| `test_placeholder_symbol.py::test_build_even_pins` | 6-pin: 3 left, 3 right, correct y-coordinates |
| `test_placeholder_symbol.py::test_build_odd_pins` | 5-pin: 3 left, 2 right |
| `test_placeholder_symbol.py::test_pin_at_matches_definition` | `pin_at` coords match the `(at ...)` nodes in the S-expression |
| `test_placeholder_symbol.py::test_symbol_id_preserved` | `symbol_id` survives round-trip through S-expression node |
| `test_placeholder_symbol.py::test_dashed_border` | definition contains `(type dash)` stroke |
| `test_placeholder_symbol.py::test_value_contains_placeholder_marker` | value property ends with `[PLACEHOLDER]` |
| `test_ir_validate.py::test_unknown_symbol_returns_result_not_raises` | `validate_ir_symbols` returns result, not raises, for missing symbol |
| `test_ir_validate.py::test_unknown_symbol_pins_from_ir` | pin set in result matches IR nets, not library |
| `test_ir_validate.py::test_known_symbol_still_validates_pins` | real symbols still check pin validity against library |
| `test_ir_validate.py::test_pin_invalid_on_placeholder_symbol` | invalid pin in IR still errors even when symbol is unknown (IR self-consistency) |
| `test_sch_apply.py::test_placeholder_advisory_emitted` | generation of IR with unknown symbol produces `SYMBOL_PLACEHOLDER_USED` advisory |
| `test_sch_apply.py::test_placeholder_embedded_in_lib_symbols` | generated schematic contains `(symbol "lib:name" ...)` for the placeholder |
| `test_sch_apply.py::test_placeholder_schematic_parseable` | generated schematic round-trips through `SchematicDoc` without error |
| `test_sch_apply.py::test_mixed_real_and_placeholder` | IR with one real symbol + one unknown generates correct schematic |

### Integration test

| Test | What it covers |
|---|---|
| `tests/integration/test_placeholder_kicad.py::test_kicad_loads_placeholder_schematic` | `kicad-cli sch validate` accepts a schematic containing a placeholder symbol (requires `kicad-cli`) |

---

## Out of scope for this feature

- **Automatic symbol substitution** (e.g. suggesting `4xxx:CD4017BE` for `74xx:74HC4017`).
  This is a separate LLM/lookup feature.
- **Interactive symbol mapping** in the wizard (letting the user pick a real symbol).
  Deferred to a future wizard step.
- **ERC pass-through** — placeholder symbols will produce KiCad ERC warnings about
  unconnected power pins etc.  These are expected and acceptable.
- **Footprint assignment** for placeholders.  Left blank intentionally; the designer
  assigns footprints after substituting the real symbol.

---

## File change summary

| File | Change |
|---|---|
| `src/kicad_pcb/placeholder_symbol.py` | **NEW** — all synthesis logic |
| `src/kicad_pcb/ir/validate.py` | Return `IrSymbolValidationResult`; catch `SYMBOL_NOT_FOUND` per symbol |
| `src/kicad_pcb/commands/_sch_apply.py` | Consume result; build placeholders; pass `pin_at_overrides`; embed in mutator |
| `src/kicad_pcb/commands/_validate.py` | Update call site for new `validate_ir_symbols` signature |
| `src/kicad_pcb/advisories.py` (or equivalent) | Add `SYMBOL_PLACEHOLDER_USED` code |
| `tests/unit/test_placeholder_symbol.py` | **NEW** |
| `tests/unit/test_ir_validate.py` | Extend with unknown-symbol cases |
| `tests/unit/test_sch_apply.py` | Extend with placeholder generation cases |
| `tests/integration/test_placeholder_kicad.py` | **NEW** (requires kicad-cli) |

`symbol_index.py`, `layout.py`, `router.py`, and `sch_doc/__init__.py` require
**no changes** — the abstraction boundaries hold.
