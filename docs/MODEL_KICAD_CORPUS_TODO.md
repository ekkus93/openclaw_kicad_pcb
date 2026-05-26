# TODO: Add Model KiCad Corpus Ingestion and Evaluation Harness

This TODO list is for GitHub Copilot. Implement the tasks in order. The goal is to make it easy to drop real `.kicad_sch` files into `model_kicad_files/`, ingest them into stable fixtures, evaluate generated schematics against them, and use the resulting reports to improve the generic netlist-JSON-to-KiCad-schematic generator.

Do not implement ML training. This is a deterministic corpus/evaluator/regression workflow.

Do not special-case individual fixture filenames or individual source schematic coordinates.

---

## Phase 0 — Safety rules and repo orientation

### 0.1 Read the relevant existing code first
- [ ] Read `src/kicad_pcb/circuit_ir.py`.
- [ ] Read `src/kicad_pcb/commands/netlist.py`.
- [ ] Read `src/kicad_pcb/commands/_sch_apply.py`.
- [ ] Read `src/kicad_pcb/adapters.py`, especially `KicadCliAdapter.export_netlist()`.
- [ ] Read `src/kicad_pcb/sch_doc/__init__.py`.
- [ ] Read `src/kicad_pcb/schematic_metrics.py`.
- [ ] Read `src/kicad_pcb/symbol_index.py`.
- [ ] Read existing tests under `tests/unit/test_schematic_metrics.py`, `tests/unit/test_readability_baseline.py`, and `tests/fixtures/readability/`.

### 0.2 Preserve existing behavior
- [ ] Do not change the current `new-from-netlist` or `apply-netlist` user-visible behavior except where required to expose reusable helpers.
- [ ] Do not move existing generator code into the corpus package.
- [ ] Do not make default unit tests require `kicad-cli`.
- [ ] Do not add new dependencies without explicit approval.
- [ ] Do not mutate raw files under `model_kicad_files/`.

### 0.3 Use uv for validation
- [ ] Use `uv run pytest` for the default test suite.
- [ ] Use `uv run pytest -m requires_kicad` only for tests that require KiCad CLI.
- [ ] Use `uv run ruff check .` if lint validation is part of the repo workflow.

---

## Phase 1 — Add corpus package skeleton

### 1.1 Create new package directory
- [ ] Add `src/kicad_pcb/corpus/__init__.py`.
- [ ] Add `src/kicad_pcb/corpus/metadata.py`.
- [ ] Add `src/kicad_pcb/corpus/ingestion.py`.
- [ ] Add `src/kicad_pcb/corpus/layout_features.py`.
- [ ] Add `src/kicad_pcb/corpus/kicadxml.py`.
- [ ] Add `src/kicad_pcb/corpus/embedded_symbols.py`.
- [ ] Add `src/kicad_pcb/corpus/reports.py`.

### 1.2 Define fixture status model
In `src/kicad_pcb/corpus/metadata.py`:

- [ ] Define allowed statuses:
  - [ ] `ready`
  - [ ] `layout_only`
  - [ ] `pending_netlist_export`
  - [ ] `rejected`
- [ ] Define a typed/Pydantic `CorpusFixtureMetadata` model with fields:
  - [ ] `fixture_id`
  - [ ] `source_file_name`
  - [ ] `source_path`
  - [ ] `status`
  - [ ] `status_reasons`
  - [ ] `created_by`
  - [ ] `kicad_schematic_version`
  - [ ] `generator`
  - [ ] `title`
  - [ ] `license`
  - [ ] `source_url`
  - [ ] `symbol_count`
  - [ ] `wire_count`
  - [ ] `label_count`
  - [ ] `global_label_count`
  - [ ] `power_symbol_count`
  - [ ] `has_embedded_symbols`
  - [ ] `has_circuit_ir`
  - [ ] `requires_custom_symbols`
  - [ ] `notes`
- [ ] Implement stable JSON writer using sorted keys and indentation.

### 1.3 Add fixture slug helper
- [ ] Implement `make_fixture_id(path: Path) -> str`.
- [ ] Convert filename stems to lower snake case.
- [ ] Replace hyphens, spaces, repeated separators, and non-alphanumeric groups with `_`.
- [ ] Strip leading/trailing `_`.
- [ ] Add deterministic collision handling if two files produce the same slug.

### 1.4 Add unit tests for metadata/slugging
- [ ] Add `tests/unit/test_model_corpus_metadata.py`.
- [ ] Test simple hyphenated names.
- [ ] Test repeated separators.
- [ ] Test collision handling.
- [ ] Test metadata JSON round trip.

---

## Phase 2 — Fix power-symbol metrics before using real source files

### 2.1 Update `count_power_symbols()`
File: `src/kicad_pcb/schematic_metrics.py`

- [ ] Replace the current direct `(power yes)`-only implementation.
- [ ] Count a placed symbol as a power symbol when any of these are true:
  - [ ] reference starts with `#PWR`
  - [ ] symbol id starts with `power:`
  - [ ] direct children include both `(in_bom no)` and `(on_board no)`
  - [ ] the placed symbol has a direct `(power yes)` marker
- [ ] Preserve the existing public function signature.
- [ ] Keep behavior deterministic.

### 2.2 Add tests using current `model_kicad_files`
- [ ] Add `tests/unit/test_power_symbol_metrics_real_source.py`.
- [ ] Use at least `model_kicad_files/mcp2551-can-transciever.kicad_sch`.
- [ ] Assert `count_power_symbols(doc) > 0` for real source schematic files that contain `power:` symbols.
- [ ] Assert existing generated power-symbol tests still pass.

---

## Phase 3 — Implement layout feature extraction

### 3.1 Define layout feature models
File: `src/kicad_pcb/corpus/layout_features.py`

- [ ] Define `SymbolLayoutFeature` with:
  - [ ] `ref`
  - [ ] `symbol_id`
  - [ ] `value`
  - [ ] `x`
  - [ ] `y`
  - [ ] `rotation`
  - [ ] `unit`
  - [ ] `role_guess`
  - [ ] `is_power_symbol`
  - [ ] `is_connector`
  - [ ] `is_passive`
  - [ ] `is_major_ic`
- [ ] Define `RelativePositionFeature` with:
  - [ ] `a`
  - [ ] `b`
  - [ ] `relation`
- [ ] Define `LayoutFeatures` with:
  - [ ] `schema_version`
  - [ ] `source`
  - [ ] `counts`
  - [ ] `symbols`
  - [ ] `role_counts`
  - [ ] `net_label_strategy`
  - [ ] `geometry`
  - [ ] `relative_positions`
  - [ ] `intrinsic_lints`

### 3.2 Implement symbol role guessing
- [ ] Implement `guess_symbol_role(ref: str, symbol_id: str, value: str) -> str`.
- [ ] Detect power symbols robustly.
- [ ] Detect connectors.
- [ ] Detect major ICs.
- [ ] Detect passives.
- [ ] Detect rough interface/display/memory-card/LED-chain roles when obvious from symbol id or value.
- [ ] Do not use fixture filenames for role detection.

### 3.3 Implement geometry and metrics extraction
- [ ] Use `SchematicDoc.list_symbols()` for placed symbols.
- [ ] Count direct root nodes through `SchematicDoc.count_nodes(...)`.
- [ ] Use existing metrics where appropriate:
  - [ ] `count_distinct_x_columns`
  - [ ] `average_symbol_spacing`
  - [ ] `wire_stub_ratio`
  - [ ] updated `count_power_symbols`
- [ ] Record bounding extents from symbol positions.
- [ ] Ignore power symbols when computing major relative positions.

### 3.4 Implement relative position extraction
- [ ] Add high-confidence `left_of`, `right_of`, `above`, `below`, and `near` relationships.
- [ ] Only compare non-power symbols by default.
- [ ] Avoid O(N^2) explosion on large schematics by limiting to important symbols and nearest neighbors.
- [ ] For V0, include:
  - [ ] relationships among major ICs and connectors
  - [ ] nearest passives to each major IC
  - [ ] nearest passives to each connector

### 3.5 Write layout feature JSON deterministically
- [ ] Implement `write_layout_features(features, path)`.
- [ ] Sort symbol keys by reference.
- [ ] Sort relative positions by `(a, b, relation)`.
- [ ] Use indentation and sorted keys.

### 3.6 Add unit tests
- [ ] Add `tests/unit/test_layout_features.py`.
- [ ] Test extraction from a small synthetic schematic.
- [ ] Test extraction from `model_kicad_files/mcp2551-can-transciever.kicad_sch`.
- [ ] Test power-symbol detection.
- [ ] Test connector/IC/passive role guessing.
- [ ] Test deterministic JSON output.

---

## Phase 4 — Implement embedded-symbol extraction

### 4.1 Extract embedded lib symbols
File: `src/kicad_pcb/corpus/embedded_symbols.py`

- [ ] Implement `extract_embedded_symbol_defs(doc: SchematicDoc) -> dict[str, ListNode]`.
- [ ] Find the root `(lib_symbols ...)` section.
- [ ] Extract child `(symbol "...")` definitions.
- [ ] Key returned entries by full symbol id when possible.
- [ ] Do not mutate the source document.

### 4.2 Write fixture-local embedded symbol artifact
- [ ] Implement `write_embedded_symbol_library(symbols, output_file)`.
- [ ] If writing a fully valid `.kicad_sym` library is too large for V0, write a deterministic `source_embedded_symbols.sexpr` artifact instead and document the limitation.
- [ ] Prefer a KiCad-compatible `.kicad_sym` if feasible.

### 4.3 Add tests
- [ ] Add `tests/unit/test_embedded_symbols.py`.
- [ ] Test extraction on at least one real `model_kicad_files` source with embedded symbols.
- [ ] Test empty/no-symbol case.
- [ ] Test deterministic artifact writing.

---

## Phase 5 — Implement KiCad XML netlist parser and CircuitIR conversion

### 5.1 Add parsed XML dataclasses/models
File: `src/kicad_pcb/corpus/kicadxml.py`

- [ ] Define `KicadXmlComponent`.
- [ ] Define `KicadXmlNetPin`.
- [ ] Define `KicadXmlNet`.
- [ ] Define `KicadXmlNetlist`.

### 5.2 Parse KiCad XML netlist
- [ ] Implement `parse_kicadxml_netlist(path: Path) -> KicadXmlNetlist`.
- [ ] Use stdlib `xml.etree.ElementTree`.
- [ ] Parse components from `<components><comp ref="...">`.
- [ ] Parse values from `<value>`.
- [ ] Parse footprints from `<footprint>` when present.
- [ ] Parse library/source symbol IDs from `<libsource>` when present.
- [ ] Parse nets from `<nets><net name="...">`.
- [ ] Parse node refs/pins from `<node ref="..." pin="...">`.
- [ ] Fail clearly on malformed XML.

### 5.3 Convert KiCad XML to CircuitIR
- [ ] Implement `kicadxml_to_circuit_ir(netlist: KicadXmlNetlist) -> CircuitIR`.
- [ ] Preserve component refs.
- [ ] Preserve values.
- [ ] Preserve footprints where available.
- [ ] Preserve symbol IDs when available.
- [ ] Build `NetIR` entries from net nodes.
- [ ] Skip nets with fewer than one pin only if KiCad XML contains them; record a warning.
- [ ] Normalize ground aliases through existing `CircuitIR` validation behavior.

### 5.4 Canonicalize CircuitIR
- [ ] Implement `canonicalize_circuit_ir(ir: CircuitIR) -> CircuitIR`.
- [ ] Sort components by ref.
- [ ] Sort nets by name.
- [ ] Sort pins in each net by `(ref, pin, unit or "")`.
- [ ] Ensure output JSON is deterministic.

### 5.5 Add XML fixtures and tests
- [ ] Add small hand-written KiCad XML fixture under `tests/fixtures/model_corpus_xml/`.
- [ ] Add `tests/unit/test_kicadxml_to_ir.py`.
- [ ] Test parsing components.
- [ ] Test parsing nets.
- [ ] Test component symbol ID preservation.
- [ ] Test CircuitIR conversion.
- [ ] Test canonical sorting.
- [ ] Test malformed XML error reporting.

---

## Phase 6 — Implement model-corpus ingestion command

### 6.1 Add command module
File: `src/kicad_pcb/commands/model_corpus.py`

- [ ] Implement `cmd_model_corpus_ingest(args)`.
- [ ] Implement `cmd_model_corpus_list(args)`.
- [ ] Keep command code thin; delegate domain logic to `corpus/ingestion.py`.

### 6.2 Add parser entries
File: `src/kicad_pcb/cli.py`

- [ ] Add `model-corpus` subparser.
- [ ] Add `ingest` subcommand.
- [ ] Add `list` subcommand.
- [ ] Add `evaluate` subcommand placeholder only after Phase 8 implements it.
- [ ] Support `--json` through existing formatting system if possible.

Required `ingest` arguments:

- [ ] `--source-dir`, default `model_kicad_files`
- [ ] `--out-dir`, default `tests/fixtures/model_corpus`
- [ ] `--refresh`, default false
- [ ] `--require-kicad`, default false

Required `list` arguments:

- [ ] `--corpus-dir`, default `tests/fixtures/model_corpus`

### 6.3 Implement ingestion logic
File: `src/kicad_pcb/corpus/ingestion.py`

- [ ] Scan source dir recursively for `.kicad_sch` files.
- [ ] Parse each file with `SchematicDoc.load(...)`.
- [ ] Create fixture directory.
- [ ] Copy raw source to `source.kicad_sch`.
- [ ] Extract and write `source_layout_features.json`.
- [ ] Extract embedded symbols and write fixture artifact when present.
- [ ] Try KiCad XML export when `kicad-cli` is available.
- [ ] If XML export succeeds, parse XML and write `circuit_ir.json`.
- [ ] If KiCad CLI is missing and `--require-kicad` is false, write a partial fixture and warning.
- [ ] If KiCad CLI is missing and `--require-kicad` is true, fail clearly.
- [ ] Write `metadata.json`.
- [ ] Write aggregate `ingestion_report.json`.
- [ ] Write aggregate `summary.md`.

### 6.4 Add result model if needed
File: `src/kicad_pcb/results.py`

- [ ] Add `ModelCorpusIngestResult` dataclass.
- [ ] Add `ModelCorpusListResult` dataclass.
- [ ] Ensure normal CLI formatting works.
- [ ] Ensure JSON formatting works.

### 6.5 Add tests
- [ ] Add `tests/unit/test_model_corpus_ingestion.py`.
- [ ] Test ingest of one real model file without KiCad CLI.
- [ ] Test status is partial when no XML netlist exists.
- [ ] Test `--refresh` behavior.
- [ ] Test no raw source mutation.
- [ ] Test list command reads fixture metadata.

---

## Phase 7 — Add KiCad CLI integration for source netlist export

### 7.1 Reuse existing adapter
- [ ] Use `KicadCliAdapter.export_netlist(sch_file, output_file)`.
- [ ] Do not shell out directly in corpus code.
- [ ] Convert adapter failures into explicit fixture status/warnings.

### 7.2 Handle unavailable KiCad CLI
- [ ] Detect command failure clearly.
- [ ] If `--require-kicad` is false, mark fixture `pending_netlist_export`.
- [ ] If `--require-kicad` is true, raise a user-facing error.

### 7.3 Add requires-kicad integration tests
- [ ] Add `tests/integration/test_model_corpus_kicad_export.py`.
- [ ] Mark with `@pytest.mark.requires_kicad`.
- [ ] Skip cleanly if `kicad-cli` is not installed.
- [ ] Export netlist from at least one small source schematic.
- [ ] Assert `source_netlist.kicadxml` is written.
- [ ] Assert `circuit_ir.json` is written.

---

## Phase 8 — Add evaluation package skeleton

### 8.1 Create evaluation modules
- [ ] Add `src/kicad_pcb/evaluation/__init__.py`.
- [ ] Add `src/kicad_pcb/evaluation/electrical.py`.
- [ ] Add `src/kicad_pcb/evaluation/scoring.py`.
- [ ] Add `src/kicad_pcb/evaluation/similarity.py`.
- [ ] Add `src/kicad_pcb/evaluation/reports.py`.

### 8.2 Implement electrical equivalence report
File: `src/kicad_pcb/evaluation/electrical.py`

- [ ] Define `ElectricalEquivalenceReport`.
- [ ] Implement `compare_circuit_ir_equivalence(source, generated)`.
- [ ] Compare component refs.
- [ ] Compare component values where present.
- [ ] Compare component symbols where present.
- [ ] Compare pin-to-net assignments.
- [ ] Return structured mismatch lists.
- [ ] Do not throw for normal mismatch; return failed report.

### 8.3 Implement intrinsic quality scoring
File: `src/kicad_pcb/evaluation/scoring.py`

- [ ] Define `IntrinsicQualityReport`.
- [ ] Score parse validity.
- [ ] Score symbol spread.
- [ ] Score wire stub ratio.
- [ ] Score power-symbol usage.
- [ ] Score layout lints.
- [ ] Keep weights named constants.
- [ ] Include actionable reasons for low sub-scores.

### 8.4 Implement source similarity scoring
File: `src/kicad_pcb/evaluation/similarity.py`

- [ ] Define `LayoutSimilarityReport`.
- [ ] Compare source/generated role counts.
- [ ] Compare relative position relationships.
- [ ] Compare label/global-label/power-symbol strategy.
- [ ] Compare geometry spread.
- [ ] Do not compare exact coordinates.
- [ ] Include actionable reasons for low sub-scores.

### 8.5 Add unit tests
- [ ] Add `tests/unit/test_model_evaluation_electrical.py`.
- [ ] Add `tests/unit/test_model_evaluation_scoring.py`.
- [ ] Add `tests/unit/test_model_evaluation_similarity.py`.
- [ ] Use small synthetic `CircuitIR` and synthetic `LayoutFeatures` objects.
- [ ] Test electrical pass.
- [ ] Test electrical fail.
- [ ] Test total score cannot pass when electrical equivalence fails.
- [ ] Test exact-coordinate differences do not automatically fail similarity.

---

## Phase 9 — Implement model-corpus evaluate command

### 9.1 Add evaluate CLI parser
File: `src/kicad_pcb/cli.py`

- [ ] Add `model-corpus evaluate`.

Required arguments:

- [ ] `--corpus-dir`, default `tests/fixtures/model_corpus`
- [ ] `--out-dir`, default `code_review/generated/model_eval`
- [ ] `--fixture`, optional fixture ID
- [ ] `--require-kicad`, default false
- [ ] `--heuristic-profile`, optional, same allowed values as generation command if reused
- [ ] `--label-mode`, optional, same allowed values as generation command if reused

### 9.2 Implement command behavior
File: `src/kicad_pcb/commands/model_corpus.py`

- [ ] Implement `cmd_model_corpus_evaluate(args)`.
- [ ] Load all fixtures or one selected fixture.
- [ ] Skip fixtures without `circuit_ir.json` in all-fixtures mode.
- [ ] Fail clearly if selected `--fixture` lacks `circuit_ir.json`.
- [ ] Generate a project from `circuit_ir.json` using existing generation path.
- [ ] Extract generated layout features.
- [ ] Write `generated_layout_features.json`.
- [ ] If KiCad CLI is available, export generated XML netlist and convert to `CircuitIR`.
- [ ] Compare electrical equivalence.
- [ ] If KiCad CLI unavailable and `--require-kicad` is false, set electrical status `not_run`.
- [ ] Score intrinsic quality.
- [ ] Score source similarity.
- [ ] Write `evaluation_report.json`.
- [ ] Write `actionable_failures.md`.
- [ ] Write aggregate `summary.json` and `summary.md`.

### 9.3 Avoid current-project/session side effects
- [ ] Ensure evaluation writes into `--out-dir`, not the active user project/session.
- [ ] If existing generation helpers require a `ProjectRef`, construct an isolated temporary/evaluation project under the fixture output directory.
- [ ] Do not change the user’s current project config.

### 9.4 Add result model if needed
File: `src/kicad_pcb/results.py`

- [ ] Add `ModelCorpusEvaluateResult` dataclass.
- [ ] Include corpus dir, output dir, fixture count, evaluated count, skipped count, failed count, summary path.
- [ ] Ensure JSON output works.

### 9.5 Add tests
- [ ] Add `tests/unit/test_model_corpus_evaluate_command.py`.
- [ ] Test skipping fixture without `circuit_ir.json`.
- [ ] Test selected fixture without `circuit_ir.json` returns clear error.
- [ ] Test report generation using a small synthetic fixture.
- [ ] Test no active project/session mutation.

---

## Phase 10 — Write Copilot-friendly actionable failure reports

### 10.1 Implement Markdown report writer
File: `src/kicad_pcb/evaluation/reports.py`

- [ ] Write `actionable_failures.md` per fixture.
- [ ] Include fixture ID and source filename.
- [ ] Include total score and sub-scores.
- [ ] Include electrical status.
- [ ] Include top failures ordered by severity.
- [ ] Include likely files to edit.
- [ ] Include explicit no-special-casing instruction.

### 10.2 Suggested file mapping
Use this mapping when creating actionable failures:

- [ ] Role/classification issue → `src/kicad_pcb/block_detection.py`
- [ ] Placement/order issue → `src/kicad_pcb/graphviz_layout/dot_builder.py`, `src/kicad_pcb/graphviz_layout/snap.py`
- [ ] Routing/wire/label issue → `src/kicad_pcb/router.py`
- [ ] Symbol resolution issue → `src/kicad_pcb/symbol_index.py`, `src/kicad_pcb/lib_symbol.py`, `src/kicad_pcb/corpus/embedded_symbols.py`
- [ ] Metric/evaluator false positive → `src/kicad_pcb/schematic_metrics.py`, `src/kicad_pcb/corpus/layout_features.py`, `src/kicad_pcb/evaluation/`

### 10.3 Add tests
- [ ] Add `tests/unit/test_model_evaluation_reports.py`.
- [ ] Test Markdown report includes no-special-casing instruction.
- [ ] Test JSON report is deterministic.
- [ ] Test severity ordering.

---

## Phase 11 — First corpus tuning loop

Do this only after ingestion and evaluation exist.

### 11.1 Start with MCP2551 fixture
- [ ] Ingest `mcp2551-can-transciever.kicad_sch`.
- [ ] Ensure `circuit_ir.json` exists when KiCad CLI is available.
- [ ] Run evaluation.
- [ ] Read `actionable_failures.md`.
- [ ] Identify one generic generator improvement.
- [ ] Modify generator code without fixture-specific checks.
- [ ] Add regression test.
- [ ] Re-run evaluation.

Likely generic rules:

- [ ] Classify transceiver/interface ICs as central blocks.
- [ ] Place logic/MCU-side signals left of interface IC.
- [ ] Place field/bus connector side right of interface IC.
- [ ] Keep termination/protection/support passives near the side they serve.

### 11.2 Then use MAX232 fixture
- [ ] Ingest/evaluate `dual-ttl-uart-to-rs232-max232-reference-design.kicad_sch`.
- [ ] Add generic interface/charge-pump capacitor locality rules if failures show that need.
- [ ] Do not hard-code MAX232-specific filenames.

### 11.3 Then use MicroSD fixture
- [ ] Ingest/evaluate `microsd-card-in-spi-mode-with-hotswap-support.kicad_sch`.
- [ ] Add generic connector + bus signal label strategy improvements if failures show that need.
- [ ] Keep pullups/decoupling/support parts near relevant connector/IC pins.

---

## Phase 12 — Documentation

### 12.1 Add corpus README
- [ ] Add `model_kicad_files/README.md`.
- [ ] Explain that files here are raw source examples.
- [ ] Explain license/source metadata expectations.
- [ ] Explain how to ingest files.
- [ ] Explain not to hand-edit generated fixture artifacts except metadata notes if needed.

### 12.2 Add model corpus docs
- [ ] Add `docs/MODEL_KICAD_CORPUS.md`.
- [ ] Document the workflow:
  - [ ] add `.kicad_sch`
  - [ ] run ingest
  - [ ] run evaluate
  - [ ] read reports
  - [ ] improve generator rules
  - [ ] re-run tests
- [ ] Explain why this is not ML training.
- [ ] Explain electrical equivalence vs schematic quality similarity.
- [ ] Explain partial fixtures when KiCad CLI is unavailable.

---

## Phase 13 — Final validation checklist

Before finishing, run:

```bash
uv run pytest
```

If KiCad CLI is installed, also run:

```bash
uv run pytest -m requires_kicad
```

Then manually verify:

- [ ] `uv run python -m kicad_pcb.cli model-corpus ingest --source-dir model_kicad_files --out-dir tests/fixtures/model_corpus --refresh` works.
- [ ] `tests/fixtures/model_corpus/` contains one directory per parseable source schematic.
- [ ] Every fixture has `metadata.json`.
- [ ] Every parseable fixture has `source_layout_features.json`.
- [ ] Fixtures with successful KiCad XML export have `circuit_ir.json`.
- [ ] `uv run python -m kicad_pcb.cli model-corpus list --corpus-dir tests/fixtures/model_corpus` works.
- [ ] `uv run python -m kicad_pcb.cli model-corpus evaluate --corpus-dir tests/fixtures/model_corpus --out-dir code_review/generated/model_eval` works for fixtures with `circuit_ir.json`.
- [ ] Evaluation writes aggregate `summary.md`.
- [ ] Per-fixture reports include actionable failures.
- [ ] Existing readability/golden tests still pass.

---

## Explicit non-goals

Do not do these in this patch:

- [ ] Do not add neural-network training.
- [ ] Do not add visual screenshot comparison.
- [ ] Do not compare exact source coordinates as a pass/fail criterion.
- [ ] Do not implement full raw-wire connectivity extraction from schematic geometry.
- [ ] Do not scrape the internet.
- [ ] Do not special-case individual files in `model_kicad_files/`.
- [ ] Do not require KiCad CLI for the default `uv run pytest` suite.
