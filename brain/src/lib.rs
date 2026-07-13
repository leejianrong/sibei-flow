//! sibei-flow brain (V1 walking skeleton).
//!
//! The Rust core (ADR-0002): webhook receiver, thin classifier, enqueue into the
//! Postgres job queue (the durable source of truth, ADR-0009), and a read-only
//! dashboard API + UI. No LLM, sandbox, git, or PR logic — those are V2–V4.

pub mod api;
pub mod classify;
pub mod config;
pub mod models;
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
