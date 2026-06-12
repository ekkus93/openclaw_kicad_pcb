# CODE_REVIEW1.md

## Context

This file summarizes the code review discussion for the OpenClaw skill `kicad-pcb`, with a focus on why it generates poor/broken KiCad files and what needs to be changed to make it reliable, testable, and production-usable.

The key goal is to move the skill from a fragile **string/regex mutation script** to a **structured, validated KiCad file generation/editing tool**.

---

## Summary of the Overall Assessment

### High-level verdict
The `kicad-pcb` skill has a lot of useful functionality and good intent, but its current implementation is brittle and error-prone for KiCad file generation.

### What is good
- It covers useful end-to-end workflows (project create, edits, checks, exports, packaging).
- It uses `kicad-cli` for validation/export tasks, which is the right backbone.
- It shows awareness of KiCad-specific complexity (e.g., symbol embedding concerns).
- It has a practical CLI surface that can be very useful once reliability improves.

### What is bad / what causes broken files
- Core file generation/editing relies heavily on **regex and string concatenation** applied to KiCad S-expression files.
- CLI behavior, command signatures, and `SKILL.md` documentation are out of sync.
- Mutation logic, subprocess calls, filesystem writes, printing, and exits are tangled in one large script.
- Error handling is inconsistent and sometimes too broad.
- No unit tests or regression tests exist to catch format breakage.
- No mandatory linting/validation pipeline prevents broken files from being written.

---

## Core Technical Problem: KiCad Files Are S-Expressions (and Need Structured Editing)

KiCad schematic (`.kicad_sch`) and PCB (`.kicad_pcb`) files are S-expression-based. That means nested structures, quoting, escaping, and versioned grammar matter.

### Why regex/string editing is the wrong abstraction
Regex/string replacement is brittle for nested S-expressions because:
- nested parentheses are not regex-safe
- formatting/whitespace changes can break matching
- ordering changes can break assumptions
- version/schema differences can invalidate hardcoded snippets
- string literals/quoted content can be accidentally damaged

This is the primary reason the current implementation can output broken KiCad files.

### The correct approach
Use a proper **S-expression tokenizer/parser/serializer** and manipulate files as an **AST (abstract syntax tree)**.

- Parse file -> mutate AST -> serialize -> validate
- No structural regex replacements for `.kicad_sch` / `.kicad_pcb` generation/editing

---

## Specific Issues Discussed in the Existing Skill

### 1) Documentation mismatch (`SKILL.md` vs actual CLI)
This is a major issue for OpenClaw/LLM-driven usage.

Examples discussed:
- `connect` docs imply one syntax while script implements another
- `add-net` examples/options don’t match reality
- some example flags/options appear unsupported (e.g., `preview-pcb --layers` in docs if not implemented)

#### Why this matters
- LLM generates incorrect commands based on bad docs
- users lose trust
- failures look like “KiCad generation is bad” when part of the problem is “the docs are wrong”

### 2) Fragile regex-based file mutations
Examples called out:
- board outline editing (`Edge.Cuts`) done with regex assumptions
- footprint placement (`at`) parsing/updating using regex
- heuristic parsing of symbol/pin info (including fixed-window scanning)

These are fragile and should be replaced with AST-based operations.

### 3) Minimal hand-written template generation risks
Creating KiCad files with manually assembled text templates can work for prototypes, but it becomes brittle if:
- version numbers/fields are hardcoded
- required sections/properties are omitted
- UUIDs are inconsistent (timestamp-like vs real UUIDs)

The discussion specifically flagged inconsistent UUID generation and hardcoded schema/version snippets as risks.

### 4) Hard-to-test architecture
The current script mixes:
- CLI argument handling
- printing / user interaction
- `sys.exit`
- filesystem reads/writes
- subprocess invocations (`kicad-cli`)
- KiCad document mutation logic

This makes unit tests hard to write and maintain.

### 5) Error handling inconsistency
Issues discussed:
- broad/bare `except:` swallowing useful errors
- inconsistent subprocess failure reporting
- mixed `return`/`sys.exit()` patterns
- lack of typed exceptions and structured errors

### 6) Environment/path assumptions
Examples discussed:
- hardcoded symbol library paths
- tool discovery resolved too early (e.g., import-time lookup)
- machine-specific examples in docs

These reduce portability and make behavior harder to test.

### 7) Scope/docs oversell relative to implementation
The skill docs suggest an advanced NL-to-PCB flow, but the implementation is mostly low-level/manual coordinate-driven commands.

This is fixable by:
- aligning docs with reality now
- improving generation later after reliability foundation is in place

---

## Root Causes of Poor KiCad Output (Ranked)

1. **Regex/string mutation of S-expression files**
2. **No structured parser/serializer**
3. **No validation-after-write pipeline**
4. **No unit tests / regression tests**
5. **Docs-command mismatch causing invalid invocation**
6. **No intermediate circuit representation (IR) for generation**

---

## What “Doing S-Expressions the Right Way” Means

The discussion explicitly rejected the regex/string “easy way out” and agreed on the correct implementation strategy.

### Required S-expression infrastructure
Implement a small, reliable S-expression stack:
- tokenizer
- parser
- AST nodes (`ListNode`, `AtomNode`, `StringNode`)
- serializer (deterministic / round-trip safe)
- AST traversal/search helpers

### Why this matters
This becomes the foundation for:
- safe KiCad file edits
- linting
- validation
- unit tests
- stable regression testing

---

## Linting and Validation: Broken Output Must Be Prevented

A major requirement from the discussion: **it is unacceptable to output broken KiCad files silently**.

### Required validation layers (all discussed)

#### 1) Syntax validation (fast, always)
- S-expression parse succeeds
- balanced parentheses
- valid string quoting/escaping
- correct root node (`kicad_sch` / `kicad_pcb`)
- required top-level sections present (for generated files)

#### 2) Structural linting (fast, custom, always)
Add custom lints for likely generation mistakes.

Examples discussed:

**Schematic lints**
- invalid root
- duplicate UUIDs
- duplicate reference designators
- missing `Reference` / `Value`
- malformed coordinates
- malformed wires
- missing/empty `lib_symbols`
- symbol instance refers to missing embedded symbol

**PCB lints**
- invalid root
- duplicate UUIDs
- footprint missing/malformed `at`
- missing `Edge.Cuts`
- non-closed generated outline (rect mode)
- impossible dimensions
- malformed layer declarations
- out-of-range coordinates

#### 3) KiCad validation (authoritative external tool)
Use `kicad-cli` for:
- schematic ERC
- PCB DRC
- export smoke checks (where useful)

This is the final external gate.

### Transactional write pipeline (critical design decision)
All mutating commands should follow:

1. Load + parse current file
2. Apply AST mutation
3. Serialize to temp file
4. Re-parse temp file (round-trip sanity)
5. Run lints
6. Run `kicad-cli` validation (based on policy)
7. Atomic replace original on success
8. Do not overwrite original on failure

This prevents broken outputs from corrupting the project.

---

## Refactor Direction Agreed in the Discussion

### Goal
Make the skill:
- reliable
- unit-testable
- maintainable
- version-aware
- safer for OpenClaw/LLM usage

### Recommended architectural shape
Split the monolithic script into modules:
- thin CLI entrypoint
- typed models
- filesystem helpers
- subprocess runner / `kicad-cli` adapter
- S-expression parser/serializer
- KiCad-specific document wrappers (`SchematicDoc`, `PcbDoc`)
- lints
- validation services
- business/services layer

### Key refactor principles
- CLI is thin (parsing/printing only)
- business logic returns structured results
- no `sys.exit()` in internal logic
- side effects are injected/wrapped for testing
- all mutation logic is AST-based

---

## Unit Testability Requirements (Explicitly Requested)

The discussion emphasized that the refactor must make the code **unit testable**, and that tests should be added (not just planned).

### What must become testable
- S-expression tokenization/parsing/serialization
- KiCad AST mutation routines
- linting logic
- validation pipeline control flow
- CLI argument parsing/dispatch
- subprocess interactions via mocks/adapters

### Testing strategy discussed

#### Unit tests (highest priority)
- S-expression tokenizer/parser/serializer
- `SchematicDoc` mutations
- `PcbDoc` mutations
- lint rules
- validation pipeline (prevent overwrite on failure)
- CLI parsing correctness

#### Golden file tests (critical)
Use known input/output fixtures and compare:
- AST equality and/or
- canonical serialized output

This is ideal for regression prevention in format generators.

#### Integration tests (with real KiCad, skip if unavailable)
- create project -> ERC
- mutate schematic/PCB -> DRC/ERC/exports
- mini end-to-end flow

---

## Specific Refactors and Improvements Discussed

### 1) Replace regex-based edits with AST-based document wrappers
Most urgent targets discussed:
- board outline (`Edge.Cuts`) generation/replacement
- footprint `at` updates (auto-place)
- schematic insertion helpers
- symbol embedding and symbol library parsing

### 2) Introduce typed models
Examples discussed:
- `ProjectRef`
- `ComponentSpec`
- `WireSegment`
- `BoardOutlineRect`
- `LintIssue`
- `ValidationResult`

These reduce stringly-typed bugs and centralize validation.

### 3) Add a `KicadCliAdapter`
Instead of scattered subprocess calls, centralize:
- version detection
- ERC/DRC
- exports
- error parsing/formatting
- compatibility handling

### 4) Add validation policy controls
Discussed modes:
- `none`
- `syntax`
- `lint`
- `kicad`
- `full`

With a safe default for mutating commands.

### 5) Add safe-write + backup support
Never overwrite original files before passing validation.

### 6) Add `doctor` command and environment checks
Useful for:
- diagnosing missing `kicad-cli`
- version mismatch
- symbol path issues
- current project problems

---

## Documentation / Skill UX Improvements Discussed

### Immediate need: fix `SKILL.md`
This was a top priority.
- Command docs must match implementation exactly.
- Remove unsupported examples/options.
- Clarify real current capabilities vs aspirational ones.

### Longer-term UX improvements
- `--dry-run` for mutations
- `--json` output mode
- explicit `lint` / `validate` commands
- clearer diagnostics with issue codes and suggestions

---

## Final Direction Agreed On

### Yes, do S-expressions the right way
The discussion explicitly agreed that regex/string hacks are the wrong abstraction for KiCad files and should be replaced with proper parsing + AST-based editing.

### Yes, add linting and validation of outputted KiCad files
The discussion explicitly agreed that broken KiCad output is unacceptable and must be prevented by:
- syntax validation
- structural linting
- `kicad-cli` validation
- transactional writes (no overwrite on failure)

### This is the path to “rock solid”
The path to a robust skill is:
1. Fix docs mismatch and immediate reliability issues
2. Refactor for testability
3. Implement S-expression parser/serializer
4. Move all mutations to AST wrappers
5. Add lint + validation pipeline
6. Add unit/golden/integration tests
7. Improve generation quality on top of a reliable foundation

---

## Relationship to `CODE_REVIEW1_TODO.md`

`CODE_REVIEW1_TODO.md` is the actionable implementation checklist derived from this review/context document. It is intended to be handed to Github Copilot (or another implementer) as the execution plan.

This file (`CODE_REVIEW1.md`) should be kept as background context so the implementer understands:
- *why* the refactor is needed
- *why* regex is insufficient here
- *why* validation and tests are mandatory
- *what “done” should look like*
