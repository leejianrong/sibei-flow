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
use crate::pr;
use crate::SatayJournalDir;

/// `GET /api/runs` — most-recent-first history of every failure seen.
pub async fn list_runs(
    State(pool): State<PgPool>,
) -> Result<Json<serde_json::Value>, (StatusCode, String)> {
    let rows = sqlx::query_as::<_, JobRow>(
        r#"
        SELECT id, idem_key, repo, run_id, task_id, node_uid, failure_class,
               payload, state, lease_expires_at, result,
               pr_url, pr_branch, pr_opened_at, created_at, updated_at
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
///
/// KAN-651: merges in a `usage` key (per-run cost) when the job's
/// `RepairResult.transcript` is the `journal` arm — a real read of the
/// worker's persisted Satay journal (`pr::aggregate_usage`), the same reader
/// `pr::body::render_body` uses for the PR body's own cost line, so the
/// dashboard and the PR never independently compute (and risk disagreeing on)
/// "what did this run cost". Absent entirely for the `lines` arm or when
/// `transcript` itself is unset — nothing to read a run_id out of.
pub async fn get_run(
    State(pool): State<PgPool>,
    State(journal_dir): State<SatayJournalDir>,
    Path(id): Path<Uuid>,
) -> Result<Json<serde_json::Value>, (StatusCode, String)> {
    let row = sqlx::query_as::<_, JobRow>(
        r#"
        SELECT id, idem_key, repo, run_id, task_id, node_uid, failure_class,
               payload, state, lease_expires_at, result,
               pr_url, pr_branch, pr_opened_at, created_at, updated_at
        FROM repair_jobs
        WHERE id = $1
        "#,
    )
    .bind(id)
    .fetch_optional(&pool)
    .await
    .map_err(internal)?;

    match row {
        Some(r) => {
            let mut detail = r.detail();
            if let Some(transcript) = r.result.as_ref().and_then(|res| res.get("transcript")) {
                if let Some((run_ids, journal_ref)) = pr::cost_run_ids(transcript) {
                    let usage = pr::aggregate_usage(&journal_dir.0, &run_ids, &journal_ref).await;
                    detail["usage"] = usage.to_json();
                }
            }
            Ok(Json(detail))
        }
        None => Err((StatusCode::NOT_FOUND, "run not found".to_string())),
    }
}

fn internal<E: std::fmt::Display>(e: E) -> (StatusCode, String) {
    (StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
}
