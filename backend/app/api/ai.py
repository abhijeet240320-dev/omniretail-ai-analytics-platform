"""Endpoints for the AI analyst, NL-to-SQL, the SQL validator and retrieval.

SCOPE
-----
`ai:read` for the four question-answering endpoints. `/validator/status` and
`/provider/status` need `admin:read`: the first because it opens a database
session against every catalogued query, and a hot loop of that is a way to make
the database do work for free; the second because anything that describes how
the AI layer is wired is reconnaissance.

WHAT THIS ROUTER WILL NOT HAVE
------------------------------
No endpoint that executes caller-supplied SQL.

`/ai/validate` checks a statement and returns a verdict. It does NOT run it.
The distinction is the whole point of the feature: validating is how you find
out that `DELETE FROM` is refused, and running arbitrary SQL is the thing being
prevented. `/ai/translate` returns SQL only when it is one of the 41 verified
statements, and returns a refusal otherwise - it has no path that reaches
`/ai/validate` with invented SQL in the first place.

WHY A REFUSAL IS A 200
----------------------
`POST /ai/ask` returns 200 with `answered: false` when the analyst declines to
answer. The request succeeded. The analyst is doing exactly what it is
supposed to do, and reporting that as an HTTP error would train clients to
retry a refusal as though it were a failure, or to log it as an incident.
The reason and the stage that produced it are in the body.

The exception is the authentication surface: a missing or wrong API key is a
401, because the caller has not proved it should get an answer at all.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query

from app.ai import analyst, nl_to_sql, retrieval
from app.ai.analyst import analyse, document_answer
from app.ai.guards import check as guard_check
from app.ai.sql_validator import validate as validate_sql
from app.api.deps import require_admin, require_ai
from app.schemas.ai import (
    AnalystResponse,
    CatalogueHealthResponse,
    LLMProviderStatus,
    SearchRequest,
    SearchResponse,
    TranslateRequest,
    TranslateResponse,
    ValidateRequest,
    ValidateResponse,
)

router = APIRouter(prefix="/api/ai", tags=["ai"])

DESCRIPTION = """
The analyst, the SQL validator and retrieval over the verified corpus.

**The rule this is built on:**

```
NO VERIFIED EVIDENCE
        ->  NO FACTUAL ANSWER
```

The model is not the source of truth. PostgreSQL is, and the committed
documentation is. Anything that is not traceable to one of those is reported
as unavailable, and `answered` is `false` with a `stopped_at` stage saying
which gate refused.

**There is no combined figure across the four sources.** They are unrelated
companies with no shared key space, so a total spanning them has no referent.
See `docs/api.md`.
"""


@router.post(
    "/ask",
    response_model=AnalystResponse,
    summary="Ask a question; get a verified answer or a refusal",
)
def ask(
    payload: TranslateRequest,
    _principal=Depends(require_ai),
) -> AnalystResponse:
    """Run the full six-gate pipeline.

    1. `false_premise`  - is the question answerable as asked?
    2. `evidence`       - does the verified corpus support it?
    3. `translation`    - is it one of the 41 catalogued queries?
    4. `sql_validator`  - is the statement safe to run?
    5. `execution`      - real rows from PostgreSQL, read-only
    6. `verification`   - is every number traceable to a returned row?

    Any gate can refuse. The response says which one did.
    """
    answer = analyse(payload.question, limit=payload.limit)
    return AnalystResponse(**answer.as_dict())


@router.post(
    "/ask/document",
    response_model=AnalystResponse,
    summary="Ask about the PROJECT (documentation), not the data",
)
def ask_document(
    payload: TranslateRequest,
    _principal=Depends(require_ai),
) -> AnalystResponse:
    """Retrieval only. No SQL runs, so no measurement is reported.

    This is the right endpoint for "what does the data dictionary say about
    discount signs" or "which tables have no primary key" - questions about
    the project rather than about the business. The answer is quoted passages
    with citations.
    """
    return AnalystResponse(**document_answer(payload.question, limit=payload.limit).as_dict())


@router.post(
    "/translate",
    response_model=TranslateResponse,
    summary="Natural language to one of the 41 verified SQL statements",
)
def translate(
    payload: TranslateRequest,
    _principal=Depends(require_ai),
) -> TranslateResponse:
    """Translate a question into SQL, or explain why it cannot be translated.

    The translator is RETRIEVAL, not generation. It ranks the 41 hand-written,
    verified statements in `backend/sql/` and returns one of them verbatim. It
    never assembles a statement, so there is no path by which a novel query
    reaches the database.

    A question outside the catalogue is refused with the nearest matches, not
    approximated. That is the deliberate trade: an unimprovised query returns
    rows, and rows get quoted as answers.
    """
    result = nl_to_sql.translate(payload.question, limit=payload.limit)
    body = result.as_dict()
    validation = None
    if result.sql:
        validation = validate_sql(result.sql).as_dict()
    return TranslateResponse(**{**body, "validation": validation})


@router.post(
    "/validate",
    response_model=ValidateResponse,
    summary="Check SQL against the AI controls WITHOUT running it",
)
def validate(
    payload: ValidateRequest,
    _principal=Depends(require_ai),
) -> ValidateResponse:
    """Validate a statement. Never execute it.

    Rejects the thirteen data- and schema-modifying statements, more than one
    statement in a string, anything that is not a SELECT or WITH, functions
    with filesystem or timing side effects, and any query that crosses source
    schemas.

    Rejection is a normal outcome here, not an error. A 200 with
    `ok: false` and a populated `violations` list is the useful response; a
    400 would say the caller misused the endpoint, which they did not.
    """
    result = validate_sql(payload.sql)
    return ValidateResponse(
        ok=result.ok,
        sql=result.sql,
        tables=result.tables,
        source_schemas=result.source_schemas,
        violations=[v.as_dict() for v in result.violations],
        explanation=result.explain(),
    )


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Retrieve verified evidence for a question",
)
def search(
    payload: SearchRequest,
    _principal=Depends(require_ai),
) -> SearchResponse:
    """Hybrid retrieval over the committed verified corpus.

    BM25 fused with the TF-IDF/SVD vector index; see
    `app/ai/vector_index.py` and `docs/retrieval.md`. The fusion is disclosed
    per request in `retrieval`, so a caller can see whether the answer came
    from a lexical match, a vector match, or both - and can see when the
    vector index was unavailable and only BM25 ran.

    Returns `supported: false` with an empty citation list when nothing
    matches. That empty result is the point: it is what `evidence_requirement`
    acts on, and it is why an unanswerable question produces no number rather
    than the least-bad document.
    """
    corpus = retrieval.load_corpus()
    found = retrieval.evidence_for(
        payload.question, limit=payload.limit, kind=payload.kind
    )

    return SearchResponse(
        supported=found["supported"],
        question=found["question"],
        corpus_chunks=corpus.size,
        corpus_documents=len({c.path for c in corpus.chunks}),
        hits=found["hits"],
        citations=found["citations"],
        retrieval=found["retrieval"],
    )


@router.get(
    "/guards",
    summary="List the false premises this analyst refuses",
)
def guards(
    _principal=Depends(require_ai),
) -> dict:
    """The refusal rules, with their corrections and citations.

    Exposed deliberately. A user who is refused should be able to see exactly
    which rule fired and what the corrected fact is, rather than having to ask
    again to find out why.
    """
    from app.ai.guards import RULES

    return {
        "rules": [
            {
                "name": r.name,
                "severity": r.severity,
                "pattern": r.pattern.pattern,
                "correction": r.correction,
                "citation": r.citation,
            }
            for r in RULES
        ]
    }


@router.get(
    "/check",
    summary="Check a question against the guards without running anything",
)
def check(
    question: str = Query(..., min_length=1, max_length=2000),
    _principal=Depends(require_ai),
) -> dict:
    """Run only the false-premise gate. Useful for debugging a refusal."""
    result = guard_check(question)
    return result.as_dict()


@router.get(
    "/validator/status",
    response_model=CatalogueHealthResponse,
    summary="Prove every catalogued statement is valid and executable",
)
def validator_status(_principal=Depends(require_admin)) -> CatalogueHealthResponse:
    """Validate AND compile every catalogued query, on demand.

    "The allowlist is safe" should be a live measurement rather than a claim in
    a README. Each statement is validated and then planned with `LIMIT 0`,
    which proves the tables and columns exist without reading a row.

    Admin-scoped: it opens a session per query, and a hot loop of that is a way
    to make the database do work for free.
    """
    started = time.monotonic()
    health = nl_to_sql.catalogue_health()
    health["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
    return CatalogueHealthResponse(**health)


@router.get(
    "/provider/status",
    response_model=LLMProviderStatus,
    summary="Whether a model is reachable, and what it is allowed to do",
)
def provider_status(_principal=Depends(require_admin)) -> LLMProviderStatus:
    """The LLM layer's own status, measured rather than documented.

    Readable without `admin:read` would be harmless - it reveals nothing but
    configuration state - but it is admin-scoped for consistency: anything that
    describes how the AI layer is wired is reconnaissance, and this project
    does not hand reconnaissance to an analytics key.
    """
    return LLMProviderStatus(**analyst.provider_status())