//! sibei-flow brain.
//!
//! The Rust core (ADR-0002): webhook receiver, thin classifier, enqueue into the
//! Postgres job queue (the durable source of truth, ADR-0009), a read-only
//! dashboard API + UI, and (V4) the PR opener — the single write action, a
//! background poller that turns verified `pr_proposed` jobs into real Pull
//! Requests via a pluggable git-host backend (see `pr`).

pub mod api;
pub mod classify;
pub mod config;
pub mod models;
pub mod pr;
pub mod web;
pub mod webhook;

use axum::{
    routing::{get, post},
    Router,
};
use sqlx::PgPool;

/// Run embedded migrations against the pool. Called at startup; the schema is
/// the source of truth for the queue.
pub async fn run_migrations(pool: &PgPool) -> Result<(), sqlx::migrate::MigrateError> {
    sqlx::migrate!("./migrations").run(pool).await
}

/// Crash recovery (V5 task 2, R7.1): on brain startup, requeue jobs a crashed
/// worker left mid-flight.
///
/// A job in a non-terminal working state (`claimed` / `verifying`) whose lease
/// has expired was orphaned — the worker that held it died without writing a
/// result. We reset it to `queued` (clearing the stale lease) so another worker
/// re-claims it. Jobs with a still-valid lease are left alone: the worker
/// holding them may still be alive, and the worker's own lease-expiry re-claim
/// (claim.py) covers them once the lease lapses. This is safe because repair
/// jobs are idempotent / re-runnable (ADR-0009): at worst a duplicate produces
/// another human-gated PR proposal.
///
/// Returns the number of jobs requeued.
pub async fn reconcile_orphaned_jobs(pool: &PgPool) -> Result<u64, sqlx::Error> {
    let result = sqlx::query(
        r#"
        UPDATE repair_jobs
           SET state = 'queued',
               lease_expires_at = NULL,
               updated_at = now()
         WHERE state IN ('claimed', 'verifying')
           AND (lease_expires_at IS NULL OR lease_expires_at < now())
        "#,
    )
    .execute(pool)
    .await?;
    let requeued = result.rows_affected();
    if requeued > 0 {
        tracing::info!(
            requeued,
            "reconciled orphaned jobs on startup (crash recovery)"
        );
    }
    Ok(requeued)
}

/// Build the axum application router.
///
/// Routes are deliberately narrow and the API surface is **read-only**:
/// `/webhook` (ingest) is the only POST; `/api/*` are GET-only, so any write
/// verb against a run returns 405.
pub fn app(pool: PgPool) -> Router {
    Router::new()
        .route("/", get(web::index))
        .route("/healthz", get(healthz))
        .route("/webhook", post(webhook::receive))
        .route("/api/runs", get(api::list_runs))
        .route("/api/runs/{id}", get(api::get_run))
        .with_state(pool)
}

async fn healthz() -> &'static str {
    "ok"
}
