-- Fixture warehouse for V2 get_schema drift detection.
--
-- Models the POST-rename upstream state: raw_customers.customer_id has been
-- renamed to cust_id. The failing orders model still references customer_id, so
-- get_schema shows customer_id gone / cust_id present — the drift the agent fixes.
--
-- Runs once on a fresh volume (docker-entrypoint-initdb.d) in the `warehouse` DB.

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.raw_customers (
    cust_id    integer PRIMARY KEY,   -- was customer_id (renamed upstream)
    order_ts   timestamptz,
    amount     numeric(12, 2)
);

INSERT INTO raw.raw_customers (cust_id, order_ts, amount) VALUES
    (1, '2026-07-08 09:00:00+00', 42.00),
    (2, '2026-07-08 10:30:00+00', 19.95),
    (3, '2026-07-09 01:15:00+00', 128.50)
ON CONFLICT DO NOTHING;

-- Read-only role the worker connects as (R2.1 / R3.1: no standing write access).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sbflow_ro') THEN
        CREATE ROLE sbflow_ro LOGIN PASSWORD 'sbflow_ro';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE warehouse TO sbflow_ro;
GRANT USAGE ON SCHEMA raw, information_schema TO sbflow_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA raw TO sbflow_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw GRANT SELECT ON TABLES TO sbflow_ro;
