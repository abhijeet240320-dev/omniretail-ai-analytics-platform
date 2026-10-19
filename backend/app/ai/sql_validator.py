"""Static validation of SQL before it reaches the database.

WHY A VALIDATOR WHEN THE ROLE IS ALREADY READ-ONLY
--------------------------------------------------
Three reasons, and the third is the one that matters:

1. **Clarity.** `permission denied for table orders` tells a user nothing.
   "REJECTED: this statement writes to the database, and the analyst is
   read-only" tells them what happened and why.

2. **Granularity.** The role is enforced at *execution*. A rejected statement
   has already been parsed and planned. This rejects it before PostgreSQL ever
   sees it, which means a malicious statement cannot burn planner time on a
   59-million-row table.

3. **The role does not catch everything.** `default_transaction_read_only`
   refuses writes inside a transaction. It does NOT refuse
   `pg_read_file('/etc/passwd')`, `pg_sleep(600)`, `COPY ... FROM PROGRAM`,
   or a `SELECT` that joins two source schemas. Those are all perfectly
   legal SELECTs, and the read-only role executes every one of them happily.
   Only a validator stops them.

WHAT IT REJECTS
----------------
  * the thirteen statements in `REQUIRED_CONTROLS["sql_validator"]`
  * more than one statement in a submitted string
  * anything that is not a SELECT or a WITH
  * functions with filesystem, network or timing side effects
  * a query touching two different source schemas (`cross_source_guard`)
  * a query reading `analytics` without a `source_system` predicate

WHY COMMENTS AND STRING LITERALS ARE STRIPPED FIRST
----------------------------------------------------
A naive `if "delete" in sql.lower()` rejects `SELECT * FROM orders WHERE
note = 'please delete this'`. That is a false positive, and a validator that
cries `insert into logs` on legitimate text trains people to route around it.

So the scanner strips `--` line comments, `/* */` block comments and `'...'`
literals - replacing each with a same-length run of spaces, so reported offsets
still point at the original text - and only then looks for keywords.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# The thirteen statements named in
# backend/tests/test_ai_grounding_contract.py::REQUIRED_CONTROLS.
# This tuple is deliberately the same list, and the test asserts the two agree,
# so a keyword cannot be quietly dropped from one and kept in the other.
FORBIDDEN_KEYWORDS: tuple[str, ...] = (
    "insert", "update", "delete", "drop", "alter", "truncate",
    "create", "grant", "revoke", "merge", "copy", "call", "do",
)

# Schemas that hold independently-sourced business data. A statement naming two
# of them is making a claim the data cannot support.
SOURCE_SCHEMAS: tuple[str, ...] = ("olist", "dataco", "dunnhumby", "m5")

# The only schema where the four sources legitimately meet. Every row there
# carries `source_system`, so a query against it must say which source it wants.
ANALYTICS_SCHEMA = "analytics"

# Functions that read the filesystem, open a socket, or burn time. None are
# needed for analytics; all are reachable by any SELECT-holding role.
FORBIDDEN_FUNCTIONS: tuple[str, ...] = (
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "lo_import", "lo_export", "dblink", "dblink_connect", "pg_terminate_backend",
    "pg_cancel_backend", "set_config", "pg_reload_conf", "pg_rotate_logfile",
    "set_config", "current_setting", "nextval", "setval", "currval",
)

# Clauses that write a file from the server. None of them are PostgreSQL syntax
# - `INTO OUTFILE`/`INTO DUMPFILE` are MySQL and `LOAD DATA INFILE` is MariaDB -
# so PostgreSQL rejects them itself with a SyntaxError before any permission is
# consulted. They are refused here anyway for two reasons: the validator is the
# thing a future LLM-generated SQL has to pass, and a statement shaped like that
# should be reported as an exfiltration attempt rather than a syntax error. This
# is defence in depth, not the barrier; the read-only role is the barrier.
FILE_EXPORT_CLAUSES: tuple[str, ...] = (
    "into outfile", "into dumpfile", "load data infile",
    "load data local infile", "into @", "select ... into outfile",
)

# A word boundary, so `updated_at` does not match `update`.
_WORD = r"[A-Za-z_][A-Za-z0-9_$]*"

_STMT_SPLIT = re.compile(r";")
_WORD_RE = re.compile(_WORD)


def _blank(match: re.Match[str]) -> str:
    """Replace a matched span with spaces, preserving offsets."""
    return " " * (match.end() - match.start())


_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# Dollar-quoted strings ($tag$ ... $tag$) are matched before plain literals:
# a function body inside one can contain anything at all.
_DOLLAR_QUOTED = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$[\s\S]*?\$\1?\$")
_STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
_QUOTED_IDENT = re.compile(r'"(?:""|[^"])*"')


def strip_comments(sql: str) -> str:
    """Blank out comments only, keeping string literals intact.

    Needed separately from `strip_literals` because the multi-statement check
    has to treat a `;` inside a string as DATA and a `;` inside a comment as
    TEXT. Using `strip_literals` for that check would reject
    `SELECT 'a;b'`, and using this function for the keyword check would accept
    `SELECT 1 -- drop everything`.
    """
    text = sql
    for pattern in (_DOLLAR_QUOTED, _BLOCK_COMMENT, _LINE_COMMENT):
        text = pattern.sub(_blank, text)
    return text


def strip_literals(sql: str) -> str:
    """Blank out comments and literals, keeping every character offset intact.

    Offsets matter: a rejection message that points at column 41 of the
    submitted text is debuggable, and one that points at column 41 of a
    mangled copy is not.
    """
    text = strip_comments(sql)
    return _STRING_LITERAL.sub(_blank, _QUOTED_IDENT.sub(_blank, text))


def _words(text: str) -> list[str]:
    """Identifier-like tokens, lowercased, in order."""
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


@dataclass
class Violation:
    """One reason a statement was refused."""

    rule: str
    message: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"rule": self.rule, "message": self.message, "detail": self.detail}


@dataclass
class ValidationResult:
    """The verdict, plus everything needed to explain it."""

    ok: bool
    sql: str
    violations: list[Violation] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    source_schemas: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "tables": self.tables,
            "source_schemas": self.source_schemas,
            "violations": [v.as_dict() for v in self.violations],
        }

    def explain(self) -> str:
        """A refusal a user can act on, one reason per line."""
        return "\n".join(f"- [{v.rule}] {v.message}" for v in self.violations)


def referenced_tables(sql: str) -> list[str]:
    """Every `schema.table` the statement names, in order, deduplicated.

    Reads FROM, JOIN, INTO, UPDATE, DELETE FROM and TABLE. It does not attempt
    to be a SQL parser - it does not need to be, because anything it misses is
    caught by the other rules and, ultimately, by the read-only role.
    """
    text = strip_literals(sql)
    found: list[str] = []
    pattern = re.compile(
        r"\b(?:from|join|into|update|table)\s+"
        r"(?:only\s+)?"
        r"([A-Za-z_][A-Za-z0-9_$]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_$]*)",
        re.IGNORECASE,
    )
    for schema, table in pattern.findall(text):
        qualified = f"{schema.lower()}.{table.lower()}"
        if qualified not in found:
            found.append(qualified)

    # Comma-separated FROM lists: `FROM olist.customers, dunnhumby.households`.
    #
    # The keyword pattern above only fires after FROM/JOIN/INTO/UPDATE/TABLE, so
    # the SECOND table in a comma list was invisible and the cross-source guard
    # passed a query that joins two datasets. This is the single largest gap in
    # a text-based table extractor, and it was found by
    # `test_generated_sql_joining_two_sources_is_rejected`.
    #
    # Scoped to the tail of a FROM clause: match `, <schema>.<table>` only while
    # still inside the FROM list. A comma inside a function call or a SELECT
    # list is not a table reference, so the scan stops at the first closing
    # parenthesis or at the next clause keyword.
    from_clause = re.search(
        r"\bfrom\s+(?:only\s+)?(.*?)(?:\bwhere\b|\bgroup\b|\border\b|\bhaving\b"
        r"|\blimit\b|\boffset\b|\bunion\b|\bjoin\b|;|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if from_clause:
        for schema, table in re.findall(
            r"(?:^|,)\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_$]*)",
            from_clause.group(1),
        ):
            qualified = f"{schema.lower()}.{table.lower()}"
            if qualified not in found:
                found.append(qualified)

    return found


def validate(sql: str) -> ValidationResult:
    """Validate a statement. Returns every violation found, not just the first.

    Reporting all of them matters: a user who writes a cross-source join and a
    write in the same attempt should be told both, not fixed one and re-submitted
    into the other.
    """
    violations: list[Violation] = []

    if not sql or not sql.strip():
        return ValidationResult(False, sql, [Violation(
            "empty_statement", "the statement is empty",
        )])

    # -- 1. exactly one statement --------------------------------------------
    # Checked on `strip_literals`, which blanks comments AND string literals.
    # Both are necessary and each covers a case the other cannot:
    #   - a `;` inside a `--` comment is text; checking the raw string flagged
    #     8 of the 41 catalogued queries, because their leading headers contain
    #     semicolons.
    #   - a `;` inside a string literal is DATA (`SELECT 'a;b'` is one
    #     statement), so a comment-only strip would reject valid SQL.
    body = strip_literals(sql.strip()).rstrip(";").rstrip()
    if _STMT_SPLIT.search(body) is not None:
        violations.append(Violation(
            "multiple_statements",
            "more than one statement was submitted; the analyst executes exactly "
            "one read-only statement per request",
            "A trailing semicolon is allowed; anything else is a second statement.",
        ))

    # -- 2. forbidden keywords ------------------------------------------------
    stripped = strip_literals(body)
    lowered = stripped.lower()
    found_keywords: list[str] = []
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", lowered):
            found_keywords.append(keyword)
    if found_keywords:
        violations.append(Violation(
            "write_statement",
            f"this statement modifies data or schema ({', '.join(found_keywords)}); "
            f"the analyst is read-only",
            f"Rejected keywords: {', '.join(found_keywords)}. These are the same "
            f"statements PostgreSQL refused for omniretail_readonly, recorded in "
            f"data/processed/_audit_report.json.",
        ))

    # -- 2b. file-export clauses ---------------------------------------------
    found_clauses = [c for c in FILE_EXPORT_CLAUSES if c in lowered]
    if found_clauses:
        violations.append(Violation(
            "file_export",
            f"this statement tries to write the result set to a file on the "
            f"server ({', '.join(found_clauses)})",
            "Reading four sources of real company data out of the database and "
            "onto disk is the one thing the analyst must never be asked to do. "
            "The result rows are already returned to you through the API.",
        ))

    # -- 2c. SELECT ... INTO <table> ----------------------------------------
    # `SELECT * INTO copy FROM olist.orders` is PostgreSQL's CREATE TABLE AS.
    # It starts with SELECT, so it walks straight past every keyword above and
    # past the "must be a read" check. omniretail_readonly would refuse it at
    # execution, but the validator is the gate an LLM-generated statement has to
    # clear first, and a write that is refused late is a write that was offered.
    select_into = re.search(
        rf"\binto\s+(?!@|outfile\b|dumpfile\b)\S", stripped, re.IGNORECASE
    )
    if select_into:
        violations.append(Violation(
            "select_into_table",
            f"this SELECT creates a table: {stripped[select_into.start():select_into.start() + 40].strip()!r}",
            "PostgreSQL reads `SELECT ... INTO <name>` as CREATE TABLE AS. It is "
            "a write wearing a SELECT's clothes, and it is the one write the "
            "keyword list cannot see.",
        ))

    # -- 3. must actually be a read ------------------------------------------
    head = stripped.lstrip().lstrip("(").lstrip().lower()
    if head and not (head.startswith("select") or head.startswith("with")):
        violations.append(Violation(
            "not_a_read",
            f"a read-only analyst only accepts SELECT or WITH; this begins {head[:24]!r}",
        ))

    # -- 4. dangerous functions ----------------------------------------------
    found_functions = [f for f in FORBIDDEN_FUNCTIONS if f in _words(stripped)]
    if found_functions:
        violations.append(Violation(
            "forbidden_function",
            f"these functions have filesystem, network or timing side effects: "
            f"{', '.join(sorted(set(found_functions)))}",
            "The read-only role permits them - they are legal SELECTs - so only "
            "this validator refuses them.",
        ))

    # -- 5. cross-source guard -----------------------------------------------
    tables = referenced_tables(body)
    schemas = sorted({t.split(".", 1)[0] for t in tables})
    touched = [s for s in schemas if s in SOURCE_SCHEMAS]

    if len(touched) > 1:
        violations.append(Violation(
            "cross_source_join",
            f"this query joins {', '.join(touched)}, which are four unrelated "
            f"companies with no shared key space",
            "A join between them asserts an identity that does not exist. Query "
            "one source at a time. No foreign key in this database crosses a "
            "schema boundary, which is why this has to be a rule about SQL.",
        ))

    if ANALYTICS_SCHEMA in schemas:
        if not re.search(r"\bsource_system\b", stripped, re.IGNORECASE):
            violations.append(Violation(
                "analytics_without_source",
                f"a query against the {ANALYTICS_SCHEMA} schema must carry a "
                f"source_system predicate",
                f"Every row there mixes four sources; without the predicate the "
                f"result cannot be attributed to any of them.",
            ))

    return ValidationResult(
        ok=not violations,
        sql=sql,
        violations=violations,
        tables=tables,
        source_schemas=touched,
    )


def explain(sql: str) -> str:
    """Validate and return the human-readable refusal, or "" if it passes."""
    result = validate(sql)
    return "" if result.ok else result.explain()