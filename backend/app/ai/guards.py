"""Refuse the questions that have a fluent, confident, wrong answer.

WHY THIS IS A RULE TABLE AND NOT A MODEL
-----------------------------------------
Each entry is not a judgement. It is a verified fact recorded in a document that
exists, mapped from a specific phrasing to a specific correction with a
citation.

A model asked "how many dunnhumby customers are there?" answers 2,500 about half
the time, because 2,500 is in the publisher's own marketing material and in the
transaction file. The correct answer - 801, the households the demographics file
actually covers - requires knowing that the two files describe different
populations. That is not linguistic; it is a fact about these four datasets, and
only a fact store can enforce it.

THE COST OF NOT HAVING THIS
---------------------------
A refusal is unhelpful. Confirming a false premise is worse: it makes the error
authoritative, and the user repeats it downstream with more confidence than
before. "The total revenue across OmniRetail is $X" travels further than
"there is no total revenue across OmniRetail".

HOW A CORRECTION IS ADMISSIBLE
------------------------------
Every correction here carries a `citation` pointing at a committed file, and
`test_false_premise_corrections_are_cited` fails the build if one does not. A
correction the analyst cannot point at is indistinguishable from an opinion, and
this module's whole claim is that it is not.

The citation's `#L<n>` is the line of `GROUNDING_CONTRACT.md` that states the
fact, and `test_guard_citations_still_point_at_the_contract` re-resolves every
one of them. Line citations rot the moment that document is edited - a stale
line number is worse than no citation, because it looks checked. If you edit
`GROUNDING_CONTRACT.md`, expect that test to fail and fix the numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Premise:
    """One verified trap: how to spot it, and what is actually true."""

    name: str
    pattern: re.Pattern[str]
    correction: str
    citation: str
    severity: str = "refuse"  # "refuse" or "qualify"


@dataclass
class GuardResult:
    """What the guard decided, and why."""

    triggered: bool
    matched: list[str] = field(default_factory=list)
    corrections: list[dict[str, str]] = field(default_factory=list)
    question: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "matched_rules": self.matched,
            "corrections": self.corrections,
        }

    def render(self) -> str:
        """The refusal, formatted for a user."""
        return "\n\n".join(c["correction"] for c in self.corrections)


def _p(body: str) -> re.Pattern[str]:
    return re.compile(body, re.IGNORECASE)


# ==============================================================================
# THE RULES
# ==============================================================================
#
# Ordering is by specificity, not severity: the first rule that matches wins for
# reporting, but every rule that matches is reported, because a question can
# carry two traps at once ("what's the total olist revenue from repeat
# purchasers") and correcting only one leaves the other standing.

RULES: tuple[Premise, ...] = (
    # -- 1. THE question with no referent -----------------------------------
    Premise(
        name="combined_total",
        pattern=_p(
            r"\b(omnitotal|omni\s*retail|omni-retail|across\s+all\s+(four|4)\s+"
            r"(datasets?|sources?|companies|companies\.|schemas?))"
            r"|\b(total|combined|overall|grand)\s+"
            r"(revenue|sales|orders?|customers?|transactions?|units?)\s+"
            r"(across|between|of)\s+"
            r"(all|the\s+four|both|every)\b"
            r"|\b(all|every|total)\s+(four|4)\s+"
            r"(datasets?|sources?|companies|companies\.|schemas?)\b"
            r"|\b(total|combined)\s+(omni\w*|everything|all\s+data)\b"
        ),
        correction=(
            "There is no OmniRetail total. These are four datasets from four "
            "unrelated companies: Olist (a Brazilian marketplace), DataCo (supply "
            "chain), dunnhumby (a US grocery panel) and M5 (Walmart daily sales). "
            "There is no shared entity, no shared key space and no transaction "
            "spanning more than one of them, so a sum across them has no referent - "
            "it is not that the total is hard to compute, it is that the number "
            "would mean nothing. No foreign key in this database crosses a schema "
            "boundary, which is how that is enforced structurally.\n\n"
            "I can answer this for any ONE source. Tell me which, and I will run "
            "the query and show the rows."
        ),
        citation="backend/app/ai/GROUNDING_CONTRACT.md#L48",
    ),

    # -- 2. the same person in two datasets ----------------------------------
    Premise(
        name="cross_source_identity",
        pattern=_p(
            r"\b(same|both)\b[^.?]{0,40}\b(olist|dunnhumby|dataco|m5)\b[^.?]{0,60}"
            r"\b(olist|dunnhumby|dataco|m5)\b"
            r"|\b(match|matching|link|join|overlap|shared)\w*\b[^.?]{0,40}"
            r"\b(olist|dunnhumby|dataco|m5)\b[^.?]{0,60}"
            r"\b(olist|dunnhumby|dataco|m5)\b"
            r"|\bcross[- ]?(source|dataset)\b"
        ),
        correction=(
            "No customer can be identified across two of these datasets, and no "
            "query can do it either. There is no shared identifier and no verified "
            "lineage between them - `olist.customers.customer_id` and "
            "`dunnhumby.households.household_key` are unrelated integer sequences "
            "from different systems. Joining them on an integer asserts an identity "
            "that does not exist, which is why this project forbids cross-source "
            "joins rather than merely discouraging them.\n\n"
            "If you mean \"how do the two populations compare\", that is a "
            "per-source question answered one source at a time - and say which."
        ),
        citation="backend/app/ai/GROUNDING_CONTRACT.md#L93",
    ),

    # -- 3. a key that does not exist ----------------------------------------
    Premise(
        name="coupon_redemptions_key",
        # `.` must stay inside the gap class here: the schema-qualified spelling
        # `dunnhumby.coupon_redemptions` is the one a user types, and a `[^.?]`
        # gap silently misses it.
        pattern=_p(
            r"(primary\s+key|pk|unique\s+key|unique\s+id)\b[^?]{0,40}"
            r"coupon_redemption"
            r"|coupon_redemption\w*\b[^?]{0,40}"
            r"(primary\s+key|pk|unique\s+key|how\s+many\s+unique|"
            r"count\s+distinct|is\s+unique)"
            # reverse word order: "count distinct ... in coupon_redemptions"
            r"|(how\s+many\s+unique|count\s+distinct)\b[^?]{0,40}"
            r"coupon_redemption"
        ),
        correction=(
            "`dunnhumby.coupon_redemptions` has NO primary key, and I did not give "
            "it one. The candidate key `(household_key, day, coupon_upc)` was "
            "tested against the real rows and REJECTED: it leaves 14 duplicate "
            "groups. Inventing a surrogate key would have made the table look "
            "tidy while making every distinct-count wrong.\n\n"
            "So the only honest row count is `count(*)` = 2,318 rows. A "
            "`count(DISTINCT ...)` over the candidate key would need the 14 "
            "duplicate groups collapsed, and no verified rule allows that."
        ),
        citation="backend/app/ai/GROUNDING_CONTRACT.md#L74",
    ),

    # -- 4. the reviews grain ------------------------------------------------
    Premise(
        name="reviews_grain",
        # Both word orders occur: "review_id IS the primary key" and
        # "is review_id the primary key". One direction alone misses half of
        # the ways the question is actually asked.
        pattern=_p(
            r"\breview_id\b[^?]{0,40}\b(primary\s+key|pk|unique)\b"
            r"|\b(is|are|as)\b[^?]{0,20}\breview_id\b[^?]{0,20}"
            r"\b(primary\s+key|pk|unique|key)\b"
            r"|\b(how\s+many|number\s+of|count)\b[^?]{0,20}\breviews\b"
        ),
        correction=(
            "`review_id` is NOT the primary key of `olist.reviews`. The live key is "
            "`(review_id, order_id)`: 789 review_id values attach to more than one "
            "order. That is a fan-out, not a resubmission - identical text, "
            "identical score, identical both timestamps, and 32 attachments where "
            "the order was placed AFTER the review was written.\n\n"
            "The consequence is concrete: joining reviews to orders on `review_id` "
            "alone double-counts reviews and inflates per-order review metrics by "
            "814 rows. Any review count has to declare its grain."
        ),
        citation="backend/app/ai/GROUNDING_CONTRACT.md#L66",
    ),

    # -- 5. the two dunnhumby populations ------------------------------------
    Premise(
        name="dunnhumby_population",
        pattern=_p(
            r"(how\s+many|number\s+of|count\s+of)\b[^.?]{0,30}"
            r"(dunnhumby\s+)?(customer|household|shopper|panel|consumer)s?\b"
            r"|\b(dunnhumby)\b[^.?]{0,40}\bhow\s+many\b"
        ),
        correction=(
            "801, not 2,500 - and which one you get depends on which file you mean.\n\n"
            "`hh_demographic.csv` covers 801 households. 2,500 households appear in "
            "`transaction_data.csv`. This is the publisher's own coverage: the file "
            "is byte-identical to the one in their distribution zip, so nothing was "
            "truncated in download.\n\n"
            "The 1,699 households with no demographics are exactly the ones with a "
            "NULL `classification_3` in `household_rfm`. So every household-level "
            "statistic has to say which of the two populations it covers. Tell me "
            "which and I will run it."
        ),
        citation="backend/app/ai/GROUNDING_CONTRACT.md#L81",
    ),

    # -- 6. people where rows are ---------------------------------------------
    Premise(
        name="olist_customer_is_a_person",
        pattern=_p(
            r"\b(repeat|returning|loyal|recurring|frequent)\s+"
            r"(customer|buyer|shopper|purchaser)s?\b"
            r"|\b(customer|buyer|shopper)s?\b[^.?]{0,20}"
            r"\b(repeat|returning|loyal|recurring)\b"
            r"|\bhow\s+many\b[^.?]{0,20}\bolist\b[^.?]{0,20}customer"
        ),
        correction=(
            "`olist.customers.customer_id` is unique PER ORDER. It is not a person. "
            "The person is `customer_unique_id`, and 96.88% of people place exactly "
            "one order.\n\n"
            "So \"repeat purchasers in Olist\" cannot be answered off "
            "`customer_id` - that column says everyone is a first-time buyer. It "
            "can be answered off `customer_unique_id`, where the population is "
            "96,096 people rather than 96,096 orders. Which population did you mean?"
        ),
        citation="backend/app/ai/GROUNDING_CONTRACT.md#L89",
    ),

    # -- 7. competitor / market-share data that does not exist --------------
    # Not a trap in the sense the others are - it is simply a question about
    # data this project does not have, and the failure mode is the same: a
    # fluent answer assembled from prices that ARE here, implying a comparison
    # with prices that are not.
    #
    # `dataco.revenue_by_market` reports share by COUNTRY, and `m5.sell_prices`
    # reports the price THIS seller charges. Neither is a competitor's price,
    # and no table in any of the four schemas holds one. The correction says so
    # and offers what can be answered instead.
    Premise(
        name="competitor_data",
        pattern=_p(
            r"\bcompetitor\w*\b"
            r"|\brival\s+(price|pricing|company|competitor\w*)\b"
            r"|\b(market\s+share|share\s+of\s+market)\b"
            r"|\b(elasticity|price\s+elasticity)\s+of\s+demand\b"
            r"|\bwhat\s+do\s+(competitors|rivals)\b"
            r"|\bcompetitor\s+pricing\b"
        ),
        correction=(
            "No competitor, rival or market-share data exists in this project. "
            "The four sources are one marketplace (Olist), one supply chain "
            "(DataCo), one grocery panel (dunnhumby) and one seller's daily "
            "sales (M5). `m5.sell_prices` is what THIS seller charged; "
            "`dataco.revenue_by_market` is share by COUNTRY, not by rival.\n\n"
            "So a price-elasticity figure, a competitor price index or a rival "
            "comparison cannot be computed from this data at all - not "
            "approximately, not with a caveat. There is no column anywhere in "
            "the 30 tables that holds another seller's price.\n\n"
            "What I can do instead: M5's own price-to-demand relationship "
            "(`m5.price_and_demand`, weekly grain, because the price table is "
            "weekly and the daily facts collapse to match), and DataCo's "
            "discount-versus-margin breakdown. Ask for either."
        ),
        citation="backend/app/ai/GROUNDING_CONTRACT.md#L100",
    ),

    # -- 8. features this build genuinely does not have ----------------------
    # Forecasting is deliberately NOT in this rule. It used to be, back when
    # no model had been evaluated; it is now implemented and measured, so
    # refusing forecast questions would refuse the truth. The analyst answers
    # those from `_forecast_results.json` instead.
    Premise(
        name="not_implemented_feature",
        pattern=_p(
            r"\b(email|e-mail)\b[^?]{0,30}(send|sending|address(es)?\b)"
            r"|\b(send|schedule|email|cc|bcc)\b[^?]{0,20}"
            r"\b(me\s+an?\s+)?(email|e-mail|alert|notification|newsletter|report)\b"
        ),
        correction=(
            "This system has no outbound email, no scheduler and no alerting. It "
            "serves read-only analytics over a PostgreSQL database; the only way a "
            "number reaches you is because you asked for it here.\n\n"
            "Forecasting, anomaly detection and the SQL catalogue ARE implemented "
            "and evaluated, so questions about those are answerable - ask one and I "
            "will report the model or the detector, its metric and its baseline."
        ),
        citation="backend/app/ai/GROUNDING_CONTRACT.md#L257",
        severity="qualify",
    ),
)


def check(question: str) -> GuardResult:
    """Run every rule. Returns a result, never a string - the caller decides.

    All matching rules are reported, not just the first. A question can carry
    two traps simultaneously, and correcting one while leaving the other standing
    is how a false premise survives.
    """
    text = question or ""
    matched: list[str] = []
    corrections: list[dict[str, str]] = []

    for rule in RULES:
        if rule.pattern.search(text):
            matched.append(rule.name)
            corrections.append(
                {
                    "rule": rule.name,
                    "severity": rule.severity,
                    "correction": rule.correction,
                    "citation": rule.citation,
                }
            )

    return GuardResult(
        triggered=bool(matched),
        matched=matched,
        corrections=corrections,
        question=text,
    )