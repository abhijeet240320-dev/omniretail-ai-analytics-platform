"""Phase 7: execute the hand-written analytics SQL and record what it actually returned.

USAGE (from the repository root)
--------------------------------
    .venv/Scripts/python.exe backend/scripts/sql/run_analytics.py
    .venv/Scripts/python.exe backend/scripts/sql/run_analytics.py --source olist
    .venv/Scripts/python.exe backend/scripts/sql/run_analytics.py --only monthly_revenue_trend

WHY A RUNNER INSTEAD OF "RUN IT BY HAND"
---------------------------------------
`docs/sql_analytics.md` is generated, not written. Every figure in it comes from
this script writing `data/processed/_sql_results.json`, and a query that errors
here never reaches the document. A finding nobody can reproduce is a rumour, and
the whole point of this project is that nothing in it was typed from memory.

FILE FORMAT
-----------
Each `.sql` file is a series of named queries:

    -- [query: monthly_revenue_trend]
    -- What does order volume and revenue look like month by month?
    SELECT ...
    SELECT ...;

The `-- [query: NAME]` marker starts a block. Everything after it up to the next
marker or EOF is one query, executed as a single statement batch. The lines
between the marker and the first SQL keyword are the question, carried through to
the documentation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402

MARKER = re.compile(r"^--\s*\[query:\s*([A-Za-z0-9_]+)\s*\]\s*$")
SQL_DIR = REPO_ROOT / "backend" / "sql"
# 00_bootstrap holds DDL for cluster setup, not analytics to re-run every time.
SOURCES = ("olist", "dataco", "dunnhumby", "m5")


class QueryBlock:
    """One `-- [query: name]` section of a .sql file."""

    def __init__(self, name: str, file: Path, source: str) -> None:
        self.name = name
        self.file = file
        self.source = source
        self.lines: list[str] = []

    @property
    def sql(self) -> str:
        return "\n".join(self.lines).strip()

    @property
    def question(self) -> str:
        """The comment lines under the marker: the business question."""
        lines = [ln.strip().lstrip("-").strip() for ln in self.lines]
        text: list[str] = []
        for line in lines:
            if not line or line.upper() == "SELECT" or line.upper().startswith("SELECT "):
                break
            text.append(line)
        return " ".join(text).strip()

    @property
    def relative(self) -> str:
        return self.file.relative_to(REPO_ROOT).as_posix()


def parse_file(path: Path, source: str) -> list[QueryBlock]:
    blocks: list[QueryBlock] = []
    current: QueryBlock | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        match = MARKER.match(raw)
        if match:
            current = QueryBlock(match.group(1), path, source)
            blocks.append(current)
            continue
        if current is not None:
            current.lines.append(raw)
    return [b for b in blocks if b.sql]


def run_block(conn, block: QueryBlock) -> dict:
    """Execute one block and return everything the documentation needs."""
    started = time.monotonic()
    record: dict = {
        "source": block.source,
        "file": block.relative,
        "name": block.name,
        "question": block.question,
        "sql": block.sql,
    }
    try:
        # The session is pinned read-only at connect time (see below), so a
        # stray DELETE in a query file cannot reach a loaded dataset.
        with conn.cursor() as cur:
            cur.execute(block.sql)
            if cur.description is None:
                record["error"] = "statement returned no result set"
                return record
            columns = [d.name for d in cur.description]
            rows = cur.fetchall()
        record["columns"] = columns
        record["rows"] = [list(r) for r in rows]
        record["row_count"] = len(rows)
        record["truncated"] = len(rows) > 50
        record["rows"] = record["rows"][:50]
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        # ROLLBACK clears the aborted transaction. The session-level read-only
        # characteristic survives it (only SET LOCAL is transaction-scoped), so
        # the next block is still protected.
        conn.rollback()
        record["error"] = f"{exc.__class__.__name__}: {exc}"
        record["row_count"] = 0
    record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the analytics SQL.")
    parser.add_argument("--source", action="append", choices=list(SOURCES))
    parser.add_argument("--only", help="run a single named query")
    args = parser.parse_args()

    import psycopg

    settings = get_settings()
    sources = args.source or list(SOURCES)

    files: list[tuple[Path, str]] = []
    for source in sources:
        folder = SQL_DIR / source
        if not folder.is_dir():
            print(f"[SKIP] no SQL folder for source '{source}'")
            continue
        files.extend((p, source) for p in sorted(folder.glob("*.sql")))
    if not files:
        print("no .sql files found under backend/sql")
        return 1

    print("OmniRetail analytics SQL runner")
    print(f"  target: {settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}")
    print(f"  files  : {len(files)}")
    print("=" * 70)

    results: list[dict] = []
    # settings.database_url is a SQLAlchemy URL ("postgresql+psycopg://...").
    # psycopg wants the plain "postgresql://..." form, so the driver tag comes
    # off. Connection arguments come from Settings so no password is ever built
    # by string surgery on a URL.
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=10,
    ) as conn:
        # Pin the whole session read-only. This loader account is normally the
        # read/write role, so without this a mistyped DELETE in a query file
        # would happily execute against a loaded dataset. Doing it at the
        # session level rather than per transaction avoids SET TRANSACTION's
        # "must be the first statement" ordering trap.
        with conn.cursor() as cur:
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.commit()
        for path, source in files:
            print(f"\n=== {source}: {path.name}")
            for block in parse_file(path, source):
                if args.only and block.name != args.only:
                    continue
                record = run_block(conn, block)
                results.append(record)
                if "error" in record:
                    print(f"  [FAIL] {block.name}: {record['error']}")
                    continue
                head = ", ".join(record["columns"])
                print(f"  [ ok ] {block.name}: {record['row_count']} row(s) [{head}]")

    failures = [r for r in results if "error" in r]
    print("\n" + "=" * 70)
    print(f"ran {len(results)} quer(ies): {len(results) - len(failures)} ok, {len(failures)} failed")

    destination = REPO_ROOT / "data" / "processed" / "_sql_results.json"

    # A full run rebuilds the file from scratch, so a query that has been renamed
    # or deleted leaves no stale entry behind. Any NARROWED run merges into the
    # previous file, keyed by query name.
    #
    # The condition must be `args.source or args.only`. Checking only
    # args.source meant `--only price_and_demand` - which runs exactly one query
    # out of 41 - rewrote the file down to that single query and destroyed the
    # recorded evidence for the other 40. I hit exactly that while re-running
    # one query to confirm it still worked.
    narrowed = bool(args.source) or bool(args.only)
    merged: dict[str, dict] = {}
    if narrowed:
        if destination.exists():
            try:
                previous = json.loads(destination.read_text(encoding="utf-8"))
                merged = {q["name"]: q for q in previous.get("queries", [])}
            except (ValueError, OSError):
                merged = {}
    for record in results:
        merged[record["name"]] = record
    ordered = sorted(merged.values(), key=lambda q: (q["source"], q["name"]))

    destination.write_text(
        json.dumps(
            {
                "query_count": len(ordered),
                "failure_count": sum(1 for q in ordered if "error" in q),
                "queries": ordered,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"results -> {destination} ({len(ordered)} quer(ies) on record)")

    if failures:
        print("\nfailed queries (these will NOT appear in the documentation):")
        for record in failures:
            print(f"  - {record['source']}/{record['name']}: {record['error']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())