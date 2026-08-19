# Phase K / Phase N Reconciliation — 2026-08-18

Repository: `ekkus93/openclaw_kicad_pcb`
Branch: `webapp`
Governing TODO: `docs/KICAD_SCHEMATIC_ELECTRICAL_INVARIANCE_AI_VISUAL_REFINEMENT_TODO_2026-08-10.md`

This addendum records the authoritative Phase K and Phase N closure disposition as implementation and evidence progress beyond the older governing TODO. It exists separately because preserving the large original tracker without risky cosmetic checkbox churn takes precedence over rewriting historical status in place.

## Phase K — closed

- [x] Real-fixture model-directed `apply_once` integration exists in `tests/integration/test_refinement_phase_k_apply_once_real_kicad.py`.
- [x] Candidate electrical/structural validation compares against the accepted baseline without allowing new or increased blocking ERC/lint findings.
- [x] Rejected candidates preserve the accepted schematic.
- [x] The accepted-baseline ERC no-regression fix landed in `7a54f52cf96db7a721e3a57149ec3a29d5622874`.
- [x] Follow-up formatting fixes landed in `4be7171ed49c40db5f318bef0a1610876d4fe723` and `22ae0cbb1e3761c89d5f1f4b9e9c6040bb4fff52`.
- [x] Permanent CI was confirmed green after the Phase K fixes.

Disposition: the Phase K hard exit is complete.

## Phase N1 — fixture set complete

The evaluation corpus under `tests/fixtures/refinement/evaluation_corpus/` contains 12 electrically valid poor-layout fixtures, within the required 10–20 range.

Covered categories:

- [x] crowded layout;
- [x] excessive spread;
- [x] poor left-to-right signal flow;
- [x] support passives visually detached from a primary IC;
- [x] avoidable wire crossings;
- [x] excessive wire bends;
- [x] inconsistent repeated blocks;
- [x] awkward connector orientation;
- [x] poor power-symbol organization;
- [x] label/readability collisions.

Required special cases:

- [x] multi-unit symbol example;
- [x] unnamed-net example;
- [x] explicit no-connect example.

Coverage is enforced by the refinement evaluation corpus tests.

Disposition: Phase N1 is complete.

## Phase N2 — baseline capture complete

For every corpus fixture, the implementation and tests cover:

- [x] authoritative Circuit IR validation;
- [x] real-KiCad electrical baseline verification;
- [x] deterministic baseline metrics;
- [x] deterministic baseline render capture;
- [x] fixture category and known visual-defect metadata without hard-coded production repairs.

Implementation and evidence include:

- `src/kicad_pcb/evaluation/refinement_baseline.py`
- `tests/unit/test_refinement_evaluation_baseline.py`
- `tests/unit/test_refinement_evaluation_corpus_baselines.py`
- `tests/integration/test_refinement_phase_n_baseline_capture_real_kicad.py`

Disposition: Phase N2 is complete.

## Phase N3 — infrastructure complete; live corpus execution open

### N3.1 — per-fixture evaluation runner complete

The evaluation runner in `src/kicad_pcb_web/services/refinement_evaluation.py` guarantees:

- [x] `analyze`, `plan`, one-round `apply_once`, and bounded `refine` each start from an independent copy of identical baseline schematic bytes;
- [x] each stage is hash-bound to the expected isolated artifact;
- [x] apply/refine final accepted artifacts receive fresh electrical-invariance verification;
- [x] final deterministic metrics are captured;
- [x] before/after renders are retained using bundle-relative paths;
- [x] operation/rejection summaries are retained;
- [x] refine stop reason is retained;
- [x] evidence is published atomically per fixture;
- [x] existing fixture output is never silently overwritten;
- [x] partial output/work directories are removed on failure;
- [x] temporary absolute paths are not emitted into the published manifest.

Disposition: N3.1 is complete.

### N3.2 — corpus harness, CLI, and regression coverage complete

The corpus-level runner and CLI now exist:

- `src/kicad_pcb_web/services/refinement_evaluation_corpus.py`
- `src/kicad_pcb_web/refinement_evaluation_cli.py`
- console entry point: `kicad-refine-eval`

The corpus implementation performs full preflight before execution, binds all fixtures to the Phase N2 baseline expectations, preserves manifest ordering, attributes fixture failures, refuses output collisions, and publishes the aggregate summary only after full success.

Focused N3 corpus/CLI regression coverage is present under `tests/web/`.

Permanent CI run `32289721240` was verified green on 2026-08-19 across:

- [x] Python lint, format, mypy, unit, and web tests;
- [x] frontend lint, unit tests, and production build;
- [x] KiCad integration tests;
- [x] wheel/sdist package smoke;
- [x] Playwright browser smoke.

Disposition: N3.2 is complete.

### N3.3a — reproducible automated local live-evaluation workflow ready

The workflow exists at `.github/workflows/refinement-live-evaluation.yml` and supports both an explicit automated trigger branch and manual fallback.

Selected automated environment:

- provider: `ollama`
- model: `qwen3-vl:8b`
- base URL: `http://127.0.0.1:11434`
- runner: `self-hosted`
- external API key: none

Workflow guarantees:

- [x] ordinary `webapp` pushes do not launch the live experiment;
- [x] moving `n3-eval-run` to an evaluated `webapp` SHA launches the experiment;
- [x] manual dispatch remains available as a fallback;
- [x] preprovisioned KiCad 9/Graphviz verification with no automatic `apt` installation;
- [x] Ollama `/api/tags` preflight verifies the selected model before schematic work;
- [x] persistent project-specific uv download cache;
- [x] fixed canonical N3 bounds for reproducibility;
- [x] unique output/work roots per GitHub run attempt;
- [x] final hard validation of exactly 12 completed fixture bundles;
- [x] `always()` artifact upload for completed evidence and the evaluation log;
- [x] normal CI guards the workflow's automated trigger, selected local model, tool provisioning policy, and evidence upload.

The first automated run (`32301536216`) proved the runner/tool/Ollama preflight path but failed during the first corpus fixture. Follow-up hardening now:

- [x] explicitly sends `think: false` for Ollama structured JSON requests so thinking-capable Qwen3-VL variants return schema-bound final content without a separate reasoning channel consuming the structured call;
- [x] retains ordinary free-text Ollama behavior unchanged;
- [x] exposes only `fixture_id`, `cause_code`, and `cause_type` for corpus fixture failures in CLI JSON diagnostics;
- [x] adds focused regression tests for both behaviors.

Operational details are documented in `docs/PHASE_N3_LIVE_EVALUATION.md`.

Disposition: N3.3a is implemented. The live experiment itself is still open until all 12 real fixture bundles complete successfully.

### N3.3 — experiment outputs still required for every fixture

- [ ] analyze output;
- [ ] plan output;
- [ ] one-round apply result;
- [ ] bounded refine result where applicable;
- [ ] final electrical equivalence;
- [ ] final deterministic metrics;
- [ ] before/after render;
- [ ] operations/rejections;
- [ ] stop reason.

Disposition: Phase N3 remains open until the real/model-directed evaluation is executed across all 12 fixtures and its evidence bundles are retained and reviewed.

## Next work

1. Run the corrected automated `ollama` / `qwen3-vl:8b` corpus experiment on the self-hosted runner.
2. If any fixture still fails, use the allowlisted `fixture_id` / `cause_code` / `cause_type` diagnostic tuple to correct the real failure without exposing raw model content or secrets.
3. Retain the resulting `phase-n3-live-evidence-<run-id>-<run-attempt>` artifact and reconcile every fixture against the required N3 evidence list.
4. Proceed to Phase N4 human visual disposition and Phase N5 heuristic-discovery reporting; do not auto-promote experimental observations into production heuristics.
