# Demand forecasting

Every number on this page came out of one execution of
`backend/scripts/ml/01_evaluate_forecast.py`, recorded in
`data/processed/_forecast_results.json`.
Generated 2026-10-03T19:34:14.936840+00:00.

If this document and that file ever disagree, the file is right and this
document is stale. Regenerate it; do not edit the prose to match.

## What was evaluated

- **Series:** 30,490 of 30,490 in `m5.series` -- the complete dataset, not a sample
- **Days:** 1,941 per series (30,490 x 1,941 = 59,181,090 series-days)
- **Train:** `d_num` 1..1913
- **Test:** `d_num` 1914..1941 (28 days)
- **Runtime:** 283 s to load, 9 s to evaluate all 5 models

**Why the split is 28 days.** The 28-day test window matches the 28 forecast-horizon days in m5.calendar, so the model is scored on the length of job it would actually do.

No model sees its test window. The split happens once, in
`evaluate_all_models`, and nowhere else -- so there is exactly one place
where a model could leak its own future, and it does not.

## Why this data needs a forecasting method chosen carefully

**30,490 of 30,490 series** fall into a
zero-heavy band. Intermittent demand is why a mean-based forecast and a
"same as yesterday" forecast behave nothing alike here, and why the
per-band table at the bottom of this page matters more than the
headline number.

| band | series | share | share of days with zero sales |
|------|rrrrrrrr|rrrrrrr|rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr|
| sporadic (>=80% zeros) | 11,783 | 38.6% | 80%-100% |
| intermittent (40-80%) | 14,582 | 47.8% | 40%-80% |
| frequent (<40%) | 4,125 | 13.5% | 0%-40% |

## The models

**`sma_7`** -- Mean of the last 7 observed days, held flat.

**`sbF`** -- Syntetos-Boylan bias-corrected Croston: Croston's rate multiplied by (1 - alpha/2) to offset the optimism from ignoring in-interval zeros.

**`croston`** -- Croston: separate running estimates of demand size and inter-demand interval, updated only on non-zero days.

**`seasonal_naive_7`** -- Repeat the value from one week earlier (weekly cycle).

**`naive`** -- Repeat the last observed value for every horizon day.

## Results

| model | MAE | RMSE | MASE | WMAPE | bias |
|-------|rrrrr|rrrrrr|rrrrrr|rrrrrrr|rrrrrr|
| `sma_7` **(winner)** | 1.0683 | 2.2487 | 1.6717 | 0.7404 | -0.1062 |
| `sbF` | 1.2692 | 3.2465 | 1.6966 | 0.8797 | -0.8880 |
| `croston` | 1.2628 | 3.2197 | 1.7012 | 0.8752 | -0.8587 |
| `seasonal_naive_7` | 1.2440 | 2.6769 | 1.8695 | 0.8622 | -0.1062 |
| `naive` | 1.3730 | 2.8936 | 2.0521 | 0.9516 | +0.1903 |

Reading the columns:

- **MASE** -- mean absolute scaled error. The mean training-window
  absolute error of each series is its own denominator, so a value of 1.0
  means "no better than last week's average". This is the ranking column
  because it is the only one that is comparable across a sparse series and
  a dense one.
- **bias** -- mean(forecast - actual). Positive means the model
  over-forecasts. Croston and sbF are visibly negative here: they smooth
  hard enough to miss the real spikes, which costs MAE far less than it
  costs RMSE, and is why they look competitive overall and lose per band.
- **WMAPE** -- weighted absolute percentage error. On intermittent data
  this stays near 1.0 because the zeros dominate the denominator; it is
  reported for completeness, not used for ranking.

## The result

**`sma_7` wins on MASE at 1.6717, ahead of**
**`sbF` at 1.6966 -- a margin of 1.5%.**

Two things are worth saying about that 1.5%.

First, it is **not** a large margin. On 30,490 series it is consistent
and it is measured, but it is not the gap between a good model and a
wrong one. Reporting it as decisive would overstate what is known.

Second, look at the bias column. `croston` and `sbF` have a bias of
-0.86 and -0.89, against
`sma_7`'s -0.11. They win on nothing -- they are
second and third -- and they lose by a similar margin on RMSE
(3.22 and 3.25 against
2.25). They smooth away the spikes. If the cost
of under-forecasting a real demand peak matters more than average error,
that is a different objective and a different model choice, and this
table is the evidence for having that conversation rather than assuming
MASE settles it.

## Which band does each model actually win?

This is the table that decides whether the headline matters.

| band | series | `naive` | `sma_7` | `seasonal_naive_7` | `croston` | `sbF` |
|------|rrrrrrrr|rrrrrrrrr|rrrrrrrrr|rrrrrrrrrrrrrrrrrrrr|rrrrrrrrrrr|rrrrrrr|
| sporadic (>=80% zeros) | 11,783 | 2.915 | 2.477 | 2.678 | 2.384 | **2.365** |
| intermittent (40-80%) | 14,582 | 1.608 | **1.247** | 1.449 | 1.321 | 1.323 |
| frequent (<40%) | 4,125 | 1.159 | **0.870** | 1.049 | 1.096 | 1.108 |

Read that as: no model is best everywhere, and the aggregate MASE is an
average over three populations with genuinely different structure. If
the decision only applies to the sporadic band, use that row -- not the
overall figure.

On the sporadic band alone, `sbF` is the better model
(2.365 against `sma_7`'s
2.477). The overall winner is not the winner of the
band that most series are in. That is the honest reading of this
table, and it is the reason the table exists.

## Reproducing this

```
.venv/Scripts/python.exe backend/scripts/ml/01_evaluate_forecast.py
.venv/Scripts/python.exe backend/scripts/docs/generate_forecasting.py
```

A full run reads 59,181,090 rows and takes
about 5 minutes. `--limit-series N` runs a
subset and this generator then REFUSES to write the document, because a
document built from a subset would be indistinguishable in form from
this one and would quietly be wrong.

`backend/tests/test_forecast_results.py` fails if the document and the
results file disagree.
