# PCB and schematic design — vocabulary and basics

This note covers common terms and practical habits for electronic design, especially in **KiCad**. It complements hands-on work (drawing schematics, laying out boards, and tools like schematic autorouting in this repo).

---

## 1. Schematic vs PCB

| Concept | Meaning |
|--------|---------|
| **Schematic** | A **logical** drawing: symbols, pins, and how they should connect (**nets**). It does not define exact copper shapes on the board. |
| **PCB / layout** | The **physical** design: **footprints**, **pads**, **traces**, **vias**, board outline, and manufacturing rules. |
| **Netlist** | A machine-readable list of nets and which pins belong to each net. Usually generated from the schematic and used by the layout tool. |

**Basic workflow:** capture the schematic → assign **footprints** → place parts on the board → **route** traces → run **DRC** → export **Gerbers** (and drill files) for fabrication.

---

## 2. Schematic vocabulary

### Parts on the sheet

| Term | Meaning |
|------|---------|
| **Symbol** | The drawing of a component in the schematic library (resistor shape, IC rectangle, etc.). |
| **Instance / placed symbol** | One copy on **your** schematic (e.g. **U1**, **R2**). |
| **Reference designator** | Unique label for an instance: **U1**, **C3**, **Y1**. |
| **Value / fields** | Part value (`10k`, `22pF`) and metadata (footprint, datasheet link). |
| **Pin** | A connection point on a symbol. Has a **pin number** (matches the **pad** on the PCB) and often a **pin name** (e.g. `XTAL1`, `RST`). |

### Connectivity on the schematic

| Term | Meaning |
|------|---------|
| **Net** | The set of everything that must be **electrically connected** (same node). |
| **Wire** | A drawn connection between points on the **same sheet**. |
| **Label** | Gives a net a **name**. Local labels stay on one sheet. |
| **Global label** | Declares a net name **across the hierarchy** or across distance on a sheet; same text ⇒ same net (when rules allow). |
| **Hierarchical label / sheet** | Connects nets into parent/child sheets in multi-sheet designs. |
| **Junction** | A dot where wires meet; clarifies **T** connections in KiCad. |
| **Bus** | A grouped representation of related nets (less common on simple MCU boards). |
| **No-connect (NC)** | A flag on a pin: “unused on purpose,” so **ERC** does not flag it. |

### Power on the schematic

| Term | Meaning |
|------|---------|
| **Power symbol** | Library symbols for rails such as **+5V** and **GND** (often `#PWR` references in the file). |
| **PWR_FLAG** | Tells **ERC** that a power net is **intentionally supplied**, reducing false “power pin not driven” warnings when used correctly. |
| **Net naming** | Rails are still **nets**. Duplicate power **symbols** or **global labels** are often for readability; the important part is **one consistent net name** and **real connectivity** (wires or correct global/hierarchical naming). |

---

## 3. PCB / layout vocabulary

| Term | Meaning |
|------|---------|
| **Footprint** | The physical **pad pattern** and silkscreen for one part (from a footprint library or custom). |
| **Pad** | Copper shape you solder or reflow to; corresponds to schematic **pin number**. |
| **Trace** | Routed copper path on a layer between pads. |
| **Via** | Plated hole connecting copper on different layers. |
| **Layer** | Copper or documentation plane (e.g. **F.Cu** front, **B.Cu** back). |
| **Plane / pour** | Large copper fill; often **GND** on an inner or outer layer. |
| **Clearance** | Minimum allowed spacing between copper items (trace–trace, trace–pad, etc.). |
| **Design rules** | Your ruleset (clearances, track widths, via sizes) plus manufacturer limits. |

---

## 4. Checks and manufacturing

| Term | Meaning |
|------|---------|
| **ERC (Electrical Rules Check)** | Runs on the **schematic**: unconnected pins, conflicting drivers, power issues, etc. |
| **DRC (Design Rules Check)** | Runs on the **PCB**: spacing, unconnected items, board outline, etc. |
| **Gerber** | Industry-standard vector format for each copper/mask/silk layer sent to the fab. |
| **Drill file** | Defines hole positions and sizes (plated and non-plated as applicable). |
| **Pick and place** | Machine-readable centroid/rotation data for assembly (if you use automated assembly). |

---

## 5. Analog building blocks (example: MCU + crystal)

| Term | Meaning |
|------|---------|
| **Crystal (XTAL)** | Passive resonator; connects between the MCU’s **XTAL1** and **XTAL2** pins (when using the chip’s crystal oscillator mode). |
| **Load capacitors** | Small capacitors (often ~22 pF, depends on crystal **CL**) from **each crystal terminal to GND**. |
| **Reset circuit** | Usually a pull-up and/or RC so **RST** rises reliably after power (exact topology depends on the MCU datasheet). |
| **Decoupling capacitor** | Cap close to **VCC/GND** pairs to supply fast **local** charge and reduce noise (often 100 nF; bulk caps optional). |

---

## 6. Basic instructions (good habits)

### Schematic

1. **Use consistent net names** for supplies (**GND**, **+5V**, etc.) and match your **datasheet** pin names where it helps readability.
2. **Wire or label deliberately:** every pin should be either connected, tied to a named net, or marked **NC**.
3. Run **ERC** before you freeze the schematic; fix **errors**, understand **warnings**.
4. **PWR_FLAG** and power symbols: learn how your CAD expects power nets to be declared so ERC stays trustworthy.
5. For **crystals**, follow the vendor **CL** and layout guidelines (short traces, solid GND reference).

### PCB layout

1. **Footprint** every symbol before serious layout; verify **pin 1** orientation.
2. Place **decoupling** capacitors **next to** the IC power pins they serve.
3. Set **design rules** to match your fabricator’s capabilities (minimum trace/space, drill sizes).
4. Route **critical nets** (high-speed, sensitive analog, crystal) with extra care; keep loops small and returns clear (often **GND** nearby).
5. Run **DRC** until clean; visually inspect **Gerber** previews before ordering boards.

### Files and collaboration

1. Commit **source** projects (`.kicad_pro`, `.kicad_sch`, `.kicad_pcb`) and libraries you depend on; avoid relying only on exports.
2. When automating (scripts, autorouters), **back up** `.kicad_sch` before overwriting and verify the file opens in KiCad after changes.

---

## 7. Quick reference — same idea, two worlds

| Idea | Schematic | PCB |
|------|-----------|-----|
| Connection | Net / wire / label | Copper trace / via / plane |
| Component | Symbol instance | Footprint placement |
| Pin | Symbol pin | Pad |
| Rule check | ERC | DRC |

---

*This document is a general primer. Always follow the **MCU**, **regulator**, and **crystal** datasheets for your specific design.*
