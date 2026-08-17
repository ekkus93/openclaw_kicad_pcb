# Phase N refinement evaluation corpus

This directory defines the **Phase N experimental fixture set and baseline expectations** for schematic visual refinement.

The fixture set intentionally references the repository's normalized real-schematic model corpus instead of copying those large schematic assets. Each manifest entry resolves its immutable inputs as:

- `tests/fixtures/model_corpus/<source_fixture_id>/source_normalized.kicad_sch`
- `tests/fixtures/model_corpus/<source_fixture_id>/circuit_ir.json`
- `tests/fixtures/model_corpus/<source_fixture_id>/metadata.json`
- `tests/fixtures/model_corpus/<source_fixture_id>/source_layout_features.json`

The manifest records **observed visual defects and coverage categories only**. It does not prescribe a repair, expected operation type, target coordinates, or expected model response. That separation is deliberate so later Phase N evaluation measures the production critic/planner rather than encoding the answer into the fixture definition.

## N1 scope

The selected set contains 12 fixtures, within the TODO target of approximately 10-20. Collectively they cover:

- crowded layout;
- excessive spread;
- poor left-to-right signal flow;
- support passives visually detached from a primary IC;
- avoidable wire crossings;
- excessive wire bends;
- inconsistent repeated blocks;
- awkward connector orientation;
- poor power-symbol organization;
- label/readability collisions;
- multi-unit symbols;
- KiCad-generated unnamed nets such as `Net-(...)`;
- explicit no-connect markers.

`tests/unit/test_refinement_evaluation_corpus.py` keeps the N1 manifest honest by validating the source artifacts, Circuit IRs, layout-warning evidence, required category coverage, and the three special-case claims.

The original 13-fixture N1 set included the RP2040 core fixture. N2 baseline work exposed that its normalized artifact cannot currently produce refinement metrics because one `H3` pin geometry is unresolved. The RP2040 entry was removed instead of weakening the deterministic metric gate. The remaining STM32 fixture continues to provide explicit-no-connect coverage, and all N1 category/special-case requirements remain covered.

## N2 baseline capture

`baseline_expectations.json` records source-schematic hashes, authoritative electrical-fingerprint hashes, page geometry, and the exact deterministic refinement metrics for every N1 fixture. `tests/unit/test_refinement_evaluation_corpus_baselines.py` recomputes those values from the source corpus so stale or non-metric-compatible fixtures fail before real-KiCad evaluation begins.

`src/kicad_pcb/evaluation/refinement_baseline.py` captures one baseline as an atomic, path-sanitized evidence bundle containing:

- `manifest.json` with fixture/category/known-defect metadata and render metadata;
- `electrical_baseline.json` binding the authoritative Circuit IR to the source schematic;
- `electrical.json` with the real-KiCad invariance result;
- `metrics.json` with the deterministic baseline metrics;
- `render/baseline.svg` and `render/baseline.png`;
- any deterministic review-region SVG/PNG artifacts under `render/review-regions/`.

`tests/integration/test_refinement_phase_n_baseline_capture_real_kicad.py` runs that capture path for every fixture under the normal `requires_kicad` integration job. The N2 tracker gate must remain open until that real-KiCad run is reported green.

## Deliberate boundary with N3

N2 records immutable baseline evidence only. It does **not** prescribe expected repairs or evaluate model output. N3 will consume these baseline bindings and add critic analysis, repair plans, apply/refine results, final electrical equivalence, final metrics/renders, operations/rejections, and stop reasons.
