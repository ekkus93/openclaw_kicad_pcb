# Phase N3 Live Evaluation

This document records the reproducible Phase N3.3 live/model-directed corpus experiment.

## Selected local environment

The current experiment target is a local Ollama service on the self-hosted runner:

- provider: `ollama`
- model: `qwen3-vl:8b-instruct-n3-64k`
- base URL: `http://127.0.0.1:11434`
- external API key: not required

The production Ollama client uses the native `/api/chat` endpoint and sends images with Ollama's native `images` message field. Structured JSON requests explicitly send `think: false`; the application consumes the schema-bound final `message.content` and does not consume a separate reasoning channel.

## Workflow

GitHub Actions workflow:

- `.github/workflows/refinement-live-evaluation.yml`
- display name: **Phase N3 Live Evaluation**
- triggers: explicit `n3-eval-run` branch push or manual `workflow_dispatch`
- runner: `self-hosted`
- automated provider/model/base URL: the selected local environment above
- manual dispatch remains available as a fallback
- corpus: all 12 fixtures in `tests/fixtures/refinement/evaluation_corpus/manifest.json`

Ordinary `webapp` pushes do not launch the live experiment. To launch an automated experiment, advance `n3-eval-run` to the `webapp` SHA that should be evaluated.

## Provider configuration

The manual fallback exposes these dispatch inputs:

- `provider`: `ollama`, `llama_server`, or `openai`
- `model`: explicit provider model identifier
- `base_url`: required for `llama_server` and `ollama`; optional for an OpenAI-compatible override
- `confirm_12_fixture_live_run`: must be enabled before a manual experiment is allowed to start

All three enabled provider families have explicit image-input capability contracts in production code. The workflow sets `KICAD_PCB_WEB_LLM_VISION_ENABLED=true`; model names are not used to infer image capability.

For `openai`, configure repository Actions secret `KICAD_PCB_REFINEMENT_LLM_API_KEY` before a manual OpenAI dispatch. For the selected local Ollama experiment, no API-key secret is required.

## Preprovisioned self-hosted runner requirements

The workflow reuses the persistent self-hosted runner and fails fast rather than installing heavyweight OS packages. The runner must already provide:

- Graphviz `dot`
- KiCad 9 `kicad-cli`
- KiCad symbol libraries at `/usr/share/kicad/symbols`
- Ollama reachable at `http://127.0.0.1:11434`
- source model `qwen3-vl:8b-instruct` available to Ollama; the preflight provisions `qwen3-vl:8b-instruct-n3-64k` with `num_ctx=65536`

The workflow queries Ollama `/api/tags` before corpus execution and fails before schematic work if the selected model is unavailable.

Python 3.11 and uv are resolved through the existing Actions setup tools, while uv package downloads reuse the persistent project cache:

`~/.cache/openclaw-kicad-pcb/uv/refinement-live-evaluation`

## Canonical experiment bounds

The N3.3 workflow pins the experiment to these bounds rather than inheriting mutable defaults:

- maximum rounds: `3`
- maximum operations per round: `4`
- maximum total accepted operations: `8`
- maximum candidate rejections: `2`
- maximum critic repairs: `1`
- maximum planner repairs: `1`
- per-request LLM timeout: `300` seconds
- maximum generated tokens per LLM response: `4096`
- workflow timeout: `240` minutes

A critic or planner structured response that fails JSON/schema validation may receive exactly one schema-correction request. The correction request retains the same strict response schema and includes the validation failure needed to repair the serialization. If the corrected response is still invalid, the call fails closed with `LLM_INVALID_STRUCTURED_OUTPUT`. This repair allowance does not retry ambiguous transport failures, relax semantic validation, or permit unbounded model calls.

The transport timeout remains the validated production maximum of 300 seconds. The explicit 4096-token generation cap is essential for the local Ollama experiment: it maps to Ollama `options.num_predict=4096`, preventing an otherwise unbounded structured critic/planner generation from occupying the entire non-streaming request timeout.

Run `32363895399` attempt 2 demonstrated this requirement: the Ollama capability preflight completed successfully, but the first real fixture (`n1-crowded-power-regulator`) had no configured output-token ceiling and reached the 300-second transport timeout before Ollama returned a response. Ambiguous transport failures remain non-retryable; the token cap bounds generation rather than replaying the request or weakening the timeout contract.

## Diagnostics

The CLI keeps normal production errors sanitized. For `REFINEMENT_EVALUATION_FIXTURE_FAILED`, it additionally emits only these safe diagnostic fields when available:

- `fixture_id`
- `cause_code`
- `cause_type`

Raw provider payloads, prompts, model responses, temporary paths, and secrets are not included in this CLI diagnostic tuple.

## Evidence layout

Each GitHub run/attempt receives a unique temporary root:

`$RUNNER_TEMP/phase-n3-live-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT`

The corpus runner writes atomic per-fixture bundles under `evidence/` and publishes `evidence/summary.json` only after the selected corpus completes successfully.

The workflow performs its final acceptance through the reusable `kicad-refine-eval-validate` command. The validator independently binds the artifact to the expected implementation SHA, provider, model, source-manifest hash, baseline-expectations hash, canonical experiment bounds, and exact 12-fixture manifest order. It then verifies each fixture bundle, including baseline/final schematic hashes, analyze/plan metric bindings, render bytes and recorded render hashes, apply/refine result bindings, final electrical status, final metric hashes, operations evidence, and stop reason.

On success the workflow writes `acceptance.json` and then generates `n4-review-packet.json`, an objective Phase N4 input containing render paths, baseline/final metrics and deltas, critic/plan output, operations/rejections, refine summary data, and stop reasons. Whether the evaluation succeeds or fails, the workflow attempts to upload completed atomic fixture evidence bundles, `evaluation.log`, and any completed acceptance/review reports.

Artifact name:

`phase-n3-live-evidence-<run-id>-<run-attempt>`

The workflow requests 30-day retention; repository-level retention policy may cap the actual artifact lifetime to a smaller value.

## Post-hoc acceptance of an earlier implementation SHA

The acceptance validator is intentionally independent of the workflow revision that invokes it. This permits a completed artifact from an earlier implementation SHA to be validated with the stronger current acceptance code without repeating the expensive model run, provided that artifact was produced under the currently accepted canonical experiment bounds.

For the Phase N3 run launched from `805e1226b3f97e28005eefbf5a0461a31da6d4bc`, use the exact full implementation SHA recorded in `summary.json`:

```bash
uv run kicad-refine-eval-validate \
  --evidence-root /path/to/unpacked/evidence \
  --implementation-sha 805e1226b3f97e28005eefbf5a0461a31da6d4bc \
  --provider ollama \
  --model qwen3-vl:8b-instruct-n3-64k \
  --manifest tests/fixtures/refinement/evaluation_corpus/manifest.json \
  --expectations tests/fixtures/refinement/evaluation_corpus/baseline_expectations.json \
  --report phase-n3-acceptance.json
```

Do not substitute a branch-tip SHA for `--implementation-sha`; it must exactly match the implementation SHA recorded by the artifact. A successful `phase-n3-acceptance.json` is the machine-readable binding used to begin Phase N4.

After acceptance, prepare the objective N4 packet without making any model calls or modifying the evidence:

```bash
uv run kicad-refine-eval-review \
  --evidence-root /path/to/unpacked/evidence \
  --implementation-sha 805e1226b3f97e28005eefbf5a0461a31da6d4bc \
  --provider ollama \
  --model qwen3-vl:8b-instruct-n3-64k \
  --manifest tests/fixtures/refinement/evaluation_corpus/manifest.json \
  --expectations tests/fixtures/refinement/evaluation_corpus/baseline_expectations.json \
  --output phase-n4-review-packet.json
```

The review-packet command re-runs the full N3 acceptance validator first. It does not assign `improved`, `neutral`, or `worse`; those remain human-review decisions.

## Expected evidence per completed fixture

The underlying Phase N3 evaluation runner retains the evidence needed for subsequent Phase N4/N5 review, including:

- analyze output;
- plan output;
- one-round `apply_once` result;
- bounded refinement result;
- post-edit electrical-invariance result;
- deterministic metrics;
- before/after renders;
- accepted operations and rejected operations;
- refinement stop reason.

## Completion rule

Creating and automating this workflow does **not** close Phase N3.3. Phase N3.3 closes only after a real model-directed run across all 12 fixtures completes, the generated artifact is retained, and the evidence is reconciled for Phase N4 human visual disposition.
