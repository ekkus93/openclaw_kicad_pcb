# KiCad PCB Web App

[![CI](https://github.com/ekkus93/openclaw_kicad_pcb/actions/workflows/ci.yml/badge.svg)](https://github.com/ekkus93/openclaw_kicad_pcb/actions/workflows/ci.yml)

This branch provides a Python FastAPI web app for deterministic KiCad project
generation from Circuit IR JSON, plus an optional local-first LLM wizard that
helps draft specs before handing off to the same deterministic pipeline.

The web app does not require OpenClaw or any external AI service for its core
generation path. Users can either provide explicit Circuit IR JSON directly or
use the `/wizard` flow to iterate on a spec, approve it, generate Circuit IR,
and then launch project generation.

The previous OpenClaw skill files are archived under
`legacy/openclaw-skill/` for reference only.

The repository still includes the underlying deterministic KiCad generation
engine, an archived CLI workflow, strong linting/validation, and a full
unit/integration test suite.

## Features

- **AST-based editing** — no regex hacks; all mutations go through `SchematicDoc`/`PcbDoc` wrappers
- **Transactional writes** — temp-write → validate → atomic replace; originals never corrupted
- **Structural linting** — 18 built-in rules (SCH/PCB 001–009) enforced before every write
- **Circuit pattern library** — resistor divider, LED+resistor, connector breakout, decoupling cap
- **Preflight checks** — duplicate refs, net name validation, symbol accessibility, footprint requirements
- **KiCad CLI integration** — ERC/DRC/export via `kicad-cli` with version compatibility layer
- **Local web app** — FastAPI-served React + TypeScript SPA for validating Circuit IR, running the wizard, generating projects, and downloading artifacts
- **Circuit IR pipeline** — deterministic Spec → IR → KiCad schematic generation (`new-from-netlist`, `apply-netlist`)
- **JSON output** — all commands support `--json` for machine-friendly automation
- **Dry-run mode** — validate without committing (`--dry-run`)

## Archived CLI quick start

```bash
uv sync --extra dev

# Create a new project
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py new MyProject

# Add a resistor divider pattern
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py open MyProject/
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py apply-pattern \
  --pattern resistor-divider \
  --r1 R1 --r2 R2 \
    --vin-net VIN --vout-net VOUT --gnd-net GND

# Lint the generated schematic
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py lint-sch MyProject/MyProject.kicad_sch

# Check environment
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py doctor
```

## Web App

Install the web dependencies and start the local FastAPI app:

```bash
uv sync --extra dev --extra web
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload
```

The browser UI is a bundled React + TypeScript single-page app styled with
Tailwind utilities and served by FastAPI shell routes. The primary browser
routes are:

- `/` — overview and direct Circuit IR tools
- `/wizard` — wizard landing page
- `/jobs/{job_id}` — generated job detail page

Default runtime settings:

- Bind host: `127.0.0.1`
- Port: `8000`
- Data dir: `./data`
- Jobs dir: `./data/jobs`
- Default validation mode for web job generation: `internal`

Override the data directory with:

```bash
export KICAD_PCB_WEB_DATA_DIR=/path/to/data
```

You can also keep web-app runtime settings in a local TOML config file. By
default the app will read `./kicad_pcb_web.toml` when present, or you can point
to another file with `KICAD_PCB_WEB_CONFIG_FILE`:

```toml
[web]
data_dir = "./data"
default_host = "127.0.0.1"
default_port = 8000
mutation_lock_timeout_s = 2.0

[llm]
provider = "disabled" # or: openai, ollama, llama_server
model = "gpt-4.1"
base_url = "https://api.openai.com/v1"
api_key = "replace-me"
timeout_s = 60
temperature = 0.2
max_tokens = 4096
system_prompt_version = "v1"
spec_max_repair_rounds = 2
ir_max_repair_rounds = 2
enable_streaming = false
request_log_redaction = true
network_probe_enabled = false
debug_artifact_capture = false
```

Environment variables still override config-file values when both are set.
The same LLM settings drive the `/wizard` flow when a provider is enabled.

Provider expectations:

- `disabled`: no provider client is constructed.
- `openai`: requires `model` and `api_key`; `base_url` is optional.
- `ollama`: requires `model` and `base_url`.
- `llama_server`: requires `model` and `base_url`.

Invalid provider configuration now fails fast when the app loads settings.

Generated web jobs are stored under:

```text
data/jobs/<job_id>/
```

Each job keeps its input, generated project, private canonical job metadata, and
downloadable artifacts inside that directory. `job.json` is authoritative private
server state and is written with atomic replacement under a bounded cross-process
lock. The web UI and API expose only curated downloads from the job's `artifacts/`
directory.

The web app defaults to `internal` validation for job generation. Optional KiCad
CLI validation is available only when `kicad-cli` is installed and a request
explicitly asks for `validation="kicad"`.

## LLM Wizard

The web app now includes a local-first LLM-assisted wizard with a start page at
`/wizard`.

The routed workflow is step-driven:

1. `Describe Circuit`
2. `Review Spec`
3. `Review Circuit IR`
4. `Generate Project`

Route family:

- `/wizard` — start page for creating a new session
- `/wizard/{session_id}` — redirector to the canonical active step
- `/wizard/{session_id}/describe`
- `/wizard/{session_id}/spec`
- `/wizard/{session_id}/ir`
- `/wizard/{session_id}/generate`

Supported flow:

```text
conversation -> circuit spec -> approved spec -> Circuit IR JSON -> validate/fix -> generate project
```

The LLM wizard does not write KiCad files directly. All schematic generation
still goes through the same deterministic Circuit IR validation and project
generation path used by the direct JSON workflow.

The server is authoritative for route access. Illegal deep links redirect back
to the blocking step instead of rendering an incomplete future page.

Supported provider modes:

- `disabled`
- `openai`
- `ollama`
- `llama_server`

Security boundary notes:

- Provider credentials stay server-side in the config/env layer.
- The local web app is still intended for local/internal use.
- If the app is ever exposed remotely, add authentication and request isolation
  at the API boundary before exposing `/api/wizard/*` routes.

The wizard stores file-backed sessions under the web data directory. Each session's
`wizard.json` is the sole authoritative record and is committed with atomic
replacement under a bounded cross-process mutation lock. `spec.json` and
`circuit_ir.json` are derived convenience exports; readers must not use them to
reconstruct session state. The authoritative record persists:

- conversation transcript
- current spec draft
- approved spec state
- current Circuit IR draft
- latest generation job link

Concurrent mutations of the same session fail explicitly with HTTP 409 after the
configured `mutation_lock_timeout_s`; they are never applied without the lock.
Unexpected server failures are logged with a correlation ID and exposed through a
sanitized API error rather than raw exception text.

Invalidation rules for backward changes:

- Sending another conversation message clears spec approval, active Circuit IR,
  and the active generation result link.
- Sending a revision note from the spec step clears active Circuit IR and the
  active generation result link.
- Regenerating Circuit IR clears the active generation result link before the
  new IR becomes current.

The routed wizard keeps the current session summary and checkpoint guidance in a
dedicated side panel on larger screens, while smaller screens stack that panel
under the main step content.

The web app binds to `127.0.0.1` by default and is intended for local/internal use
in v1. Do not expose it publicly without adding authentication, isolation, and
additional sandboxing around user-supplied netlists and generated artifacts.

## Circuit IR pipeline

Circuit IR JSON is the canonical input format for both the web app and the
archived CLI workflow:

```
Circuit IR JSON → KiCad .kicad_sch
```

The deterministic Circuit IR generation path does not depend on LLMs. The web
app now optionally includes the local `/wizard` flow, but both the direct JSON
workflow and the wizard converge on the same validated Circuit IR pipeline.

Historical OpenClaw or other LLM-assisted workflows may still target this same
Circuit IR format, but they are optional producers for the pipeline rather than
alternate generation paths.

Users provide a **Circuit IR JSON** file describing components and net
connections. The deterministic engine compiles that IR into a KiCad schematic.

### Minimal Circuit IR example

```json
{
  "version": "1",
  "components": [
    { "ref": "R1", "symbol": "Device:R", "value": "10k", "footprint": "Resistor_SMD:R_0402" },
    { "ref": "C1", "symbol": "Device:C", "value": "100n" }
  ],
  "nets": [
    { "name": "VCC",  "pins": [{ "ref": "R1", "pin": "1" }] },
    { "name": "NODE", "pins": [{ "ref": "R1", "pin": "2" }, { "ref": "C1", "pin": "1" }] },
    { "name": "GND",  "pins": [{ "ref": "C1", "pin": "2" }] }
  ]
}
```

### Commands

**Create a new project from Circuit IR** (strict validation by default):
```bash
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py new-from-netlist \
    --name MyProject \
    --netlist circuit.json \
    --symbols-dir /path/to/symbols \
  --validate kicad      # default; requires kicad-cli
  # --validate internal # internal syntax+lint only; no kicad-cli required
```

`compile-netlist` is an alias for `new-from-netlist` with identical arguments.

**Apply Circuit IR to the current/open project**:
```bash
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py open MyProject/
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py apply-netlist \
    --netlist circuit.json \
    --symbols-dir /path/to/symbols \
    --force               # adopt schematic if not already OpenClaw-managed
    --dry-run             # validate without writing
```

**Inspect the current schematic**:
```bash
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py info-sch
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py info-sch --json   # machine-readable
```

### Ownership model

The tool uses a durable **ownership marker** (`OpenClaw:generated=v1`) stored as
an off-canvas text item in the root schematic. This lets the tool distinguish its
own generated content from user-authored content.

- If the marker is absent, `apply-netlist` **refuses** to modify the schematic
  (prevents accidental rewrites of manually authored files).
- Pass `--force` to adopt an existing schematic and insert the marker.
- New generation writes components, wires, and labels directly to the main
  `<name>.kicad_sch` file. The legacy `OpenClaw_Managed.kicad_sch` name remains
  recognized only for backward compatibility with older projects.

### Validation modes

| Mode       | Behaviour |
|------------|-----------|
| `internal` | Syntax check + 18 built-in lint rules; no `kicad-cli` required |
| `kicad`    | All internal checks **plus** `kicad-cli sch validate`; fails hard if `kicad-cli` is missing |

Default: `new-from-netlist` and `compile-netlist` use **`kicad`** (strict); `apply-netlist` uses **`internal`**.

## Validated generation pipeline

All supported schematic generation flows converge on the same validated path:

```text
Circuit IR JSON
  -> CircuitIR.load(...)
  -> validate_circuit_ir(...)
  -> validate_ir_symbols(...)
  -> advisory_warnings(...) / raise_for_blocking_advisories(...)
  -> mutate_and_validate_sch(...)
  -> validate_generated_schematic(...)
  -> OpenClaw_Warnings.json + optional debug dump
```

Supported entry points:

- `new-from-netlist`
- `compile-netlist` (alias of `new-from-netlist`)
- `apply-netlist`

Unsupported or legacy side formats are not alternate public generation pipelines.
They must be normalized into canonical Circuit IR before the validated path runs.

## Hard-fail invariants

These invariants are non-negotiable for supported generation:

- Every `(ref, pin)` belongs to exactly one canonical net.
- Every referenced symbol exists and every referenced pin is valid.
- Blocking domain advisories stop generation before artifact success is reported.
- Generated schematics must reparse through the project document model.
- Non-empty generated designs must contain real symbols and, when routing expected wires, real wires.
- Generated schematics must preserve declared pin-to-net bindings without missing, unexpected, or duplicated bindings.
- Generated artifacts must carry enough structure to be trusted as real KiCad schematics.

## Debugging generation failures

Use these commands when generation fails or a readability regression is suspected.

Validate the input IR without writing files:

```bash
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py validate-netlist \
    --netlist circuit.json \
    --symbols-dir tests/fixtures/symbols
```

Generate a new project and keep the structured debug dump:

```bash
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py new-from-netlist \
    --name DebugProject \
    --out-dir /tmp/openclaw-debug \
    --netlist circuit.json \
    --symbols-dir tests/fixtures/symbols \
  --validate internal \
    --debug-dump /tmp/openclaw-debug/OpenClaw_Debug.json
```

Inspect the generated warning sidecar and diagnostics:

```bash
cat /tmp/openclaw-debug/DebugProject/OpenClaw_Warnings.json
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py info-sch --json
```

When a run fails, inspect these artifacts in order:

1. The source Circuit IR JSON.
2. `validate-netlist` output and advisory/blocking codes.
3. The generated main schematic: `DebugProject.kicad_sch`.
4. The post-generation diagnostics in `OpenClaw_Warnings.json`.
5. The optional debug dump stage markers and routing/layout summaries in `OpenClaw_Debug.json`.

## Schematic layout engine (Graphviz)

When generating schematics from a Circuit IR the tool runs a **graph layout
engine** to place components so that signals flow left → right. The current
implementation uses [Graphviz `dot`](https://graphviz.org) for schematic
placement. There is no heuristic fallback mode documented or intended here:
if Graphviz is unavailable, layout should be treated as unavailable rather than
silently downgraded.

### Installing Graphviz

| Platform | Command |
|----------|---------|
| Debian / Ubuntu | `sudo apt-get install graphviz` |
| macOS (Homebrew) | `brew install graphviz` |
| Windows | Installer at <https://graphviz.org/download/> |

### Overriding the `dot` path

If you need a specific `dot` binary set the `GRAPHVIZ_DOT` environment
variable to its absolute path before running any command:

```bash
export GRAPHVIZ_DOT=/opt/local/bin/dot
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py new-from-netlist --netlist circuit.json ...
```

The discovery order is:

1. Package-local `kicad_pcb/graphviz_layout/bin/dot` if present
2. `GRAPHVIZ_DOT` environment variable
3. System `PATH` (`shutil.which("dot")`)

Current releases do not ship a package-local Graphviz binary, so in normal use
the active lookup path is `GRAPHVIZ_DOT` first and then the system `PATH`.

Run `uv run python legacy/openclaw-skill/scripts/kicad_pcb.py doctor` to see which binary is active and
its version.

### Layout mode

The README previously described heuristic and multi-engine fallback behavior.
That was incorrect. The intended documented behavior is Graphviz-based layout;
if Graphviz is unavailable, fix the environment or code path rather than
falling back to a heuristic placer.

### Licensing

Graphviz is an independent open-source tool licensed under the
[Eclipse Public License 1.0](https://www.eclipse.org/legal/epl-v10.html).
This package does **not** bundle or redistribute any Graphviz binary.
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for full details.

## Packaging and frontend builds

The production React bundle under `src/kicad_pcb_web/static/spa/` is committed and
included in both wheels and source distributions. A normal wheel installation does
not invoke Node or download frontend packages.

After changing `frontend/src/`, rebuild and commit the bundle:

```bash
cd frontend
npm ci
npm run build
cd ..
git diff -- src/kicad_pcb_web/static/spa
```

CI rebuilds the SPA and fails when the committed bundle is stale. Node is required
for frontend development, not for running an already built wheel.

```bash
uv sync --frozen --extra web
uv build
uv run --frozen --extra web python scripts/package_smoke_test.py
```

The package smoke test validates wheel and sdist contents, checks declared runtime
and web dependency metadata, extracts the wheel outside the checkout, verifies that
imports resolve from the extracted wheel, and probes the API, SPA shell, deep routes,
JavaScript, CSS, and favicon.

## Generated model-evaluation output

Curated model-corpus inputs belong under `tests/fixtures/model_corpus/`. Regenerable
evaluation projects and reports belong under the ignored path:

```text
code_review/generated/model_eval/
```

Regenerate reports with:

```bash
uv run python legacy/openclaw-skill/scripts/kicad_pcb.py model-corpus evaluate \
  --corpus-dir tests/fixtures/model_corpus \
  --out-dir code_review/generated/model_eval
```

List generated output without deleting it:

```bash
bash scripts/cleanup-generated.sh
```

Delete only the contents of the canonical generated-output directory after reviewing
the dry-run list:

```bash
bash scripts/cleanup-generated.sh --apply
```

The cleanup command has no user-supplied path argument and refuses any target other
than `code_review/generated/`. Curated fixtures and the committed SPA bundle are not
inside that directory. CI artifacts such as coverage, Playwright reports, package
archives, and failure diagnostics belong in GitHub Actions artifacts rather than Git.
Deleting current generated files does not rewrite or reduce historical Git objects;
repository-history cleanup would require a separate explicit project.

## Development

```bash
# Install locked Python dependencies
uv sync --frozen --extra dev --extra web

# Install locked frontend dependencies
npm --prefix frontend ci

# Python static checks and tests
uv run ruff check .
uv run ruff format --check .
uv run mypy src/kicad_pcb src/kicad_pcb_web
uv run pytest tests/unit tests/web --cov --cov-report=term-missing

# Frontend checks
npm --prefix frontend run lint
npm --prefix frontend run test:run
npm --prefix frontend run build
git diff --exit-code -- src/kicad_pcb_web/static/spa

# Ordinary local gates; add --python-only for a Python-only loop
bash scripts/validate.sh

# Full non-KiCad gates including wheel smoke and Playwright
bash scripts/validate-all.sh

# KiCad integration tests (requires kicad-cli >= 9 and system libraries)
uv run pytest tests/integration -m requires_kicad
```

## CI

The single **[CI workflow](.github/workflows/ci.yml)** runs on `webapp` pushes and
pull requests. It contains separate jobs for Python quality and coverage, frontend
lint/tests/build, extracted-wheel smoke testing, Playwright browser smoke tests,
and KiCad 9 integration tests. Missing external tools fail the relevant job rather
than being reported as successful validation.

## Design notes

### Serializer and round-trip formatting

The S-expression serializer (`sexpr/`) builds a **basic AST** — a tree of lists and atoms that is rendered back to text with canonical formatting (sorted keys, consistent indentation).  This is an intentional trade-off:

- **What it means in practice:** editing a file and writing it back may reformat its contents (comments are dropped; key order and whitespace may change).  A diff against the original will therefore include cosmetic changes alongside the real mutation.
- **Why this approach:** implementing a comment-preserving concrete-syntax-tree (CST) round-tripper for KiCad S-expressions would add substantial complexity with little practical benefit for automation use-cases.  The canonical output is deterministic and diff-friendly once the initial reformat has been committed.
- **Future work:** if lossless round-tripping becomes a priority a CST layer can be added without changing the public API; the basic-AST serializer would remain as the default formatter.
