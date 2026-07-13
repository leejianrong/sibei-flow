-- Staging passthrough over the raw upstream (hero pipeline's `dbt_run_staging`).
--
-- Deliberately `select *` so it survives the flagship column rename
-- (customer_id -> cust_id): the drift must surface at `dbt_build_orders`
-- (the marts model that names `customer_id`), not here. This keeps the failing
-- task precise for the hero story while making the DAG a genuine multi-stage
-- pipeline rather than a single model.

select * from {{ source('raw', 'raw_customers') }}
