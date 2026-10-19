"""Per-key rate limiting, because a timeout is a ceiling and not a budget.

THE GAP THIS CLOSES
-------------------
`STATEMENT_TIMEOUT_MS` caps how long ONE query may run. It says nothing about
how many a caller may start, so a key that can run a 300-second query can
start forty of them and hold every connection in the pool while doing it.
`docs/api.md` documented that as a known gap. This closes it.

WHY A TOKEN BUCKET AND NOT A FIXED WINDOW
----------------------------------------
A fixed window (`N requests per 60 s`) permits 2N requests across a window
boundary: nine hundred and ninety at 11:59:59 and nine hundred and ninety more
at 12:00:00. For a resource this expensive to serve, that burst is the whole
problem. A token bucket allows a controlled burst *by design* and then settles
to the sustained rate, which is what a shared analytics instance actually
wants.

WHY IN-PROCESS AND NOT REDIS
----------------------------
This application is a single-process analytics API over a single local
PostgreSQL instance. A distributed limiter would add a dependency, a failure
mode and a network hop to solve a problem that does not exist yet. What that
costs is stated plainly in `limitations()`: with `--workers N` the effective
limit is N times the configured one, because each worker keeps its own buckets.
That is a real caveat and it is surfaced at runtime through
`GET /api/status`, not buried here.

WHAT IS AND IS NOT LIMITED
--------------------------
* Keyed by API key id when there is a key, by client address when there is not.
  A shared NAT address therefore shares a bucket for unauthenticated calls -
  acceptable, because every route except `/api/health` requires a key anyway.
* `/api/health` is exempt. It is the liveness probe, it is public, it costs one
  `SELECT 1`, and a monitoring system that gets 429s is a monitoring system
  that reports the service down.
* Nothing is limited more strictly than the bucket. Rate limiting is a
  fairness control; the safety controls (read-only role, validator, timeout)
  are what actually protect the data.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

# A bucket that has been full for this long is indistinguishable from a bucket
# that was never used, and keeping it costs memory for nothing.
_IDLE_EVICTION_SECONDS = 900.0

# Hard cap on distinct tracked keys. An attacker who can present unbounded
# distinct keys must not be able to turn the limiter itself into the memory
# exhaustion it exists to prevent. When full, the least-recently-used bucket is
# evicted, which degrades to "that key gets a fresh bucket" rather than to
# "limiting stops".
_MAX_TRACKED_KEYS = 10_000


@dataclass
class Bucket:
    """One caller's allowance: `tokens` available, refilling at `rate`/s."""

    tokens: float
    updated: float
    hits: int = 0

    def consume(self, now: float, rate: float, capacity: float, cost: float) -> bool:
        elapsed = max(0.0, now - self.updated)
        self.tokens = min(capacity, self.tokens + elapsed * rate)
        self.updated = now

        if self.tokens < cost:
            return False

        self.tokens -= cost
        self.hits += 1
        return True

    def retry_after(self, now: float, rate: float, cost: float) -> float:
        """Seconds until `cost` tokens are available. Never less than 1."""
        if rate <= 0:
            return 60.0
        missing = max(0.0, cost - self.tokens)
        return max(1.0, missing / rate)


@dataclass
class Verdict:
    """The limiter's answer, including why, so 429s can explain themselves."""

    allowed: bool
    remaining: int
    limit: int
    retry_after: int = 0
    key: str = ""

    def headers(self) -> dict[str, str]:
        """Standard rate-limit headers.

        `X-RateLimit-*` rather than the IETF `RateLimit-*` draft fields because
        the draft names have changed twice and shipping headers a client cannot
        rely on is worse than shipping the widely-implemented ones.
        """
        out = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
        }
        if not self.allowed:
            out["Retry-After"] = str(self.retry_after)
        return out


@dataclass
class RateLimiter:
    """A token bucket per key, guarded by one lock."""

    per_minute: int = 120
    burst: int = 30
    enabled: bool = True
    clock: object = time.monotonic
    _buckets: "OrderedDict[str, Bucket]" = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        # `burst` is the ceiling; the sustained rate is the refill. Keeping
        # them as one number when a caller sets only one avoids a limiter that
        # allows a burst larger than its own capacity.
        if self.burst < 1:
            self.burst = 1
        if self.per_minute < 1:
            self.per_minute = 1
        if self.burst > self.per_minute:
            self.burst = self.per_minute

    @property
    def capacity(self) -> float:
        return float(self.burst)

    @property
    def rate(self) -> float:
        """Tokens per second - the sustained rate."""
        return self.per_minute / 60.0

    def check(self, key: str, cost: float = 1.0) -> Verdict:
        if not self.enabled:
            return Verdict(allowed=True, remaining=self.burst, limit=self.per_minute)

        now = float(self.clock())  # type: ignore[operator]
        with self._lock:
            self._evict(now)

            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = Bucket(tokens=self.capacity, updated=now)
                self._buckets[key] = bucket
            else:
                self._buckets.move_to_end(key)

            allowed = bucket.consume(now, self.rate, self.capacity, cost)
            retry_after = (
                0 if allowed else int(round(bucket.retry_after(now, self.rate, cost)))
            )
            verdict = Verdict(
                allowed=allowed,
                remaining=int(bucket.tokens),
                limit=self.per_minute,
                retry_after=retry_after,
                key=key,
            )

            if len(self._buckets) > _MAX_TRACKED_KEYS:
                self._buckets.popitem(last=False)

            return verdict

    def _evict(self, now: float) -> None:
        """Drop buckets that have been full and idle long enough to be noise.

        The refill is applied BEFORE the comparison, not after. Checking the
        stored `tokens` directly is wrong in a way that is easy to miss: a
        bucket that spent one token and was then left alone for an hour still
        holds `capacity - 1` on the record, because `consume` is what refills
        it. Judging eviction on the stored value therefore never evicts a
        partially-used bucket, the map grows until the hard cap, and the
        eviction pass silently stops doing anything.
        """
        stale = []
        for key, bucket in self._buckets.items():
            if now - bucket.updated <= _IDLE_EVICTION_SECONDS:
                continue
            refilled = min(
                self.capacity,
                bucket.tokens + (now - bucket.updated) * self.rate,
            )
            if refilled >= self.capacity:
                stale.append(key)

        for key in stale:
            self._buckets.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._buckets)

    def limitations(self) -> dict[str, object]:
        """What this limiter does not do, in a form the API can disclose."""
        return {
            "enabled": self.enabled,
            "per_minute": self.per_minute,
            "burst": self.burst,
            "algorithm": "token bucket, per API key (client address if unauthenticated)",
            "storage": "in-process",
            "tracked_keys": self.tracked_keys(),
            "caveat": (
                "Buckets live in process memory. Running uvicorn with more than "
                "one worker multiplies the effective limit by the worker count, "
                "because each worker keeps its own buckets. A shared limiter "
                "needs shared storage."
            ),
            "exempt": ["/api/health"],
        }
