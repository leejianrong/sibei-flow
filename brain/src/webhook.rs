//! N1 webhook receiver + N2/N3 classify & enqueue.
//!
//! `POST /webhook` accepts an Airflow / dbt / CLI failure payload, normalizes it
//! to the frozen `Failure` contract, classifies it, and either enqueues an
//! in-scope job (`state = queued`) or records a dropped one (`state = done`,
//! `outcome = out_of_scope`) **without dispatch**.

use axum::{
    body::Bytes,
    extract::State,
    http::{HeaderMap, StatusCode},
    Json,
};
use hmac::{Hmac, KeyInit, Mac};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::classify::classify;
use crate::models::Failure;
use crate::AppState;

/// `X-Sbflow-Signature: sha256=<hex hmac>` (KAN-926).
const SIGNATURE_HEADER: &str = "x-sbflow-signature";
/// `X-Sbflow-Timestamp: <unix seconds>`, signed together with the raw body.
const TIMESTAMP_HEADER: &str = "x-sbflow-timestamp";
/// Reject a request whose timestamp is more than this many seconds away from
/// now, in either direction (clock skew allowance).
const MAX_SKEW_SECS: i64 = 300;

/// Response body for an accepted webhook.
#[derive(serde::Serialize)]
pub struct WebhookAck {
    pub id: Uuid,
    pub failure_class: String,
    pub state: String,
    /// True when the job was enqueued for a worker; false when recorded+dropped.
    pub dispatched: bool,
    /// True when this webhook was a re-delivery that collapsed onto an existing
    /// job (dedup, R7.2) — no new row was created.
    pub deduplicated: bool,
}

/// Handler for `POST /webhook`.
///
/// Takes the raw body bytes (not a `Json<Value>` extractor) because HMAC
/// verification (KAN-926) must run over the exact bytes the caller signed,
/// not a re-serialized copy — JSON re-serialization is not guaranteed to
/// round-trip byte-for-byte (key order, whitespace, number formatting).
pub async fn receive(
    State(state): State<AppState>,
    headers: HeaderMap,
    raw_body: Bytes,
) -> Result<(StatusCode, Json<WebhookAck>), (StatusCode, String)> {
    if let Some(secret) = &state.webhook_secret {
        verify_signature(secret, &headers, &raw_body)?;
    }
    let pool = state.pool;

    let body: serde_json::Value = serde_json::from_slice(&raw_body)
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("invalid JSON body: {e}")))?;

    let failure = normalize(&body);
    let idem_key = idem_key(&failure);
    let classification = classify(&failure.error_text, &failure.adapter);

    let payload = serde_json::to_value(&failure)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    let id = Uuid::new_v4();

    let (state, result) = if classification.in_scope {
        // In-scope → durable queue row for a worker to claim.
        ("queued", None::<serde_json::Value>)
    } else {
        // Out-of-scope → recorded, terminal, never dispatched.
        let reason = classification
            .failure_class
            .strip_prefix("out_of_scope:")
            .unwrap_or("unknown")
            .to_string();
        (
            "done",
            Some(serde_json::json!({
                "outcome": "out_of_scope",
                "reason": reason,
            })),
        )
    };

    // Dedup (V5, R7.2): a re-delivered Failure has the same idem_key. The UNIQUE
    // partial index (migration 0003) + `ON CONFLICT DO NOTHING` collapses it to
    // the one existing job — never a duplicate, never corrupted state (ADR-0009).
    // `RETURNING id` is present only when a row was actually inserted.
    let inserted_id: Option<Uuid> = sqlx::query_scalar(
        r#"
        INSERT INTO repair_jobs
            (id, idem_key, repo, run_id, task_id, node_uid,
             failure_class, payload, state, result, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, now(), now())
        ON CONFLICT (idem_key) WHERE idem_key IS NOT NULL DO NOTHING
        RETURNING id
        "#,
    )
    .bind(id)
    .bind(&idem_key)
    .bind(&failure.repo)
    .bind(&failure.run_id)
    .bind(&failure.task_id)
    .bind(&failure.node_uid)
    .bind(&classification.failure_class)
    .bind(&payload)
    .bind(state)
    .bind(&result)
    .fetch_optional(&pool)
    .await
    .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    if let Some(new_id) = inserted_id {
        // Fresh enqueue. Nudge the worker to wake immediately (LISTEN/NOTIFY,
        // V5 task 6) for an in-scope job; the poll loop is the fallback so a
        // missed NOTIFY never strands a job.
        if classification.in_scope {
            if let Err(e) = sqlx::query("SELECT pg_notify('sbflow_jobs', $1)")
                .bind(new_id.to_string())
                .execute(&pool)
                .await
            {
                tracing::warn!(error = %e, "pg_notify failed; worker will pick it up on poll");
            }
        }
        tracing::info!(
            id = %new_id, failure_class = %classification.failure_class, state,
            dispatched = classification.in_scope, "webhook accepted"
        );
        return Ok((
            StatusCode::ACCEPTED,
            Json(WebhookAck {
                id: new_id,
                failure_class: classification.failure_class,
                state: state.to_string(),
                dispatched: classification.in_scope,
                deduplicated: false,
            }),
        ));
    }

    // Conflict: re-delivery. Return the existing job's identity/state so the
    // caller sees a stable ack instead of a spurious new id.
    let existing = sqlx::query_as::<_, (Uuid, String, Option<String>)>(
        "SELECT id, state, failure_class FROM repair_jobs WHERE idem_key = $1",
    )
    .bind(&idem_key)
    .fetch_one(&pool)
    .await
    .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    tracing::info!(
        id = %existing.0, idem_key = %idem_key,
        "webhook re-delivery collapsed onto existing job (dedup)"
    );
    Ok((
        StatusCode::ACCEPTED,
        Json(WebhookAck {
            id: existing.0,
            failure_class: existing.2.unwrap_or(classification.failure_class),
            state: existing.1,
            dispatched: classification.in_scope,
            deduplicated: true,
        }),
    ))
}

/// Verify the `X-Sbflow-Signature` / `X-Sbflow-Timestamp` pair against the raw
/// request body (KAN-926). Enforced only when the caller (`receive`) has a
/// configured secret — this function is never reached otherwise.
///
/// Rejects with `401 Unauthorized` — consistently, for every failure mode —
/// when: either header is missing or malformed, the timestamp is outside the
/// `MAX_SKEW_SECS` window, or the signature doesn't match. The comparison is
/// constant-time via `hmac::Mac::verify_slice` (no hand-rolled byte compare).
fn verify_signature(
    secret: &str,
    headers: &HeaderMap,
    raw_body: &[u8],
) -> Result<(), (StatusCode, String)> {
    let bad = |msg: &str| (StatusCode::UNAUTHORIZED, msg.to_string());

    let sig_header = headers
        .get(SIGNATURE_HEADER)
        .and_then(|v| v.to_str().ok())
        .ok_or_else(|| bad("missing X-Sbflow-Signature header"))?;
    let ts_header = headers
        .get(TIMESTAMP_HEADER)
        .and_then(|v| v.to_str().ok())
        .ok_or_else(|| bad("missing X-Sbflow-Timestamp header"))?;

    let timestamp: i64 = ts_header
        .parse()
        .map_err(|_| bad("X-Sbflow-Timestamp must be a unix-seconds integer"))?;
    let now = chrono::Utc::now().timestamp();
    if (now - timestamp).abs() > MAX_SKEW_SECS {
        return Err(bad("X-Sbflow-Timestamp is outside the allowed window"));
    }

    let sig_hex = sig_header
        .strip_prefix("sha256=")
        .ok_or_else(|| bad("X-Sbflow-Signature must be of the form sha256=<hex>"))?;
    let sig_bytes = hex::decode(sig_hex).map_err(|_| bad("X-Sbflow-Signature is not valid hex"))?;

    // Signed message is `{timestamp}.{raw_body}`, built over raw bytes (not a
    // formatted String) so a non-UTF8 body still verifies correctly.
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes())
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    mac.update(ts_header.as_bytes());
    mac.update(b".");
    mac.update(raw_body);

    mac.verify_slice(&sig_bytes)
        .map_err(|_| (StatusCode::UNAUTHORIZED, "signature mismatch".to_string()))
}

/// Idempotency key `hash(repo, run_id, task_id, node_uid)` (B-S7).
/// Populated in V1; uniqueness/dedupe enforcement lands in V5.
fn idem_key(f: &Failure) -> String {
    let mut hasher = Sha256::new();
    hasher.update(format!(
        "{}|{}|{}|{}",
        f.repo, f.run_id, f.task_id, f.node_uid
    ));
    hex::encode(hasher.finalize())
}

/// Normalize a raw Airflow / dbt / CLI payload to the frozen `Failure` shape.
fn normalize(body: &serde_json::Value) -> Failure {
    let source = str_field(body, &["source"]).unwrap_or_else(|| {
        if body.get("dag_id").is_some() || body.get("exception").is_some() {
            "airflow".to_string()
        } else {
            "dbt".to_string()
        }
    });

    Failure {
        repo: str_field(body, &["repo"]).unwrap_or_else(|| "unknown".to_string()),
        run_id: str_field(body, &["run_id", "dag_run_id", "invocation_id"]).unwrap_or_default(),
        task_id: str_field(body, &["task_id"]).unwrap_or_default(),
        node_uid: str_field(body, &["node_uid", "unique_id"])
            .or_else(|| str_field(body, &["task_id"]))
            .unwrap_or_default(),
        error_text: str_field(body, &["error_text", "exception", "message"]).unwrap_or_default(),
        adapter: str_field(body, &["adapter"]).unwrap_or_else(|| "postgres".to_string()),
        run_results_ref: str_field(body, &["run_results_ref"]),
        source,
    }
}

/// First present, non-empty string among the given keys.
fn str_field(body: &serde_json::Value, keys: &[&str]) -> Option<String> {
    for k in keys {
        if let Some(v) = body.get(k).and_then(|v| v.as_str()) {
            if !v.is_empty() {
                return Some(v.to_string());
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_airflow_shape() {
        let body = serde_json::json!({
            "dag_id": "daily",
            "task_id": "build_orders",
            "run_id": "manual__2026-07-09",
            "exception": "column \"customer_id\" does not exist",
        });
        let f = normalize(&body);
        assert_eq!(f.source, "airflow");
        assert_eq!(f.task_id, "build_orders");
        assert_eq!(f.node_uid, "build_orders"); // falls back to task_id
        assert!(f.error_text.contains("does not exist"));
    }

    #[test]
    fn idem_key_is_stable() {
        let f = Failure {
            repo: "r".into(),
            run_id: "1".into(),
            task_id: "t".into(),
            node_uid: "n".into(),
            error_text: "e".into(),
            adapter: "postgres".into(),
            run_results_ref: None,
            source: "dbt".into(),
        };
        assert_eq!(idem_key(&f), idem_key(&f));
    }
}
