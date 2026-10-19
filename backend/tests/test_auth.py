"""Authentication and authorization tests.

Keys are minted into the real `app.api_keys` table and revoked afterwards, so
these exercise the actual Argon2id verification path rather than a mock. A mocked
verifier proves the dependency graph is wired; it cannot prove a wrong key is
rejected.

Cleanup is in a fixture finaliser rather than a try/finally in each test,
because a test that fails mid-body would otherwise leave live keys behind.
"""

from __future__ import annotations

import sys
import time

import pytest

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "backend"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import analytics, health, insights, overview  # noqa: E402
from app.core.security import (  # noqa: E402
    ALL_SCOPES,
    SCOPE_ADMIN_READ,
    SCOPE_AI_READ,
    SCOPE_ANALYTICS_READ,
    generate_api_key,
    hash_key,
    key_prefix,
    needs_rehash,
    parse_scopes,
    verify_key,
)
from app.core.config import get_settings  # noqa: E402

LABEL_PREFIX = "pytest-auth-"


def _writer_connection():
    import psycopg

    settings = get_settings()
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=10,
    )


@pytest.fixture(scope="module")
def keys():
    """Mint one key per scope combination, then revoke them all afterwards."""
    minted: dict[str, str] = {}
    conn = _writer_connection()
    try:
        for label, scopes, active, expires in (
            ("full", ",".join(sorted(ALL_SCOPES)), True, None),
            ("analytics", SCOPE_ANALYTICS_READ, True, None),
            ("ai", SCOPE_AI_READ, True, None),
            ("admin", SCOPE_ADMIN_READ, True, None),
            ("none", "", True, None),
            ("revoked", SCOPE_ANALYTICS_READ, False, None),
            ("expired", SCOPE_ANALYTICS_READ, True, "2020-01-01T00:00:00+00:00"),
            ("unknown-scope", "analytics:read,teleport:god", True, None),
        ):
            key = generate_api_key()
            conn.execute(
                """
                INSERT INTO app.api_keys
                    (key_prefix, key_hash, label, scopes, is_active, created_at, expires_at)
                VALUES (%s, %s, %s, %s, %s, now(), %s)
                """,
                (key_prefix(key), hash_key(key), f"{LABEL_PREFIX}{label}", scopes,
                 active, expires),
            )
            minted[label] = key
        conn.commit()
    finally:
        conn.close()

    yield minted

    conn = _writer_connection()
    try:
        conn.execute("DELETE FROM app.api_keys WHERE label LIKE %s", (f"{LABEL_PREFIX}%",))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    for router in (
        health.router,
        overview.router,
        analytics.router,
        insights.router,
    ):
        app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def header(keys, label):
    return {"X-API-Key": keys[label]}


# ==============================================================================
# HASHING - the primitive everything else rests on
# ==============================================================================

def test_a_key_verifies_against_its_own_hash():
    key = generate_api_key()
    stored = hash_key(key)
    assert verify_key(stored, key) is True


def test_a_different_key_does_not_verify():
    stored = hash_key(generate_api_key())
    assert verify_key(stored, generate_api_key()) is False


def test_the_plaintext_key_is_never_in_the_hash():
    key = generate_api_key()
    stored = hash_key(key)
    assert key not in stored
    assert stored.startswith("$argon2id$"), stored[:20]


def test_two_hashes_of_the_same_key_differ():
    """Salted, not deterministic.

    A deterministic hash means identical keys look identical in the table, and an
    attacker can tell which rows share a credential without ever seeing one.
    """
    key = generate_api_key()
    assert hash_key(key) != hash_key(key)


def test_keys_are_long_enough_to_brute_force():
    """256 bits of entropy from secrets.token_urlsafe(32)."""
    key = generate_api_key()
    assert key.startswith("omr_")
    assert len(key) >= 40, f"key is only {len(key)} characters"


def test_a_corrupt_hash_is_rejected_rather_than_crashing():
    assert verify_key("not-a-hash", "anything") is False
    assert needs_rehash("not-a-hash") is True


def test_a_freshly_created_hash_needs_no_rehash():
    assert needs_rehash(hash_key(generate_api_key())) is False


# ==============================================================================
# SCOPES
# ==============================================================================

def test_unknown_scopes_are_dropped_not_trusted():
    """A hand-edited row must not conjure a scope the code does not implement."""
    parsed = parse_scopes("analytics:read,teleport:god,root")
    assert parsed == {"analytics:read"}, parsed


def test_empty_scopes_parse_to_nothing():
    assert parse_scopes("") == set()
    assert parse_scopes("   ") == set()


def test_scope_whitespace_is_tolerated():
    assert parse_scopes(" analytics:read , ai:read ") == {"analytics:read", "ai:read"}


# ==============================================================================
# AUTHENTICATION
# ==============================================================================

def test_no_credential_is_401(client):
    response = client.get("/api/analytics/queries")
    assert response.status_code == 401
    assert "missing" in response.json()["detail"].lower()


def test_an_invalid_key_is_401(client, keys):
    response = client.get("/api/analytics/queries", headers={"X-API-Key": "omr_wrong"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid API key"


def test_a_key_with_the_right_prefix_but_wrong_secret_is_401(client, keys):
    """The prefix is a lookup hint, not the credential.

    If the implementation verified only the prefix, this would pass. It must not.
    """
    real_prefix = key_prefix(keys["analytics"])
    impostor = f"{real_prefix}" + "X" * (len(keys["analytics"]) - len(real_prefix))
    assert key_prefix(impostor) == real_prefix, "the test did not construct an impostor"

    response = client.get("/api/analytics/queries", headers={"X-API-Key": impostor})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid API key"


def test_a_revoked_key_is_401(client, keys):
    response = client.get("/api/analytics/queries", headers=header(keys, "revoked"))
    assert response.status_code == 401
    assert "revoked" in response.json()["detail"].lower()


def test_an_expired_key_is_401(client, keys):
    response = client.get("/api/analytics/queries", headers=header(keys, "expired"))
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_a_401_carries_a_challenge_header(client):
    response = client.get("/api/analytics/queries")
    assert "www-authenticate" in {k.lower() for k in response.headers}


def test_health_needs_no_credential(client):
    """A load balancer must be able to reach health without a secret."""
    assert client.get("/api/health").status_code == 200


# ==============================================================================
# AUTHORIZATION
# ==============================================================================

def test_analytics_scope_grants_the_analytics_catalogue(client, keys):
    response = client.get("/api/analytics/queries", headers=header(keys, "analytics"))
    assert response.status_code == 200
    assert response.json()["total"] == 41


def test_ai_scope_alone_cannot_read_analytics(client, keys):
    """AI endpoints let the server construct SQL; analytics does not.

    Keeping them separate means a key issued for the AI layer does not
    implicitly unlock the whole read surface, and vice versa.
    """
    response = client.get("/api/analytics/queries", headers=header(keys, "ai"))
    assert response.status_code == 403
    assert "analytics:read" in response.json()["detail"]


def test_admin_scope_alone_cannot_read_analytics(client, keys):
    response = client.get("/api/analytics/queries", headers=header(keys, "admin"))
    assert response.status_code == 403


def test_a_key_with_no_scopes_is_refused_everything(client, keys):
    for path in ("/api/analytics/queries", "/api/overview", "/api/status"):
        response = client.get(path, headers=header(keys, "none"))
        assert response.status_code == 403, f"{path} should be forbidden"


def test_a_full_scope_key_reaches_everything(client, keys):
    for path in ("/api/analytics/queries", "/api/overview", "/api/status"):
        response = client.get(path, headers=header(keys, "full"))
        assert response.status_code == 200, f"{path} returned {response.status_code}"


def test_status_enumerates_the_schema_and_is_therefore_admin_only(client, keys):
    """Table names, column counts and row counts are reconnaissance."""
    for label, expected in (("analytics", 403), ("admin", 200), ("full", 200)):
        response = client.get("/api/status", headers=header(keys, label))
        assert response.status_code == expected, (
            f"{label} got {response.status_code}, expected {expected}"
        )


def test_a_403_explains_what_is_missing(client, keys):
    detail = client.get("/api/overview", headers=header(keys, "ai")).json()["detail"]
    assert "analytics:read" in detail
    assert "ai:read" in detail, "the error should say what the key DOES hold"


def test_unknown_scopes_do_not_grant_anything(client, keys):
    """The row claims `teleport:god`; nothing implements it, so it grants nothing."""
    response = client.get("/api/analytics/queries", headers=header(keys, "unknown-scope"))
    assert response.status_code == 200, (
        "the valid scope in the list should still work"
    )
    parsed = parse_scopes("analytics:read,teleport:god,root")
    assert parsed == {"analytics:read"}


# ==============================================================================
# THE INSIGHT ENDPOINTS ARE NOT A PUBLIC DOOR
# ==============================================================================
# `/api/forecasting` and `/api/anomalies` shipped without a dependency while
# every other route required a key. They are read-only and cheap - they serve a
# recorded JSON artefact rather than running a model - but they publish the whole
# benchmark and the whole anomaly list, so "cheap" is not the same as "safe to
# leave open". These tests pin the dependency on.


INSIGHT_PATHS = ("/api/forecasting", "/api/anomalies")
API_KEY_HEADER_NAME = "X-API-Key"


@pytest.mark.parametrize("path", INSIGHT_PATHS)
def test_an_insight_endpoint_needs_a_credential(client, path):
    response = client.get(path)
    assert response.status_code == 401, f"{path} returned {response.status_code} unauthenticated"
    detail = response.json()["detail"]
    assert "missing" in detail and API_KEY_HEADER_NAME in detail, detail
    assert "www-authenticate" in {k.lower() for k in response.headers}


@pytest.mark.parametrize("path", INSIGHT_PATHS)
def test_an_insight_endpoint_is_analytics_scoped_not_ai_scoped(client, keys, path):
    """A key issued to ask questions must not also read the recorded analyses.

    AI scope lets the server construct SQL on the caller's behalf; analytics
    scope is for the curated catalogue. The insight endpoints serve curated,
    pre-computed results, so they follow analytics - not AI.
    """
    response = client.get(path, headers=header(keys, "ai"))
    assert response.status_code == 403, (path, response.status_code)
    assert "analytics:read" in response.json()["detail"]


@pytest.mark.parametrize("path", INSIGHT_PATHS)
def test_an_insight_endpoint_refuses_a_revoked_or_expired_key(client, keys, path):
    for label in ("revoked", "expired"):
        response = client.get(path, headers=header(keys, label))
        assert response.status_code == 401, (path, label, response.status_code)


@pytest.mark.parametrize("path", INSIGHT_PATHS)
def test_an_insight_endpoint_serves_real_results_to_an_analytics_key(client, keys, path):
    """The happy path, so the 403 tests above cannot pass on a broken endpoint.

    Asserting the body against the artefact on disk is what separates "the auth
    guard is correct" from "the endpoint returns a 403 to everything".
    """
    response = client.get(path, headers=header(keys, "analytics"))
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["source_artefact"].startswith("data/processed/")
    assert body["generated_utc"], f"{path} does not say when the run happened"

    if path == "/api/forecasting":
        # `winner_by_mase` is a plain string in the artefact, not an object.
        # An endpoint that reshaped it would break every consumer of the
        # recorded run, which is the whole reason this endpoint returns it
        # verbatim.
        assert body["winner_by_mase"] == "sma_7", body["winner_by_mase"]
        assert body["scope"]["series_evaluated"] == 30490, body["scope"]
        assert body["scope"]["is_full_dataset"] is True
        assert body["shape"].startswith("aggregate model-selection benchmark")
    else:
        assert body["total"] > 0, "no firings were reported"
        assert body["metrics_count"] > 0
        assert body["caveat"], "an anomaly list without a caveat is a claim"
        # One row per FIRING, not per metric: the flat list is what a table
        # renders, and conflating the two is how a dashboard claims 3 anomalies
        # when 63 were detected.
        assert body["total"] == 63, body["total"]
        assert len(body["rows"]) == min(200, body["total"])


def test_an_unknown_scope_in_an_insight_key_grants_nothing_new(client, keys):
    """`teleport:god` must not become a back door to the analyses."""
    response = client.get("/api/forecasting", headers=header(keys, "unknown-scope"))
    assert response.status_code == 200, response.status_code
    # 200, because `analytics:read` is also in that row and is genuinely held.
    # The point is that the unknown scope contributes nothing: a key holding
    # only the bogus scope is refused the same way as any other keyless caller.
    assert parse_scopes("analytics:read,teleport:god,root") == {"analytics:read"}


def test_an_insight_endpoint_requires_analytics_and_nothing_more(client, keys):
    """`admin:read` does not imply `analytics:read`."""
    response = client.get("/api/anomalies", headers=header(keys, "admin"))
    assert response.status_code == 403, response.status_code


# ==============================================================================
# A TEST KEY MUST NOT OUTLIVE THE TEST RUN
# ==============================================================================


def test_the_leaked_key_sweep_actually_revokes_a_leaked_key():
    """The end-of-suite guard is only worth having if it works.

    Mint an active key labelled like a test key, run the same sweep the session
    fixture runs, and assert it came back revoked. Without this, the guard is an
    assertion that could silently become a no-op - and it did, once: two
    admin-scoped `pytest` keys sat active in this database because a fixture
    finaliser never ran and nothing else was watching.
    """
    from conftest import revoke_leaked_test_keys

    conn = _writer_connection()
    try:
        key = generate_api_key()
        conn.execute(
            """
            INSERT INTO app.api_keys
                (key_prefix, key_hash, label, scopes, is_active, created_at)
            VALUES (%s, %s, %s, %s, TRUE, now())
            """,
            (key_prefix(key), hash_key(key), "pytest-leak-probe", SCOPE_ADMIN_READ),
        )
        conn.commit()
        leaked_id = conn.execute(
            "SELECT max(id) FROM app.api_keys WHERE label = 'pytest-leak-probe'"
        ).fetchone()[0]
    finally:
        conn.close()

    try:
        revoked = revoke_leaked_test_keys()
        assert (leaked_id, "pytest-leak-probe") in [tuple(r) for r in revoked], (
            f"the sweep did not revoke the probe key; it returned {revoked}"
        )

        conn = _writer_connection()
        try:
            still_active = conn.execute(
                "SELECT is_active FROM app.api_keys WHERE id = %s", (leaked_id,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert still_active is False, "the revoked key is still usable"
    finally:
        conn = _writer_connection()
        try:
            conn.execute("DELETE FROM app.api_keys WHERE label = 'pytest-leak-probe'")
            conn.commit()
        finally:
            conn.close()


def test_the_sweep_leaves_working_keys_alone():
    """`frontend-dev`, `frontend-admin` and `ai-smoke` are not test keys.

    A sweep that revoked everything would pass every test above while locking
    the operator out of the dashboard, so the negative case is asserted too.
    """
    from conftest import revoke_leaked_test_keys

    conn = _writer_connection()
    try:
        working = conn.execute(
            "SELECT id FROM app.api_keys WHERE is_active AND label NOT LIKE %s ORDER BY id",
            ("pytest%",),
        ).fetchall()
    finally:
        conn.close()
    assert working, (
        "there are no active non-test keys, so this test cannot prove the "
        "sweep is narrow - issue one with 08_issue_api_key.py first"
    )

    revoked = revoke_leaked_test_keys()
    revoked_ids = {r[0] for r in revoked}

    conn = _writer_connection()
    try:
        for (key_id,) in working:
            still_active = conn.execute(
                "SELECT is_active FROM app.api_keys WHERE id = %s", (key_id,)
            ).fetchone()[0]
            assert still_active is True, (
                f"the sweep revoked working key {key_id}; its label does not "
                f"start with 'pytest', so it is not a test credential"
            )
        assert not (revoked_ids & {w[0] for w in working})
    finally:
        conn.close()


def test_no_active_key_has_an_empty_or_dev_only_label():
    """A credential with a meaningless label cannot be revoked by a human.

    `08_issue_api_key.py revoke` takes a prefix, so this is about prefixes
    being unique enough to act on - but the real risk is a key nobody can
    account for, so the assertion is that every active key names who it is
    for.
    """
    conn = _writer_connection()
    try:
        rows = conn.execute(
            "SELECT id, label FROM app.api_keys WHERE is_active ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert rows, "no active keys at all; the API would be unreachable"
    for key_id, label in rows:
        assert label and label.strip(), f"key {key_id} has no label"
        assert len(label.strip()) >= 3, (
            f"key {key_id} is labelled {label!r}, which is too terse to "
            f"identify or revoke safely"
        )


# ==============================================================================
# STORAGE
# ==============================================================================

def test_the_database_never_stores_a_plaintext_key(keys):
    conn = _writer_connection()
    try:
        rows = conn.execute(
            "SELECT label, key_hash, key_prefix FROM app.api_keys WHERE label LIKE %s",
            (f"{LABEL_PREFIX}%",),
        ).fetchall()
        assert rows, "the fixture minted no keys - the test proved nothing"
        for label, key_hash, prefix in rows:
            assert key_hash.startswith("$argon2id$"), f"{label} stored a raw value"
            for key in keys.values():
                assert key not in key_hash, f"{label} leaked a plaintext key"
                assert key not in prefix, f"{label} leaked a plaintext key"
    finally:
        conn.close()


def test_authenticating_does_not_write_to_the_database(client, keys):
    """A read-only API must not acquire a write on the authentication path.

    The obvious implementation records `last_used_at`. Doing that from the
    request path would mean the read-only role needed UPDATE on a credential
    table - so the column exists, is populated by an operator-run script, and the
    request path touches nothing.
    """
    conn = _writer_connection()
    try:
        before = dict(
            conn.execute(
                "SELECT label, last_used_at FROM app.api_keys WHERE label LIKE %s",
                (f"{LABEL_PREFIX}%",),
            ).fetchall()
        )
    finally:
        conn.close()

    client.get("/api/analytics/queries", headers=header(keys, "analytics"))

    conn = _writer_connection()
    try:
        after = dict(
            conn.execute(
                "SELECT label, last_used_at FROM app.api_keys WHERE label LIKE %s",
                (f"{LABEL_PREFIX}%",),
            ).fetchall()
        )
    finally:
        conn.close()

    assert before == after, "a GET request modified app.api_keys"


def test_authentication_cost_is_constant_for_present_and_absent_keys(client, keys):
    """A missing key must not be dramatically faster than a wrong one.

    A timing gap here tells an attacker whether a prefix exists. Both paths do an
    indexed lookup plus (or minus) one Argon2 hash, and Argon2 at these cost
    parameters dominates both by orders of magnitude, so the medians should be
    close.

    A loose bound on purpose: this is a smoke test against a pathological
    regression (for example, skipping verification entirely), not a statistical
    timing attack defence. Anything stricter would be flaky on a shared machine.
    """
    def median_ms(key_value, rounds=3):
        samples = []
        for _ in range(rounds):
            started = time.perf_counter()
            client.get("/api/analytics/queries", headers={"X-API-Key": key_value})
            samples.append((time.perf_counter() - started) * 1000)
        return sorted(samples)[len(samples) // 2]

    wrong = median_ms("omr_" + "x" * 43)
    real = median_ms(keys["analytics"])
    ratio = max(wrong, real) / max(min(wrong, real), 0.001)
    assert ratio < 4.0, (
        f"absent key took {wrong:.1f}ms and present key {real:.1f}ms "
        f"(ratio {ratio:.2f}); one path is skipping the hash"
    )