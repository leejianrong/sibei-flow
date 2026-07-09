-- V1 walking skeleton — the durable spine (Postgres source of truth, ADR-0009).
-- Schema is exactly per docs/design/V1-plan.md §"Data model".
--
-- `idem_key` and `lease_expires_at` are populated now but their
-- uniqueness/recovery semantics are deferred to V5 (see SLICES.md).

CREATE TABLE IF NOT EXISTS repair_jobs (
    id               uuid PRIMARY KEY,
    idem_key         text,        -- hash(repo,run_id,task_id,node_uid); unique index in V5
    repo             text,
    run_id           text,
    task_id          text,
    node_uid         text,
    failure_class    text,        -- schema_drift | code_sql | out_of_scope:<reason>
    payload          jsonb,       -- normalized Failure
    state            text NOT NULL,  -- received|classified|queued|claimed|verifying|done
    lease_expires_at timestamptz, -- for V5 recovery; set on claim
    result           jsonb,       -- RepairResult (nullable until done)
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Claim-loop access path: workers poll for the oldest queued job.
CREATE INDEX IF NOT EXISTS repair_jobs_state_created_idx
    ON repair_jobs (state, created_at);

-- Non-unique for now (dedupe uniqueness arrives in V5); speeds idem lookups.
CREATE INDEX IF NOT EXISTS repair_jobs_idem_key_idx
    ON repair_jobs (idem_key);
