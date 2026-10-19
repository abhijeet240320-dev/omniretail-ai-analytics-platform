"""`result_verification`: no number reaches a user unless a query returned it.

THE RULE, IN CODE
-----------------
    Every number in an answer must appear in the rows a query returned in THIS
    request. Not from memory, not from the model, not from a previous answer,
    not from a document that itself needs checking.

This is the runtime twin of `backend/tests/test_analytics_findings.py`, which
does the same check at BUILD time against `data/processed/_sql_results.json`.
That test already fails the build when a documented figure is not in a recorded
result. This one runs on every answer the API produces.

WHY A LIST-MEMBERSHIP CHECK AND NOT A PARSER
--------------------------------------------
`extract_numbers` pulls every numeric token out of the answer, normalises it to
a canonical form, and asserts membership in the values actually returned. It is
deliberately crude, because a clever number-parser has a way of missing the
number you built it to miss.

The trade is known and stated: a number the check cannot parse (a year written
as "two thousand", a percentage written as "about a third") is not verified.
So the analyst does not produce prose with numbers in it. It produces
structured claims, and every claim's value is checked here against the rows.
Prose is rendered from claims, never the other way round.

WHAT MAKES A CLAIM FAIL
------------------------
  * a value with no source rows            -> `unsourced`
  * a value present but as a DIFFERENT quantity (same digits, different
    column) -> this is why claims carry a `column`: 5 meaning "5 states" is
    not evidence for "5 orders"
  * a forecast with no evaluated model     -> `unevaluated_forecast`
  * an anomaly with no detection run       -> `unrun_anomaly`

THE FOUR REJECTIONS REQUIRED BY THE CONTRACT
--------------------------------------------
`backend/tests/test_ai_grounding_contract.py::REQUIRED_CONTROLS` names four:

  "a stated number absent from the executed result rows"
  "an answer with no executed query behind it"
  "a forecast with no evaluated model behind it"
  "an anomaly with no detection run behind it"

Each has a function here and a test named in `test_ai_controls.py`. The tests
run the SAME keyword list as the contract, so the two cannot drift.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
FORECAST_RESULTS = REPO_ROOT / "data" / "processed" / "_forecast_results.json"
ANOMALY_RESULTS = REPO_ROOT / "data" / "processed" / "_anomaly_results.json"

# Numbers written with a thousands separator, a decimal point, a percent sign,
# or a currency symbol. Deliberately does NOT try to parse spelled-out numbers:
# an unverifiable number must fail, not slip past as "not a number".
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"[-+]?"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d+)?"
    r"%?"
    r"(?![A-Za-z0-9_])"
)

# A date is not a measurement. `2011-01-29T00:00:00` contains six numeric tokens
# and none of them is a fact about the data; parsing them would fail a correct
# answer for containing a date, which is how a verifier gets switched off.
#
# ISO-8601 date, optional `T`/space and time, optional fractional seconds and
# optional offset. Also US and day-first slash dates, because the source data
# carries both.
_DATE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
)

# Bare 4-digit years, exempted from verification for the same reason but kept
# VISIBLE: a date is excluded from the claim check and reported, rather than
# excluded from the output.
_YEAR_RE = re.compile(r"^(1[89]\d{2}|20\d{2})$")


def normalise(value: Any) -> set[str]:
    """Every form under which a returned value should be accepted.

    `1234` from PostgreSQL may be `1234`, `1234.0`, `"1,234"` or `"1234"`. All
    of them mean the same number, and a check that only accepted one of them
    would fail a correct answer - which trains people to ignore it.
    """
    if value is None or isinstance(value, bool):
        return set()
    out: set[str] = set()
    try:
        number = float(value)
    except (TypeError, ValueError):
        text = str(value).strip().replace(",", "")
        try:
            number = float(text)
        except ValueError:
            return {str(value).strip()}
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return set()

    out.add(_fmt(number))
    # A whole number returned as a float still has to match "42".
    if number == int(number):
        out.add(str(int(number)))
    return out


def _fmt(number: float) -> str:
    """Render a float as PLAIN DECIMAL, never scientific notation.

    This function is the single canonical renderer: `verification` uses it to
    normalise values, and `analyst.g_format` calls it rather than formatting
    independently. That is not tidiness, it is correctness.

    The two disagreed once. `g_format` used `f"{value:.6g}"`, which turned a
    revenue of 5,998,230.12 into the string `5.99823e+06`; the number extractor
    then parsed the exponent's digits as three separate numbers - 5, 9, 9, 8,
    2, 3 - and the verification step failed on a query that had run correctly
    and returned correct rows.

    Scientific notation in a table of business figures is wrong on its own
    terms. It is unreadable, and it destroys the digit string a reader would
    check the number against.
    """
    if number != number or number in (float("inf"), float("-inf")):
        return ""
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    if abs(number) >= 1e16 or (number != 0 and abs(number) < 1e-4):
        # `repr` switches to scientific notation here. Plain decimal at these
        # magnitudes is 16+ digits of noise, so the exponent form is the more
        # readable of the two. Neither end is reachable in this dataset - the
        # guard exists so an unexpected value degrades legibly rather than
        # printing a screenful of zeros.
        return f"{number:.6g}"
    # `repr` is the SHORTEST string that parses back to the same float, which
    # is exactly the property needed here: whatever the prose prints, the
    # number extractor reads back to the identical value and matches it against
    # the cell.
    #
    # Significant digits, not decimal places. `f"{17113870.54:.10f}"` is ten
    # DECIMAL places, which printed 17113870.539999999 for a value that is
    # exactly that, and produced a digit string that appears nowhere in the
    # result set.
    return repr(number)


# Public alias. The analyst's renderer calls this rather than formatting a
# float on its own; see `_fmt` for why two formatters would drift.
format_number = _fmt


def extract_numbers(text: str) -> list[str]:
    """Every numeric token in a string, in order, duplicates preserved.

    Returns normalised forms so the caller can compare against `normalise`.

    Dates are blanked out first. `m5.store_comparison` returns `first_date` and
    `last_date` columns, so its rendered table is full of
    `2011-01-29T00:00:00` - which parsed as 2011, 1, 29, 0, 0, 0 and failed
    verification on 60 values in a query that had run perfectly.
    """
    found: list[str] = []
    scrubbed = _DATE_RE.sub(" ", text or "")
    for match in _NUMBER_RE.finditer(scrubbed):
        token = match.group(0)
        bare = token.rstrip("%").replace(",", "")
        try:
            number = float(bare)
        except ValueError:
            continue
        if number != number:
            continue
        if _YEAR_RE.match(bare) and "." not in bare:
            # A bare 4-digit year is usually a date, not a measurement. Kept in
            # the output but flagged, because dropping it silently would let
            # "in 2016 there were 2,318 redemptions" pass unverified on the 2,318.
            found.append(f"year:{bare}")
            continue
        found.append(_fmt(number))
    return found


@dataclass
class Claim:
    """One asserted value, and what it is asserted about.

    `column` is what makes "5" checkable. Five states and five orders produce
    the same digits, and without the column the check would accept either.
    """

    text: str
    value: Any
    column: str | None = None
    kind: str = "measurement"  # measurement | forecast | anomaly | count
    source: str = ""            # the query or report that produced it

    def forms(self) -> set[str]:
        return normalise(self.value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "value": self.value,
            "column": self.column,
            "kind": self.kind,
            "source": self.source,
        }


@dataclass
class VerificationResult:
    ok: bool
    checked: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checked": self.checked, "failures": self.failures}


def _row_forms(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Every accepted numeric form, mapped to the columns that produced it."""
    table: dict[str, set[str]] = {}
    for row in rows:
        for column, value in row.items():
            for form in normalise(value):
                table.setdefault(form, set()).add(column)
    return table


def verify_claims(
    claims: list[Claim],
    rows: list[dict[str, Any]] | None,
    query_name: str,
) -> VerificationResult:
    """Every claim must be present in `rows`, in the column it names.

    `rows=None` means no query ran. Then EVERY measurement claim fails, which is
    the "an answer with no executed query behind it" rejection.
    """
    result = VerificationResult(ok=True, checked=[])

    if rows is None:
        for claim in claims:
            result.ok = False
            result.failures.append({
                "reason": "unsourced",
                "text": claim.text,
                "detail": (
                    f"no query executed, so {claim.value!r} in {claim.text!r} has "
                    f"nothing behind it"
                ),
            })
            result.checked.append(claim.as_dict())
        return result

    table = _row_forms(rows)
    for claim in claims:
        result.checked.append(claim.as_dict())
        forms = claim.forms()
        if not forms:
            result.ok = False
            result.failures.append({
                "reason": "unsourced",
                "text": claim.text,
                "detail": f"{claim.text!r} carries no numeric value to verify",
            })
            continue

        matched_column: str | None = None
        for form in forms:
            if form in table:
                columns = table[form]
                if claim.column is None or claim.column in columns:
                    matched_column = claim.column or sorted(columns)[0]
                    break

        if matched_column is None:
            result.ok = False
            present_in = {
                c for f in forms for c in table.get(f, set())
            }
            detail = (
                f"{claim.value!r} is not in any column of the {len(rows)} returned "
                f"row(s)"
            )
            if present_in:
                detail += (
                    f"; the same digits occur in {', '.join(sorted(present_in))}, "
                    f"which is a different measurement and does not support this "
                    f"claim"
                )
            elif claim.column:
                detail += f" (looked for column {claim.column!r})"
            result.failures.append({
                "reason": "unverified",
                "text": claim.text,
                "detail": detail,
            })

    result.ok = not result.failures
    return result


def _scrub_labels(text: str, labels: set[str]) -> str:
    """Blank out table cells whose ENTIRE content is a known label.

    `str.replace` does not work here, and the failure it causes is nasty.
    `dunnhumby.promo_exposure_coverage` returns a `display` column of TEXT
    CODES - `'0'`, `'6'`, `'A'`, `'D'` - which the source stores as characters.
    Scrubbing the label `'0'` with a global replace turns the adjacent cell
    `21038745` into `21 3874 5`, and the check then fails on three numbers it
    invented out of one it was supposed to protect.

    So labels are matched per CELL, on the whole trimmed cell, and only in
    markdown table rows where the cell boundary is unambiguous. A line that is
    not a table row is left completely alone.
    """
    if not labels:
        return text
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            cells = line.split("|")
            out.append("|".join(
                " " * len(cell) if cell.strip() in labels else cell
                for cell in cells
            ))
        else:
            out.append(line)
    return "\n".join(out)


def verify_prose(
    answer: str,
    rows: list[dict[str, Any]] | None,
    labels: set[str] | None = None,
) -> VerificationResult:
    """Check every number in free text against the returned rows.

    A safety net UNDER the structured claim check, not a replacement for it.
    It catches a number that reached the prose without going through a Claim,
    which is the failure mode that actually happens when an analyst grows
    features.

    `labels` is the set of NON-NUMERIC cell values the renderer put in the
    answer. Digits inside them are category names, not measurements:
    `dataco.discount_vs_margin` returns a `discount_band` column whose values
    are `"1. no discount"` and `"2. 1-14% off"`. Parsing the 1, the 2 and the 14
    as claims failed four correct queries, because a text extractor cannot tell
    a label from a measurement.

    Years are exempt, because a date is not a measurement - but the year count
    is reported in `checked` so an exempt year is visible rather than silently
    dropped.
    """
    if rows is None:
        return verify_claims(
            [Claim(text=answer, value=extract_numbers(answer), kind="measurement")],
            None,
            query_name="<none>",
        )

    labels = labels or set()
    scrubbed = _scrub_labels(answer, labels)

    claims: list[Claim] = []
    years: list[str] = []
    for token in extract_numbers(scrubbed):
        if token.startswith("year:"):
            years.append(token[5:])
        else:
            claims.append(
                Claim(
                    text=answer[:120], value=token, kind="measurement",
                    source="prose",
                )
            )
    result = verify_claims(claims, rows, query_name="<prose>")
    if years:
        result.checked.append(
            {"text": "years exempted as dates", "value": years,
             "column": None, "kind": "date", "source": "prose"}
        )
    return result


def label_values(
    columns: list[str], rows: list[dict[str, Any]]
) -> set[str]:
    """Every non-numeric cell value in the result, as a set of strings.

    These are the strings whose digits are NAMES rather than MEASURES. Feeding
    them to `verify_prose` is what lets a text-level check distinguish
    `"2. 1-14% off"` from a measurement of 14.
    """
    out: set[str] = set()
    for row in rows:
        for column in columns:
            value = row.get(column)
            if value is None or isinstance(value, bool):
                continue
            if isinstance(value, (int, float, Decimal)):
                continue
            text = str(value).strip()
            if text:
                out.add(text)
    return out


def verify_rendered_cells(
    rows: list[dict[str, Any]],
    columns: list[str],
    renderer: Any,
) -> VerificationResult:
    """Round-trip every numeric cell through the renderer that displayed it.

    This is the structural check, and it is stronger than the text one because
    it knows which cell a digit came from. For every numeric value the renderer
    must produce a string that parses back to exactly that value.

    It catches the two real failures this project hit: scientific notation
    (`5.99823e+06` for 5998226.96) and decimal-place formatting
    (17113870.539999999 for 17113870.54). Both produced digit strings that
    appear nowhere in the result set, and both would have shipped a correct
    number that no reader could verify.
    """
    result = VerificationResult(ok=True)
    for row in rows:
        for column in columns:
            value = row.get(column)
            if value is None or isinstance(value, bool):
                continue
            if not isinstance(value, (int, float, Decimal)):
                continue
            number = float(value)
            rendered = renderer(value).strip()
            parsed = extract_numbers(rendered)
            expected = normalise(number)
            # Direct string membership FIRST. `extract_numbers` exempts bare
            # 4-digit tokens as years, which is right for prose and wrong for a
            # CELL: a cell containing 1805 is a measurement of 1805, and the
            # exemption turned three correct queries into verification
            # failures with the baffling message that '1805' is not 1805.
            matched = rendered in expected or bool(set(parsed) & expected)
            result.checked.append({
                "text": f"{column}={rendered}", "value": number,
                "column": column, "kind": "cell", "source": "render",
            })
            if not matched:
                result.ok = False
                result.failures.append({
                    "reason": "unrenderable",
                    "text": f"{column}={rendered}",
                    "detail": (
                        f"the value {number!r} is rendered as {rendered!r}, which "
                        f"does not read back as the same number (expected one of "
                        f"{sorted(expected)}). A reader checking this figure "
                        f"against the JSON would not be able to."
                    ),
                })
    return result


# ==============================================================================
# FORECASTS AND ANOMALIES - "was the thing actually run?"
# ==============================================================================


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


@dataclass
class ArtifactVerdict:
    ok: bool
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "detail": self.detail}


def forecast_claim_is_evaluated(model: str) -> ArtifactVerdict:
    """A forecast may be reported only if that model was actually evaluated.

    Rejects three distinct failures:
      * no `_forecast_results.json` at all            -> never_run
      * the file exists but the scope was partial     -> not_full_dataset
      * the named model is not in the results         -> unevaluated_forecast
    """
    payload = _load(FORECAST_RESULTS)
    if payload is None:
        return ArtifactVerdict(
            False, "unevaluated_forecast",
            {
                "error": f"{FORECAST_RESULTS.name} does not exist",
                "how_to_fix": (
                    ".venv/Scripts/python.exe backend/scripts/ml/01_evaluate_forecast.py"
                ),
            },
        )

    scope = payload.get("scope", {})
    if not scope.get("is_full_dataset"):
        return ArtifactVerdict(
            False, "partial_forecast",
            {
                "error": (
                    f"only {scope.get('series_evaluated')} of "
                    f"{scope.get('series_total_in_database')} series were evaluated"
                ),
                "evaluated_utc": payload.get("generated_utc"),
            },
        )

    models = payload.get("models", {})
    if model not in models:
        return ArtifactVerdict(
            False, "unevaluated_forecast",
            {
                "error": f"model {model!r} is not in the results",
                "evaluated_models": sorted(models),
                "evaluated_utc": payload.get("generated_utc"),
            },
        )

    row = models[model]
    # The metrics file writes them UPPERCASE (`MASE`, `MAE`) because they are
    # metric names, and this function read them lowercase. Every metric came
    # back None, so the control that exists to stop an unevaluated forecast
    # being reported was refusing every evaluated one. Case is normalised here
    # rather than at the writer so a re-run that changes the casing cannot
    # silently disarm the check.
    lowered = {str(k).lower(): v for k, v in row.items()}
    if lowered.get("mase") is None:
        return ArtifactVerdict(
            False, "unevaluated_forecast",
            {
                "error": f"model {model!r} was evaluated but reports no MASE",
                "metrics_present": sorted(row),
            },
        )

    return ArtifactVerdict(
        True, "", {
            "model": model,
            "mae": lowered.get("mae"),
            "rmse": lowered.get("rmse"),
            "mase": lowered.get("mase"),
            "wmape": lowered.get("wmape"),
            "series_evaluated": scope.get("series_evaluated"),
            "test_days": payload.get("split", {}).get("test_days"),
            "evaluated_utc": payload.get("generated_utc"),
        },
    )


def anomaly_claim_is_detected(detector: str) -> ArtifactVerdict:
    """An anomaly may be reported only if that detector actually flagged it."""
    payload = _load(ANOMALY_RESULTS)
    if payload is None:
        return ArtifactVerdict(
            False, "unrun_anomaly",
            {
                "error": f"{ANOMALY_RESULTS.name} does not exist",
                "how_to_fix": (
                    ".venv/Scripts/python.exe backend/scripts/ml/02_detect_anomalies.py"
                ),
            },
        )

    # `02_detect_anomalies.py` writes `detections` as a LIST of DetectionResult
    # dicts, keyed by `source`. It was being read as a dict keyed by detector
    # name, so every lookup returned "not run" - the control refused all three
    # detectors including the ones that had just executed successfully.
    detections = payload.get("detections", [])
    if isinstance(detections, dict):  # tolerate the dict shape if it ever changes
        by_name = detections
    else:
        by_name = {d.get("metric"): d for d in detections if isinstance(d, dict)}

    # Accept either the metric name or the source name, because "which detector
    # ran?" is naturally answered either way and both are printed by the script.
    entry = by_name.get(detector) or next(
        (d for d in detections
         if isinstance(d, dict) and d.get("source") == detector),
        None,
    )
    if entry is None:
        return ArtifactVerdict(
            False, "unrun_anomaly",
            {
                "error": f"detector {detector!r} was not run",
                "detectors_run": sorted(str(k) for k in by_name),
                "run_utc": payload.get("generated_utc"),
            },
        )

    return ArtifactVerdict(
        True, "", {
            "detector": entry.get("metric"),
            "source": entry.get("source"),
            "baseline": entry.get("baseline"),
            "threshold": entry.get("method"),
            "anomalies_detected": entry.get("anomalies_found"),
            "points_evaluated": entry.get("points_evaluated"),
            "run_utc": payload.get("generated_utc"),
        },
    )


# The four rejections named in REQUIRED_CONTROLS, as data, so the test that
# proves them cannot pass by asserting against a different list.
REQUIRED_REJECTIONS = (
    "a stated number absent from the executed result rows",
    "an answer with no executed query behind it",
    "a forecast with no evaluated model behind it",
    "an anomaly with no detection run behind it",
)


def money(value: Any) -> float:
    """`Decimal` to `float`, explicitly, the way the API layer does it.

    Shared so a claim built from a query and a value read from a JSON report
    end up in the same numeric space before being compared.
    """
    if isinstance(value, Decimal):
        return float(value)
    return value