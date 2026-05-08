"""
End-to-end KiCad routing pipeline.

Preparation (default, so re-runs do not stack duplicate geometry):
  0a. (optional) --from-base: copy a clean / known-good .kicad_sch over the
      pipeline input (the current file is backed up first if it exists).
  0b. (default)  Strip all (wire …) and (junction …) from the input
      .kicad_sch (with backup) so CONVERT sees only symbol + label
      connectivity.  Use --no-clean-input to keep existing wires (not
      recommended if the sheet was already auto-routed once).

Main stages:
  1. CONVERT  .kicad_sch        -> schematic.json
  2. ROUTE    schematic.json    -> schematic.routes.json (headless A*)
  3. APPLY    schematic.routes  -> overwrite original .kicad_sch (with backup)

Each stage is invokable in isolation via --skip-* flags. By default the
pipeline runs prep + all three stages, overwrites the original .kicad_sch in
place, and writes timestamped backups under <project>-backups/.

Output paths default next to the input:
  <input>.schematic.json
  <input>.schematic.routes.json

Usage:
  python3 pipeline.py path/to/foo.kicad_sch
  python3 pipeline.py path/to/foo.kicad_sch --no-overwrite
  python3 pipeline.py path/to/foo.kicad_sch --from-base path/to/foo_clean.kicad_sch
  python3 pipeline.py path/to/foo.kicad_sch --no-clean-input   # keep wires before convert
  python3 pipeline.py path/to/foo.kicad_sch --skip-convert     # reuse existing JSON
  python3 pipeline.py path/to/foo.kicad_sch --skip-apply       # convert+route only
  python3 pipeline.py path/to/foo.kicad_sch --dry-run
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from typing import Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from apply_routes_to_kicad_sch import make_backup, strip_schematic_routing


# ── helpers ─────────────────────────────────────────────────────────────────


def _banner(label: str) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n  {label}\n{bar}")


def _run(cmd: Sequence[str], *, label: str, dry_run: bool = False) -> int:
    _banner(label)
    print("$ " + " ".join(cmd))
    if dry_run:
        return 0
    t0 = time.perf_counter()
    rc = subprocess.call(cmd)
    dt = time.perf_counter() - t0
    print(f"\n[exit={rc}, elapsed={dt:.2f}s]")
    return rc


def _require_file(path: str, what: str) -> None:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{what} not found: {path}")


# ── stage runners ───────────────────────────────────────────────────────────


def stage_convert(kicad_sch: str, schematic_json: str, *, dry_run: bool = False) -> int:
    return _run(
        [sys.executable,
         os.path.join(HERE, "kicad_sch_to_schematic_json.py"),
         kicad_sch, "-o", schematic_json],
        label="STAGE 1/3 — CONVERT  (.kicad_sch -> schematic.json)",
        dry_run=dry_run,
    )


def stage_route(schematic_json: str, *, dry_run: bool = False) -> int:
    # cad_viewer's --export-routes-only writes <input>.routes.json next to the
    # JSON. We don't pass the output path; we derive and verify it instead.
    return _run(
        [sys.executable,
         os.path.join(HERE, "cad_viewer.py"),
         schematic_json, "--export-routes-only"],
        label="STAGE 2/3 — ROUTE    (schematic.json -> schematic.routes.json)",
        dry_run=dry_run,
    )


def stage_apply(routes_json: str, kicad_sch: str, *,
                no_overwrite: bool = False, dry_run: bool = False) -> int:
    cmd = [sys.executable,
           os.path.join(HERE, "apply_routes_to_kicad_sch.py"),
           routes_json, "--schematic", kicad_sch]
    if no_overwrite:
        cmd.append("--no-overwrite")
    return _run(
        cmd,
        label="STAGE 3/3 — APPLY    (routes -> backup + write .kicad_sch)",
        dry_run=dry_run,
    )


def stage_copy_from_base(base_sch: str, target_sch: str, *, dry_run: bool = False) -> int:
    base_sch = os.path.abspath(base_sch)
    target_sch = os.path.abspath(target_sch)
    _require_file(base_sch, "Base .kicad_sch")
    _banner("STAGE 0a — FROM-BASE  (copy clean schematic → pipeline input)")
    print(f"  base:   {base_sch}")
    print(f"  target: {target_sch}")
    if dry_run:
        print("  [dry-run: no copy]")
        return 0
    if os.path.isfile(target_sch):
        bp = make_backup(target_sch)
        print(f"  backed up existing target: {bp}")
    shutil.copy2(base_sch, target_sch)
    print("  copy completed.")
    return 0


def stage_strip_routing(kicad_sch: str, *, dry_run: bool = False) -> int:
    _banner("STAGE 0b — STRIP    (remove wires + junctions from input .kicad_sch)")
    if dry_run:
        print(f"$ strip_schematic_routing({kicad_sch!r})")
        return 0
    strip_schematic_routing(kicad_sch, backup=True)
    return 0


# ── pipeline driver ─────────────────────────────────────────────────────────


def run_pipeline(
    kicad_sch: str,
    *,
    schematic_json: str | None = None,
    routes_json: str | None = None,
    from_base: str | None = None,
    clean_input: bool = True,
    skip_convert: bool = False,
    skip_route: bool = False,
    skip_apply: bool = False,
    no_overwrite: bool = False,
    dry_run: bool = False,
) -> int:
    kicad_sch = os.path.abspath(kicad_sch)

    if from_base:
        from_base = os.path.abspath(from_base)
        _require_file(from_base, "Base .kicad_sch")
    elif not os.path.isfile(kicad_sch):
        raise FileNotFoundError(f"Input .kicad_sch not found: {kicad_sch}")

    # Default sibling paths (matches what cad_viewer.py / apply_routes write).
    sch_base, _ext = os.path.splitext(kicad_sch)
    schematic_json = os.path.abspath(schematic_json or (sch_base + ".schematic.json"))
    routes_json = os.path.abspath(routes_json or (
        os.path.splitext(schematic_json)[0] + ".routes.json"
    ))

    print("Pipeline:")
    print(f"  input  .kicad_sch   = {kicad_sch}")
    if from_base:
        print(f"  --from-base         = {from_base}")
    print(f"  clean-input         = {clean_input}  (strip wires/junctions before convert)")
    print(f"  schematic.json      = {schematic_json}")
    print(f"  routes.json         = {routes_json}")
    print(f"  apply mode          = {'WRITE *.routed.kicad_sch' if no_overwrite else 'OVERWRITE original (with backup)'}")
    if dry_run:
        print("  DRY RUN: no commands will execute")

    # ── prep: optional base copy ──────────────────────────────────────────
    if from_base:
        rc = stage_copy_from_base(from_base, kicad_sch, dry_run=dry_run)
        if rc != 0:
            return rc

    if not dry_run:
        _require_file(kicad_sch, "Pipeline input .kicad_sch")

    # ── prep: strip existing routing before convert (fresh netlist from labels) ──
    if clean_input and not skip_convert:
        rc = stage_strip_routing(kicad_sch, dry_run=dry_run)
        if rc != 0:
            return rc
    elif clean_input and skip_convert:
        _banner("STAGE 0b — STRIP    [SKIPPED — --skip-convert]")
        print(
            "  Warning: existing wires remain on disk; schematic.json may be stale "
            "relative to .kicad_sch. Regenerate JSON or omit --skip-convert."
        )

    if not skip_convert:
        rc = stage_convert(kicad_sch, schematic_json, dry_run=dry_run)
        if rc != 0:
            return rc
        if not dry_run:
            _require_file(schematic_json, "Convert output")
    else:
        _banner("STAGE 1/3 — CONVERT  [SKIPPED]")
        if not skip_route or not skip_apply:
            _require_file(schematic_json, "Existing schematic.json")

    if not skip_route:
        rc = stage_route(schematic_json, dry_run=dry_run)
        if rc != 0:
            return rc
        if not dry_run:
            _require_file(routes_json, "Route output")
    else:
        _banner("STAGE 2/3 — ROUTE    [SKIPPED]")
        if not skip_apply:
            _require_file(routes_json, "Existing routes.json")

    if not skip_apply:
        rc = stage_apply(routes_json, kicad_sch,
                         no_overwrite=no_overwrite, dry_run=dry_run)
        if rc != 0:
            return rc
    else:
        _banner("STAGE 3/3 — APPLY    [SKIPPED]")

    _banner("PIPELINE COMPLETE")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("kicad_sch", help="Path to .kicad_sch input (pipeline target)")
    ap.add_argument("--from-base", metavar="BASE.kicad_sch",
                    help="Copy this clean schematic over kicad_sch first (existing target is backed up)")
    ap.add_argument("--no-clean-input", action="store_true",
                    help="Do not strip wires/junctions before convert (default is to strip)")
    ap.add_argument("--schematic-json",
                    help="Override schematic.json path (default: <input>.schematic.json)")
    ap.add_argument("--routes-json",
                    help="Override routes.json path (default: <schematic_json>.routes.json)")
    ap.add_argument("--skip-convert", action="store_true",
                    help="Reuse existing schematic.json")
    ap.add_argument("--skip-route", action="store_true",
                    help="Reuse existing routes.json")
    ap.add_argument("--skip-apply", action="store_true",
                    help="Stop after producing routes.json")
    ap.add_argument("--no-overwrite", action="store_true",
                    help="In APPLY, write to *.routed.kicad_sch instead of overwriting the original")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print stage commands without executing them")
    args = ap.parse_args()

    rc = run_pipeline(
        kicad_sch=args.kicad_sch,
        schematic_json=args.schematic_json,
        routes_json=args.routes_json,
        from_base=args.from_base,
        clean_input=not args.no_clean_input,
        skip_convert=args.skip_convert,
        skip_route=args.skip_route,
        skip_apply=args.skip_apply,
        no_overwrite=args.no_overwrite,
        dry_run=args.dry_run,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
