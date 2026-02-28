---
name: kicad-pcb
version: 1.0.0
description: Automate PCB design with KiCad. Create schematics, design boards, export Gerbers, order from PCBWay. Full design-to-manufacturing pipeline.
license: GPL-3.0-or-later
keywords: [pcb, kicad, electronics, gerber, schematic, circuit, pcbway, manufacturing, hardware]
triggers: ["pcb design", "kicad", "circuit board", "schematic", "gerber", "pcbway", "electronics project"]
---

# 🔧 KiCad PCB Automation

**Design → Prototype → Manufacture**

Automate PCB design workflows using KiCad. From natural language circuit descriptions to manufacturing-ready Gerber files.

## ⛔ ABSOLUTE RULES — READ FIRST

1. **NEVER write `.kicad_sch` or `.kicad_pcb` files by hand.** Do not generate
   KiCad s-expression syntax directly. Always use `new-from-netlist` or
   `apply-netlist` to compile Circuit IR JSON into schematics. Hand-written
   KiCad files will have broken symbol inheritance (`extends` with no base),
   missing pin geometry, wrong pin counts, unconnected nets, and no ownership
   marker — they will fail DRC and cannot be reliably edited in KiCad.

2. **ALWAYS run `search-symbols` before writing any Circuit IR JSON.** Symbol
   names differ between KiCad versions. Use the exact `Lib:SymbolName` returned
   by `search-symbols` — never guess or invent symbol IDs.

3. **Use the Circuit IR pipeline for all schematic generation.** Write the
   complete Circuit IR JSON first (all components + all nets), then compile it
   with `new-from-netlist`. Do not use `add-component` in a loop as a
   substitute — it bypasses ownership, managed-sheet isolation, and net wiring.

---

## What This Skill Does

1. **Design** — Create schematics from circuit descriptions
2. **Layout** — Design PCB layouts with component placement
3. **Verify** — Run DRC checks, generate previews for review
4. **Export** — Generate manufacturing files (Gerbers, drill files, BOM)
5. **Order** — Prepare and place orders on PCBWay

## Requirements

### KiCad Installation

```bash
# Ubuntu/Debian
sudo add-apt-repository ppa:kicad/kicad-8.0-releases
sudo apt update
sudo apt install kicad

# Verify CLI
kicad-cli --version
```

### Python Dependencies

```bash
python3 -m pip install cairosvg pillow  # optional — enables PNG schematic preview
```

## Installation

This is a private skill — install it by copying this folder into an OpenClaw
skills directory.

**Workspace skill** (available to agents in this workspace only):

```bash
cp -r kicad-pcb/ <path-to-openclaw-workspace>/skills/kicad-pcb/
```

**Shared skill** (available to all agents on this machine):

```bash
cp -r kicad-pcb/ ~/.openclaw/skills/kicad-pcb/
```

Or, if you're working from the repo, symlink instead of copying so changes take
effect without re-copying:

```bash
ln -s "$(pwd)/kicad-pcb" ~/.openclaw/skills/kicad-pcb
```

Start a new OpenClaw session to pick up the skill.

> **Note:** The `{baseDir}` placeholder in the commands below is automatically
> expanded by OpenClaw to the skill's installed folder path. You do not need to
> type it literally — it is provided to the agent at runtime.
>
> OpenClaw executes skill commands in the same host environment as the gateway
> process. Install Python dependencies into that same `python3` environment
> (avoid venv-only installs unless OpenClaw itself is started from that venv).

## Verifying Installation

Run the following after starting a new OpenClaw session:

```bash
# 1. Confirm the skill's entrypoint is reachable
python3 {baseDir}/scripts/kicad_pcb.py --help

# 2. Check system prerequisites
python3 {baseDir}/scripts/kicad_pcb.py doctor

# 3. Confirm kicad-cli is on PATH
kicad-cli --version

# 4. Confirm optional Python modules are importable by this python3
python3 -c "import cairosvg, PIL; print('python deps ok')"
```

Expected output for step 1: usage listing with all subcommands (`new`, `info`,
`drc`, `export-gerbers`, etc.).

Expected output for step 2: a green check (or a clear list of what is missing)
for `kicad-cli`, Python version, and optional packages (`cairosvg`, `pillow`).

If `kicad-cli` is missing, follow the [KiCad Installation](#requirements) steps
below. If `python3 {baseDir}/scripts/kicad_pcb.py` fails with `No such file or
directory`, the skill folder is not in the right place — confirm the
`kicad-pcb/` directory exists under `~/.openclaw/skills/` (or your workspace
`skills/` folder) and that you started a fresh OpenClaw session.

## Quick Start

```bash
# 1. Discover correct symbol IDs for your KiCad version
python3 {baseDir}/scripts/kicad_pcb.py search-symbols "555 timer"
python3 {baseDir}/scripts/kicad_pcb.py search-symbols "resistor"
python3 {baseDir}/scripts/kicad_pcb.py search-symbols "LED"

# 2. Write circuit.json (Circuit IR) using the exact symbol IDs from step 1
#    See "Circuit IR Pipeline Workflow" section below for the JSON format.

# 3. Create project from Circuit IR (all components + nets in one shot)
python3 {baseDir}/scripts/kicad_pcb.py new-from-netlist \
    --name LED_Blinker \
    --netlist circuit.json \
    --symbols-dir /usr/share/kicad/symbols \
    --mode internal

# 4. Generate schematic preview (for review)
python3 {baseDir}/scripts/kicad_pcb.py preview-schematic

# 5. Run design rule check
python3 {baseDir}/scripts/kicad_pcb.py drc

# 6. Export manufacturing files
python3 {baseDir}/scripts/kicad_pcb.py export-gerbers

# 7. Prepare PCBWay order
python3 {baseDir}/scripts/kicad_pcb.py pcbway-quote --quantity 5
```

## Commands

### Project Management

| Command | Description |
|---------|-------------|
| `new <name>` | Create new KiCad project |
| `open <path>` | Open existing project |
| `info` | Show current project info |
| `doctor` | Check system config and diagnose issues |


### Schematic Design

| Command | Description |
|---------|-------------|
| `add-component <LIB:SYM> <REF>` | Add component to schematic |
| `connect --from X,Y --to X,Y` | Wire two coordinates together |
| `add-net NAME [--x X] [--y Y]` | Create named net label at position |
| `preview-schematic` | Generate schematic image |
| `erc` | Run electrical rules check |
| `info-sch [--json]` | Inspect current schematic (symbols, ownership, pin→net bindings) |

### Symbol Discovery (ALWAYS do this before writing Circuit IR JSON)

Symbol names differ between KiCad versions (e.g. `Device:CP` in KiCad 8 became
`Device:C_Polarized` in KiCad 9). **Before writing any Circuit IR JSON**, use
`search-symbols` to find the exact symbol ID for the installed version:

```bash
# Find the correct symbol for a polarized capacitor
{baseDir}/scripts/kicad_pcb.py search-symbols "polarized capacitor"
# → Device:C_Polarized  (2 pins)  — Polarized capacitor

# Find the correct symbol for a potentiometer
{baseDir}/scripts/kicad_pcb.py search-symbols "potentiometer"
# → Device:R_Potentiometer  (3 pins)  — Potentiometer

# Find op-amp symbols
{baseDir}/scripts/kicad_pcb.py search-symbols "operational amplifier NE5532"
# → Amplifier_Operational:NE5532  (8 pins)

# Search within an explicit library directory
{baseDir}/scripts/kicad_pcb.py search-symbols "audio jack" --symbols-dir /usr/share/kicad/symbols
```

> **Speed tip:** Symbol search parses `.kicad_sym` files on first use and
> caches results in `~/.openclaw/kicad-pcb/symbol_index.db`. The very first
> query may take a few seconds; all subsequent queries are near-instant. To
> pre-populate the cache after installing or upgrading KiCad, run:
> ```bash
> {baseDir}/scripts/kicad_pcb.py build-symbol-index
> # (use --symbols-dir /usr/share/kicad/symbols to target a specific dir)
> ```

> **Extends-chain symbols:** Many KiCad library symbols (especially op-amp
> families such as `Amplifier_Operational:NE5532`) inherit all their pins from
> a base symbol via `(extends ...)`. The search cache and pin validator both
> follow these chains automatically, so the pin count shown by `search-symbols`
> is always the full resolved count. If you suspect a broken chain, use
> `debug-symbol` to inspect the full pin list and extends relationship:
> ```bash
> {baseDir}/scripts/kicad_pcb.py debug-symbol Amplifier_Operational:NE5532
> # 🔬 debug-symbol: Amplifier_Operational:NE5532
> #   Extends: Amplifier_Operational:LM2904
> #   Pins (8): 1  2  3  4  5  6  7  8
> ```

The output lists `Lib:SymbolName  (N pins)  — description`.  Copy the
`Lib:SymbolName` exactly into your Circuit IR JSON `"symbol"` field.

### Pin Name Reference for Common Symbols

Pin names must match the KiCad library exactly. Common footguns:

| Symbol | Pin names | Notes |
|--------|-----------|-------|
| `Device:R` | `1`, `2` | |
| `Device:C` | `1`, `2` | |
| `Device:C_Polarized` | `1`, `2` | Pin `1` = positive (+), pin `2` = negative (−). The `+` mark is visual only — the pin *number* is `1`/`2`. |
| `Device:R_Potentiometer` | `1`, `2`, `3` | 1 & 3 = outer lugs, 2 = wiper |
| `Connector:AudioJack3` | `T`, `R`, `S` | Tip, Ring, Sleeve — **not** `1`/`2`/`3` |
| `Amplifier_Operational:NE5532` | `1`–`8` | 3=+A, 2=−A, 1=outA, 5=+B, 6=−B, 7=outB, 4=V−, 8=V+ |
| `Device:LED` | `A`, `K` | Anode, Kathode |
| `power:VCC` / `power:GND` | `1` | Single-pin power symbols |

When in doubt, run `search-symbols` — the pin count shown is the authoritative
count (extends chains are resolved automatically). If your IR uses a pin name
not in the library the tool will reject the netlist with `SYMBOL_NOT_FOUND`,
`SYMBOL_HAS_NO_PINS` (broken extends chain), or `PIN_INVALID`. Use
`debug-symbol <Lib:Name>` to inspect the exact pin list before writing IR JSON.

### Circuit IR Pipeline (preferred for LLM-driven generation)

| Command | Description |
|---------|-------------|
| `search-symbols <keywords>` | **Search installed libraries for symbol IDs** (use before writing IR JSON) |
| `build-symbol-index [--symbols-dir DIR]` | Pre-populate symbol search cache (run once after installing KiCad) |
| `debug-symbol <Lib:Name> [--symbols-dir DIR]` | Show resolved pin list and extends chain for one symbol (use to diagnose pin count issues) |
| `new-from-netlist --name N --netlist circuit.json` | Create project from Circuit IR JSON (strict by default) |
| `compile-netlist --name N --netlist circuit.json` | Alias for `new-from-netlist` |
| `apply-netlist --netlist circuit.json [--force]` | Apply IR to open project's managed region |

**Common flags** (all three commands): `--symbols-dir`, `--mode internal\|kicad`, `--dry-run`.

#### Circuit IR JSON Schema (EXACT FORMAT — do not invent a different structure)

The schema is validated strictly by Pydantic (`additionalProperties: false`). Any
extra field (e.g. `type`, `pins` on a component, a nested `metadata` object) will
cause `❌ Circuit IR schema validation failed` and the tool will refuse to run.

```json
{
  "version": "1",
  "components": [
    {
      "ref": "U1",
      "symbol": "Amplifier_Operational:NE5532",
      "value": "NE5532"
    },
    {
      "ref": "R1",
      "symbol": "Device:R",
      "value": "10k",
      "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"
    }
  ],
  "nets": [
    {
      "name": "VCC",
      "pins": [
        {"ref": "U1", "pin": "8"},
        {"ref": "R1", "pin": "1"}
      ]
    }
  ]
}
```

**Required top-level keys:** `version` (string `"1"`), `components` (array), `nets` (array).  
**Component fields:** `ref` (required), `symbol` (required — KiCad lib ID like `"Device:R"`), `value` (optional), `footprint` (optional), `fields` (optional dict).  
**Forbidden component fields:** `type`, `pins`, `nets`, `connections`, or any other key not listed above.  
**Net fields:** `name` (required), `pins` (required — array of `{"ref": "X", "pin": "Y"}` objects).  
**Pin values** in nets are pin *numbers* (e.g. `"1"`, `"2"`) or named pins (e.g. `"T"`, `"R"`, `"S"` for `AudioJack3`) — check the Pin Name Reference table above.

#### ❌ WRONG Circuit IR formats — these will always fail validation

The following are real examples of broken formats the LLM tends to invent. None of
them pass `new-from-netlist`. Do not use any of them.

```jsonc
// ❌ WRONG: "title" instead of "version", inline "pins" on component,
//           no "symbol" field, "nets" as a dict of "REF-PIN" strings
{
  "title": "My Circuit",
  "components": [
    {"ref": "R1", "value": "10k", "pins": {"1": "VCC", "2": "GND"}}
  ],
  "nets": {
    "VCC": ["U1-8", "R1-1"],
    "GND": ["R1-2"]
  }
}

// ❌ WRONG: nested "metadata" wrapper, missing "symbol", "type" field on component
{
  "metadata": {"version": 1, "description": "Headphone Amp"},
  "components": [
    {"ref": "C1", "type": "electrolytic", "value": "10u",
     "pins": {"+": "VCC", "-": "GND"}}
  ]
}

// ❌ WRONG: EDA-tool-style format — "nets" is a name-only list, per-component
//           "pins" array carries the net assignment, "type" field on component,
//           "version" is integer 1 instead of string "1", no "symbol" field.
//           This is a real format produced by LLMs imitating SPICE/EDA netlists.
{
  "metadata": {"title": "My Amp", "version": 1},
  "nets": [
    {"name": "VPLUS15"},
    {"name": "GND"}
  ],
  "components": [
    {"ref": "U1", "value": "NE5532", "type": "IC", "pins": [
      {"num": 8, "name": "V+", "net": "VPLUS15"},
      {"num": 4, "name": "V-", "net": "GND"}
    ]},
    {"ref": "C1", "value": "100nF", "type": "C", "pins": [
      {"num": 1, "net": "VPLUS15"},
      {"num": 2, "net": "GND"}
    ]}
  ]
}

// ❌ WRONG: hand-written .kicad_sch with old version, fake generator, no wires
// (kicad_sch (version 20211014) (generator "OpenAI-GPT") ...)
// If new-from-netlist fails, fix the netlist — do NOT fall back to writing
// .kicad_sch by hand. Hand-written output has no validation and will be wrong.
```

**If you see `❌ Circuit IR schema validation failed`**: your netlist JSON has the
wrong structure. Discard it, rewrite it using the exact format shown above
(see also the pre-flight checklist and error recovery loop in Step 2 of the workflow),
and retry `new-from-netlist`. Do not write a `.kicad_sch` file manually.



### PCB Layout

| Command | Description |
|---------|-------------|
| `import-netlist` | Export netlist from schematic and report components ready for PCB layout |
| `auto-place` | Auto-place components |
| `auto-route` | Auto-route traces |
| `set-board-size <W>x<H>` | Set board dimensions (mm) |
| `preview-pcb` | Generate PCB preview images |
| `drc` | Run design rules check |

### Manufacturing Export

| Command | Description |
|---------|-------------|
| `export-gerbers` | Export Gerber files |
| `export-drill` | Export drill files |
| `export-bom` | Export bill of materials |
| `export-pos` | Export pick-and-place file |
| `export-3d` | Export 3D model (STEP/GLB) |
| `package-for-fab` | Create ZIP with all files |

### PCBWay Integration

| Command | Description |
|---------|-------------|
| `pcbway-quote` | Get instant cost estimate |

> PCBWay upload/order requires manual web upload at pcbway.com — no API available.

## Workflow: Natural Language to PCB

### Step 1: Describe Your Circuit

Tell me what you want to build:

> "I need a simple 555 timer circuit that blinks an LED at about 1Hz. 
> Should run on 9V battery, through-hole components for easy soldering."

### Step 2: I'll Generate the Design

```bash
# First: discover the exact symbol IDs for the installed KiCad version
{baseDir}/scripts/kicad_pcb.py search-symbols "555 timer"
{baseDir}/scripts/kicad_pcb.py search-symbols "LED"
{baseDir}/scripts/kicad_pcb.py search-symbols "resistor"
{baseDir}/scripts/kicad_pcb.py search-symbols "capacitor"
```

Then write the complete Circuit IR JSON (all components + all nets at once;
**do not** write `.kicad_sch` by hand):

```json
{
  "version": "1",
  "components": [
    {"ref": "U1", "symbol": "Timer:NE555", "value": "NE555", "footprint": "Package_DIP:DIP-8_W7.62mm"},
    {"ref": "R1", "symbol": "Device:R",   "value": "47k",  "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"},
    {"ref": "R2", "symbol": "Device:R",   "value": "47k",  "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"},
    {"ref": "C1", "symbol": "Device:C",   "value": "10uF", "footprint": "Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm"},
    {"ref": "D1", "symbol": "Device:LED", "value": "LED",  "footprint": "LED_THT:LED_D3.0mm"}
  ],
  "nets": [
    {"name": "VCC",  "pins": [{"ref": "U1", "pin": "8"}, {"ref": "R1", "pin": "1"}]},
    {"name": "GND",  "pins": [{"ref": "U1", "pin": "1"}, {"ref": "C1", "pin": "2"}]},
    {"name": "OUT",  "pins": [{"ref": "U1", "pin": "3"}, {"ref": "R2", "pin": "1"}, {"ref": "D1", "pin": "A"}]}
  ]
}
```

Then compile it:

```bash
# Compile Circuit IR → KiCad schematic (never write .kicad_sch directly)
{baseDir}/scripts/kicad_pcb.py new-from-netlist \
    --name LED_Blinker_555 \
    --netlist circuit.json \
    --symbols-dir /usr/share/kicad/symbols \
    --mode internal
```

#### ✅ Pre-flight self-check — run this mentally before calling `new-from-netlist`

Go through this checklist on your Circuit IR JSON before calling the tool.
If any item fails, fix the JSON first. Do not call the tool with a failing item.

**Top-level structure:**
- [ ] Only three top-level keys: `"version"`, `"components"`, `"nets"` — nothing else
- [ ] `"version"` is the **string** `"1"`, not integer `1`, not missing
- [ ] No `"metadata"`, `"title"`, `"description"` or other wrapper objects at top level

**Every component entry:**
- [ ] Has `"ref"` and `"symbol"` (lib ID like `"Device:R"`) — both required
- [ ] Does **NOT** have `"type"`, `"pins"`, `"nets"`, `"connections"`, `"polarity"`, `"num"` or any other extra key
- [ ] `"symbol"` is a real KiCad lib ID obtained from `search-symbols`, not an invented string

**Every net entry:**
- [ ] Has `"name"` (string) and `"pins"` (array) — both required
- [ ] `"pins"` is an **array of objects** `[{"ref": "X", "pin": "Y"}, ...]`, not a list of strings, not a dict
- [ ] Net assignments live **only in the `nets` array** — not inside component entries
- [ ] Every `"ref"` in a net's pins matches a ref in `"components"`
- [ ] Every `"pin"` value is a valid pin for that component's symbol (check with `debug-symbol` if unsure)

**Quick sanity check (run in your head):**
```
For each net: does it have at least 2 pin entries? (a single-pin net is usually a bug)
For each component: is every pin of that component accounted for in at least one net?
Are there components in "components" that are never referenced in any net? (unused component — likely a mistake)
```

#### ❌ Error recovery loop

When `new-from-netlist` returns an error:

1. **Read the full error message** — it names the exact problem (`schema validation failed` / `PIN_INVALID: Net X references R1 pin 3` / `IR_SEMANTIC_INVALID: duplicate refs`).
2. **Fix the Circuit IR JSON** using the error message as a guide. Do NOT guess — read the error.
3. **Re-run `new-from-netlist`** on the corrected JSON.
4. Repeat until exit 0. Accept up to 3 fix-and-retry cycles before asking the user for clarification.
5. **Never write `.kicad_sch` by hand** — not on the first failure, not on the third. If after 3 retries the tool still fails, report the exact error to the user and ask for guidance.

On success the tool prints two paths:
- **Root schematic** (`<name>.kicad_sch`) — a thin wrapper that references the managed sub-sheet. Contains no symbols.
- **Managed schematic** (`OpenClaw_Managed.kicad_sch`) — contains all the actual components and nets.

**⚠️ You MUST deliver BOTH files to the user.** They must be placed in the same directory.
The user opens the **root** file (`<name>.kicad_sch`) in KiCad — not `OpenClaw_Managed.kicad_sch`.
Opening only the managed file will appear to work but the sheet hierarchy UUID will not resolve.

When you deliver the results, explicitly say:
> "Save both files in the same folder and open `<name>.kicad_sch` in KiCad (not `OpenClaw_Managed.kicad_sch`)."



### Step 3: Review & Confirm
- Schematic preview image
- Component list (BOM)
- Calculated values (resistors for timing, etc.)

You confirm or request changes.

### Step 4: PCB Layout

```bash
# Import to PCB
{baseDir}/scripts/kicad_pcb.py import-netlist

# Auto-layout (or manual guidance)
{baseDir}/scripts/kicad_pcb.py auto-place --spacing 15
{baseDir}/scripts/kicad_pcb.py set-board-size 50x30

# Preview
{baseDir}/scripts/kicad_pcb.py preview-pcb
```

### Step 5: Manufacturing

```bash
# Run final checks
{baseDir}/scripts/kicad_pcb.py drc --strict

# Export everything
{baseDir}/scripts/kicad_pcb.py package-for-fab --output LED_Blinker_fab.zip

# Get quote
{baseDir}/scripts/kicad_pcb.py pcbway-quote --quantity 10 --layers 2 --thickness 1.6
```

## Common Circuit Templates

> No built-in templates are included in v1.0.0. Build circuits using
> `add-component` and `connect` commands as shown in the workflow above.

## Configuration

Create `~/.kicad-pcb/config.json`:

```json
{
  "default_fab": "pcbway",
  "pcbway": {
    "email": "your@email.com",
    "default_options": {
      "layers": 2,
      "thickness": 1.6,
      "color": "green",
      "surface_finish": "hasl"
    }
  },
  "kicad_path": "/usr/bin/kicad-cli",
  "projects_dir": "~/kicad-projects",
  "auto_backup": true
}
```

## Design Review Protocol

Before ordering, I'll always:

1. **Show schematic** — Visual confirmation of circuit
2. **Show PCB renders** — Top, bottom, 3D view
3. **List BOM** — All components with values
4. **Report DRC** — Any warnings or errors
5. **Show quote** — Cost breakdown before ordering

**I will NOT auto-order without explicit confirmation.**

## PCBWay Order Flow (Current)

1. Export Gerbers + drill files
2. Create ZIP package
3. **Manual step**: You upload to pcbway.com
4. **Future**: Automated upload + cart placement

## Cost Reference

PCBWay typical pricing (2-layer, 100x100mm, qty 5):
- Standard (5-7 days): ~$5
- Express (3-4 days): ~$15
- Shipping: ~$15-30 DHL

## Safety Notes

⚠️ **High Voltage Warning**: This skill does not validate electrical safety. For mains-connected circuits, consult a qualified engineer.

⚠️ **No Auto-Order (Yet)**: Cart placement requires your explicit confirmation.

## File Safety

All schematic (`.kicad_sch`) and PCB (`.kicad_pcb`) write operations include a
basic S-expression syntax check before committing to disk. If the generated
output fails the balanced-parentheses or root-node check, the write is aborted
and the original file is left untouched. A `ParseError` is raised describing
the failure.

## Circuit IR Pipeline Workflow ← USE THIS FOR ALL SCHEMATIC GENERATION

**This is the only correct way to generate schematics.** Never write
`.kicad_sch` files directly. Write Circuit IR JSON → compile with
`new-from-netlist`. The tool reads real symbol definitions from the installed
KiCad libraries, embeds them correctly, wires all nets, and produces a
validated, KiCad-openable schematic in one step.

The preferred way for an LLM to generate circuits is via **Circuit IR** — a
structured JSON that the tool compiles deterministically into a KiCad schematic.

### Circuit IR format (minimal)

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

### Create a new project from IR

```bash
# Requires kicad-cli (strict validation); use --mode internal to skip
{baseDir}/scripts/kicad_pcb.py new-from-netlist \
    --name MyProject \
    --netlist circuit.json \
    --symbols-dir /usr/share/kicad/symbols \
    --mode kicad
```

### Update a project's schematic in-place

```bash
{baseDir}/scripts/kicad_pcb.py open MyProject/

# First time on a non-owned schematic: use --force to adopt it
{baseDir}/scripts/kicad_pcb.py apply-netlist \
    --netlist updated_circuit.json \
    --force

# Subsequent updates: marker is already present, --force not needed
{baseDir}/scripts/kicad_pcb.py apply-netlist --netlist updated_circuit.json
```

### Inspect generated schematic

```bash
{baseDir}/scripts/kicad_pcb.py info-sch --json
```

Returns: `owned_by_openclaw`, `symbols[]`, `pin_net_bindings[]`, `warnings[]`.

### Ownership and `--force`

Generated schematics carry `OpenClaw:generated=v1` as an off-canvas text marker.
`apply-netlist` refuses to modify a schematic that lacks this marker (safety
guard against overwriting hand-authored files). Use `--force` once to adopt a
schematic and insert the marker; subsequent updates work without `--force`.

### Validation modes

| Mode | Requirement | Used by default for |
|------|-------------|---------------------|
| `internal` | None (built-in lint) | `apply-netlist` |
| `kicad` | `kicad-cli` installed | `new-from-netlist`, `compile-netlist` |

Pass `--mode internal` to any command to skip the `kicad-cli` requirement.

---

## Changelog

### v1.0.0
- Initial release
- KiCad CLI integration
- Schematic/PCB preview generation
- Gerber export
- PCBWay quote integration
- Template system

---
