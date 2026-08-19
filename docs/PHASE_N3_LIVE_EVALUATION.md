# Phase N3 Live Evaluation

This document records the reproducible Phase N3.3 live/model-directed corpus experiment.

## Selected experiment environment

The current selected N3.3 environment is local Ollama on the self-hosted runner:

- provider: `ollama`
- model: `qwen3-vl:8b`
- base URL: `http://127.0.0.1:11434`
- API key: not required

The repository workflow is pre-filled with these values. The Ollama client uses the native `/api/chat` endpoint and the workflow performs a cheap `/api/tags` preflight to verify that the exact selected model is installed before any fixture evaluation starts.

## Workflow

GitHub Actions workflow:

- `.github/workflows/refinement-live-evaluation.yml`
- display name: **Phase N3 Live Evaluation**
- trigger: `workflow_dispatch` only
- runner: `self-hosted`
- permitted execution branch: `webapp` only
- corpus: all 12 fixtures in `tests/fixtures/refinement/evaluation_corpus/manifest.json`

The workflow deliberately has no `push`, `pull_request`, or scheduled trigger because it performs real model calls and can consume substantial local compute.

## Provider configuration

The workflow exposes these dispatch inputs:

- `provider`: `ollama`, `llama_server`, or `openai`; current default is `ollama`
- `model`: explicit provider model identifier; current default is `qwen3-vl:8b`
- `base_url`: required for `llama_server` and `ollama`; current default is `http://127.0.0.1:11434`
- `confirm_12_fixture_live_run`: must be enabled before the experiment is allowed to start

All three enabled provider families have explicit image-input capability contracts in production code. The workflow also sets `KICAD_PCB_WEB_LLM_VISION_ENABLED=true`; model names are not used to infer vision capability.

For the selected local Ollama configuration, no API-key secret is required.

For `openai`, configure this repository Actions secret before dispatch:

- `KICAD_PCB_REFINEMENT_LLM_API_KEY`

The secret is scoped only to the provider preflight and actual evaluation command. It is not exposed to checkout, Python setup, uv setup, or artifact-upload actions.

For `llama_server` and `ollama`, no API-key secret is required by the current provider contract, but a reachable `base_url` must be supplied.

## Ollama preflight

When `provider=ollama`, the workflow queries:

`<base_url>/api/tags`

before running the corpus. The preflight fails if:

- Ollama is unreachable;
- the response is not valid JSON in the expected native Ollama shape;
- the exact selected model is not installed.

This avoids spending time on KiCad/model evaluation when the runner-local inference service is not ready.

## Preprovisioned self-hosted runner requirements

The workflow reuses the persistent self-hosted runner and fails fast rather than installing heavyweight OS packages. The runner must already provide:

- Graphviz `dot`
- KiCad 9 `kicad-cli`
- KiCad symbol libraries at `/usr/share/kicad/symbols`
- Ollama reachable at the configured base URL for an Ollama run
- selected model already pulled into Ollama

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

## Evidence layout

Each GitHub run/attempt receives a unique temporary root:

`$RUNNER_TEMP/phase-n3-live-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT`

The corpus runner writes atomic per-fixture bundles under `evidence/` and publishes `evidence/summary.json` only after the selected corpus completes successfully.

The workflow performs a final hard check that the summary is Phase `N3`, has status `completed`, contains exactly 12 unique fixture IDs, and has a directory for every fixture.

Whether the evaluation succeeds or fails, the workflow attempts to upload:

- all completed atomic fixture evidence bundles;
- `summary.json` when the full corpus completed;
- `evaluation.log`.

Artifact name:

`phase-n3-live-evidence-<run-id>-<run-attempt>`

Artifact retention: 30 days.

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

Creating and configuring this workflow does **not** close Phase N3.3. Phase N3.3 closes only after a real model-directed run across all 12 fixtures completes, the generated artifact is retained, and the evidence is reconciled for Phase N4 human visual disposition.
