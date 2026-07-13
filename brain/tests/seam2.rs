//! PRD "Seam 2" — brain webhook → job → dispatch state machine (first cut).
//!
//! Driven by posting failure payloads to the real HTTP surface against a
//! throwaway Postgres (`#[sqlx::test]` provisions a fresh DB per test and runs
//! the migrations in ./migrations). Asserts external, observable behavior only.
//!
//! Requires a reachable Postgres via `DATABASE_URL` (the compose `postgres`
//! service works: `DATABASE_URL=postgres://sibei:sibei@localhost:5432/sibei`).

use serde_json::{json, Value};
use sqlx::PgPool;
use std::net::SocketAddr;

/// Spawn the brain on an ephemeral port using the test pool; return its base URL.
async fn spawn(pool: PgPool) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr: SocketAddr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, brain::app(pool)).await.unwrap();
    });
    format!("http://{addr}")
}

fn schema_drift_payload() -> Value {
    json!({
        "repo": "acme/analytics",
        "run_id": "manual__2026-07-09T02:00:00",
        "task_id": "build_orders",
        "node_uid": "model.analytics.orders",
        "error_text": "column \"customer_id\" does not exist",
        "adapter": "postgres",
        "source": "airflow"
    })
}

fn timeout_payload() -> Value {
    json!({
        "repo": "acme/analytics",
        "run_id": "manual__2026-07-09T03:00:00",
        "task_id": "big_agg",
        "node_uid": "model.analytics.big_agg",
        "error_text": "canceling statement due to statement timeout",
        "adapter": "postgres",
        "source": "dbt"
    })
}

/// Valid in-scope payload → exactly one `queued` job (worker will mark it done).
#[sqlx::test]
async fn in_scope_payload_creates_one_queued_job(pool: PgPool) {
    let base = spawn(pool.clone()).await;
    let client = reqwest::Client::new();

    let resp = client
        .post(format!("{base}/webhook"))
        .json(&schema_drift_payload())
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 202);
    let ack: Value = resp.json().await.unwrap();
    assert_eq!(ack["failure_class"], "schema_drift");
    assert_eq!(ack["state"], "queued");
    assert_eq!(ack["dispatched"], true);

    // Exactly one queued row exists — the durable dispatch.
    let queued: i64 = sqlx::query_scalar("SELECT count(*) FROM repair_jobs WHERE state = 'queued'")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(queued, 1);

    // And it surfaces in the dashboard history with no outcome yet.
    let runs: Value = client
        .get(format!("{base}/api/runs"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let arr = runs["runs"].as_array().unwrap();
    assert_eq!(arr.len(), 1);
    assert_eq!(arr[0]["failure_class"], "schema_drift");
    assert_eq!(arr[0]["state"], "queued");
    assert!(arr[0]["outcome"].is_null());
}

/// Out-of-scope payload → recorded, terminal, and NOT dispatched.
#[sqlx::test]
async fn out_of_scope_payload_is_recorded_not_dispatched(pool: PgPool) {
    let base = spawn(pool.clone()).await;
    let client = reqwest::Client::new();

    let resp = client
        .post(format!("{base}/webhook"))
        .json(&timeout_payload())
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 202);
    let ack: Value = resp.json().await.unwrap();
    assert_eq!(ack["failure_class"], "out_of_scope:timeout");
    assert_eq!(ack["state"], "done");
    assert_eq!(ack["dispatched"], false);

    // Nothing was enqueued for a worker.
    let queued: i64 = sqlx::query_scalar("SELECT count(*) FROM repair_jobs WHERE state = 'queued'")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(queued, 0);

    // It is recorded as a dropped run with outcome out_of_scope.
    let id = ack["id"].as_str().unwrap();
    let detail: Value = client
        .get(format!("{base}/api/runs/{id}"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(detail["state"], "done");
    assert_eq!(detail["outcome"], "out_of_scope");
    assert_eq!(detail["result"]["reason"], "timeout");
}

/// Re-delivering the same Failure collapses to exactly one job (R7.2, story 27).
/// The second webhook returns the SAME job id, flagged `deduplicated`, and never
/// creates a second row — never corrupted state (ADR-0009).
#[sqlx::test]
async fn redelivered_payload_collapses_to_one_job(pool: PgPool) {
    let base = spawn(pool.clone()).await;
    let client = reqwest::Client::new();
    let payload = schema_drift_payload();

    let first: Value = client
        .post(format!("{base}/webhook"))
        .json(&payload)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(first["deduplicated"], false);
    let first_id = first["id"].as_str().unwrap().to_string();

    // Re-deliver the identical payload (duplicate webhook / at-least-once).
    let second: Value = client
        .post(format!("{base}/webhook"))
        .json(&payload)
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(second["deduplicated"], true, "second delivery must dedup");
    assert_eq!(
        second["id"].as_str().unwrap(),
        first_id,
        "dedup must return the existing job id, not a new one"
    );

    // Exactly one row exists for this failure — the durable dispatch is unique.
    let total: i64 = sqlx::query_scalar("SELECT count(*) FROM repair_jobs")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(total, 1, "re-delivery must not create a second job");
}

/// Crash recovery (R7.1, story 26): a job orphaned mid-run by a crashed worker
/// (non-terminal state + expired lease) is requeued on brain restart and
/// resumes; a job whose lease is still valid is left for its live worker.
#[sqlx::test]
async fn startup_reconcile_requeues_only_expired_orphans(pool: PgPool) {
    // Orphan: worker died mid-verification, lease already expired.
    sqlx::query(
        r#"
        INSERT INTO repair_jobs (id, idem_key, state, lease_expires_at, created_at, updated_at)
        VALUES (gen_random_uuid(), 'orphan', 'verifying', now() - interval '1 minute', now(), now())
        "#,
    )
    .execute(&pool)
    .await
    .unwrap();

    // Healthy: another worker holds this claim, lease still valid — must NOT move.
    sqlx::query(
        r#"
        INSERT INTO repair_jobs (id, idem_key, state, lease_expires_at, created_at, updated_at)
        VALUES (gen_random_uuid(), 'live', 'claimed', now() + interval '5 minutes', now(), now())
        "#,
    )
    .execute(&pool)
    .await
    .unwrap();

    let requeued = brain::reconcile_orphaned_jobs(&pool).await.unwrap();
    assert_eq!(requeued, 1, "exactly the expired orphan is requeued");

    let orphan_state: String =
        sqlx::query_scalar("SELECT state FROM repair_jobs WHERE idem_key = 'orphan'")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(orphan_state, "queued", "orphan resumes as queued");

    let live_state: String =
        sqlx::query_scalar("SELECT state FROM repair_jobs WHERE idem_key = 'live'")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(live_state, "claimed", "live claim is untouched");

    // Reconcile is idempotent — a second pass (another restart) moves nothing.
    let again = brain::reconcile_orphaned_jobs(&pool).await.unwrap();
    assert_eq!(again, 0);
}

/// The dashboard API is read-only — only GET is allowed; write verbs 405.
#[sqlx::test]
async fn dashboard_api_exposes_no_write_endpoints(pool: PgPool) {
    let base = spawn(pool.clone()).await;
    let client = reqwest::Client::new();

    // Seed one run so an id exists to target.
    let ack: Value = client
        .post(format!("{base}/webhook"))
        .json(&schema_drift_payload())
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let id = ack["id"].as_str().unwrap();

    // GET works.
    assert_eq!(
        client
            .get(format!("{base}/api/runs"))
            .send()
            .await
            .unwrap()
            .status(),
        200
    );
    assert_eq!(
        client
            .get(format!("{base}/api/runs/{id}"))
            .send()
            .await
            .unwrap()
            .status(),
        200
    );

    // Every write verb against the runs API is rejected (405 Method Not Allowed).
    for status in [
        client
            .post(format!("{base}/api/runs"))
            .json(&json!({}))
            .send()
            .await
            .unwrap()
            .status(),
        client
            .put(format!("{base}/api/runs/{id}"))
            .json(&json!({}))
            .send()
            .await
            .unwrap()
            .status(),
        client
            .delete(format!("{base}/api/runs/{id}"))
            .send()
            .await
            .unwrap()
            .status(),
        client
            .patch(format!("{base}/api/runs/{id}"))
            .json(&json!({}))
            .send()
            .await
            .unwrap()
            .status(),
    ] {
        assert_eq!(
            status, 405,
            "write verbs must be rejected on the read-only API"
        );
    }
}
