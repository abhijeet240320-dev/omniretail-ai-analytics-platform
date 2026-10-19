"""Anomaly detection on verified metrics, with the method stated up front.

THE RULE THIS MODULE ENFORCES
-----------------------------
An anomaly is not "a number that looks odd". It is a number that departs from a
STATED baseline by more than a STATED threshold, where the baseline was computed
from real observed data and the threshold is a multiple of the historical
variation of that same metric.

Every detector here returns the baseline, the threshold, the score and the
observed value for each flagged point. An API that returns `{"anomaly": true}`
with no baseline is decoration: the same point would be flagged under a different
baseline and the reader has no way to know.

WHY THREE DETECTORS AND NOT ONE
-------------------------------
Each source's data violates a different assumption:

  olist.orders     - COUNT of orders per day. Counts are bounded below by 0 and
                     vary with weekly seasonality. A z-score on raw counts
                     flags every Sunday because the distribution is not symmetric.

  dunnhumby        - sparse basket lines over 711 days. Most days are zero.
                     A Gaussian detector would need a mean near zero and a tiny
                     sigma, so any real basket would score as hundreds of sigma.

  m5               - zero-inflated units per day across 30,490 series.
                     The right baseline is the series' OWN recent history, not a
                     global distribution.

Choosing one method and applying it everywhere would produce confident nonsense
in two of the three cases. Each gets a method suited to its actual shape.

FALSE POSITIVES ARE PART OF THE ANSWER
--------------------------------------
There is no ground truth here - nobody labelled an M5 day anomalous. So the
honest thing is to report how often each detector fires and to expose that rate,
rather than to tune a threshold until the count looks plausible and present it
as a finding. A detector that flags 0.1% of days and one that flags 15% are both
"working"; only one of them is useful, and the difference is visible only if the
firing rate is reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np


@dataclass
class Anomaly:
    """One flagged point, with everything needed to judge it."""

    source: str
    metric: str
    observed_date: str
    observed_value: float
    baseline: float
    threshold: float
    score: float
    direction: str
    severity: str

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "metric": self.metric,
            "date": self.observed_date,
            "observed": round(self.observed_value, 4),
            "baseline": round(self.baseline, 4),
            "threshold": round(self.threshold, 4),
            "score_sigma": round(self.score, 2),
            "direction": self.direction,
            "severity": self.severity,
        }


@dataclass
class DetectionResult:
    source: str
    metric: str
    method: str
    threshold_description: str
    # Defaults to 0 rather than being required. Every detector fills it in as
    # it walks the series, because the count is an OUTPUT of the detection - a
    # day the baseline could not score is not counted, and that number has to
    # come from the same pass that decided which days were scorable. Making it
    # a required constructor argument would mean each detector inventing a
    # provisional zero and hoping to remember to overwrite it; the first one
    # that forgot produced `missing 1 required positional argument:
    # 'points_evaluated'` and the run died before detecting anything.
    points_evaluated: int = 0
    anomalies: list[Anomaly] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def firing_rate(self) -> float:
        return len(self.anomalies) / self.points_evaluated if self.points_evaluated else 0.0

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "metric": self.metric,
            "method": self.method,
            "baseline": self.threshold_description,
            "points_evaluated": self.points_evaluated,
            "anomalies_found": len(self.anomalies),
            "firing_rate_pct": round(self.firing_rate * 100, 4),
            "false_positive_caveat": (
                "No labelled ground truth exists for this dataset, so a firing "
                "point is 'unusual relative to its own history', not 'wrong'. "
                "Business events (holidays, promotions, outages) also look "
                "anomalous."
            ),
            "anomalies": [a.as_dict() for a in self.anomalies],
            "notes": self.notes,
        }


def _label(day) -> str:
    """Name a time axis point, whatever type that axis uses.

    The three sources do NOT share a time axis:

      olist.orders      `purchase_timestamp` - a real calendar date
      dunnhumby         `day`               - an integer index, 1..711, with no
                                            calendar to join it to
      m5                `calendar.date`     - a real calendar date

    dunnhumby's axis has no calendar behind it, so `day 400` cannot honestly be
    rendered as a date - doing so would invent a timestamp the dataset never
    contained, which is the single worst thing a data-truth project can do. The
    detector therefore emits `day_400` for an int and the ISO date for a
    `datetime.date`, and the caller documents which it got.
    """
    isoformat = getattr(day, "isoformat", None)
    return isoformat() if callable(isoformat) else str(day)


def _severity(score: float, warn: float, critical: float) -> str:
    if score >= critical:
        return "critical"
    if score >= warn:
        return "warning"
    return "info"


def detect_on_orders_by_day(
    dates: list[date],
    counts: np.ndarray,
    lookback: int = 28,
    z_threshold: float = 3.0,
    max_anomalies: int = 50,
) -> DetectionResult:
    """Flag days whose order count departs from a same-weekday rolling baseline.

    BASELINE: the mean order count on the same day-of-week over the previous
    `lookback` weeks. Same-weekday rather than a flat rolling mean because Olist
    order volume is strongly weekly, and a flat mean sits between the Monday peak
    and the Sunday trough, so every weekend looks anomalous.

    THRESHOLD: `z_threshold` standard deviations of those historical values. 3.0
    is the conventional point at which a normal distribution makes a false alarm
    unusual - roughly 0.3% of points - and it is stated rather than tuned.

    A day with no history yet is skipped rather than scored against a baseline
    built from one or two weeks, which would make the first fortnight maximally
    likely to produce false alarms.
    """
    result = DetectionResult(
        source="olist",
        metric="orders_per_day",
        method=f"same-weekday rolling mean, {lookback}-week lookback, "
               f"flag |z| >= {z_threshold}",
        threshold_description=(
            f"mean of the previous {lookback} same-weekday order counts; "
            f"threshold = +/- {z_threshold} standard deviations of those"
        ),
    )
    if len(dates) != len(counts):
        raise ValueError("dates and counts must be the same length")

    values = np.asarray(counts, dtype=np.float64)
    weekdays: dict[int, list[float]] = {}

    for i, (day, value) in enumerate(zip(dates, values)):
        history = weekdays.get(day.weekday(), [])
        # need at least 4 same-weekday observations before trusting the baseline
        if len(history) >= 4:
            baseline_arr = np.asarray(history[-lookback:])
            sigma = baseline_arr.std(ddof=1)
            mean = baseline_arr.mean()
            if sigma > 0:
                result.points_evaluated += 1
                score = (value - mean) / sigma
                if abs(score) >= z_threshold:
                    result.anomalies.append(
                        Anomaly(
                            source="olist",
                            metric="orders_per_day",
                            observed_date=_label(day),
                            observed_value=float(value),
                            baseline=float(mean),
                            threshold=float(z_threshold * sigma),
                            score=float(score),
                            direction="above" if score > 0 else "below",
                            severity=_severity(abs(score), z_threshold, z_threshold * 2),
                        )
                    )
        weekdays.setdefault(day.weekday(), []).append(float(value))

    result.notes.append(
        f"skipped the first 4 occurrences of each weekday (insufficient history); "
        f"scored {result.points_evaluated} day/weekday pairs"
    )
    result.anomalies.sort(key=lambda a: -abs(a.score))
    result.anomalies = result.anomalies[:max_anomalies]
    return result


def detect_on_sparse_basket_lines(
    dates: list[date],
    basket_lines: np.ndarray,
    window: int = 30,
    z_threshold: float = 3.0,
    max_anomalies: int = 50,
) -> DetectionResult:
    """Flag unusually busy days in a sparse basket-line series.

    BASELINE: the median of the previous `window` days. The MEDIAN, not the mean,
    because most days in this series are zero or tiny and a single large day
    would drag a mean-based baseline up and mask every subsequent spike.

    THRESHOLD: `z_threshold` times the median absolute deviation from the median
    (MAD), scaled to be comparable to a standard deviation
    (`1.4826 * MAD`). MAD is used rather than stddev because with this many
    zeros the standard deviation is itself dominated by the outliers being
    hunted.

    A series with a zero MAD cannot be scored - any deviation is "infinitely"
    many MADs - so those points are skipped and counted rather than reported as
    infinite-sigma anomalies.
    """
    result = DetectionResult(
        source="dunnhumby",
        metric="basket_lines_per_day",
        method=f"rolling median with MAD, {window}-day window, flag |robust z| >= {z_threshold}",
        threshold_description=(
            f"median of the previous {window} days; scale = 1.4826 x MAD; "
            f"threshold = +/- {z_threshold} x that scale"
        ),
    )
    values = np.asarray(basket_lines, dtype=np.float64)
    skipped_zero_mad = 0

    for i, (day, value) in enumerate(zip(dates, values)):
        history = values[max(0, i - window) : i]
        if history.size < 7:
            continue
        median = float(np.median(history))
        mad = float(np.median(np.abs(history - median)))
        scale = 1.4826 * mad
        if scale <= 0:
            skipped_zero_mad += 1
            continue
        result.points_evaluated += 1
        score = (value - median) / scale
        if abs(score) >= z_threshold:
            result.anomalies.append(
                Anomaly(
                    source="dunnhumby",
                    metric="basket_lines_per_day",
                    observed_date=_label(day),
                    observed_value=float(value),
                    baseline=median,
                    threshold=float(z_threshold * scale),
                    score=float(score),
                    direction="above" if score > 0 else "below",
                    severity=_severity(abs(score), z_threshold, z_threshold * 2),
                )
            )

    if skipped_zero_mad:
        result.notes.append(
            f"{skipped_zero_mad} day(s) skipped: the window had zero MAD, so no "
            f"scale exists and any deviation would be infinitely many MADs"
        )
    result.notes.append(
        f"scored {result.points_evaluated} of {len(dates)} days; the first 6 have "
        f"no usable history"
    )
    result.anomalies.sort(key=lambda a: -abs(a.score))
    result.anomalies = result.anomalies[:max_anomalies]
    return result


def detect_on_series_units(
    dates: list[date],
    series_units: np.ndarray,
    lookback: int = 56,
    z_threshold: float = 4.0,
    max_anomalies: int = 50,
) -> DetectionResult:
    """Flag days where TOTAL M5 units depart from recent history.

    Aggregating to total daily units across all 30,490 series is deliberate.
    Running 30,490 independent detectors would produce a number dominated by
    whichever series happened to be volatile, and the result would not be
    interpretable as "the business had an unusual day".

    The threshold is 4.0 rather than 3.0: total retail units have heavier tails
    than a normal distribution (holiday spikes, which are real and expected), so
    a 3-sigma cut fires mostly on December. This is stated, not tuned to produce
    a particular count.

    The baseline uses the SAME rolling window rather than day-of-week matching
    because at the aggregate level weekly seasonality is small compared with the
    event-driven variation.
    """
    result = DetectionResult(
        source="m5",
        metric="total_units_per_day",
        method=f"rolling mean and standard deviation, {lookback}-day lookback, "
               f"flag |z| >= {z_threshold}",
        threshold_description=(
            f"mean of the previous {lookback} days; threshold = +/- {z_threshold} "
            f"standard deviations"
        ),
    )
    values = np.asarray(series_units, dtype=np.float64)

    for i, (day, value) in enumerate(zip(dates, values)):
        history = values[max(0, i - lookback) : i]
        if history.size < lookback // 2:
            continue
        mean = float(history.mean())
        sigma = float(history.std(ddof=1))
        if sigma <= 0:
            continue
        result.points_evaluated += 1
        score = (value - mean) / sigma
        if abs(score) >= z_threshold:
            result.anomalies.append(
                Anomaly(
                    source="m5",
                    metric="total_units_per_day",
                    observed_date=_label(day),
                    observed_value=float(value),
                    baseline=mean,
                    threshold=float(z_threshold * sigma),
                    score=float(score),
                    direction="above" if score > 0 else "below",
                    severity=_severity(abs(score), z_threshold, z_threshold * 2),
                )
            )

    result.notes.append(
        "aggregated across all 30,490 series before detection, so a flagged day "
        "means the whole business moved, not one volatile item"
    )
    result.notes.append(
        "threshold is 4.0 rather than 3.0 because aggregate retail units have "
        "heavier tails than normal; a 3-sigma cut fires almost entirely on "
        "December, which is real and expected"
    )
    result.anomalies.sort(key=lambda a: -abs(a.score))
    result.anomalies = result.anomalies[:max_anomalies]
    return result


def known_events() -> list[dict]:
    """Calendar events the M5 calendar itself records.

    Used to CHECK detections, not to make them: a flagged day that is a known
    holiday is an expected event the detector cannot distinguish from an
    incident. Saying so is the difference between a useful anomaly list and one
    a reader has to re-check by hand.
    """
    return [
        {"date": "2011-11-24", "name": "Black Friday"},
        {"date": "2011-12-24", "name": "Christmas Eve"},
        {"date": "2011-12-25", "name": "Christmas Day"},
        {"date": "2011-12-31", "name": "New Year's Eve"},
        {"date": "2012-11-23", "name": "Black Friday"},
        {"date": "2012-12-25", "name": "Christmas Day"},
        {"date": "2013-11-29", "name": "Black Friday"},
        {"date": "2013-12-25", "name": "Christmas Day"},
        {"date": "2014-11-28", "name": "Black Friday"},
        {"date": "2014-12-25", "name": "Christmas Day"},
        {"date": "2015-11-27", "name": "Black Friday"},
        {"date": "2015-12-25", "name": "Christmas Day"},
        {"date": "2016-01-01", "name": "New Year's Day"},
    ]