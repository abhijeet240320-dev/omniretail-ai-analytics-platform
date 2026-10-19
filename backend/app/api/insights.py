"""Forecasting and anomaly-detection results, as read-only API endpoints.

WHY THIS ROUTER EXISTS
----------------------
Both analyses were run to completion before this API was written, and each one
persisted its entire output to `data/processed/`:

    _forecast_results.json    30,490 M5 series, 1,941 days, 5 models scored
    _anomaly_results.json     3 metrics, 3,223 points evaluated, 63 firings

That JSON is the only place those results exist. Without these two endpoints
they are unreachable from anything except a file reader, which breaks the
architecture rule this project is built on:

    React -> FastAPI -> services / SQL -> PostgreSQL

A frontend that `fetch`ed `data/processed/_forecast_results.json` would be
reading an analysis artefact straight off disk, with no auth, no schema and no
way to reason about staleness. So the two endpoints exist purely to put the
already-computed, already-verified numbers behind the same door as everything
else.

WHAT THESE ENDPOINTS DELIBERATELY DO NOT DO
-------------------------------------------
They do not run a model. There is no training here, no fitting, no inference.
Requesting `/api/forecasting` reads a file that a long batch job wrote once.
That distinction matters for how a reader should treat the numbers: they are
from a completed, reproducible run recorded at `generated_utc`, not from
something that happened while the page was open. Both endpoints say so in the
payload (`generated_utc`, `is_full_dataset`).

They also do not synthesise. If the artefact is missing the response is a 503
that names the file and the command that produces it, rather than an empty
200 that a dashboard would render as "this dataset has no anomalies".

WHY NOT JUST ADD MORE SQL
-------------------------
The temptation is to query `m5.sales_long` for a forecast. You cannot: a
forecast is not in the data. `m5.sales_long` holds observed units, and the
forecast is a model's output, which by construction is nowhere in PostgreSQL.
Reconstructing it here would mean shipping the model into the request path,
which is a different system with different failure modes. Serving the recorded
result is the honest option, and it is the one the rest of this project takes
with `_audit_report.json` and `_sql_results.json`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_analytics
from app.core.config import get_settings

router = APIRouter(prefix="/api", tags=["insights"])

REPO_ROOT = Path(__file__).resolve().parents[3]


def _artefact(filename: str) -> Path:
    """Locate a measurement artefact under the configured data directory.

    The default is the repository's `data/processed`, which is where every
    script in this project writes. `OMNIRETAIL_DATA_DIR` exists so the same API
    can be pointed at a different artefact set in a test or a review - it is a
    path override, not a way to inject results.
    """
    return get_settings().resolved_data_dir / "processed" / filename


def _load(filename: str, missing_detail: str) -> dict[str, Any]:
    """Read one artefact, or refuse with a message that says how to make it.

    Cached because the files are write-once per batch run and this endpoint is
    polled by a dashboard. `lru_cache` on the filename is enough: the set of
    artefacts does not change while the process is up.
    """
    path = _artefact(filename)
    try:
        return _load_cached(str(path))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=missing_detail) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"{path.name} is not valid JSON: {exc.msg} at line {exc.lineno}",
        ) from exc


@lru_cache(maxsize=4)
def _load_cached(resolved: str) -> dict[str, Any]:
    with open(resolved, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    return payload


@router.get("/forecasting", summary="Recorded M5 demand-forecasting benchmark")
def forecasting(
    _principal=Depends(require_analytics),
) -> dict[str, Any]:
    """The completed M5 forecast run: scope, split, models, metrics, winner.

    This is an aggregate model-selection benchmark over 30,490 series, not a
    per-series point forecast. No point forecast series is stored anywhere in
    this project, and the response says so in `shape` so a caller cannot
    mistake `total_forecast` for a forecast curve.

    Returns the artefact verbatim. Selecting fields here would create a second
    place to keep in step with the run that produced them, and the run's own
    JSON is already the reviewed, documented artefact.
    """
    payload = _load(
        "_forecast_results.json",
        (
            "_forecast_results.json is absent, so there is no forecasting result "
            "to report. Run: .venv/Scripts/python.exe backend/scripts/ml/01_evaluate_forecast.py "
            "(this takes about 5 minutes on the full 30,490-series dataset)."
        ),
    )
    payload = dict(payload)
    payload["shape"] = (
        "aggregate model-selection benchmark over every series in the dataset; "
        "no per-series point forecast is stored, and none is invented here"
    )
    payload["source_artefact"] = "data/processed/_forecast_results.json"
    return payload


@router.get("/anomalies", summary="Recorded anomaly detections per source")
def anomalies(
    _principal=Depends(require_analytics),
    source: str | None = Query(
        None, description="Filter to one source: olist, dunnhumby, m5"
    ),
    severity: str | None = Query(
        None, description="Filter to one severity: critical, warning, info"
    ),
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """The completed anomaly-detection run, one block per metric.

    Each firing carries its own observed value, baseline, threshold and sigma
    score. That is the whole point of the detector: a bare "anomaly: true" is
    unfalsifiable, so none is reported without the numbers that produced it.

    `total` is the number of individual firings after filtering, which is not
    the same as `len(detections)` - that is the number of METRICS scanned.
    Reporting both is what stops a dashboard counting three when it means
    thirty-two.
    """
    payload = _load(
        "_anomaly_results.json",
        (
            "_anomaly_results.json is absent, so there is no anomaly result to "
            "report. Run: .venv/Scripts/python.exe backend/scripts/ml/02_detect_anomalies.py"
        ),
    )

    blocks = payload.get("detections", [])
    if not isinstance(blocks, list):
        raise HTTPException(
            status_code=500,
            detail="_anomaly_results.json has a malformed `detections` field; "
            "expected a list of per-metric blocks",
        )

    if source is not None:
        blocks = [b for b in blocks if b.get("source") == source]
    if severity is not None:
        blocks = [
            b
            for b in blocks
            if any(a.get("severity") == severity for a in b.get("anomalies", []))
        ]

    # Flatten to one row per FIRING, keeping the parent metric's method text so
    # a row can always be read without its context. The nested shape stays in
    # `metrics` for callers that want the per-source summary.
    flat: list[dict[str, Any]] = []
    for block in blocks:
        axis = _time_axis(block)
        for anomaly in block.get("anomalies", []):
            flat.append(
                {
                    **anomaly,
                    "method": block.get("method"),
                    "baseline_definition": block.get("baseline"),
                    "time_axis": axis,
                }
            )
    flat.sort(key=lambda a: (_axis_sort_key(a.get("time_axis")), a.get("date") or "", a.get("source") or ""))

    return {
        "generated_utc": payload.get("generated_utc"),
        "method_note": payload.get("method_note"),
        "source_artefact": "data/processed/_anomaly_results.json",
        "known_events": payload.get("known_events", []),
        "metrics": blocks,
        "metrics_count": len(blocks),
        "total": len(flat),
        "limit": limit,
        "offset": offset,
        "rows": flat[offset : offset + limit],
        "caveat": (
            "No labelled ground truth exists for these datasets, so a firing "
            "point means 'unusual relative to its own recent history', not "
            "'wrong'. Business events - holidays, promotions - are the obvious "
            "confounder and every detected date should be checked against "
            "known_events before anyone acts on it."
        ),
    }


def _time_axis(block: dict[str, Any]) -> str:
    """Describe what the `date` field actually is, from the data itself.

    Two of the three metrics carry ISO dates. The third - dunnhumby - carries an
    integer day index, because dunnhumby's time axis IS an integer index and
    there is no calendar in the dataset to convert it with. Rendering that
    column as "Date" would invite a reader to look `643` up as the 643rd day of
    a year, which is exactly the kind of confident misreading this project
    exists to prevent.

    Decided by INSPECTING the labels rather than by hardcoding a source list, so
    a future metric on a fourth dataset gets labelled correctly for free.
    """
    labels = [str(a.get("date", "")) for a in block.get("anomalies", [])]
    if not labels:
        return "unknown (no firings)"
    if all(len(label) == 10 and label[4] == "-" and label[7] == "-" for label in labels):
        return "calendar date"
    return "integer index - not a date"


def _axis_sort_key(axis: str) -> str:
    """Group the two axis kinds apart; within a kind, ISO strings sort correctly."""
    return "1" if axis == "calendar date" else "0"