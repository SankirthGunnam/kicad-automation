#!/usr/bin/env python3
"""
Convert a KiCad .kicad_sch schematic into the lightweight schematic.json format
used by cad_viewer.py in this repo.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from typing import Any, Iterable

import sexpdata

# Viewer coordinates are in millimetres (1:1 with KiCad). No scaling, no padding.
DEFAULT_SCALE = 1.0


def _parse_sexpr(text: str) -> Any:
    # sexpdata returns nested Python lists of:
    # - sexpdata.Symbol for symbols/keywords
    # - str for quoted strings
    # - int/float for numeric atoms
    return sexpdata.loads(text)


def _is_list(x: Any) -> bool:
    return isinstance(x, list)


def _atom_str(a: Any) -> str:
    if isinstance(a, sexpdata.Symbol):
        return a.value()
    if isinstance(a, str):
        return a
    if isinstance(a, (int, float)):
        # used rarely (e.g. if someone put numbers where we expected a string)
        return str(a)
    raise TypeError(f"Expected atom (symbol/string/number), got {type(a)}")


def _find_children(node: Any, head: str) -> list[list[Any]]:
    if not _is_list(node):
        return []
    out: list[list[Any]] = []
    for ch in node:
        if _is_list(ch) and ch and _atom_str(ch[0]) == head:
            out.append(ch)
    return out


def _first_child(node: Any, head: str) -> list[Any] | None:
    xs = _find_children(node, head)
    return xs[0] if xs else None


def _child_atom(node: Any, head: str, default: str | None = None) -> str | None:
    ch = _first_child(node, head)
    if not ch or len(ch) < 2:
        return default
    return _atom_str(ch[1])


def _child_floats(node: Any, head: str) -> tuple[float, ...] | None:
    ch = _first_child(node, head)
    if not ch or len(ch) < 2:
        return None
    vals: list[float] = []
    for a in ch[1:]:
        try:
            if isinstance(a, (int, float)):
                vals.append(float(a))
            else:
                vals.append(float(_atom_str(a)))
        except Exception:
            break
    return tuple(vals)


@dataclass(frozen=True)
class LibPin:
    number: str
    name: str
    ox: float
    oy: float
    angle: float = 0.0  # pin orientation in degrees (0/90/180/270)

    @property
    def side(self) -> str:
        # KiCad pin angle convention (the direction the pin extends INTO the body):
        #   0   → pin's `at` is on the LEFT edge   of the body
        #   90  → pin's `at` is on the BOTTOM edge of the body (lib Y-up = screen "bottom")
        #   180 → pin's `at` is on the RIGHT edge  of the body
        #   270 → pin's `at` is on the TOP edge    of the body (lib Y-up = screen "top")
        a = int(round(self.angle)) % 360
        return {0: "left", 90: "bottom", 180: "right", 270: "top"}.get(a, "right")


def _collect_lib_pins(root: Any) -> dict[str, list[LibPin]]:
    """
    Returns {lib_id: [LibPin...]} extracted from (lib_symbols (symbol "<lib_id>" ...)).
    """
    out: dict[str, list[LibPin]] = {}
    lib_symbols = _first_child(root, "lib_symbols")
    if not lib_symbols:
        return out

    for sym in _find_children(lib_symbols, "symbol"):
        if len(sym) < 2:
            continue
        lib_id = _atom_str(sym[1])
        pins: list[LibPin] = []

        # Pin definitions are nested under sub-symbols (units) like (symbol "R_1_1" (pin ...)).
        for unit_sym in _find_children(sym, "symbol"):
            for pin in _find_children(unit_sym, "pin"):
                at = _child_floats(pin, "at") or (0.0, 0.0, 0.0)
                num = _child_atom(pin, "number") or ""
                name = _child_atom(pin, "name") or ""
                if not num:
                    continue
                ang = float(at[2]) if len(at) >= 3 else 0.0
                pins.append(LibPin(number=num, name=name,
                                   ox=float(at[0]), oy=float(at[1]), angle=ang))

        # De-dupe by pin number (multiple unit symbols often repeat)
        by_num: dict[str, LibPin] = {}
        for p in pins:
            if p.number not in by_num:
                by_num[p.number] = p

        out[lib_id] = list(by_num.values())
    return out


def _collect_lib_body_bboxes(root: Any) -> dict[str, tuple[float, float, float, float]]:
    """
    Returns {lib_id: (xmin, ymin, xmax, ymax)} of the visible body in the lib's
    Y-up frame, computed from rectangles, polylines, circles and arcs.
    Pins are not included — we want the *body* extent, not the pin tips.
    """
    out: dict[str, tuple[float, float, float, float]] = {}
    lib_symbols = _first_child(root, "lib_symbols")
    if not lib_symbols:
        return out

    def update(box: list[float], x: float, y: float) -> None:
        box[0] = x if box[0] is None else min(box[0], x)
        box[1] = y if box[1] is None else min(box[1], y)
        box[2] = x if box[2] is None else max(box[2], x)
        box[3] = y if box[3] is None else max(box[3], y)

    for sym in _find_children(lib_symbols, "symbol"):
        if len(sym) < 2:
            continue
        lib_id = _atom_str(sym[1])
        box: list[float | None] = [None, None, None, None]

        def walk(node: Any) -> None:
            if not _is_list(node) or not node:
                return
            h = _atom_str(node[0]) if (node and not _is_list(node[0])) else ""
            if h == "rectangle":
                s = _child_floats(node, "start")
                e = _child_floats(node, "end")
                if s and e:
                    update(box, s[0], s[1])
                    update(box, e[0], e[1])
            elif h == "polyline":
                pts = _first_child(node, "pts")
                if pts:
                    for xy in _find_children(pts, "xy"):
                        if len(xy) >= 3:
                            update(box, float(_atom_str(xy[1])), float(_atom_str(xy[2])))
            elif h == "circle":
                ctr = _child_floats(node, "center")
                rad = _child_floats(node, "radius")
                if ctr and rad:
                    cx, cy, r = ctr[0], ctr[1], rad[0]
                    update(box, cx - r, cy - r)
                    update(box, cx + r, cy + r)
            elif h == "arc":
                for key in ("start", "mid", "end"):
                    p = _child_floats(node, key)
                    if p:
                        update(box, p[0], p[1])
            for ch in node:
                walk(ch)

        walk(sym)
        if all(v is not None for v in box):
            out[lib_id] = (box[0], box[1], box[2], box[3])
    return out


@dataclass
class SymbolInst:
    sid: int
    lib_id: str
    ref: str
    value: str
    x: float
    y: float
    rot_deg: float


def _collect_symbol_instances(root: Any) -> list[SymbolInst]:
    insts: list[SymbolInst] = []
    sid = 1
    for node in _find_children(root, "symbol"):
        lib_id = _child_atom(node, "lib_id")
        at = _child_floats(node, "at")
        if not lib_id or not at or len(at) < 2:
            continue
        ref = ""
        value = ""
        for prop in _find_children(node, "property"):
            if len(prop) >= 3:
                key = _atom_str(prop[1])
                val = _atom_str(prop[2])
                if key == "Reference":
                    ref = val
                elif key == "Value":
                    value = val
        rot = float(at[2]) if len(at) >= 3 else 0.0
        insts.append(SymbolInst(sid=sid, lib_id=lib_id, ref=ref, value=value, x=float(at[0]), y=float(at[1]), rot_deg=rot))
        sid += 1
    return insts


@dataclass(frozen=True)
class WireSeg:
    x1: float
    y1: float
    x2: float
    y2: float


def _collect_wires(root: Any) -> list[WireSeg]:
    segs: list[WireSeg] = []
    for w in _find_children(root, "wire"):
        pts = _first_child(w, "pts")
        if not pts:
            continue
        xys: list[tuple[float, float]] = []
        for xy in _find_children(pts, "xy"):
            if len(xy) >= 3:
                xys.append((float(_atom_str(xy[1])), float(_atom_str(xy[2]))))
        if len(xys) == 2:
            (x1, y1), (x2, y2) = xys
            segs.append(WireSeg(x1, y1, x2, y2))
    return segs


@dataclass(frozen=True)
class LabelAt:
    text: str
    x: float
    y: float


def _collect_labels(root: Any) -> list[LabelAt]:
    labels: list[LabelAt] = []
    for head in ("label", "global_label", "hierarchical_label"):
        for node in _find_children(root, head):
            at = _child_floats(node, "at")
            # KiCad stores the label text as the positional arg right after the head:
            #   (label "NETNAME" (at x y r) ...)
            #   (global_label "NETNAME" (at x y r) (shape ...) ...)
            txt: str | None = None
            if len(node) >= 2 and not _is_list(node[1]):
                try:
                    txt = _atom_str(node[1])
                except Exception:
                    txt = None
            if not txt:
                txt = _child_atom(node, "text") or _child_atom(node, "value")
            if at and txt:
                labels.append(LabelAt(text=txt, x=float(at[0]), y=float(at[1])))
    return labels


def _rot(x: float, y: float, deg: float) -> tuple[float, float]:
    r = math.radians(deg)
    cs = math.cos(r)
    sn = math.sin(r)
    return (x * cs - y * sn, x * sn + y * cs)


def _round_pt(x: float, y: float, tol: float = 1e-6) -> tuple[float, float]:
    # Normalize floating noise; KiCad coords are usually exact decimals.
    return (round(x / tol) * tol, round(y / tol) * tol)


def _build_connectivity(
    symbols: list[SymbolInst],
    lib_pins: dict[str, list[LibPin]],
    wires: list[WireSeg],
    labels: list[LabelAt],
) -> tuple[dict[int, list[tuple[str, str, float, float]]], dict[str, list[tuple[int, str]]]]:
    """
    Returns:
      - per_symbol_pins[sid] = [(pin_id, display_name, abs_x, abs_y), ...]
      - net_to_pins[net_name] = [(sid, pin_id), ...]
    """

    # Build graph over wire endpoints
    adj: dict[tuple[float, float], set[tuple[float, float]]] = {}

    def add_edge(a: tuple[float, float], b: tuple[float, float]) -> None:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    for s in wires:
        a = _round_pt(s.x1, s.y1)
        b = _round_pt(s.x2, s.y2)
        add_edge(a, b)

    # Attach labels as their own nodes (connected to existing node if exact match)
    label_at: dict[tuple[float, float], str] = {}
    for lb in labels:
        p = _round_pt(lb.x, lb.y)
        label_at[p] = lb.text
        adj.setdefault(p, set())

    # Compute pin absolute positions and attach them to the nearest existing node if exact match.
    per_symbol_pins: dict[int, list[tuple[str, str, float, float]]] = {}
    pin_at: dict[tuple[float, float], tuple[int, str]] = {}

    for sym in symbols:
        pins = lib_pins.get(sym.lib_id, [])
        plist: list[tuple[str, str, float, float]] = []
        for p in pins:
            rx, ry = _rot(p.ox, p.oy, sym.rot_deg)
            # KiCad lib symbols use Y-up; schematic uses Y-down.
            # Flip Y when mapping lib coords to schematic coords.
            ax, ay = sym.x + rx, sym.y - ry
            pin_id = p.number
            display = p.name if p.name else p.number
            plist.append((pin_id, display, ax, ay))
            pin_at[_round_pt(ax, ay)] = (sym.sid, pin_id)
            adj.setdefault(_round_pt(ax, ay), set())
        per_symbol_pins[sym.sid] = plist

    # Connected components of the wire graph
    seen: set[tuple[float, float]] = set()
    comps: list[set[tuple[float, float]]] = []
    for n in adj.keys():
        if n in seen:
            continue
        stack = [n]
        cc: set[tuple[float, float]] = set()
        seen.add(n)
        while stack:
            cur = stack.pop()
            cc.add(cur)
            for nb in adj.get(cur, ()):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        comps.append(cc)

    # Per-CC pin and label sets
    cc_pins: list[list[tuple[int, str]]] = []
    cc_labels: list[set[str]] = []
    for cc in comps:
        pins_on = [pin_at[pt] for pt in cc if pt in pin_at]
        labels_in = {label_at[pt] for pt in cc if pt in label_at}
        cc_pins.append(pins_on)
        cc_labels.append(labels_in)

    # Merge CCs that share a label name (same net name = same logical net,
    # regardless of how many wire islands the schematic has).
    parent = list(range(len(comps)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    label_to_first_cc: dict[str, int] = {}
    for i, lbls in enumerate(cc_labels):
        for lb in lbls:
            if lb in label_to_first_cc:
                union(i, label_to_first_cc[lb])
            else:
                label_to_first_cc[lb] = i

    # Aggregate by merged group
    group_pins: dict[int, list[tuple[int, str]]] = {}
    group_labels: dict[int, set[str]] = {}
    for i in range(len(comps)):
        r = find(i)
        group_pins.setdefault(r, []).extend(cc_pins[i])
        group_labels.setdefault(r, set()).update(cc_labels[i])

    net_to_pins: dict[str, list[tuple[int, str]]] = {}
    net_idx = 1
    for r, pins_on in group_pins.items():
        if len(pins_on) < 2:
            continue
        lbls = group_labels.get(r, set())
        if lbls:
            net = sorted(lbls)[0]
        else:
            net = f"N$${net_idx}"
            net_idx += 1
        net_to_pins.setdefault(net, []).extend(pins_on)

    return per_symbol_pins, net_to_pins


def _color_for(lib_id: str) -> list[int]:
    # simple stable palette
    if lib_id.startswith("power:"):
        return [120, 120, 120]
    if "Connector" in lib_id:
        return [60, 140, 200]
    if "MCU" in lib_id or "Interface_" in lib_id:
        return [70, 160, 120]
    return [160, 100, 200]


_MIN_DIM_MM = 0.5  # absolute floor so a degenerate symbol still renders as a dot


def _body_dims_and_offset(
    lib_id: str,
    pins: list[LibPin],
    body_bboxes: dict[str, tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    """
    Returns (w_mm, h_mm, dx, dy) where (dx, dy) is the offset (in schematic Y-down
    mm) from the symbol anchor to the top-left of the body. No padding, no scaling.

    Priority:
      1. Body graphic bbox (rectangle/polyline/circle/arc).
      2. Pin offsets bbox.
      3. _MIN_DIM_MM fallback.
    """
    bbox = body_bboxes.get(lib_id)
    if bbox is None and pins:
        xs = [p.ox for p in pins]
        ys = [p.oy for p in pins]
        bbox = (min(xs), min(ys), max(xs), max(ys))

    if bbox is None:
        return (_MIN_DIM_MM, _MIN_DIM_MM, -_MIN_DIM_MM / 2.0, -_MIN_DIM_MM / 2.0)

    xmin, ymin, xmax, ymax = bbox
    w_mm = max(xmax - xmin, _MIN_DIM_MM)
    h_mm = max(ymax - ymin, _MIN_DIM_MM)
    # Lib frame is Y-up; schematic is Y-down. Top-left of body in schematic frame:
    #   dx = xmin (left edge in lib X, unchanged)
    #   dy = -ymax (top in screen = max Y in lib, with sign flipped)
    return (w_mm, h_mm, xmin, -ymax)


def kicad_to_schematic_json(in_path: str, out_path: str, scale: float) -> dict[str, Any]:
    with open(in_path, "r", encoding="utf-8") as f:
        txt = f.read()
    root = _parse_sexpr(txt)

    lib_pins = _collect_lib_pins(root)
    body_bboxes = _collect_lib_body_bboxes(root)
    insts = _collect_symbol_instances(root)
    wires = _collect_wires(root)
    labels = _collect_labels(root)

    per_symbol_pins, net_to_pins = _build_connectivity(insts, lib_pins, wires, labels)

    # Components
    components = []
    for sym in insts:
        pins = lib_pins.get(sym.lib_id, [])
        w_mm, h_mm, dx, dy = _body_dims_and_offset(sym.lib_id, pins, body_bboxes)

        # JSON x/y is the top-left of the body in the schematic Y-down frame, so
        # the viewer's setPos(x,y) (which uses top-left) places the part exactly
        # where KiCad shows it.
        x = (sym.x + dx) * scale
        y = (sym.y + dy) * scale
        w = w_mm * scale
        h = h_mm * scale

        pin_spec = [[lp.side, lp.number] for lp in pins]

        label = sym.ref or sym.value or sym.lib_id
        comp = {
            "id": sym.sid,
            "type": "rf_component",
            "label": label,
            "color": _color_for(sym.lib_id),
            "w": w,
            "h": h,
            "x": x,
            "y": y,
            "pins": pin_spec or [["left", "1"], ["right", "2"]],
            "comment": sym.lib_id,
        }
        components.append(comp)

    # Connections (net fanout -> pairwise chain)
    connections = []
    for net, pins in net_to_pins.items():
        uniq: list[tuple[int, str]] = []
        seen = set()
        for p in pins:
            if p not in seen:
                uniq.append(p)
                seen.add(p)
        if len(uniq) < 2:
            continue
        for a, b in zip(uniq, uniq[1:]):
            (sid1, pin1), (sid2, pin2) = a, b
            connections.append({"from": [sid1, str(pin1)], "to": [sid2, str(pin2)], "label": net})

    # Scene rect from component bbox (in mm). Pad ~30 mm around the design.
    if components:
        xs0 = [c["x"] for c in components]
        ys0 = [c["y"] for c in components]
        xs1 = [c["x"] + c["w"] for c in components]
        ys1 = [c["y"] + c["h"] for c in components]
        minx, maxx = min(xs0), max(xs1)
        miny, maxy = min(ys0), max(ys1)
    else:
        minx = miny = -30.0
        maxx = maxy = 30.0
    pad = 30.0 * scale
    scene = {"rect": [minx - pad, miny - pad, (maxx - minx) + 2 * pad, (maxy - miny) + 2 * pad]}

    out = {"scene": scene, "components": components, "connections": connections}

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    return {
        "components": len(components),
        "connections": len(connections),
        "labels": len(labels),
        "wires": len(wires),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kicad_sch", help="Input .kicad_sch file")
    ap.add_argument(
        "-o",
        "--out",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "schematic.json"),
        help="Output schematic.json path (default: PathfindingAlgo/schematic.json)",
    )
    ap.add_argument("--scale", type=float, default=DEFAULT_SCALE, help="Scale factor from KiCad mm to viewer units")
    args = ap.parse_args()

    stats = kicad_to_schematic_json(args.kicad_sch, args.out, args.scale)
    print(f"Wrote {args.out}")
    print(f"Components: {stats['components']}  Connections: {stats['connections']}  Wires: {stats['wires']}  Labels: {stats['labels']}")


if __name__ == "__main__":
    main()

