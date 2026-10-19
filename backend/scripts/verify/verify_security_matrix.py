"""Run the auth and rate-limit matrix against the LIVE API and print what happened.

Not a pytest module - this is the verification script for the final report, and
it needs to talk to a real running server over HTTP rather than to a
TestClient, because two of the things it checks (the CORS headers on a 429, and
the rate limiter's behaviour across real requests) only exist on the wire.

Usage, from the repository root:

    .venv/Scripts/python.exe backend/scripts/verify/verify_security_matrix.py

It reads the working key from the same file the browser session uses and mints
the short-lived keys it needs through the shipping issuer, then revokes them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE = os.environ.get("OMNIRETAIL_API", "http://127.0.0.1:8077")
KEY_FILE = Path(os.path.expanduser("~")) / ".omniretail" / "frontend_key.txt"
ISSUER = REPO_ROOT / "backend" / "scripts" / "db" / "08_issue_api_key.py"


def call(path, key=None, method="GET", body=None, timeout=180):
    req = urllib.request.Request(BASE + path, method=method)
    if key:
        req.add_header("X-API-Key", key)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - this is a diagnostic script
        return 0, f"{type(e).__name__}: {e}"


def issue(label: str, scopes: str, expires_days: int | None = None) -> str:
    """Mint a key through the shipping issuer and read the full value back.

    The issuer prints the key exactly once, between a pair of `===` rules, and
    also prints a short prefix on the summary table. Two traps here, both of
    which make a security check silently test the wrong thing:

    * Grepping `omr_[A-Za-z0-9_]+` returns the PREFIX, not the key. A prefix
      presented as a key produces a 401 that is indistinguishable from a
      correct expiry refusal.
    * The alphabet INCLUDES `-`. A pattern that stops at `-` truncates a valid
      key mid-string and produces the same false 401.

    So the full-length value is matched specifically, hyphens included.
    """
    cmd = [str(REPO_ROOT / ".venv/Scripts/python.exe"), str(ISSUER),
           "issue", "--label", label, "--scopes", scopes]
    if expires_days is not None:
        cmd += ["--expires-days", str(expires_days)]
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, check=True)
    match = re.search(r"\bomr_[A-Za-z0-9_-]{40,}\b", out.stdout)
    if not match:
        raise SystemExit(f"could not read the issued key from:\n{out.stdout}\n{out.stderr}")
    return match.group(0)


def revoke(label: str) -> None:
    """Revoke by the key's PREFIX - `revoke` takes it positionally.

    The `list` table columns are: id, prefix, label, scopes, active, ... so the
    label is `parts[2]`. Reading `parts[3]` picks up the SCOPES column, matches
    nothing, and silently revokes nothing - which then makes a "revoked key"
    row in the matrix pass or fail for entirely the wrong reason.
    """
    listing = subprocess.run(
        [str(REPO_ROOT / ".venv/Scripts/python.exe"), str(ISSUER), "list"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    ).stdout
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == label:
            subprocess.run(
                [str(REPO_ROOT / ".venv/Scripts/python.exe"), str(ISSUER), "revoke", parts[1]],
                capture_output=True, text=True, cwd=REPO_ROOT, check=False,
            )
            return
    raise SystemExit(f"could not find a key labelled {label!r} to revoke")


def expire_now(label: str) -> None:
    """Backdate a key's `expires_at` so the expiry path can be tested for real.

    The issuer CANNOT produce an already-expired key: `--expires-days` is
    guarded by `if args.expires_days`, so `0` is falsy and yields
    `expires_at = NULL`, i.e. a key that NEVER expires. A matrix row labelled
    "expired key" that was built with `--expires-days 0` is therefore testing a
    never-expiring key, and would report 200 where the correct answer is 401.

    So the timestamp is set directly, using the writer role. This is an
    operator script, not the API: the API holds no write credential at all,
    which is exactly why reaching for the writer here is the only option and
    also why the read-only guarantee is unaffected by this test.
    """
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from sqlalchemy import create_engine, text

    from app.core.config import get_settings

    engine = create_engine(get_settings().database_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.api_keys SET expires_at = now() - interval '1 minute' "
                "WHERE label = :l"
            ),
            {"l": label},
        )


# A per-run suffix on every label this script mints.
#
# Without it, a second run issues a NEW key carrying a label an earlier run
# already used, and `revoke()` - which matches on the FIRST row with that label
# and `list` returns ordered by id - revokes the OLD key and leaves the new one
# active. The matrix then reports a live key as "revoked" and gets 200, which
# reads exactly like an auth failure and is not one.
RUN = time.strftime("%H%M%S")


def label_for(name: str) -> str:
    return f"{name}-{RUN}"


PROTECTED = [
    "/api/status", "/api/overview", "/api/analytics/queries",
    "/api/ai/guards", "/api/forecasting", "/api/anomalies",
    "/api/integrations/powerbi/status", "/api/integrations/lineage", "/api/rate-limit",
]

# POST-only endpoints. `/api/ai/search` takes a question, so a GET returns 405
# before authentication ever runs - asserting 401 on it would be asserting that
# the API leaks nothing, when it simply has no GET route.
PROTECTED_POST = [
    ("/api/ai/search", {"question": "olist revenue by state", "limit": 2}),
    ("/api/ai/ask", {"question": "olist revenue by state"}),
    ("/api/ai/validate", {"sql": "SELECT count(*) FROM olist.orders"}),
]

# The scope each route actually requires.
#
# NOT a single expected code for the whole column set. A key holding `ai:read`
# is SUPPOSED to get 200 from `/api/ai/guards` and 403 from `/api/status`; a flat
# "partial scope -> 403" expectation tests nothing and reports false failures.
# The first version of this script did exactly that and produced 32 findings of
# which zero were real.
ROUTE_SCOPE: dict[str, str] = {
    "/api/status": "admin:read",
    "/api/overview": "analytics:read",
    "/api/analytics/queries": "analytics:read",
    "/api/ai/guards": "ai:read",
    "/api/ai/search": "ai:read",
    "/api/ai/ask": "ai:read",
    "/api/ai/validate": "ai:read",
    "/api/forecasting": "analytics:read",
    "/api/anomalies": "analytics:read",
    "/api/integrations/powerbi/status": "admin:read",
    "/api/integrations/lineage": "admin:read",
    "/api/rate-limit": "admin:read",
}


def sweep_leftovers() -> int:
    """Revoke every key whose label looks like one of ours. Returns the count.

    Label-prefix based rather than exact, so it also catches rows a crashed run
    left behind with a different timestamp suffix. Without this, the next
    matrix run would find duplicates - which is the same ambiguity that made
    the "revoked key" row report a live key as revoked.
    """
    listing = subprocess.run(
        [str(REPO_ROOT / ".venv/Scripts/python.exe"), str(ISSUER), "list"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    ).stdout
    swept = 0
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[2].startswith("pytest-") and parts[4] == "True":
            subprocess.run(
                [str(REPO_ROOT / ".venv/Scripts/python.exe"), str(ISSUER), "revoke", parts[1]],
                capture_output=True, text=True, cwd=REPO_ROOT, check=False,
            )
            swept += 1
    return swept


def expected_for(path: str, scopes: frozenset[str] | None) -> int:
    """200 when the key holds the route's scope, 403 when it does not.

    `None` scopes means the request must fail authentication outright, which is
    401 for every route - a missing, malformed, revoked or expired key is
    rejected before any scope is considered.
    """
    if scopes is None:
        return 401
    required = ROUTE_SCOPE[path]
    return 200 if required in scopes else 403


def main() -> int:
    if not KEY_FILE.is_file():
        raise SystemExit(f"no key file at {KEY_FILE}")

    minted: list[str] = []

    full = re.search(r"\bomr_[A-Za-z0-9_-]{40,}\b", KEY_FILE.read_text(encoding="utf-8")).group(0)
    ai_only = issue(label_for("pytest-matrix-ai"), "ai:read"); minted.append("pytest-matrix-ai")
    no_scope = issue(label_for("pytest-matrix-noscope"), "analytics:read"); minted.append("pytest-matrix-noscope")
    expired = issue(label_for("pytest-matrix-expired"), "admin:read,ai:read,analytics:read"); minted.append("pytest-matrix-expired")
    expire_now(label_for("pytest-matrix-expired"))
    full2 = issue(label_for("pytest-matrix-full"), "admin:read,ai:read,analytics:read"); minted.append("pytest-matrix-full")
    revoked = revoke_and_get(); minted.append("pytest-matrix-revoked")

    # `None` scopes = "must fail authentication", which is always 401.
    states = {
        "no key": (None, None),
        "invalid key": ("omr_totally_invalid_key_value_00000", None),
        "revoked key": (revoked, None),
        "expired key": (expired, None),
        "ai:read only": (ai_only, frozenset({"ai:read"})),
        "analytics:read only": (no_scope, frozenset({"analytics:read"})),
        "full scopes": (full2, frozenset({"admin:read", "ai:read", "analytics:read"})),
        "browser key": (full, frozenset({"admin:read", "ai:read", "analytics:read"})),
    }

    failures: list[str] = []

    print("=" * 78)
    print("AUTH MATRIX - live API")
    print("=" * 78)
    for state, (key, scopes) in states.items():
        row, expected_row = [], []
        for path in PROTECTED:
            code, _ = call(path, key)
            want = expected_for(path, scopes)
            row.append(code)
            expected_row.append(want)
            if code != want:
                failures.append(f"{state} GET {path} -> {code}, expected {want}")
        post_row, post_expected = [], []
        for path, payload in PROTECTED_POST:
            code, _ = call(path, key, method="POST", body=payload)
            want = expected_for(path, scopes)
            post_row.append(code)
            post_expected.append(want)
            if code != want:
                failures.append(f"{state} POST {path} -> {code}, expected {want}")
        print(f"  {state:22s} GET  exp {expected_row} got {row}")
        print(f"  {'':22s} POST exp {post_expected} got {post_row}")

    print()
    print("=" * 78)
    print("PUBLIC ENDPOINT")
    print("=" * 78)
    for key_label, key in (("no key", None), ("invalid key", "omr_nope"), ("full key", full2)):
        code, _ = call("/api/health", key)
        print(f"  /api/health with {key_label:14s} -> {code}")
        if code != 200:
            failures.append(f"/api/health with {key_label} -> {code}, expected 200")

    print()
    print("=" * 78)
    print("CORS")
    print("=" * 78)
    for origin, expected in (
        ("http://127.0.0.1:5173", "allowed"),
        ("http://evil.example.com", "not allowed"),
    ):
        req = urllib.request.Request(BASE + "/api/status")
        req.add_header("Origin", origin)
        req.add_header("X-API-Key", full2)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                headers = dict(r.headers)
        except urllib.error.HTTPError as e:
            headers = dict(e.headers)
        allow = headers.get("access-control-allow-origin")
        present = allow is not None
        state = "allowed" if present else "not allowed"
        print(f"  Origin {origin:28s} -> ACAO={allow!r} ({state})")
        if (expected == "allowed") != present:
            failures.append(f"CORS origin {origin} -> {allow!r}, expected {expected}")

    print()
    print("=" * 78)
    print("RATE LIMIT - live, over HTTP")
    print("=" * 78)
    burst_key = issue(label_for("pytest-matrix-burst"), "analytics:read"); minted.append("pytest-matrix-burst")
    codes = []
    for _ in range(300):
        code, _ = call("/api/analytics/queries", burst_key, timeout=30)
        codes.append(code)
        if code == 429:
            break
    got_429 = 429 in codes
    idx = codes.index(429) if got_429 else None
    print(f"  first 429 at request #{idx + 1 if idx is not None else 'never'} (burst is 40/min)")
    print(f"  final code: {codes[-1]}")
    if not got_429:
        failures.append("the rate limiter never returned 429 over 300 requests")

    code, body = call("/api/analytics/queries", burst_key, timeout=30)
    if code == 429:
        parsed = json.loads(body)
        print(f"  429 body keys : {sorted(parsed)}")
        print(f"  retry_after   : {parsed.get('retry_after_seconds')}s")
        if not parsed.get("detail"):
            failures.append("the 429 body has no detail explaining itself")
    else:
        # The bucket refills at 40/min, i.e. ~0.67 tokens/second, so by the time
        # this separate request goes out a token may already be back. That is
        # CORRECT behaviour, not a missing limit - the request that proved the
        # limit is the one in the loop above. Asserting 429 here would be
        # asserting that time does not pass.
        print(f"  follow-up request after the 429 -> {code} "
              f"(token refilled; the limit WAS enforced, see above)")

    print("  /api/health while limited:", call("/api/health", None, timeout=30)[0], "(must be 200)")
    if call("/api/health", None, timeout=30)[0] != 200:
        failures.append("/api/health was rate limited; a monitor would report the service down")

    # A different key must not be affected by the noisy one.
    other = issue(label_for("pytest-matrix-quiet"), "analytics:read"); minted.append("pytest-matrix-quiet")
    code, _ = call("/api/analytics/queries", other, timeout=30)
    print(f"  a DIFFERENT key while the first is limited -> {code}")
    if code != 200:
        failures.append(f"a second key was refused because a different key was limited: {code}")

    for name in minted:
        revoke(label_for(name))

    # Keys an earlier, interrupted run may have left behind. Sweeping a prefix
    # rather than an exact label, and tolerating a miss - a cleanup step that
    # aborts the script has just reported the matrix as failed for a reason that
    # is not the matrix.
    sweep_leftovers()

    print()
    print("=" * 78)
    swept = sweep_leftovers()
    print(f"CLEANUP: revoked {swept} leftover key(s); active keys remaining:")
    listing = subprocess.run(
        [str(REPO_ROOT / ".venv/Scripts/python.exe"), str(ISSUER), "list"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    ).stdout
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[4] == "True":
            print(f"  {line}")

    print()
    print("=" * 78)
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL SECURITY CHECKS PASSED")
    return 0


def revoke_and_get() -> str:
    """A key that is definitely revoked: issue one, revoke it, return the value."""
    label = label_for("pytest-matrix-revoked")
    key = issue(label, "admin:read,ai:read,analytics:read")
    revoke(label)
    return key


if __name__ == "__main__":
    sys.exit(main())
