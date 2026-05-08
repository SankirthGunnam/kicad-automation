"""
Pure-Python data model for the lightweight schematic.json format produced by
kicad_sch_to_schematic_json.py.

No Qt or GUI dependencies. Used by cad_viewer.py to populate the scene and
by other tools (pipeline orchestrator, headless validators, route applier).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Component:
    id: int
    label: str                              # reference designator (U1, R1, etc.)
    type: str                               # 'rf_component', 'rfic', 'antenna', ...
    color: tuple[int, int, int]
    x: float
    y: float
    w: float
    h: float
    pins: list[tuple[str, str]]             # [(side, pin_number), ...]
    pin_xy: list[tuple[float, float]] | None = None  # optional [(x,y), ...] per pin, component-local mm
    lib_id: str = ""                        # original KiCad lib_id (from "comment")
    raw: dict[str, Any] = field(default_factory=dict)

    def has_position(self) -> bool:
        # x/y == 0,0 is a legitimate location, but in practice we always emit
        # explicit numbers from the converter. We treat presence of the
        # original key in `raw` as the source of truth.
        return "x" in self.raw and "y" in self.raw


@dataclass
class Connection:
    from_id: int
    from_pin: str
    to_id: int
    to_pin: str
    label: str = ""                         # net name (e.g. "+5V", "GND", "RST")
    layout: bool = True                     # if False, skip in column-rank layout
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Schematic:
    scene_rect: tuple[float, float, float, float]
    components: list[Component] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    units: str = "mm"
    source_path: str = ""                   # absolute path of the JSON it was loaded from

    def by_id(self) -> dict[int, Component]:
        return {c.id: c for c in self.components}


# ── I/O ─────────────────────────────────────────────────────────────────────


def load(path: str) -> Schematic:
    """Read a schematic.json file into a Schematic dataclass."""
    abs_path = os.path.abspath(path)
    with open(abs_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rect = data.get("scene", {}).get("rect", [0, 0, 0, 0])
    sch = Schematic(scene_rect=tuple(rect), source_path=abs_path)

    for c in data.get("components", []):
        xy_raw = c.get("pin_xy")
        pin_xy = None
        if xy_raw:
            pin_xy = [tuple(float(t) for t in pair) for pair in xy_raw]
        sch.components.append(Component(
            id=c["id"],
            label=c.get("label", ""),
            type=c.get("type", "rf_component"),
            color=tuple(c.get("color", [128, 128, 128])),
            x=c.get("x", 0),
            y=c.get("y", 0),
            w=c.get("w", 0),
            h=c.get("h", 0),
            pins=[tuple(p) for p in c.get("pins", [])],
            pin_xy=pin_xy,
            lib_id=c.get("comment", ""),
            raw=c,
        ))

    for cn in data.get("connections", []):
        f, t = cn["from"], cn["to"]
        sch.connections.append(Connection(
            from_id=f[0], from_pin=str(f[1]),
            to_id=t[0],   to_pin=str(t[1]),
            label=cn.get("label", ""),
            layout=cn.get("layout", True),
            raw=cn,
        ))

    return sch


def dump(sch: Schematic, path: str) -> None:
    """Write a Schematic back to disk in the same JSON shape."""
    out: dict[str, Any] = {
        "scene": {"rect": list(sch.scene_rect)},
        "components": [],
        "connections": [],
    }
    for c in sch.components:
        row: dict[str, Any] = {
            "id": c.id,
            "type": c.type,
            "label": c.label,
            "color": list(c.color),
            "w": c.w,
            "h": c.h,
            "x": c.x,
            "y": c.y,
            "pins": [list(p) for p in c.pins],
            "comment": c.lib_id,
        }
        if c.pin_xy is not None:
            row["pin_xy"] = [list(p) for p in c.pin_xy]
        out["components"].append(row)
    for cn in sch.connections:
        d: dict[str, Any] = {
            "from": [cn.from_id, cn.from_pin],
            "to":   [cn.to_id,   cn.to_pin],
        }
        if cn.label:
            d["label"] = cn.label
        if cn.layout is False:
            d["layout"] = False
        out["connections"].append(d)
    out_dir = os.path.dirname(os.path.abspath(path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")


def main() -> None:
    """CLI: dump a one-line summary of a schematic.json."""
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("path", help="schematic.json file to inspect")
    args = ap.parse_args()

    sch = load(args.path)
    print(f"file:        {sch.source_path}")
    print(f"units:       {sch.units}")
    print(f"scene rect:  {sch.scene_rect}")
    print(f"components:  {len(sch.components)}")
    print(f"connections: {len(sch.connections)}")
    nets = sorted({cn.label for cn in sch.connections if cn.label})
    if nets:
        print(f"nets ({len(nets)}): {', '.join(nets)}")


if __name__ == "__main__":
    main()
