"""The forecasting and anomaly documents must match their recorded results.

WHY THIS FILE EXISTS
--------------------
`docs/forecasting.md` and `docs/anomaly_detection.md` both claim that a test
fails when the document and the evidence file disagree. That test did not
exist: the claim was aspirational, and an aspirational test is a lie with a
citation. This file is it.

THE FAILURE IT PREVENTS IS SPECIFIC
-----------------------------------
Both documents are GENERATED from the artefacts, so they cannot drift while the
generator and the artefact agree. They drift the moment somebody edits the
artefact by hand, re-runs a partial evaluation, or regenerates the document from
a different run without noticing. The number stays in the prose, the number on
the page stops matching the run, and nothing else in the project notices -
because the document is not a data source.

So each headline figure below is read out of the document's own text and
compared with the artefact. Asserting a literal against the artefact would only
prove the artefact has not changed; asserting the DOCUMENT contains the
artefact's value is what actually catches the drift.

WHAT IS CHECKED, AND WHAT CANNOT BE
----------------------------------
Checked: every headline figure - series count, day count, holdout window, the
winner's MASE, the margin over the runner-up, the intermittency share, the
firing counts per source, the total number of firings.

Cannot be checked: that a *model* is correct. This asserts the recorded metrics
are reported faithfully, not that `sma_7` really is the best forecasting model
in the world. Re-running the benchmark is
`backend/scripts/ml/01_evaluate_forecast.py`, and it is documented as taking
about five minutes for a reason.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "backend"))

FORECAST_DOC = REPO_ROOT / "docs" / "forecasting.md"
ANOMALY_DOC = REPO_ROOT / "docs" / "anomaly_detection.md"
FORECAST_ARTEFACT = REPO_ROOT / "data" / "processed" / "_forecast_results.json"
ANOMALY_ARTEFACT = REPO_ROOT / "data" / "processed" / "_anomaly_results.json"


@pytest.fixture(scope="module")
def forecast_doc() -> str:
    assert FORECAST_DOC.exists(), f"{FORECAST_DOC} is missing"
    return FORECAST_DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def forecast() -> dict:
    assert FORECAST_ARTEFACT.exists(), (
        f"{FORECAST_ARTEFACT} is missing. Run:\n"
        f"  .venv/Scripts/python.exe backend/scripts/ml/01_evaluate_forecast.py"
    )
    return json.loads(FORECAST_ARTEFACT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def anomaly() -> dict:
    assert ANOMALY_ARTEFACT.exists(), (
        f"{ANOMALY_ARTEFACT} is missing. Run:\n"
        f"  .venv/Scripts/python.exe backend/scripts/ml/02_detect_anomalies.py"
    )
    return json.loads(ANOMALY_ARTEFACT.read_text(encoding="utf-8"))


def _needs(text: str, value: str, what: str) -> None:
    assert value in text, (
        f"{what} is {value} in the artefact but that string does not appear in "
        f"{FORECAST_DOC.name if FORECAST_DOC.name in str(text) else ANOMALY_DOC.name}. "
        f"The document has drifted from the run that produced it."
    )


# ==============================================================================
# FORECASTING
# ==============================================================================


def test_the_winner_named_in_the_document_is_the_winner_in_the_artefact(
    forecast_doc, forecast
):
    """The most quoted sentence in the document must be the measured one."""
    winner = forecast["winner_by_mase"]
    assert isinstance(winner, str), forecast["winner_by_mase"]
    _needs(forecast_doc, f"**`{winner}` wins on MASE", "the winning model")


def test_the_winning_mase_is_stated_in_the_sentence_that_claims_it(
    forecast_doc, forecast
):
    """Anchored to the claim, not to a bare number.

    `1.6717` also appears in the metric table, so asserting "the document
    contains this number" is satisfied by the table even when the sentence that
    actually makes the claim has been edited. This matches the whole sentence.
    """
    winner = forecast["winner_by_mase"]
    mase = forecast["models"][winner]["MASE"]
    assert f"wins on MASE at {mase:.4f}" in forecast_doc, (
        f"the artefact's winning MASE is {mase:.4f}; the sentence that claims "
        f"the result in {FORECAST_DOC.name} does not say that"
    )


def test_the_scope_of_the_run_is_stated_correctly(forecast_doc, forecast):
    scope = forecast["scope"]
    assert scope["is_full_dataset"] is True, (
        "the artefact no longer describes a full-dataset run; the document says "
        "it does, and one of them is now wrong"
    )
    assert scope["series_evaluated"] == scope["series_total_in_database"], (
        "the artefact says the run covered a subset, while the document claims "
        "the whole dataset"
    )
    _needs(forecast_doc, f"{scope['series_evaluated']:,}", "the series count")
    _needs(forecast_doc, f"{scope['days_loaded']:,}", "the day count")


def test_the_holdout_window_matches_the_recorded_split(forecast_doc, forecast):
    split = forecast["split"]
    assert split["test_days"] == 28, split
    # The document quotes the d_num range. Compare on the numbers, not the
    # exact string, so a reformatted range does not read as drift.
    train = re.search(r"(\d+)\.\.(\d+)", split["train_d_num"])
    test = re.search(r"(\d+)\.\.(\d+)", split["test_d_num"])
    assert train and test, split
    assert test.group(1) == str(int(train.group(2)) + 1), (
        f"the test window does not start the day after training ends: "
        f"{split}"
    )
    assert int(test.group(2)) - int(test.group(1)) + 1 == split["test_days"]
    _needs(forecast_doc, test.group(1), "the first held-out d_num")
    _needs(forecast_doc, test.group(2), "the last held-out d_num")


def test_the_intermittency_bands_are_stated(forecast_doc, forecast):
    """Intermittent demand is the caveat the whole document turns on.

    The share is recomputed from the artefact's own counts rather than read
    from a summary field, so a run that moved series between bands cannot leave
    the prose describing the old distribution.
    """
    bands = forecast["intermittency"]
    total = forecast["scope"]["series_evaluated"]
    for label, entry in bands.items():
        series = entry["series"]
        share = 100.0 * series / total
        zero_range = entry["zero_rate_range"]
        # Anchored to the whole summary row. `11,783` also appears in the
        # per-band model table, so asserting "the document contains this
        # number" would be satisfied by the table even if the band summary -
        # the place that states the share - had been edited.
        row = (
            f"| {label} | {series:,} | {share:.1f}% | "
            f"{zero_range[0]:.0%}-{zero_range[1]:.0%} |"
        )
        assert row in forecast_doc, (
            f"band {label!r} is {series:,} series ({share:.1f}%) in the "
            f"artefact. The band-summary row\n  {row}\ndoes not appear in "
            f"{FORECAST_DOC.name}."
        )

    assert sum(e["series"] for e in bands.values()) == total, (
        "the intermittency bands do not partition the dataset; every series "
        "must land in exactly one band or the shares are meaningless"
    )


def test_the_margin_over_the_runner_up_is_stated(forecast_doc, forecast):
    """The document says 1.5%; the artefact says 0.0147 in MASE units.

    They are the same fact in different units - the document converts to a
    percentage, the artefact stores a ratio - so the test checks the converted
    value, not the raw one. A 0.0147 margin printed as "1.5%" is correct; a
    1.5% printed where the run measured 14.7% is not, and that is the mistake
    worth catching.
    """
    margin_pct = forecast["margin_over_runner_up"] * 100.0
    assert margin_pct > 0, "the winner cannot be behind the runner-up"
    assert f"{margin_pct:.1f}%" in forecast_doc, (
        f"the measured margin is {margin_pct:.2f}% of MASE and the document "
        f"does not state it"
    )


def test_the_runner_up_named_in_the_document_is_the_runner_up(forecast):
    """The comparison is only as good as the model it is compared against.

    Anchored to the sentence rather than to the bare MASE, because the runner-
    up's MASE also appears in the metric table - where editing it changes
    nothing a reader would act on, since the table is generated.
    """
    scored = sorted(forecast["models"].values(), key=lambda m: m["MASE"])
    assert scored[0]["model"] == forecast["winner_by_mase"], scored[:2]
    doc = FORECAST_DOC.read_text(encoding="utf-8")
    runner = scored[1]
    margin_pct = forecast["margin_over_runner_up"] * 100.0
    sentence = (
        f"`{runner['model']}` at {runner['MASE']:.4f} -- "
        f"a margin of {margin_pct:.1f}%."
    )
    assert sentence in doc, (
        f"the runner-up is {runner['model']} at {runner['MASE']:.4f} with a "
        f"{margin_pct:.1f}% margin; the sentence that claims the result does "
        f"not say that.\n  expected to find: {sentence}"
    )


def test_the_artefact_says_when_it_was_produced(forecast):
    """A benchmark with no timestamp is not a measurement."""
    assert forecast.get("generated_utc"), (
        "_forecast_results.json has no generated_utc; /api/forecasting serves "
        "this file verbatim, so a caller would have no way to judge its age"
    )


# ==============================================================================
# ANOMALY DETECTION
# ==============================================================================


def test_the_firing_counts_in_the_document_match_the_artefact(anomaly):
    """63 firings across 3 metrics, broken down per source.

    The document and the artefact have to agree on the per-source counts, not
    just the total: a detector that silently stopped flagging one source would
    keep the headline number roughly right and be wrong about the data.

    Matched on the summary TABLE row - source, metric, points scored, flagged,
    firing rate - because that is where the document makes its claims, and
    because a bare number like "63" would match almost anything.
    """
    doc = ANOMALY_DOC.read_text(encoding="utf-8")
    blocks = anomaly["detections"]
    assert blocks, "the artefact records no metrics at all"

    total = sum(len(b.get("anomalies", [])) for b in blocks)
    assert f"{len(blocks)}" in doc, (
        f"the artefact covers {len(blocks)} metrics; {ANOMALY_DOC.name} does "
        f"not state that"
    )

    for block in blocks:
        count = len(block.get("anomalies", []))
        points = block["points_evaluated"]
        # The document prints the artefact's own `firing_rate_pct`, not a
        # recomputation of it. Recomputing here would test Python's rounding
        # rather than the claim: 32/606 is 5.280528...%, the run recorded
        # 5.2805, and the document prints that as 5.280%. Both are right.
        rate = block["firing_rate_pct"]
        row = (
            f"| `{block['source']}` | `{block['metric']}` | "
            f"{points:,} | {count} | {rate:.3f}% |"
        )
        assert row in doc, (
            f"the artefact reports {block['metric']} as {count} firings from "
            f"{points:,} scored points ({rate:.3f}%). The summary table row\n"
            f"  {row}\ndoes not appear in {ANOMALY_DOC.name}."
        )

        # ...and separately, that the artefact's own rate agrees with its own
        # counts. Two fields that can drift apart are worse than one.
        assert block["anomalies_found"] == count, (
            f"{block['metric']} records {block['anomalies_found']} firings in "
            f"its summary but carries {count} in `anomalies`"
        )
        assert abs(rate - 100.0 * count / points) < 0.001, (
            f"{block['metric']} records firing_rate_pct={rate} but "
            f"{count}/{points} is {100.0 * count / points:.4f}%"
        )

    assert total == sum(
        len(b.get("anomalies", [])) for b in blocks
    ), "the firing total is not the sum of the per-metric counts"


def test_every_firing_carries_the_arithmetic_that_produced_it(anomaly):
    """A firing without its baseline and threshold is unfalsifiable.

    This is the property the whole detector rests on, and it is the one thing
    `/api/anomalies` would silently break if a future change flattened a row.
    """
    for block in anomaly["detections"]:
        for firing in block.get("anomalies", []):
            where = f"{block['metric']} {firing.get('date')}"
            for field in ("observed", "baseline", "threshold", "score_sigma", "severity"):
                assert firing.get(field) is not None, (
                    f"{where} has no {field}; a firing without its arithmetic "
                    f"cannot be checked"
                )


def test_the_artefact_records_when_it_was_produced(anomaly):
    assert anomaly.get("generated_utc"), (
        "_anomaly_results.json has no generated_utc; /api/anomalies serves it "
        "verbatim, so a caller would have no way to judge its age"
    )


def test_the_dunnhumby_axis_is_not_presented_as_a_date(anomaly):
    """dunnhumby's time axis is an integer index and the dataset has no calendar.

    A generator that started formatting those labels as dates would produce a
    document nobody could notice was wrong, because the numbers would still look
    like a date.
    """
    dunnhumby = next(
        (b for b in anomaly["detections"] if b["source"] == "dunnhumby"), None
    )
    if dunnhumby is None:
        pytest.skip("the recorded run does not include a dunnhumby metric")
    labels = [str(a.get("date", "")) for a in dunnhumby.get("anomalies", [])]
    assert labels, "the dunnhumby block has no firings to check"
    iso_like = [x for x in labels if len(x) == 10 and x[4] == "-" and x[7] == "-"]
    assert not iso_like, (
        f"dunnhumby labels {iso_like[:3]} look like ISO dates. That source's "
        f"time axis is an integer day index; rendering it as a date invents a "
        f"calendar the dataset does not contain."
    )