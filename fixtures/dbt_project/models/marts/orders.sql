-- Flagship failing model for the demo.
--
-- `raw_customers.customer_id` was renamed upstream to `cust_id`, so this
-- reference breaks with:  column "customer_id" does not exist  (schema drift).
-- In later slices the agent drafts the one-line fix (customer_id -> cust_id).

with customers as (
    select * from {{ source('raw', 'raw_customers') }}
)

select
    customer_id,
    order_ts,
    amount
from customers
