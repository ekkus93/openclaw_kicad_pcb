# Readability Regression Fixture — NE5532 Headphone Amp (Left Channel)

**Captured:** 2026-03-11  
**Source session:** `ne5532_headphone_amp_fa070cbe`  
**Purpose:** Phase 0 snapshot for CODE_REVIEW7 layout-regression work

## Why this fixture exists

This fixture captures the latest known bad output for the NE5532 left-channel
headphone amp after the earlier readability pass regressed.

The problem is no longer basic routing correctness. The regression is in
**placement and composition**: too many components collapse back into U1's
column, so the schematic reads like a narrow vertical pillar instead of an
intentionally drafted analog stage.

## Regression symptoms

### 1. Too many parts line up with U1 in one vertical column
- U1 sits at `x = 173.99 mm` in the captured layout.
- Multiple nearby passives and support parts share that exact x-column.
- The result is a tall, narrow op-amp-centered stack instead of a spread stage.

### 2. Block separation is weaker than the previous iteration
- Input-side parts remain left-biased.
- Output, feedback, and decoupling parts collapse around the op-amp column.
- The page no longer reads as clearly separated input, op-amp, output, and power areas.

### 3. The op-amp neighborhood is too tall and narrow
- Feedback and support parts are placed above and below U1 in the same x band.
- Local affinity is being enforced too aggressively as same-column placement.

### 4. Overall composition looks worse than the previous iteration
- The schematic still routes electrically.
- It no longer looks intentionally composed for human reading.
- The visual impression is again auto-generated rather than drafted.

## Included files

### `regressed_generated.kicad_sch`
The exact managed schematic captured from the latest session artifact.

### `circuit_ir.json`
The source netlist / circuit IR JSON used to generate the regressed output.

### `regressed_preview.svg`
Visual preview exported alongside the latest session output.

### `baseline_metrics.json`
Snapshot of Phase 0 regression-specific metrics:
- non-power refs sharing U1's x-column,
- feedback/support refs sharing U1's x-column,
- per-block spread statistics.

## Source provenance

- Session directory: `/home/ubo/kicad-projects/sessions/ne5532_headphone_amp_fa070cbe`
- Project directory: `NE5532_Headphone_Amp_Left/`
- Managed schematic captured from: `OpenClaw_Managed.kicad_sch`
- Preview captured from: `schematic_preview.svg/NE5532_Headphone_Amp_Left-OpenClaw_Managed.svg`

## Current measured regression metrics

- `u1_same_column_non_power = 9`
- `u1_same_column_feedback_support = 5`

Those values are intentionally poor. They define the failure mode that later
phases must improve.