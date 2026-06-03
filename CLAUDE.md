# CLAUDE.md

## Project overview

KiCad PCB Web App (`webapp` branch) — a FastAPI web app for deterministic KiCad schematic
generation from Circuit IR JSON, with an optional local-first LLM wizard.

Two Python packages under `src/`:
- `kicad_pcb` — deterministic engine: Circuit IR validation, Graphviz layout, routing,
  AST-based schematic mutation, autofix, placeholder symbol synthesis
- `kicad_pcb_web` — thin FastAPI shell: wizard sessions, job management, LLM provider

Frontend: React + TypeScript SPA under `frontend/`, built with Vite, styled with Tailwind.
Built artifacts served from `src/kicad_pcb_web/static/spa/`.

---

## Essential commands

```bash
# Install dependencies
uv sync --extra dev --extra web

# Start the web server (always run from project root, not from frontend/)
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload

# Python quality gates
uv run ruff check .
uv run ruff format --check .
uv run mypy src/kicad_pcb src/kicad_pcb_web
uv run pytest tests/unit/

# Frontend (run from project root or frontend/)
cd frontend && npm run build
cd frontend && npm run lint   # if available

# All Python gates at once
uv run bash scripts/validate.sh
```

---

## Working rules

- Read relevant files before making changes. Inspect adjacent files before editing.
- Prefer small, safe, targeted changes over sweeping rewrites.
- Do not suppress lint/type warnings with `# noqa` or `# type: ignore` unless briefly
  justified. Fix the root cause.
- Never introduce new top-level configs or files just to silence warnings.
- All tests must pass before committing. All lint/format/mypy checks must be clean.
- When fixing a bug, add a targeted test that would have caught it.

---

## Commit discipline

Only commit when:
1. `uv run ruff check .` passes with zero errors
2. `uv run ruff format --check .` passes
3. `uv run mypy src/kicad_pcb src/kicad_pcb_web` passes
4. `uv run pytest tests/unit/` passes
5. If frontend code changed: `cd frontend && npm run build` passes

Commit message convention: `type(scope): description` — e.g.
`fix(autofix): handle unqualified symbol IDs`, `feat(webapp): add Clear Circuit IR button`

After committing, push to the `webapp` branch on GitHub.

---

## Frontend validation

Whenever `frontend/src/` changes:
1. Run `cd frontend && npm run build` — must succeed with zero TypeScript errors.
2. Run `cd frontend && npm run lint` — must pass.
3. The built SPA artifacts (`src/kicad_pcb_web/static/spa/`) must be committed alongside
   the source change.

Do not treat Python-only checks as sufficient for TypeScript changes.

---

## Code style and quality

- Python 3.11+, `from __future__ import annotations` in all modules.
- Full type annotations. Pydantic v2 for models. `dataclass(frozen=True)` for value objects.
- `ruff` for lint and format; `mypy` for type checking — match existing project config.
- No bare `except:`. Catch specific exceptions and raise domain errors at boundaries.
- No global state. Pass dependencies explicitly.
- No silent fallbacks. Either raise a typed error or return a Result-style value.
- No hard-coded secrets, URLs, or paths. Use env/config.

---

## Testing

- Framework: `pytest`. Fixtures, `tmp_path`, real fakes over mocks where practical.
- Unit tests live in `tests/unit/`. Integration tests in `tests/integration/`.
- Cover both happy path and error/edge cases for each public function.
- TDD preferred: small failing test → minimal implementation → refactor.
- Do not create stub implementations to satisfy tests. Implement real behavior.

---

## Project-specific notes

- **Data directory**: defaults to `./data` resolved relative to `kicad_pcb_web.toml`. Always
  start the server from the project root so the config file is found correctly.
- **LLM provider**: configured in `kicad_pcb_web.toml` under `[llm]`. Currently `openai`
  with model `gpt-5.4-mini`.
- **Autofix pipeline**: `src/kicad_pcb/ir/autofix.py` — layered deterministic IR fixers.
  New layers go between existing ones; follow the `(data, fixes_list)` return pattern.
- **Placeholder symbols**: `src/kicad_pcb/placeholder_symbol.py` — synthesises generic
  KiCad symbols for unknown library refs. Registered via `SymbolIndex.register_placeholder`.
- **Wizard service**: `src/kicad_pcb_web/services/wizard.py` — session lifecycle, LLM
  prompts, IR generation loop. The IR contract text is in `_ir_contract_text()`.
- **Frontend state**: all wizard and job UI lives in `frontend/src/App.tsx` — one large
  component file. Build the bundle after any TSX/CSS change.

---

## Non-trivial tasks

For any non-trivial task, provide before starting:
1. Summary of what was found / the root cause
2. Concrete change plan with files to touch
3. Any risks or side effects
4. Tests to run to verify
