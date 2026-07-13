-- Hero pipeline seed — the HEALTHY / PRE-rename upstream state.
--
-- Run by `make hero` (against the running `warehouse` service) *before* the
-- first `analytics_daily` DAG run, so that run goes GREEN: raw_customers still
-- has `customer_id`, which orders.sql references.
--
-- This is deliberately NOT `db/warehouse/init.sql`. init.sql seeds the
-- POST-rename state (`cust_id`) that the worker tests (test_agent_loop /
-- test_sandbox) and the fixture-payload demo depend on, and it only runs once on
-- a fresh volume. The hero flow instead drives the state live:
--   make hero        -> this script (pre-rename, healthy)   -> DAG green
--   make hero-break  -> hero_break.sql (rename)             -> DAG fails -> heal
-- The end state after the break (cust_id present, customer_id gone) matches what
-- init.sql produces, which is exactly the drift the worker expects.
--
-- Idempotent: safe to re-run. Resets raw_customers to the healthy state.

CREATE SCHEMA IF NOT EXISTS raw;

-- Recreate the upstream table in the PRE-rename (healthy) shape.
DROP TABLE IF EXISTS raw.raw_customers CASCADE;
CREATE TABLE raw.raw_customers (
    customer_id integer PRIMARY KEY,   -- healthy: not yet drifted to cust_id
    order_ts    timestamptz,
    amount      numeric(12, 2)
);

INSERT INTO raw.raw_customers (customer_id, order_ts, amount) VALUES
    (1, '2026-07-08 09:00:00+00', 42.00),
    (2, '2026-07-08 10:30:00+00', 19.95),
    (3, '2026-07-09 01:15:00+00', 128.50);

-- The pipeline's OWN application role. Distinct from sibei-flow's read-only
-- (sbflow_ro) and dev/sample (sbflow_dev) roles: Airflow's dbt runs use this to
-- build staging + mart views. sibei-flow never holds this credential — it only
-- reads (sbflow_ro) or builds into a sample schema (sbflow_dev). No prod-write
-- credential lives anywhere in sibei-flow itself.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_app') THEN
        CREATE ROLE analytics_app LOGIN PASSWORD 'analytics_app';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE warehouse TO analytics_app;
GRANT USAGE, CREATE ON SCHEMA raw TO analytics_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA raw TO analytics_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO analytics_app;
-- A schema for the DAG's built models (staging + marts materialize here).
CREATE SCHEMA IF NOT EXISTS analytics AUTHORIZATION analytics_app;

-- Re-grant sibei-flow's read-only + sample roles on the freshly recreated table
-- (DROP TABLE dropped the old grants). These roles are created by init.sql; if
-- the volume was seeded elsewhere they may be absent, so guard the grants.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sbflow_ro') THEN
        GRANT USAGE ON SCHEMA raw TO sbflow_ro;
        GRANT SELECT ON raw.raw_customers TO sbflow_ro;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sbflow_dev') THEN
        GRANT USAGE ON SCHEMA raw TO sbflow_dev;
        GRANT SELECT ON raw.raw_customers TO sbflow_dev;
    END IF;
END
$$;
