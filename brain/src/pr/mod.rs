//! N14 — the PR opener (V4): the single write action in the whole system.
//!
//! A brain-side background poller (started in `main.rs`, alongside the axum
//! server) that finds terminal jobs which the worker verified as
//! `pr_proposed` and turns each into a real Pull Request via the configured
//! git-host backend (see `githost.rs`).
//!
//! Guarantees enforced here:
//!   * **Compile gate** — only `outcome == "pr_proposed"` is ever opened; a
//!     `no_fix` / `out_of_scope` job is never touched (CLAUDE.md invariant).
//!   * **Idempotency (concurrency + crash safe, V5)** — candidates are *claimed*
//!     with `FOR UPDATE SKIP LOCKED` + a `pr_claimed_at` stamp, so two pollers
//!     never pick the same row and never open duplicate PRs. `pr_url IS NULL`
//!     still gates recording, and a claim abandoned by a crashed poller is
//!     retried after a TTL (at worst a second human-gated PR — ADR-0009).
//!   * **Read-only elsewhere** — the poller only ever pushes a branch + opens a
//!     PR; it holds no warehouse / prod-write credential.

pub mod body;
pub mod git;
pub mod githost;
pub mod github;
pub mod offline;

use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use sqlx::PgPool;

pub use githost::{build_host, GitHost, PrOpenerConfig, PrRef, PrRequest};

use crate::models::JobRow;

/// Start the PR-opener poller as a detached background task.
pub fn spawn(pool: PgPool, cfg: PrOpenerConfig) -> Result<()> {
    let host = build_host(&cfg)?;
    tracing::info!(
        host = %host.kind(),
        base = %cfg.base_branch,
        interval_secs = cfg.poll_interval_secs,
        "PR opener started"
    );
    tokio::spawn(async move { run_loop(pool, cfg, host).await });
    Ok(())
}

async fn run_loop(pool: PgPool, cfg: PrOpenerConfig, host: Arc<dyn GitHost>) {
    let period = Duration::from_secs_f64(cfg.poll_interval_secs.max(0.5));
    let mut ticker = tokio::time::interval(period);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        ticker.tick().await;
        if let Err(e) = poll_once(&pool, &cfg, &host).await {
            tracing::warn!(error = %e, "PR opener poll failed; will retry");
        }
    }
}

/// One poll: pick up verified-but-unopened jobs and open a PR for each.
async fn poll_once(pool: &PgPool, cfg: &PrOpenerConfig, host: &Arc<dyn GitHost>) -> Result<()> {
    for job in fetch_candidates(pool).await? {
        let id = job.id;
        if let Err(e) = open_for_job(pool, cfg, host, job).await {
            // Transient failure (clone/push, remote not yet up). Release the
            // claim so the NEXT tick retries immediately instead of waiting out
            // the crash-recovery TTL. pr_url stays NULL; the result is untouched.
            tracing::warn!(job = %id, error = %e, "failed to open PR for job; releasing claim to retry");
            if let Err(e2) = release_claim(pool, id).await {
                tracing::warn!(job = %id, error = %e2, "failed to release PR claim; TTL will recover");
            }
        }
    }
    Ok(())
}

/// A claimed PR candidate whose `pr_claimed_at` is older than this is considered
/// abandoned (poller crashed mid-open) and becomes eligible again. On retry the
/// worst case is a second, human-gated PR — never a corrupted state (ADR-0009).
const PR_CLAIM_TTL_SECS: i64 = 300;

/// Claim verified drafts with no PR yet — the opener's work list.
///
/// This is the concurrency/crash-safe guard (V5): a single `UPDATE … WHERE id IN
/// (SELECT … FOR UPDATE SKIP LOCKED)` atomically stamps `pr_claimed_at` on each
/// candidate it takes, so two pollers running at once never pick the same row
/// and never open duplicate PRs. Rows already claimed within the TTL are skipped;
/// a claim older than the TTL (a crashed poller) is retried. `pr_url IS NULL`
/// still gates so a fully-recorded PR is never reopened.
async fn fetch_candidates(pool: &PgPool) -> Result<Vec<JobRow>> {
    let rows = sqlx::query_as::<_, JobRow>(
        r#"
        UPDATE repair_jobs
           SET pr_claimed_at = now(), updated_at = now()
         WHERE id IN (
             SELECT id FROM repair_jobs
             WHERE state = 'done'
               AND result->>'outcome' = 'pr_proposed'
               AND pr_url IS NULL
               AND (pr_claimed_at IS NULL
                    OR pr_claimed_at < now() - make_interval(secs => $1))
             ORDER BY updated_at ASC
             LIMIT 20
             FOR UPDATE SKIP LOCKED
         )
        RETURNING id, idem_key, repo, run_id, task_id, node_uid, failure_class,
                  payload, state, lease_expires_at, result,
                  pr_url, pr_branch, pr_opened_at, created_at, updated_at
        "#,
    )
    .bind(PR_CLAIM_TTL_SECS as f64)
    .fetch_all(pool)
    .await
    .context("claiming PR candidates")?;
    Ok(rows)
}

/// Release a claim so the next poll retries a candidate that failed to open for
/// a transient reason. Only clears the claim when no PR was recorded.
async fn release_claim(pool: &PgPool, job_id: uuid::Uuid) -> Result<()> {
    sqlx::query(
        r#"
        UPDATE repair_jobs SET pr_claimed_at = NULL
         WHERE id = $1 AND pr_url IS NULL
        "#,
    )
    .bind(job_id)
    .execute(pool)
    .await
    .context("releasing PR claim")?;
    Ok(())
}

async fn open_for_job(
    pool: &PgPool,
    cfg: &PrOpenerConfig,
    host: &Arc<dyn GitHost>,
    job: JobRow,
) -> Result<()> {
    // Compile-gate guard (defense in depth): never open a PR for anything but a
    // verified pr_proposed. The query already filters, but re-assert here so the
    // invariant holds even if the query is ever changed.
    if job.outcome().as_deref() != Some("pr_proposed") {
        anyhow::bail!("refusing to open PR: outcome is not pr_proposed");
    }
    let diff = job
        .result
        .as_ref()
        .and_then(|r| r.get("diff"))
        .and_then(|d| d.as_str())
        .filter(|d| !d.is_empty())
        .context("pr_proposed job has no diff to apply")?
        .to_string();

    let title = body::render_title(&job);
    let body_md = body::render_body(&job);
    let head_branch = format!(
        "{}-{}",
        cfg.branch_prefix,
        job.id.simple().to_string().get(..8).unwrap_or("job")
    );
    let repo = job.repo.clone().unwrap_or_else(|| "unknown".to_string());

    let req = PrRequest {
        job_id: job.id,
        repo,
        base_branch: cfg.base_branch.clone(),
        head_branch,
        title,
        body: body_md,
        diff,
    };

    // Git + HTTP are blocking; run them off the async runtime.
    let host = Arc::clone(host);
    let pr = tokio::task::spawn_blocking(move || host.open_pr(&req))
        .await
        .context("PR opener task panicked")??;

    // Persist the PR link. The `pr_url IS NULL` guard makes this idempotent even
    // under a duplicate poll.
    let updated = sqlx::query(
        r#"
        UPDATE repair_jobs
           SET pr_url = $1, pr_branch = $2, pr_opened_at = now(), updated_at = now()
         WHERE id = $3 AND pr_url IS NULL
        "#,
    )
    .bind(&pr.url)
    .bind(&pr.branch)
    .bind(job.id)
    .execute(pool)
    .await
    .context("recording PR url on job")?;

    if updated.rows_affected() == 1 {
        tracing::info!(job = %job.id, branch = %pr.branch, url = %pr.url, "opened PR");
    } else {
        tracing::info!(job = %job.id, "PR already recorded by another poll; skipping");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use sqlx::PgPool;
    use uuid::Uuid;

    async fn insert_pr_proposed(pool: &PgPool) -> Uuid {
        let id = Uuid::new_v4();
        sqlx::query(
            r#"
            INSERT INTO repair_jobs (id, idem_key, repo, state, result, created_at, updated_at)
            VALUES ($1, $2, 'acme/analytics', 'done',
                    '{"outcome":"pr_proposed","diff":"d"}'::jsonb, now(), now())
            "#,
        )
        .bind(id)
        .bind(id.to_string())
        .execute(pool)
        .await
        .unwrap();
        id
    }

    /// V5 dedupe gap: claiming a candidate stamps `pr_claimed_at`, so a second
    /// poll (a concurrent poller) does not re-pick the same row — no duplicate PR.
    #[sqlx::test]
    async fn claiming_a_candidate_hides_it_from_the_next_poll(pool: PgPool) {
        let id = insert_pr_proposed(&pool).await;

        let first = fetch_candidates(&pool).await.unwrap();
        assert_eq!(first.len(), 1);
        assert_eq!(first[0].id, id);

        // A second poller polling immediately must see nothing to do — the row
        // is already claimed within the TTL.
        let second = fetch_candidates(&pool).await.unwrap();
        assert!(
            second.is_empty(),
            "a claimed candidate must not be re-claimed by a concurrent poll"
        );

        // Releasing the claim (transient-failure path) makes it eligible again.
        release_claim(&pool, id).await.unwrap();
        let third = fetch_candidates(&pool).await.unwrap();
        assert_eq!(third.len(), 1, "released claim is retried");
    }

    /// Once a PR is recorded (`pr_url` set), the candidate is never re-opened,
    /// even though the claim mechanism is new.
    #[sqlx::test]
    async fn recorded_pr_is_never_reclaimed(pool: PgPool) {
        let id = insert_pr_proposed(&pool).await;
        sqlx::query("UPDATE repair_jobs SET pr_url = 'http://x/pull/1' WHERE id = $1")
            .bind(id)
            .execute(&pool)
            .await
            .unwrap();

        let candidates = fetch_candidates(&pool).await.unwrap();
        assert!(
            candidates.is_empty(),
            "a recorded PR must never be reopened"
        );
    }
}
