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
//!   * **Idempotency** — the candidate query requires `pr_url IS NULL`, and the
//!     URL is written back on success, so a PR is never opened twice for a job.
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
            // Leave pr_url NULL so the next tick retries (transient clone/push
            // failures, remote not yet up, etc.). Never mutate the result.
            tracing::warn!(job = %id, error = %e, "failed to open PR for job; will retry");
        }
    }
    Ok(())
}

/// Verified drafts with no PR yet — the opener's work list (idempotency guard).
async fn fetch_candidates(pool: &PgPool) -> Result<Vec<JobRow>> {
    let rows = sqlx::query_as::<_, JobRow>(
        r#"
        SELECT id, idem_key, repo, run_id, task_id, node_uid, failure_class,
               payload, state, lease_expires_at, result,
               pr_url, pr_branch, pr_opened_at, created_at, updated_at
        FROM repair_jobs
        WHERE state = 'done'
          AND result->>'outcome' = 'pr_proposed'
          AND pr_url IS NULL
        ORDER BY updated_at ASC
        LIMIT 20
        "#,
    )
    .fetch_all(pool)
    .await
    .context("fetching PR candidates")?;
    Ok(rows)
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
