# Readability Baseline — NE5532 Headphone Amp (Left Channel)

**Generated:** 2026-03-10  
**Purpose:** Baseline "before" fixture for CODE_REVIEW6 readability improvements

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

- **IR/Netlist:** Canonical headphone amp IR from `tests/fixtures/regressions/headphone_amp_ir.json`
- **Generation:** `cmd_new_from_netlist` with current code (before readability improvements)
- **Circuit:** Simplified dual-channel passive/resistive headphone amplifier (13 components, 9 nets)
