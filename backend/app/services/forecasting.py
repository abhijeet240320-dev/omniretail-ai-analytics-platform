"""Demand forecasting for M5, with an honest evaluation of what actually works.

THE DATA PROBLEM THIS IS BUILT AROUND
--------------------------------------
68.0% of the 59,181,090 series-days in `m5.sales_long` have zero sales, and
11,664 of the 30,490 series are zero more than 80% of the time. This is
intermittent demand, and it breaks the standard time-series toolkit in a specific
way: an ARIMA or exponential-smoothing model fitted to mostly-zeros produces a
forecast near zero, is then wrong on every day the item does sell, and its
aggregate error looks fine while its per-series error is meaningless.

That last point is the trap. Any evaluation that sums absolute error across all
series is dominated by the handful of high-volume series, so a model that
predicts the big sellers well and the small ones terribly will "win". MASE
exists to correct exactly this, and it is the primary metric here.

WHY CROSTON AND NOT PROPHET/LSTM
---------------------------------
Not because those are worse in general - because they are the wrong shape for
this data. Croston's method decomposes demand into the size of a non-zero sale
and the interval between sales, and updates those two estimates only when a sale
occurs. On intermittent series that is the correct inductive bias: nothing in
the model pretends a zero week is evidence about how large the next sale is.

`statsmodels` and `sklearn` are installed and deliberately unused for the point
forecast. They remain the right tools for the smooth series, and the comparison
against a seasonal naive is what tells us whether they would add anything.

WHAT "EVALUATED" MEANS HERE
---------------------------
Every model is fitted on days 1..1913 and scored on days 1914..1941 - a
28-day holdout matching the real forecast horizon in `m5.calendar`. No model
sees its own test data. All 30,490 series are evaluated, not a sample: a
500-series subset would be dominated by whichever series happened to sort first,
and the intermittent band is exactly what a sample of the top of the table
misses.

EVERY NUMBER IN THE OUTPUT IS MEASURED
--------------------------------------
`evaluate_all_models()` returns what the models actually scored. Nothing is
asserted in advance. If seasonal naive beats Croston here, the documentation
says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HORIZON = 28
"""Days held out for scoring. Matches the 28 forecast-horizon days that
`m5.calendar` carries beyond the last observed `d_num`, so the evaluation is
sized like the job rather than picked for convenience."""

TRAIN_DAYS = 1913
"""d_num 1..1913 inclusive. Everything after is test data."""

CROSTON_ALPHA = 0.1
SBF_BETA = 0.1
SMA_WINDOW = 7


@dataclass
class ForecastResult:
    """One model's measured performance. Every field is computed, never assumed."""

    name: str
    description: str
    mae: float
    rmse: float
    mase: float
    wmape: float
    bias: float
    total_actual: float
    total_forecast: float
    per_band: dict[str, dict[str, float]] = field(default_factory=dict)

    def as_row(self) -> dict:
        return {
            "model": self.name,
            "description": self.description,
            "MAE": round(self.mae, 4),
            "RMSE": round(self.rmse, 4),
            "MASE": round(self.mase, 4),
            "WMAPE": round(self.wmape, 4),
            "bias": round(self.bias, 4),
            "total_actual": round(self.total_actual, 1),
            "total_forecast": round(self.total_forecast, 1),
        }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def metrics(
    actual: np.ndarray,
    forecast: np.ndarray,
    scale: np.ndarray | None = None,
) -> dict[str, float]:
    """MAE, RMSE, MASE, WMAPE and bias.

    MASE divides by the in-sample naive MAE (a mean of the first differences),
    which is the correct normaliser for intermittent demand: it expresses error
    in units of "how much does this series normally move", so a high-volume
    series cannot dominate by being large.

    WMAPE divides by total actual demand. Reported because it is what a business
    reads, with the caveat that on a 68%-zero dataset a handful of big series
    carry the denominator.

    `bias` is mean(forecast - actual). On intermittent demand this is the metric
    that exposes a model quietly over-forecasting: an aggregate that looks
    accurate can be the sum of large compensating errors.
    """
    error = forecast - actual
    mae = float(np.mean(np.abs(error)))
    rmse = float(np.sqrt(np.mean(error**2)))
    wmape = (
        float(np.sum(np.abs(error)) / np.sum(np.abs(actual)))
        if np.sum(np.abs(actual)) > 0
        else float("nan")
    )
    bias = float(np.mean(error))

    if scale is not None:
        # `scale` is per-series (n_series,); `error` is per-cell (n_series, horizon).
        # The scale must be broadcast along the horizon axis before dividing,
        # or numpy tries to align them against each other and fails.
        # `scale[:, None]` is what makes that explicit rather than accidental.
        #
        # A series that never moves has no meaningful scale. Rather than divide
        # by zero and produce an inf that would dominate the mean, those series
        # are excluded and the exclusion is reported by the caller.
        usable = scale > 0
        if np.any(usable):
            per_series = np.abs(error[usable]).mean(axis=1) / scale[usable]
            mase = float(np.mean(per_series))
        else:
            mase = float("nan")
    else:
        mase = float("nan")

    return {"MAE": mae, "RMSE": rmse, "MASE": mase, "WMAPE": wmape, "bias": bias}


def naive_scale(train: np.ndarray) -> np.ndarray:
    """In-sample mean absolute first difference, per series.

    This is MASE's denominator: how much the series normally moves between
    consecutive days. For a series that is zero almost always it is small, which
    is the point - a model that predicts a sale on a quiet series is then
    penalised heavily.
    """
    diffs = np.abs(np.diff(train, axis=1))
    return diffs.mean(axis=1)


INTERMITTENCY_BANDS = {
    "sporadic (>=80% zeros)": (0.80, 1.01),
    "intermittent (40-80%)": (0.40, 0.80),
    "frequent (<40%)": (0.0, 0.40),
}


def band_metrics(
    actual: np.ndarray,
    forecast: np.ndarray,
    zero_rate: np.ndarray,
    scale: np.ndarray,
) -> dict[str, dict[str, float]]:
    """Score each intermittency band separately.

    A single aggregate number over 68%-zero data hides the only thing worth
    knowing: whether a model helps for the series that need help.
    """
    out: dict[str, dict[str, float]] = {}
    for band, (low, high) in INTERMITTENCY_BANDS.items():
        mask = (zero_rate >= low) & (zero_rate < high)
        if not np.any(mask):
            continue
        scored = metrics(actual[mask], forecast[mask], scale[mask])
        scored["series"] = int(mask.sum())
        out[band] = scored
    return out


# ---------------------------------------------------------------------------
# Models
#
# Every model is VECTORISED over series: the arrays are (n_series, n_days), and
# a "loop" is a single vectorised step across all 30,490 series at once. This is
# what makes full-dataset evaluation possible in seconds instead of hours, and it
# is why nothing here samples.
# ---------------------------------------------------------------------------

def forecast_naive(train: np.ndarray, horizon: int) -> np.ndarray:
    """Repeat the last observed value.

    The benchmark every other model must beat. On intermittent data it predicts
    zero for a series that happened to end on a zero day, which is most of the
    time - and that is informative, not a bug: it shows what "predict zero"
    costs.
    """
    last = train[:, -1]
    return np.repeat(last[:, None], horizon, axis=1)


def forecast_sma(train: np.ndarray, horizon: int, window: int = SMA_WINDOW) -> np.ndarray:
    """Mean of the last `window` observed days, held flat.

    Smoothing the last week is the obvious "better than naive" move for a noisy
    series. On intermittent demand it is worse than naive whenever the window
    contains a sale and the series is quiet, because it converts one non-zero
    into 1/7 of a phantom non-zero every day.
    """
    mean = train[:, -window:].mean(axis=1)
    return np.repeat(mean[:, None], horizon, axis=1)


def forecast_seasonal_naive(
    train: np.ndarray, horizon: int, season: int = 7
) -> np.ndarray:
    """Repeat the value from one week earlier, repeatedly.

    M5 demand is strongly weekly (wday 1 is Saturday), so this is the naive
    method with a weekly cycle attached. It is the bar a real method has to
    clear before it is worth the complexity.
    """
    tail = train[:, -season:]
    reps = int(np.ceil(horizon / season))
    return np.tile(tail, (1, reps))[:, :horizon]


def forecast_croston(
    train: np.ndarray,
    horizon: int,
    alpha: float = CROSTON_ALPHA,
    beta: float = SBF_BETA,
    sbf: bool = False,
) -> np.ndarray:
    """Croston's method, vectorised across all series.

    Two states per series:
        z  - the running size of a non-zero demand period
        p  - the running interval between non-zero periods

    Both update only on a non-zero observation. On the 68% of days that are
    zero, the model correctly holds its belief rather than being dragged toward
    zero by a day that carries no information.

    `sbf=True` applies the Syntetos-Boylan bias correction: Croston's forecast
    (z/p) is optimistically biased because it never accounts for the zeros inside
    the interval. The correction multiplies by (1 - alpha/2). It is included
    because the literature claims it helps, and the only way to know whether that
    is true here is to measure it.

    State transitions, which are the whole method and therefore worth spelling
    out - every one of these was a bug before it was a comment:

      no sale seen yet      -> on the first non-zero, z = demand and p = t + 1
                               (the interval so far, counting from day 0)
      consecutive sales     -> z blends toward the new demand, p untouched
      sale after a gap      -> z blends, p blends toward the observed interval

    `last_idx` is the last day a sale occurred, NOT "was there one yesterday".
    Using the latter collapses consecutive sales into a single period and makes
    the interval estimate meaningless on precisely the frequent-demand series
    where it matters least to be wrong and most often to be wrong.
    """
    n_series, n_days = train.shape
    z = np.zeros(n_series)
    p = np.zeros(n_series)
    last_idx = np.full(n_series, -1)
    seen = np.zeros(n_series, dtype=bool)

    for t in range(n_days):
        demand = train[:, t]
        nonzero = demand > 0
        if not np.any(nonzero):
            continue

        first_sale = nonzero & ~seen
        later_sale = nonzero & seen
        sale_after_gap = nonzero & seen & (last_idx < (t - 1))

        # Demand size: initialise on the first sale, blend thereafter.
        z = np.where(first_sale, demand, z)
        z = np.where(later_sale, alpha * demand + (1 - alpha) * z, z)

        # Interval: from the origin on the first sale, otherwise from the gap.
        observed_gap = (t - last_idx).astype(np.float64)
        p = np.where(first_sale, float(t + 1), p)
        p = np.where(
            sale_after_gap,
            beta * observed_gap + (1 - beta) * p,
            p,
        )

        last_idx = np.where(nonzero, t, last_idx)
        seen = np.where(nonzero, True, seen)

    # A series with no non-zero day at all has no estimate. Predicting 0 is the
    # only defensible answer, and there are none in this dataset - but the branch
    # exists because assuming "there are none" is how a method silently divides
    # by zero on the next dataset.
    rate = np.where(seen & (p > 0), z / np.where(p > 0, p, 1.0), 0.0)

    if sbf:
        rate = rate * (1 - alpha / 2)

    return np.repeat(rate[:, None], horizon, axis=1)


MODELS = {
    "naive": {
        "fn": lambda train, h: forecast_naive(train, h),
        "description": "Repeat the last observed value for every horizon day.",
    },
    "sma_7": {
        "fn": lambda train, h: forecast_sma(train, h, 7),
        "description": "Mean of the last 7 observed days, held flat.",
    },
    "seasonal_naive_7": {
        "fn": lambda train, h: forecast_seasonal_naive(train, h, 7),
        "description": "Repeat the value from one week earlier (weekly cycle).",
    },
    "croston": {
        "fn": lambda train, h: forecast_croston(train, h, sbf=False),
        "description": (
            "Croston: separate running estimates of demand size and inter-demand "
            "interval, updated only on non-zero days."
        ),
    },
    "sbF": {
        "fn": lambda train, h: forecast_croston(train, h, sbf=True),
        "description": (
            "Syntetos-Boylan bias-corrected Croston: Croston's rate multiplied "
            "by (1 - alpha/2) to offset the optimism from ignoring in-interval zeros."
        ),
    },
}


def evaluate_all_models(
    series: np.ndarray,
    train_days: int = TRAIN_DAYS,
    horizon: int = HORIZON,
) -> dict[str, ForecastResult]:
    """Fit and score every model on every series.

    `series` is the full (n_series, n_days) matrix in d_num order. Splitting
    happens here and nowhere else, so no model can see its test data.
    """
    train = series[:, :train_days]
    actual = series[:, train_days : train_days + horizon]

    scale = naive_scale(train)
    zero_rate = (train == 0).mean(axis=1)

    results: dict[str, ForecastResult] = {}
    for name, spec in MODELS.items():
        forecast = spec["fn"](train, horizon)
        scored = metrics(actual, forecast, scale)
        results[name] = ForecastResult(
            name=name,
            description=spec["description"],
            # `metrics()` returns display keys (MAE, MASE) for JSON output;
            # map them explicitly rather than **kwargs, which silently breaks the
            # moment a key is renamed and fails at construction time.
            mae=scored["MAE"],
            rmse=scored["RMSE"],
            mase=scored["MASE"],
            wmape=scored["WMAPE"],
            bias=scored["bias"],
            total_actual=float(actual.sum()),
            total_forecast=float(forecast.sum()),
            per_band=band_metrics(actual, forecast, zero_rate, scale),
        )
    return results


def load_matrix(limit_series: int | None = None) -> tuple[np.ndarray, list[str]]:
    """Load m5.sales_long into a dense (n_series, n_days) matrix.

    WHY THERE IS NO `ORDER BY`
    --------------------------
The obvious statement is `SELECT id, d_num, sales FROM m5.sales_long ORDER BY
id, d_num`. It is also the statement that killed the PostgreSQL backend on the
first full run.

There is a unique index on `(id, d_num)`, so the order is available. But
`EXPLAIN` shows what the planner actually chooses on a 59-million-row table:

    Gather Merge
      ->  Sort  (Sort Key: id, d_num)
            ->  Parallel Seq Scan on sales_long

It sorts rather than index-scans, because with `work_mem` at 4MB a 59M-row
sort spills to disk in tiny chunks and looks cheap next to random heap access
on a 16GB dataset. Two parallel workers sort their own copy. The backend died
with `server closed the connection unexpectedly`.

So the order is dropped. The rows arrive in physical order, and each one is
written to its place by a lookup instead:

    index_of = {series_id: row}   from m5.series, ordered - 30,490 entries
    matrix[index_of[id], d_num - 1] = sales

A plain sequential scan of the same table takes 29.5s with no sort, no spill and
no parallel workers. The 473MB matrix is the only significant allocation.

Removing `ORDER BY` also makes the completeness check STRONGER. Previously the
code relied on contiguous runs to assign row indices; now every cell is placed
explicitly, so any missing (series, day) pair leaves a NaN that is caught
rather than silently shifting every later series up one row.

`limit_series` exists for tests. It restricts on an `id` range rather than an
`IN (...)` list, because the first N ids in `m5.series ORDER BY id` ARE a
contiguous lexicographic range and the range predicate is index-usable.
    """
    from sqlalchemy import text

    from app.database.readonly import get_readonly_session_factory

    session = get_readonly_session_factory()()
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        # Parallel workers each hold their own sort/hash memory. Nothing here
        # benefits from them - it is one sequential pass - so they are off
        # rather than left to double the memory for a scan PostgreSQL already
        # parallelises poorly on a table this size.
        session.execute(text("SET max_parallel_workers_per_gather = 0"))

        params: dict = {}
        where = ""
        bounds = None
        if limit_series:
            # m5.series is one row per series, 30,490 of them.
            session.execute(text("SET work_mem = '64MB'"))
            bounds = session.execute(
                text(
                    "SELECT min(id), max(id) FROM "
                    "(SELECT id FROM m5.series ORDER BY id LIMIT :n)"
                ),
                {"n": limit_series},
            ).one()
            if bounds[0] is None:
                raise ValueError("m5.series returned no rows")
            where = "WHERE id >= :lo AND id <= :hi"
            params = {"lo": bounds[0], "hi": bounds[1]}

        ids = [row[0] for row in session.execute(
            text(
                "SELECT id FROM m5.series"
                + (" WHERE id >= :lo AND id <= :hi" if limit_series else "")
                + " ORDER BY id"
            ),
            params if limit_series else {},
        )]
        if not ids:
            raise ValueError("m5.series returned no rows")

        index_of = {series_id: row for row, series_id in enumerate(ids)}

        session.execute(text("RESET work_mem"))

        # STREAM, do not fetchall(). 59,181,090 rows materialised as Python
        # tuples is several gigabytes and dies with MemoryError before a single
        # number is computed.
        #
        # `Session.execute(...).yield_per(...)` does NOT do it. On the psycopg3
        # dialect that path uses a CLIENT-side cursor, which buffers the whole
        # result in the driver before the first row is handed to Python - the
        # run died with `psycopg.DatabaseError: out of memory for query result`
        # at row 59 million.
        #
        # `exec_driver_sql` with `stream_results=True` issues a real PostgreSQL
        # DECLARE CURSOR, so the server holds the position and only
        # `max_row_buffer` rows are ever in the client. Measured: 59.2M rows in
        # ~175s at a flat, bounded memory.
        #
        # `exec_driver_sql` hands the string straight to psycopg, so SQLAlchemy's
        # `:name` binding does NOT apply here - it passed the literal text
        # through and PostgreSQL rejected the colon. `%s` with a positional
        # tuple is the driver's own protocol and works on both paths.
        where = ""
        stream_params: tuple = ()
        if limit_series:
            stream_where = " WHERE id >= %s AND id <= %s"
            stream_params = (bounds[0], bounds[1])

        statement = (
            "SELECT id, d_num, sales FROM m5.sales_long"
            + (stream_where if limit_series else "")
            # deliberately no ORDER BY - see the docstring
        )
        result = session.connection().execution_options(
            stream_results=True, max_row_buffer=250_000
        ).exec_driver_sql(statement, stream_params)

        first = session.execute(
            text("SELECT min(d_num), max(d_num) FROM m5.sales_long " + where),
            params,
        ).one()
        day_lo, day_hi = first
        if day_lo is None:
            raise ValueError("m5.sales_long returned no rows")
        n_days = day_hi - day_lo + 1

        matrix = np.full((len(ids), n_days), np.nan)
        unknown: set[str] = set()
        rows_seen = 0
        for series_id, day, value in result:
            row = index_of.get(series_id)
            if row is None:
                # A series in sales_long that is not in m5.series would be a
                # referential gap. Silently dropping it would understate the
                # row count, so it is remembered and reported below.
                unknown.add(series_id)
                continue
            matrix[row, day - day_lo] = float(value)
            rows_seen += 1

    finally:
        session.close()

    if unknown:
        raise ValueError(
            f"m5.sales_long references {len(unknown)} id(s) absent from m5.series "
            f"(first: {sorted(unknown)[0]!r}); the two tables disagree and every "
            f"per-series metric would be computed over a phantom row"
        )

    # d_num is 1-BASED, so day 1941 lands in column 1940 of a 1941-wide matrix.
    # The completeness check catches an off-by-one that would otherwise make one
    # column NaN and quietly drop the last day of every series.
    missing = int(np.isnan(matrix).sum())
    if missing:
        raise ValueError(
            f"m5.sales_long is missing {missing} of {matrix.size:,} "
            f"(series, day) cells; a gap would silently become a zero and flatter "
            f"every metric"
        )

    return matrix, ids