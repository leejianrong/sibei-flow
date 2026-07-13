-- V5 — close the V4 PR-opener dedupe gap (concurrency + crash safety).
--
-- The V4 opener guarded solely on `pr_url IS NULL`, which is safe for a single
-- poller but (a) lets two concurrent pollers both pick the same candidate and
-- open duplicate PRs, and (b) has a push→record crash window where a PR was
-- opened but its URL never recorded.
--
-- `pr_claimed_at` lets a poller CLAIM a candidate (with FOR UPDATE SKIP LOCKED)
-- before doing the slow clone/push, so concurrent pollers never open duplicate
-- PRs. A claim older than the recovery window is retryable, so a poller that
-- crashed mid-open eventually gets retried (at worst a second, harmless,
-- human-gated PR — acceptable per ADR-0009).

ALTER TABLE repair_jobs
    ADD COLUMN IF NOT EXISTS pr_claimed_at timestamptz;  -- when a poller claimed this candidate
