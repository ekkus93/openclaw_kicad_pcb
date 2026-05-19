# KiCad PCB Web App

[![CI](https://github.com/ekkus93/openclaw_kicad_pcb/actions/workflows/ci.yml/badge.svg)](https://github.com/ekkus93/openclaw_kicad_pcb/actions/workflows/ci.yml)

This branch provides a Python FastAPI web app for deterministic KiCad project
generation from Circuit IR JSON.

The web app does not require OpenClaw, an LLM, an agent runtime, or any
external AI service. Users provide explicit Circuit IR JSON, and the app
validates it, generates KiCad project files, and exposes curated downloadable
artifacts.

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
- **Local web app** — FastAPI + Jinja UI for validating Circuit IR, generating projects, and downloading artifacts
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

Generated web jobs are stored under:

```text
data/jobs/<job_id>/
```

Each job keeps its input, generated project, private canonical job metadata, and
downloadable artifacts inside that directory. The web UI and API expose curated
artifact downloads from the job's `artifacts/` directory.

The web app defaults to `internal` validation for job generation. Optional KiCad
CLI validation is available only when `kicad-cli` is installed and a request
explicitly asks for `validation="kicad"`.

The web app binds to `127.0.0.1` by default and is intended for local/internal use
in v1. Do not expose it publicly without adding authentication, isolation, and
additional sandboxing around user-supplied netlists and generated artifacts.

## Circuit IR pipeline

Circuit IR JSON is the canonical input format for both the web app and the
archived CLI workflow:

```
Circuit IR JSON → KiCad .kicad_sch
```

The web app does not depend on any LLM integration. Historical OpenClaw or
LLM-assisted workflows may still target this same Circuit IR format, but they
are optional and external to the web app itself.

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

**Apply Circuit IR to the current/open project** (updates managed region):
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
- Generated components live in a dedicated embedded sheet (`OpenClaw_Managed`),
  leaving the rest of the schematic untouched.

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
3. The emitted managed schematic: `OpenClaw_Managed.kicad_sch`.
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

## Development

```bash
# Install dev dependencies
uv sync --extra dev --extra web

# Run unit tests
uv run pytest tests/unit/

# Run unit tests with coverage
uv run pytest tests/unit/ --cov --cov-report=term-missing

# Static checks
uv run ruff check .
uv run ruff format --check .
uv run mypy src/kicad_pcb src/kicad_pcb_web

# Integration tests (requires kicad-cli)
uv run pytest tests/integration/ -m requires_kicad

# Run all local quality gates at once (lint + type check + unit tests)
uv run bash scripts/validate.sh
```

## CI

- **[CI workflow](.github/workflows/ci.yml)** — runs on every PR: lint, format check, type check, unit tests with coverage
- **[Integration workflow](.github/workflows/integration.yml)** — runs nightly with a full KiCad install

## Design notes

### Serializer and round-trip formatting

The S-expression serializer (`sexpr/`) builds a **basic AST** — a tree of lists and atoms that is rendered back to text with canonical formatting (sorted keys, consistent indentation).  This is an intentional trade-off:

- **What it means in practice:** editing a file and writing it back may reformat its contents (comments are dropped; key order and whitespace may change).  A diff against the original will therefore include cosmetic changes alongside the real mutation.
- **Why this approach:** implementing a comment-preserving concrete-syntax-tree (CST) round-tripper for KiCad S-expressions would add substantial complexity with little practical benefit for automation use-cases.  The canonical output is deterministic and diff-friendly once the initial reformat has been committed.
- **Future work:** if lossless round-tripping becomes a priority a CST layer can be added without changing the public API; the basic-AST serializer would remain as the default formatter.
