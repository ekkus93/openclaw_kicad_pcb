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
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/
                                       # use python -m pytest — uv run pytest
                                       # resolves to mambaforge Python 3.10

# Frontend (run from project root or frontend/)
cd frontend && npm run build
cd frontend && npm run lint   # if available

# Full gate suite: Python (ruff/mypy/pytest unit+web) + frontend lint/test/build
# + the committed-SPA-bundle guard. Flags: --fast, --python-only.
uv run bash scripts/validate.sh
```

---

## Commit rules

- **Never add `Co-Authored-By:` trailers to commit messages.** Do not include
  `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>` or any similar
  attribution line. Commit messages should be plain and contain only the change
  description.

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
1. `uv run --extra dev --extra web ruff check .` passes with zero errors
2. `uv run --extra dev --extra web ruff format --check .` passes
3. `uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web` passes
4. `uv run --extra dev --extra web python -m pytest tests/unit/` passes
5. If frontend code changed: `cd frontend && npm run build` passes

Commit message convention: `type(scope): description` — e.g.
`fix(autofix): handle unqualified symbol IDs`, `feat(webapp): add Clear Circuit IR button`

After committing, push to the `webapp` branch on GitHub.

Optional local guard: `bash git-hooks/install.sh` installs a pre-commit hook running
`ruff format --check` + `ruff check`. CI (`.github/workflows/ci.yml`) runs only on the
`webapp` branch and needs graphviz + KiCad symbols.

---

## Frontend validation

Whenever `frontend/src/` changes:
1. Run `cd frontend && npm run build` — must succeed with zero TypeScript errors.
2. Run `cd frontend && npm run lint` — must pass.
3. Rebuild and commit the SPA bundle (`src/kicad_pcb_web/static/spa/`) alongside the
   source change. CI and `validate.sh` hard-fail on any uncommitted diff there
   (`git diff --exit-code -- src/kicad_pcb_web/static/spa`).

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
- Unit tests live in `tests/unit/`; web tests in `tests/web/`. CI and `validate.sh` run
  `pytest tests/unit tests/web`. `tests/integration/` exists but is not run in CI.
- Cover both happy path and error/edge cases for each public function.
- TDD preferred: small failing test → minimal implementation → refactor.
- Do not create stub implementations to satisfy tests. Implement real behavior.
- **Config isolation**: unit tests must not read from or write to `~/.kicad-pcb` or
  `~/kicad-projects`. The autouse fixture in `tests/unit/conftest.py` redirects both
  via `KICAD_PCB_CONFIG_DIR` and `KICAD_PCB_PROJECTS_DIR`. Do not patch the deprecated
  module-level constants (`CURRENT_PROJECT_FILE`, etc.); use the env vars or the dynamic
  helpers (`get_current_project_file()`, `get_config_dir()`, etc.) instead.
- Manual isolation check: `KICAD_PCB_CONFIG_DIR="$(mktemp -d)" KICAD_PCB_PROJECTS_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/ -q`
- **Web test counts are environment-dependent**: tests using the registered `requires_kicad`
  marker — or the `requires_generation_pipeline` decorator (in `tests/conftest.py`, which
  applies `requires_kicad`) — skip when `kicad-cli`, KiCad system symbol libraries, or
  `rsvg-convert` are unavailable. Record the exact local pass/skip counts and skip reasons
  in completion notes; do not hard-code universal counts.

---

## Project-specific notes

- **Runtime config**: there is no committed `kicad_pcb_web.toml`. `settings.py` reads a TOML
  file only if `KICAD_PCB_WEB_CONFIG_FILE` points to one (or `./kicad_pcb_web.toml` exists);
  otherwise all settings come from env vars + defaults. Key vars: `KICAD_PCB_WEB_DATA_DIR`,
  `KICAD_PCB_WEB_LLM_PROVIDER`, `KICAD_PCB_WEB_LLM_MODEL`.
- **Data directory**: defaults to `data` relative to the config file's dir, or the CWD when
  no config file is present — so start the server from the project root.
- **LLM provider**: defaults to `disabled` (valid: `disabled`, `openai`, `ollama`,
  `llama_server`); default model is unset. Configure via env vars or a TOML `[llm]` table.
- **Autofix pipeline**: `src/kicad_pcb/ir/autofix.py` — layered deterministic IR fixers.
  New layers go between existing ones; follow the `(data, fixes_list)` return pattern.
- **Placeholder symbols**: `src/kicad_pcb/placeholder_symbol.py` — synthesises generic
  KiCad symbols for unknown library refs. Registered via `SymbolIndex.register_placeholder`.
- **Wizard service**: `src/kicad_pcb_web/services/wizard.py` — session lifecycle, LLM
  prompts, IR generation loop. The IR contract text is `_ir_contract_text()` in
  `src/kicad_pcb_web/services/_wizard_llm.py` (imported by `wizard.py`).
- **Frontend state**: the app shell and top-level routing live in `frontend/src/App.tsx`.
  Wizard UI is split across `frontend/src/routes/WizardPage.tsx` and
  `frontend/src/routes/wizard/*`. Job UI lives in `frontend/src/routes/JobPage.tsx`
  and related route modules. Shared UI utilities and components live under
  `frontend/src/components/` and `frontend/src/utils*`. Keep route components focused;
  avoid rebuilding a monolithic `App.tsx`.

---

## Non-trivial tasks

For any non-trivial task, provide before starting:
1. Summary of what was found / the root cause
2. Concrete change plan with files to touch
3. Any risks or side effects
4. Tests to run to verify
