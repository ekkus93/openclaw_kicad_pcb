# `kicad-pcb` Codebase Walkthrough

This document walks through the `kicad-pcb` library end-to-end: what problems it
solves, how the code is organised, and the exact path a Circuit IR JSON takes from
input to a `.kicad_sch` file on disk.

---

## 1. Purpose

`kicad-pcb` is a Python library and CLI that lets an **LLM (or a human) describe
a circuit as a structured JSON netlist** and have it compiled into a valid KiCad
schematic (`.kicad_sch`) automatically.  The main value propositions are:

* The LLM never touches raw S-expressions.  It emits a clean JSON "Circuit IR"
  that the library validates, places, routes, and writes as KiCad files.
* All generated content is owned by the tool: a small ownership marker lets the
  library safely re-generate the schematic from fresh IR without risking hand-
  drawn sections of the file.
* Standard manufacturing outputs (Gerbers, BOM, fab package) are generated with
  a single command once the PCB layout is complete.

---

## 2. The Circuit IR JSON

Everything starts with a **Circuit IR** — a JSON object that describes components
and nets.  This is the only input the LLM (or a programmer) needs to provide.

```json
{
  "version": "1",
  "components": [
    {"ref": "U1", "symbol": "Device:LM358", "value": "LM358",
     "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"},
    {"ref": "R1", "symbol": "Device:R", "value": "10k"},
    {"ref": "J1", "symbol": "Connector:Conn_01x02", "value": "Power"}
  ],
  "nets": [
    {"name": "VCC",  "pins": [{"ref": "J1", "pin": "1"},
                               {"ref": "U1", "pin": "8"}]},
    {"name": "GND",  "pins": [{"ref": "J1", "pin": "2"},
                               {"ref": "U1", "pin": "4"},
                               {"ref": "R1",  "pin": "2"}]},
    {"name": "IN+",  "pins": [{"ref": "U1", "pin": "3"},
                               {"ref": "R1",  "pin": "1"}]}
  ]
}
```

The Python model for this is in `circuit_ir.py`:

```python
class PinRefIR(BaseModel):
    ref: str = Field(min_length=1)
    pin: str = Field(min_length=1)
    unit: str | None = None

class NetIR(BaseModel):
    name: str = Field(min_length=1)
    pins: list[PinRefIR] = Field(min_length=1)
    # GND aliases ("0V", "GROUND", "VSS", …) are normalised to "GND" here:
    @field_validator("name", mode="after")
    @classmethod
    def _normalize_gnd_alias(cls, v: str) -> str:
        return _normalize_gnd_net_name(v)

class ComponentIR(BaseModel):
    ref: str; symbol: str; value: str | None = None
    footprint: str | None = None; fields: dict[str, str] | None = None

class CircuitIR(BaseModel):
    version: str
    components: list[ComponentIR]
    nets: list[NetIR]
    options: OptionsIR | None = None   # tech hint: "THT" / "SMD"
```

All classes use `model_config = ConfigDict(extra="forbid")` — any unknown key is
a validation error immediately, before any file is touched.

---

## 3. Two Main Workflows

### 3.1 `new-from-netlist` — create a project from scratch

```
kicad-pcb new-from-netlist --ir circuit.json --name my_board
```

1. Reads and Pydantic-validates the Circuit IR JSON (`CircuitIR.model_validate`).
2. `validate_circuit_ir()` checks semantic constraints (no duplicate refs, no
   duplicate net names, every pin appears in at most one net per component).
3. `validate_ir_symbols()` queries the KiCad symbol libraries to confirm every
   `symbol` field resolves to an accessible library entry.
4. Creates a new KiCad project directory with a skeleton `.kicad_pro` +
   `.kicad_sch` file pair.
5. Calls `_apply_netlist_to_project()` to write the schematic (see §5).
6. Returns a `NewFromNetlistResult` with the path to the generated project.

### 3.2 `apply-netlist` — regenerate an existing managed schematic

```
kicad-pcb apply-netlist --ir circuit.json
```

Identical validation, then calls `_apply_netlist_to_project()` on the *existing*
project.  Before touching anything the command checks that the schematic contains
the ownership marker (`OpenClaw:generated=v1`); if the marker is absent it refuses
with a user error (unless `--force` is passed).

### 3.3 Other useful commands

| Command | What it does |
|---|---|
| `validate-netlist` | Parse + validate IR, print errors, exit non-zero on failure |
| `fix-netlist` | Run `autofix_circuit_ir()` to repair common IR problems, write result |
| `info-sch` | Print symbol list, pin bindings, and managed-sheet status |
| `lint-sch` | Run all SCH + LAY rules, print issues with codes |
| `validate-sch` | Lint + optional KiCad ERC via kicad-cli |
| `auto-place` | Re-run layout engine on current project schematic |
| `auto-route` | Invoke FreeRouting on the PCB layout |
| `export-gerbers` | Generate Gerber + drill files |
| `export-bom` | Generate CSV bill of materials |
| `package-for-fab` | Zip gerbers + BOM into a fab-ready archive |
| `add-component` | Manually inject one component via AST |
| `add-net` | Manually add a net and wires via AST |
| `connect` | Connect two pin stubs with a wire via AST |
| `drc` / `erc` | Run KiCad's DRC / ERC via kicad-cli |
| `doctor` | Check environment (kicad-cli, graphviz) |
| `search-symbols` | Query the local KiCad symbol library cache |

---

## 4. IR Validation Pipeline

Before any schematic mutation the library runs two passes:

**`ir/validate.py` — `validate_circuit_ir(ir)`**

Checks constraints the Pydantic schema cannot encode:

* No two components share the same `ref`.
* No two nets share the same `name`.
* Each `(ref, pin)` pair appears at most once across all nets.

**`ir/autofix.py` — `autofix_circuit_ir(ir)`**

Called only by `fix-netlist`.  Attempts to repair common LLM mistakes:

* Splits multi-unit IC references that use the same `unit` value.
* Merges duplicate pin entries in the same net.
* Removes nets with zero pins.

---

## 5. Applying the Netlist to a Schematic

`commands/_sch_apply._apply_netlist_to_project()` orchestrates the full write:

1. **Pre-flight checks** (`preflight.py`) — abort before touching any file:
   - `check_no_duplicate_refs()`
   - `check_net_names_valid()`
   - `check_symbol_accessible()` — queries `SymbolIndex`
   - `check_footprints_assigned()` if footprint validation is on

2. **Tier assignment** (`tier.assign_tiers(ir)`) — builds a `{ref: int}` map
   for left-to-right column ordering (§6).

3. **Layout** (`layout_engine.make_layout_engine(...)`) — converts tiers into
   `{ref: (x_mm, y_mm)}` positions (§7).

4. **Symbol writing** (`_write_symbols()`) — for each component, calls
   `SchematicDoc.add_symbol()` with position, orientation, and all fields.

5. **Net writing** — for each net, draws wires and places net labels or power
   symbols via `SchematicDoc.add_wire()` / `add_label()`.

6. **Transactional commit** (`pipeline.mutate_and_validate_sch()`) — writes to
   a temp file, runs the chosen `ValidationMode`, then atomically renames into
   place (§9.4).

---

## 6. Tier Assignment (`tier.py`)

The tier index decides which *column* each component occupies in the schematic
(Tier 0 = left, Tier N-1 = right).  Algorithm in `assign_tiers(ir)`:

**Step 1 — BFS preliminary tiers.**  Find the alphabetically-first connector
(`J*` / `CON*` / `P*`), treat it as source, run BFS over the signal graph.
Components not reachable by BFS default to tier 0.

**Step 2 — Build directed signal DAG.**  Each net edge points from lower BFS
tier to higher BFS tier.  Back-edges (detected by DFS) are removed to guarantee
a DAG.

**Step 3 — Longest-path DP.**  Run Kahn topological sort; for each node compute
the longest path from any tier-0 source.  This is the final tier index.

**Step 4 — Pin input/output connector role detection.**  `classify_connector_roles()`
inspects pin names ("IN", "OUT", "A", "B", etc.) and the net topology to decide
whether a connector is an *input source* (forced tier 0) or an *output sink*
(forced to the last tier).

Power-only components (only connected to `VCC`/`GND`/similar) are assigned tier 0
and later snapped to be near the IC pins they power.

```python
tiers: dict[str, int] = assign_tiers(ir)
# e.g. {"J1": 0, "U1": 1, "R1": 1, "J2": 2}
```

---

## 7. Layout Engines

`layout_engine.make_layout_engine(...)` returns an object implementing the
`LayoutEngine` Protocol:

```python
class LayoutEngine(Protocol):
    def compute_symbol_positions(
        self, ir: CircuitIR
    ) -> dict[str, tuple[float, float, float | None]]: ...
```

**Graphviz is mandatory.**  The factory always returns a `GraphvizLayoutEngine`
and raises `RuntimeError` if the `dot` binary cannot be found:

```python
dot = find_dot_binary()   # checks PATH and GRAPHVIZ_DOT env var
if not dot:
    raise RuntimeError(
        "Graphviz 'dot' binary not found.  "
        "Set the GRAPHVIZ_DOT environment variable or install graphviz, then retry."
    )
return GraphvizLayoutEngine(dot_path=dot, seed=seed, cache_path=cache_path, tiers=tiers)
```

Two engine classes exist:

| Class | Used where |
|---|---|
| `GraphvizLayoutEngine` | Production — the only engine returned by `make_layout_engine()` |
| `NoneLayoutEngine` | Tests only — places every component at a fixed origin point |

If `dot` is missing the command fails fast with a clear error rather than
silently producing a lower-quality layout.

---

## 8. The Graphviz Layout Pipeline

`graphviz_layout/` is a four-file package.

### 8.1 DOT graph construction (`dot_builder.py`)

`_build_dot_source(ir, tiers, connector_roles, affinity_order)` builds a Graphviz DOT description:

* Each IC or connector is a node.
* Signal nets become edges.
* Components in the same tier share a `{ rank=same }` subgraph.
* Input connectors use `rank=source`; output connectors use `rank=sink`.
* Decoupling capacitors get invisible edges to the IC they serve so that `dot`
  positions them nearby.
* Feedback paths get their own cluster so the backwards arrow is rendered
  without crossing the forward-signal spine.
* **Affinity ordering:** before calling `_build_dot_source()`, `__init__.py`
  calls `compute_affinity_groups(ir, tiers)` from `layout.py`.  The resulting
  `dict[int, list[str]]` (tier → refs sorted by coupling strength to the
  previous tier) is passed as `affinity_order` and forwarded to
  `_emit_tier_subgraphs()`, which emits nodes within each `{rank=same}` block
  in affinity order instead of alphabetical order.  This gives `dot` a better
  starting point and reduces crossings before any snap pass runs.

### 8.2 Calling `dot` (`graphviz_layout/__init__.py`)

```
dot -Tplain -Gstart=<seed> <input.dot>
```

Output is the "plain" text format: one `node <name> <x> <y> …` line per component.
The library parses that into floating-point `(x, y)` coordinates and scales from
Graphviz inches to KiCad millimetres.

The call is wrapped in `cache.py` — a SHA256 keyed cache keyed on the DOT source
so repeated `apply-netlist` runs with an unchanged IR skip the `dot` subprocess.

### 8.3 Snap passes (`snap.py`)

After mapping raw DOT coordinates to the KiCad grid the library applies a
sequence of deterministic snap passes:

1. `snap_positions()` — align every component to the `30.48 mm × 20.32 mm` grid.
2. `_snap_power_symbols()` — move `VCC` / `GND` power flags adjacent to the pin
   they label.
3. `_enforce_connector_x_bounds()` — clamp input/output connectors to the
   leftmost/rightmost column so they never appear inside the signal spine
   (conditional on `roles` being available).
4. `_snap_connectors_to_ic_y()` — vertically align connectors to the IC row they
   connect to.
5. `_snap_feedback_components()` — pull feedback-path components below the main
   signal row so back-arrows are unambiguous.
6. `_snap_opamp_halo()` — pull halo members (bypass caps, bias resistors) back to
   their anchor IC after any prior pass has moved the IC (conditional on `halo`
   being provided).
7. `_apply_stereo_split()` — if two signal channels are detected as stereo
   (identical topology, different net suffixes), place them on separate rows.
8. `_post_stereo_barycentric()` — within each row, re-order by barycentric weight
   to minimise wire crossings.
9. `_compact_y_gap()` — collapse empty rows.
10. `_center_ics_in_columns()` — re-sort each column so ICs land at the vertical
    midpoint with passive components above and below (interleaved
    `plain_other[:mid] + halo_other[:mid] + ic_refs + halo_other[mid:] +
    plain_other[mid:]`).  Always runs; no-op when no ICs are present.
11. `_post_snap_decoupling_caps()` — move decoupling caps immediately below the
    IC pin they decouple (runs after IC centering so it sees the ICs' final
    centred y-values).
12. `_deoverlap_positions()` — push apart any components that still share a grid
    cell.
13. `_remediate_crossings()` — measure the signal-wire crossing ratio; if it
    meets or exceeds the threshold (default 0.30), apply up to
    `max_sweeps` iterations of `barycentric_sort` to reduce crossings, then
    re-runs `_deoverlap_positions()` internally.  Always runs as the final pass.

### 8.4 Orientation

`compute_orientations(positions, ir, tiers, roles)` returns `{ref: rotation_degrees}`
using only **rotation** (never mirroring — KiCad does not support mirrored pins
for complex symbols like ICs).  Rules, in priority order:

* **Connectors** (J/CON/P/…): `"output"` role or last tier → 180° (pins face
  left toward the circuit); `"input"` / first tier → 0°.
* **Op-amps / ICs** (U/IC/OA): always **0°** — the standard KiCad orientation
  with inputs on the left and output on the right.
* **Shunt passives** (R/C/L with one power-net pin and one signal-net pin):
  90° so the component visually straddles the rail.
* **Series passives** (all signal-net pins): 90° when `|Δy|` to neighbours
  exceeds `|Δx|`; otherwise 0°.
* **Diodes and everything else**: 0°.

---

## 9. Writing Schematics (`sch_doc/` and `sexpr/`)

### 9.1 The S-expression layer (`sexpr/`)

KiCad uses S-expressions for all its file formats.  The `sexpr/` package provides
a minimal, self-contained pipeline:

```
raw .kicad_sch text
    → sexpr.tokenize()  → list of tokens
    → sexpr.parse()     → tree of AtomNode / StringNode / ListNode
    → (mutations)
    → sexpr.serialize() → canonical .kicad_sch text
```

The serializer always produces deterministic output: comments are dropped,
key order is normalised.  This is intentional — it means diffs between
`apply-netlist` runs are meaningful (only real content changes, not formatting).

### 9.2 SchematicDoc (`sch_doc/__init__.py`)

`SchematicDoc` wraps the parsed S-expression tree and exposes write methods:

```python
doc = SchematicDoc.from_file(path)
doc.add_symbol("Device:R", ref="R1", value="10k", footprint="",
               x=60.96, y=50.80, sym_uuid=new_uuid(),
               pin_nums=["1","2"], pin_uuids=[new_uuid(), new_uuid()],
               project_name="my_board", rotation=0)
doc.add_wire(x1=50.80, y1=50.80, x2=60.96, y2=50.80, wire_uuid=new_uuid())
doc.add_label("GND", x=60.96, y=71.12, label_uuid=new_uuid())
doc.add_junction(x=60.96, y=50.80, junction_uuid=new_uuid())
```

Internally each `add_*` call constructs an S-expression node via the helpers in
`sch_doc/nodes.py` (`make_symbol_node`, `make_wire_node`, `make_label_node`, …)
and appends it to the appropriate position in the AST.  Nothing is written to
disk until the caller invokes the pipeline.

### 9.3 Ownership model

When `new-from-netlist` creates a schematic it writes a small off-canvas text
item:

```
OpenClaw:generated=v1
```

`apply-netlist` looks for this marker before touching the file.  If the marker
is absent the command refuses to mutate the file — protecting hand-drawn content
the user created outside the tool.

All generated symbols are written into a sub-sheet named
`OpenClaw_Managed.kicad_sch` that is linked from the top-level schematic.
This keeps the managed content isolated so a user can safely add hand-drawn
content in the parent schematic without it being overwritten on the next
`apply-netlist` run.

### 9.4 Transactional writes (`pipeline.py`)

All schematic mutations go through `mutate_and_validate_sch()`:

```python
from kicad_pcb.pipeline import ValidationMode, mutate_and_validate_sch
from kicad_pcb.sch_doc import SchematicDoc

def _mutate(doc: SchematicDoc) -> None:
    doc.add_label("VCC", 60.0, 50.0, new_uuid())

mutate_and_validate_sch(
    Path("project.kicad_sch"),
    _mutate,
    operation="add-net",         # shown in error messages
    mode=ValidationMode.LINT,    # default
)
```

The pipeline:

1. Calls `_mutate(doc)` on a deep copy of the in-memory AST.
2. Serialises to a temp file next to the original.
3. Runs the chosen `ValidationMode`:
   - `NONE` — skip all checks (bare atomic write).
   - `SYNTAX` — round-trip parse + root-node type assertion.
   - `LINT` — `SYNTAX` + error-level SCH / LAY rules.
   - `KICAD` — `LINT` + KiCad CLI ERC.
   - `FULL` — `KICAD` + lint warnings promoted to errors.
4. On success: `os.replace(tmp, original)` — atomic on POSIX.
5. On failure: temp file is deleted, original is never changed.

---

## 10. Lint Rules

The `lint/` package defines three rule domains.  Rules are referenced by code in
error messages and can be suppressed per-project with a config file.

### SCH rules — structural correctness

| Code | Fires when |
|---|---|
| SCH001 | File is not a `.kicad_sch` (wrong root token) |
| SCH002 | Duplicate UUIDs found (KiCad did not properly generate them) |
| SCH003 | Duplicate reference designators in one schematic |
| SCH004 | A symbol is missing the required `Reference` property |
| SCH005 | A symbol is missing the required `Value` property |
| SCH006 | `(at x y)` placement coordinates are not valid numbers |
| SCH007 | A wire `(pts …)` node has non-numeric coordinates |
| SCH008 | A symbol's library definition is not embedded in the file |
| SCH009 | A `lib_id` does not resolve to any installed library |
| SCH010 | A global label pin has no matching label elsewhere in the project |

### LAY rules — readability

| Code | Severity | Fires when |
|---|---|---|
| LAY001 | WARNING | Same net label placed more than 3 times (prefer wires) |
| LAY002 | WARNING | Short stub wire + label that could be a direct wire connection |
| LAY003 | ERROR | Two symbols share the same grid cell (overlapping placement) |
| LAY004 | WARNING | A symbol is outside the A4 printable area (0–297 × 0–210 mm) |
| LAY005 | WARNING | Wire island — a group of wires has no net label and no pin |
| LAY007 | WARNING | Signal-wire crossing ratio exceeds 50 % |

### PCB rules — layout file correctness

| Code | Fires when |
|---|---|
| PCB001 | File is not a `.kicad_pcb` |
| PCB002 | Duplicate UUIDs in PCB file |
| PCB003–004 | Footprint missing or invalid `(at x y)` placement |
| PCB005–007 | Board outline (`Edge.Cuts`) absent, not closed, or out-of-range |
| PCB008–011 | Miscellaneous PCB structural checks |

---

## 11. Manufacturing Export Pipeline

Once the PCB layout is complete:

```bash
kicad-pcb export-gerbers          # → gerbers/  (one file per copper layer + edge cuts + drill)
kicad-pcb export-bom              # → bom.csv
kicad-pcb package-for-fab         # → <board>_fab.zip (gerbers + bom)
```

`export-gerbers` and `export-bom` call `KicadCliAdapter` which shells out to
`kicad-cli`:

```
kicad-cli pcb export gerbers  --output gerbers/  board.kicad_pcb
kicad-cli sch export bom      --output bom.csv   board.kicad_sch
```

`package-for-fab` creates a zip of the gerbers directory plus `bom.csv` for
direct submission to a PCB fabrication house.

---

## 12. Module Map

```
src/kicad_pcb/
├── cli.py                 argparse entry point; dispatches all 40+ commands
├── circuit_ir.py          Pydantic v2 IR schema (CircuitIR, ComponentIR, NetIR …)
├── component_types.py     Ref-prefix constants, GND aliases, power-net helpers
├── tier.py                DAG longest-path tier assignment
├── layout.py              Shared layout utilities (SDS, barycentric sort, orientations, snap helpers)
├── layout_engine.py       LayoutEngine Protocol + make_layout_engine() factory
├── graphviz_layout/
│   ├── __init__.py        GraphvizLayoutEngine; runs dot subprocess
│   ├── dot_builder.py     Builds DOT source from CircuitIR + tiers
│   ├── snap.py            Post-layout snap/align passes for KiCad grid
│   └── cache.py           SHA256-keyed cache for dot output
├── sch_doc/
│   ├── __init__.py        SchematicDoc AST wrapper; add_symbol, add_wire, …
│   └── nodes.py           make_symbol_node, make_wire_node, … factory functions
├── sexpr/
│   ├── __init__.py        parse(), serialize(), find_first(), find_all(), walk()
│   └── (tokenizer, etc.)
├── pipeline.py            mutate_and_validate_sch(); ValidationMode enum
├── preflight.py           Pre-mutation semantic checks
├── ir/
│   ├── validate.py        validate_circuit_ir(); duplicate ref/net/pin checks
│   └── autofix.py         autofix_circuit_ir(); repair common LLM mistakes
├── lint/
│   ├── defs.py            Rule codes + fix hint strings for all three domains
│   ├── sch.py             SCH001-010 + LAY001-007 implementations
│   ├── pcb.py             PCB001-011 implementations
│   └── helpers.py         Shared lint helpers
├── commands/
│   ├── netlist.py         new-from-netlist, apply-netlist, validate-netlist, …
│   └── (one file per command group)
├── adapters.py            KicadCliAdapter — shells out to kicad-cli
├── pcb_doc.py             PcbDoc AST wrapper for .kicad_pcb files
├── router.py              auto-route via FreeRouting
├── symbol_index.py        KiCad symbol library search
├── symbol_cache.py        Persistent cache for symbol lookups
├── patterns.py            Circuit pattern library (resistor divider, LED, …)
├── config.py              Project / session state (current project path, etc.)
├── models.py              Dataclasses for complex return types
└── results.py             CLI result types (NewFromNetlistResult, etc.)
```

---

## 13. End-to-End Example

```bash
# 1. Write a Circuit IR to a file
cat > circuit.json << 'EOF'
{
  "version": "1",
  "components": [
    {"ref": "U1", "symbol": "Device:LM358", "value": "LM358",
     "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"},
    {"ref": "R1", "symbol": "Device:R",     "value": "10k"},
    {"ref": "J1", "symbol": "Connector:Conn_01x02", "value": "Power"}
  ],
  "nets": [
    {"name": "VCC", "pins": [{"ref":"J1","pin":"1"},{"ref":"U1","pin":"8"}]},
    {"name": "GND", "pins": [{"ref":"J1","pin":"2"},{"ref":"U1","pin":"4"},
                              {"ref":"R1","pin":"2"}]},
    {"name": "IN+", "pins": [{"ref":"U1","pin":"3"},{"ref":"R1","pin":"1"}]}
  ]
}
EOF

# 2. Validate the IR before creating anything
kicad-pcb validate-netlist --ir circuit.json

# 3. Create a new project
kicad-pcb new-from-netlist --ir circuit.json --name my_board
cd my_board

# 4. Check the schematic looks right
kicad-pcb lint-sch
kicad-pcb info-sch

# 5. Update the schematic from a revised IR (safe — only touches managed sheet)
kicad-pcb apply-netlist --ir circuit_v2.json

# 6. Auto-route the PCB after manual component placement
kicad-pcb auto-route

# 7. Export for fabrication
kicad-pcb export-gerbers
kicad-pcb export-bom
kicad-pcb package-for-fab
```
