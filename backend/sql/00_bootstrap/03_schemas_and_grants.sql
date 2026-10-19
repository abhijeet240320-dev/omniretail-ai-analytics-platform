-- =====================================================================
-- 03_schemas_and_grants.sql  -  schema layout + privileges
-- =====================================================================
-- Run against the omniretail database:
--
--   psql -h 127.0.0.1 -p 5433 -U postgres -d omniretail \
--        -f backend/sql/00_bootstrap/03_schemas_and_grants.sql
--
-- WHY SCHEMA-PER-SOURCE (and not one shared schema)
-- ------------------------------------------------
-- The four datasets describe FOUR DIFFERENT ORGANISATIONS. They have no
-- shared key space, no shared customer, no shared product and no shared
-- calendar of operations. Putting them in one namespace would invite exactly
-- the mistake this project exists to avoid: writing
--
--     JOIN olist.customers c ON c.customer_id = dunnhumby.households.id
--
-- which is meaningless because "customer 123" in one dataset has nothing to
-- do with "customer 123" in another. Four schemas make that mistake hard to
-- write by accident and easy to spot in review: every table name carries its
-- provenance.
--
--   dunnhumby  - grocery households, baskets, products, promotions, coupons
--   dataco     - supply-chain orders, shipping, profitability
--   olist      - e-commerce orders, items, payments, reviews, geography
--   m5         - Walmart demand series, calendar, prices
--   analytics  - curated views, KPI definitions, ML outputs. This is the ONLY
--                layer permitted to combine sources, and only where the
--                concepts are genuinely compatible, always with a
--                source_system column preserved.
-- =====================================================================

-- Pure SQL (no psql meta-commands): see the note in 01_roles.sql.

-- Annotate the database itself. This lives here rather than in
-- 02_database.sql because a batch containing CREATE DATABASE plus anything
-- else is treated as one transaction, and CREATE DATABASE cannot run in a
-- transaction block. COMMENT is idempotent, so re-running is harmless.
COMMENT ON DATABASE omniretail IS
  'OmniRetail AI Analytics Platform - multi-source real retail data '
  '(dunnhumby, DataCo, Olist, M5). Each source keeps its own schema; '
  'no cross-source ID joins are permitted.';

CREATE SCHEMA IF NOT EXISTS dunnhumby AUTHORIZATION omniretail;
CREATE SCHEMA IF NOT EXISTS dataco    AUTHORIZATION omniretail;
CREATE SCHEMA IF NOT EXISTS olist     AUTHORIZATION omniretail;
CREATE SCHEMA IF NOT EXISTS m5        AUTHORIZATION omniretail;
CREATE SCHEMA IF NOT EXISTS analytics AUTHORIZATION omniretail;

COMMENT ON SCHEMA dunnhumby IS 'Source: dunnhumby The Complete Journey. Grocery customer/promotion analytics.';
COMMENT ON SCHEMA dataco    IS 'Source: DataCo SMART Supply Chain. Operations and late-delivery analytics.';
COMMENT ON SCHEMA olist     IS 'Source: Olist Brazilian E-Commerce. E-commerce order/review/geo analytics.';
COMMENT ON SCHEMA m5        IS 'Source: Walmart M5. Daily demand series, calendar and prices.';
COMMENT ON SCHEMA analytics IS 'Cross-source curated layer. Every table carries source_system.';

-- ---------------------------------------------------------------------
-- Read-only privileges for the AI analyst role
-- ---------------------------------------------------------------------
-- The AI must be able to read every domain and write to none of them.
GRANT CONNECT ON DATABASE omniretail TO omniretail_readonly;

GRANT USAGE ON SCHEMA dunnhumby, dataco, olist, m5, analytics
      TO omniretail_readonly;

-- Explicitly deny the things that would let the role reshape the database.
-- PostgreSQL does not grant these by default, but stating them makes the
-- intent auditable and keeps the posture correct if defaults ever change.
REVOKE CREATE ON SCHEMA dunnhumby, dataco, olist, m5, analytics
       FROM omniretail_readonly;
REVOKE CREATE ON DATABASE omniretail FROM omniretail_readonly;

-- Read every existing table/view.
GRANT SELECT ON ALL TABLES IN SCHEMA dunnhumby, dataco, olist, m5, analytics
      TO omniretail_readonly;

-- And read anything created LATER by the application role. Without this,
-- every new table added by a migration would silently be invisible to the
-- AI analyst until someone remembered to re-run a GRANT - a classic
-- production bug.
ALTER DEFAULT PRIVILEGES FOR ROLE omniretail
      IN SCHEMA dunnhumby, dataco, olist, m5, analytics
      GRANT SELECT ON TABLES TO omniretail_readonly;

-- The Alembic revision table lives in `public`, which is outside the five
-- source schemas above. The API's /api/status endpoint reports the current
-- revision, so the read-only role needs to READ it - and only read it.
--
-- This is granted conditionally because `alembic_version` is created by
-- `alembic stamp`, which runs AFTER this bootstrap. Granting unconditionally
-- would abort on a database that has never been stamped, and aborting the
-- bootstrap over a read-only grant is a bad trade.
DO $$
BEGIN
  IF to_regclass('public.alembic_version') IS NOT NULL THEN
    EXECUTE 'GRANT SELECT ON public.alembic_version TO omniretail_readonly';
  END IF;
END
$$;

-- Sequences are readable because the AI may need them for metadata queries,
-- but it still cannot write.
ALTER DEFAULT PRIVILEGES FOR ROLE omniretail
      IN SCHEMA dunnhumby, dataco, olist, m5, analytics
      GRANT USAGE, SELECT ON SEQUENCES TO omniretail_readonly;

-- ---------------------------------------------------------------------
-- Cross-check: the AI role must NOT be able to write.
-- ---------------------------------------------------------------------
-- This DO block fails loudly if the read-only role somehow ends up with a
-- write privilege on an existing table. It turns "we think it is locked
-- down" into "we verified it is locked down".
DO $$
DECLARE
  offending record;
BEGIN
  FOR offending IN
    SELECT table_schema, table_name, privilege_type
    FROM information_schema.role_table_grants
    WHERE grantee = 'omniretail_readonly'
      AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER')
  LOOP
    RAISE EXCEPTION
      'read-only role has % on %.%', offending.privilege_type,
      offending.table_schema, offending.table_name;
  END LOOP;
  RAISE NOTICE 'verified: omniretail_readonly holds no write privileges';
END
$$;
