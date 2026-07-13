-- V5 task 1 — dedup: make webhook re-delivery safe (R7.2, story 27).
--
-- `idem_key = hash(repo, run_id, task_id, node_uid)` has been populated since V1
-- (webhook.rs) but only under a NON-unique index. This migration promotes it to
-- a UNIQUE (partial) index so a re-delivered Failure collapses to one job via
-- `INSERT … ON CONFLICT (idem_key) DO NOTHING` (see webhook.rs).
--
-- Two guards before the unique index can be created:
--   1. Collapse any pre-existing duplicates (keep the earliest job per idem_key)
--      so the CREATE UNIQUE INDEX does not fail on historical re-deliveries.
--   2. Use a PARTIAL index (WHERE idem_key IS NOT NULL) so legacy rows with a
--      NULL key are never blocked; the enqueue path always sets a key.

-- 1. Collapse historical duplicates: keep the oldest row per idem_key.
DELETE FROM repair_jobs a
USING repair_jobs b
WHERE a.idem_key IS NOT NULL
  AND a.idem_key = b.idem_key
  AND (a.created_at, a.id) > (b.created_at, b.id);

-- 2. Replace the non-unique idem index with a UNIQUE partial index.
DROP INDEX IF EXISTS repair_jobs_idem_key_idx;

CREATE UNIQUE INDEX IF NOT EXISTS repair_jobs_idem_key_uidx
    ON repair_jobs (idem_key)
    WHERE idem_key IS NOT NULL;
