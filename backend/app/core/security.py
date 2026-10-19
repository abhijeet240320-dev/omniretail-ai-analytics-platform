"""Password/key hashing and token generation.

WHY ARGON2ID AND NOT SHA-256
-----------------------------
An API key is a 256-bit random value, so unlike a password it has no low
entropy to brute-force offline. A fast hash would therefore be defensible for the
*key* itself.

Argon2id is used anyway, for two reasons that have nothing to do with entropy:

1. It makes a leaked database unusable if a weak key is ever issued. The cost
   parameters mean an attacker who reads `app.api_keys` faces a deliberate
   slowdown, so a human-chosen key (`"omniretail-dev"`) is not a credential
   anyone gets into a deploy script and leaves there.
2. It means the door is open to a real user table later without a format change.

The cost parameters are deliberately the library's DEFAULTS rather than
something tuned faster. That is the whole point: the parameters are the defence,
and lowering them for benchmark reasons is how they stop being one.

THE KEY FORMAT
--------------
    omr_<base62 random>

`key_prefix` stores the first 12 characters, which is the `omr_` marker plus 8
characters of entropy. That is enough to identify a key in a log without being
useful to anyone. It is public, it is indexed, and it is what makes lookup a
single indexed read instead of hashing every candidate row.

A constant-time comparison is used where two secrets are compared directly.
Argon2 verification is itself constant-time with respect to the hash contents,
so the prefix lookup does not need to be - but `secrets.compare_digest` is used
anyway for the direct comparisons, because it is free and the habit is what
matters.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Library defaults, spelled out so they are visible and so a future edit has to
# be deliberate. Do not lower these for speed.
_TIME_COST = 3
_MEMORY_COST = 65536  # KiB = 64 MiB per hash
_PARALLELISM = 4
_HASH_LEN = 32
_SALT_LEN = 16

KEY_PREFIX_LENGTH = 12
KEY_MARKER = "omr_"

_hasher = PasswordHasher(
    time_cost=_TIME_COST,
    memory_cost=_MEMORY_COST,
    parallelism=_PARALLELISM,
    hash_len=_HASH_LEN,
    salt_len=_SALT_LEN,
)


def generate_api_key() -> str:
    """Mint a new key. The only time the plaintext exists.

    32 bytes from `secrets` = 256 bits of entropy. `token_urlsafe` is base64url,
    which survives a header without escaping.
    """
    return f"{KEY_MARKER}{secrets.token_urlsafe(32)}"


def key_prefix(key: str) -> str:
    """The public identifying fragment stored alongside the hash."""
    return key[:KEY_PREFIX_LENGTH]


def hash_key(key: str) -> str:
    """Hash a key for storage. Never reversible, never logged."""
    return _hasher.hash(key)


def verify_key(stored_hash: str, presented_key: str) -> bool:
    """Check a presented key against a stored hash.

    Returns False rather than raising for every failure, because "wrong key" and
    "corrupt hash" are the same thing to a caller: this key is not valid.
    """
    try:
        return _hasher.verify(stored_hash, presented_key)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when a stored hash used weaker parameters than the current ones.

    Re-hashing on the next successful authentication is the standard way to
    upgrade old hashes without an outage: the plaintext is available at exactly
    one moment - when the caller presents it - and never stored.
    """
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two secrets without leaking their contents through timing."""
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------
# Scopes are additive: holding any of a set of acceptable scopes grants access.
# There is no hierarchy. `admin:read` is not "more than" `analytics:read`, it is
# simply a different permission, and pretending otherwise is how a scope system
# ends up with `admin` meaning "everything" by accident.

SCOPE_ANALYTICS_READ = "analytics:read"
SCOPE_AI_READ = "ai:read"
SCOPE_ADMIN_READ = "admin:read"

ALL_SCOPES = (SCOPE_ANALYTICS_READ, SCOPE_AI_READ, SCOPE_ADMIN_READ)


def parse_scopes(raw: str) -> set[str]:
    """Parse the comma-separated scope column.

    Unknown scopes are dropped rather than trusted. A row edited by hand to grant
    `"analytics:write"` must not produce a scope this code does not understand -
    the safe behaviour is to ignore what you do not recognise.
    """
    return {s.strip() for s in (raw or "").split(",") if s.strip()} & set(ALL_SCOPES)


def has_scope(granted: set[str], required: str) -> bool:
    return required in granted