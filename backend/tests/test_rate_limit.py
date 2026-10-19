"""Rate limiting: the controls that make it real, and the limits that bound it.

WHAT IS BEING PROTECTED
-----------------------
`STATEMENT_TIMEOUT_MS` already caps a single query at 300 seconds. That is a
ceiling on duration and says nothing about concurrency, so a caller able to run
one expensive query could start many. The token bucket bounds the second thing.

WHAT THESE TESTS REFUSE TO DO
-----------------------------
They do not assert that "rate limiting exists". They break it. A limiter that
is never driven past its limit cannot be distinguished from one that is not
wired up, so the tests use a fake clock and fire far past the ceiling.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rate_limit import EXEMPT_PATHS, RateLimitMiddleware
from app.core.rate_limit import Bucket, RateLimiter


class FakeClock:
    """A clock the test owns, so refill behaviour is deterministic."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ==============================================================================
# The bucket itself
# ==============================================================================


def test_a_call_within_the_burst_is_allowed():
    limiter = RateLimiter(per_minute=60, burst=10, clock=FakeClock())
    for _ in range(10):
        assert limiter.check("key-a").allowed


def test_the_call_after_the_burst_is_refused():
    limiter = RateLimiter(per_minute=60, burst=10, clock=FakeClock())
    for _ in range(10):
        limiter.check("key-a")
    verdict = limiter.check("key-a")
    assert not verdict.allowed
    assert verdict.remaining == 0


def test_a_refusal_carries_a_retry_after_and_the_limit():
    limiter = RateLimiter(per_minute=60, burst=2, clock=FakeClock())
    limiter.check("key-a")
    limiter.check("key-a")
    verdict = limiter.check("key-a")

    assert not verdict.allowed
    assert verdict.limit == 60
    assert verdict.retry_after >= 1
    headers = verdict.headers()
    assert headers["Retry-After"] == str(verdict.retry_after)
    assert headers["X-RateLimit-Limit"] == "60"
    assert headers["X-RateLimit-Remaining"] == "0"


def test_remaining_counters_down_and_never_goes_negative():
    limiter = RateLimiter(per_minute=60, burst=3, clock=FakeClock())
    seen = [limiter.check("key-a").remaining for _ in range(6)]
    assert seen == [2, 1, 0, 0, 0, 0]
    assert all(value >= 0 for value in seen)


def test_the_bucket_refills_over_time_at_the_sustained_rate():
    clock = FakeClock()
    limiter = RateLimiter(per_minute=60, burst=2, clock=clock)
    limiter.check("key-a")
    limiter.check("key-a")
    assert not limiter.check("key-a").allowed

    # One token per second (60/minute). Two seconds buys two tokens.
    clock.advance(2.0)
    assert limiter.check("key-a").allowed
    assert limiter.check("key-a").allowed
    assert not limiter.check("key-a").allowed


def test_refill_never_exceeds_the_burst_capacity():
    """An hour of idling must not buy an hour of requests."""
    clock = FakeClock()
    limiter = RateLimiter(per_minute=60, burst=5, clock=clock)
    clock.advance(3600.0)
    allowed = sum(1 for _ in range(20) if limiter.check("key-a").allowed)
    assert allowed == 5, f"an idle hour granted {allowed} requests; the burst is 5"


def test_one_key_cannot_exhaust_another():
    """The whole point of keying by key rather than globally."""
    limiter = RateLimiter(per_minute=60, burst=3, clock=FakeClock())
    for _ in range(10):
        limiter.check("noisy")
    assert not limiter.check("noisy").allowed
    assert limiter.check("quiet").allowed


def test_a_newly_issued_key_is_not_punished_for_the_old_one_s_traffic():
    """Buckets are keyed by key, so rotating a key is not a way to reset a limit.

    Revoking and reissuing produces a NEW key, and a new key gets a new bucket.
    That is correct for an admin rotating a credential, and it is also why the
    limit is a fairness control rather than an abuse control: an attacker with
    the ability to mint keys is not the threat model here. `STATEMENT_TIMEOUT_MS`
    and the read-only role are.
    """
    clock = FakeClock()
    limiter = RateLimiter(per_minute=60, burst=2, clock=clock)
    assert limiter.check("old-key").allowed
    assert limiter.check("old-key").allowed
    assert not limiter.check("old-key").allowed

    # The old key stays limited...
    assert not limiter.check("old-key").allowed
    # ...and the new one starts clean.
    assert limiter.check("new-key").allowed
    assert limiter.check("new-key").allowed
    assert not limiter.check("new-key").allowed


def test_burst_is_clamped_to_the_per_minute_rate():
    """A burst larger than the rate would grant more than a minute of traffic."""
    limiter = RateLimiter(per_minute=10, burst=999)
    assert limiter.burst <= limiter.per_minute
    assert limiter.capacity == 10.0


def test_nonsense_configuration_is_clamped_rather_than_crashing():
    for kwargs in (
        {"per_minute": 0, "burst": 0},
        {"per_minute": -5, "burst": -1},
        {"per_minute": 1, "burst": 1},
    ):
        limiter = RateLimiter(**kwargs, clock=FakeClock())
        assert limiter.per_minute >= 1
        assert limiter.burst >= 1
        assert limiter.capacity >= 1.0
        assert limiter.rate > 0


def test_a_disabled_limiter_allows_everything():
    limiter = RateLimiter(per_minute=1, burst=1, enabled=False, clock=FakeClock())
    for _ in range(500):
        assert limiter.check("key-a").allowed


def test_the_bucket_does_not_need_recently_used_keys_to_survive():
    """Bounded memory is a security property, not an optimisation."""
    clock = FakeClock()
    limiter = RateLimiter(per_minute=60, burst=5, clock=clock)
    for index in range(20_000):
        limiter.check(f"key-{index}")
        if index % 1000 == 0:
            clock.advance(60.0)
    assert limiter.tracked_keys() <= 10_001, (
        f"{limiter.tracked_keys()} buckets tracked; the cap is 10,000"
    )


def test_idle_buckets_are_evicted_even_if_they_were_partially_used():
    """
    The eviction bug this test was written for.

    Each of these buckets spent exactly one of its five tokens and was then
    left alone. Judging eviction on the STORED token count - which is still
    `capacity - 1` because only `consume` refills - would never evict them, and
    the map would grow until the hard cap with the eviction pass doing nothing.
    """
    clock = FakeClock()
    limiter = RateLimiter(per_minute=60, burst=5, clock=clock)
    for index in range(50):
        limiter.check(f"key-{index}")
    assert limiter.tracked_keys() == 50

    clock.advance(901.0)
    limiter.check("someone-new")
    assert limiter.tracked_keys() == 1, (
        f"{limiter.tracked_keys()} partially-used buckets survived an idle "
        f"eviction pass; the map would grow to the hard cap"
    )


def test_a_bucket_still_within_the_idle_window_is_kept():
    clock = FakeClock()
    limiter = RateLimiter(per_minute=60, burst=5, clock=clock)
    limiter.check("key-a")
    clock.advance(30.0)
    limiter.check("key-b")
    assert limiter.tracked_keys() == 2, "a key in active use must not be evicted"


def test_bucket_retry_after_is_never_less_than_a_second():
    """`Retry-After: 0` invites an immediate retry storm."""
    clock = FakeClock()
    limiter = RateLimiter(per_minute=60, burst=1, clock=clock)
    limiter.check("key-a")
    verdict = limiter.check("key-a")
    assert verdict.retry_after >= 1

    bucket = Bucket(tokens=0.0, updated=clock.now)
    assert bucket.retry_after(clock.now, limiter.rate, 1.0) >= 1.0


def test_the_limiter_discloses_its_own_caveat():
    """A limit that silently multiplies across workers is worse than none."""
    limiter = RateLimiter(per_minute=60, burst=5, clock=FakeClock())
    limiter.check("key-a")
    info = limiter.limitations()

    assert info["enabled"] is True
    assert info["per_minute"] == 60
    assert info["burst"] == 5
    assert info["algorithm"].startswith("token bucket")
    assert "/api/health" in info["exempt"]
    assert "worker" in str(info["caveat"]).lower()


# ==============================================================================
# Through the middleware
# ==============================================================================


def _app(**kwargs) -> FastAPI:
    application = FastAPI()
    application.add_middleware(
        RateLimitMiddleware, limiter=RateLimiter(clock=FakeClock(), **kwargs)
    )

    @application.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @application.get("/api/protected")
    def protected() -> dict:
        return {"status": "ok"}

    @application.post("/api/protected")
    def protected_post() -> dict:
        return {"status": "ok"}

    return application


def test_requests_under_the_limit_pass_through():
    with TestClient(_app(per_minute=60, burst=5)) as client:
        for _ in range(5):
            assert client.get("/api/protected").status_code == 200


def test_the_request_over_the_limit_is_429_with_a_retry_after():
    with TestClient(_app(per_minute=60, burst=3)) as client:
        for _ in range(3):
            client.get("/api/protected")
        response = client.get("/api/protected")

    assert response.status_code == 429
    assert response.headers["Retry-After"]
    assert int(response.headers["Retry-After"]) >= 1
    assert response.json()["retry_after_seconds"] >= 1
    assert response.json()["limit_per_minute"] == 60


def test_a_429_explains_itself_rather_than_returning_a_bare_status():
    with TestClient(_app(per_minute=60, burst=1)) as client:
        client.get("/api/protected")
        body = client.get("/api/protected").json()

    assert "detail" in body
    assert "token bucket" in body["detail"]
    assert "exempt_note" in body
    assert body["path"] == "/api/protected"


def test_the_health_probe_is_never_rate_limited():
    """A monitoring system that gets 429 reports the service down."""
    with TestClient(_app(per_minute=60, burst=2)) as client:
        for _ in range(200):
            response = client.get("/api/health")
            assert response.status_code == 200, f"health returned {response.status_code}"
    assert "/api/health" in EXEMPT_PATHS


def test_every_exempt_path_is_a_real_decision():
    """An exempt path is an attack surface; each one is named and justified."""
    assert EXEMPT_PATHS == {
        "/api/health",
        "/docs",
        "/openapi.json",
        "/favicon.ico",
    }


def test_successful_responses_carry_the_limit_headers():
    with TestClient(_app(per_minute=60, burst=5)) as client:
        response = client.get("/api/protected")

    assert response.headers["X-RateLimit-Limit"] == "60"
    assert int(response.headers["X-RateLimit-Remaining"]) == 4


def test_the_limit_applies_to_writes_as_well_as_reads():
    """POST is in the CORS allowlist, so it must be limited too."""
    with TestClient(_app(per_minute=60, burst=2)) as client:
        assert client.post("/api/protected").status_code == 200
        assert client.post("/api/protected").status_code == 200
        assert client.post("/api/protected").status_code == 429


def test_different_keys_get_independent_buckets_over_http():
    with TestClient(_app(per_minute=60, burst=2)) as client:
        headers = {"X-API-Key": "omr_test_key_one"}
        for _ in range(5):
            client.get("/api/protected", headers=headers)
        assert client.get("/api/protected", headers=headers).status_code == 429

        other = {"X-API-Key": "omr_test_key_two"}
        assert client.get("/api/protected", headers=other).status_code == 200


def test_the_raw_key_is_never_used_as_the_bucket_key():
    """The bucket map could plausibly end up in a memory dump."""
    with TestClient(_app(per_minute=60, burst=1)) as client:
        key = "omr_a_very_recognisable_secret_value"
        client.get("/api/protected", headers={"X-API-Key": key})
        client.get("/api/protected", headers={"X-API-Key": key})
        response = client.get("/api/protected", headers={"X-API-Key": key})

    assert response.status_code == 429
    assert key not in response.text


@pytest.mark.parametrize("method", ["get", "post"])
def test_the_limiter_survives_a_disabled_configuration_end_to_end(method: str):
    with TestClient(_app(per_minute=1, burst=1, enabled=False)) as client:
        for _ in range(50):
            assert getattr(client, method)("/api/protected").status_code == 200
