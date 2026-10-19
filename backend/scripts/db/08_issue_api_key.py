"""Issue an API key for the FastAPI layer.

USAGE
-----
    .venv/Scripts/python.exe backend/scripts/db/08_issue_api_key.py --label "frontend dev"
    .venv/Scripts/python.exe backend/scripts/db/08_issue_api_key.py --label ci --scopes analytics:read
    .venv/Scripts/python.exe backend/scripts/db/08_issue_api_key.py --list
    .venv/Scripts/python.exe backend/scripts/db/08_issue_api_key.py --revoke omr_abc12345

THE PLAINTEXT IS SHOWN ONCE
---------------------------
This is the only moment the key exists in readable form. It is printed, never
written to a file, and cannot be recovered afterwards - the database holds an
Argon2id hash and nothing else.

That is a deliberate trade. The alternative (encrypt at rest with a key from
.env) means a database dump plus a stolen .env yields working credentials; a
hash means the dump alone yields nothing, and a forgotten key is reissued rather
than recovered.

Runs as the read/write `omniretail` role because it must INSERT. The API itself
only ever SELECTs from this table.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.core.security import (  # noqa: E402
    ALL_SCOPES,
    generate_api_key,
    hash_key,
    key_prefix,
    parse_scopes,
)

BANNER = """
================================================================================
  API KEY - COPY THIS NOW. IT WILL NOT BE SHOWN AGAIN.
================================================================================
"""


def connect():
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


def table_exists(conn) -> bool:
    return conn.execute(
        "SELECT to_regclass('app.api_keys') IS NOT NULL"
    ).fetchone()[0]


def create_key(
    conn,
    label: str,
    scopes: str,
    expires=None,
    created_by: str | None = None,
) -> tuple[int, str]:
    """Insert one key. Returns `(id, plaintext)`.

    Extracted from `issue()` so there is ONE place that writes a key row.
    `issue()` prints the banner and returns an exit code, which is right for a
    CLI and useless to a caller that needs the value programmatically - which is
    exactly what the API test fixture needs. Duplicating the INSERT there would
    have meant two implementations of the credential-creation path, one of them
    exercised only by tests.
    """
    key = generate_api_key()
    row = conn.execute(
        """
        INSERT INTO app.api_keys
            (key_prefix, key_hash, label, scopes, is_active, created_at,
             expires_at, created_by)
        VALUES (%s, %s, %s, %s, TRUE, now(), %s, %s)
        RETURNING id, created_at
        """,
        (key_prefix(key), hash_key(key), label, scopes, expires, created_by),
    ).fetchone()
    return row[0], key


def issue(args) -> int:
    scopes = ",".join(sorted(parse_scopes(args.scopes))) if args.scopes else ",".join(
        ALL_SCOPES
    )
    expires = (
        datetime.now(timezone.utc) + timedelta(days=args.expires_days)
        if args.expires_days
        else None
    )

    with connect() as conn:
        if not table_exists(conn):
            print(
                "app.api_keys does not exist. Run:\n"
                "    .venv/Scripts/python.exe -m alembic upgrade head",
                file=sys.stderr,
            )
            return 1
        key_id, key = create_key(conn, args.label, scopes, expires, args.created_by)
        row = conn.execute(
            "SELECT id, created_at FROM app.api_keys WHERE id = %s", (key_id,)
        ).fetchone()

    print(BANNER)
    print(f"  {key}")
    print("=" * 80)
    print(f"  id        : {row[0]}")
    print(f"  prefix    : {key_prefix(key)}")
    print(f"  label     : {args.label}")
    print(f"  scopes    : {scopes}")
    print(f"  created   : {row[1]}")
    print(f"  expires   : {expires or 'never'}")
    print()
    print("Use it as:   X-API-Key: <the key above>")
    print("Store it in .env as API_KEY=<the key above>. Never commit it.")
    return 0


def list_keys(_args) -> int:
    with connect() as conn:
        if not table_exists(conn):
            print("app.api_keys does not exist; nothing issued yet.")
            return 0
        rows = conn.execute(
            """
            SELECT id, key_prefix, label, scopes, is_active, created_at,
                   last_used_at, expires_at
            FROM app.api_keys ORDER BY id
            """
        ).fetchall()

    if not rows:
        print("no keys issued yet.")
        return 0

    print(f"{'id':>3}  {'prefix':<14} {'label':<22} {'scopes':<34} "
          f"{'active':<7} {'last used':<21} expires")
    for r in rows:
        last = r[6].isoformat(sep=" ") if r[6] else "never"
        exp = r[7].isoformat(sep=" ") if r[7] else "never"
        print(f"{r[0]:>3}  {r[1]:<14} {r[2]:<22} {r[3]:<34} "
              f"{str(r[4]):<7} {last:<21} {exp}")
    print()
    print("Note: a hash cannot be recovered, so a lost key must be reissued.")
    return 0


def revoke(args) -> int:
    with connect() as conn:
        if not table_exists(conn):
            print("app.api_keys does not exist.", file=sys.stderr)
            return 1
        # Deactivated rather than deleted: the row records that a key existed and
        # when it was revoked, which a DELETE would erase.
        updated = conn.execute(
            """
            UPDATE app.api_keys SET is_active = FALSE
            WHERE key_prefix LIKE %s AND is_active
            RETURNING id, label
            """,
            (args.revoke + "%",),
        ).fetchall()

    if not updated:
        print(f"no active key with prefix {args.revoke!r}", file=sys.stderr)
        return 1
    for row in updated:
        print(f"revoked id={row[0]} ({row[1]})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    issue_parser = sub.add_parser("issue", help="mint a new key (prints it once)")
    issue_parser.add_argument("--label", required=True,
                              help="who this key is for, e.g. 'frontend dev'")
    issue_parser.add_argument("--scopes", default=None,
                              help=f"comma-separated, subset of {','.join(ALL_SCOPES)}")
    issue_parser.add_argument("--expires-days", type=int, default=None,
                              help="expiry in days; omit for a non-expiring key")
    issue_parser.add_argument("--created-by", default="interactive",
                              help="recorded for the audit trail")
    issue_parser.set_defaults(func=issue)

    list_parser = sub.add_parser("list", help="show issued keys (never the secret)")
    list_parser.set_defaults(func=list_keys)

    revoke_parser = sub.add_parser("revoke", help="deactivate a key by prefix")
    revoke_parser.add_argument("revoke", help="the key prefix, e.g. omr_ab12cd34")
    revoke_parser.set_defaults(func=revoke)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())