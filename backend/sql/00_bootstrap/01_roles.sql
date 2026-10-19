-- =====================================================================
-- 01_roles.sql  -  create the two application roles
-- =====================================================================
-- Run against the maintenance database (usually "postgres"):
--
--   psql -h 127.0.0.1 -p 5433 -U postgres -d postgres \
--        -v rw_password=... -v ro_password=... \
--        -f backend/sql/00_bootstrap/01_roles.sql
--
-- WHY TWO ROLES FROM DAY ONE
-- --------------------------
-- The read/write role loads data, runs migrations and writes analytics
-- outputs. The read-only role exists solely for the AI natural-language
-- layer, and its whole purpose is DEFENCE IN DEPTH:
--
--   * If the SQL validator is bypassed, the database refuses the write.
--   * If a bug or an injected statement reaches the server, it is rejected
--     by privilege, not by our own parsing code.
--
-- Two independent controls (request validation + database privilege) mean a
-- single mistake is not enough to corrupt data. That is a fundamentally
-- different risk posture from "trust our regex".
--
-- The read-only role additionally carries default_transaction_read_only=on,
-- so a session started as that role cannot write even by mistake. This is
-- the pointer the codebase relies on: the AI never holds a write credential.
-- =====================================================================

-- NOTE: this file is PURE SQL on purpose, so it can be executed by psycopg
-- during `bootstrap_db.py`. psql-only meta-commands (\set, \gexec) are
-- deliberately absent; the Python runner supplies the passwords as bound
-- parameters and stops on the first error by default.

-- Roles are cluster-level objects, so these statements are not name-spaced.
-- DO blocks make the script idempotent: running it twice is harmless.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omniretail') THEN
    CREATE ROLE omniretail LOGIN;
    RAISE NOTICE 'created role omniretail';
  ELSE
    RAISE NOTICE 'role omniretail already exists';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'omniretail_readonly') THEN
    CREATE ROLE omniretail_readonly LOGIN;
    RAISE NOTICE 'created role omniretail_readonly';
  END IF;
END
$$;

-- The passwords are substituted by backend/scripts/db/bootstrap_db.py from
-- the same Settings object the application uses, so there is exactly one
-- source of truth for credentials and it never appears in this file.
-- ALTER ROLE keeps the script re-runnable: the password is brought in line
-- with whatever .env currently says.
ALTER ROLE omniretail
      WITH LOGIN
           PASSWORD :'rw_password'
           NOSUPERUSER
           NOCREATEDB
           NOCREATEROLE
           NOBYPASSRLS;

ALTER ROLE omniretail_readonly
      WITH LOGIN
           PASSWORD :'ro_password'
           NOSUPERUSER
           NOCREATEDB
           NOCREATEROLE
           NOBYPASSRLS;

-- The read-only default is a SEPARATE statement on purpose. PostgreSQL's
-- grammar treats "ALTER ROLE name SET parameter ..." as its own command
-- form; it cannot be appended to the option list above (that is a syntax
-- error at the SET keyword).
--
-- Why it matters: with this setting, EVERY session opened as the AI role
-- starts in a read-only transaction. Even if a future GRANT accidentally
-- hands it a write privilege, the write still fails with "cannot execute
-- ... in a read-only transaction". Two independent controls, not one.
ALTER ROLE omniretail_readonly SET default_transaction_read_only = on;

-- Explanation of the attributes above:
--   NOSUPERUSER    - never a superuser; cannot bypass any check.
--   NOCREATEDB     - cannot create databases.
--   NOCREATEROLE   - cannot create or escalate roles.
--   NOBYPASSRLS    - cannot ignore row-level security policies.
--   default_transaction_read_only = on (read-only role only) - writes fail
--     at the transaction level regardless of table privileges.
