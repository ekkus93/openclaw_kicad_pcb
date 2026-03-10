# CODE_REVIEW6.md

## Context

The latest `kicad-pcb` schematic generation output is a substantial improvement over the earlier versions. The schematic is no longer just a vertical “column dump” of components with stub wires and repeated labels. It now resembles a real schematic attempt and is clearly closer to something usable.

However, it still does not look like a polished human-drafted circuit diagram. The remaining problems are primarily about **readability, drafting quality, visual grouping, and page composition**, not about basic electrical correctness.

`CODE_REVIEW6_TODO.md` exists to address those remaining drafting/readability issues.

---

## Summary of What Improved

Compared with the older versions, the following has improved:

- symbols are no longer collapsed into a single narrow vertical list
- there is more left-to-right structure
- routing is more like a circuit and less like a pure netlist dump
- the op-amp is more central and the overall drawing is closer to a conventional schematic
- page usage is better than before

This is important context: the current problem is **not** “the schematic is totally broken.” The current problem is that it still looks **auto-generated rather than designed**.

---

## Main Remaining Problems

### 1) Local crowding is still too high
The upper-left and center-left regions are still too dense. Too many parts and wires compete in a compact area, which makes the schematic hard to read at normal zoom.

### 2) Functional blocks are not visually obvious enough
The schematic should clearly communicate blocks such as:
- input stage
- op-amp gain stage
- output stage
- power entry / decoupling

Those blocks exist electrically, but they are not visually separated enough.

### 3) Power / decoupling is still visually messy
The power connector and nearby capacitors/support parts still look cluttered and do not read as a clean power-distribution block.

### 4) Ground handling is still visually noisy
There are fewer repeated ground labels than before, but there are still too many separate ground symbols. This fragments the drawing visually.

### 5) Signal flow is better, but still not strong enough
A viewer can now somewhat follow the circuit left-to-right, but not as cleanly as they should. The signal path still feels partially obscured by local placement choices and wiring detours.

### 6) Too many short jogs and tiny wire segments remain
The schematic still has a lot of small orthogonal wire fragments that make it feel over-routed and mechanical.

### 7) Some long wires still dominate visually
Certain longer wires still attract too much attention and reduce the “locality” of the drawing.

### 8) The op-amp neighborhood is not yet cleanly composed
The area around the NE5532 is better than before, but feedback parts, input-side parts, output-side parts, and supply-related parts are still not as clearly organized around the op-amp as they should be.

### 9) Output staging is not visually strong enough
The output section exists on the right side, but it does not yet read as a clean output block with a clear chain into the output connector.

### 10) Input staging could be clearer
The input jack and related parts are on the left, but the input stage does not yet read as one coherent, intentionally organized block.

### 11) Page composition is still uneven
Some areas are dense, some are sparse, and the overall page still feels auto-laid-out rather than composed.

### 12) Readability at normal zoom is still weak
A human should be able to understand the circuit quickly at a normal KiCad editing zoom. The current schematic still requires too much visual effort.

### 13) Some component orientations feel routing-driven rather than function-driven
A few symbols appear rotated/oriented mainly to satisfy routing, rather than to help communicate the circuit.

### 14) The overall impression is still “router-driven”
This is the best high-level summary:
the schematic is now valid and improved, but it still looks like the tool optimized mostly for connection and collision avoidance, not for human understanding.

---

## High-Level Direction for the Fix

The next stage of improvement should focus on **schematic drafting quality** rather than just routing correctness.

The main themes are:

- stronger functional block detection and separation
- better whitespace and lower local density
- clearer left-to-right signal flow
- a cleaner op-amp-centered local arrangement
- reduced ground/power clutter
- simpler wires with fewer short jogs
- improved input/output staging
- more balanced page composition
- more consistent component orientation

This implies adding more **block-aware layout heuristics**, **readability metrics**, and **readability lints/tests**.

---

## Relationship to Earlier Work

Earlier work focused on:
- making the S-expression handling correct
- preventing broken files
- improving routing by passing `tiers`, `positions`, and enabling spine/bus routing
- reducing the original “column dump” problem

That earlier work was necessary and successful.

`CODE_REVIEW6_TODO.md` is the next layer on top:
it is about taking a technically valid schematic and making it look significantly more like something a human analog designer would recognize as intentionally drafted.

---

## What `CODE_REVIEW6_TODO.md` Is For

The TODO file is meant for Github Copilot to implement the next readability pass. It provides:

- concrete phases
- tasks and subtasks
- testing expectations
- metric/regression suggestions
- a practical implementation order

The work is intentionally heuristic and iterative. Exact coordinate-perfect golden outputs are less important than achieving clear, measurable improvements in readability and block structure.
