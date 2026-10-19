"""Evaluate demand-forecasting models on M5 and record what actually happened.

USAGE
-----
    .venv/Scripts/python.exe backend/scripts/ml/01_evaluate_forecast.py
    .venv/Scripts/python.exe backend/scripts/ml/01_evaluate_forecast.py --limit-series 2000

WHY THIS WRITES A JSON FILE
---------------------------
Because "Croston performed best on intermittent demand" is a claim that stops
being true the moment the data changes. `_forecast_results.json` is what
`docs/forecasting.md` is generated from, and `test_forecast_results_are_current`
fails when the document and the measurement disagree.

THE SPLIT IS FIXED AND EXPLAINED
--------------------------------
Train on d_num 1..1913, test on 1914..1941. The 28-day test window matches the
28 horizon days that `m5.calendar` carries past the last observation, so the
model is scored on the length of job it would actually do.

No model sees its test window. The split happens once, in
`evaluate_all_models`, and nowhere else.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import numpy as np  # noqa: E402

from app.services.forecasting import (  # noqa: E402
    HORIZON,
    INTERMITTENCY_BANDS,
    TRAIN_DAYS,
    evaluate_all_models,
    load_matrix,
)

OUTPUT = REPO_ROOT / "data" / "processed" / "_forecast_results.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit-series", type=int, default=None,
                        help="evaluate only the first N series (testing only; the "
                             "documented numbers come from a full run)")
    args = parser.parse_args()

    print("OmniRetail forecast evaluation")
    print(f"  train : d_num 1..{TRAIN_DAYS}")
    print(f"  test  : d_num {TRAIN_DAYS + 1}..{TRAIN_DAYS + HORIZON} ({HORIZON} days)")
    print(f"  scope : {'first ' + str(args.limit_series) + ' series' if args.limit_series else 'all series'}")
    print("=" * 78)

    started = time.monotonic()
    matrix, ids = load_matrix(limit_series=args.limit_series)
    load_seconds = time.monotonic() - started
    print(f"  loaded {matrix.shape[0]:,} series x {matrix.shape[1]:,} days "
          f"in {load_seconds:.1f}s")

    started = time.monotonic()
    results = evaluate_all_models(matrix)
    eval_seconds = time.monotonic() - started
    print(f"  evaluated {len(results)} models in {eval_seconds:.1f}s\n")

    zero_rate = (matrix[:, :TRAIN_DAYS] == 0).mean(axis=1)
    print("  intermittency profile of the training data:")
    for band, (low, high) in INTERMITTENCY_BANDS.items():
        mask = (zero_rate >= low) & (zero_rate < high)
        if np.any(mask):
            print(f"    {band:<26} {int(mask.sum()):>6,} series")
    print()

    header = f"  {'model':<20} {'MAE':>9} {'RMSE':>10} {'MASE':>8} {'WMAPE':>8} {'bias':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, result in results.items():
        print(f"  {name:<20} {result.mae:9.4f} {result.rmse:10.4f} {result.mase:8.4f} "
              f"{result.wmape:8.4f} {result.bias:10.4f}")

    ranked = sorted(results.values(), key=lambda r: r.mase)
    winner = ranked[0]
    runner_up = ranked[1]
    print()
    print(f"  WINNER by MASE: {winner.name} ({winner.mase:.4f})")
    print(f"  runner-up    : {runner_up.name} ({runner_up.mase:.4f})")
    print(f"  margin       : {(runner_up.mase - winner.mase) / runner_up.mase:.1%} better")

    print()
    print("  per-band MASE - the number that decides whether any of this is worth doing:")
    print(f"  {'band':<26} {'series':>7}  " + "  ".join(f"{n[:12]:>12}" for n in results))
    for band in INTERMITTENCY_BANDS:
        if band not in winner.per_band:
            continue
        series_count = winner.per_band[band]["series"]
        cells = "  ".join(
            f"{results[n].per_band[band]['MASE']:12.3f}" for n in results
        )
        print(f"  {band:<26} {series_count:>7,}  {cells}")

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": "m5.sales_long",
        "scope": {
            "series_evaluated": int(matrix.shape[0]),
            "series_total_in_database": 30490,
            "days_loaded": int(matrix.shape[1]),
            "is_full_dataset": args.limit_series is None,
        },
        "split": {
            "train_d_num": f"1..{TRAIN_DAYS}",
            "test_d_num": f"{TRAIN_DAYS + 1}..{TRAIN_DAYS + HORIZON}",
            "test_days": HORIZON,
            "rationale": (
                "The 28-day test window matches the 28 forecast-horizon days in "
                "m5.calendar, so the model is scored on the length of job it "
                "would actually do."
            ),
        },
        "intermittency": {
            band: {
                "series": int(((zero_rate >= low) & (zero_rate < high)).sum()),
                "zero_rate_range": [low, min(high, 1.0)],
            }
            for band, (low, high) in INTERMITTENCY_BANDS.items()
        },
        "models": {name: result.as_row() for name, result in results.items()},
        "model_descriptions": {
            name: result.description for name, result in results.items()
        },
        "per_band": {
            name: result.per_band for name, result in results.items()
        },
        "winner_by_mase": winner.name,
        "margin_over_runner_up": round(
            (runner_up.mase - winner.mase) / runner_up.mase, 4
        ),
        "elapsed_seconds": {
            "load": round(load_seconds, 2),
            "evaluate": round(eval_seconds, 2),
        },
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print()
    print(f"  report -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())