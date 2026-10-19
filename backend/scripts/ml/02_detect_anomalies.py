"""Run anomaly detection over the three time-series sources and record it.

USAGE
-----
    .venv/Scripts/python.exe backend/scripts/ml/02_detect_anomalies.py

WHY THE OUTPUT IS A FILE AND NOT A PRINT
----------------------------------------
`docs/anomaly_detection.md` is generated from
`data/processed/_anomaly_results.json`. A finding that exists only in a terminal
scrollback is a rumour by the time anyone reads it, and the whole project treats
that as unacceptable.

WHAT GETS CROSS-CHECKED
-----------------------
Detected M5 anomalies are compared against the calendar events the dataset itself
records (Black Friday, Christmas). A detector that flags Christmas is not wrong -
it is flagging a real, expected spike - and this script counts how many of its
flags are known events so the number can be read honestly.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import numpy as np  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services.anomaly_detection import (  # noqa: E402
    detect_on_orders_by_day,
    detect_on_sparse_basket_lines,
    detect_on_series_units,
    known_events,
)

OUTPUT = REPO_ROOT / "data" / "processed" / "_anomaly_results.json"


def _connect():
    import psycopg

    settings = get_settings()
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_readonly_user,
        password=settings.postgres_readonly_password,
        connect_timeout=15,
    )


def main() -> int:
    print("OmniRetail anomaly detection")
    print("=" * 78)

    conn = _connect()
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    conn.commit()

    results = []

    # ---- olist: orders per day -------------------------------------------
    print("\n  [1/3] olist.orders - daily order counts")
    rows = conn.execute(
        """
        SELECT purchase_date::date AS d, count(*) AS n
        FROM olist.orders
        WHERE purchase_date IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    olist_dates = [r[0] for r in rows]
    olist_counts = np.array([r[1] for r in rows], dtype=np.float64)
    olist_result = detect_on_orders_by_day(olist_dates, olist_counts)
    print(f"        {olist_result.points_evaluated} day/weekday pairs scored, "
          f"{len(olist_result.anomalies)} flagged "
          f"({olist_result.firing_rate:.2%})")
    results.append(olist_result)

    # ---- dunnhumby: basket lines per day ---------------------------------
    print("\n  [2/3] dunnhumby.transactions - daily basket lines")
    # dunnhumby's time axis is `day`, an integer index. There is no calendar to
    # join, so the dates below are POSITIONAL LABELS, not real dates, and are
    # marked as such - reporting day 400 as "2013-05-14" would invent a date the
    # dataset never contained.
    rows = conn.execute(
        """
        SELECT day, count(*) AS n
        FROM dunnhumby.transactions
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    dnh_days = [r[0] for r in rows]
    dnh_counts = np.array([r[1] for r in rows], dtype=np.float64)
    # The detector's `_label` already renders an integer axis as `day_N`, so
    # there is nothing to fix up here. It used to be fixed up afterwards, which
    # meant the first run raised AttributeError inside the detector before this
    # line was ever reached.
    dnh_result = detect_on_sparse_basket_lines(dnh_days, dnh_counts)
    dnh_result.notes.insert(
        0,
        "dunnhumby's time axis is the integer `day` index (1..711), not a "
        "calendar date. Labels are day_N and MUST NOT be read as dates.",
    )
    print(f"        {dnh_result.points_evaluated} days scored, "
          f"{len(dnh_result.anomalies)} flagged "
          f"({dnh_result.firing_rate:.2%})")
    results.append(dnh_result)

    # ---- m5: total units per day -----------------------------------------
    print("\n  [3/3] m5.sales_long - total daily units across all series")
    rows = conn.execute(
        """
        SELECT cal.date::date AS d, sum(s.sales) AS units
        FROM m5.sales_long s
        JOIN m5.calendar cal ON cal.d_num = s.d_num
        WHERE s.d_num <= (SELECT max(d_num) FROM m5.sales_long)
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    m5_dates = [r[0] for r in rows]
    m5_units = np.array([r[1] for r in rows], dtype=np.float64)
    m5_result = detect_on_series_units(m5_dates, m5_units)
    print(f"        {m5_result.points_evaluated} days scored, "
          f"{len(m5_result.anomalies)} flagged "
          f"({m5_result.firing_rate:.2%})")

    events = {e["date"]: e["name"] for e in known_events()}
    explained = [
        a for a in m5_result.anomalies if a.observed_date in events
    ]
    unexplained = [
        a for a in m5_result.anomalies if a.observed_date not in events
    ]
    m5_result.notes.append(
        f"{len(explained)} of {len(m5_result.anomalies)} flagged m5 dates are "
        f"known calendar events recorded in the dataset itself "
        f"({', '.join(sorted({events[a.observed_date] for a in explained})) if explained else 'none'}); "
        f"{len(unexplained)} are not explained by the calendar"
    )
    results.append(m5_result)

    conn.close()

    print("\n" + "=" * 78)
    print("  SUMMARY")
    for r in results:
        print(f"    {r.source:<10} {r.metric:<24} {len(r.anomalies):>3} flagged "
              f"of {r.points_evaluated:>5,} scored  ({r.firing_rate:.3%})")
    print()
    print("  Top 5 m5 anomalies by |z|:")
    for a in m5_result.anomalies[:5]:
        tag = f"  <- {events[a.observed_date]}" if a.observed_date in events else ""
        print(f"    {a.observed_date}  units={a.observed_value:>10,.0f}  "
              f"baseline={a.baseline:>10,.0f}  z={a.score:>6.2f}  "
              f"{a.direction}{tag}")

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "method_note": (
            "Every anomaly carries its own baseline and threshold. No point is "
            "reported without them, because a bare 'anomaly: true' cannot be "
            "judged by a reader."
        ),
        "detections": [r.as_dict() for r in results],
        "known_events": known_events(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print()
    print(f"  report -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())