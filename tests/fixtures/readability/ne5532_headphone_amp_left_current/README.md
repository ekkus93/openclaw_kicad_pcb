# Readability Baseline — NE5532 Headphone Amp (Left Channel)

**Generated:** 2026-03-10  
**Purpose:** Baseline "before" fixture for CODE_REVIEW6 readability improvements

## Fixture Identity

- Fixture name: `ne5532_headphone_amp_left_current`
- Fixture class: named readability regression fixture
- Paired regressed fixture: `ne5532_headphone_amp_left_regressed`
- Canonical paths live in the shared `tests` fixture registry so tests and review tooling resolve this fixture by name instead of duplicating raw paths.

## Current Problems

This schematic is the **baseline** output from the generator before Phase 1–4 readability improvements. It is electrically correct and passes structural lints, but it has the following readability problems:

### 1. Local Crowding (High Density)
- **Upper-left and center-left regions** are too dense
- Too many components and wires compete in a compact area
- Hard to read at normal zoom levels

### 2. Weak Functional Block Separation
- Input stage, op-amp gain stage, output stage, and power blocks exist electrically but are **not visually distinct**
- Blocks bleed into each other visually
- No clear spatial grouping

### 3. Power/Decoupling Clutter
- Power connector and nearby capacitors/support parts are **visually messy**
- Do not read as a clean power-distribution block
- Mixed into signal circuitry

### 4. Excessive Ground Symbols
- Too many separate GND symbols fragment the drawing visually
- Ground handling is noisy rather than clean

### 5. Weak Signal Flow
- Can somewhat follow the circuit left-to-right, but not cleanly
- Signal path feels obscured by local placement choices and wiring detours

### 6. Too Many Short Jogs and Wire Fragments
- Lots of small orthogonal wire segments
- Feels over-routed and mechanical
- Jaggy appearance

### 7. Some Long Wires Dominate
- Certain longer wires attract too much visual attention
- Reduce locality of the drawing

### 8. Op-Amp Neighborhood Not Clean
- Area around NE5532 is better than before but still not cleanly composed
- Feedback parts, input-side parts, output-side parts, and supply parts are not clearly organized around the op-amp

### 9. Output Staging Not Strong
- Output section exists on right side but doesn't read as a clean output block
- No clear chain into output connector

### 10. Input Staging Could Be Clearer
- Input jack and related parts are on left
- Input stage doesn't read as one coherent, intentionally organized block

### 11. Uneven Page Composition
- Some areas dense, some sparse
- Page feels auto-laid-out rather than composed

### 12. Weak Readability at Normal Zoom
- Requires too much visual effort to understand the circuit
- Should be readable quickly at normal KiCad editing zoom

### 13. Some Orientations Feel Routing-Driven
- A few symbols appear rotated/oriented mainly to satisfy routing rather than to help communicate the circuit

### 14. Overall "Router-Driven" Impression
- Schematic is valid and improved from earlier versions
- Still looks like the tool optimized for connection/collision avoidance, not human understanding

## What Improved From Earlier Versions

Compared to older column-dump layouts:
- Symbols no longer collapsed into single narrow vertical list
- More left-to-right structure
- Routing more like a circuit, less like netlist dump
- Op-amp more central
- Better page usage

## Expected Improvements (Phases 1-4)

After completing Phases 1-4 of CODE_REVIEW6_TODO, this schematic should:

1. **Show clear functional blocks**: input, op-amp/gain, output, power
2. **Reduce crowding**: better whitespace, lower local density
3. **Strengthen signal flow**: clear left-to-right visual story
4. **Clean up op-amp neighborhood**: intentional organization around active device
5. **Reduce ground clutter**: fewer GND symbols, cleaner power presentation
6. **Improve page composition**: balanced, human-readable page usage

## Source

- **IR/Netlist:** Canonical headphone amp IR from `tests/fixtures/readability/ne5532_headphone_amp_left_current/circuit_ir.json`
- **Generation:** `cmd_new_from_netlist` with current code (before readability improvements)
- **Circuit:** Simplified dual-channel passive/resistive headphone amplifier (13 components, 9 nets)

## Authoritative Structural Expectations

### Expected key component refs

- Active device: `U1`
- Power entry: `J3`
- Input path: `J1`, `C5`, `R1`, `RV1`
- Gain / feedback: `R2`, `R3`, `R4`
- Buffer / coupling / output: `C6`, `R5`, `R6`, `C7`, `R7`, `J2`
- Decoupling: `C1`, `C2`, `C3`, `C4`

### Expected key net names

- Supply rails: `VPLUS15`, `VMINUS15`, `0V`
- Input path: `LEFT_IN`, `IN_L_AC`, `VOL_L_OUT`
- Gain / feedback: `U1A_INV`, `OUT_L_STAGE1`
- Buffer / output: `BUF_L_IN`, `OUT_L_STAGE2_RAW`, `AFTER_R6`, `HP_L_OUT`

### Fixture-specific expectations

- `U1` is a dual `NE5532`; generation should preserve explicit multi-unit handling rather than collapsing both stages into one ambiguous drawable instance.
- `J1` and `J2` are TRS connectors used in mono-left mode for this fixture. Their sleeve pins join `0V` and their tip pins carry `LEFT_IN` / `HP_L_OUT`.
- The ring pins on `J1` and `J2` are intentionally unused in this left-channel fixture. Generated managed schematics should therefore contain two explicit KiCad `no_connect` markers, one for each unused ring pin.
- The `R1` / `C5` input topology is intentionally preserved as-authored by the fixture. Validation may warn about it, but generation must not silently rewrite it.

## Baseline Metrics Snapshot

- `symbol_count = 19`
- `x_columns = 11`
- `short_wires = 29`
- `wire_stub_ratio = 0.40384615384615385`
- `avg_spacing = 15.52910168432786`
- Region density:
	- `top_left = 0.3684210526315789`
	- `top_right = 0.15789473684210525`
	- `bottom_left = 0.21052631578947367`
	- `bottom_right = 0.2631578947368421`

These values are baseline measurements only. They describe the current generated layout and should be used for regression comparison, not as the target end-state for schematic readability.
