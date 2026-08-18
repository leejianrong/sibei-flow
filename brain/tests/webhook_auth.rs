//! KAN-926 — HMAC-SHA256 request signing on `POST /webhook`.
//!
//! Covers: a valid signature is accepted; a missing header, wrong signature,
//! or stale timestamp is rejected; and verification is a no-op when
//! `SBFLOW_WEBHOOK_SECRET` is unset (the existing `seam2.rs` webhook tests
//! exercise that last case implicitly — they never set a secret and keep
//! passing unmodified).

use hmac::{Hmac, KeyInit, Mac};
use serde_json::{json, Value};
use sha2::Sha256;
use sqlx::PgPool;
use std::net::SocketAddr;

/// Spawn the brain with an explicit (possibly absent) webhook secret; return
/// its base URL.
async fn spawn(pool: PgPool, webhook_secret: Option<String>) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr: SocketAddr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(
            listener,
            brain::app_with_webhook_secret(pool, webhook_secret),
        )
        .await
        .unwrap();
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

/// `sha256=<hex hmac>` over `{timestamp}.{raw_body}`, matching the contract in
/// `webhook::verify_signature`.
fn sign(secret: &str, timestamp: i64, raw_body: &[u8]) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).unwrap();
    mac.update(timestamp.to_string().as_bytes());
    mac.update(b".");
    mac.update(raw_body);
    format!("sha256={}", hex::encode(mac.finalize().into_bytes()))
}

const SECRET: &str = "test-shared-secret-do-not-use-in-prod";

#[sqlx::test]
async fn valid_signature_is_accepted(pool: PgPool) {
    let base = spawn(pool, Some(SECRET.to_string())).await;
    let client = reqwest::Client::new();
    let body = serde_json::to_vec(&schema_drift_payload()).unwrap();
    let ts = chrono::Utc::now().timestamp();
    let sig = sign(SECRET, ts, &body);

    let resp = client
        .post(format!("{base}/webhook"))
        .header("content-type", "application/json")
        .header("X-Sbflow-Signature", sig)
        .header("X-Sbflow-Timestamp", ts.to_string())
        .body(body)
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 202);
    let ack: Value = resp.json().await.unwrap();
    assert_eq!(ack["dispatched"], true);
}

#[sqlx::test]
async fn missing_signature_header_is_rejected(pool: PgPool) {
    let base = spawn(pool, Some(SECRET.to_string())).await;
    let client = reqwest::Client::new();
    let body = serde_json::to_vec(&schema_drift_payload()).unwrap();
    let ts = chrono::Utc::now().timestamp();

    // Only the timestamp header, no signature.
    let resp = client
        .post(format!("{base}/webhook"))
        .header("content-type", "application/json")
        .header("X-Sbflow-Timestamp", ts.to_string())
        .body(body.clone())
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 401);

    // Neither header at all.
    let resp = client
        .post(format!("{base}/webhook"))
        .header("content-type", "application/json")
        .body(body)
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 401);
}

#[sqlx::test]
async fn wrong_signature_is_rejected(pool: PgPool) {
    let base = spawn(pool, Some(SECRET.to_string())).await;
    let client = reqwest::Client::new();
    let body = serde_json::to_vec(&schema_drift_payload()).unwrap();
    let ts = chrono::Utc::now().timestamp();

    // Well-formed but wrong (signed with a different secret).
    let sig = sign("some-other-secret", ts, &body);
    let resp = client
        .post(format!("{base}/webhook"))
        .header("content-type", "application/json")
        .header("X-Sbflow-Signature", sig)
        .header("X-Sbflow-Timestamp", ts.to_string())
        .body(body.clone())
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 401);

    // Malformed (not `sha256=<hex>`).
    let resp = client
        .post(format!("{base}/webhook"))
        .header("content-type", "application/json")
        .header("X-Sbflow-Signature", "not-a-real-signature")
        .header("X-Sbflow-Timestamp", ts.to_string())
        .body(body)
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 401);
}

#[sqlx::test]
async fn stale_timestamp_is_rejected(pool: PgPool) {
    let base = spawn(pool, Some(SECRET.to_string())).await;
    let client = reqwest::Client::new();
    let body = serde_json::to_vec(&schema_drift_payload()).unwrap();
    // Correctly signed for this timestamp, but it's 10 minutes old — outside
    // the 300s skew window.
    let stale_ts = chrono::Utc::now().timestamp() - 600;
    let sig = sign(SECRET, stale_ts, &body);

    let resp = client
        .post(format!("{base}/webhook"))
        .header("content-type", "application/json")
        .header("X-Sbflow-Signature", sig)
        .header("X-Sbflow-Timestamp", stale_ts.to_string())
        .body(body)
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 401);
}

/// When `SBFLOW_WEBHOOK_SECRET` is unset, verification is a complete no-op —
/// an unsigned request is accepted exactly as before this change.
#[sqlx::test]
async fn verification_is_noop_when_secret_unset(pool: PgPool) {
    let base = spawn(pool, None).await;
    let client = reqwest::Client::new();

    let resp = client
        .post(format!("{base}/webhook"))
        .json(&schema_drift_payload())
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 202);
    let ack: Value = resp.json().await.unwrap();
    assert_eq!(ack["dispatched"], true);
}
