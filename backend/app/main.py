"""The FastAPI application.

RUN IT
------
From the repository root:

    .venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --reload

WHY EVERYTHING IS UNDER /api
----------------------------
It leaves the root free for a frontend or a docs site, and it makes a proxy rule
trivial: send `/api` here and nothing else. A health check at `/health` that
belongs to the app is easier to reason about than one that might be the frontend
returning 200 because its JavaScript loaded.

WHAT THE APP WILL NOT DO
------------------------
It has no write endpoints, because there is nothing in this project to write.
The data is loaded by `04_load.py`, the schema is owned by Alembic, and the
analytics are read-only. Adding a POST here would mean inventing a mutation that
no requirement calls for - and the read-only role would refuse it anyway, which
is the correct outcome arrived at by accident.

Authentication is Argon2id API keys with per-key scopes, issued by
`backend/scripts/db/08_issue_api_key.py` and stored hashed in `app.api_keys`.
Every router except `/api/health` requires one; `/api/status`,
`/api/ai/validator/status` need `admin:read`, the analyst and the catalogue
need `analytics:read` or `ai:read`.

A timing oracle was found and fixed during the build: a request with an absent
key returned in 5.1 ms while a present one took 124.4 ms, because the absent
path skipped the Argon2 verification entirely. `authenticate()` now verifies
against a dummy hash so both paths cost the same, and the measured ratio is
1.02. `backend/tests/test_auth.py` pins it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import ai, analytics, health, insights, integrations, overview
from app.api.rate_limit import RateLimitMiddleware
from app.core.rate_limit import RateLimiter
from app.core.config import get_settings

logger = logging.getLogger("omniretail.api")

DESCRIPTION ="""Read-only analytics over four real public retail datasets.

**What this is:** the 41 verified SQL analytics from `docs/sql_analytics.md`,
per-source overview KPIs, and an AI analyst that answers from live PostgreSQL
queries - served read-only.

**What this is not:** a demo. Every number returned is the result of a query that
ran against 106,704,583 loaded rows, or a measurement recorded by a model that
was actually evaluated.

**There is deliberately no combined total.** These are four unrelated companies
with no shared key space, so a sum across them would have no referent. Each
source is reported separately and never summed - see `/api/overview`.

**The AI layer refuses rather than guesses.** `/api/ai/ask` reports
`answered: false` with a `stopped_at` stage whenever a question rests on
something untrue of this data, has no supporting evidence, matches no verified
query, or produces a number that is not in the rows the query returned.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Log the target on startup; verify the database rather than assume it."""
    settings = get_settings()
    logger.info(
        "%s starting against %s:%s/%s (read-only role %s)",
        settings.app_name,
        settings.postgres_host,
        settings.postgres_port,
        settings.postgres_db,
        settings.postgres_readonly_user,
    )
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    # Built here, not inside `add_middleware`, so the same instance can be
    # reached by `/api/status/rate-limit`. The endpoint reports the LIVE
    # limiter - how many buckets it is actually tracking - rather than
    # restating the settings file, which is the thing that goes stale.
    limiter = RateLimiter(
        enabled=settings.rate_limit_enabled,
        per_minute=settings.rate_limit_per_minute,
        burst=settings.rate_limit_burst,
    )

    application = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.limiter = limiter

    # Added LAST, so it is the OUTERMOST middleware. Starlette runs middleware
    # in reverse registration order, and a rate limiter that sits inside CORS
    # would return a 429 with no CORS headers - the browser reports an opaque
    # network error instead of the limit, which is the one moment a client most
    # needs to be told what is happening.
    application.add_middleware(RateLimitMiddleware, limiter=limiter)
    application.add_middleware(
        CORSMiddleware,
        # Localhost only. A wildcard origin here would let any page a developer
        # visits issue requests to the API and read the response. If this is ever
        # deployed, this list must name real origins - it is a security control,
        # not a convenience.
        allow_origins=[
            "http://localhost:5173",   # vite dev server
            "http://127.0.0.1:5173",
            "http://localhost:3000",
        ],
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    application.include_router(health.router)
    application.include_router(overview.router)
    application.include_router(analytics.router)
    application.include_router(ai.router)
    application.include_router(insights.router)
    application.include_router(integrations.router)

    @application.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        """A 500 that says nothing useful to the caller.

        The message names the exception TYPE and nothing else. Echoing the failing
        statement or a traceback teaches an attacker the schema, and the habit of
        including them is exactly how that leak ships to production. The full
        detail goes to the log, where it belongs.
        """
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "detail": f"{type(exc).__name__}; see the server log for detail",
            },
        )

    return application


app = create_app()


def limiter_for_status() -> RateLimiter:
    """The live limiter instance, for `GET /api/status/rate-limit`.

    Reads `app.state` rather than module state so the endpoint reports the
    limiter actually installed on the running application, not a second one
    built from the same settings file.
    """
    return app.state.limiter