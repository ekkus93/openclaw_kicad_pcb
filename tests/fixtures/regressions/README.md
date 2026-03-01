# Regression Fixtures — `tests/fixtures/regressions/`

This directory captures **baseline inputs and outputs** for the schematic
layout readability problem described in `code_review/CODE_REVIEW5.md`.
The fixtures serve as:

1. A concrete reference for what the current generator produces (the
   "before" state).
2. The acceptance target against which Phase 4 (Graphviz layout engine)
   improvements will be measured.

---

## Circuit: Simplified Headphone Amplifier

The fixture circuit is a simplified dual-channel passive/resistive headphone
amplifier designed to exercise the common layout problems without requiring
system-installed KiCad libraries.  All component symbols use `TestLib:R`
(available in `tests/fixtures/symbols/TestLib.kicad_sym`) so the fixture is
self-contained and reproducible in any environment.

### Topology (13 components, 9 nets)

```
J1 (AudioIn_L)    ──IN_L──► R1 ──STAGE_L──► R7 ──OUT_L──► J4 (HeadphoneOut_L)
                                   │
                                   R5
                                   │
J2 (AudioIn_R)    ──IN_R──► R2 ──STAGE_R──► R8 ──OUT_R──► J5 (HeadphoneOut_R)
                                   │
                                   R6
                                   │
                             ◄──MID_RAIL──►
                            R3(top)   R4(bottom)
                             │              │
J3 (PowerSupply)  ──VCC──► R3            R4 ──GND
                                           │
                    GND ◄─────────────────┘
                    (also: J1.2, J2.2, J3.2, J4.2, J5.2)
```

**Net degree summary:**

| Net       | Degree | Expected routing (Phase 4 goal)         |
|-----------|--------|-----------------------------------------|
| IN_L      | 2      | Direct wire (J1 → R1)                   |
| IN_R      | 2      | Direct wire (J2 → R2)                   |
| VCC       | 2      | Direct wire (J3 → R3)                   |
| OUT_L     | 2      | Direct wire (J4 → R7)                   |
| OUT_R     | 2      | Direct wire (J5 → R8)                   |
| STAGE_L   | 3      | T-junction / spine wire (R1, R5, R7)    |
| STAGE_R   | 3      | T-junction / spine wire (R2, R6, R8)    |
| MID_RAIL  | 4      | Spine wire with junctions (R3,R4,R5,R6) |
| GND       | 6      | Power symbol on each component          |

---

## Files

### `headphone_amp_ir.json`

The Circuit IR (input) that the generator consumes.  This is the canonical
input for all layout tests; do not modify it unless also updating all related
snapshots.

**Produced by:** Hand-authored; represents a realistic but system-lib-free
dual-channel amp topology.

### `headphone_amp_current_layout.kicad_sch`

The schematic **produced by the current generator** (BFS signal-flow layout +
direct-wire router, as of the commit that introduced this fixture).

**Produced by:**
```
cmd_new_from_netlist(name="HeadphoneAmpBaseline",
                     netlist="headphone_amp_ir.json",
                     symbols_dir="tests/fixtures/symbols/",
                     mode="internal")
```

**Known layout problems in this output (why it is "current bad"):**

1. **Label-stub style for all multi-pin nets** — the router emits a short
   `(stub + label)` for every pin of every net with degree ≥ 3.  In the
   generated schematic:
   - `GND` appears as a net label **6 times** (once per component that shares
     ground) instead of a power symbol or a connected power rail.
   - `MID_RAIL` appears **4 times** as a duplicate net label instead of a
     spine wire with junctions.
   - `STAGE_L` and `STAGE_R` each appear **3 times** as duplicate labels
     instead of T-junction wires.
   - Total: **16 label nodes** across **only 4 distinct nets** — the remaining
     5 nets (2-pin) are wired directly (correct).

2. **No power symbols** — `GND` and `VCC` are plain net labels rather than
   KiCad power symbols (`PWR_FLAG`, `GND`, `VCC`), making the power topology
   invisible.

3. **No junctions** — where three or more wires should meet, the schematic
   has no `(junction …)` nodes.

4. **Layout is grid-sequential, not topology-driven** — components are
   placed in a BFS grid (X: 30.5–91.4 mm, Y: 50.8–152.4 mm) that does not
   visually convey the left-channel / right-channel / power-rail structure
   of the circuit.

**Measured statistics at time of capture:**
- Wire segments: 34 total, 8 routing wires (> stub length)
- Label nodes: 16 (GND ×6, MID_RAIL ×4, STAGE_L ×3, STAGE_R ×3)
- Page usage: 91 mm wide × 152 mm tall on A4 (fits page ✓)

---

## Intended Behavior After Phase 4 (Graphviz Layout)

The target output for `headphone_amp_ir.json` after Phase 4 is completed:

- Graphviz `dot` (rankdir=LR) computes component positions reflecting signal
  flow: connectors at the left/right edges, passive ladders in the centre.
- `GND` and `VCC` are replaced by KiCad power symbols on each pin rather than
  duplicate net labels.
- `MID_RAIL`, `STAGE_L`, `STAGE_R` are drawn as spine wires with explicit
  `(junction …)` nodes where branches meet.
- The schematic reads as a conventional circuit diagram with minimal label
  repetition.

A golden snapshot will be added to `tests/fixtures/golden/` once Phase 4 is
implemented and the Graphviz output is reviewed and accepted.
