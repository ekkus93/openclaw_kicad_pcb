# Readability Baseline — NE5532 Headphone Amp (Left Channel)

**Generated:** 2026-04-01  
**Purpose:** Current readability baseline for the authoritative reviewed NE5532 left-channel headphone-amp netlist

## Fixture Identity

- Fixture name: `ne5532_headphone_amp_left_current`
- Fixture class: named readability regression fixture
- Paired regressed fixture: `ne5532_headphone_amp_left_regressed`
- Canonical lookup lives in the shared `tests` fixture registry so tests and tooling resolve this fixture by name instead of duplicating paths.

## Source Of Truth

- **Authoritative source netlist:** `code_review/ne5532_headphone_amp_netlist.json`
- **Fixture IR copy:** `tests/fixtures/readability/ne5532_headphone_amp_left_current/circuit_ir.json`
- **Generation path:** `cmd_new_from_netlist` with `--symbols-dir tests/fixtures/symbols --mode internal`
- **Managed schematic baseline:** `tests/fixtures/readability/ne5532_headphone_amp_left_current/baseline_generated.kicad_sch`

This fixture now mirrors the real reviewed NE5532 circuit rather than the older passive placeholder headphone-amp IR.

## Circuit Scope

Single-channel NE5532 signal path with:

- input TRS connector and AC-coupled input stage
- volume potentiometer feeding the first op-amp stage
- first gain stage on `U1A`
- AC-coupled handoff into `U1B`
- follower/output stage with series output resistor and output coupling capacitor
- split-rail power entry with local decoupling capacitors
- mono-left usage of TRS input/output connectors with explicit unused ring-pin handling in generated schematics

## Expected Structural Semantics

### Expected key component refs

- Active device: `U1`
- Power entry: `J3`
- Decoupling: `C1`, `C2`, `C3`, `C4`
- Input path: `J1`, `C5`, `RV1`
- Gain / feedback: `R2`, `R3`, `R4`
- Inter-stage/output path: `C6`, `R5`, `R6`, `C7`, `R7`, `J2`

### Expected key net names

- Supply rails: `VPLUS15`, `VMINUS15`, `0V`
- Input path: `LEFT_IN`, `IN_L_AC`, `VOL_L_OUT`
- Gain / feedback: `U1A_INV`, `OUT_L_STAGE1`
- Buffer / output: `BUF_L_IN`, `OUT_L_STAGE2_RAW`, `AFTER_R6`, `HP_L_OUT`

### Fixture-specific expectations

- `U1` is a dual `NE5532`; generation should preserve explicit multi-unit handling.
- `J1` and `J2` remain authored as `Connector:AudioJack3` symbols. Connector clarity comes from explicit KiCad `no_connect` markers on the intentionally unused ring pins.
- The reviewed source netlist no longer includes the old `R1` input-capacitor bypass path. `C5` is the only element between `LEFT_IN` and `IN_L_AC`.
- The current advisory set for this fixture is expected to include:
  - `HEADPHONE_OUTPUT_IMPEDANCE_HIGH`
  - `SPLIT_RAIL_INTERSTAGE_AC_COUPLING_PRESENT`
- Generation in internal mode also reports `VALIDATION_MODE_INTERNAL`.

## Baseline Readability Notes

This fixture is not the intentionally bad regression snapshot. It is the current generated baseline for the real reviewed circuit. The paired `ne5532_headphone_amp_left_regressed` fixture remains the known worse comparison point for review tooling.

The current layout is expected to preserve:

- clear left-to-right signal progression from `J1` to `J2`
- an identifiable op-amp core with nearby feedback/input staging
- a compact decoupling cluster around the op-amp family
- explicit connector-edge placement and connector-facing orientation rules

## Baseline Metrics Snapshot

- `symbol_count = 24`
- `x_columns = 16`
- `short_wires = 81`
- `wire_stub_ratio = 0.47580645161290325`
- `avg_spacing = 9.623934104840641`
- Region density:
  - `top_left = 0.0`
  - `top_right = 0.125`
  - `bottom_left = 0.3333333333333333`
  - `bottom_right = 0.5416666666666666`

These values describe the checked-in current baseline only. They are comparison anchors for readability regression tests and review tooling, not claims that the schematic is fully optimized.
