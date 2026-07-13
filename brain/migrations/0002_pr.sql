-- V4 — the PR opener. Records the single write action (opening a PR) back on
-- the durable job row, WITHOUT touching the frozen RepairResult contract shape
-- (docs/design/V4-plan.md, CLAUDE.md invariants).
--
-- `pr_url` doubles as the idempotency guard: the opener only considers jobs
-- where it IS NULL, so a PR is never opened twice for the same job.

ALTER TABLE repair_jobs
    ADD COLUMN IF NOT EXISTS pr_url       text,          -- host PR URL (github) or offline compare ref
    ADD COLUMN IF NOT EXISTS pr_branch    text,          -- the fix branch that was pushed
    ADD COLUMN IF NOT EXISTS pr_opened_at timestamptz;   -- when the opener recorded the PR

-- Fast lookup of the opener's work list: verified drafts with no PR yet.
CREATE INDEX IF NOT EXISTS repair_jobs_pr_pending_idx
    ON repair_jobs (state)
    WHERE pr_url IS NULL;
