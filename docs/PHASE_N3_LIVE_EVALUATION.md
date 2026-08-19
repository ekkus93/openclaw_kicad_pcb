# Phase N3 Live Evaluation

This document records the reproducible Phase N3.3 live/model-directed corpus experiment.

## Selected local environment

The current experiment target is a local Ollama service on the self-hosted runner:

- provider: `ollama`
- model: `qwen3-vl:8b`
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
- `qwen3-vl:8b` installed in Ollama

The workflow queries Ollama `/api/tags` before corpus execution and fails before schematic work if the selected model is unavailable.

Python 3.11 and uv are resolved through the existing Actions setup tools, while uv package downloads reuse the persistent project cache:

`~/.cache/openclaw-kicad-pcb/uv/refinement-live-evaluation`

## Canonical experiment bounds

The N3.3 workflow pins the experiment to these bounds rather than inheriting mutable defaults:

- maximum rounds: `3`
- maximum operations per round: `4`
- maximum total accepted operations: `8`
- maximum candidate rejections: `2`
- maximum critic repairs: `0`
- maximum planner repairs: `0`
- per-request LLM timeout: `180` seconds
- workflow timeout: `240` minutes

These values are recorded again in the generated corpus summary.

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

The workflow performs a final hard check that the summary is Phase `N3`, has status `completed`, contains exactly 12 unique fixture IDs, and has a directory for every fixture.

Whether the evaluation succeeds or fails, the workflow attempts to upload completed atomic fixture evidence bundles and `evaluation.log`.

Artifact name:

`phase-n3-live-evidence-<run-id>-<run-attempt>`

The workflow requests 30-day retention; repository-level retention policy may cap the actual artifact lifetime to a smaller value.

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
