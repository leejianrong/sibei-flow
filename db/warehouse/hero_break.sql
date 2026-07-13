-- Hero pipeline break — the flagship schema drift.
--
-- Run by `make hero-break`: an upstream rename of `customer_id -> cust_id`.
-- After this, orders.sql (which still selects `customer_id`) fails a real
-- `dbt build` with:  column "customer_id" does not exist.
--
-- This is the canonical rename from PRD.md §Acceptance. The staging model
-- (stg_customers, `select *`) survives it, so the failure surfaces precisely at
-- the `dbt_build_orders` task — the hero story's single failing node.

ALTER TABLE raw.raw_customers RENAME COLUMN customer_id TO cust_id;
