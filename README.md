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

