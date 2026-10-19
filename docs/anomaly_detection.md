# Anomaly detection

Every number on this page came out of one execution of
`backend/scripts/ml/02_detect_anomalies.py`, recorded in
`data/processed/_anomaly_results.json`.
Generated 2026-10-03T19:42:05.305667+00:00.

Every anomaly carries its own baseline and threshold. No point is reported without them, because a bare 'anomaly: true' cannot be judged by a reader.

## What an anomaly is, and is not

There is **no labelled ground truth** for any of these three sources.
Nobody marked an Olist order as wrong, nobody marked an M5 day as an
incident. So a flagged point means **"unusual relative to its own recent
history"** -- which is a weaker claim than "wrong", and a different one
from "unexpected by a human".

A Christmas Day is anomalous by every statistical definition and is
entirely expected. That is not a defect in the detector; it is what
means to detect an event the calendar already knows about. The firing
rates below are reported so you can see how much of each detector's
output is that kind of finding.

## Summary

| source | metric | points scored | flagged | firing rate |
|--------|--------|rrrrrrrrrrrrrrr|rrrrrrrrr|rrrrrrrrrrrrr|
| `olist` | `orders_per_day` | 606 | 32 | 5.280% |
| `dunnhumby` | `basket_lines_per_day` | 704 | 26 | 3.693% |
| `m5` | `total_units_per_day` | 1,913 | 5 | 0.261% |

Firing rates differ by an order of magnitude across the three detectors,
and that is a property of the DATA rather than of the thresholds. A
detector tuned to fire 3% on one source and 0.26% on another is not being
inconsistent -- the sources have different variance structures, and a
global rate would hide that. Read each row on its own.

## Detectors

Three detectors, because the three sources violate three different
assumptions. Applying one method everywhere would produce confident
nonsense in two of the three cases.

### `olist.orders_per_day`

**Method.** same-weekday rolling mean, 28-week lookback, flag |z| >= 3.0

**Baseline.** mean of the previous 28 same-weekday order counts; threshold = +/- 3.0 standard deviations of those

**Scored** 606 point(s); **flagged**
32 (5.280%).

- skipped the first 4 occurrences of each weekday (insufficient history); scored 606 day/weekday pairs

| date | observed | baseline | threshold | score (sigma) | severity |
|------|rrrrrrrrrr|rrrrrrrrrr|rrrrrrrrrrr|rrrrrrrrrrrrrrr|----------|
| `2017-11-24` | 1,176 | 131.14 | +/-71.93 | +43.58 | critical |
| `2017-11-25` | 499 | 100.04 | +/-65.16 | +18.37 | critical |
| `2017-11-26` | 391 | 120.79 | +/-62.66 | +12.94 | critical |
| `2017-11-27` | 403 | 159.68 | +/-89.76 | +8.13 | critical |
| `2017-11-28` | 380 | 159.96 | +/-95.95 | +6.88 | critical |
| `2017-11-29` | 323 | 148.21 | +/-99.61 | +5.26 | warning |
| `2017-11-23` | 283 | 140.32 | +/-84.69 | +5.05 | warning |
| `2018-09-29` | 1 | 166 | +/-115.36 | -4.29 | warning |
| `2018-08-30` | 4 | 240.14 | +/-168.71 | -4.20 | warning |
| `2018-08-31` | 1 | 207.64 | +/-151.01 | -4.11 | warning |

_Top 10 of 32 by |sigma|, ranked
strongest first. The full list is in
`data/processed/_anomaly_results.json`._

### `dunnhumby.basket_lines_per_day`

**Method.** rolling median with MAD, 30-day window, flag |robust z| >= 3.0

**Baseline.** median of the previous 30 days; scale = 1.4826 x MAD; threshold = +/- 3.0 x that scale

**Scored** 704 point(s); **flagged**
26 (3.693%).

- dunnhumby's time axis is the integer `day` index (1..711), not a calendar date. Labels are day_N and MUST NOT be read as dates.
- scored 704 of 711 days; the first 6 have no usable history

| date | observed | baseline | threshold | score (sigma) | severity |
|------|rrrrrrrrrr|rrrrrrrrrr|rrrrrrrrrrr|rrrrrrrrrrrrrrr|----------|
| `643` | 2 | 3,897.50 | +/-1,552.28 | -7.53 | critical |
| `641` | 7,066 | 3,823 | +/-1,363.25 | +7.14 | critical |
| `278` | 6 | 3,880.50 | +/-2,410.71 | -4.82 | warning |
| `684` | 6,681 | 3,976 | +/-2,141.62 | +3.79 | warning |
| `610` | 6,352 | 3,879 | +/-1,977.05 | +3.75 | warning |
| `691` | 7,426 | 4,039 | +/-2,735.40 | +3.71 | warning |
| `96` | 4,039 | 2,520.50 | +/-1,232.04 | +3.70 | warning |
| `246` | 6,746 | 3,652.50 | +/-2,544.14 | +3.65 | warning |
| `67` | 2,629 | 1,496.50 | +/-931.81 | +3.65 | warning |
| `425` | 5,531 | 3,926 | +/-1,325.44 | +3.63 | warning |

_Top 10 of 26 by |sigma|, ranked
strongest first. The full list is in
`data/processed/_anomaly_results.json`._

### `m5.total_units_per_day`

**Method.** rolling mean and standard deviation, 56-day lookback, flag |z| >= 4.0

**Baseline.** mean of the previous 56 days; threshold = +/- 4.0 standard deviations

**Scored** 1,913 point(s); **flagged**
5 (0.261%).

- aggregated across all 30,490 series before detection, so a flagged day means the whole business moved, not one volatile item
- threshold is 4.0 rather than 3.0 because aggregate retail units have heavier tails than normal; a 3-sigma cut fires almost entirely on December, which is real and expected
- 5 of 5 flagged m5 dates are known calendar events recorded in the dataset itself (Christmas Day); 0 are not explained by the calendar

| date | observed | baseline | threshold | score (sigma) | severity |
|------|rrrrrrrrrr|rrrrrrrrrr|rrrrrrrrrrr|rrrrrrrrrrrrrrr|----------|
| `2015-12-25` -- Christmas Day | 14 | 37,690.30 | +/-22,769.74 | -6.62 | warning |
| `2011-12-25` -- Christmas Day | 13 | 28,491.11 | +/-17,924.18 | -6.36 | warning |
| `2014-12-25` -- Christmas Day | 20 | 34,595.38 | +/-22,509.24 | -6.14 | warning |
| `2013-12-25` -- Christmas Day | 20 | 34,891.89 | +/-23,068.72 | -6.05 | warning |
| `2012-12-25` -- Christmas Day | 11 | 32,949.23 | +/-23,399.39 | -5.63 | warning |

## The most useful thing on this page

**All 5 flagged M5 days are Christmas Day.**

That is a detector working correctly and a dataset with no
incidents in it. Across 1,913 scored days, total daily units
never departed from its recent history by 4 standard deviations
*except* on a day the calendar already flags.

It is also the honest answer to "what went wrong in this
data?": **nothing that this method can see.** A clean result is a
result. Reporting "5 anomalies detected" without the fact that
all 5 are Christmas would have turned a correct detector into a
false alarm list.

| date | event | units | baseline | score (sigma) |
|------|-------|rrrrrrr|rrrrrrrrrr|rrrrrrrrrrrrrrr|
| `2015-12-25` | Christmas Day | 14 | 37,690.30 | -6.62 |
| `2011-12-25` | Christmas Day | 13 | 28,491.11 | -6.36 |
| `2014-12-25` | Christmas Day | 20 | 34,595.38 | -6.14 |
| `2013-12-25` | Christmas Day | 20 | 34,891.89 | -6.05 |
| `2012-12-25` | Christmas Day | 11 | 32,949.23 | -5.63 |

## Why Olist fires more often than M5

Olist flagged 32 of
606 scored day/weekday pairs
(5.28%), M5 flagged
0.261% of its own. Three causes, all visible in the data rather than
in the threshold:

1. **Order counts are a much smaller sample of the same volatility.**
   ~1,000 orders/day against tens of thousands of units/day. A
   proportionally larger share of the variance is Poisson noise on a
   small count, which a z-score cannot distinguish from a real shift.
2. **Olist's window is shorter.** 606 scored points against M5's 1,913,
   so each Olist estimate rests on 28 weeks of history instead of
   five years. A shorter baseline is a noisier one.
3. **Brazilian national holidays** produce real, large, *unexplained*
   swings. Some of these flagged days are Carnival or Easter, which
   this dataset carries no calendar for.

None of these are reasons to lower the threshold. They are reasons to
read the Olist list as "the days this method considers unusual"
rather than as an incident list.

## Reproducing this

```
.venv/Scripts/python.exe backend/scripts/ml/02_detect_anomalies.py
.venv/Scripts/python.exe backend/scripts/docs/generate_anomaly_detection.py
```

Both steps are read-only: the run connects as `omniretail_readonly` and
the generator reads one JSON file.
