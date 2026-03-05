# Fallback Code Audit (kicad-pcb)

Date: 2026-03-05
Scope: `kicad-pcb/src/kicad_pcb/**`

This inventory lists every fallback/suppression path found during the audit, grouped by review priority.

## A) Potentially Silent Fallbacks (highest priority)

1. Graphviz layout cache read/write failures are swallowed
   - [kicad-pcb/src/kicad_pcb/graphviz_layout/cache.py](../kicad-pcb/src/kicad_pcb/graphviz_layout/cache.py#L55-L96)
   - Behavior: On any cache parse/read/write exception, code returns `None` or logs debug and continues.
	- Status: ✅ Addressed on 2026-03-05 — cache read/parse/write errors now raise `RuntimeError` (fail-fast).

2. KiCad adapter version detection is suppressed and capability checks can be skipped
   - [kicad-pcb/src/kicad_pcb/adapters.py](../kicad-pcb/src/kicad_pcb/adapters.py#L332-L355)
   - Behavior: `detected_version` suppresses all exceptions; unknown version means capability check may effectively be bypassed.
	- Status: ✅ Addressed on 2026-03-05 — unknown-version capability checks now raise `ToolError`; broad suppression removed from version detection path.

3. KiCad adapter report reads degrade to `None`/empty string
   - [kicad-pcb/src/kicad_pcb/adapters.py](../kicad-pcb/src/kicad_pcb/adapters.py#L307-L329)
   - Behavior: JSON/text read errors return `None` or `""` without surfacing an error.
	- Status: ✅ Addressed on 2026-03-05 — successful command paths now require output files and raise `ToolError` on missing/malformed/unreadable report/output content.

4. Autofix pin-alias stage swallows pin lookup exceptions
   - [kicad-pcb/src/kicad_pcb/ir/autofix.py](../kicad-pcb/src/kicad_pcb/ir/autofix.py#L214-L228)
   - Behavior: broad `except Exception` sets pin set empty and skips alias fix for that symbol.
	- Status: ✅ Addressed on 2026-03-05 — now catches only `UserError` and reports lookup failures in `remaining_errors`; unexpected exceptions propagate (fail-fast).

5. fix-netlist suppresses symbol index construction failures
   - [kicad-pcb/src/kicad_pcb/commands/netlist.py](../kicad-pcb/src/kicad_pcb/commands/netlist.py#L207-L215)
   - Behavior: `with contextlib.suppress(UserError)` continues with `symbol_index=None`, reducing fix capability.
	- Status: ✅ Addressed on 2026-03-05 — `cmd_fix_netlist` now fails fast when `SymbolIndex` construction fails (no `UserError` suppression).

6. Library parse/read failure treated as symbol-not-found signal
   - [kicad-pcb/src/kicad_pcb/lib_symbol.py](../kicad-pcb/src/kicad_pcb/lib_symbol.py#L197-L226)
   - Behavior: parse/read exceptions return `None`, conflating I/O/parse failures with missing symbols.
	- Status: ✅ Addressed on 2026-03-05 — library parse/read failures now raise explicit errors; only true missing library/symbol paths return `None`/empty values.

7. SymbolIndex declaration scan ignores file read errors
   - [kicad-pcb/src/kicad_pcb/symbol_index.py](../kicad-pcb/src/kicad_pcb/symbol_index.py#L130-L138)
   - Behavior: `except OSError: pass` while probing for declared symbol headers.
	- Status: ✅ Addressed on 2026-03-05 — declaration-scan read failures now raise `UserError(IO_ERROR)` with symbol and library path context.

8. search-symbols grep fallback + unreadable file skip
   - [kicad-pcb/src/kicad_pcb/commands/search.py](../kicad-pcb/src/kicad_pcb/commands/search.py#L157-L199)
   - Behavior: grep failures/timeouts silently fall back to Python scan; unreadable files are skipped.
	- Status: ✅ Addressed on 2026-03-05 — grep pre-screen failures/timeouts now raise explicit `UserError(IO_ERROR)`; silent fallback scan path removed.

9. add-component defaults to fallback symbol dir and default pins
   - [kicad-pcb/src/kicad_pcb/commands/sch.py](../kicad-pcb/src/kicad_pcb/commands/sch.py#L23-L66)
   - Behavior: when symbol missing, warns and uses pins `["1", "2"]`.
	- Status: ✅ Addressed on 2026-03-05 — `cmd_add_component` now fails fast on unresolved symbol directory (`SYMBOL_DIR_MISSING`) and missing symbol pins (`SYMBOL_NOT_FOUND`), no default-pin fallback.

10. Config/session load returns `None` on malformed state
	- [kicad-pcb/src/kicad_pcb/config.py](../kicad-pcb/src/kicad_pcb/config.py#L194-L211)
	- Behavior: malformed/IO issues return `None`; stale session marker unlink errors suppressed.
	- Status: ✅ Addressed on 2026-03-05 — malformed current project/session state now raises `UserError(IO_ERROR)`; stale session marker unlink failures are surfaced as `UserError(IO_ERROR)`.

## B) Explicit Functional Fallbacks (intentional behavior)

11. Router fallback strategies (label/global-label/off-canvas)
	- [kicad-pcb/src/kicad_pcb/router.py](../kicad-pcb/src/kicad_pcb/router.py#L427-L583)
	- Behavior: non-power nets may fall back from direct/hub/spine to label route; unknown pins go off-canvas with labels.
	- Status: ✅ Addressed on 2026-03-05 — strict mode now fails fast on unknown pin endpoints (`PIN_INVALID`) instead of off-canvas fallback; non-strict mode keeps existing explicit label/global-label routing behavior.

12. SDS-to-BFS layout fallback when connector roles incomplete
	- [kicad-pcb/src/kicad_pcb/layout.py](../kicad-pcb/src/kicad_pcb/layout.py#L616-L655)
	- Behavior: logs warning and uses BFS column assignment.

13. Orientation fallback when engine provides no rotation
	- [kicad-pcb/src/kicad_pcb/commands/_sch_apply.py](../kicad-pcb/src/kicad_pcb/commands/_sch_apply.py#L416-L425)
	- Behavior: computes orientations separately if layout tuples include `None` rotation.

14. Power symbol insertion fallback to global label (API contract)
	- [kicad-pcb/src/kicad_pcb/sch_doc/__init__.py](../kicad-pcb/src/kicad_pcb/sch_doc/__init__.py#L380-L410)
	- Behavior: `add_power_symbol(...) -> False` if symbol def unavailable; caller may fallback to global label.

15. Symbol directory discovery fallback chain (explicit → env → config → platform)
	- [kicad-pcb/src/kicad_pcb/config.py](../kicad-pcb/src/kicad_pcb/config.py#L84-L120)
	- Behavior: source precedence fallback for locating KiCad symbol libraries.

16. Graphviz dot discovery fallback chain (bundled → env var → PATH)
	- [kicad-pcb/src/kicad_pcb/graphviz_layout/__init__.py](../kicad-pcb/src/kicad_pcb/graphviz_layout/__init__.py#L106-L148)
	- Behavior: binary discovery precedence, not execution fallback.

17. Connector anchor fallback within feedback snap pass
	- [kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py](../kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py#L439-L447)
	- Behavior: prefers IC/connector anchor, then falls back to any positioned neighbour.

18. Connector-seed fallback in tier selection
	- [kicad-pcb/src/kicad_pcb/tier.py](../kicad-pcb/src/kicad_pcb/tier.py#L388-L393)
	- Behavior: when no ICs are present, chooses alphabetically-first connector.

## C) Suppression Used for Cleanup/Resilience (re-raises or non-behavioral)

19. apply-netlist cleanup suppresses unlink race only, then re-raises original failure
	- [kicad-pcb/src/kicad_pcb/commands/_sch_apply.py](../kicad-pcb/src/kicad_pcb/commands/_sch_apply.py#L162-L172)

20. atomic write temp cleanup suppression (error is re-raised)
	- [kicad-pcb/src/kicad_pcb/fs.py](../kicad-pcb/src/kicad_pcb/fs.py#L99-L160)

21. pipeline temp-file cleanup suppression in `finally`
	- [kicad-pcb/src/kicad_pcb/pipeline.py](../kicad-pcb/src/kicad_pcb/pipeline.py#L393-L424)

22. STEP export file-size stat suppression (result still returned)
	- [kicad-pcb/src/kicad_pcb/adapters.py](../kicad-pcb/src/kicad_pcb/adapters.py#L548-L553)

## D) Not a runtime fallback bug (kept for completeness)

23. Serializer inline-vs-block formatting fallback
	- [kicad-pcb/src/kicad_pcb/sexpr/serializer.py](../kicad-pcb/src/kicad_pcb/sexpr/serializer.py#L44-L52)
	- Behavior: formatting choice only.

24. Numeric parsing defaults in AST introspection
	- [kicad-pcb/src/kicad_pcb/sch_doc/__init__.py](../kicad-pcb/src/kicad_pcb/sch_doc/__init__.py#L100-L111)
	- Behavior: parse helpers return prior/default value for malformed numeric atoms.

---

## Suggested review order

1) A1–A6 (highest risk of masking real failures)

2) A7–A10 (data/source quality degradation paths)

3) B11–B18 (intentional behavioral fallbacks; decide policy)

4) C19–C22 (cleanup suppressions; usually acceptable)

