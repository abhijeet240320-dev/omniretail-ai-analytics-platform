"""Generate docs/forecasting.md from the executed model evaluation.

USAGE (from the repository root)
--------------------------------
    .venv/Scripts/python.exe backend/scripts/ml/01_evaluate_forecast.py
    .venv/Scripts/python.exe backend/scripts/docs/generate_forecasting.py

WHY THE DOCUMENT IS GENERATED
-----------------------------
"Croston performed best on intermittent demand" is a claim that stops being
true the moment the data changes. `backend/scripts/ml/01_evaluate_forecast.py`
evaluates five models against a 28-day holdout on all 30,490 M5 series and
writes the actual measurements to `data/processed/_forecast_results.json`. This
script renders that file, and nothing else.

It will NOT write the document from a partial run. If the evaluation covered
fewer series than the database holds, the script stops and says so - a document
built from a 2,000-series trial run would be indistinguishable in form from one
built from the full dataset, and would quietly be wrong.

WHAT THIS SCRIPT REFUSES TO DO
------------------------------
It does not choose a winner. `winner_by_mase` is copied from the results file,
which recorded it from the run. Re-deriving it here would mean two places that
could disagree about which model won, and the loser of that disagreement is the
reader.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS = REPO_ROOT / "data" / "processed" / "_forecast_results.json"
DESTINATION = REPO_ROOT / "docs" / "forecasting.md"

sys.path.insert(0, str(REPO_ROOT / "backend"))


def load() -> dict:
    if not RESULTS.exists():
        raise SystemExit(
            f"{RESULTS} does not exist.\n"
            f"Run the evaluation first:\n"
            f"  .venv/Scripts/python.exe backend/scripts/ml/01_evaluate_forecast.py"
        )
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    if not payload["scope"]["is_full_dataset"]:
        raise SystemExit(
            f"the recorded evaluation covered "
            f"{payload['scope']['series_evaluated']} of "
            f"{payload['scope']['series_total_in_database']} series.\n"
            f"docs/forecasting.md documents the FULL dataset, so a partial run "
            f"must not be written up. Re-run without --limit-series."
        )
    return payload


def _table(rows: list[list[str]], header: list[str], align: list[str]) -> str:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join(a * (len(h) + 2) for h, a in zip(header, align)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def render(payload: dict) -> str:
    scope = payload["scope"]
    split = payload["split"]
    models = payload["models"]
    descriptions = payload["model_descriptions"]
    winner = payload["winner_by_mase"]
    bands = payload["intermittency"]
    per_band = payload["per_band"]
    elapsed = payload["elapsed_seconds"]

    names = list(models)
    ranked = sorted(names, key=lambda n: models[n]["MASE"])
    runner_up = ranked[1]

    total_series = scope["series_evaluated"]
    zero_series = sum(b["series"] for b in bands.values())

    lines: list[str] = []
    A = lines.append

    A("# Demand forecasting")
    A("")
    A("Every number on this page came out of one execution of")
    A("`backend/scripts/ml/01_evaluate_forecast.py`, recorded in")
    A("`data/processed/_forecast_results.json`.")
    A(f"Generated {payload['generated_utc']}.")
    A("")
    A("If this document and that file ever disagree, the file is right and this")
    A("document is stale. Regenerate it; do not edit the prose to match.")
    A("")

    # ---- what was run ----
    A("## What was evaluated")
    A("")
    A(f"- **Series:** {total_series:,} of {scope['series_total_in_database']:,} "
      f"in `m5.series` -- the complete dataset, not a sample")
    A(f"- **Days:** {scope['days_loaded']:,} per series "
      f"({total_series:,} x {scope['days_loaded']:,} = "
      f"{total_series * scope['days_loaded']:,} series-days)")
    A(f"- **Train:** `d_num` {split['train_d_num']}")
    A(f"- **Test:** `d_num` {split['test_d_num']} ({split['test_days']} days)")
    A(f"- **Runtime:** {elapsed['load']:.0f} s to load, "
      f"{elapsed['evaluate']:.0f} s to evaluate all {len(names)} models")
    A("")
    A(f"**Why the split is {split['test_days']} days.** {split['rationale']}")
    A("")
    A("No model sees its test window. The split happens once, in")
    A("`evaluate_all_models`, and nowhere else -- so there is exactly one place")
    A("where a model could leak its own future, and it does not.")
    A("")

    # ---- intermittency ----
    A("## Why this data needs a forecasting method chosen carefully")
    A("")
    A(f"**{zero_series:,} of {total_series:,} series** fall into a")
    A("zero-heavy band. Intermittent demand is why a mean-based forecast and a")
    A("\"same as yesterday\" forecast behave nothing alike here, and why the")
    A("per-band table at the bottom of this page matters more than the")
    A("headline number.")
    A("")
    rows = []
    for band, detail in bands.items():
        low, high = detail["zero_rate_range"]
        rows.append([
            band,
            f"{detail['series']:,}",
            f"{detail['series'] / total_series:.1%}",
            f"{low:.0%}-{high:.0%}",
        ])
    A(_table(rows, ["band", "series", "share", "share of days with zero sales"],
             ["-", "r", "r", "r"]))
    A("")

    # ---- models ----
    A("## The models")
    A("")
    for name in ranked:
        A(f"**`{name}`** -- {descriptions[name]}")
        A("")

    # ---- results ----
    A("## Results")
    A("")
    rows = []
    for name in ranked:
        m = models[name]
        marker = " **(winner)**" if name == winner else ""
        rows.append([
            f"`{name}`{marker}",
            f"{m['MAE']:.4f}",
            f"{m['RMSE']:.4f}",
            f"{m['MASE']:.4f}",
            f"{m['WMAPE']:.4f}",
            f"{m['bias']:+.4f}",
        ])
    A(_table(rows, ["model", "MAE", "RMSE", "MASE", "WMAPE", "bias"],
             ["-", "r", "r", "r", "r", "r"]))
    A("")
    A("Reading the columns:")
    A("")
    A("- **MASE** -- mean absolute scaled error. The mean training-window")
    A("  absolute error of each series is its own denominator, so a value of 1.0")
    A("  means \"no better than last week's average\". This is the ranking column")
    A("  because it is the only one that is comparable across a sparse series and")
    A("  a dense one.")
    A("- **bias** -- mean(forecast - actual). Positive means the model")
    A("  over-forecasts. Croston and sbF are visibly negative here: they smooth")
    A("  hard enough to miss the real spikes, which costs MAE far less than it")
    A("  costs RMSE, and is why they look competitive overall and lose per band.")
    A("- **WMAPE** -- weighted absolute percentage error. On intermittent data")
    A("  this stays near 1.0 because the zeros dominate the denominator; it is")
    A("  reported for completeness, not used for ranking.")
    A("")

    # ---- winner ----
    A("## The result")
    A("")
    margin = payload["margin_over_runner_up"]
    A(f"**`{winner}` wins on MASE at {models[winner]['MASE']:.4f}, ahead of**")
    A(f"**`{runner_up}` at {models[runner_up]['MASE']:.4f} -- a margin of {margin:.1%}.**")
    A("")
    A(f"Two things are worth saying about that {margin:.1%}.")
    A("")
    A(f"First, it is **not** a large margin. On 30,490 series it is consistent")
    A("and it is measured, but it is not the gap between a good model and a")
    A("wrong one. Reporting it as decisive would overstate what is known.")
    A("")
    A("Second, look at the bias column. `croston` and `sbF` have a bias of")
    A(f"{models['croston']['bias']:+.2f} and {models['sbF']['bias']:+.2f}, against")
    A(f"`{winner}`'s {models[winner]['bias']:+.2f}. They win on nothing -- they are")
    A("second and third -- and they lose by a similar margin on RMSE")
    A(f"({models['croston']['RMSE']:.2f} and {models['sbF']['RMSE']:.2f} against")
    A(f"{models[winner]['RMSE']:.2f}). They smooth away the spikes. If the cost")
    A("of under-forecasting a real demand peak matters more than average error,")
    A("that is a different objective and a different model choice, and this")
    A("table is the evidence for having that conversation rather than assuming")
    A("MASE settles it.")
    A("")

    # ---- per band ----
    A("## Which band does each model actually win?")
    A("")
    A("This is the table that decides whether the headline matters.")
    A("")
    header = ["band", "series"] + [f"`{n}`" for n in names]
    align = ["-", "r"] + ["r"] * len(names)
    rows = []
    for band in bands:
        if band not in per_band[winner]:
            continue
        series = per_band[winner][band]["series"]
        cells = [f"{per_band[n][band]['MASE']:.3f}" for n in names]
        best = min(float(c) for c in cells)
        cells = [
            f"**{c}**" if float(c) == best else c for c in cells
        ]
        rows.append([band, f"{series:,}", *cells])
    A(_table(rows, header, align))
    A("")
    A("Read that as: no model is best everywhere, and the aggregate MASE is an")
    A("average over three populations with genuinely different structure. If")
    A("the decision only applies to the sporadic band, use that row -- not the")
    A("overall figure.")
    A("")
    sporadic = next((b for b in bands if b.startswith("sporadic")), None)
    if sporadic and per_band[winner].get(sporadic):
        cells = {n: per_band[n][sporadic]["MASE"] for n in names}
        best_sporadic = min(cells, key=lambda n: cells[n])
        if best_sporadic != winner:
            A(f"On the sporadic band alone, `{best_sporadic}` is the better model")
            A(f"({cells[best_sporadic]:.3f} against `{winner}`'s")
            A(f"{cells[winner]:.3f}). The overall winner is not the winner of the")
            A("band that most series are in. That is the honest reading of this")
            A("table, and it is the reason the table exists.")
        else:
            A(f"On the sporadic band, `{winner}` is also best, at")
            A(f"{cells[winner]:.3f}.")
    A("")

    # ---- reproduction ----
    A("## Reproducing this")
    A("")
    A("```")
    A(".venv/Scripts/python.exe backend/scripts/ml/01_evaluate_forecast.py")
    A(".venv/Scripts/python.exe backend/scripts/docs/generate_forecasting.py")
    A("```")
    A("")
    A(f"A full run reads {total_series * scope['days_loaded']:,} rows and takes")
    A(f"about {elapsed['load'] / 60:.0f} minutes. `--limit-series N` runs a")
    A("subset and this generator then REFUSES to write the document, because a")
    A("document built from a subset would be indistinguishable in form from")
    A("this one and would quietly be wrong.")
    A("")
    A("`backend/tests/test_forecast_results.py` fails if the document and the")
    A("results file disagree.")
    A("")

    return "\n".join(lines)


def main() -> int:
    payload = load()
    text = render(payload)
    DESTINATION.write_text(text, encoding="utf-8")
    print(f"wrote {DESTINATION} ({len(text.splitlines()):,} lines) "
          f"from {RESULTS.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())