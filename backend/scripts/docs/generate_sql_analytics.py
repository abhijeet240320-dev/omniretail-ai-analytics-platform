"""Generate docs/sql_analytics.md from the executed SQL and the written findings.

USAGE (from the repository root)
--------------------------------
    .venv/Scripts/python.exe backend/scripts/docs/generate_sql_analytics.py

WHY THE DOCUMENT IS GENERATED
-----------------------------
`backend/scripts/sql/run_analytics.py` executes every query in
`backend/sql/<source>/*.sql` and records the real rows to
`data/processed/_sql_results.json`. This script turns those rows, plus the
written interpretation in `backend/config/sql_findings.yaml`, into the document.

The interpretation has to be written by a person; the numbers must not be. So
this script enforces the split: every figure quoted in the findings prose is
checked against the actual result rows for that query, and if a number cannot be
found in the data it refuses to write the file and tells you which one. A
finding that cites 16,008,872.12 has to be citing a cell that came back from
PostgreSQL.

Usage (from the repository root):

    .venv/Scripts/python.exe backend/scripts/sql/run_analytics.py
    .venv/Scripts/python.exe backend/scripts/docs/generate_sql_analytics.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS = REPO_ROOT / "data" / "processed" / "_sql_results.json"
FINDINGS = REPO_ROOT / "backend" / "config" / "sql_findings.yaml"
DESTINATION = REPO_ROOT / "docs" / "sql_analytics.md"

SOURCES = {
    "olist": ("Olist - Brazilian E-Commerce Marketplace", "ECOMMERCE"),
    "dataco": ("DataCo - Supply Chain Dataset", "SUPPLY CHAIN"),
    "dunnhumby": ("Dunn & Bradstreet Complete Journey", "CPG / RETAIL"),
    "m5": ("M5 - Walmart Global Selling Competition", "DEMAND FORECASTING"),
}

# A number in the prose: optional thousands separators, optional decimals.
NUMBER = re.compile(r"\d[\d,]*\.?\d*")


def normalise(value: str) -> str:
    return value.replace(",", "")


def numbers_in_results(query: dict) -> set[str]:
    """Every numeric string that appears anywhere in this query's result set.

    A date cell such as 2017-01 or 2016-05-22 also contributes its parts, so
    prose can refer to the year on its own without the check rejecting a figure
    that genuinely came back from the database.

    A numeric cell carrying more precision than a sentence wants (the worst
    DataCo order is held as -4325.8500) also registers its 2-decimal form, so
    prose may write -4,325.85 for it. That permits rounding an exact value for
    readability; it does not permit quoting a different value.
    """
    found: set[str] = set()
    for row in query.get("rows", []):
        for cell in row:
            if cell is None:
                continue
            value = normalise(str(cell))
            found.add(value)
            parts = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", value)
            if parts:
                found.add(parts.group(1))
                found.add(parts.group(2))
            if re.fullmatch(r"-?\d+\.\d{3,}", value):
                found.add(f"{float(value):.2f}")
                found.add(f"{float(value):.4f}")
    return found


def numbers_in_text(text: str) -> list[str]:
    """Numeric literals in prose, skipping numbers that are part of a name.

    Three things are deliberately NOT treated as measurements:

    * anything inside backticks - band labels the query invented
      (`2. 1-14% off`), column names, schema names;
    * a digit glued to an identifier - the 5 in M5, the 3 in FOODS_3, the 256 in
      SHA-256. These are names, not quantities;
    * the tail of a hyphenated date or range - 2016-05-22 contributes only
      2016, and `1-14` contributes nothing unless it is backticked.

    A digit followed by letters (12th, 3rd) is likewise a word, not a figure.

    A MINUS sign is kept when it is a real sign - preceded by whitespace or at
    the start - so that a genuine negative measurement such as -11.18 is checked.
    A hyphen inside a word is not a sign, so 1-14 contributes nothing.
    """
    without_code = re.sub(r"`[^`]*`", " ", text)
    literals: list[str] = []
    for match in NUMBER.finditer(without_code):
        start, end = match.span()
        before = without_code[start - 1] if start else ""
        after = without_code[end] if end < len(without_code) else ""
        if after.isalpha() or after == "_":
            continue
        if before.isalnum() or before == "_":
            continue
        sign = ""
        if before == "-":
            char_before_sign = without_code[start - 2] if start >= 2 else " "
            if not char_before_sign.isspace():
                continue  # a hyphen inside a word or a date, not a sign
            sign = "-"
        literal = (sign + match.group(0)).rstrip(".,;:")
        if literal.strip("-."):
            literals.append(literal)
    return literals


def check_findings(results: list[dict], findings: dict) -> list[str]:
    """Return a list of prose numbers that do not appear in their query's rows."""
    problems: list[str] = []
    by_key = {f"{q['source']}.{q['name']}": q for q in results}
    for key, text in findings.items():
        query = by_key.get(key)
        if query is None:
            problems.append(f"{key}: has findings but no executed query result")
            continue
        if "error" in query:
            problems.append(f"{key}: query FAILED, findings cannot be published")
            continue
        available = numbers_in_results(query)
        for literal in numbers_in_text(text):
            if normalise(literal) not in available:
                problems.append(
                    f"{key}: {literal!r} is quoted in the findings but does not "
                    f"appear in the query's result rows"
                )
    return problems


def render_table(query: dict, limit: int = 14) -> list[str]:
    columns = query.get("columns", [])
    rows = query.get("rows", [])
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    shown = rows[:limit]
    for row in shown:
        cells = ["" if v is None else str(v) for v in row]
        # Tables in Markdown break if a cell contains a pipe.
        cells = [c.replace("|", "\\|") for c in cells]
        lines.append("| " + " | ".join(cells) + " |")
    if len(rows) > limit:
        lines.append(f"| _... {len(rows) - limit} further rows not shown ..._ |"
                     + " |" * (len(columns) - 1))
    return lines


def build(results: list[dict], findings: dict) -> list[str]:
    ok = [q for q in results if "error" not in q]
    failed = [q for q in results if "error" in q]
    by_source: dict[str, list[dict]] = {}
    for query in ok:
        by_source.setdefault(query["source"], []).append(query)

    lines = [
        "# SQL Analytics - what the four sources actually say",
        "",
        "<!-- GENERATED by backend/scripts/docs/generate_sql_analytics.py. DO NOT EDIT. -->",
        "",
        "Every table below was produced by executing the SQL in `backend/sql/` "
        "against the loaded PostgreSQL database. Every figure quoted in the prose "
        "was checked against the result rows of the query it belongs to, and this "
        "file will not be written if a quoted figure is missing from the data.",
        "",
        "```bash",
        ".venv/Scripts/python.exe backend/scripts/sql/run_analytics.py",
        ".venv/Scripts/python.exe backend/scripts/docs/generate_sql_analytics.py",
        "```",
        "",
        f"**{len(ok)} queries across {len(by_source)} sources, "
        f"{len(failed)} failed.** A failing query is excluded from this document "
        "rather than described from memory.",
        "",
    ]

    if failed:
        lines += [
            "## Queries that did not run",
            "",
            "These are recorded here rather than quietly dropped:",
            "",
            "| Query | Error |",
            "|---|---|",
        ]
        for query in failed:
            error = str(query.get("error", "")).replace("|", "\\|").splitlines()[0]
            lines.append(f"| `{query['source']}.{query['name']}` | {error} |")
        lines.append("")

    for source in SOURCES:
        queries = by_source.get(source)
        if not queries:
            continue
        title, domain = SOURCES[source]
        lines += [
            f"## {title}",
            "",
            f"*Domain: {domain}. Source file: "
            f"`{queries[0]['file']}`.*",
            "",
        ]
        for query in sorted(queries, key=lambda q: q["name"]):
            lines += [f"### `{query['name']}`", ""]
            if query.get("question"):
                lines += [query["question"], ""]
            key = f"{query['source']}.{query['name']}"
            text = findings.get(key)
            if text:
                lines += ["**What we found.**", ""]
                lines += [text.strip(), ""]
            elif key not in findings:
                lines += [
                    "_No written finding for this query._", ""
                ]
            if query.get("row_count"):
                lines += [
                    f"_{query['row_count']} row(s), "
                    f"{query.get('elapsed_ms', 0):,.0f} ms._",
                    "",
                ]
            lines += render_table(query)
            lines.append("")
            lines += [
                "<details><summary>SQL</summary>",
                "",
                "```sql",
                query.get("sql", "").strip(),
                "```",
                "",
                "</details>",
                "",
            ]

    return lines


def main() -> int:
    if not RESULTS.exists():
        print(
            f"missing {RESULTS}\n"
            "run backend/scripts/sql/run_analytics.py first"
        )
        return 1
    results = json.loads(RESULTS.read_text(encoding="utf-8"))["queries"]
    findings = yaml.safe_load(FINDINGS.read_text(encoding="utf-8")) or {}

    problems = check_findings(results, findings)
    if problems:
        print(f"REFUSING TO WRITE - {len(problems)} finding(s) do not match the data:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    orphan = [f"{q['source']}.{q['name']}" for q in results
              if f"{q['source']}.{q['name']}" not in findings]
    if orphan:
        print(f"note: {len(orphan)} query(ies) have no written finding yet:")
        for key in sorted(orphan):
            print(f"  - {key}")

    DESTINATION.write_text("\n".join(build(results, findings)), encoding="utf-8")
    print(f"wrote {DESTINATION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())