# openclaw_kicad_pcb

[![CI](https://github.com/ekkus93/openclaw_kicad_pcb/actions/workflows/ci.yml/badge.svg)](https://github.com/ekkus93/openclaw_kicad_pcb/actions/workflows/ci.yml)

KiCad PCB automation skill for [OpenClaw](https://openclaw.ai).

Generates valid KiCad schematic and PCB files using proper S-expression parsing
and structured document editing, with strong linting/validation and a full
unit/integration test suite.

## Features

- **AST-based editing** — no regex hacks; all mutations go through `SchematicDoc`/`PcbDoc` wrappers
- **Transactional writes** — temp-write → validate → atomic replace; originals never corrupted
- **Structural linting** — 18 built-in rules (SCH/PCB 001–009) enforced before every write
- **Circuit pattern library** — resistor divider, LED+resistor, connector breakout, decoupling cap
- **Preflight checks** — duplicate refs, net name validation, symbol accessibility, footprint requirements
- **KiCad CLI integration** — ERC/DRC/export via `kicad-cli` with version compatibility layer
- **Circuit IR pipeline** — deterministic Spec → IR → KiCad schematic generation (`new-from-netlist`, `apply-netlist`)
- **JSON output** — all commands support `--json` for machine-friendly automation
- **Dry-run mode** — validate without committing (`--dry-run`)

## Quick start

```bash
pip install -e ".[dev]"

# Create a new project
python scripts/kicad_pcb.py new MyProject

# Add a resistor divider pattern
python scripts/kicad_pcb.py apply-pattern MyProject resistor-divider \
    --r1-ref R1 --r2-ref R2 \
    --vin-net VIN --vout-net VOUT --gnd-net GND

# Lint the generated schematic
python scripts/kicad_pcb.py lint-sch MyProject/MyProject.kicad_sch

# Check environment
python scripts/kicad_pcb.py doctor
```

## Circuit IR pipeline

The skill supports a **compiler-style pipeline** for LLM-driven circuit generation:

```
LLM output (Spec) → Circuit IR JSON → KiCad .kicad_sch
```

An LLM never draws wires by XY coordinates — it outputs a **Circuit IR JSON** file describing
components and net connections. The tool compiles this into a deterministic KiCad schematic.

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
python scripts/kicad_pcb.py new-from-netlist \
    --name MyProject \
    --netlist circuit.json \
    --symbols-dir /path/to/symbols \
    --mode kicad          # default; requires kicad-cli
    # --mode internal     # internal syntax+lint only; no kicad-cli required
```

`compile-netlist` is an alias for `new-from-netlist` with identical arguments.

**Apply Circuit IR to the current/open project** (updates managed region):
```bash
python scripts/kicad_pcb.py open MyProject/
python scripts/kicad_pcb.py apply-netlist \
    --netlist circuit.json \
    --symbols-dir /path/to/symbols \
    --force               # adopt schematic if not already OpenClaw-managed
    --dry-run             # validate without writing
```

**Inspect the current schematic**:
```bash
python scripts/kicad_pcb.py info-sch
python scripts/kicad_pcb.py info-sch --json   # machine-readable
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

## Schematic layout engine (Graphviz)

When generating schematics from a Circuit IR the tool runs a **graph layout
engine** to place components so that signals flow left → right.  By default
it uses [Graphviz `dot`](https://graphviz.org) for high-quality positioning;
if Graphviz is not available it falls back to the built-in heuristic engine.

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
python scripts/kicad_pcb.py new-from-netlist --netlist circuit.json ...
```

The discovery order is:

1. `GRAPHVIZ_DOT` environment variable (highest priority)
2. System `PATH` (`shutil.which("dot")`)

Run `python scripts/kicad_pcb.py doctor` to see which binary is active and
its version.

### Selecting a layout engine

Pass `--layout` to any generation command:

| Value | Behaviour |
|-------|-----------|
| `auto` *(default)* | Graphviz if available, else heuristic |
| `graphviz` | Graphviz only; error if not found |
| `heuristic` | BFS-based layout; no external tools required |
| `none` | No layout; symbols placed at (0, 0) |

### Licensing

Graphviz is an independent open-source tool licensed under the
[Eclipse Public License 1.0](https://www.eclipse.org/legal/epl-v10.html).
This package does **not** bundle or redistribute any Graphviz binary.
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for full details.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run unit tests
pytest tests/unit/

# Run unit tests with coverage
pytest tests/unit/ --cov --cov-report=term-missing

# Static checks
ruff check .
ruff format --check .
mypy kicad-pcb/src

# Integration tests (requires kicad-cli)
pytest tests/integration/ -m requires_kicad

# Run all local quality gates at once (lint + type check + unit tests)
bash scripts/validate.sh
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

