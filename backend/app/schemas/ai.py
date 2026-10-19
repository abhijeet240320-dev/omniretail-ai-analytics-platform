"""Response schemas for the AI layer.

WHAT IS AND IS NOT IN THE RESPONSE
----------------------------------
Every refusal is a first-class response with a `reason` and a `stopped_at`
stage, not an exception or a 500. The stage names the gate that refused, which
is what makes the refusal debuggable: "the SQL validator said no" and "the
verification step said no" are different problems with different fixes, and a
caller that cannot tell them apart will assume the system is broken.

The analyst's answer body carries `answered: bool`. A refusal has
`answered=False` and a `reason`, and it returns 200 - the request succeeded, the
answer is "no". Returning 400 would conflate "you asked badly" with "I will not
answer that", and conflating them is how a refusal gets read as a bug.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Citation(BaseModel):
    citation: str = Field(description="path#Lline - openable and checkable")
    path: str
    kind: str
    section: str
    score: float
    matched_terms: list[str] = Field(default_factory=list)


class LLMProviderStatus(BaseModel):
    """What the optional LLM layer can do, measured from this process.

    Exposed rather than asserted in prose so that "is the model actually on?"
    is a question with an answer at runtime. `available: false` is a normal,
    supported state - it is the state this project ships in - and `reason` says
    exactly what is missing rather than "not configured".
    """

    name: str
    available: bool
    reason: str
    model: str | None = None
    fallback_active: bool = Field(
        description="True when the deterministic analyst produced the answer"
    )
    deterministic_analyst: str = Field(
        description="Always true, and the reason this endpoint is safe to call"
    )
    model_may_not: list[str] = Field(
        description="The limits that are enforced in code, not by prompting"
    )


class Evidence(BaseModel):
    supported: bool
    question: str
    hits: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


class CandidateQuery(BaseModel):
    query: str
    source: str
    question: str
    score: float
    reasons: list[str] = Field(default_factory=list)


class VerificationDetail(BaseModel):
    """What the verification step examined, and what it refused.

    `checked` is included on purpose. A verifier that only reports failures is
    indistinguishable from one that checked nothing, and "I checked 214 values
    and found 0 problems" is a materially different claim from silence.
    """

    ok: bool
    checked: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, str]] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)


class Correction(BaseModel):
    rule: str
    severity: str
    correction: str
    citation: str


class AnalystResponse(BaseModel):
    """The analyst's answer, or its refusal. Never both."""

    answered: bool = Field(
        description=(
            "False means a refusal. The request succeeded; the answer is 'no'. "
            "Read `reason` and `stopped_at`."
        )
    )
    question: str
    answer: str = Field(default="", description="Empty when answered is false")
    reason: str = Field(default="", description="Why there is no answer")
    stopped_at: str = Field(
        default="",
        description=(
            "The pipeline stage that refused: false_premise, evidence, "
            "translation, sql_validator, execution, verification. Empty when "
            "answered."
        ),
    )
    query: str | None = None
    source: str | None = None
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    total_rows: int = 0
    elapsed_ms: float = 0.0
    citations: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    matched_rules: list[str] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list)
    verified: VerificationDetail | None = None
    candidates: list[CandidateQuery] = Field(default_factory=list)
    pipeline: list[str] = Field(
        default_factory=list,
        description=(
            "Every gate the request passed, in order. Present so the pipeline is "
            "observable rather than asserted."
        ),
    )
    llm: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Whether a model wrote the prose. `llm_used: false` means the "
            "deterministic composer did, which is the default and the supported "
            "state. When a model IS used, its text has been re-checked against "
            "the returned rows with the same gate that checks the "
            "deterministic answer."
        ),
    )


class TranslateRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    limit: int = Field(
        default=5, ge=1, le=10,
        description="How many near-miss candidates to report on a refusal",
    )


class TranslateResponse(BaseModel):
    ok: bool
    question: str
    query: str | None = None
    sql: str | None = None
    reason: str = ""
    sources_named: list[str] = Field(default_factory=list)
    sources_required: list[str] = Field(default_factory=list)
    candidates: list[CandidateQuery] = Field(default_factory=list)
    validation: dict[str, Any] | None = None


class ValidateRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)


class ValidateResponse(BaseModel):
    ok: bool
    sql: str
    tables: list[str] = Field(default_factory=list)
    source_schemas: list[str] = Field(default_factory=list)
    violations: list[dict[str, str]] = Field(default_factory=list)
    explanation: str = Field(default="", description="Human-readable refusal")


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)
    kind: str | None = Field(
        default=None,
        description=(
            "Restrict to one corpus kind: documentation, analytics-query, "
            "configuration, measurement-report, provenance, policy"
        ),
    )


class RetrievalProvenance(BaseModel):
    """How an answer was retrieved, disclosed rather than implied.

    `mode` is `hybrid-bm25+lsa` when both retrievers ran and `bm25-only` when
    the vector index was unavailable or had nothing for this question. The
    second is a supported state, not an error, and `note` says which.
    """

    mode: str
    lexical_hits: int
    semantic_hits: int
    vector_backend: str
    vector_dimensions: int
    note: str = ""


class SearchResponse(BaseModel):
    supported: bool
    question: str
    corpus_chunks: int = 0
    corpus_documents: int = 0
    hits: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    retrieval: RetrievalProvenance | None = None


class CatalogueHealthResponse(BaseModel):
    total: int
    validator_ok: int
    executable: int
    source_schemas: list[str]
    queries: list[dict[str, Any]]