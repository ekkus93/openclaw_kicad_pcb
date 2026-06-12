# CODE_REVIEW7.md

## Context

This review covers the latest `kicad-pcb` codebase in `openclaw_kicad_pcb-master.zip` and the generated schematic in `NE5532_Headphone_Amp_Left_schematic.zip`.

The newest output is a regression from the previous readability pass. The generated schematic has drifted back toward a **column-oriented placement** where many parts line up vertically with the op-amp, and the page composition again looks auto-generated rather than intentionally drafted.

This is not a total return to the original worst-case “pure netlist dump,” because:
- Graphviz routing is still active,
- bus/spine routing is still present,
- power is using symbols instead of repeated `GND` labels in this output,

but the layout has clearly regressed and is no longer using space or block structure well.

---

## What is wrong with the generated schematic

### 1) Too many parts are stacked in the same op-amp column
The output still has multiple X columns overall, but too many important parts are clustered into the same main X column as the op-amp.

Concrete signs from the generated schematic:
- U1 is placed in the same visual column as many resistors/capacitors.
- The result looks like a vertical “pillar” of parts around the op-amp rather than a staged analog circuit.

This is the strongest visible regression.

### 2) Feedback/halo logic is over-constraining placement
The current code appears to force too many related parts into the same column as the op-amp instead of merely keeping them nearby.

That makes the op-amp neighborhood too narrow and tall.

### 3) Functional block separation is still not actually implemented
The code review docs discuss:
- input block
- output block
- power/decoupling block
- op-amp stage block

But the current code does not yet appear to have a real block-detection / block-zoning layer wired into layout. So the layout engine is still mostly working from tiers, connector roles, net structure, and Graphviz constraints alone.

This means the schematic is still missing explicit:
- input-zone anchoring,
- output-zone anchoring,
- power-zone anchoring,
- local grouping around active stages.

### 4) Internal fallback behavior still exists inside the layout logic
Even if the external fallback engine was removed, there are still fallback-style behaviors inside the layout pipeline.

Notable examples:
- `layout.py` still contains SDS/BFS fallback logic when connector-role information is missing or incomplete.
- Graph-level tiering/column assignment can still fall back to simpler behavior if the expected connector role structure is not available.

So while there may no longer be a separate “fallback engine,” there are still fallback **algorithms** inside the pipeline.

### 5) Halo/feedback constraints are likely too aggressive
The code currently has multiple mechanisms that co-locate components near the anchor IC:
- Graphviz halo constraints in `graphviz_layout/dot_builder.py`
- explicit same-column forcing in `layout.py`

These are likely over-applied and are a strong candidate for the “everything lines up with U1” regression.

### 6) Power-only / support-part clustering is still too simplistic
The DOT builder still puts power-only refs into a dedicated cluster/rank. That may be acceptable for pure supply support, but in practice some support parts still need better local placement relative to the signal path and op-amp.

### 7) The routing is not the main problem now
This is an important distinction:
- earlier, routing and label-stub fallback were the dominant issue
- now, the bigger problem is **placement constraints and composition**

The current regression is more about **where parts are placed** than whether nets are drawn at all.

### 8) The schematic still lacks intentional page composition
The page still does not read as:
- input stage on the left,
- op-amp/gain stage in the center,
- output stage on the right,
- power/decoupling clearly above or aside.

Instead, it reads as a graph whose constraints are being satisfied without a true drafting plan.

---

## Code-level findings

### `_sch_apply.py`
This file is no longer missing the `tiers` / `positions` / `use_bus` wiring. That earlier bug appears fixed.

This means the regression is **not** due to simply forgetting to pass routing context into `route_nets()`.

### `router.py`
`route_nets(..., use_bus=True)` is now the default and is being called with tier/position info.

So router fallback is not the primary cause of the column regression.

### `commands/_sch_apply.py::_resolve_layout()`
The command layer only allows `"graphviz"`. So there is no obvious command-level fallback engine path here.

### `layout.py`
This file still contains fallback behavior internally:
- SDS recursive halving when roles exist
- BFS fallback when connector roles are incomplete
- explicit column forcing for halo members

This is a likely contributor to the regression.

### `graphviz_layout/dot_builder.py`
This file still emits:
- rank groupings
- connector rank constraints
- halo constraints
- cluster_power

These constraints may now be over-constraining the layout into narrow columns.

### `graphviz_layout/__init__.py`
The Graphviz engine is still the active engine, but the code comment explicitly says no bundled binary is currently present and discovery falls through to env/PATH. That is not necessarily causing this regression, but it is a mismatch with earlier assumptions about bundled Graphviz.

### Missing implementation relative to prior plan
The code review/TODO docs talk about:
- block detection
- block-level zoning
- density reduction
- readability-aware refinement

Those do not appear to be fully implemented yet, which explains why Graphviz alone is still producing awkward results.

---

## High-confidence likely causes of the regression

### Most likely cause #1
**Halo / same-column forcing around the op-amp is too aggressive.**

This is likely the biggest direct cause of the op-amp-centered column stack.

### Most likely cause #2
**The layout still lacks explicit block-aware zoning.**

Without real block placement, Graphviz plus tiers will still tend to produce compact graph-centric rather than human-centric placement.

### Most likely cause #3
**The SDS/BFS internal fallback logic is still influencing placement.**

Even if there is no external fallback engine, internal fallback column assignment can still produce degraded layouts if connector-role information is incomplete or not strong enough.

---

## What should happen next

The fix should focus first on:
1. reducing/opting down same-column halo forcing,
2. proving whether connector-role/SDS fallback is being hit,
3. adding explicit block zoning before or during Graphviz layout,
4. adding regression tests for “too many parts share the op-amp column.”

The next TODO file is designed around those priorities.
