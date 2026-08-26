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
    extract::FromRef,
    routing::{get, post},
    Router,
};
use sqlx::PgPool;

/// Shared axum state: the DB pool, the optional webhook HMAC secret (KAN-926),
/// and where the worker's persisted Satay journal is mounted (KAN-651).
/// `PgPool`/`SatayJournalDir`: `FromRef<AppState>` let the existing
/// `State<PgPool>` handlers (`web::index`, `api::*`) keep working unchanged, and
/// let `api::get_run` pull in just the journal dir, via axum's substate
/// pattern.
#[derive(Clone)]
pub struct AppState {
    pub pool: PgPool,
    pub webhook_secret: Option<String>,
    pub satay_journal_dir: String,
}

impl FromRef<AppState> for PgPool {
    fn from_ref(state: &AppState) -> Self {
        state.pool.clone()
    }
}

/// KAN-651: the directory the worker's persisted Satay journal (`satay.db`,
/// plus its sibling `blobs/` spill dir) is mounted under, read-only, in this
/// container — the same volume `pr::PrOpenerConfig::satay_journal_dir` points
/// the PR-opener's own journal reads at (`pr/journal.rs`). A distinct
/// newtype (rather than a bare `String` substate) so axum's `FromRef`
/// dispatches on type, not on accidentally matching some other `String` field
/// `AppState` might grow later.
#[derive(Clone)]
pub struct SatayJournalDir(pub String);

impl FromRef<AppState> for SatayJournalDir {
    fn from_ref(state: &AppState) -> Self {
        SatayJournalDir(state.satay_journal_dir.clone())
    }
}

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
    app_with_webhook_secret(pool, None)
}

/// Build the router with an explicit webhook HMAC secret (KAN-926). `None`
/// keeps `POST /webhook` unauthenticated, exactly `app`'s behavior.
///
/// `satay_journal_dir` (KAN-651) is read directly from `SBFLOW_SATAY_JOURNAL_DIR`
/// here, mirroring `PrOpenerConfig::from_env`'s own default — rather than
/// threading it through `Config`/every caller of `app`/`app_with_webhook_secret`
/// — so this function's signature (and every existing test/`main.rs` call site)
/// stays unchanged. Harmless when the worker never ran a Satay-backed job: the
/// file simply doesn't exist yet, and `api::get_run`/`pr::body::render_body`
/// both already treat that as "cost not available", never a crash.
pub fn app_with_webhook_secret(pool: PgPool, webhook_secret: Option<String>) -> Router {
    let satay_journal_dir = std::env::var("SBFLOW_SATAY_JOURNAL_DIR")
        .unwrap_or_else(|_| "/var/lib/sbflow/satay".to_string());
    let state = AppState {
        pool,
        webhook_secret,
        satay_journal_dir,
    };
    Router::new()
        .route("/", get(web::index))
        .route("/healthz", get(healthz))
        .route("/webhook", post(webhook::receive))
        .route("/api/runs", get(api::list_runs))
        .route("/api/runs/{id}", get(api::get_run))
        .with_state(state)
}

async fn healthz() -> &'static str {
    "ok"
}
