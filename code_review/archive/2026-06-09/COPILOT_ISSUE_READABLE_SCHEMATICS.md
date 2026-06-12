# Issue: Make generated schematics readable (Graphviz placement + real wiring; eliminate “column dump”)

## Problem Summary

The `kicad-pcb` skill produces `.kicad_sch` files that are **electrically valid** (netlist/IR converts cleanly and KiCad loads them), but the **schematic layout is unusable for humans**. The output looks like a “netlist dump” instead of a conventional circuit diagram.

Using `ne5532_headphone_amp_left_v3_schematic.zip` as a concrete example, `OpenClaw_Managed.kicad_sch` shows:

- **Column dump placement**: most symbols share the same X coordinate (single vertical list), leaving most of the page empty and destroying left-to-right signal flow.
- **Out-of-bounds / overlap**: layout lints trigger (notably **LAY004** and **LAY003**) yet output is still produced.
- **Power net clutter**: many repeated `GND` global labels near pins; rails/power symbols aren’t used effectively.
- **Stub/trunk wiring style**: lots of tiny stub wires + long vertical trunks; connectivity is technically there but hard to read.

## Goal

Generate schematics that follow basic schematic conventions:

- Components placed in a **graph layout** with meaningful grouping and page usage.
- Prefer **real wires and junctions** for small/medium nets.
- Power nets (GND/V+/V-) handled with **power symbols or rails**, not repeated labels.
- Enforce **layout lints as validation gates** (don’t write output when LAY004 occurs).

---

## Acceptance Criteria (Measurable)

On the headphone amp fixture (`OpenClaw_Managed.kicad_sch` produced from the same IR), after this change:

### Placement
1) **Uses page width**: at least **6 distinct X columns** for symbol positions (not a single vertical stack).
2) **No out-of-bounds**: **LAY004 must not occur** (and if it does, generation must fail and not write files).
3) **No overlaps**: **LAY003 must not occur** (or must be reduced to zero overlaps by deoverlap/post-fit).

### Power nets
4) **GND clutter reduction**: do **not** place a `global_label "GND"` at every power pin.

   Either:
   - use KiCad **power symbols** for GND/V rails, or
   - create a **rail strategy** (one rail per net) and connect taps.

   Target: **GND global labels ≤ 2** per sheet (ideally 0 if using symbols).

### Wiring
5) **Multi-pin nets are routed as spines/hubs**, not per-pin label stubs.

   For nets with degree 3–6, default routing should be **spine (bus) routing**.

6) **Direct wiring for 2-pin nets**: degree-2 nets should produce a real wire between pins unless distance is extreme.
7) **Reduce stub dominance**: fewer “pin → 5.08mm stub → trunk” patterns; overall diagram should show connected local wiring.

### Validation behavior
8) If the new layout/wiring violates layout lints (LAY003/LAY004/LAY005), the pipeline must:

- fail the operation (non-zero),
- report lint codes + details,
- and **must not overwrite** existing files (transactional write contract).

---

## Implementation Notes / Where to Change Code

### 1) Pass placement/tier info into the router (currently missing)

**File:** `src/kicad_pcb/commands/_sch_apply.py`

Currently routing is called without the information it needs:
- `tiers=None`
- `positions=None`
- `use_bus=False`

**Change:**
- Have `_write_symbols()` return `raw_layout` including rotation and/or a `positions` map suitable for routing.
- Compute `tiers = assign_tiers(ir)` (or reuse from layout engine if it already computed tiers).
- Call:

```py
routing = route_nets(
    ir=ir,
    pin_endpoints=pin_endpoints,
    tiers=tiers,
    positions=raw_layout,   # {ref: (x,y,rot)}
    use_bus=True,
)
```

This should immediately reduce label-stub fallback and improve wire routing decisions.

### 2) Make spine routing the default for multi-pin nets

**File:** `src/kicad_pcb/router.py`

Today, “spine route” is optional and many nets fall back to “stub+label-per-pin”.

**Change:**
- For `3 <= degree <= HUB_MAX`, default to spine routing (equivalent to `use_bus=True` behavior) unless a net is explicitly marked “power”.
- Make “stub+label-per-pin” the last resort, not the default.

### 3) Improve power net rendering (stop emitting global_label per pin)

**File:** `src/kicad_pcb/router.py` (power net handling)

**Change:**
- Replace repeated `global_label("GND")` per power pin with:

  - **Power symbols** (preferred), or
  - **single rail** + short taps and one label per rail.

Define a clear policy:
- power nets => symbol/rail strategy
- only label power nets once per sheet (if labeling is used at all)

### 4) Ensure final “fit to page” occurs after all post-layout snaps

**File:** `src/kicad_pcb/graphviz_layout/snap.py`

Post-layout steps (snap, connector snaps, stereo split, deoverlap, crossing remediation) can push symbols out of bounds.

**Change:**
- Add a final “fit_to_page / clamp_to_bounds” after `_apply_post_layout_snaps()` completes.
- Ensure the final positions respect page size + margins.

### 5) Enforce layout lints as hard gates in the pipeline

Confirm that LAY004 (and likely LAY003) are treated as errors in the validation pipeline and that these errors abort the write.

If lint errors currently only warn:
- promote LAY004 to “error” and block output.
- ensure the transactional write path does not commit on lint failure.

---

## Tests to Add (Regression + Golden)

### Unit tests
1) Routing receives `tiers` and `positions`:
   - verify `route_nets()` uses tier/position-aware routing paths when provided.
2) Multi-pin net spine routing:
   - a net with 4 endpoints produces a spine + taps, not 4 stubs + labels.
3) Power net policy:
   - GND net with N pins does not produce N global labels.

### Golden tests (fixtures)
Use the headphone amp IR / netlist fixture:

Assert:
- distinct X columns ≥ 6
- no LAY004 and no LAY003
- GND global labels ≤ 2
- wiring count/pattern shows spine routing for multi-pin nets

### Integration test (optional, skip if no KiCad)
- `kicad-cli sch erc` passes on the generated schematic.

---

## Deliverable

A readable schematic generator that:
- uses Graphviz placement effectively (no column dump),
- routes nets with conventional schematic wiring rules,
- handles power nets cleanly,
- and refuses to output/write schematics that violate layout validation.
