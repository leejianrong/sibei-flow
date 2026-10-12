-- The post-rename upstream state for this case. It is the same state the
-- fixture warehouse boots into (db/warehouse/init.sql), written idempotently so
-- running the case never disturbs the other infra tests that share `raw`.
-- New cases should build their OWN schema (e.g. `eval_<case_id>`) and drop it in
-- teardown.sql instead of touching `raw`.

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

GRANT USAGE ON SCHEMA raw TO sbflow_ro, sbflow_dev;
GRANT SELECT ON ALL TABLES IN SCHEMA raw TO sbflow_ro, sbflow_dev;
