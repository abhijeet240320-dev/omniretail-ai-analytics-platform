"""Run the cleaning/transformation stage for one or more sources.

USAGE (from the repository root)
--------------------------------
    .venv/Scripts/python.exe backend/etl/run.py                # all sources
    .venv/Scripts/python.exe backend/etl/run.py --source olist
    .venv/Scripts/python.exe backend/etl/run.py --source olist --source dataco

Reads  : data/raw/<source>/**          (never modified)
Writes : data/processed/<source>/*.csv  (rebuilt every run)
         data/processed/<source>/_report.json

Each source module is imported lazily and in isolation. One dataset failing to
clean therefore does not stop the others, and the runner reports exactly which
sources succeeded - which matters because these are four independent pipelines
that only happen to share a command.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from pathlib import Path

# The etl package lives under <root>/backend, so add that to the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etl.common import write_report  # noqa: E402
from etl.registry import load_manifest  # noqa: E402

# Source id -> the module that knows how to clean it.
MODULES = {
    "dunnhumby": "etl.dunnhumby",
    "dataco": "etl.dataco",
    "olist": "etl.olist",
    "m5": "etl.m5",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the OmniRetail ETL.")
    parser.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="only this source id (repeatable). Default: all selected sources.",
    )
    args = parser.parse_args()

    manifest = load_manifest()
    if args.sources:
        wanted = [manifest.by_id(source_id) for source_id in args.sources]
    else:
        wanted = [s for s in manifest.selected() if s.files]

    print("OmniRetail ETL - clean and transform")
    print(f"  raw       : {manifest.raw_root}")
    print(f"  processed : {manifest.processed_root}")
    print(f"  sources   : {', '.join(s.id for s in wanted)}")

    results: dict[str, str] = {}
    for source in wanted:
        module_name = MODULES.get(source.id)
        if module_name is None:
            print(f"\n=== {source.id}: no cleaning module registered, skipping")
            results[source.id] = "skipped"
            continue

        try:
            module = importlib.import_module(module_name)
            report = module.run(manifest, source)
        except Exception:  # noqa: BLE001 - we want the traceback, then continue
            print(f"\n!!! {source.id} FAILED")
            traceback.print_exc()
            results[source.id] = "FAILED"
            continue

        report.summarise(source.id)
        write_report(manifest, report)
        results[source.id] = "OK"

    print("\n--- summary ---")
    for source_id, status in results.items():
        print(f"  {status:<8} {source_id}")

    return 0 if all(status in {"OK", "skipped"} for status in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
