# Broken Fixtures — `tests/fixtures/broken/`

These four `.kicad_sch` files each reproduce a specific semantic bug that was
found and fixed in the `kicad_pcb` skill.  They are **syntactically valid
S-expressions** (the parser must accept all of them), but each contains an
incorrect pattern that KiCad 9 / `kicad-cli` would reject or silently
mishandle at the semantic level.

The regression tests in `tests/unit/test_p41_regression_fixtures.py` confirm:
1. Every file parses without exception.
2. The specific bad pattern is still detectable in the raw text.

---

## bug1_subname_rename.kicad_sch

**Produced by:** Early version of `cmd_new_from_netlist` / `_find_symbol_def`
(pre-AST-based symbol embedding, commit range before the S-expression
refactor).

**Bug:** `re.sub` without `count=1` renamed every occurrence of the symbol
name in the extracted library block — including sub-symbol names like `R_0_1`
and `R_1_1` — to `Device:R_0_1` and `Device:R_1_1`.

**Failure before fix:** `kicad-cli` rejected the schematic with "No parent for
extended symbol" because the sub-symbol names no longer matched their parent.

**Behavior after fix:** Sub-symbol names keep their short form
(`R_0_1`, `R_1_1`); only the root `(symbol "R" …)` line is renamed to
`(symbol "Device:R" …)` via `count=1`.

---

## bug2_id_property.kicad_sch

**Produced by:** `_find_symbol_def` when targeting KiCad library files
distributed with older KiCad builds (library format version 20211014).

**Bug:** Older library files include `(id N)` tokens inside property blocks,
e.g. `(property "Reference" "R" (id 0) (at …))`.  KiCad 9 uses a newer
schema that rejects the `(id N)` token.

**Failure before fix:** `kicad-cli` ERC/DRC exited with a parse error when
loading the schematic.

**Behavior after fix:** `_find_symbol_def` strips all `(id N)` tokens from
the embedded symbol definition before writing.

---

## bug3_paren_indent.kicad_sch

**Produced by:** `_embed_lib_symbol` string-template approach, where the
closing `)` for `(lib_symbols …)` was placed at column 0 instead of being
indented to column 2.

**Bug:** The `)` at column 0 closed the outer `(kicad_sch …)` node
prematurely.  All placed symbol nodes that followed fell outside the root
S-expression, producing an unbalanced document.

**Failure before fix:** The schematic file loaded with a structural
deserialization error; placed symbols were invisible in KiCad.

**Behavior after fix:** The closing paren for `(lib_symbols …)` is emitted at
the correct 2-space indent, keeping all subsequent nodes inside
`(kicad_sch …)`.

---

## bug4_no_instances.kicad_sch

**Produced by:** `cmd_add_component` before the `(instances …)` block was
added to placed symbol nodes.

**Bug:** Every placed `(symbol …)` node was missing its `(instances …)` child.
KiCad 9 requires this block to map the placed symbol to a project reference.

**Failure before fix:** `kicad-cli` exported an empty `<components/>` XML
element; the schematic BOM showed 0 components even though symbols were
visually present.

**Behavior after fix:** Every placed symbol now includes
`(instances (project "…" (path "/" (reference "R1") (unit 1))))`.
