//! KAN-651 — the run-detail API (`GET /api/runs/:id`) merges in a `usage` key
//! (per-run cost, read from the worker's persisted Satay journal) for jobs whose
//! `RepairResult.transcript` is the `journal` arm.
//!
//! This deliberately does NOT stand up a real Satay journal file (that would mean
//! duplicating `pr::journal`'s `#[cfg(test)]`-only fixture builders, which are not
//! visible across the crate boundary an external `tests/*.rs` integration binary
//! compiles behind — see `brain/src/pr/journal.rs`'s own unit tests, and
//! `brain/src/pr/body.rs`'s, for the usage-computation coverage itself). What this
//! file covers instead is the wiring: a `journal`-arm transcript gets a `usage` key
//! at all (honestly reporting "not available" when — as in this test environment —
//! no journal file is actually mounted), and a `lines`-arm / transcript-less job
//! gets none, end to end through the real HTTP API.

use serde_json::{json, Value};
use sqlx::PgPool;
use std::net::SocketAddr;
use uuid::Uuid;

async fn spawn(pool: PgPool) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr: SocketAddr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, brain::app(pool)).await.unwrap();
    });
    format!("http://{addr}")
}

async fn insert_job(pool: &PgPool, result: Value) -> Uuid {
    let id = Uuid::new_v4();
    sqlx::query(
        r#"
        INSERT INTO repair_jobs (id, idem_key, repo, state, result, created_at, updated_at)
        VALUES ($1, $2, 'acme/analytics', 'done', $3, now(), now())
        "#,
    )
    .bind(id)
    .bind(id.to_string())
    .bind(&result)
    .execute(pool)
    .await
    .unwrap();
    id
}

/// A `journal`-arm transcript gets a `usage` key, honestly disclosing "not
/// available" rather than omitting the field or fabricating a number — the
/// journal file this points at is not mounted in this test environment (no
/// `SBFLOW_SATAY_JOURNAL_DIR` override here; the default path simply does not
/// exist), which is exactly the "worker never ran a Satay-backed job yet" case
/// `pr::aggregate_usage` is documented to degrade gracefully for.
#[sqlx::test]
async fn journal_transcript_gets_a_usage_key_even_when_not_available(pool: PgPool) {
    let base = spawn(pool.clone()).await;
    let client = reqwest::Client::new();

    let id = insert_job(
        &pool,
        json!({
            "outcome": "pr_proposed",
            "diff": "d",
            "transcript": {"kind": "journal", "run_id": "r1", "ref": "satay.db"}
        }),
    )
    .await;

    let detail: Value = client
        .get(format!("{base}/api/runs/{id}"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();

    assert!(detail.get("usage").is_some(), "usage key must be present");
    assert_eq!(detail["usage"]["available"], false);
    assert_eq!(detail["usage"]["entry_count"], 0);
}

/// A `lines`-arm transcript (pre-Satay-port) has no journal to read at all —
/// `usage` must be entirely absent, not a "not available" placeholder either.
#[sqlx::test]
async fn lines_transcript_gets_no_usage_key(pool: PgPool) {
    let base = spawn(pool.clone()).await;
    let client = reqwest::Client::new();

    let id = insert_job(
        &pool,
        json!({
            "outcome": "pr_proposed",
            "diff": "d",
            "transcript": {"kind": "lines", "lines": ["a", "b"]}
        }),
    )
    .await;

    let detail: Value = client
        .get(format!("{base}/api/runs/{id}"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();

    assert!(detail.get("usage").is_none());
}

/// A job with no `transcript` at all (e.g. `no_fix` before any drafting) — same
/// absence, no crash.
#[sqlx::test]
async fn job_with_no_transcript_gets_no_usage_key(pool: PgPool) {
    let base = spawn(pool.clone()).await;
    let client = reqwest::Client::new();

    let id = insert_job(
        &pool,
        json!({"outcome": "no_fix", "explanation": "no diff drafted"}),
    )
    .await;

    let detail: Value = client
        .get(format!("{base}/api/runs/{id}"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();

    assert!(detail.get("usage").is_none());
}
