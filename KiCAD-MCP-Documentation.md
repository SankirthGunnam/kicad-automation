# KiCAD MCP Server — How It All Works

A complete reference explaining how Claude controls KiCAD schematics through the MCP server: the setup, the configuration, and the tool workflow.

---

## Table of Contents
1. [What Is MCP?](#what-is-mcp)
2. [Architecture Overview](#architecture-overview)
3. [Installation & Setup](#installation--setup)
4. [Configuration](#configuration)
5. [How Claude Edits a Schematic](#how-claude-edits-a-schematic)
6. [Key MCP Tools Used](#key-mcp-tools-used)
7. [Clean Schematic Strategy (Net Labels vs Wires)](#clean-schematic-strategy)
8. [Troubleshooting & Fixes Applied](#troubleshooting--fixes-applied)
9. [Project Files Reference](#project-files-reference)

---

## What Is MCP?

**MCP (Model Context Protocol)** is an open standard developed by Anthropic that lets AI models like Claude communicate with external tools and services through a structured JSON-RPC interface.

Instead of Claude relying purely on its training knowledge, MCP servers expose **callable tools** that Claude can invoke at runtime — like an API for the real world.

```
User ──► Claude (AI) ──► MCP Server ──► KiCAD / Python / File System
                  ◄──────────────────────────────────────────
```

When you ask Claude *"add a 100nF capacitor at position (130, 71)"*, Claude doesn't write a Python script and hope for the best. It calls the `sch_add_symbol` MCP tool directly, which the server executes using KiCAD's embedded Python scripting engine. The result comes back to Claude as structured JSON.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Claude Desktop                          │
│                                                              │
│   User Message ──► Claude AI ──► Tool Call (JSON-RPC)       │
│                         ▲                                    │
│                         │ Tool Result                        │
└─────────────────────────┼───────────────────────────────────┘
                          │ stdio (stdin/stdout)
┌─────────────────────────┼───────────────────────────────────┐
│              KiCAD MCP Server (Node.js)                      │
│                                                              │
│   index.js  ──► Tool Router ──► Python Bridge               │
│                                      │                       │
│                              KiCad Python 3.9                │
│                         (embedded in KiCad.app)              │
│                                      │                       │
│                              kicad-skip / pcbnew             │
│                         (reads/writes .kicad_sch files)      │
└─────────────────────────────────────────────────────────────┘
                          │
              ┌───────────┴───────────┐
              │   .kicad_sch file     │  ← schematic on disk
              │   .kicad_pcb file     │  ← PCB layout on disk
              └───────────────────────┘
```

**Key components:**

| Component | Role |
|-----------|------|
| **Claude Desktop** | The AI frontend; receives user requests and calls MCP tools |
| **KiCAD-MCP-Server** | Node.js server that bridges Claude to KiCAD's Python API |
| **KiCad Python 3.9** | KiCAD's embedded Python, runs inside `KiCad.app` |
| **kicad-skip** | Python library that parses and edits `.kicad_sch` S-expression files |
| **kicad-cli** | KiCAD's command-line tool used for exports (PDF, Gerber, etc.) |

---

## Installation & Setup

### 1. Clone the MCP Server

```bash
git clone https://github.com/mixelpixx/KiCAD-MCP-Server
cd KiCAD-MCP-Server
npm install
npm run build
```

This produces `dist/index.js` — the entry point Claude launches.

### 2. Install Python Dependencies into KiCAD's Embedded Python

This is the most critical step. KiCAD ships with its **own isolated Python 3.9**, separate from your system Python. All Python packages must be installed into *that* interpreter, not `pip3` / system Python.

```bash
# Path to KiCAD's embedded Python on macOS
KICAD_PY="/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"

# Install required packages into KiCAD's Python
$KICAD_PY -m pip install kicad-skip sexpdata Pillow colorlog pydantic requests python-dotenv
```

> **Why?** The MCP server calls this specific Python binary (set via `KICAD_PYTHON` env var). If packages are only in your system Python, the server will throw `ModuleNotFoundError: No module named 'sexpdata'` at startup.

### 3. Verify cairo Library (macOS)

On macOS, KiCAD needs `libcairo` for rendering. If installed via Homebrew, it may not be on the dynamic linker path by default.

```bash
brew install cairo   # if not already installed
```

The path `/opt/homebrew/opt/cairo/lib` must be added to `DYLD_LIBRARY_PATH` in the MCP config (see next section).

---

## Configuration

The MCP server is registered in Claude Desktop's configuration file:

**File:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "kicad": {
      "command": "/Users/sankirthgunnam/.nvm/versions/node/v24.11.1/bin/node",
      "args": [
        "/Users/sankirthgunnam/KiCAD-MCP-Server/dist/index.js"
      ],
      "env": {
        "KICAD_PYTHON": "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3",
        "PYTHONPATH": "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/lib/python3.9/site-packages",
        "DYLD_LIBRARY_PATH": "/opt/homebrew/opt/cairo/lib",
        "LOG_LEVEL": "info"
      }
    }
  }
}
```

### Configuration Fields Explained

| Field | Purpose |
|-------|---------|
| `command` | Full path to Node.js (use `which node` or `nvm` path — must be absolute) |
| `args[0]` | Path to the compiled MCP server entry point |
| `KICAD_PYTHON` | Points to KiCAD's own Python binary — used to run all schematic editing scripts |
| `PYTHONPATH` | Ensures KiCAD's site-packages are on the Python module search path |
| `DYLD_LIBRARY_PATH` | macOS dynamic library path for `libcairo`, required for rendering/export |
| `LOG_LEVEL` | Server verbosity (`info`, `debug`, `error`) |

> **After any config change:** Fully quit Claude Desktop (`Cmd+Q`) and reopen it. The MCP server process is launched fresh on each Claude Desktop start.

---

## How Claude Edits a Schematic

### The Workflow

When Claude receives a request like *"create an Arduino Uno schematic"*, here is what actually happens:

```
1. Set Active Project
   └─► kicad_set_project(project_dir, sch_file)
       The server now knows which .kicad_sch file to read/write.

2. Query Existing State
   └─► sch_get_symbols()        — lists all placed components
   └─► sch_get_pin_positions()  — calculates exact XY of each pin

3. Place Components
   └─► sch_add_symbol()         — places a schematic symbol at XY coords
   └─► sch_add_power_symbol()   — places +5V, GND, VIN etc.

4. Connect Components
   └─► sch_add_label()          — adds net labels (clean connection method)
   └─► sch_add_wire()           — adds physical wire segments (short ones only)
   └─► sch_add_no_connect()     — marks intentionally unconnected pins

5. Validate
   └─► run_erc()                — runs Electrical Rules Check
   └─► schematic_quality_gate() — checks for open nets, missing PWR_FLAGs, etc.

6. Export
   └─► export_sch_pdf()         — renders schematic to PDF for review
```

### Coordinate System

KiCAD schematics use **millimeters** as the unit, with `(0, 0)` at the top-left. Y increases **downward** (screen coordinates).

```
(0,0) ──────────────────────► X (mm)
  │
  │    Component at (60, 55)
  │         ┌──────┐
  │         │ U3   │
  │         └──────┘
  ▼
  Y (mm)
```

All tool calls accept `x_mm` and `y_mm` parameters. Coordinates **snap to a 2.54 mm grid** by default (standard KiCAD schematic grid). Use `snap_to_grid=false` only for exact off-grid pin connections.

### How Components Know Where Their Pins Are

`sch_get_pin_positions(library, symbol_name, x_mm, y_mm)` returns the **absolute** XY position of every pin for a symbol placed at that location. This is what Claude uses to:
- Place net labels exactly on pin endpoints
- Place power symbols exactly on power pin endpoints
- Route short wires between adjacent components with exact coordinates

Example response:
```
Device:R @ (88.9, 48.26) rot=0:
  Pin 1: (88.90, 52.07) mm   ← bottom
  Pin 2: (88.90, 44.45) mm   ← top
```

---

## Key MCP Tools Used

### Project Management

| Tool | What It Does |
|------|-------------|
| `kicad_set_project` | Sets the active project directory and file paths |
| `kicad_get_version` | Returns KiCAD version info |
| `kicad_list_recent_projects` | Lists recently opened projects |

### Schematic Editing

| Tool | What It Does |
|------|-------------|
| `sch_add_symbol` | Place a component from a KiCAD library at (x, y) with rotation |
| `sch_add_power_symbol` | Place a power net symbol (+5V, GND, VIN, etc.) at (x, y) |
| `sch_add_label` | Place a net label at (x, y) — same name = same net |
| `sch_add_wire` | Draw a wire segment between two points |
| `sch_add_no_connect` | Place an ×-marker on intentionally unconnected pins |
| `sch_add_bus` | Add a bus line (for grouped signals) |
| `sch_delete_symbol` | Remove a placed symbol by reference |
| `sch_delete_wire` | Remove a wire by UUID |
| `sch_move_symbol` | Move a placed symbol to a new position |
| `sch_get_symbols` | List all symbols currently on the schematic |
| `sch_get_wires` | List all wires with their UUIDs and coordinates |
| `sch_get_pin_positions` | Calculate absolute pin XY for a symbol at given placement |
| `sch_get_net_names` | List all electrical nets in the schematic |
| `sch_build_circuit` | Build/replace an entire schematic from a circuit description |
| `sch_annotate` | Auto-assign reference designators (U1, R1, C1…) |

### Validation

| Tool | What It Does |
|------|-------------|
| `run_erc` | Run KiCAD's Electrical Rules Check |
| `schematic_quality_gate` | Higher-level check: open nets, missing power flags, etc. |
| `sch_check_power_flags` | Verify PWR_FLAG symbols are present on all power nets |

### Export

| Tool | What It Does |
|------|-------------|
| `export_sch_pdf` | Export schematic to PDF via kicad-cli |
| `export_svg` | Export schematic to SVG |
| `export_netlist` | Export netlist for PCB import |
| `export_bom` | Export Bill of Materials |
| `export_gerber` | Export Gerber manufacturing files |

---

## Clean Schematic Strategy

The single most important design decision for a readable schematic is **using net labels instead of routed wires** to connect distant pins.

### The Problem with Wires

When pins are far apart (e.g., the ATmega328P MCU in the center, and GPIO headers on the right), routing physical wires creates:
- Spaghetti lines crossing over components
- Confusing overlapping connections
- Difficult-to-read signal paths

### The Solution: Net Labels

**Rule:** In KiCAD, any two pins that have a **net label with the same name** are electrically connected — even if no wire physically joins them.

```
                        ── same name ──► electrically connected ──
Pin (ATmega PD0/RX) ──[RX0]       [RX0]── Pin (CH340C TXD)
                        no wire needed between them!
```

### Implementation Strategy Used

1. **Power symbols** (`+5V`, `GND`, `VIN`) placed **directly on** each power pin — no power buses needed.

2. **Net labels** placed at every signal pin — identical label name = electrical connection.

3. **Short wires only** for physically adjacent components where a direct connection is visually cleaner (e.g., crystal ↔ load caps, R2 ↔ D1 LED).

### Example: ATmega328P Connected to GPIO Header (Clean Way)

```
ATmega Pin PD2 (pin 4)        J3 Header Pin 3
at (170.24, 92.22) ─[D2]     [D2]─ at (205.74, 109.22)

No wire drawn. Net label "D2" placed at both pin endpoints.
```

### Example: ATmega VCC Power Connection (Clean Way)

```
                [+5V]          ← power symbol placed at pin
                  │
         ATmega VCC pin (7)
              at (155, 148.1)
```

---

## Troubleshooting & Fixes Applied

### Error: `ModuleNotFoundError: No module named 'sexpdata'`

**Cause:** Python packages installed into system Python instead of KiCAD's embedded Python.

**Fix:**
```bash
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \
  -m pip install kicad-skip sexpdata Pillow colorlog pydantic requests python-dotenv
```

---

### Error: `OSError: no library called "cairo-2" was found`

**Cause:** Homebrew's `libcairo` is installed but macOS's dynamic linker (`dyld`) can't find it.

**Fix:** Add to `claude_desktop_config.json` env section:
```json
"DYLD_LIBRARY_PATH": "/opt/homebrew/opt/cairo/lib"
```

---

### Error: Wires don't reach pin endpoints (off-grid snapping)

**Cause:** `snap_to_grid=true` rounds coordinates to 2.54 mm grid, but KiCAD symbol pins are sometimes at non-grid positions.

**Fix:** Use `snap_to_grid=false` with the **exact** coordinates returned by `sch_get_pin_positions`.

```python
# Wrong — may miss the pin
sch_add_wire(x1=170.0, y1=92.0, x2=170.0, y2=88.0, snap_to_grid=True)

# Correct — exact pin coordinates
sch_add_wire(x1=170.24, y1=92.22, x2=170.24, y2=88.0, snap_to_grid=False)
```

---

### Error: `Wire 'uuid' was not found` during deletion

**Cause:** Wire UUIDs are regenerated each time the schematic is modified. A UUID fetched before an edit is invalid after it.

**Fix:** Always call `sch_get_wires()` immediately before any wire deletion — never cache UUIDs across edits.

---

### ERC: `power_pin_not_driven`

**Cause:** KiCAD's ERC requires at least one `PWR_FLAG` symbol on every power net that doesn't have a driving source (e.g., a net connected only to passive components).

**Fix:** Add a `PWR_FLAG` symbol to `+5V` and `GND` nets:
```
sch_add_power_symbol(name="PWR_FLAG", x_mm=..., y_mm=...)
```

---

### ERC: `multiple_net_names` (connector GND conflicts)

**Cause:** When using `sch_build_circuit` with auto-generated nets, connector GND pins can be assigned both the connector's internal net name (`J4_GND`) and the global `GND` net.

**Fix:** Do not use `sch_build_circuit` with `auto_layout=True`. Instead, place all components manually with `sch_add_symbol`, then connect via explicit net labels and power symbols.

---

## Project Files Reference

```
~/kicad-projects/arduino-uno/
├── arduino-uno.kicad_pro    ← KiCAD project file
├── arduino-uno.kicad_sch    ← Schematic (S-expression text format)
├── arduino-uno.kicad_pcb    ← PCB layout
└── output/
    ├── schematic.pdf        ← Exported schematic PDF
    └── *.gbr                ← Gerber files (after export)

~/KiCAD-MCP-Server/
├── src/                     ← TypeScript source
├── dist/index.js            ← Compiled server (what Claude launches)
├── package.json
└── README.md

~/Library/Application Support/Claude/
└── claude_desktop_config.json   ← MCP server registration
```

### ATmega328P-P Pin Map Reference (KiCAD Symbol Quirks)

The KiCAD `ATmega328P-P` DIP-28 symbol has an **unconventional pin layout**:

| Pin | Function | Absolute Position (at U1: 154.94, 109.22) |
|-----|----------|------------------------------------------|
| 7 | VCC | (155.00, 148.10) ← **bottom** of symbol |
| 20 | AVCC | (157.54, 148.10) ← **bottom** |
| 8, 22 | GND | (155.00, 71.90) ← **top** of symbol |
| 21 | AREF | (139.76, 140.48) ← left side |
| 1 | ~RESET | (170.24, 102.38) ← right side |
| 2 | PD0/RX | (170.24, 97.30) ← right side |
| 3 | PD1/TX | (170.24, 94.76) |
| 4–6 | PD2–PD4 | (170.24, 92.22–87.14) |
| 9 | XTAL1 | (170.24, 125.24) |
| 10 | XTAL2 | (170.24, 122.70) |
| 11–13 | PD5–PD7 | (170.24, 84.60–79.52) |
| 14–19 | PB0–PB5 | (170.24, 140.48–127.78) |
| 23–28 | PC0–PC5 | (170.24, 117.62–104.92) |

> VCC and GND are swapped top-to-bottom compared to most chip symbols. This is valid — it reflects the physical DIP pinout — but it means GND symbols go at the **top** and +5V symbols at the **bottom** of this symbol.

---

*Generated during Arduino Uno schematic session — KiCAD MCP Pro v3.1.8 / KiCAD 10.0.1 / Claude Sonnet*
