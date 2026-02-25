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
```

## CI

- **[CI workflow](.github/workflows/ci.yml)** — runs on every PR: lint, format check, type check, unit tests with coverage
- **[Integration workflow](.github/workflows/integration.yml)** — runs nightly with a full KiCad install
