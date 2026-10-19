"""Authentication and authorization dependencies.

THE ORDER MATTERS AND IS NOT INTERCHANGEABLE
--------------------------------------------
1. no credential          -> 401, says so
2. credential invalid     -> 401, says so
3. credential valid but
   lacking the scope      -> 403, says which scope is needed

Collapsing 1 and 2 into one response ("unauthorized") is what makes brute-force
detection possible: a caller cannot tell a wrong key from a missing one. Keeping
3 separate from 1 and 2 matters because they are different problems with
different fixes - the first is "send credentials", the second is "use a
different key".

WHY THE HEADER, NOT A BEARER TOKEN
----------------------------------
An API key in `X-API-Key` is chosen over JWT because this is a read-only local
analytics API with no user accounts, and a JWT would require a signing secret, a
user store, token refresh and expiry handling - four pieces of infrastructure to
answer a question this deployment does not ask.

The format is deliberately not OAuth-style `Bearer`, so that an existing
credential-helper or proxy rule that forwards `Authorization` headers does not
accidentally forward a credential to a different service.

WHAT THIS DOES NOT PROTECT AGAINST
----------------------------------
An API key cannot express "this user may see this row". It says "this caller may
call this endpoint". Row-level authorisation is not implemented because there
are no multi-tenant rows - the four source schemas are public datasets. Adding
row-level checks before they are needed would be complexity with no threat
behind it.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import (
    SCOPE_ADMIN_READ,
    SCOPE_AI_READ,
    SCOPE_ANALYTICS_READ,
    has_scope,
    hash_key,
    key_prefix,
    needs_rehash,
    parse_scopes,
    verify_key,
)
from app.database.readonly import get_readonly_session

API_KEY_HEADER = "X-API-Key"

# A real Argon2id hash of a value nobody knows, computed once. Verifying against
# it when no row matched costs exactly what verifying against a genuine key
# costs, which is what closes the prefix-enumeration timing gap.
_DUMMY_HASH = hash_key("omniretail-timing-equaliser-not-a-real-key")


@dataclass(frozen=True)
class Principal:
    """Who is calling, and what they may do.

    Carries the key id and label so an audit log can attribute an action without
    storing or logging the credential itself.
    """

    key_id: int
    label: str
    scopes: frozenset[str]
    prefix: str

    def require(self, scope: str) -> None:
        if not has_scope(set(self.scopes), scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"key {self.prefix!r} ({self.label}) lacks the {scope!r} "
                    f"scope. It holds: "
                    f"{', '.join(sorted(self.scopes)) or 'none'}."
                ),
            )


def authenticate(
    x_api_key: str | None = Header(
        None,
        alias=API_KEY_HEADER,
        description="API key issued by backend/scripts/db/08_issue_api_key.py",
    ),
    session: Session = Depends(get_readonly_session),
) -> Principal:
    """Resolve the presented key to a Principal, or refuse.

    The prefix lookup is an indexed single-row read; the hash verification is the
    expensive part and happens only for that one row. Verifying against every key
    would turn an O(1) lookup into O(n) and would also mean an attacker learns
    nothing about which key was close.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"missing {API_KEY_HEADER} header",
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )

    prefix = key_prefix(x_api_key)
    row = session.execute(
        text(
            """
            SELECT id, key_prefix, key_hash, label, scopes, is_active,
                   expires_at,
                   -- Expiry is decided HERE, in SQL, not in Python.
                   --
                   -- `expires_at` is `timestamp without time zone`, and this
                   -- cluster's session timezone is Asia/Calcutta (UTC+05:30).
                   -- The value written into it therefore comes from the
                   -- server's LOCAL clock. Comparing it in Python against
                   -- `datetime.now(timezone.utc)` reads that local value as if
                   -- it were UTC and therefore sees an instant
                   -- (server_local + 5:30) hours in the FUTURE - which is how a
                   -- key expired at 23:51 local was still accepted at 23:53
                   -- local, on a 5.5 hour overshoot.
                   --
                   -- Comparing in SQL puts both sides in the same zone by
                   -- construction and removes the assumption entirely. It also
                   -- cannot drift if the server's timezone changes, because it
                   -- reads `now()` from the same session it reads the row from.
                   (expires_at IS NOT NULL AND expires_at < now()) AS is_expired
            FROM app.api_keys
            WHERE key_prefix = :prefix
            """
        ),
        {"prefix": prefix},
    ).mappings().first()

    # TIMING: both branches must cost the same.
    #
    # `row is None or not verify_key(...)` short-circuits - an absent prefix
    # would return without ever running Argon2, and an attacker could enumerate
    # valid prefixes by timing the difference. Measured before this fix: 5.1ms
    # for an absent key against 124.4ms for a present one, a 24x gap that names
    # every issued key.
    #
    # So an absent row is verified against a fixed dummy hash instead. Same work,
    # same cost, no information leaked. The dummy is a real Argon2 hash of a
    # value nobody has, computed once at import.
    if row is None:
        verify_key(_DUMMY_HASH, x_api_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API key",
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )

    # The prefix identifies the row, not the key. A wrong key whose prefix
    # matches must still fail, so verification is mandatory even here.
    if not verify_key(row["key_hash"], x_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API key",
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )

    if not row["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"API key {row['label']!r} has been revoked",
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )

    if row["expires_at"] is not None and row["is_expired"]:
        # The expiry comparison itself happened in SQL - see the comment on the
        # query above for why it must not be done in Python here.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"API key {row['label']!r} expired at "
                f"{row['expires_at'].isoformat()} (server local time)"
            ),
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )

    scopes = frozenset(parse_scopes(row["scopes"]))
    principal = Principal(
        key_id=row["id"],
        label=row["label"],
        scopes=scopes,
        prefix=row["key_prefix"],
    )

    # last_used_at is deliberately NOT updated here. The session is read-only and
    # must stay that way - a "harmless" UPDATE on an authentication path is how
    # a read-only API acquires a write it never declared. Recording usage is a
    # writer-role concern and belongs in a separate audited path, or nowhere.
    #
    # needs_rehash is surfaced by /api/status for an operator to act on, rather
    # than silently rehashed mid-request for the same reason.
    return principal


def require_analytics(
    principal: Principal = Depends(authenticate),
) -> Principal:
    principal.require(SCOPE_ANALYTICS_READ)
    return principal


def require_ai(principal: Principal = Depends(authenticate)) -> Principal:
    """For the AI analyst, NL-to-SQL and RAG endpoints.

    A separate scope from analytics because those endpoints let a caller cause
    the server to construct and execute SQL. Read-only execution bounds the
    damage, but letting an arbitrary reader reach SQL generation is still a
    materially different capability from browsing fixed queries.
    """
    principal.require(SCOPE_AI_READ)
    return principal


def require_admin(principal: Principal = Depends(authenticate)) -> Principal:
    principal.require(SCOPE_ADMIN_READ)
    return principal