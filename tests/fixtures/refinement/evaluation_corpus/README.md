# Phase N refinement evaluation corpus

This directory defines the **Phase N1 experimental fixture set** for schematic visual refinement.

The fixture set intentionally references the repository's normalized real-schematic model corpus instead of copying those large schematic assets. Each manifest entry resolves its immutable inputs as:

- `tests/fixtures/model_corpus/<source_fixture_id>/source_normalized.kicad_sch`
- `tests/fixtures/model_corpus/<source_fixture_id>/circuit_ir.json`
- `tests/fixtures/model_corpus/<source_fixture_id>/metadata.json`
- `tests/fixtures/model_corpus/<source_fixture_id>/source_layout_features.json`

The manifest records **observed visual defects and coverage categories only**. It does not prescribe a repair, expected operation type, target coordinates, or expected model response. That separation is deliberate so later Phase N evaluation measures the production critic/planner rather than encoding the answer into the fixture definition.

## N1 scope

The selected set contains 13 fixtures, within the TODO target of approximately 10-20. Collectively they cover:

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

`tests/unit/test_refinement_evaluation_corpus.py` keeps the manifest honest by validating the source artifacts, Circuit IRs, layout-warning evidence, required category coverage, and the three special-case claims.

## Deliberate boundary with N2

N1 establishes the fixture population and coverage. It does **not** claim the Phase N2 real-KiCad baseline gate. N2 must still run every fixture through the real KiCad electrical baseline path, record deterministic metrics and baseline renders, and persist those results as evaluation evidence.
