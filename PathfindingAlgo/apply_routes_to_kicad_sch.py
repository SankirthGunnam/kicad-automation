#!/usr/bin/env python3
"""
Apply A*-routed paths (from cad_viewer.py's routes JSON) back into the
original .kicad_sch file.

Also exposes strip_schematic_routing(): remove every top-level (wire …) and
(junction …) so the sheet can be re-converted from label connectivity only.
The pipeline calls this before CONVERT by default.

Workflow:
  1. Parse the routes JSON.
  2. Locate the .kicad_sch (from --schematic, or `schematic_path` field
     in the routes JSON, or alongside it).
  3. Back up the original schematic to a sibling folder
     `<project>-backups/<schematic>.YYYYMMDD-HHMMSS.bak`.
  4. Compute the *actual* KiCad pin coordinates for every (component_ref,
     pin_number) referenced by the routes (the routes JSON's start/end
     are viewer pin positions, not KiCad's, so we re-anchor to real pins).
  5. Replace every (wire …) and (junction …) entry in the .kicad_sch with
     fresh wires built from the routed polylines (real-pin start/end +
     polyline interior). Junctions are added at any point shared by 3+
     wire endpoints. All other top-level forms (symbols, labels,
     no_connect, lib_symbols, sheet_instances, …) are preserved verbatim.
  6. Overwrite the original (or write to *.routed.kicad_sch with
     --no-overwrite).

CLI shortcuts:
  python apply_routes_to_kicad_sch.py routes.json [--schematic PATH]
  python apply_routes_to_kicad_sch.py --strip-only sheet.kicad_sch

Only wires/junctions are changed by apply_routes; strip removes them only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import uuid as uuid_mod
from datetime import datetime
from typing import Any

import sexpdata

Sym = sexpdata.Symbol


# ── tiny s-expression helpers ────────────────────────────────────────────────


def _head(n: Any) -> str | None:
    if isinstance(n, list) and n and hasattr(n[0], "value"):
        return n[0].value()
    return None


def _atom(a: Any) -> str:
    if hasattr(a, "value"):
        return a.value()
    return str(a)


def _child(node: Any, name: str) -> list[Any] | None:
    if not isinstance(node, list):
        return None
    for ch in node:
        if isinstance(ch, list) and _head(ch) == name:
            return ch
    return None


def _children(node: Any, name: str) -> list[list[Any]]:
    if not isinstance(node, list):
        return []
    return [ch for ch in node if isinstance(ch, list) and _head(ch) == name]


def _atomf(a: Any) -> float:
    return float(_atom(a))


# ── extract symbol library + placed instances ────────────────────────────────


def collect_pin_offsets(root: Any) -> dict[str, dict[str, tuple[float, float]]]:
    """Returns {lib_id: {pin_number: (ox, oy)}} from lib_symbols."""
    out: dict[str, dict[str, tuple[float, float]]] = {}
    ls = _child(root, "lib_symbols")
    if not ls:
        return out

    for sym in _children(ls, "symbol"):
        if len(sym) < 2:
            continue
        lib_id = _atom(sym[1])
        offsets: dict[str, tuple[float, float]] = {}
        # Pin defs live one level deeper inside (symbol "Foo_1_1" (pin …))
        for unit in _children(sym, "symbol"):
            for pin in _children(unit, "pin"):
                num = _child(pin, "number")
                at = _child(pin, "at")
                if num and len(num) >= 2 and at and len(at) >= 3:
                    offsets[_atom(num[1])] = (_atomf(at[1]), _atomf(at[2]))
        if offsets:
            # Don't clobber a previously-seen lib_id with an empty dict
            out.setdefault(lib_id, offsets)
    return out


def collect_placed_symbols(root: Any) -> dict[str, tuple[str, float, float, float]]:
    """{ Reference → (lib_id, x, y, rot_deg) } for every placed (symbol …)."""
    out: dict[str, tuple[str, float, float, float]] = {}
    if not isinstance(root, list):
        return out
    for s in root:
        if not (isinstance(s, list) and _head(s) == "symbol"):
            continue
        lib_id = None
        for ch in s:
            if isinstance(ch, list) and _head(ch) == "lib_id" and len(ch) >= 2:
                lib_id = _atom(ch[1])
                break
        ref = ""
        for ch in s:
            if (
                isinstance(ch, list)
                and _head(ch) == "property"
                and len(ch) >= 3
                and _atom(ch[1]) == "Reference"
            ):
                ref = _atom(ch[2])
                break
        at = _child(s, "at")
        if not lib_id or not ref or not at or len(at) < 3:
            continue
        sx = _atomf(at[1])
        sy = _atomf(at[2])
        rot = _atomf(at[3]) if len(at) >= 4 else 0.0
        out[ref] = (lib_id, sx, sy, rot)
    return out


def _rotate(ox: float, oy: float, deg: float) -> tuple[float, float]:
    r = math.radians(deg)
    cs, sn = math.cos(r), math.sin(r)
    return (ox * cs - oy * sn, ox * sn + oy * cs)


def kicad_pin_position(
    ref: str,
    pin_num: str,
    placed: dict[str, tuple[str, float, float, float]],
    lib_pins: dict[str, dict[str, tuple[float, float]]],
) -> tuple[float, float] | None:
    info = placed.get(ref)
    if not info:
        return None
    lib_id, sx, sy, rot = info
    offsets = lib_pins.get(lib_id) or {}
    op = offsets.get(str(pin_num))
    if op is None:
        return None
    rx, ry = _rotate(op[0], op[1], rot)
    # Lib symbols are Y-up; schematic is Y-down → flip Y
    return (round(sx + rx, 6), round(sy - ry, 6))


# ── wire / junction builders ─────────────────────────────────────────────────


def _new_uuid() -> str:
    return str(uuid_mod.uuid4())


def make_wire(x1: float, y1: float, x2: float, y2: float) -> list[Any]:
    return [
        Sym("wire"),
        [Sym("pts"),
         [Sym("xy"), x1, y1],
         [Sym("xy"), x2, y2]],
        [Sym("stroke"),
         [Sym("width"), 0],
         [Sym("type"), Sym("default")]],
        [Sym("uuid"), _new_uuid()],
    ]


def make_junction(x: float, y: float) -> list[Any]:
    return [
        Sym("junction"),
        [Sym("at"), x, y],
        [Sym("diameter"), 0],
        [Sym("color"), 0, 0, 0, 0],
        [Sym("uuid"), _new_uuid()],
    ]


# ── path simplification ──────────────────────────────────────────────────────


def _collinear(a: tuple[float, float],
               b: tuple[float, float],
               c: tuple[float, float],
               eps: float = 1e-6) -> bool:
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return abs(cross) < eps


def cleanup_polyline(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop duplicate points and collapse 3 consecutive collinear points."""
    if not pts:
        return pts
    cleaned: list[tuple[float, float]] = [pts[0]]
    for p in pts[1:]:
        if cleaned[-1] != p:
            cleaned.append(p)
    # Collapse straight-line runs
    out: list[tuple[float, float]] = []
    for p in cleaned:
        while len(out) >= 2 and _collinear(out[-2], out[-1], p):
            out.pop()
        out.append(p)
    return out


def orthogonalize_polyline(
    pts: list[tuple[float, float]],
    tol: float = 1e-5,
) -> list[tuple[float, float]]:
    """
    Replace diagonal segments with an H+V elbow so KiCad wires stay Manhattan.
    Older routes.json may omit per-pin waypoint snapping from export.
    """
    if len(pts) < 2:
        return pts

    def aligned(a: tuple[float, float], b: tuple[float, float]) -> bool:
        return abs(a[0] - b[0]) <= tol or abs(a[1] - b[1]) <= tol

    out: list[tuple[float, float]] = [pts[0]]
    for b in pts[1:]:
        a = out[-1]
        if aligned(a, b):
            out.append(b)
            continue
        mid = (b[0], a[1])
        if aligned(a, mid) and aligned(mid, b):
            out.append(mid)
            out.append(b)
            continue
        mid2 = (a[0], b[1])
        if aligned(a, mid2) and aligned(mid2, b):
            out.append(mid2)
            out.append(b)
            continue
        out.append(b)

    deduped: list[tuple[float, float]] = [out[0]]
    for p in out[1:]:
        if abs(p[0] - deduped[-1][0]) > tol or abs(p[1] - deduped[-1][1]) > tol:
            deduped.append(p)
    return deduped


# ── backup ───────────────────────────────────────────────────────────────────


def _resolve_kicad_sch(candidate: str, routes_json: str) -> str | None:
    """
    The viewer's schema_file is the .schematic.json — so routes.json's
    `schematic_path` typically points at the JSON. Strip *.json /
    *.schematic.json suffixes and look for a .kicad_sch alongside.
    """
    candidate = os.path.abspath(candidate) if candidate else ""
    tries: list[str] = []
    if candidate:
        tries.append(candidate)
        # If the candidate is a JSON, derive the kicad_sch sibling.
        if candidate.lower().endswith(".kicad_sch"):
            pass
        else:
            base = candidate
            for suffix in (".routes.json", ".schematic.json", ".json"):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break
            tries.append(base + ".kicad_sch")

    # Final fallback: same dir as routes_json, any single .kicad_sch.
    routes_dir = os.path.dirname(os.path.abspath(routes_json))
    if os.path.isdir(routes_dir):
        for entry in sorted(os.listdir(routes_dir)):
            if entry.lower().endswith(".kicad_sch"):
                tries.append(os.path.join(routes_dir, entry))

    for p in tries:
        if p and os.path.isfile(p) and p.lower().endswith(".kicad_sch"):
            return p
    return None


def make_backup(sch_path: str) -> str:
    sch_path = os.path.abspath(sch_path)
    proj_dir = os.path.dirname(sch_path)
    proj_name = os.path.basename(proj_dir) or "schematic"
    backup_dir = os.path.join(
        os.path.dirname(proj_dir) or proj_dir, f"{proj_name}-backups"
    )
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    sch_base = os.path.basename(sch_path)
    backup_path = os.path.join(backup_dir, f"{sch_base}.{ts}.bak")
    shutil.copy2(sch_path, backup_path)
    return backup_path


def strip_schematic_routing(
    sch_path: str,
    *,
    backup: bool = True,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Remove every top-level (wire …) and (junction …) from a .kicad_sch file.
    Symbols, labels, global_labels, no_connect, lib_symbols, etc. are kept.

    Use this before re-running convert/route so connectivity comes only from
    labels (and any wires you want must be re-added by apply_routes).

    Returns counts: wires_removed, junctions_removed.
    """
    sch_path = os.path.abspath(sch_path)
    if not os.path.isfile(sch_path):
        raise FileNotFoundError(sch_path)

    with open(sch_path, "r", encoding="utf-8") as f:
        txt = f.read()
    root = sexpdata.loads(txt)

    if not isinstance(root, list) or _head(root) != "kicad_sch":
        raise ValueError(f"Not a kicad_sch root: {sch_path}")

    n_wire = n_junc = 0
    for tok in root[1:]:
        if isinstance(tok, list) and _head(tok) == "wire":
            n_wire += 1
        elif isinstance(tok, list) and _head(tok) == "junction":
            n_junc += 1

    if n_wire == 0 and n_junc == 0:
        return {"wires_removed": 0, "junctions_removed": 0}

    if dry_run:
        return {"wires_removed": n_wire, "junctions_removed": n_junc}

    if backup:
        print(f"Backup:  {make_backup(sch_path)}")

    new_root = [
        tok for tok in root
        if not (
            isinstance(tok, list)
            and _head(tok) in ("wire", "junction")
        )
    ]

    with open(sch_path, "w", encoding="utf-8") as f:
        f.write(sexpdata.dumps(new_root))
        f.write("\n")

    print(f"Stripped:  {sch_path}")
    print(f"  wires removed:     {n_wire}")
    print(f"  junctions removed: {n_junc}")
    return {"wires_removed": n_wire, "junctions_removed": n_junc}


# ── main ─────────────────────────────────────────────────────────────────────


def apply_routes(routes_json: str,
                 schematic_path: str | None = None,
                 overwrite: bool = True) -> dict[str, int]:
    with open(routes_json, "r", encoding="utf-8") as f:
        routes = json.load(f)

    candidate = (
        schematic_path
        or routes.get("schematic_path")
        or os.path.splitext(routes_json)[0] + ".kicad_sch"
    )
    sch_path = _resolve_kicad_sch(candidate, routes_json)
    if not sch_path or not os.path.isfile(sch_path):
        raise FileNotFoundError(
            f"Could not locate .kicad_sch from candidate: {candidate}"
        )

    with open(sch_path, "r", encoding="utf-8") as f:
        root = sexpdata.loads(f.read())

    placed = collect_placed_symbols(root)
    lib_pins = collect_pin_offsets(root)

    backup_path = make_backup(sch_path)
    print(f"Backup:  {backup_path}")

    # Build wire / junction lists
    new_wires: list[list[Any]] = []
    endpoint_count: dict[tuple[float, float], int] = {}
    skipped: list[str] = []

    def reg(p: tuple[float, float]) -> None:
        endpoint_count[p] = endpoint_count.get(p, 0) + 1

    for conn in routes.get("connections", []):
        f_ref = conn["from"]["component"]
        f_pin = conn["from"]["pin"]
        t_ref = conn["to"]["component"]
        t_pin = conn["to"]["pin"]
        path = [tuple(p) for p in (conn.get("path") or [])]
        if len(path) < 2:
            skipped.append(f"{f_ref}/{f_pin} → {t_ref}/{t_pin}: empty path")
            continue

        actual_start = kicad_pin_position(f_ref, f_pin, placed, lib_pins)
        actual_end = kicad_pin_position(t_ref, t_pin, placed, lib_pins)
        if actual_start is None or actual_end is None:
            skipped.append(
                f"{f_ref}/{f_pin} → {t_ref}/{t_pin}: pin not found in .kicad_sch"
            )
            continue

        # Anchor first / last to real KiCad pin coordinates.
        pts = [actual_start] + path[1:-1] + [actual_end]
        pts = orthogonalize_polyline(pts)
        pts = cleanup_polyline(pts)
        if len(pts) < 2:
            continue

        for a, b in zip(pts, pts[1:]):
            new_wires.append(make_wire(a[0], a[1], b[0], b[1]))
            reg(a)
            reg(b)

    new_junctions = [make_junction(p[0], p[1])
                     for p, n in endpoint_count.items() if n >= 3]

    # Strip old wires + junctions, keep everything else.
    new_root = [
        tok for tok in root
        if not (isinstance(tok, list) and _head(tok) in ("wire", "junction"))
    ]

    # Insert new wires + junctions just before sheet_instances (or at end).
    insert_at = len(new_root)
    for i, tok in enumerate(new_root):
        if isinstance(tok, list) and _head(tok) == "sheet_instances":
            insert_at = i
            break
    new_root[insert_at:insert_at] = new_junctions + new_wires

    out_path = sch_path if overwrite else (
        os.path.splitext(sch_path)[0] + ".routed.kicad_sch"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(sexpdata.dumps(new_root))
        f.write("\n")

    print(f"Wrote:   {out_path}")
    print(f"  wires:     {len(new_wires)}")
    print(f"  junctions: {len(new_junctions)}")
    if skipped:
        print(f"  skipped:   {len(skipped)}")
        for s in skipped:
            print(f"    - {s}")

    return {
        "wires": len(new_wires),
        "junctions": len(new_junctions),
        "skipped": len(skipped),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("routes_json", nargs="?",
                    help="Path to *.routes.json from cad_viewer.py (not used with --strip-only)")
    ap.add_argument("--schematic", help="Override .kicad_sch path "
                                          "(default: schematic_path field in routes.json)")
    ap.add_argument("--no-overwrite", action="store_true",
                    help="Write to <name>.routed.kicad_sch instead of overwriting the original")
    ap.add_argument("--strip-only", metavar="KICAD_SCH",
                    help="Only remove all wires and junctions from this .kicad_sch (with backup); "
                         "do not apply routes")
    ap.add_argument("--no-backup", action="store_true",
                    help="With --strip-only: do not create a timestamped backup before stripping")
    args = ap.parse_args()

    if args.strip_only:
        strip_schematic_routing(args.strip_only, backup=not args.no_backup)
        return

    if not args.routes_json:
        ap.error("routes_json is required unless --strip-only is used")

    apply_routes(args.routes_json,
                 schematic_path=args.schematic,
                 overwrite=not args.no_overwrite)


if __name__ == "__main__":
    main()
