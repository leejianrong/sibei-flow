//! N15 dashboard read API (read-only, R8).
//!
//! `GET /api/runs`      — run history (U4).
//! `GET /api/runs/:id`  — run detail (U5): class, outcome, timing.
//!
//! There are **no** write endpoints here — approval lives in the PR (V4+), and
//! the web UI is explicitly read-only (R8.3).

use axum::{
    extract::{Path, State},
    http::StatusCode,
    Json,
};
use sqlx::PgPool;
use uuid::Uuid;

use crate::models::JobRow;

/// `GET /api/runs` — most-recent-first history of every failure seen.
pub async fn list_runs(
    State(pool): State<PgPool>,
) -> Result<Json<serde_json::Value>, (StatusCode, String)> {
    let rows = sqlx::query_as::<_, JobRow>(
        r#"
        SELECT id, idem_key, repo, run_id, task_id, node_uid, failure_class,
               payload, state, lease_expires_at, result, created_at, updated_at
        FROM repair_jobs
        ORDER BY created_at DESC
        LIMIT 200
        "#,
    )
    .fetch_all(&pool)
    .await
    .map_err(internal)?;

    let runs: Vec<_> = rows.iter().map(JobRow::summary).collect();
    Ok(Json(serde_json::json!({ "runs": runs })))
}

/// `GET /api/runs/:id` — full detail for one run.
pub async fn get_run(
    State(pool): State<PgPool>,
    Path(id): Path<Uuid>,
) -> Result<Json<serde_json::Value>, (StatusCode, String)> {
    let row = sqlx::query_as::<_, JobRow>(
        r#"
        SELECT id, idem_key, repo, run_id, task_id, node_uid, failure_class,
               payload, state, lease_expires_at, result, created_at, updated_at
        FROM repair_jobs
        WHERE id = $1
        "#,
    )
    .bind(id)
    .fetch_optional(&pool)
    .await
    .map_err(internal)?;

    match row {
        Some(r) => Ok(Json(r.detail())),
        None => Err((StatusCode::NOT_FOUND, "run not found".to_string())),
    }
}

fn internal<E: std::fmt::Display>(e: E) -> (StatusCode, String) {
    (StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
}
