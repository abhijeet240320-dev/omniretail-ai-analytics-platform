"""The AI Analyst: question in, verified answer or refusal out.

THE PIPELINE
------------
    question
       |
       v
    1. guards.check          false premise?        -> REFUSE with correction
       |
       v
    2. retrieval.evidence    documented?          -> no evidence -> NO FACT
       |
       v
    3. nl_to_sql.translate    translatable?       -> no -> NO FACT, say why
       |
       v
    4. sql_validator.validate is it safe to run?  -> no -> REFUSE with rule
       |
       v
    5. execute                                     real rows from PostgreSQL
       |
       v
    6. verification.verify    every number backed? -> no -> NO FACT, list them
       |
       v
    answer, with citations and the query that produced it

EVERY STAGE CAN END THE ANSWER, AND THAT IS THE POINT
------------------------------------------------------
Six gates, each of which can refuse. A system like this fails if any one gate
can be bypassed, so the design is that bypassing one only reaches the next -
and there is always another. The refusal paths are not error handling bolted on
at the end; they are the main exit from the function.

NO MODEL GENERATES THE PROSE
----------------------------
`g.compose` renders text from the rows and the citations. It does not compose
sentences and then look for numbers in them. That direction of travel is the
one that produces "the total revenue was $47 million" from a table of state
revenues - the sentence comes first and the numbers are filled in afterwards.

The answer is therefore always traceable to a specific column of a specific row
of a specific query, and `verification.verified_answer` re-checks the rendered
prose anyway as a net under the structured check.

THE THING IT WILL NOT DO
-------------------------
Answer a question the corpus and the database cannot answer. Not with a
plausible guess, not with a caveated guess. The correct output is the refusal,
and the refusals in this module are written to be useful: they say what is
missing, what to do instead, and which files hold the related evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ai import guards, llm, nl_to_sql, retrieval
from app.ai import verification as verify
from app.ai.sql_validator import validate


@dataclass
class Answer:
    """The analyst's response. `answered` false means a refusal, not an error."""

    answered: bool
    question: str
    answer: str = ""
    reason: str = ""
    stage: str = ""

    # What ran, and what it returned.
    query: str | None = None
    source: str | None = None
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    total_rows: int = 0
    elapsed_ms: float = 0.0

    # The evidence for the answer, and the proof the answer was checked.
    citations: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)
    corrections: list[dict[str, str]] = field(default_factory=list)
    verified: Any = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    pipeline: list[str] = field(default_factory=list)

    #: Whether a model wrote the prose or the deterministic composer did, and
    #: why. Present on every response so "an LLM answered this" is never an
    #: assumption - it is a field a caller can check.
    llm: dict[str, Any] = field(default_factory=dict)

    @property
    def stopped_at(self) -> str:
        """The stage that refused, under the name the API and tests use.

        The dataclass field is `stage`; the wire format and the control tests
        both say `stopped_at`. Aliasing here rather than in the schema keeps
        one concept with two spellings out of the response body.
        """
        return self.stage

    def as_dict(self) -> dict[str, Any]:
        return {
            "answered": self.answered,
            "question": self.question,
            "answer": self.answer,
            "reason": self.reason,
            "stopped_at": self.stage,
            "query": self.query,
            "source": self.source,
            "sql": self.sql,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "total_rows": self.total_rows,
            "elapsed_ms": self.elapsed_ms,
            "citations": self.citations,
            "evidence": self.evidence,
            "matched_rules": self.matched_rules,
            "corrections": self.corrections,
            # None rather than {} when no verification ran. A refusal at
            # `false_premise` never executes anything, so there is nothing to
            # verify - and `{}` serialises into a response model whose `ok` is
            # required, producing a 500 on the most important path the API has.
            "verified": self.verified.as_dict() if self.verified else None,
            "candidates": self.candidates,
            "pipeline": self.pipeline,
            "llm": self.llm,
        }


def _refuse(question: str, stage: str, reason: str, **extra: Any) -> Answer:
    return Answer(
        answered=False, question=question, reason=reason, stage=stage, **extra
    )


@dataclass
class VerificationMerged:
    """Two verification results presented as one.

    The structural check runs first because it is the one that cannot produce a
    false failure; the textual check runs second as a net. Merging keeps
    `checked` complete in the response, so a reader can see every cell and
    every prose number that was examined rather than a summary of one of them.
    """

    primary: Any
    secondary: Any

    @property
    def ok(self) -> bool:
        return self.primary.ok and self.secondary.ok

    @property
    def failures(self) -> list[dict[str, str]]:
        return [*self.primary.failures, *self.secondary.failures]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": [*self.primary.checked, *self.secondary.checked],
            "failures": self.failures,
            "checks": ["rendered_cells", "prose_numbers"],
        }


def g_format(value: Any) -> str:
    """Render a returned value the way it appears in the JSON, not in prose.

    Deliberately not thousands-separated: the table below the prose already uses
    the raw form, and a prose "1,234" next to a table "1234.0" invites the
    reader to wonder whether they are different numbers.

    The float branch delegates to `verification._fmt` rather than formatting
    independently. Two formatters in two modules WILL drift, and when they do the
    drift is invisible until a verification step fails on a correct query - a
    failure that reads like a data bug and is not one.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return verify.format_number(value)
    return str(value)


def g_render_table(columns: list[str], rows: list[dict[str, Any]]) -> str:
    """A markdown table of the rows that actually came back.

    Rendered from `rows`, not from a claim about the rows. If a column is long
    the table is truncated per cell and says so, because a 4,000-character cell
    makes the whole response unreadable and the reader cannot see the numbers
    that matter.
    """
    if not rows:
        return "_(the query returned no rows)_"
    widths = {
        c: min(48, max(len(c), *(len(g_format(r.get(c))) for r in rows)))
        for c in columns
    }
    head = "| " + " | ".join(c.ljust(widths[c]) for c in columns) + " |"
    rule = "|" + "|".join("-" * (widths[c] + 2) for c in columns) + "|"
    body = []
    for row in rows:
        cells = []
        for c in columns:
            text = g_format(row.get(c))
            if len(text) > widths[c]:
                text = text[: widths[c] - 1] + "..."
            cells.append(text.ljust(widths[c]))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([head, rule, *body])


def g_compose(
    question: str,
    query: nl_to_sql.CataloguedQuery,
    columns: list[str],
    rows: list[dict[str, Any]],
    total_rows: int,
    evidence: list[dict[str, Any]],
    elapsed_ms: float,
    notes: list[str] | None = None,
) -> str:
    """Build the answer text from the rows and nothing else.

    Every sentence here is either a label copied from the catalogue metadata, a
    value copied from a returned cell, or a count of the rows returned. No
    inference, no arithmetic, no "which suggests".
    """
    parts: list[str] = []

    parts.append(
        f"Answered by **{query.qualified_name}** against `{query.source}`.\n\n"
        f"> {query.question}\n"
    )

    if rows:
        head = rows[0]
        # The first row is described column by column, because "the top result
        # is SP" is only meaningful if the reader can see WHICH column said SP.
        described = ", ".join(
            f"**{c}** = {g_format(head.get(c))}" for c in columns
        )
        parts.append(f"The first of the {len(rows)} returned row(s): {described}.")
        parts.append("")
        parts.append(g_render_table(columns, rows))

        if total_rows > len(rows):
            parts.append("")
            parts.append(
                f"Showing {len(rows)} of {total_rows} rows. "
                f"`GET /api/analytics/queries/{query.name}` pages through them."
            )
    else:
        parts.append(
            "The query executed successfully and returned no rows. That is a "
            "real result about this data, not an error - it means the "
            "combination asked for does not occur."
        )

    if evidence:
        lines = "\n".join(
            f"- `{hit['citation']}` -- {hit['section']} "
            f"(score {hit['score']:.2f}, matched: {', '.join(hit['matched_terms'][:5])})"
            for hit in evidence[:3]
        )
        parts.append(
            "\n\n**Supporting documents**\n\n"
            "Retrieved from the committed corpus; each is openable and "
            "checkable:\n\n" + lines
        )

    parts.append(
        f"\n\n_Executed in {elapsed_ms:.0f} ms on the read-only role "
        f"`omniretail_readonly`, which holds SELECT and nothing else._"
    )

    # Corrections go FIRST, not last. A reader who takes the numbers and skips
    # the footer would be misled; a reader who sees the correction first cannot.
    if notes:
        parts.insert(1, "\n\n".join(notes))
    return "\n".join(parts)


def g_analyse(
    question: str,
    limit: int = 20,
    require_evidence: bool = True,
) -> Answer:
    """Run the full pipeline. Never raises; always returns an `Answer`."""
    question = (question or "").strip()
    pipeline: list[str] = []

    if not question:
        return _refuse(question, "input", "the question is empty")

    # -- 1. false premise ----------------------------------------------------
    pipeline.append("guards")
    guard = guards.check(question)
    named_query = nl_to_sql._exact_name(question)  # noqa: SLF001 - same package
    if guard.triggered and not named_query:
        # Every matching rule is reported. Correcting one trap while leaving
        # another standing is how a false premise survives the encounter.
        return _refuse(
            question, "false_premise",
            "the question rests on something that is not true of this data",
            matched_rules=guard.matched,
            corrections=guard.corrections,
            answer=guard.render(),
            pipeline=pipeline,
        )
    if guard.triggered:
        pipeline.append(f"guards:corrected-{','.join(guard.matched)}")
    else:
        pipeline.append("guards:clear")

    # -- 2. evidence ---------------------------------------------------------
    # `require_evidence` is a switch, not a default, because it is the control
    # that makes a question about a DOCUMENT (rather than the database) still
    # answerable: "what does the data dictionary say about discount signs" has
    # no SQL behind it and needs none, but it does need a document.
    pipeline.append("retrieval")
    found = retrieval.evidence_for(question, limit=4)
    evidence = found["hits"]
    if require_evidence and not evidence:
        pipeline.append("retrieval:no-evidence")
        return _refuse(
            question, "evidence",
            (
                "nothing in the verified corpus supports an answer to this, so "
                "there is no factual answer to give. The corpus is the project's "
                "generated documentation, the verified analytics catalogue, the "
                "pipeline configuration and the recorded load, verification and "
                "audit reports. A question none of it covers gets no number."
            ),
        )
    pipeline.append(f"retrieval:{len(evidence)}-hits")

    # -- 3. translate --------------------------------------------------------
    pipeline.append("translate")
    translation = nl_to_sql.translate(question)
    if not translation.ok:
        pipeline.append("translate:refused")
        return _refuse(
            question, "translation",
            translation.reason,
            candidates=translation.candidates,
            citations=found["citations"],
            evidence=evidence,
            pipeline=pipeline,
        )
    pipeline.append(f"translate:{translation.query.qualified_name}")

    # -- 4. validate ---------------------------------------------------------
    # Belt and braces: translate() already validated the statement it is about
    # to return, and the catalogue is validated on every call. Re-running it
    # here means the gate is visible in the pipeline trace and is proven to
    # execute in the path that matters, not merely to exist somewhere.
    pipeline.append("sql_validator")
    verdict = validate(translation.sql or "")
    if not verdict.ok:
        pipeline.append("sql_validator:rejected")
        return _refuse(
            question, "sql_validator",
            "the generated statement was refused by the SQL validator:\n"
            + verdict.explain(),
            citations=found["citations"],
            evidence=evidence,
            pipeline=pipeline,
        )
    pipeline.append("sql_validator:clear")

    # -- 5. execute ----------------------------------------------------------
    pipeline.append("execute")
    from app.database.readonly import get_readonly_session_factory
    from app.services.analytics import execute_query

    session = get_readonly_session_factory()()
    try:
        result = execute_query(
            session, translation.sql, limit=limit, offset=0
        )
        rows, columns = result.rows, result.columns
        total_rows, elapsed_ms = result.total_rows, result.elapsed_ms
    except Exception as exc:  # noqa: BLE001 - surfaced, never guessed around
        pipeline.append("execute:failed")
        return _refuse(
            question, "execution",
            (
                f"PostgreSQL refused the statement: {type(exc).__name__}. No "
                f"answer is given, because a query that did not run is not "
                f"evidence for anything."
            ),
            citations=found["citations"],
            evidence=evidence,
            pipeline=pipeline,
        )
    finally:
        session.close()
    pipeline.append(f"execute:{len(rows)}-rows")

    # -- 6. verify -----------------------------------------------------------
    # The prose is re-checked against the rows here, even though `g_compose`
    # built it FROM the rows. That is deliberate redundancy: the compose path
    # is the one place a formatting bug could put a number in the answer that
    # no cell supports, and this catches exactly that.
    pipeline.append("verify")
    # Two checks, because they catch different things.
    #
    #   1. STRUCTURAL - every numeric cell must render as a string that reads
    #      back as the same number. This is what catches scientific notation.
    #   2. TEXTUAL    - every number in the rendered prose must be a value the
    #      query returned, excluding digits inside category labels.
    #
    # The textual check on its own produced false failures on four correct
    # queries, because `"2. 1-14% off"` is a category name and not a
    # measurement of 2, 1 and 14. The structural check cannot tell label from
    # measurement and does not need to: it never looks at the text.
    cells = verify.verify_rendered_cells(rows, columns, g_format)
    rendered = g_render_table(columns, rows)
    prose = verify.verify_prose(
        rendered, rows, labels=verify.label_values(columns, rows)
    )
    checked = (
        cells
        if not cells.ok
        else VerificationMerged(cells, prose)
    )
    if not checked.ok:
        pipeline.append("verify:failed")
        return _refuse(
            question, "verification",
            (
                f"{len(checked.failures)} value(s) in the rendered result are not "
                f"present in the rows the query returned, so the answer is "
                f"withheld rather than published unverified."
            ),
            query=translation.query.qualified_name,
            source=translation.query.source,
            sql=translation.sql,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            total_rows=total_rows,
            citations=found["citations"],
            evidence=evidence,
            verified=checked,
            pipeline=pipeline,
        )
    pipeline.append("verify:ok")

    # The deterministic answer is composed and verified FIRST. Only then may a
    # model restate it, and only if one is configured - see app/ai/llm.py. The
    # ordering is the safety property: the model never sees a question that has
    # not been screened, never runs SQL, and its text is re-checked against the
    # same rows before it is returned.
    deterministic_answer = g_compose(
        question, translation.query, columns, rows, total_rows,
        evidence, elapsed_ms,
        notes=list(translation.notes),
    )
    enrichment = llm.enrich(question, deterministic_answer, columns, rows)
    # Deliberately NOT appended to `pipeline`. That list is the safety trace,
    # and its invariant is that an answered request ENDS at `verify:ok` -
    # nothing runs after the last gate. Presentation is not a gate, so it is
    # reported in the `llm` field instead, where it cannot be mistaken for one.

    return Answer(
        answered=True,
        question=question,
        answer=enrichment.answer,
        query=translation.query.qualified_name,
        source=translation.query.source,
        sql=translation.sql,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        total_rows=total_rows,
        elapsed_ms=elapsed_ms,
        citations=found["citations"],
        evidence=evidence,
        candidates=translation.candidates,
        verified=checked,
        pipeline=pipeline,
        llm=enrichment.as_dict(),
    )


def analyse(question: str, limit: int = 20) -> Answer:
    """The public entry point. `require_evidence=True`: the strict path."""
    return g_analyse(question, limit=limit, require_evidence=True)


def provider_status() -> dict[str, Any]:
    """What the LLM layer can do right now, measured from this process.

    A status endpoint rather than a sentence in the README, because "the LLM is
    optional" and "the LLM is actually off" are different claims and only one
    of them is checkable.
    """
    status = llm.get_provider().status()
    return {
        **status.as_dict(),
        "deterministic_analyst": (
            "always available; every gate in app/ai/ runs with no model"
        ),
        "model_may_not": [
            "supply a number that is not in the returned rows",
            "choose or alter SQL",
            "see the database, the catalogue or the filesystem",
            "call a tool",
        ],
    }


def document_answer(question: str, limit: int = 5) -> Answer:
    """Retrieval only. No SQL runs, so no number may be stated.

    This is the correct path for a question about the project rather than about
    the data - "what does the data dictionary say about discount signs", "which
    tables have no primary key". The answer is quoted passages with citations.
    """
    question = (question or "").strip()
    found = retrieval.evidence_for(question, limit=limit)
    if not found["hits"]:
        return _refuse(
            question, "evidence",
            (
                "nothing in the verified corpus covers this, so there is nothing "
                "to quote. Absence of evidence ends the answer; it does not "
                "invite a guess."
            ),
        )

    lines = []
    for hit in found["hits"]:
        body = hit["text"]
        if len(body) > 900:
            body = body[:900].rsplit(" ", 1)[0] + " [...]"
        lines.append(
            f"### `{hit['path']}` -- {hit['section']}\n\n"
            f"_{hit['citation']}, relevance {hit['score']:.2f}_\n\n{body}\n"
        )

    return Answer(
        answered=True,
        question=question,
        answer="\n".join(lines),
        citations=found["citations"],
        evidence=found["hits"],
        pipeline=["retrieval", f"retrieval:{len(found['hits'])}-hits", "quote"],
        stage="retrieval",
    )