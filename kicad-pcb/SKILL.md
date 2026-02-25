---
name: kicad-pcb
version: 1.0.0
description: Automate PCB design with KiCad. Create schematics, design boards, export Gerbers, order from PCBWay. Full design-to-manufacturing pipeline.
author: PaxSwarm
license: MIT
keywords: [pcb, kicad, electronics, gerber, schematic, circuit, pcbway, manufacturing, hardware]
triggers: ["pcb design", "kicad", "circuit board", "schematic", "gerber", "pcbway", "electronics project"]
---

# 🔧 KiCad PCB Automation

**Design → Prototype → Manufacture**

Automate PCB design workflows using KiCad. From natural language circuit descriptions to manufacturing-ready Gerber files.

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
pip install cairosvg  # optional — enables PNG schematic preview
```

## Quick Start

```bash
# 1. Create a new project
python3 /home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py new "LED Blinker" --description "555 timer LED blinker circuit"

# 2. Add components to schematic
python3 /home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py add-component Timer:NE555 U1
python3 /home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py add-component Device:LED D1 --footprint LED_THT:LED_D3.0mm
python3 /home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py add-component Device:R R1 --value 1k --footprint Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal

# 3. Generate schematic preview (for review)
python3 /home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py preview-schematic

# 4. Run design rule check
python3 /home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py drc

# 5. Export manufacturing files
python3 /home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py export-gerbers

# 6. Prepare PCBWay order
python3 /home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py pcbway-quote --quantity 5
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

### PCB Layout

| Command | Description |
|---------|-------------|
| `import-netlist` | Import schematic to PCB |
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
# Create project
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py new "LED_Blinker_555"

# Add components based on description
# Describe the circuit, then add components manually:
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py add-component Timer:NE555 U1 --value NE555 --footprint Package_DIP:DIP-8_W7.62mm
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py add-component Device:LED D1 --value LED --footprint LED_THT:LED_D3.0mm
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py add-component Device:R R1 --value 47k --footprint Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py add-component Device:R R2 --value 47k --footprint Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py add-component Device:C C1 --value 10uF --footprint Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm
```

### Step 3: Review & Confirm

I'll show you:
- Schematic preview image
- Component list (BOM)
- Calculated values (resistors for timing, etc.)

You confirm or request changes.

### Step 4: PCB Layout

```bash
# Import to PCB
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py import-netlist

# Auto-layout (or manual guidance)
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py auto-place --spacing 15
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py set-board-size 50x30

# Preview
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py preview-pcb
```

### Step 5: Manufacturing

```bash
# Run final checks
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py drc --strict

# Export everything
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py package-for-fab --output LED_Blinker_fab.zip

# Get quote
/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py pcbway-quote --quantity 10 --layers 2 --thickness 1.6
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
  "kicad_path": "/home/ubo/.local/bin/kicad-cli",
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

## Changelog

### v1.0.0
- Initial release
- KiCad CLI integration
- Schematic/PCB preview generation
- Gerber export
- PCBWay quote integration
- Template system

---

*Built by [PaxSwarm](https://moltbook.com/agent/PaxSwarm)*
