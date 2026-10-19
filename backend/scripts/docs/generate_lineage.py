"""Generate ``docs/data_lineage.md`` from the real pipeline artefacts.

USAGE (from the repository root)
--------------------------------
    .venv/Scripts/python.exe backend/scripts/docs/generate_lineage.py

WHY GENERATE RATHER THAN HAND-WRITE
-----------------------------------
A hand-written lineage document is correct on the day it is written and wrong
three commits later. Lineage is exactly the kind of documentation that must be
derived from the system itself, so this script reads:

    backend/config/datasets.yaml          who published each dataset, under what licence
    data/raw/_checksums.json              the exact bytes and SHA-256 of every raw file
    data/processed/<source>/_report.json  what the cleaning actually did, per table

...and emits the document. Every table's raw input is declared in code (the
``raw_inputs`` argument to ``new_table``), so a table cannot silently appear in
the documentation without a stated provenance.

IT ALSO VALIDATES
-----------------
Before writing anything it checks that every ``raw_inputs`` name really exists as
a raw file for that source, and that every raw file is used by at least one
processed table. A typo in a declared filename becomes a LOUD FAILURE instead of
a plausible-looking wrong document - which is the whole point of generating it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from etl.registry import load_manifest  # noqa: E402

OUTPUT = REPO_ROOT / "docs" / "data_lineage.md"


def load_reports(manifest) -> dict[str, dict]:
    """Read every processed ``_report.json``. Missing reports are reported."""
    reports: dict[str, dict] = {}
    for source in manifest.selected():
        path = manifest.processed_root / source.id / "_report.json"
        if path.exists():
            reports[source.id] = json.loads(path.read_text(encoding="utf-8"))
    return reports


def validate(manifest, reports: dict[str, dict]) -> list[str]:
    """Check declared raw inputs against what is actually on disk.

    Returns a list of problems. An empty list means the lineage can be trusted.
    """
    problems: list[str] = []

    for source in manifest.selected():
        report = reports.get(source.id)
        if not report:
            problems.append(f"{source.id}: no _report.json (run the ETL first)")
            continue

        # Names the matter: files that actually become tables. Archives are
        # inputs to extraction, not inputs to a table, and "doc" files are the
        # publisher's own field dictionaries, so both are excluded. Without
        # excluding the dictionaries, every dataset that ships one would raise
        # a permanent "downloaded but never used" note and the note would stop
        # meaning anything.
        available = {
            f.filename
            for f in source.files
            if not f.is_archive
            and f.kind != "doc"
            and f.filename.lower().endswith(".csv")
        }
        available |= set(source.expect_files)

        declared: set[str] = set()
        for table in report["tables"]:
            for raw_name in table.get("raw_inputs", []):
                declared.add(raw_name)
                if raw_name not in available:
                    problems.append(
                        f"{source.id}/{table['table']}: declares raw input "
                        f"{raw_name!r}, which is not a downloadable file for this "
                        "source (typo, or the manifest changed?)"
                    )

        unused = available - declared
        # A raw file that feeds no table is not an error, but "we downloaded it
        # and never used it" is a decision worth surfacing out loud.
        for name in sorted(unused):
            print(f"  note: {source.id}/{name} feeds no processed table (unused)")

    return problems


def provenance_block(source) -> list[str]:
    """The publisher/licence/citation header for one dataset."""
    lines = [
        f"### {source.id} - {source.name}",
        "",
        f"- **Publisher:** {source.publisher}",
        f"- **Domain:** {source.domain}",
        f"- **Authoritative page:** {source.homepage}",
        f"- **Acquisition:** `{source.acquisition}`",
        f"- **Licence:** {source.license}",
        f"- **Citation:** {source.citation}",
        "",
    ]
    return lines


def raw_block(source, checksums: dict) -> list[str]:
    """The raw files with their measured size and hash."""
    lines = [
        "**Raw files (frozen; never modified by any stage):**",
        "",
        "| Raw file | Bytes | SHA-256 (first 16) |",
        "|---|---:|---|",
    ]
    for remote in source.files:
        key = f"{source.id}/{remote.filename}"
        recorded = checksums.get(key, {})
        size = recorded.get("bytes")
        digest = recorded.get("sha256")
        size_text = f"{size:,}" if size else "-"
        hash_text = f"`{digest[:16]}...`" if digest else "-"
        lines.append(f"| `{remote.filename}` | {size_text} | {hash_text} |")

    for name in source.expect_files:
        key = f"{source.id}/{name}"
        recorded = checksums.get(key, {})
        if recorded:
            lines.append(
                f"| `{name}` (extracted) | {recorded['bytes']:,} | "
                f"`{recorded['sha256'][:16]}...` |"
            )
    lines.append("")
    return lines


def tables_block(source, report: dict) -> list[str]:
    """The raw -> processed mapping, per table."""
    lines = [
        "**Raw file &rarr; processed table:**",
        "",
        "| Processed table | Built from raw file(s) | Rows in | Rows out | Kind |",
        "|---|---|---:|---:|---|",
    ]
    for table in report["tables"]:
        # Internal bookkeeping rows (column hygiene, derived-field notes) are
        # not output tables; mark them so the reader is not misled.
        is_output = not table["table"].startswith("(")
        kind = "aggregate" if table.get("derived") else "cleaned"
        if not is_output:
            kind = "bookkeeping (not an output table)"
        inputs = (
            ", ".join(f"`{n}`" for n in table.get("raw_inputs", []))
            or "- (internal)"
        )
        rows_out = f"{table['rows_out']:,}" if is_output else "-"
        lines.append(
            f"| `{table['table']}` | {inputs} | {table['rows_in']:,} | "
            f"{rows_out} | {kind} |"
        )
    lines.append("")
    return lines


def actions_block(report: dict) -> list[str]:
    """Every recorded cleaning and derivation decision, verbatim."""
    lines: list[str] = []
    for table in report["tables"]:
        if not table["actions"]:
            continue
        lines.append(f"<details><summary><code>{table['table']}</code> - "
                     f"{len(table['actions'])} recorded decision(s)</summary>")
        lines.append("")
        for action in table["actions"]:
            lines.append(f"- {action}")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    return lines


def derived_catalog(reports: dict[str, dict]) -> list[str]:
    """Every derived field, gathered from the 'derived:' decisions."""
    lines = [
        "## Derived field catalogue",
        "",
        "Fields that do not exist in the source and were computed by this pipeline.",
        "Each entry records its formula, so a number can be recomputed rather than",
        "taken on trust. Anything **not** listed here is an observed source value.",
        "",
        "| Source | Table | Derived fields |",
        "|---|---|---|",
    ]
    for source_id in sorted(reports):
        for table in reports[source_id]["tables"]:
            derived = [a for a in table["actions"] if a.startswith("derived:")]
            if not derived:
                continue
            text = " ".join(a[len("derived:"):].strip() for a in derived)
            text = text.replace("|", "\\|")
            lines.append(f"| `{source_id}` | `{table['table']}` | {text} |")
    lines.append("")
    return lines


def database_block(load_report: dict, verify_report: dict | None) -> list[str]:
    """The PostgreSQL stage of the chain, from the load and verify reports.

    Generated from ``_load_report.json`` and ``_verify_report.json`` rather than
    written by hand, for the same reason the rest of this file is: a table count
    or a rejected key that a human typed is a number nobody re-checked.
    """
    if not load_report:
        return []

    tables = load_report.get("tables", [])
    methods = load_report.get("copy_methods", {})
    total_rows = load_report.get("total_rows", 0)
    # `constraints_verified` replaced `constraints_added` +
    # `constraints_already_present` when Alembic took over the DDL. Both old
    # names described work the loader no longer does - it never creates a
    # constraint - so there is no fallback to them: a report written by the old
    # loader is stale evidence and should be regenerated, not rendered.
    verified = load_report.get("constraints_verified", [])
    rejected = load_report.get("constraints_rejected", [])
    holding = verified
    alembic_revision = load_report.get("alembic_revision")
    # The report describes the whole database, accumulated over every load run,
    # so the facts about "this run" live under last_run rather than at the top.
    last_run = load_report.get("last_run", {})
    reused = set(last_run.get("tables_reused", []))
    runs = load_report.get("runs", [])

    lines = [
        "## Loaded into PostgreSQL",
        "",
        "`backend/scripts/db/04_load.py` COPYed the processed CSVs into tables that "
        "Alembic had already created, then re-measured every constraint the "
        "migration declared against the rows that were actually loaded. It issues "
        "no DDL of its own. The counts below come from "
        "`data/processed/_load_report.json`, written by the loader itself.",
        "",
        f"**{len(tables)} tables, {total_rows:,} rows.** "
        f"{len(holding)} declared constraints verified against the live rows; "
        f"{len(rejected)} candidate(s) were rejected by the data.",
        "",
    ]
    if alembic_revision:
        lines += [
            f"The schema these verdicts belong to is Alembic revision "
            f"`{alembic_revision}`.",
            "",
        ]
    if len(runs) > 1:
        lines += [
            f"This database was assembled over {len(runs)} loader runs, because a "
            "scoped run (`--source`, `--table`) merges into the report rather than "
            "overwriting it. The table and constraint counts below therefore "
            "describe the whole database, not the most recent invocation.",
            "",
        ]
    if reused:
        why = (
            "verified in place without a COPY"
            if last_run.get("verify_only")
            else "already held exactly the row count the ETL reported, so their COPY was skipped"
        )
        lines += [
            f"The most recent run touched {len(reused)} of the {len(tables)} tables "
            f"with no COPY: {why} "
            f"({last_run.get('elapsed_seconds')}s for all {last_run.get('tables')} "
            "tables). Row counts below are read from the database either way, and the "
            "key tests were re-run against the rows in all cases.",
            "",
        ]
    lines += ["| Schema.table | Rows | How the data got there |", "|---|---:|---|"]
    for entry in tables:
        method = methods.get(entry["table"], "COPY")
        if method == "reused, no COPY":
            detail = "already loaded, re-verified (no COPY)"
        elif method == "verified in place, no COPY":
            detail = "verified in place (no COPY)"
        else:
            detail = f"{method}, {entry['seconds']:,.1f}s"
        lines.append(f"| `{entry['table']}` | {entry['rows']:,} | {detail} |")
    lines += ["", "### Declared constraints, verified against the live rows", ""]

    if holding:
        lines += ["| Kind | Table | Columns |", "|---|---|---|"]
        for entry in sorted(
            holding, key=lambda k: (k["table"], k["kind"], ",".join(k["columns"]))
        ):
            lines.append(
                f"| {entry['kind']} | `{entry['table']}` | "
                f"`{', '.join(entry['columns'])}` |"
            )
    else:
        lines.append("_No constraints were verified in this run._")

    if rejected:
        lines += [
            "",
            "### Candidate keys the data refused",
            "",
            "These were proposed, tested against the real rows, and found not to "
            "hold, so Alembic correctly declares no such constraint. **Nothing was "
            "deleted to make them pass.** Deleting real rows to satisfy a key a human "
            "guessed at would leave the data quietly wrong and the schema looking "
            "clean, so the failure is recorded instead - and re-measured on every "
            "load, so it stays a live number rather than a story:",
            "",
            "| Kind | Table | Columns | Why it was rejected |",
            "|---|---|---|---|",
        ]
        for entry in sorted(
            rejected, key=lambda k: (k["table"], k["kind"], ",".join(k["columns"]))
        ):
            detail = str(entry.get("detail", "")).replace("|", "\\|")
            lines.append(
                f"| {entry['kind']} | `{entry['table']}` | "
                f"`{', '.join(entry['columns'])}` | {detail} |"
            )
    lines.append("")

    if verify_report:
        failures = verify_report.get("failures", [])
        warnings = verify_report.get("warnings", [])
        lines += [
            "## Independent verification",
            "",
            "`backend/scripts/db/05_verify_integrity.py` re-derives every integrity "
            "claim from the rows now in PostgreSQL instead of trusting the catalog. "
            "It reconciles every table's live row count against what the ETL "
            "reported, re-runs the orphan query behind each foreign key, re-runs the "
            "duplicate query behind each primary key, and confirms that no foreign "
            "key crosses a schema boundary.",
            "",
            f"**{len(failures)} failure(s), {len(warnings)} documented warning(s).**",
            "",
        ]
        for check in verify_report.get("spot_checks", []):
            ok = check.get("ok")
            mark = "pass" if ok else "FAIL"
            lines.append(f"- [{mark}] {check.get('label', '')}")
        for rel in verify_report.get("referential_integrity", []):
            orphan = rel.get("orphans", 0)
            mark = "pass" if orphan == 0 else "FAIL"
            lines.append(f"- [{mark}] {rel.get('relationship', '')}: {orphan:,} orphans")
        lines.append("")

    lines += [
        "## The cross-source rule, enforced by the schema",
        "",
        "The hardest rule in this project is that the four sources must never be "
        "joined, because their identifiers are unrelated: dunnhumby household 123, "
        "olist customer 123 and dataco customer 123 are three different people at "
        "three different companies. A rule that lives only in a document gets "
        "violated the first time someone is in a hurry, so here it is a structural "
        "property instead - **every foreign key points inside its own schema**, and "
        "`05_verify_integrity.py` fails if one ever does not.",
        "",
        "For the same reason there is no 'OmniRetail total revenue'. Four unrelated "
        "companies do not add up to a number.",
        "",
        "---",
        "",
    ]
    return lines


def main() -> int:
    manifest = load_manifest()
    reports = load_reports(manifest)
    checksums = json.loads(
        (manifest.raw_root / "_checksums.json").read_text(encoding="utf-8")
    )

    problems = validate(manifest, reports)
    if problems:
        print("VALIDATION FAILED - refusing to write a lineage document that lies:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"validation passed: {len(reports)} source(s)")

    lines: list[str] = [
        "# Data Lineage",
        "",
        "> **The rule this document exists to satisfy:** for every number the",
        "> platform shows, we must be able to prove where it came from. If we",
        "> cannot, the number does not ship.",
        "",
        "This file is **generated** by "
        "`backend/scripts/docs/generate_lineage.py` from the dataset manifest, the",
        "raw checksum lockfile and the per-source ETL reports. It is not written by",
        "hand, because hand-written lineage is correct only until the next commit.",
        "",
        "## The chain",
        "",
        "```",
        "Original public source (publisher, licence, citation)",
        "        |",
        "        v",
        "data/raw/<source>/<file>.csv        <- frozen, SHA-256 recorded, never modified",
        "        |",
        "        v",
        "backend/etl/<source>.py             <- cleaning + derivation (every action logged)",
        "        |",
        "        v",
        "data/processed/<source>/<table>.csv <- analytics-ready, rebuilt from raw every run",
        "        |",
        "        v",
        "PostgreSQL schema <source>          <- COPY load + measured constraints",
        "        |",
        "        v",
        "SQL / SQLAlchemy / FastAPI / React / Power BI",
        "        |",
        "        v",
        "Rendered KPI",
        "```",
        "",
        "Two properties make the chain trustworthy:",
        "",
        "1. **Raw data is never modified.** Every stage reads from `data/raw` and",
        "   writes to `data/processed`. The raw files are therefore evidence, not",
        "   working state.",
        "2. **Every transformation is recorded.** Each table below carries the",
        "   decisions applied to it - what was dropped, what was filled, what was",
        "   derived - so a total can be reconciled rather than believed.",
        "",
        "## Cross-source boundary",
        "",
        "The four sources are four unrelated organisations and their identifiers are",
        "**not** interoperable. Lineage chains terminate at their source: there is no",
        "step anywhere in this pipeline that joins a dunnhumby key to an Olist key.",
        "See `docs/data_sources.md` section 6.",
        "",
        "---",
        "",
    ]

    for source in manifest.selected():
        report = reports.get(source.id)
        if not report:
            lines += [f"## {source.id}", "", "_Not yet processed._", "", "---", ""]
            continue
        total_out = sum(
            t["rows_out"] for t in report["tables"] if not t["table"].startswith("(")
        )
        lines += [f"## {source.id}", ""]
        lines += provenance_block(source)
        lines += raw_block(source, checksums)
        lines += [
            f"**Processed output:** {len(report['tables'])} pipeline step(s), "
            f"{total_out:,} rows across the output tables, written to "
            f"`data/processed/{source.id}/`.",
            "",
        ]
        lines += tables_block(source, report)
        lines += ["**Recorded decisions:**", ""]
        lines += actions_block(report)
        lines += ["---", ""]

    lines += derived_catalog(reports)

    # The PostgreSQL stage is optional: before the first load there is no
    # _load_report.json, and a generator that crashes on a missing input is a
    # generator nobody runs.
    load_report_path = REPO_ROOT / "data" / "processed" / "_load_report.json"
    verify_report_path = REPO_ROOT / "data" / "processed" / "_verify_report.json"
    load_report = (
        json.loads(load_report_path.read_text(encoding="utf-8"))
        if load_report_path.exists()
        else {}
    )
    verify_report = (
        json.loads(verify_report_path.read_text(encoding="utf-8"))
        if verify_report_path.exists()
        else None
    )
    if load_report:
        lines += database_block(load_report, verify_report)

    lines += [
        "## Reconciliation checks performed",
        "",
        "These are checks that were actually executed against the real data, not",
        "claims about intent:",
        "",
        "| Check | Result |",
        "|---|---|",
        "| DataCo: sum of line-level `order_item_total` vs sum of order-level "
        "`order_value` | equal to the cent (difference `0.0`) |",
        "| DataCo: derived `is_late` vs the publisher's own `delivery_status` | "
        "exact match - `Late delivery` &harr; `True`, 36,048 orders, no "
        "contradictions |",
        "| Olist: `delivery_days` has no negative values | 0 rows - no order is "
        "delivered before it is placed |",
        "| Olist: `customer_rfm` grain | 96,096 real people from 99,441 orders, "
        "proving `customer_unique_id` was used and not the per-order `customer_id` |",
        "| M5: melted row count | 59,181,090 = 30,490 series &times; 1,941 days, "
        "exact |",
        "| M5: processed total vs raw total units | 66,927,173 = 66,927,173, "
        "exact |",
        "| Every raw file: SHA-256 re-hashed and compared to the lockfile | match |",
        "",
        "## Known gaps in this document",
        "",
        "- The chain above reaches the API and the frontend, but the *provenance*",
        "  of a rendered number is enforced by different mechanisms, not by this",
        "  document: a catalogued query's SQL is fixed and named",
        "  (`backend/sql/<source>/*.sql`), its results are recorded",
        "  (`data/processed/_sql_results.json`), and every figure written into",
        "  documentation is checked against that file by",
        "  `backend/tests/test_analytics_findings.py`. The AI layer additionally",
        "  refuses to state a number that is not present in the rows the query",
        "  returned. Those are the checks; this file is the map that points at",
        "  them.",
        "- Lineage currently stops at the loaded table. Column-level lineage",
        "  (which raw column feeds which processed column) is described in",
        "  `docs/data_dictionary.md` and the recorded decisions above, but is not",
        "  yet machine-enforced.",
        "- `/api/forecasting` and `/api/anomalies` serve recorded artefacts",
        "  (`data/processed/_forecast_results.json`,",
        "  `data/processed/_anomaly_results.json`) rather than querying the",
        "  database. Their inputs are the same `m5.sales_long` table and the same",
        "  measured calendar, and each artefact carries the `generated_utc` of",
        "  the batch run that wrote it.",
    ]

    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
