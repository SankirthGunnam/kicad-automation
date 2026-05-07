# Wire Routing Logic — How It Works

> Applies to both `cad_viewer.py` (S23 RF Frontend) and `cad_viewer copy.py` (random-component version).  
> The routing engine is identical in both files.

---

## Big Picture

Every wire between two pins is routed in three stages:

```
Pin position  →  Exit point  →  A* path on grid  →  Simplify  →  Draw
```

The key idea: the canvas is overlaid with a fine **grid of cells** (8×8 px each). The router treats each cell as either *free*, *blocked* (inside a component), or *congested* (already used by another wire). A* finds the cheapest orthogonal path through free/cheap cells.

---

## Stage 1 — Build the RouterGrid

**File:** `cad_viewer.py:31` — `class RouterGrid`

```
Scene pixels  ──divide by CELL_SIZE=8──►  (row, col) integer grid
```

### What it does

1. Computes a bounding box covering all components plus margins.
2. For every component, marks a rectangle of cells as **blocked** — inflated by `COMP_PAD_CELLS = 1` cell (8 px) on each side so wires don't hug component edges.
3. Keeps a separate `_congestion` dict: `(row, col) → wire_count`.

### Key constants

| Constant | Value | Meaning |
|---|---|---|
| `CELL_SIZE` | 8 px | Size of one routing grid cell |
| `COMP_PAD_CELLS` | 1 cell | Extra blocked margin around each component |
| `PIN_EXIT_CELLS` | 3 cells | How far a wire must travel straight out from a pin before it can turn |
| `CONGESTION_PENALTY` | 30 | Extra cost per wire already in a cell |
| `TURN_PENALTY` | 4 | Extra cost every time the wire changes direction |

### Coordinate conversion

```python
# World pixel → grid cell
row = int((y - origin_y) / CELL_SIZE)
col = int((x - origin_x) / CELL_SIZE)

# Grid cell → world pixel (center of cell)
x = col * CELL_SIZE + origin_x + CELL_SIZE/2
y = row * CELL_SIZE + origin_y + CELL_SIZE/2
```

---

## Stage 2 — Pin Exit Points

**File:** `cad_viewer.py:146` — `pin_exit_point()`

A pin sits on the **boundary** of a component — which is inside the blocked padding zone. If A* started from the pin itself it would immediately be stuck.

The fix: offset the start/end point `PIN_EXIT_CELLS × CELL_SIZE = 24 px` straight out in the pin's direction, placing it clearly outside the blocked area.

```
Component body
┌──────────────┐
│              │──● pin (blocked zone)
│              │    ──────► exit point (24px out, free)
└──────────────┘
```

```python
off = 3 * 8  # = 24 px
'right'  →  QPointF(pin.x + 24,  pin.y     )
'left'   →  QPointF(pin.x - 24,  pin.y     )
'bottom' →  QPointF(pin.x,       pin.y + 24)
'top'    →  QPointF(pin.x,       pin.y - 24)
```

The start and end cells are added to an `exclude` set so A* is allowed to pass through them even though they might fall inside a padded region.

---

## Stage 3 — A* Search

**File:** `cad_viewer.py:91` — `astar_route()`

Standard A* on the grid with two extra costs baked into the edge weight:

### State

Each node in the search is `(row, col, last_direction)`.  
The direction is remembered so the router can penalise turns.

### Cost function

```
total_cost(cell) = 1                         # base step cost
                 + TURN_PENALTY  (if turning) # = 4, discourages zigzags
                 + congestion_cost(cell)       # = wire_count × 30
```

### Heuristic

```python
h(r, c) = |r - goal_r| + |c - goal_c|   # Manhattan distance (admissible)
```

Because the heuristic never overestimates, A* is guaranteed to find the **optimal** path (lowest total cost).

### Allowed moves

Only 4 directions — **no diagonals**. This produces the clean orthogonal (horizontal/vertical only) wires seen in real schematics.

```
        N(-1, 0)
W(0,-1) ── cell ── E(0,+1)
        S(+1, 0)
```

### Path reconstruction

When the goal cell is reached, the algorithm walks `came_from` backwards to reconstruct the cell sequence, then converts each `(row, col)` back to world-space `QPointF` coordinates.

---

## Stage 4 — Path Simplification

**File:** `cad_viewer.py:132` — `simplify_path()`

The raw A* path has one point per cell — hundreds of points for a long wire. Simplification removes collinear intermediate points, keeping only **corner** points.

```
Before:  ●──●──●──●──●
                     │
                     ●──●──●

After:   ●───────────●
                     │
                     ●──────●
```

A point is kept only if it is a corner — i.e., the direction changes between `prev→curr` and `curr→next`.

---

## Stage 5 — Congestion Marking

**File:** `cad_viewer.py:157` — `_mark_path()`

After each wire is routed, every grid cell it passes through is marked in `RouterGrid._congestion`. The next wire to route will pay `count × 30` extra to use those cells, pushing it to find a different path.

This is a **soft penalty** (not a hard block), so A* can still use a congested cell as a last resort — it just tries to avoid it.

```python
grid.mark_cell(r, c)
# _congestion[(r,c)] += 1
# Next wire pays +30 to use this cell
```

---

## Stage 6 — Routing Order

**File:** `cad_viewer.py:688` — `route_all()`

Wires are sorted by **Manhattan distance** (shortest first) before routing. Short wires tend to have clean, direct paths; routing them first lets them claim the best routes before longer wires fill the grid.

```python
sorted_conns = sorted(self.connections, key=net_len)
```

---

## Stage 7 — Drawing the Wire

**File:** `cad_viewer.py:491` — `ConnectionItem._draw_routed()`

The simplified waypoints are the A* path (grid-snapped points). Two extra fixes are applied before drawing:

### Pin-stub alignment fix

Grid quantization means the first waypoint might be a few pixels off-axis from the pin. This caused a tiny diagonal stub at the pin end. The fix snaps the first/last waypoints to align exactly with the pin:

```python
# For a left/right pin, the wire must leave horizontally
if pin.side in ('left', 'right'):
    waypoints[0] = QPointF(waypoints[0].x(), pin.y)   # force same Y
else:  # top/bottom pin
    waypoints[0] = QPointF(pin.x, waypoints[0].y)     # force same X
```

### Final path

```
pin1 → waypoint[0] → ... → waypoint[n] → pin2
       (grid-snapped,       (grid-snapped,
        axis-aligned)        axis-aligned)
```

---

## What Changes on Drag

When a component is moved:

1. **During drag** (`ItemPositionHasChanged`): each connected wire calls `update_path()` which redraws using the existing (now stale) waypoints — the wire **stretches** visually.
2. **On mouse release**: `CADView.mouseReleaseEvent` detects `_needs_reroute = True` and calls `route_all()`, which rebuilds the `RouterGrid` from scratch and re-routes every wire cleanly.

```
Drag ──► stretch (fast, no A*)
Release ──► route_all() (full A* reroute)
```

---

## What's Different Between the Two Files

| | `cad_viewer copy.py` | `cad_viewer.py` |
|---|---|---|
| Scene | 20 random components + 1 RFIC | S23 RF Frontend (LNA, PAM, LPAMID, FEM, Coupler, Switch, Antenna) |
| Components | Random types/colors | Named RF blocks with signal-named pins |
| Connections | Random pin pairs | Hardcoded realistic RF signal paths |
| Wire selection | Not implemented | Click wire → red dot-dashed highlight |
| Net labels | None | Signal name shown at wire midpoint |
| Pin bend fix | Not applied | First/last waypoint snapped to pin axis |
| Antenna | RFIC only | Cone-shaped `AntennaItem` |

**The routing engine (`RouterGrid`, `astar_route`, `simplify_path`, `pin_exit_point`, `_mark_path`) is byte-for-byte identical in both files.**
