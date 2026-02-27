# CODE_REVIEW4 — Symbol Handling Bugs Analysis

**Original report (OpenClaw bot):** `search-symbols "NE5532"` returns
`Amplifier_Operational:NE5532  (0 pins)`, which causes apply-netlist to silently
produce an empty schematic.

**Analyst:** GitHub Copilot code review, 2026-02-27

---

## Bug 1 — `search-symbols` reports 0 pins for `extends`-based symbols (CONFIRMED)

### Root cause (corrected)

The original report attributes the 0-pin count to "multi-unit symbol aggregation".
That diagnosis is **partially incorrect**. The actual root cause is KiCad's **`extends`
inheritance mechanism**, not multi-unit layout.

In `Amplifier_Operational.kicad_sym`, NE5532 is defined as:

```
(symbol "NE5532" (extends "LM2904")
  (property "ki_description" "Dual Low-Noise Operational Amplifiers, DIP-8/SOIC-8" ...)
  ...properties only, no pins, no graphics...
)
```

All 8 pins live in `LM2904`'s sub-unit blocks (`LM2904_1_1`, `LM2904_2_1`,
`LM2904_3_1`). KiCad resolves this at load time. The NE5532 block itself contains
**zero `(pin ...` entries**.

The indexer (`_count_pins_in_block` in `commands/search.py`) counts pins with a
regex scan over the raw block text:

```python
def _count_pins_in_block(block_text: str) -> int:
    return len(re.findall(r"\(pin\s+", block_text))
```

For NE5532, that regex finds nothing — correctly, since the block genuinely has no
pins. The bug is that the indexer does **not follow the `extends` chain** to the
parent symbol to collect pins from there.

### Why multi-unit is not the bug here

For standalone symbols that do have sub-units (e.g. LM2904 itself), those
sub-unit blocks (`LM2904_1_1` etc.) are nested **inside** the parent's block text.
`_count_pins_in_block` receives the entire parent block text and the regex does
pick up the nested `(pin ...` entries. The `_SUB_UNIT_RE` filter in
`_extract_symbol_blocks` only skips sub-unit entries as **top-level** symbols — it
does not strip them from the parent's block text before pin counting. So LM2904
itself reports the correct pin count.

### Scope

This affects **every symbol that uses `(extends ...)`** in any KiCad library.
It is widespread: dozens of op-amps, comparators, BJTs, and other components use
this pattern to avoid duplicating pin definitions across package variants.

### Fix location

`kicad-pcb/src/kicad_pcb/commands/search.py` — `_parse_file_to_cached()`.

Replace `_count_pins_in_block(block_text)` with a call to
`read_lib_symbol_pins(lib_name, sym_name, symbols_dir=lib_file.parent)` from
`sch_doc.py`, which already correctly walks the `extends` chain. This requires
adding an import of `read_lib_symbol_pins` to `search.py`.

Since `_parse_file_to_cached` is the cache-miss slow path (runs at most once per
file per install), the additional AST parsing cost is acceptable.

### Side effect: stale cache entries

After the fix, any existing `symbol_index.db` entries built with the broken counter
will have wrong pin counts. Because the cache is keyed by `(lib_file, mtime)` and
the library files will not have changed, these stale entries will **not** be evicted
automatically. A schema version bump in the cache (or a `--force` flag on
`build-symbol-index`) is needed to force regeneration.

---

## Bug 2a — `apply-netlist` has no 0-pin preflight guard (CONFIRMED)

`netlist.py` has **no guard** that calls `read_lib_symbol_pins` before writing the
schematic. A symbol with 0 pins (whether due to Bug 1 or a genuinely missing symbol)
will not cause an error; the tool reports "Symbols added: 18, Nets applied: 12" and
exits 0.

`_embed_symbol_if_found` in `netlist.py` calls `read_lib_symbol_def_chain`, which
**does** handle `extends` correctly — so the NE5532 symbol block (with full pin
graphics inherited from LM2904) is likely being embedded into the schematic file
correctly. The fix for Bug 1 won't change that. What's missing is validation that
every IR net references a pin that actually exists on the resolved symbol.

### Fix location

`kicad-pcb/src/kicad_pcb/commands/netlist.py` — add a preflight check before the
schematic write step. For each unique symbol in the IR:
1. Call `read_lib_symbol_pins(lib_name, sym_name, symbols_dir=...)`.
2. If the result is empty, abort with `SYMBOL_HAS_NO_PINS: <symbol_id>`.
3. For each net pin reference, verify the pin name/number exists in the resolved
   pin list. If not, abort with `PIN_NOT_FOUND: <ref>.<pin> on <symbol_id>`.

---

## Bug 2b — SVG preview blank even when symbols are embedded (NEEDS INVESTIGATION)

The `_write_nets` function in `netlist.py` places binding markers as **hidden text
elements** (`doc.add_text(..., hidden=True)`), not as actual KiCad wire-to-pin
connections. KiCad does not render hidden text in schematic previews, and the
EESchema SVG exporter will show only component outlines with no visible wiring —
which explains the "blank" appearance even when the tool claims 12 nets applied.

This is a **separate, deeper bug** from the 0-pin count issue. The current net
application strategy does not produce a valid KiCad wire-connected schematic. It
needs to be re-examined independently.

### Requires investigation

- Determine what `doc.add_symbol` expects for `pin_uuids` and whether pin UUIDs
  need to correspond to actual wired connections.
- Check whether existing integration tests cover a round-trip of a real
  multi-net schematic through `apply-netlist` + `preview-schematic`.
- Examine the SVG output of a known-good apply-netlist run against a simple 2-pin
  resistor to isolate whether `_write_nets` produces any visible output at all.

---

## Bug 3 — `debug-symbol` command does not exist (CONFIRMED MISSING)

`sch_doc.py` already exports `read_lib_symbol_pins` which returns the full resolved
pin list (handling `extends`). Adding `debug-symbol` is a thin CLI wrapper around
that function — low effort, high diagnostic value.

The command should print:
- Resolved pin numbers and names
- Whether the symbol uses `extends` and what base symbol it resolves to
- Total pin count

---

## Summary

| # | Issue | Root Cause | File(s) | Priority |
|---|-------|-----------|---------|----------|
| 1 | `search-symbols` reports 0 pins for `extends` symbols (NE5532, etc.) | `_count_pins_in_block` ignores `extends` inheritance | `commands/search.py` | P0 |
| 2a | `apply-netlist` has no 0-pin / invalid-pin preflight guard | `read_lib_symbol_pins` never called in netlist pipeline | `commands/netlist.py` | P0 |
| 2b | SVG preview blank despite symbols being embedded | `_write_nets` uses hidden text, not real KiCad wiring | `commands/netlist.py` | P1 — investigate first |
| 3 | No `debug-symbol` command | Feature not implemented | `commands/search.py`, `cli.py` | P2 |
