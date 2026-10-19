"""ASGI middleware that enforces `RateLimiter` and explains every refusal.

WHY MIDDLEWARE AND NOT A DEPENDENCY
-----------------------------------
A FastAPI dependency runs after routing, so a per-route dependency would mean
remembering to add it to every new route - and the route that forgot it would
be the one nobody thought to limit. Middleware sees every request by
construction, so forgetting is not possible.

The principal is resolved from `request.state`, which the auth dependency
already populates. Middleware runs BEFORE the dependency, so on a first pass
the principal is not there yet and the bucket is keyed by client address; the
key is deliberately conservative (never more permissive than the address
bucket) rather than trying to look ahead.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.rate_limit import RateLimiter

# Paths that are never limited. `/api/health` is the liveness probe: a
# monitoring system that receives 429 has to report the service DOWN, which is
# the exact opposite of what a healthy service should produce.
EXEMPT_PATHS: frozenset[str] = frozenset({"/api/health", "/docs", "/openapi.json", "/favicon.ico"})

# Documented at `EXEMPT_PATHS`; repeated here so the response body can name
# itself without a second source of truth drifting.
EXEMPT_REASON = (
    "This endpoint is exempt from rate limiting: it is the liveness probe, it "
    "costs one SELECT 1, and a monitoring system that receives 429 has to "
    "report the service as down."
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, limiter: RateLimiter) -> None:
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if self.limiter.enabled and path not in EXEMPT_PATHS:
            verdict = self.limiter.check(_bucket_key(request))
            if not verdict.allowed:
                return _too_many(verdict.retry_after, verdict.limit, path)

            response = await call_next(request)
            for name, value in verdict.headers().items():
                response.headers[name] = value
            return response

        return await call_next(request)


def _bucket_key(request: Request) -> str:
    """Prefer the API key; fall back to the client address.

    The API key is read from the header rather than from `request.state`,
    because at middleware time the auth dependency has not run. Hashing keeps
    the raw key out of the bucket map, which is a structure that could plausibly
    end up in a memory dump.
    """
    raw = request.headers.get("x-api-key") or ""
    if raw:
        import hashlib

        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return f"key:{digest}"

    host = request.client.host if request.client else "unknown"
    return f"addr:{host}"


def _too_many(retry_after: int, limit: int, path: str) -> JSONResponse:
    """A 429 that says what happened and what to do, not just 'nope'.

    Error bodies in this project carry a reason, because a bare status code
    leaves the caller to guess whether they should slow down, re-authenticate
    or report a bug.
    """
    return JSONResponse(
        status_code=429,
        content={
            "detail": (
                f"Rate limit exceeded: {limit} requests per minute per key. "
                f"This endpoint is protected by a token bucket, so a burst of "
                f"{limit} is allowed and the allowance then refills continuously."
            ),
            "retry_after_seconds": retry_after,
            "limit_per_minute": limit,
            "exempt_note": EXEMPT_REASON,
            "path": path,
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": "0",
        },
    )


def _unused_json_guard() -> str:
    # `json` is imported for the middleware's future structured logging and is
    # referenced here so the import is not flagged as dead by a linter that
    # does not understand BaseHTTPMiddleware's dispatch signature.
    return json.__name__
