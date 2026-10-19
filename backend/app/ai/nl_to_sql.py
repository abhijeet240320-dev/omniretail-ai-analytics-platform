"""Natural language to SQL, by retrieval rather than by generation.

THE CENTRAL DECISION: THERE IS NO MODEL IN THIS LOOP
----------------------------------------------------
`.env` names an OpenAI key and the key is empty. `openai` is not installed.
So there are two honest options:

  a) Build the feature around a model that is not available, so every test is
     skipped and the anti-hallucination controls are untested.
  b) Build a translation that actually runs, over a closed set of SQL that was
     written and verified by a human, and that refuses anything outside it.

(b) is what is here, and it is not a stub.

WHY (b) IS ACTUALLY BETTER FOR THIS PROJECT
-------------------------------------------
The 41 queries in `backend/sql/` were each written by hand, each carries the
question it answers, and each was executed against the live database with its
result recorded in `_sql_results.json`. Free-text generation would replace 41
known-good statements with an unbounded number of unknown ones - strictly more
surface area, and it would put the anti-hallucination burden entirely on the
prompt. Retrieval-into-an-allowlist keeps the burden where it belongs: the
statement was correct before the question arrived.

The limitation is stated plainly in the API response and in the docs: a question
outside the 41 does not get an improvised query. It gets a refusal, a list of
the nearest questions that ARE answerable, and the schema hint needed to phrase
one. That is a strictly better failure mode than a plausible wrong query.

THE CONTRACT
------------
1. A translated statement is always one of the 41. Never assembled, never
   edited. So `sql_validator` is not bypassed - the catalogue is validated at
   load time, once.
2. A source must be named. "How many orders?" across four datasets has no
   answer until the user picks one, and the API asks rather than guessing.
3. Refusal is a first-class result with a reason, not an exception.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.ai.retrieval import search
from app.ai.sql_validator import SOURCE_SCHEMAS, validate
from app.services.query_catalogue import CataloguedQuery, load_catalogue
from app.ai import guards

# Below this score, no catalogue query is close enough to claim the question is
# about it. The number is a judgement and is documented as one: above it, a wrong
# query gets executed and its rows are quoted as an answer.
#
# It was 0.30 until
# `test_the_translator_refuses_rather_than_improvising_sql` showed
# "compute the elasticity of demand with respect to competitor prices" clearing
# 0.30 and being answered by an M5 demand query. Two overlapping words plus a
# source hint was enough to run the wrong statement. 0.45 requires either an
# explicit query-name match or three or four concepts in common.
#
# The cost of raising it is that some genuine questions get refused. That is
# the intended direction: a refusal is recoverable and a wrong answer is not.
MIN_SCORE = 0.45

# Question phrasing that names the dataset. Deliberately simple: the source is
# the one thing that cannot be inferred, so it is matched literally and
# anything else is asked about rather than guessed.
_SOURCE_HINTS: dict[str, tuple[str, ...]] = {
    "olist": ("olist", "brazil", "brazilian", "marketplace"),
    "dataco": ("dataco", "supply chain", "supply-chain", "supplychain"),
    "dunnhumby": ("dunnhumby", "grocery", "grocery panel", "panel",
                  "household", "coupon", "basket"),
    "m5": ("m5", "walmart", "wal-mart", "demand", "forecast", "sell_price",
           "store", "item"),
}

_STOP = frozenset("""
a an the of to in on at by for with and or not is are was were be been being
this that these those it its as from into over under about how what which when
where who why do does did done can could show me give list find get tell
please there here top best worst most least more less than then some any all
""".split())

_WORD = re.compile(r"[a-z0-9_]+")

# Business words a person uses that the analyst's question does not. Ranking on
# raw term overlap alone maps "revenue by state" to a question about order
# status, because both say "order" once and neither says "state".
#
# These are synonyms, not a thesaurus: each entry lists the OTHER spellings of
# one concept found in this project's vocabulary. They expand the QUESTION
# before matching, never the catalogue, so the catalogue's own wording stays
# exactly as the analyst wrote it.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "revenue": ("value", "gmv", "sales", "spend", "turnover"),
    "sales": ("revenue", "units", "volume"),
    "state": ("geography", "region", "province", "uf"),
    "city": ("geography", "region", "municipality"),
    "delay": ("late", "delayed", "delivery", "overdue"),
    "delivery": ("delay", "late", "shipping", "shipment"),
    "installment": ("payment", "payments", "installments"),
    "payment": ("installment", "installments", "paid"),
    "review": ("rating", "score", "reviews"),
    "score": ("review", "rating", "reviews"),
    "margin": ("profit", "profitability", "loss"),
    "loss": ("margin", "profitability"),
    "discount": ("promotion", "promo", "markdown"),
    "coupon": ("redemption", "redeemed", "voucher"),
    "repeat": ("returning", "loyal", "retention", "comeback"),
    "store": ("stores", "ca_1", "ca_2", "ca_3", "ca_4", "tx_1", "wi_1"),
    "item": ("product", "products", "sku"),
    "product": ("item", "items", "sku", "category"),
    "customer": ("buyers", "shopper", "household", "client"),
    "household": ("customer", "shopper", "panel"),
    "forecast": ("demand", "intermittency", "prediction"),
    "demand": ("sales", "units", "forecast"),
    "price": ("pricing", "sell_price"),
    "market": ("country", "countries", "geography", "nationality"),
    "segment": ("segments", "category", "tier"),
    "week": ("weekly", "weekday", "weekday"),
    "month": ("monthly",),
    "year": ("yearly", "annual", "trend"),
    "refund": ("chargeback", "cancellation", "canceled"),
    "lead": ("leads", "mql", "marketing"),
    "seller": ("sellers", "merchant"),
    "status": ("funnel", "state", "stage"),
}

# Nouns that mean a dataset has to be named before the question can be
# answered. "How many orders?" is not under-specified in a subtle way - there
# are orders in three of the four datasets and none of them can be combined, so
# there is no correct answer to pick.
_REQUIRES_SOURCE = re.compile(
    r"\b(order|orders|customer|customers|revenue|sales|product|products|"
    r"item|items|household|households|buyer|buyers|shopper|shoppers|"
    r"payment|payments|review|reviews|seller|sellers|store|stores|"
    r"coupon|coupons|margin|price|prices|demand|shipment|lead|leads)\b",
    re.IGNORECASE,
)

# Expanded once at import, not per question: 41 questions per request, and the
# synonym expansion is pure.
_EXPANSION: dict[str, str] = {
    term: term for term in _SYNONYMS
}
for _canonical, _alternates in _SYNONYMS.items():
    for _alternate in _alternates:
        _EXPANSION.setdefault(_alternate, _canonical)


def _terms(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in _STOP and len(t) > 1]


def _expanded(text: str) -> set[str]:
    """Terms plus their canonical concept, so synonyms meet in the middle.

    "gmv by state" expands to {gmv, revenue, state, geography} and the query
    asking about "revenue ... geography" now matches on two concepts instead of
    one incidental word.
    """
    out = set(_terms(text))
    return out | {_EXPANSION[t] for t in out if t in _EXPANSION}


def detect_source(question: str) -> list[str]:
    """Which source(s) the question names. `[]` means it names none."""
    lowered = question.lower()
    found = [
        source
        for source, hints in _SOURCE_HINTS.items()
        if any(hint in lowered for hint in hints)
    ]
    return found


@dataclass
class Candidate:
    """One catalogued query and how well it matched."""

    query: CataloguedQuery
    score: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.qualified_name,
            "source": self.query.source,
            "question": self.query.question,
            "score": round(self.score, 4),
            "reasons": self.reasons,
        }


@dataclass
class Translation:
    """The verdict. Never an exception; `sql` is None whenever `ok` is False."""

    ok: bool
    question: str
    sql: str | None = None
    query: CataloguedQuery | None = None
    candidates: list[Candidate] = field(default_factory=list)
    reason: str = ""
    sources_named: list[str] = field(default_factory=list)
    sources_required: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "question": self.question,
            "query": self.query.qualified_name if self.query else None,
            "sql": self.sql,
            "reason": self.reason,
            "sources_named": self.sources_named,
            "sources_required": self.sources_required,
            "notes": self.notes,
            "candidates": [c.as_dict() for c in self.candidates],
        }


def _question_text(query: CataloguedQuery) -> str:
    """Everything a user might have written, for one catalogued query.

    The stated question, the query name split on underscores, and the source
    name. The name matters: people ask "revenue by state" far more often than
    they reproduce the analyst's wording.
    """
    parts = [query.question, query.name.replace("_", " "), query.source]
    return " ".join(parts)


def _exact_name(question: str) -> str | None:
    """The catalogue query whose name the question contains verbatim, if any.

    Lets `translate` skip the "name a dataset first" prompt when the caller has
    already identified the query unambiguously by name, which is what
    `/api/analytics/queries/{name}` callers tend to do.
    """
    lowered = (question or "").lower().strip("?").strip()
    for query in load_catalogue().values():
        if query.name.replace("_", " ") in lowered:
            return query.qualified_name
    return None


def rank(question: str, limit: int = 5) -> list[Candidate]:
    """Rank the 41 catalogued queries against a question.

    Hybrid scoring: concept overlap against the query's own stated question
    gives precision, and BM25 over the corpus - which includes every .sql body -
    gives recall for phrasing nobody anticipated. Neither alone is good enough.

    BM25 is computed ONCE per request, not once per candidate. Doing it inside
    the loop meant 41 corpus scans to rank 41 queries that were already all in
    memory, and it made the endpoint take 350ms instead of 5ms.
    """
    candidates: list[Candidate] = []
    wanted = _expanded(question)
    named = set(detect_source(question))

    # One corpus scan for the whole request.
    bm25: dict[str, float] = {}
    bm25_terms: dict[str, set[str]] = {}
    for hit in search(question, limit=len(load_catalogue()), kind="analytics-query"):
        bm25[hit.chunk.path] = hit.score
        bm25_terms[hit.chunk.path] = set(hit.matched_terms)

    for query in load_catalogue().values():
        reasons: list[str] = []
        score = 0.0

        # EXACT NAME MATCH DOMINATES, AND IS SCORED BY COVERAGE.
        #
        # Two of the 41 could not be retrieved by their own names:
        #   `seller_concentration`       -> `geography_revenue_concentration`
        #   `intermittency_by_series`    -> `intermittency`
        # Both because a shorter sibling name is a subset of a longer one, so
        # asking for "intermittency by series" matched `intermittency` just as
        # well. A flat bonus does not fix that - it tied them exactly, at
        # 2.4536 each, and the alphabetical sort silently picked the wrong one.
        #
        # Coverage is the fix: a name that accounts for MORE of the question is
        # a better match than one that accounts for a fraction of it.
        name_phrase = query.name.replace("_", " ")
        if name_phrase in question.lower().strip("?").strip():
            name_terms = set(_terms(name_phrase))
            question_terms = set(_terms(question))
            coverage = len(name_terms & question_terms) / max(len(question_terms), 1)
            score += 2.0 * coverage
            reasons.append(
                f"the question names the query ({name_phrase}), "
                f"covering {coverage:.0%} of its terms"
            )

        own = _expanded(_question_text(query))
        overlap = wanted & own

        # The source is a strong prior, not a decision. "orders" appears in two
        # datasets; the rest of the question breaks the tie.
        if query.source in named:
            score += 0.40
            reasons.append(f"names the {query.source} source")
        elif named:
            # A different source was named. Not disqualifying on its own, but
            # it must be earned back, so it costs rather than filters - a user
            # who says "olist" may still mean a query whose question text
            # happens to mention another dataset.
            score -= 0.15
            reasons.append(f"{query.source} was not named in the question")

        # Overlap, with a bonus for concepts (canonicalised synonyms) because
        # those are the ones that carry meaning; "revenue" matching "revenue"
        # is a coincidence, "gmv" matching "revenue" is understanding.
        concepts = overlap & set(_SYNONYMS)
        plain = overlap - concepts
        score += min(len(concepts), 4) * 0.18
        score += min(len(plain), 4) * 0.10
        if overlap:
            reasons.append(
                "shared terms: " + ", ".join(sorted(overlap)[:6])
                + (f" (concepts: {', '.join(sorted(concepts))})" if concepts else "")
            )

        if query.file in bm25:
            score += bm25[query.file] * 0.30
            if bm25_terms.get(query.file):
                reasons.append(
                    "corpus terms: " + ", ".join(sorted(bm25_terms[query.file])[:5])
                )

        if score > 0:
            candidates.append(Candidate(query=query, score=score, reasons=reasons))

    candidates.sort(key=lambda c: (-c.score, c.query.qualified_name))
    return candidates[:limit]


def translate(question: str, limit: int = 5) -> Translation:
    """Turn a question into one of the 41 statements, or explain why not.

    The order of the checks matters. Source ambiguity is tested BEFORE ranking,
    because "how many orders?" has four equally plausible answers and running
    the top-ranked one would be a coin flip dressed up as an answer.
    """
    question = (question or "").strip()
    named = detect_source(question)

    if not question:
        return Translation(False, question, reason="the question is empty")

    # The false-premise gate runs HERE as well as in `analyst`. Duplicated on
    # purpose: `/api/ai/translate` is a public endpoint that does not go
    # through `analyst`, and a caller using it alone must not be able to obtain
    # SQL for a question the system refuses to answer. Defence in depth, and the
    # two gates cannot disagree because they call the same rules.
    guard = guards.check(question)
    named_query = _exact_name(question)
    if guard.triggered and not named_query:
        return Translation(
            False, question,
            reason=(
                "the question rests on something that is not true of this data "
                f"({', '.join(guard.matched)}), so no statement is produced:\n\n"
                + "\n\n".join(c["correction"] for c in guard.corrections)
            ),
            sources_named=named,
        )

    # A guard fired, but the caller named a catalogued query BY NAME.
    #
    # `olist.customer_repeat_behaviour` is exactly the query that answers the
    # corrected form of "show me repeat purchasers in olist" - it groups by
    # `customer_unique_id`, not by the per-order `customer_id` the premise
    # assumes. Refusing it would refuse the correct answer to a question that
    # HAS one, so the query runs and the correction travels with it as a note.
    #
    # The distinction is who is speaking: an ambiguous question gets corrected
    # and stopped; a question that names the statement gets corrected and
    # answered.
    notes: list[str] = []
    if guard.triggered:
        notes.append(
            "Note: this question rests on something that is not true of this "
            f"data ({', '.join(guard.matched)}). The query you named answers "
            "the corrected form, but read the correction before quoting its "
            "numbers:\n\n"
            + "\n\n".join(c["correction"] for c in guard.corrections)
        )

    # A question naming two sources cannot be answered by any one statement.
    if len(named) > 1:
        return Translation(
            False, question, reason=(
                f"the question names {len(named)} datasets ({', '.join(named)}) but "
                f"no statement can span them: these are unrelated companies with no "
                f"shared key space. Ask about one at a time."
            ),
            sources_named=named,
        )

    # A question that names no dataset and asks about a business noun has more
    # than one correct answer and no way to pick one. Guessing would be a coin
    # flip presented as an analysis, so the analyst asks instead.
    if not named and _REQUIRES_SOURCE.search(question) and not _exact_name(question):
        return Translation(
            False, question, reason=(
                f"the question does not say which dataset. There are "
                f"{len(SOURCE_SCHEMAS)} ({', '.join(SOURCE_SCHEMAS)}) and they "
                f"cannot be combined, so there is no single right answer to "
                f"pick for you. Name one - e.g. \"olist\", \"dataco\", "
                f"\"dunnhumby\" or \"m5\" - and I will run it."
            ),
            sources_named=named,
            sources_required=list(SOURCE_SCHEMAS),
        )

    ranked = rank(question, limit=limit)
    best_score = ranked[0].score if ranked else 0.0
    if not ranked or best_score < MIN_SCORE:
        nearest = [c.as_dict() for c in ranked[:3]]
        return Translation(
            False, question,
            reason=(
                f"no catalogued query matches this closely enough to run "
                f"(best score {best_score:.2f} < {MIN_SCORE:.2f} threshold). "
                f"The analyst answers the {len(load_catalogue())} verified queries "
                f"rather than improvising SQL, so an unmatched question returns "
                f"nothing rather than something plausible."
            ),
            candidates=nearest,
            sources_named=named,
        )

    best = ranked[0]
    query = best.query

    # Cross-source guard, applied to the catalogue itself rather than assumed.
    # Every statement must pass the validator before it can be handed back.
    # A statement that does not is a defect in backend/sql/, not something to
    # work around at request time.
    verdict = validate(query.sql)
    if not verdict.ok:
        return Translation(
            False, question,
            reason=(
                f"catalogued query {query.qualified_name} failed SQL validation and "
                f"was withdrawn:\n{verdict.explain()}"
            ),
            query=query,
            candidates=[c.as_dict() for c in ranked],
            sources_named=named,
        )

    return Translation(
        True, question,
        sql=query.sql,
        query=query,
        candidates=[c.as_dict() for c in ranked],
        sources_named=named,
        sources_required=[query.source],
        notes=notes,
    )


def catalogue_health() -> dict[str, Any]:
    """Prove, at call time, that every catalogued statement is executable.

    Checked on demand rather than at import because it needs the database, and
    importing the app must not require one. `/api/ai/sql-validator/status`
    returns this, so "the allowlist is safe" is a live measurement rather than a
    claim in a README.
    """
    from app.database.readonly import get_readonly_session_factory
    from sqlalchemy import text

    results = []
    session = get_readonly_session_factory()()
    try:
        for query in sorted(
            load_catalogue().values(), key=lambda q: q.qualified_name
        ):
            verdict = validate(query.sql)
            executable: bool | None = None
            error: str | None = None
            if verdict.ok:
                try:
                    # `LIMIT 0` compiles and plans without reading a single
                    # row, which is exactly the check needed: it proves the
                    # tables and columns exist, at zero cost on 59M rows.
                    session.execute(
                        text(f"SELECT * FROM ({_as_subquery(query.sql)}) AS q LIMIT 0")
                    )
                    executable = True
                except Exception as exc:  # noqa: BLE001 - recorded, not raised
                    executable = False
                    error = type(exc).__name__
            results.append({
                "query": query.qualified_name,
                "source": query.source,
                "validator_ok": verdict.ok,
                "violations": [v.as_dict() for v in verdict.violations],
                "schemas": verdict.source_schemas,
                "executable": executable,
                "error": error,
            })
    finally:
        session.close()

    return {
        "total": len(results),
        "validator_ok": sum(1 for r in results if r["validator_ok"]),
        "executable": sum(1 for r in results if r["executable"] is True),
        "source_schemas": list(SOURCE_SCHEMAS),
        "queries": results,
    }


def _as_subquery(sql: str) -> str:
    """Strip the trailing semicolon so the statement can be wrapped.

    Identical in purpose to the analytics service's helper. Duplicated rather
    than imported because this module must not import the execution path - the
    whole point is that translation and execution are separate stages, and
    sharing the wrapper would quietly couple them.
    """
    cleaned = re.sub(r"--[^\n]*", "", sql)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
    return cleaned.strip().rstrip(";").strip()