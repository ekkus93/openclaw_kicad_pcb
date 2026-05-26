# SPEC: Model KiCad Corpus and Evaluation Harness

## Purpose

Set up `openclaw_kicad_pcb` so adding real KiCad schematic files is easy and repeatable:

```text
model_kicad_files/*.kicad_sch
    ↓
corpus ingestion
    ↓
tests/fixtures/model_corpus/<fixture_id>/
    source.kicad_sch
    metadata.json
    source_layout_features.json
    source_netlist.kicadxml        optional, requires kicad-cli
    circuit_ir.json                optional until XML export/import succeeds
    source_embedded_symbols.kicad_sym optional when needed
    ↓
model-corpus evaluation
    ↓
code_review/generated/model_eval/<fixture_id>/
    generated_project/
    generated.kicad_sch or OpenClaw_Managed.kicad_sch
    generated_layout_features.json
    evaluation_report.json
    actionable_failures.md
```

The goal is not ML training. The goal is a deterministic fixture/evaluation loop that lets GitHub Copilot refine the generic netlist-JSON-to-KiCad-schematic generator using real schematic examples.

Copilot should improve reusable rules in the generator. It must not special-case individual fixture filenames, individual source URLs, or one-off reference designators unless the rule is explicitly generalized and tested.

---

## Current repository context

The repo already has these important pieces:

| Concern | Existing files |
|---|---|
| Circuit IR schema | `src/kicad_pcb/circuit_ir.py` |
| Netlist validation | `src/kicad_pcb/ir/validate.py`, `src/kicad_pcb/commands/netlist.py` |
| Main netlist-to-schematic generator | `src/kicad_pcb/commands/_sch_apply.py` |
| KiCad CLI adapter | `src/kicad_pcb/adapters.py` |
| S-expression parser/serializer | `src/kicad_pcb/sexpr/` |
| Schematic AST wrapper | `src/kicad_pcb/sch_doc/` |
| Symbol lookup | `src/kicad_pcb/symbol_index.py`, `src/kicad_pcb/lib_symbol.py` |
| Graphviz placement | `src/kicad_pcb/graphviz_layout/` |
| Block/role heuristics | `src/kicad_pcb/block_detection.py` |
| Routing | `src/kicad_pcb/router.py` |
| Schematic metrics | `src/kicad_pcb/schematic_metrics.py` |
| Schematic lints | `src/kicad_pcb/lint/sch.py` |
| Existing readability fixtures | `tests/fixtures/readability/` |

The new `model_kicad_files/` directory contains real KiCad schematics. In a local inspection, all current files parsed with `SchematicDoc.load(...)`. The files vary from small, good first fixtures to large hard cases:

| Fixture source file | Rough role |
|---|---|
| `mcp2551-can-transciever.kicad_sch` | small CAN interface fixture |
| `dual-ttl-uart-to-rs232-max232-reference-design.kicad_sch` | small UART/RS232 interface fixture |
| `microsd-card-in-spi-mode-with-hotswap-support.kicad_sch` | SPI/card connector fixture |
| `tpic8101dwrg4-tpic8101dwrg4.kicad_sch` | medium IC support circuit fixture |
| `4-channel-switched-constant-current-source.kicad_sch` | repeated-channel/current-source fixture |
| `ws2812c-addressable-led-chain-cleverhand-hardware.kicad_sch` | LED chain/repeated component fixture |
| `li-ion-battery-charger-circuit-gems-electronics.kicad_sch` | larger charger/power fixture with custom symbols |
| `st7789-tft-lcd-interface-neuralnetwork-based-autonomouscar.kicad_sch` | larger display/interface fixture |
| `5v-linear-voltage-regulator-eurorack.kicad_sch` | large hard-case power/audio-ish fixture |

Known current limitation: `schematic_metrics.count_power_symbols()` currently detects `(power yes)` directly on placed symbol instances and reports zero for real source schematics that use placed symbols such as `power:GND`, `power:+5V`, etc. The metric should be fixed before using it as a source-schematic quality signal.

---

## Definitions

### `model_kicad_files/`

Raw input directory for real source schematics. Developers should be able to drop `.kicad_sch` files here and run a corpus command.

Rules:

- Do not mutate files in `model_kicad_files/`.
- Do not rely on these files being already curated.
- Treat them as raw inputs.
- The ingestion command decides whether a file becomes an accepted fixture, a partial fixture, or a rejected fixture.

### `model_corpus/`

Normalized fixture directory generated from `model_kicad_files/`.

Default path:

```text
tests/fixtures/model_corpus/
```

Each fixture gets a stable slug directory:

```text
tests/fixtures/model_corpus/mcp2551_can_transciever/
```

### Source layout features

A normalized JSON feature extraction from the source schematic. This is used as a style/quality reference.

It is not an exact coordinate answer key.

### Electrical equivalence

Hard pass/fail check that the generated schematic exports to the same canonical electrical netlist as the source schematic.

Electrical equivalence must be checked before quality scoring. A pretty schematic with wrong connectivity is a failure.

### Schematic quality similarity

A soft score comparing generated schematic features against source schematic features and general readability rules.

This should compare relationships and conventions, not exact coordinates.

---

## Required developer workflow

The desired workflow after implementation is:

```bash
# 1. Add real source files.
cp ~/Downloads/some-real-project.kicad_sch model_kicad_files/

# 2. Ingest raw source files into normalized fixtures.
uv run python -m kicad_pcb.cli model-corpus ingest \
  --source-dir model_kicad_files \
  --out-dir tests/fixtures/model_corpus

# 3. Generate/evaluate all fixtures that have circuit_ir.json.
uv run python -m kicad_pcb.cli model-corpus evaluate \
  --corpus-dir tests/fixtures/model_corpus \
  --out-dir code_review/generated/model_eval

# 4. Read actionable failures.
less code_review/generated/model_eval/summary.md
less code_review/generated/model_eval/mcp2551_can_transciever/actionable_failures.md

# 5. Improve generic generator rules.
# 6. Re-run evaluation and tests.
uv run pytest
```

Optional when `kicad-cli` is available:

```bash
uv run pytest -m requires_kicad
```

The default unit test suite must not require KiCad CLI.

---

## New package structure

Add these modules:

```text
src/kicad_pcb/corpus/
    __init__.py
    metadata.py
    ingestion.py
    layout_features.py
    kicadxml.py
    embedded_symbols.py
    reports.py

src/kicad_pcb/evaluation/
    __init__.py
    electrical.py
    scoring.py
    similarity.py
    reports.py

src/kicad_pcb/commands/model_corpus.py
```

Keep concerns separate:

- `corpus/` handles source-file ingestion and feature extraction.
- `evaluation/` handles electrical equivalence, quality scoring, and source/generated comparison.
- `commands/model_corpus.py` handles CLI command plumbing only.
- Existing generator code remains under `_sch_apply.py`, `graphviz_layout/`, `block_detection.py`, and `router.py`.

Do not put corpus/evaluation logic into `_sch_apply.py`.

---

## New CLI interface

Add a command group named `model-corpus` with subcommands.

### `model-corpus ingest`

```bash
uv run python -m kicad_pcb.cli model-corpus ingest \
  --source-dir model_kicad_files \
  --out-dir tests/fixtures/model_corpus \
  [--refresh] \
  [--require-kicad]
```

Behavior:

1. Scan `--source-dir` recursively for `.kicad_sch` files.
2. Parse each file using `SchematicDoc.load(...)`.
3. Create a stable fixture slug from filename stem.
4. Copy the source file into the fixture directory as `source.kicad_sch`.
5. Write `metadata.json`.
6. Write `source_layout_features.json`.
7. Extract embedded symbol definitions where present.
8. If `kicad-cli` is available, export `source_netlist.kicadxml`.
9. If `source_netlist.kicadxml` exists or was just exported, parse it and write `circuit_ir.json`.
10. If KiCad CLI is unavailable, still write a partial fixture with status `layout_only` or `pending_netlist_export`.
11. Write `ingestion_report.json` and `summary.md`.

`--require-kicad` changes missing KiCad CLI from a warning into a command failure.

`--refresh` allows overwriting generated fixture artifacts. Without `--refresh`, ingestion must avoid silently overwriting an existing curated fixture.

### `model-corpus evaluate`

```bash
uv run python -m kicad_pcb.cli model-corpus evaluate \
  --corpus-dir tests/fixtures/model_corpus \
  --out-dir code_review/generated/model_eval \
  [--fixture mcp2551_can_transciever] \
  [--require-kicad]
```

Behavior:

1. Load each fixture with a `circuit_ir.json`.
2. Generate a new schematic using the existing netlist-to-schematic path.
3. Extract `generated_layout_features.json`.
4. If KiCad CLI is available, export generated XML netlist and run electrical equivalence.
5. If KiCad CLI is unavailable, produce a partial evaluation and mark electrical equivalence as `not_run`.
6. Compute intrinsic generated-schematic quality.
7. Compute source/generated schematic similarity.
8. Write `evaluation_report.json` per fixture.
9. Write `actionable_failures.md` per fixture.
10. Write an aggregate `summary.json` and `summary.md`.

### `model-corpus list`

```bash
uv run python -m kicad_pcb.cli model-corpus list \
  --corpus-dir tests/fixtures/model_corpus
```

Behavior:

- Print fixture IDs, status, source filename, symbol count, wire count, label count, and whether `circuit_ir.json` exists.
- With `--json`, return structured output.

---

## Fixture directory contract

Each fixture directory should follow this contract:

```text
tests/fixtures/model_corpus/<fixture_id>/
    source.kicad_sch
    metadata.json
    source_layout_features.json
    source_netlist.kicadxml          optional
    circuit_ir.json                  optional until KiCad netlist import succeeds
    source_embedded_symbols.kicad_sym optional
    README.md                        optional, generated or hand-edited
```

### `metadata.json`

Required shape:

```json
{
  "fixture_id": "mcp2551_can_transciever",
  "source_file_name": "mcp2551-can-transciever.kicad_sch",
  "source_path": "model_kicad_files/mcp2551-can-transciever.kicad_sch",
  "status": "ready",
  "status_reasons": [],
  "created_by": "model-corpus ingest",
  "kicad_schematic_version": "20211123",
  "generator": "eeschema",
  "title": "",
  "license": "unknown",
  "source_url": null,
  "symbol_count": 8,
  "wire_count": 25,
  "label_count": 0,
  "global_label_count": 4,
  "power_symbol_count": 3,
  "has_embedded_symbols": true,
  "has_circuit_ir": true,
  "requires_custom_symbols": false,
  "notes": []
}
```

Allowed status values:

```text
ready                 source parsed, features extracted, circuit_ir.json exists
layout_only           source parsed/features extracted, no electrical IR yet
pending_netlist_export source parsed, needs kicad-cli to export XML netlist
rejected              source cannot be parsed or is unsuitable
```

### `source_layout_features.json`

Required top-level shape:

```json
{
  "schema_version": "1.0",
  "source": {
    "fixture_id": "mcp2551_can_transciever",
    "file": "source.kicad_sch"
  },
  "counts": {
    "symbols": 8,
    "non_power_symbols": 5,
    "power_symbols": 3,
    "wires": 25,
    "labels": 0,
    "global_labels": 4,
    "junctions": 0,
    "no_connects": 0
  },
  "symbols": {
    "U1": {
      "ref": "U1",
      "symbol_id": "Interface_CAN_LIN:MCP2551-I-SN",
      "value": "MCP2551-I-SN",
      "x": 101.6,
      "y": 63.5,
      "rotation": 0,
      "unit": "1",
      "role_guess": "interface_ic",
      "is_power_symbol": false,
      "is_connector": false,
      "is_passive": false
    }
  },
  "role_counts": {
    "interface_ic": 1,
    "passive": 4,
    "power_symbol": 3
  },
  "net_label_strategy": {
    "local_label_count": 0,
    "global_label_count": 4,
    "power_symbol_count": 3
  },
  "geometry": {
    "min_x": 38.1,
    "max_x": 177.8,
    "min_y": 38.1,
    "max_y": 101.6,
    "distinct_x_columns": 5,
    "average_symbol_spacing_mm": 15.53,
    "wire_stub_ratio": 0.52
  },
  "relative_positions": [
    {
      "a": "J1",
      "b": "U1",
      "relation": "left_of"
    }
  ],
  "intrinsic_lints": []
}
```

Notes:

- Exact coordinates may be recorded but must not be used as exact answer keys.
- Similarity scoring should compare derived relationships, roles, and strategies.
- `relative_positions` should initially include only high-confidence relationships among non-power symbols.

---

## KiCad XML netlist import

Add `src/kicad_pcb/corpus/kicadxml.py`.

Required public functions:

```python
from pathlib import Path
from kicad_pcb.circuit_ir import CircuitIR


def parse_kicadxml_netlist(path: Path) -> KicadXmlNetlist:
    """Parse KiCad XML netlist into typed internal dataclasses."""


def kicadxml_to_circuit_ir(netlist: KicadXmlNetlist) -> CircuitIR:
    """Convert parsed KiCad XML netlist into CircuitIR."""


def canonicalize_circuit_ir(ir: CircuitIR) -> CircuitIR:
    """Return deterministically sorted/normalized CircuitIR."""
```

Use only Python stdlib XML parsing unless a dependency already exists. Do not add new dependencies for this parser.

Minimum KiCad XML fields to preserve:

- component ref
- component value
- component library/source symbol where present
- footprint where present
- nets
- pin numbers attached to each net

Limitations allowed in V0:

- Hierarchical sheet names may be flattened if the exported XML is already flattened.
- Fields not represented in `CircuitIR` may be stored in `ComponentIR.fields` if useful.
- Unknown library IDs may be preserved exactly as exported.

Do not infer connectivity from wires in V0. Use KiCad XML export as the canonical electrical source.

---

## Embedded symbol support

Real source schematics often contain embedded symbols or use custom libraries such as:

```text
SamacSys_Parts:...
GEMS_Library:...
00_custom:...
```

The existing `SymbolIndex` searches repo-local and system `.kicad_sym` directories. It should gain an optional embedded-symbol source for corpus fixtures.

Add `src/kicad_pcb/corpus/embedded_symbols.py`.

Required public functions:

```python
from kicad_pcb.sexpr.nodes import ListNode
from kicad_pcb.sch_doc import SchematicDoc


def extract_embedded_symbol_defs(doc: SchematicDoc) -> dict[str, ListNode]:
    """Return embedded lib symbol definitions keyed by full Lib:Symbol id when possible."""


def write_embedded_symbol_library(symbols: dict[str, ListNode], output_file: Path) -> None:
    """Write extracted symbols to a fixture-local .kicad_sym-style file if feasible."""
```

Then extend `SymbolIndex` with one of these approaches:

Preferred:

```python
SymbolIndex(symbols_dir=..., fallback_dirs=..., embedded_symbols=...)
```

Acceptable V0 alternative:

- materialize embedded symbols as fixture-local `.kicad_sym` files
- pass that directory as `symbols_dir` or `fallback_dirs`

The goal is that a fixture with custom symbols can still validate and generate without requiring the developer to install unknown third-party symbol libraries.

---

## Layout feature extraction

Add `src/kicad_pcb/corpus/layout_features.py`.

Required public functions:

```python
from pathlib import Path
from kicad_pcb.sch_doc import SchematicDoc


def extract_layout_features(doc: SchematicDoc, *, fixture_id: str, source_file: str) -> LayoutFeatures:
    """Extract normalized schematic layout/style/readability features."""


def write_layout_features(features: LayoutFeatures, path: Path) -> None:
    """Write stable JSON with sorted keys and deterministic ordering."""
```

Use typed dataclasses or Pydantic v2 models. Since the repo already uses Pydantic v2 for `CircuitIR`, Pydantic models are acceptable.

Feature extraction requirements:

1. Count direct root nodes: symbols, wires, labels, global labels, junctions, no-connect markers.
2. Identify power symbols robustly:
   - reference starts with `#PWR`, or
   - symbol id starts with `power:`, or
   - direct child `(in_bom no)` and `(on_board no)`, or
   - embedded symbol definition has a power marker.
3. Identify connectors by symbol id prefix/name and reference prefix when possible.
4. Identify passives by ref prefix and/or symbol id:
   - `R`, `C`, `L`, `D`, `Q`, etc.
5. Identify major ICs by ref prefix `U` and non-power symbol IDs.
6. Extract x/y/rotation/unit/value/symbol_id per placed symbol.
7. Compute geometry summary:
   - min/max x/y
   - distinct x columns
   - average symbol spacing
   - wire stub ratio
8. Compute simple relative-position relationships among non-power symbols:
   - `left_of`
   - `right_of`
   - `above`
   - `below`
   - `near`
9. Run existing `lint_schematic_layout` cautiously. Source schematics may trigger false positives due approximate bounding boxes. Report source lints as features, not hard failures.

Update `schematic_metrics.count_power_symbols()` so it works on real source schematics, not only generated symbols.

---

## Evaluation behavior

Add `src/kicad_pcb/evaluation/`.

### Electrical equivalence

`evaluation/electrical.py` should compare canonical `CircuitIR` objects.

Required public function:

```python
from kicad_pcb.circuit_ir import CircuitIR


def compare_circuit_ir_equivalence(source: CircuitIR, generated: CircuitIR) -> ElectricalEquivalenceReport:
    """Return hard pass/fail electrical equivalence report."""
```

Comparison rules:

- Sort components by ref.
- Sort nets by name.
- Sort pins in each net by `(ref, pin, unit or "")`.
- Normalize known ground aliases through existing ground normalization.
- Compare component refs.
- Compare component values where present.
- Compare symbol IDs where present.
- Compare pin-to-net assignments.

If electrical equivalence fails, total score must be zero or the result must be hard failure, depending on command mode.

### Intrinsic quality scoring

`evaluation/scoring.py` should score generated schematic quality independent of source.

Initial categories:

| Category | Meaning |
|---|---|
| validity | generated schematic parses and has expected components |
| overlap | no severe symbol/text overlaps |
| page bounds | symbols are inside page bounds |
| routing simplicity | reasonable wire count/stub ratio |
| label strategy | high-fanout and power nets are not turned into spaghetti |
| spread | enough x-columns / not collapsed into one vertical stack |
| power symbols | power symbols detected correctly |

### Source similarity scoring

`evaluation/similarity.py` should compare `source_layout_features.json` and `generated_layout_features.json`.

Compare:

- role counts approximately match
- major-symbol relative order approximately matches
- connector edge/side placement approximately matches
- local/global/power label strategy is similar
- generated schematic has similar or better wire-stub ratio
- generated schematic uses similar or better x-column spread
- generated schematic preserves repeated-channel organization where detectable

Do not compare exact symbol coordinates.

### Evaluation report

`evaluation/reports.py` should write JSON and Markdown.

`evaluation_report.json` shape:

```json
{
  "schema_version": "1.0",
  "fixture_id": "mcp2551_can_transciever",
  "result": "pass_with_warnings",
  "total_score": 78.5,
  "electrical_equivalence": {
    "status": "passed",
    "mismatches": []
  },
  "scores": {
    "intrinsic_quality": 82.0,
    "source_similarity": 70.0,
    "validity": 100.0
  },
  "generated_artifacts": {
    "project_dir": "...",
    "schematic_path": "...",
    "layout_features": "generated_layout_features.json"
  },
  "actionable_failures": [
    {
      "rule": "connector_edge_placement",
      "severity": "medium",
      "message": "CAN/bus connector appears central instead of to the right of the interface IC.",
      "suggested_files": [
        "src/kicad_pcb/block_detection.py",
        "src/kicad_pcb/graphviz_layout/dot_builder.py"
      ]
    }
  ]
}
```

`actionable_failures.md` should be written for Copilot. It must contain:

- fixture ID
- source file name
- pass/fail state
- score summary
- concrete generator rule failures
- likely files to edit
- explicit instruction: do not special-case this fixture

---

## How Copilot should refine the generator

Copilot should use evaluation reports this way:

1. Pick one fixture with a clear failure.
2. Read `source_layout_features.json` and `evaluation_report.json`.
3. Identify a generic generator rule.
4. Modify generator code in the correct area:
   - role classification: `block_detection.py`
   - layout constraints: `graphviz_layout/dot_builder.py`, `graphviz_layout/snap.py`
   - routing behavior: `router.py`
   - symbol/pin handling: `symbol_index.py`, `lib_symbol.py`, `_sch_apply.py`
   - metrics/evaluator: `schematic_metrics.py`, `corpus/layout_features.py`, `evaluation/`
5. Add or update unit tests.
6. Re-run model-corpus evaluation.
7. Ensure existing readability/golden tests still pass.

Bad instruction:

```text
Make mcp2551-can-transciever look like the source file.
```

Good instruction:

```text
For small transceiver/interface circuits, classify the interface IC as the central block, place MCU/logic-side signals to the left, bus/field-side connector signals to the right, and keep termination/protection/support parts near the side they serve. Use the MCP2551 fixture as regression coverage. Do not special-case the MCP2551 filename, U1, or specific source coordinates.
```

---

## Acceptance criteria

### Ingestion acceptance

- `model-corpus ingest` parses every current file under `model_kicad_files/`.
- It creates one fixture directory per parseable source file.
- It writes deterministic `metadata.json` and `source_layout_features.json` for every parseable source file.
- It does not mutate raw source files.
- It works without `kicad-cli`, creating partial fixtures when necessary.
- With `--require-kicad`, it fails clearly if `kicad-cli` is unavailable.

### Evaluation acceptance

- `model-corpus evaluate` skips fixtures without `circuit_ir.json` unless `--fixture` targets one directly, in which case it reports a clear error.
- It generates a project/schematic for fixtures with `circuit_ir.json`.
- It writes `generated_layout_features.json`, `evaluation_report.json`, and `actionable_failures.md`.
- It marks electrical equivalence as `not_run` when KiCad CLI is missing.
- It performs full electrical equivalence when KiCad CLI is available.
- It never reports a passing full evaluation if electrical equivalence failed.

### Testing acceptance

Add unit tests that run without KiCad CLI:

- `tests/unit/test_model_corpus_ingestion.py`
- `tests/unit/test_layout_features.py`
- `tests/unit/test_kicadxml_to_ir.py`
- `tests/unit/test_model_evaluation_reports.py`
- `tests/unit/test_power_symbol_metrics_real_source.py`

Add integration tests marked `requires_kicad`:

- `tests/integration/test_model_corpus_kicad_export.py`
- `tests/integration/test_model_corpus_evaluation_with_kicad.py`

The default command must pass:

```bash
uv run pytest
```

Optional full command when KiCad is installed:

```bash
uv run pytest -m requires_kicad
```

---

## Non-goals for first implementation

Do not implement these in the first patch:

- No neural network or ML model training.
- No visual screenshot comparison.
- No exact source-coordinate matching.
- No full wire-geometry connectivity solver from raw schematic coordinates.
- No automatic internet scraping.
- No new runtime dependencies unless explicitly approved.
- No hand-coded special cases for individual fixture filenames.

---

## First fixtures to use for tuning

Start with the smallest, most understandable fixtures:

1. `mcp2551-can-transciever.kicad_sch`
2. `dual-ttl-uart-to-rs232-max232-reference-design.kicad_sch`
3. `microsd-card-in-spi-mode-with-hotswap-support.kicad_sch`

Treat these as development fixtures.

Hold back larger files as validation/hard cases until the ingestion/evaluation loop is stable:

- `5v-linear-voltage-regulator-eurorack.kicad_sch`
- `li-ion-battery-charger-circuit-gems-electronics.kicad_sch`
- `st7789-tft-lcd-interface-neuralnetwork-based-autonomouscar.kicad_sch`
