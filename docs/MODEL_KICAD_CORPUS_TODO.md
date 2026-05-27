# TODO: Add Model KiCad Corpus Ingestion and Evaluation Harness

This TODO list is for GitHub Copilot. Implement the tasks in order. The goal is to make it easy to drop real `.kicad_sch` files into `model_kicad_files/`, ingest them into stable fixtures, evaluate generated schematics against them, and use the resulting reports to improve the generic netlist-JSON-to-KiCad-schematic generator.

Do not implement ML training. This is a deterministic corpus/evaluator/regression workflow.

Do not special-case individual fixture filenames or individual source schematic coordinates.

---

## Phase 0 — Safety rules and repo orientation

### 0.1 Read the relevant existing code first
- [x] Read `src/kicad_pcb/circuit_ir.py`.
- [x] Read `src/kicad_pcb/commands/netlist.py`.
- [x] Read `src/kicad_pcb/commands/_sch_apply.py`.
- [x] Read `src/kicad_pcb/adapters.py`, especially `KicadCliAdapter.export_netlist()`.
- [x] Read `src/kicad_pcb/sch_doc/__init__.py`.
- [x] Read `src/kicad_pcb/schematic_metrics.py`.
- [x] Read `src/kicad_pcb/symbol_index.py`.
- [x] Read existing tests under `tests/unit/test_schematic_metrics.py`, `tests/unit/test_readability_baseline.py`, and `tests/fixtures/readability/`.

### 0.2 Preserve existing behavior
- [x] Do not change the current `new-from-netlist` or `apply-netlist` user-visible behavior except where required to expose reusable helpers.
- [x] Do not move existing generator code into the corpus package.
- [x] Do not make default unit tests require `kicad-cli`.
- [x] Do not add new dependencies without explicit approval.
- [x] Do not mutate raw files under `model_kicad_files/`.

### 0.3 Use uv for validation
- [x] Use `uv run pytest` for the default test suite.
- [x] Use `uv run pytest -m requires_kicad` only for tests that require KiCad CLI.
- [x] Use `uv run ruff check .` if lint validation is part of the repo workflow.

---

## Phase 1 — Add corpus package skeleton

### 1.1 Create new package directory
- [x] Add `src/kicad_pcb/corpus/__init__.py`.
- [x] Add `src/kicad_pcb/corpus/metadata.py`.
- [x] Add `src/kicad_pcb/corpus/ingestion.py`.
- [x] Add `src/kicad_pcb/corpus/layout_features.py`.
- [x] Add `src/kicad_pcb/corpus/kicadxml.py`.
- [x] Add `src/kicad_pcb/corpus/embedded_symbols.py`.
- [x] Add `src/kicad_pcb/corpus/reports.py`.

### 1.2 Define fixture status model
In `src/kicad_pcb/corpus/metadata.py`:

- [ ] Define allowed statuses:
- [x] Define allowed statuses:
  - [x] `ready`
  - [x] `layout_only`
  - [x] `pending_netlist_export`
  - [x] `rejected`
- [x] Define a typed/Pydantic `CorpusFixtureMetadata` model with fields:
  - [x] `fixture_id`
  - [x] `source_file_name`
  - [x] `source_path`
  - [x] `status`
  - [x] `status_reasons`
  - [x] `created_by`
  - [x] `kicad_schematic_version`
  - [x] `generator`
  - [x] `title`
  - [x] `license`
  - [x] `source_url`
  - [x] `symbol_count`
  - [x] `wire_count`
  - [x] `label_count`
  - [x] `global_label_count`
  - [x] `power_symbol_count`
  - [x] `has_embedded_symbols`
  - [x] `has_circuit_ir`
  - [x] `requires_custom_symbols`
  - [x] `notes`
- [x] Implement stable JSON writer using sorted keys and indentation.

### 1.3 Add fixture slug helper
- [x] Implement `make_fixture_id(path: Path) -> str`.
- [x] Convert filename stems to lowercase ASCII slug form.
- [x] Replace non-alphanumeric runs with `-`.
- [x] Trim leading/trailing `-`.
- [x] Add deterministic collision handling using `base-slug--<hash8>` for full collision groups.

### 1.4 Add unit tests for metadata/slugging
- [x] Add `tests/unit/test_model_corpus_metadata.py`.
- [x] Test simple hyphenated names.
- [x] Test repeated separators.
- [x] Test collision handling.
- [x] Test metadata JSON round trip.

---

## Phase 2 — Fix power-symbol metrics before using real source files

### 2.1 Update `count_power_symbols()`
File: `src/kicad_pcb/schematic_metrics.py`

- [x] Replace the current direct `(power yes)`-only implementation.
- [x] Count a placed symbol as a power symbol when any of these are true:
  - [x] reference starts with `#PWR`
  - [x] symbol id starts with `power:`
  - [x] direct children include both `(in_bom no)` and `(on_board no)`
  - [x] the placed symbol has a direct `(power yes)` marker
- [x] Preserve the existing public function signature.
- [x] Keep behavior deterministic.

### 2.2 Add tests using current `model_kicad_files`
- [x] Add `tests/unit/test_power_symbol_metrics_real_source.py`.
- [x] Use at least `model_kicad_files/mcp2551-can-transciever.kicad_sch`.
- [x] Assert `count_power_symbols(doc) > 0` for real source schematic files that contain `power:` symbols.
- [x] Assert existing generated power-symbol tests still pass.

---

## Phase 3 — Implement layout feature extraction

### 3.1 Define layout feature models
File: `src/kicad_pcb/corpus/layout_features.py`

- [x] Define `SymbolLayoutFeature` with:
  - [x] `ref`
  - [x] `symbol_id`
  - [x] `value`
  - [x] `x`
  - [x] `y`
  - [x] `rotation`
  - [x] `unit`
  - [x] `role_guess`
  - [x] `is_power_symbol`
  - [x] `is_connector`
  - [x] `is_passive`
  - [x] `is_major_ic`
- [x] Define `RelativePositionFeature` with:
  - [x] `a`
  - [x] `b`
  - [x] `relation`
- [x] Define `LayoutFeatures` with:
  - [x] `schema_version`
  - [x] `source`
  - [x] `counts`
  - [x] `symbols`
  - [x] `role_counts`
  - [x] `net_label_strategy`
  - [x] `geometry`
  - [x] `relative_positions`
  - [x] `intrinsic_lints`

### 3.2 Implement symbol role guessing
- [x] Implement `guess_symbol_role(ref: str, symbol_id: str, value: str) -> str`.
- [x] Detect power symbols robustly.
- [x] Detect connectors.
- [x] Detect major ICs.
- [x] Detect passives.
- [x] Detect rough interface/display/memory-card/LED-chain roles when obvious from symbol id or value.
- [x] Do not use fixture filenames for role detection.

### 3.3 Implement geometry and metrics extraction
- [x] Use `SchematicDoc.list_symbols()` for placed symbols.
- [x] Count direct root nodes through `SchematicDoc.count_nodes(...)`.
- [x] Use existing metrics where appropriate:
  - [x] `count_distinct_x_columns`
  - [x] `average_symbol_spacing`
  - [x] `wire_stub_ratio`
  - [x] updated `count_power_symbols`
- [x] Record bounding extents from symbol positions.
- [x] Ignore power symbols when computing major relative positions.

### 3.4 Implement relative position extraction
- [x] Add high-confidence `left_of`, `right_of`, `above`, `below`, and `near` relationships.
- [x] Only compare non-power symbols by default.
- [x] Avoid O(N^2) explosion on large schematics by limiting to important symbols and nearest neighbors.
- [x] For V0, include:
  - [x] relationships among major ICs and connectors
  - [x] nearest passives to each major IC
  - [x] nearest passives to each connector

### 3.5 Write layout feature JSON deterministically
- [x] Implement `write_layout_features(features, path)`.
- [x] Sort symbol keys by reference.
- [x] Sort relative positions by `(a, b, relation)`.
- [x] Use indentation and sorted keys.

### 3.6 Add unit tests
- [x] Add `tests/unit/test_layout_features.py`.
- [x] Test extraction from a small synthetic schematic.
- [x] Test extraction from `model_kicad_files/mcp2551-can-transciever.kicad_sch`.
- [x] Test power-symbol detection.
- [x] Test connector/IC/passive role guessing.
- [x] Test deterministic JSON output.

---

## Phase 4 — Implement embedded-symbol extraction

### 4.1 Extract embedded lib symbols
File: `src/kicad_pcb/corpus/embedded_symbols.py`

- [x] Implement `extract_embedded_symbol_defs(doc: SchematicDoc) -> dict[str, ListNode]`.
- [x] Find the root `(lib_symbols ...)` section.
- [x] Extract child `(symbol "...")` definitions.
- [x] Key returned entries by full symbol id when possible.
- [x] Do not mutate the source document.

### 4.2 Write fixture-local embedded symbol artifact
- [x] Implement `write_embedded_symbol_library(symbols, output_file)`.
- [x] If writing a fully valid `.kicad_sym` library is too large for V0, write a deterministic `source_embedded_symbols.sexpr` artifact instead and document the limitation.
- [x] Prefer a KiCad-compatible `.kicad_sym` if feasible.

### 4.3 Add tests
- [x] Add `tests/unit/test_embedded_symbols.py`.
- [x] Test extraction on at least one real `model_kicad_files` source with embedded symbols.
- [x] Test empty/no-symbol case.
- [x] Test deterministic artifact writing.

---

## Phase 5 — Implement KiCad XML netlist parser and CircuitIR conversion

### 5.1 Add parsed XML dataclasses/models
File: `src/kicad_pcb/corpus/kicadxml.py`

- [x] Define `KicadXmlComponent`.
- [x] Define `KicadXmlNetPin`.
- [x] Define `KicadXmlNet`.
- [x] Define `KicadXmlNetlist`.

### 5.2 Parse KiCad XML netlist
- [x] Implement `parse_kicadxml_netlist(path: Path) -> KicadXmlNetlist`.
- [x] Use stdlib `xml.etree.ElementTree`.
- [x] Parse components from `<components><comp ref="...">`.
- [x] Parse values from `<value>`.
- [x] Parse footprints from `<footprint>` when present.
- [x] Parse library/source symbol IDs from `<libsource>` when present.
- [x] Parse nets from `<nets><net name="...">`.
- [x] Parse node refs/pins from `<node ref="..." pin="...">`.
- [x] Fail clearly on malformed XML.

### 5.3 Convert KiCad XML to CircuitIR
- [x] Implement `kicadxml_to_circuit_ir(netlist: KicadXmlNetlist) -> CircuitIR`.
- [x] Preserve component refs.
- [x] Preserve values.
- [x] Preserve footprints where available.
- [x] Preserve symbol IDs when available.
- [x] Build `NetIR` entries from net nodes.
- [x] Skip nets with fewer than one pin only if KiCad XML contains them; record a warning.
- [x] Normalize ground aliases through existing `CircuitIR` validation behavior.

### 5.4 Canonicalize CircuitIR
- [x] Implement `canonicalize_circuit_ir(ir: CircuitIR) -> CircuitIR`.
- [x] Sort components by ref.
- [x] Sort nets by name.
- [x] Sort pins in each net by `(ref, pin, unit or "")`.
- [x] Ensure output JSON is deterministic.

### 5.5 Add XML fixtures and tests
- [x] Add small hand-written KiCad XML fixture under `tests/fixtures/model_corpus_xml/`.
- [x] Add `tests/unit/test_kicadxml_to_ir.py`.
- [x] Test parsing components.
- [x] Test parsing nets.
- [x] Test component symbol ID preservation.
- [x] Test CircuitIR conversion.
- [x] Test canonical sorting.
- [x] Test malformed XML error reporting.

---

## Phase 6 — Implement model-corpus ingestion command

### 6.1 Add command module
File: `src/kicad_pcb/commands/model_corpus.py`

- [x] Implement `cmd_model_corpus_ingest(args)`.
- [x] Implement `cmd_model_corpus_list(args)`.
- [x] Keep command code thin; delegate domain logic to `corpus/ingestion.py`.

### 6.2 Add parser entries
File: `src/kicad_pcb/cli.py`

- [x] Add `model-corpus` subparser.
- [x] Add `ingest` subcommand.
- [x] Add `list` subcommand.
- [x] Add `evaluate` subcommand placeholder only after Phase 8 implements it.
- [x] Support `--json` through existing formatting system if possible.

Required `ingest` arguments:

- [x] `--source-dir`, default `model_kicad_files`
- [x] `--out-dir`, default `tests/fixtures/model_corpus`
- [x] `--refresh`, default false
- [x] `--require-kicad`, default false

Required `list` arguments:

- [x] `--corpus-dir`, default `tests/fixtures/model_corpus`

### 6.3 Implement ingestion logic
File: `src/kicad_pcb/corpus/ingestion.py`

- [x] Scan source dir recursively for `.kicad_sch` files.
- [x] Parse each file with `SchematicDoc.load(...)`.
- [x] Create fixture directory.
- [x] Copy raw source to `source.kicad_sch`.
- [x] Extract and write `source_layout_features.json`.
- [x] Extract embedded symbols and write fixture artifact when present.
- [x] Try KiCad XML export when `kicad-cli` is available.
- [x] If XML export succeeds, parse XML and write `circuit_ir.json`.
- [x] If KiCad CLI is missing and `--require-kicad` is false, write a partial fixture and warning.
- [x] If KiCad CLI is missing and `--require-kicad` is true, fail clearly.
- [x] Write `metadata.json`.
- [x] Write aggregate `ingestion_report.json`.
- [x] Write aggregate `summary.md`.

### 6.4 Add result model if needed
File: `src/kicad_pcb/results.py`

- [x] Add `ModelCorpusIngestResult` dataclass.
- [x] Add `ModelCorpusListResult` dataclass.
- [x] Ensure normal CLI formatting works.
- [x] Ensure JSON formatting works.

### 6.5 Add tests
- [x] Add `tests/unit/test_model_corpus_ingestion.py`.
- [x] Test ingest of one real model file without KiCad CLI.
- [x] Test status is partial when no XML netlist exists.
- [x] Test `--refresh` behavior.
- [x] Test no raw source mutation.
- [x] Test list command reads fixture metadata.

---

## Phase 7 — Add KiCad CLI integration for source netlist export

### 7.1 Reuse existing adapter
- [x] Use `KicadCliAdapter.export_netlist(sch_file, output_file)`.
- [x] Do not shell out directly in corpus code.
- [x] Convert adapter failures into explicit fixture status/warnings.

### 7.2 Handle unavailable KiCad CLI
- [x] Detect command failure clearly.
- [x] If `--require-kicad` is false, mark fixture `pending_netlist_export`.
- [x] If `--require-kicad` is true, raise a user-facing error.

### 7.3 Add requires-kicad integration tests
- [x] Add `tests/integration/test_model_corpus_kicad_export.py`.
- [x] Mark with `@pytest.mark.requires_kicad`.
- [x] Skip cleanly if `kicad-cli` is not installed or is too old for the repo's schematic format.
- [x] Export netlist from at least one small source schematic.
- [x] Assert `source_netlist.kicadxml` is written.
- [x] Assert `circuit_ir.json` is written.

---

## Phase 8 — Add evaluation package skeleton

### 8.1 Create evaluation modules
- [x] Add `src/kicad_pcb/evaluation/__init__.py`.
- [x] Add `src/kicad_pcb/evaluation/electrical.py`.
- [x] Add `src/kicad_pcb/evaluation/scoring.py`.
- [x] Add `src/kicad_pcb/evaluation/similarity.py`.
- [x] Add `src/kicad_pcb/evaluation/reports.py`.

### 8.2 Implement electrical equivalence report
File: `src/kicad_pcb/evaluation/electrical.py`

- [x] Define `ElectricalEquivalenceReport`.
- [x] Implement `compare_circuit_ir_equivalence(source, generated)`.
- [x] Compare component refs.
- [x] Compare component values where present.
- [x] Compare component symbols where present.
- [x] Compare pin-to-net assignments.
- [x] Return structured mismatch lists.
- [x] Do not throw for normal mismatch; return failed report.

### 8.3 Implement intrinsic quality scoring
File: `src/kicad_pcb/evaluation/scoring.py`

- [x] Define `IntrinsicQualityReport`.
- [x] Score parse validity.
- [x] Score symbol spread.
- [x] Score wire stub ratio.
- [x] Score power-symbol usage.
- [x] Score layout lints.
- [x] Keep weights named constants.
- [x] Include actionable reasons for low sub-scores.

### 8.4 Implement source similarity scoring
File: `src/kicad_pcb/evaluation/similarity.py`

- [x] Define `LayoutSimilarityReport`.
- [x] Compare source/generated role counts.
- [x] Compare relative position relationships.
- [x] Compare label/global-label/power-symbol strategy.
- [x] Compare geometry spread.
- [x] Do not compare exact coordinates.
- [x] Include actionable reasons for low sub-scores.

### 8.5 Add unit tests
- [x] Add `tests/unit/test_model_evaluation_electrical.py`.
- [x] Add `tests/unit/test_model_evaluation_scoring.py`.
- [x] Add `tests/unit/test_model_evaluation_similarity.py`.
- [x] Use small synthetic `CircuitIR` and synthetic `LayoutFeatures` objects.
- [x] Test electrical pass.
- [x] Test electrical fail.
- [x] Test total score cannot pass when electrical equivalence fails.
- [x] Test exact-coordinate differences do not automatically fail similarity.

---

## Phase 9 — Implement model-corpus evaluate command

### 9.1 Add evaluate CLI parser
File: `src/kicad_pcb/cli.py`

- [x] Add `model-corpus evaluate`.

Required arguments:

- [x] `--corpus-dir`, default `tests/fixtures/model_corpus`
- [x] `--out-dir`, default `code_review/generated/model_eval`
- [x] `--fixture`, optional fixture ID
- [x] `--require-kicad`, default false
- [x] `--heuristic-profile`, optional, same allowed values as generation command if reused
- [x] `--label-mode`, optional, same allowed values as generation command if reused

### 9.2 Implement command behavior
File: `src/kicad_pcb/commands/model_corpus.py`

- [x] Implement `cmd_model_corpus_evaluate(args)`.
- [x] Load all fixtures or one selected fixture.
- [x] Skip fixtures without `circuit_ir.json` in all-fixtures mode.
- [x] Fail clearly if selected `--fixture` lacks `circuit_ir.json`.
- [x] Generate a project from `circuit_ir.json` using existing generation path.
- [x] Extract generated layout features.
- [x] Write `generated_layout_features.json`.
- [x] If KiCad CLI is available, export generated XML netlist and convert to `CircuitIR`.
- [x] Compare electrical equivalence.
- [x] If KiCad CLI unavailable and `--require-kicad` is false, set electrical status `not_run`.
- [x] Score intrinsic quality.
- [x] Score source similarity.
- [x] Write `evaluation_report.json`.
- [x] Write `actionable_failures.md`.
- [x] Write aggregate `summary.json` and `summary.md`.

### 9.3 Avoid current-project/session side effects
- [x] Ensure evaluation writes into `--out-dir`, not the active user project/session.
- [x] If existing generation helpers require a `ProjectRef`, construct an isolated temporary/evaluation project under the fixture output directory.
- [x] Do not change the user’s current project config.

### 9.4 Add result model if needed
File: `src/kicad_pcb/results.py`

- [x] Add `ModelCorpusEvaluateResult` dataclass.
- [x] Include corpus dir, output dir, fixture count, evaluated count, skipped count, failed count, summary path.
- [x] Ensure JSON output works.

### 9.5 Add tests
- [x] Add `tests/unit/test_model_corpus_evaluate_command.py`.
- [x] Test skipping fixture without `circuit_ir.json`.
- [x] Test selected fixture without `circuit_ir.json` returns clear error.
- [x] Test report generation using a small synthetic fixture.
- [x] Test no active project/session mutation.

---

## Phase 10 — Write Copilot-friendly actionable failure reports

### 10.1 Implement Markdown report writer
File: `src/kicad_pcb/evaluation/reports.py`

- [x] Write `actionable_failures.md` per fixture.
- [x] Include fixture ID and source filename.
- [x] Include total score and sub-scores.
- [x] Include electrical status.
- [x] Include top failures ordered by severity.
- [x] Include likely files to edit.
- [x] Include explicit no-special-casing instruction.

### 10.2 Suggested file mapping
Use this mapping when creating actionable failures:

- [x] Role/classification issue → `src/kicad_pcb/block_detection.py`
- [x] Placement/order issue → `src/kicad_pcb/graphviz_layout/dot_builder.py`, `src/kicad_pcb/graphviz_layout/snap.py`
- [x] Routing/wire/label issue → `src/kicad_pcb/router.py`
- [x] Symbol resolution issue → `src/kicad_pcb/symbol_index.py`, `src/kicad_pcb/lib_symbol.py`, `src/kicad_pcb/corpus/embedded_symbols.py`
- [x] Metric/evaluator false positive → `src/kicad_pcb/schematic_metrics.py`, `src/kicad_pcb/corpus/layout_features.py`, `src/kicad_pcb/evaluation/`

### 10.3 Add tests
- [x] Add `tests/unit/test_model_evaluation_reports.py`.
- [x] Test Markdown report includes no-special-casing instruction.
- [x] Test JSON report is deterministic.
- [x] Test severity ordering.

---

## Phase 11 — First corpus tuning loop

Do this only after ingestion and evaluation exist.

Current status: the corpus harness now ingests all current `model_kicad_files/` sources under `kicad-cli 9.0.9`, materializes fixture-local embedded symbols for evaluation, and runs full-corpus `model-corpus evaluate` end-to-end. The repo-wide validation gate is green again after the latest routing/pin-direction fixes, MCP2551 passes electrical equivalence after fixing compact decoupling rail clearance plus the repo-local `power:GND` symbol definition, and MAX232 now passes after switching aligned two-pin fallback GND clusters from a shared lane to direct per-pin GND symbol attachments. The active work is now the remaining generic tuning pass for the MicroSD fixture.

### 11.1 Start with MCP2551 fixture
- [x] Ingest `mcp2551-can-transciever.kicad_sch`.
- [x] Ensure `circuit_ir.json` exists when KiCad CLI is available.
- [x] Run evaluation.
- [x] Read `actionable_failures.md`.
- [x] Identify one generic generator improvement.
- [x] Modify generator code without fixture-specific checks.
- [x] Add regression test.
- [x] Re-run evaluation.

Likely generic rules:

- [ ] Classify transceiver/interface ICs as central blocks.
- [ ] Place logic/MCU-side signals left of interface IC.
- [ ] Place field/bus connector side right of interface IC.
- [ ] Keep termination/protection/support passives near the side they serve.

### 11.2 Then use MAX232 fixture
- [x] Ingest/evaluate `dual-ttl-uart-to-rs232-max232-reference-design.kicad_sch`.
- [x] Add generic interface/charge-pump capacitor locality rules if failures show that need.
- [x] Do not hard-code MAX232-specific filenames.

### 11.3 Then use MicroSD fixture
- [ ] Ingest/evaluate `microsd-card-in-spi-mode-with-hotswap-support.kicad_sch`.
- [ ] Add generic connector + bus signal label strategy improvements if failures show that need.
- [ ] Keep pullups/decoupling/support parts near relevant connector/IC pins.

---

## Phase 12 — Documentation

### 12.1 Add corpus README
- [x] Add `model_kicad_files/README.md`.
- [x] Explain that files here are raw source examples.
- [x] Explain license/source metadata expectations.
- [x] Explain how to ingest files.
- [x] Explain not to hand-edit generated fixture artifacts except metadata notes if needed.

### 12.2 Add model corpus docs
- [x] Add `docs/MODEL_KICAD_CORPUS.md`.
- [x] Document the workflow:
  - [x] add `.kicad_sch`
  - [x] run ingest
  - [x] run evaluate
  - [x] read reports
  - [x] improve generator rules
  - [x] re-run tests
- [x] Explain why this is not ML training.
- [x] Explain electrical equivalence vs schematic quality similarity.
- [x] Explain partial fixtures when KiCad CLI is unavailable.

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

- [x] `uv run python -m kicad_pcb.cli model-corpus ingest --source-dir model_kicad_files --out-dir tests/fixtures/model_corpus --refresh` works.
- [x] `tests/fixtures/model_corpus/` contains one directory per parseable source schematic.
- [x] Every fixture has `metadata.json`.
- [x] Every parseable fixture has `source_layout_features.json`.
- [x] Fixtures with successful KiCad XML export have `circuit_ir.json`.
- [x] `uv run python -m kicad_pcb.cli model-corpus list --corpus-dir tests/fixtures/model_corpus` works.
- [x] `uv run python -m kicad_pcb.cli model-corpus evaluate --corpus-dir tests/fixtures/model_corpus --out-dir code_review/generated/model_eval` works for fixtures with `circuit_ir.json`. Remaining work is fixture tuning; the harness itself now completes and writes reports.
- [x] Evaluation writes aggregate `summary.md`.
- [x] Per-fixture reports include actionable failures.
- [x] Existing readability/golden tests still pass.

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
