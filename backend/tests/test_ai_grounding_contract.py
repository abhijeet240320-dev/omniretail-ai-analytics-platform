"""The anti-hallucination contract, enforced against the live database and the
live AI layer.

THE RULE
--------
    NO VERIFIED EVIDENCE
            ↓
    NO FACTUAL ANSWER

This file was written BEFORE the AI layer existed, as a specification of what
that layer would have to enforce. It did two things:

  1. Proved the controls that already existed - the read-only role, the role
     setting, the absence of cross-source foreign keys - by asserting against
     the live database rather than against documentation.
  2. Named, in `REQUIRED_CONTROLS`, five controls that had to exist BEFORE any
     system was allowed to answer a question about this data, each with the
     test that would justify marking it implemented.

BOTH ARE NOW REAL
-----------------
All five controls are built. `backend/app/ai/` holds the implementations and
`backend/tests/test_ai_controls.py` holds the proving tests, which are named in
`REQUIRED_CONTROLS[name]["test_when_built"]` and checked for agreement by
`test_every_required_control_has_a_proving_test` in that same file.

`test_no_control_is_marked_implemented_without_proving_it` still exists, and
its meaning changed with the layer. It now requires each entry to carry both an
`implemented` flag AND a `proving_test` naming a test that exists. So the
tripwire is still a tripwire: marking a control implemented without proving it
fails the build.

WHAT IS STILL DELIBERATELY NOT HERE
-----------------------------------
No test that runs a language model. `OPENAI_API_KEY` in `.env` is empty and
`openai` is not installed, so every control here is enforced by deterministic
code - a static SQL validator, a retrieval gate, a result verifier and a rule
table - rather than by a prompt. That is a stronger position than a prompt for
these five controls, and it is testable, which a prompt is not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from conftest import REPO_ROOT

from app.database.base import Base

# Importing the package registers all 30 tables on Base.metadata. Without it,
# Base.metadata is empty and every table looks like drift.
import app.models  # noqa: F401

AUDIT_REPORT = REPO_ROOT / "data" / "processed" / "_audit_report.json"
FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "truncate",
    "create", "grant", "revoke", "merge", "copy", "call", "do",
)


# ==============================================================================
# PART 1 - CONTROLS THAT MUST HOLD TODAY
# ==============================================================================

def test_ai_role_cannot_write_to_anything(live):
    """The read-only role must hold SELECT and nothing else, on all 30 tables.

    This is not "the application does not write". The DATABASE refuses to let
    it, so a bug in the AI layer cannot become a data change. Defence in depth
    that is actually load-bearing rather than merely present in review.
    """
    from sqlalchemy import text

    with live.bind.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT privilege_type FROM "
                "information_schema.role_table_grants "
                "WHERE grantee = 'omniretail_readonly'"
            )
        ).fetchall()
    granted = {row[0] for row in rows}
    assert granted <= {"SELECT"}, (
        f"omniretail_readonly holds {sorted(granted)}; it must hold SELECT only"
    )


def test_ai_role_holds_no_create_on_any_source_schema(live):
    """No CREATE on a source schema means the AI cannot add a table to hide in."""
    from sqlalchemy import text

    with live.bind.connect() as conn:
        can_create = conn.execute(
            text(
                "SELECT nspname FROM pg_namespace "
                "WHERE nspname IN ('olist', 'dataco', 'dunnhumby', 'm5', 'analytics') "
                "AND has_schema_privilege('omniretail_readonly', nspname, 'CREATE')"
            )
        ).fetchall()
    assert can_create == [], f"omniretail_readonly can CREATE in {can_create}"


def test_every_ai_session_starts_read_only(live):
    """The role is configured so its sessions cannot write even by accident.

    `default_transaction_read_only = on` at ROLE level means a forgotten
    `SET TRANSACTION READ ONLY` in some future code path still cannot result in
    a write. The application-level flag is the first line of defence; this is
    the one that holds when the first line is missing.
    """
    from sqlalchemy import text

    with live.bind.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT setdatabase, array_to_string(setconfig, ',') "
                "FROM pg_db_role_setting s "
                "JOIN pg_roles r ON r.oid = s.setrole "
                "WHERE r.rolname = 'omniretail_readonly'"
            )
        ).fetchall()
    assert rows, (
        "omniretail_readonly has no role-level settings; a future code path could "
        "open a writable session"
    )
    assert any(
        setdatabase == 0 and "default_transaction_read_only=on" in (config or "")
        for setdatabase, config in rows
    ), f"role settings are {rows}, expected a role-level default_transaction_read_only=on"


def test_the_audit_itself_ran_read_only():
    """The audit that cleared the data must have run as a role that cannot change it.

    Otherwise "no defects found" is worth very little: it could equally mean the
    audit quietly repaired whatever it disliked.
    """
    assert AUDIT_REPORT.exists(), (
        f"{AUDIT_REPORT} is missing. Run:\n"
        "  .venv/Scripts/python.exe backend/scripts/db/07_data_truth_audit.py"
    )
    report = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    assert report["read_only_role"] == "omniretail_readonly", report["read_only_role"]


def test_no_data_defects_are_known():
    """A defect recorded by the audit must block work, not sit in a report.

    Zero defects is the precondition for building on the data. This fails the
    moment somebody re-runs the audit and a real defect appears, which is the
    point: the next step should not be a FastAPI endpoint.
    """
    assert AUDIT_REPORT.exists(), f"{AUDIT_REPORT} is missing; run the audit first"
    report = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    defects = [f for f in report["findings"] if f["severity"] == "defect"]
    assert not defects, (
        "the audit recorded data defects - investigate before building on this data:\n"
        + "\n".join(
            f"  [{f['section']}] {f['subject']}: {f['message']}" for f in defects
        )
    )


def test_every_source_anomaly_is_classified_not_just_flagged():
    """An anomaly must say whether it is ours or the publisher's.

    "1,359 orders have an impossible date order" is alarming and useless. "1,359,
    and the raw source has 1,359, so the ETL is faithful and the source is
    imperfect" is a fact a KPI can be built on top of. Anything in the
    source_corroboration section that has not been checked against the raw file
    is a pipeline defect by definition.
    """
    report = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    corroboration = report.get("source_corroboration", {})
    assert corroboration, "no anomalies were corroborated against the raw files"

    unexplained = [
        subject for subject, evidence in corroboration.items()
        if not evidence.get("agree")
    ]
    assert not unexplained, (
        "these anomalies are NOT reproduced by the raw source, so they were "
        "introduced somewhere in this pipeline:\n  " + "\n  ".join(unexplained)
    )


def test_cross_source_joins_are_impossible_in_the_data():
    """The structural guarantee behind the project's hardest rule.

    An AI that decides `olist.customers JOIN dunnhumby.households ON
    customer_id = household_key` is asserting that Olist customer 123 and
    dunnhumby household 123 are the same person. They are not, and there is no
    foreign key that could say otherwise - so the query has no verified meaning
    and must never produce a number.
    """
    offenders = []
    for table in Base.metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            for element in constraint.elements:
                if element.column.table.schema != table.schema:
                    offenders.append(
                        f"{table.schema}.{table.name} -> {element.column.table.schema}"
                    )
    assert not offenders, f"cross-source foreign keys exist: {offenders}"


def review_findings_rows(report: dict) -> int | None:
    """Extract the recorded review_id-level fan-out surplus from the findings.

    Parses the number AFTER the word "surplus". Taking the first number in the
    message instead would return the 789 duplicate count, which is a different
    measurement entirely.
    """
    for finding in report["findings"]:
        message = finding.get("message", "")
        if "reviews" in str(finding.get("subject", "")) and "surplus" in message:
            tail = message.split("surplus", 1)[1]
            for token in re.findall(r"[\d,]+", tail):
                return int(token.replace(",", ""))
    return None


def test_known_fanouts_are_documented_so_the_ai_can_be_told_about_them():
    """Every join that multiplies must be a recorded fact, not a surprise.

    `olist.reviews` keyed on review_id alone would double-count per-order review
    metrics. The fix is to know the grain before writing the query, which means
    the grain has to be retrievable - this is the raw material the RAG layer
    will retrieve.

    The subtlety: `duplicate_groups` is 0 here, and that is CORRECT. The grain is
    (review_id, order_id) and that pair is unique, so there are no duplicates at
    the declared grain. The fan-out lives one level up - 789 review_ids attach
    to more than one order. An auditor who checked only `duplicate_groups`
    would wrongly conclude the table is clean.
    """
    report = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    fanout = report.get("fanout", [])
    assert fanout, "no join fan-out was measured"

    reviews = next((f for f in fanout if "reviews" in f["join"]), None)
    assert reviews is not None, "the olist.reviews fan-out was not measured"
    assert reviews["rows_per_key"] > 1.0, (
        "olist.reviews no longer fans out; the documented review-grain finding "
        "needs revisiting"
    )

    grain = report["grain"]["olist.reviews"]
    assert grain["behaviour"] == "fanout", (
        "olist.reviews is no longer declared as a fan-out grain"
    )
    assert grain["grain_columns"] == ["review_id", "order_id"], grain["grain_columns"]
    assert grain["duplicate_groups"] == 0, (
        f"(review_id, order_id) is no longer unique - {grain['duplicate_groups']} "
        f"duplicate groups at the declared grain"
    )
    # The fan-out probe counts distinct *order_id* (98,673), giving a surplus of
    # 551 rows. The documented 814 is the surplus on *review_id* (98,410), which
    # is the level at which the double-counting actually happens. Two different
    # denominators, both correct - so the test asserts the finding, not the probe.
    surplus = review_findings_rows(report)
    assert surplus == 814, f"the review fan-out surplus changed from 814 to {surplus}"

    # and the documented 789 must still be traceable to a finding
    review_findings = [
        f for f in report["findings"]
        if "reviews" in str(f.get("subject", ""))
    ]
    assert any("789" in f["message"] for f in review_findings), (
        "the 789 duplicated review_ids is no longer recorded in any finding"
    )


def test_keyless_table_is_still_keyless():
    """dunnhumby.coupon_redemptions must have no key, in the models or the database.

    An AI asked "how many redemptions are there" must be told the answer is
    2,318 ROWS, not a distinct count under an invented key. The moment somebody
    adds a surrogate primary key to make an ORM happy, that distinction becomes
    invisible.
    """
    table = Base.metadata.tables["dunnhumby.coupon_redemptions"]
    assert list(table.primary_key.columns) == [], "a key appeared on coupon_redemptions"

    report = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    grain = report["grain"]["dunnhumby.coupon_redemptions"]
    assert grain["verdict"] == "unverified", grain
    assert grain["candidate_key_duplicate_groups"] == 14, grain


# ==============================================================================
# PART 2 - CONTROLS THAT MUST EXIST BEFORE THE AI LAYER IS BUILT
# ==============================================================================

REQUIRED_CONTROLS = {
    "sql_validator": {
        "must_reject": list(FORBIDDEN_KEYWORDS),
        "implemented": True,
        "proving_test": "test_validator_rejects_every_forbidden_statement",
        "implemented_in": "backend/app/ai/sql_validator.py",
        "why": (
            "Generated SQL is model output, and model output is untrusted input. "
            "PostgreSQL's read-only role is the backstop; the validator is the "
            "control that produces a clear REJECTION rather than a database error, "
            "and the one that catches a write smuggled inside a CTE or a "
            "data-modifying CTE the role would otherwise permit."
        ),
        "test_when_built": (
            "test_validator_rejects_every_forbidden_statement - and it must run the "
            "same keyword list that is here, so a keyword cannot be dropped from one "
            "and not the other."
        ),
    },
    "result_verification": {
        "must_reject": [
            "a stated number absent from the executed result rows",
            "an answer with no executed query behind it",
            "a forecast with no evaluated model behind it",
            "an anomaly with no detection run behind it",
        ],
        "implemented": True,
        "proving_test": "test_answer_numbers_all_appear_in_the_query_result",
        "implemented_in": "backend/app/ai/verification.py",
        "why": (
            "This is the rule the whole project turns on. The existing mechanism - "
            "test_analytics_findings.py failing the build when a documented figure is "
            "not in _sql_results.json - is the template. The AI layer needs the same "
            "check at ANSWER time rather than at build time."
        ),
        "test_when_built": (
            "test_answer_numbers_all_appear_in_the_query_result - answer a question, "
            "parse every number out of the answer, assert each is present in the rows."
        ),
    },
    "evidence_requirement": {
        "must_reject": [
            "a business rule stated without a retrieved document",
            "a KPI quoted without its definition",
            "a column meaning inferred rather than read from the data dictionary",
        ],
        "implemented": True,
        "proving_test": "test_unanswerable_question_returns_no_factual_claim",
        "implemented_in": "backend/app/ai/retrieval.py + analyst.py",
        "why": (
            "'Do not invent undocumented business rules' is only meaningful if there "
            "is a step where absence of evidence ends the answer instead of inviting "
            "a guess. The corpus exists (docs/*.md, _sql_results.json, "
            "_checksums.json); the retrieval gate does not."
        ),
        "test_when_built": (
            "test_unanswerable_question_returns_no_factual_claim - ask something the "
            "corpus does not cover, assert the response contains no number and no "
            "definitive claim."
        ),
    },
    "false_premise_handling": {
        "must_reject": [
            "'total revenue for OmniRetail' - four unrelated companies",
            "'olist customer 123 and dunnhumby household 123 are the same person'",
            "'the dunnhumby coupon_redemptions primary key' - there is none",
            "'repeat purchasers in olist' as if the table were people",
        ],
        "implemented": True,
        "proving_test": "test_false_premises_are_corrected_not_confirmed",
        "implemented_in": "backend/app/ai/guards.py",
        "why": (
            "Every one of these is a question a user will actually ask, and each has "
            "a fluent, confident, wrong answer available. This is the highest-value "
            "control in the whole AI layer and the easiest to skip."
        ),
        "test_when_built": (
            "test_false_premises_are_corrected_not_confirmed - each of the above must "
            "be refused or corrected with the reason, never answered."
        ),
    },
    "cross_source_guard": {
        "must_reject": [
            "any generated query joining two different source schemas",
            "any generated query joining through the analytics schema without "
            "an explicit source_system predicate",
        ],
        "implemented": True,
        "proving_test": "test_generated_sql_joining_two_sources_is_rejected",
        "implemented_in": "backend/app/ai/sql_validator.py + nl_to_sql.py",
        "why": (
            "The schema makes this impossible to express as a foreign key, so it has "
            "to be a rule about generated SQL. A join is legal SQL and would return "
            "rows; only a rule stops it being quoted as a finding."
        ),
        "test_when_built": (
            "test_generated_sql_joining_two_sources_is_rejected"
        ),
    },
}


def test_the_required_control_list_is_not_empty():
    """Guard against the list being emptied to make a phase look further along."""
    assert REQUIRED_CONTROLS, "REQUIRED_CONTROLS was emptied"
    for name, spec in REQUIRED_CONTROLS.items():
        assert spec.get("must_reject"), f"{name} has no stated rejection cases"
        assert spec.get("why"), f"{name} has no stated reason"
        assert spec.get("test_when_built"), f"{name} has no stated test"


def test_no_control_is_marked_implemented_without_proving_it():
    """Implemented means PROVEN, not present.

    The original version of this test asserted every control was NOT
    implemented, as a tripwire against a control being quietly retired. Now that
    all five exist it asserts the opposite discipline: a control may be marked
    `implemented` only if it also names a `proving_test` that is really
    defined. So the tripwire is still a tripwire - it now stops somebody
    flipping a flag to make a report look better.
    """
    import test_ai_controls  # noqa: PLC0415 - deliberate, to avoid a cycle

    for name, spec in REQUIRED_CONTROLS.items():
        if spec.get("implemented"):
            proving = spec.get("proving_test")
            assert proving, (
                f"{name} is marked implemented but names no proving_test"
            )
            assert hasattr(test_ai_controls, proving), (
                f"{name} claims {proving!r} proves it, but that test does not exist"
            )


def test_every_control_is_now_implemented():
    """All five were specified before the AI layer and all five were built.

    This is the check that the specification was actually discharged rather
    than quietly reduced. If somebody removes a control from `REQUIRED_CONTROLS`
    instead of implementing it, `test_every_required_control_has_a_proving_test`
    in `test_ai_controls.py` fails, because that file asserts the two lists are
    equal.
    """
    not_implemented = [
        name for name, spec in REQUIRED_CONTROLS.items()
        if not spec.get("implemented")
    ]
    assert not not_implemented, (
        f"these required controls are still not implemented: {not_implemented}"
    )


def test_the_keyword_list_covers_every_statement_the_audit_probed():
    """The validator's future keyword list must match what was actually tested.

    The audit probed nine statements against the live database. If the future
    validator's list does not cover the same ground, the two will drift and the
    validator will look thorough while missing something.
    """
    report = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    probed = set(report.get("write_probes", {}))
    covered = {keyword for keyword in REQUIRED_CONTROLS["sql_validator"]["must_reject"]}
    assert probed, "the audit probed no write statements"
    missing = {p for p in probed if p.lower() not in covered}
    assert not missing, (
        f"the audit probed {sorted(missing)} but the required validator keyword "
        f"list does not cover them"
    )


def test_the_audit_refused_every_write_it_tried():
    """The backstop control is only a control if it actually refuses."""
    report = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    probes = report.get("write_probes", {})
    assert len(probes) == 9, f"expected 9 probes, found {len(probes)}"
    not_refused = {k: v for k, v in probes.items() if not v.startswith("refused")}
    assert not not_refused, f"the database accepted these writes: {not_refused}"


# ==============================================================================
# PART 3 - THE CONTRACT, WRITTEN WHERE A LLM PROMPT WILL SEE IT
# ==============================================================================

CONTRACT_FILE = REPO_ROOT / "backend" / "app" / "ai" / "GROUNDING_CONTRACT.md"


def _normalise(value: str) -> str:
    """Lowercase, drop markdown emphasis and punctuation, collapse whitespace.

    Lets a test match prose without dictating its exact wording.
    """
    text = re.sub(r"[*_`#>]", " ", value.lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return f" {text.strip()} "


def test_the_contract_is_written_and_covers_every_required_control():
    """Each required control must appear in a document the AI layer will load.

    A rule that lives only in a test is a rule the prompt builder cannot see.
    This exists to force the two to stay in step.
    """
    assert CONTRACT_FILE.exists(), (
        f"{CONTRACT_FILE} is missing. The AI layer loads this file; a control that "
        "is not in it cannot be prompted for."
    )
    # Normalised: the contract is prose with markdown emphasis, so "sql_validator"
    # is written as "**SQL validator**". Compare on normalised text, and also
    # accept the underscore form so the test does not force awkward wording.
    text = _normalise(CONTRACT_FILE.read_text(encoding="utf-8"))
    for name in REQUIRED_CONTROLS:
        keyword = _normalise(name.replace("_", " "))
        assert keyword in text or _normalise(name) in text, (
            f"the grounding contract does not mention '{keyword}', which "
            f"REQUIRED_CONTROLS says must be enforced"
        )


def test_the_contract_states_the_core_rule():
    """The one sentence everything else serves."""
    text = CONTRACT_FILE.read_text(encoding="utf-8").lower()
    assert "no verified evidence" in text, (
        "the contract must state the rule it serves: no verified evidence, no "
        "factual answer"
    )
    assert "llm" in text and "not the source of truth" in text, (
        "the contract must state that the model itself is not a source of truth"
    )


@pytest.mark.parametrize(
    "forbidden",
    ["omnitotal revenue", "omni retail total revenue"],
)
def test_forbidden_phrases_are_named_in_the_contract(forbidden):
    """'OmniRetail Total Revenue' must be called out by name, not left implied.

    Four unrelated companies with no shared key space cannot be summed. The one
    time this matters is when someone asks for exactly that number, and the
    fluent answer is available.
    """
    text = CONTRACT_FILE.read_text(encoding="utf-8").lower()
    assert forbidden in text, (
        f"the grounding contract does not name '{forbidden}' as a forbidden "
        f"question, which is where the hallucination would come from"
    )


def test_the_contract_lists_the_known_false_premises():
    """Each verified finding that is a trap must be in the contract verbatim."""
    text = CONTRACT_FILE.read_text(encoding="utf-8").lower()
    required = [
        "(review_id, order_id)",
        "coupon_redemptions",
        "801",
        "96.88",
    ]
    missing = [needle for needle in required if needle.lower() not in text]
    assert not missing, (
        f"the grounding contract omits these verified findings, which are exactly "
        f"the things a model will confidently get wrong: {missing}"
    )
