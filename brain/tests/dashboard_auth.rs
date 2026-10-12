//! SIB-87 — bearer-token gate on the dashboard read API.
//!
//! `/api/*` requires `Authorization: Bearer <token>` when a token is
//! configured; `/` (static shell, no run data) and `/healthz` stay open; the
//! webhook keeps its own auth; with no token configured the API is open
//! (loopback dev use — the startup guard covers exposed binds).

use sqlx::PgPool;
use std::net::SocketAddr;

const TOKEN: &str = "test-dashboard-token-do-not-use-in-prod";

async fn spawn(pool: PgPool, dashboard_token: Option<String>) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr: SocketAddr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, brain::app_with_auth(pool, None, dashboard_token))
            .await
            .unwrap();
    });
    format!("http://{addr}")
}

#[sqlx::test]
async fn api_without_token_is_401(pool: PgPool) {
    let base = spawn(pool, Some(TOKEN.to_string())).await;
    let client = reqwest::Client::new();
    for path in [
        "/api/runs",
        "/api/runs/00000000-0000-0000-0000-000000000000",
    ] {
        let resp = client.get(format!("{base}{path}")).send().await.unwrap();
        assert_eq!(resp.status(), 401, "{path} must require the token");
        assert_eq!(
            resp.headers().get("www-authenticate").unwrap(),
            "Bearer",
            "{path}"
        );
    }
}

#[sqlx::test]
async fn api_with_wrong_or_malformed_token_is_401(pool: PgPool) {
    let base = spawn(pool, Some(TOKEN.to_string())).await;
    let client = reqwest::Client::new();
    for header in [
        "Bearer wrong-token".to_string(),
        format!("Basic {TOKEN}"),
        TOKEN.to_string(),
        "Bearer ".to_string(),
    ] {
        let resp = client
            .get(format!("{base}/api/runs"))
            .header("authorization", &header)
            .send()
            .await
            .unwrap();
        assert_eq!(resp.status(), 401, "header {header:?} must be rejected");
    }
}

#[sqlx::test]
async fn api_with_correct_token_is_200(pool: PgPool) {
    let base = spawn(pool, Some(TOKEN.to_string())).await;
    let client = reqwest::Client::new();
    let resp = client
        .get(format!("{base}/api/runs"))
        .bearer_auth(TOKEN)
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 200);

    // A valid token on an unknown run is a 404 (authenticated, not found).
    let resp = client
        .get(format!(
            "{base}/api/runs/00000000-0000-0000-0000-000000000000"
        ))
        .bearer_auth(TOKEN)
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 404);
}

#[sqlx::test]
async fn shell_and_healthz_stay_open(pool: PgPool) {
    let base = spawn(pool, Some(TOKEN.to_string())).await;
    let client = reqwest::Client::new();
    assert_eq!(
        client
            .get(format!("{base}/"))
            .send()
            .await
            .unwrap()
            .status(),
        200
    );
    assert_eq!(
        client
            .get(format!("{base}/healthz"))
            .send()
            .await
            .unwrap()
            .status(),
        200
    );
}

#[sqlx::test]
async fn no_token_configured_leaves_api_open(pool: PgPool) {
    let base = spawn(pool, None).await;
    let client = reqwest::Client::new();
    let resp = client.get(format!("{base}/api/runs")).send().await.unwrap();
    assert_eq!(resp.status(), 200);
}

#[sqlx::test]
async fn dashboard_token_does_not_gate_the_webhook(pool: PgPool) {
    // The webhook has its own HMAC scheme; the dashboard token must not be
    // required (or accepted as a substitute) there.
    let base = spawn(pool, Some(TOKEN.to_string())).await;
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("{base}/webhook"))
        .json(&serde_json::json!({
            "repo": "acme/analytics",
            "run_id": "r1",
            "task_id": "t1",
            "node_uid": "model.analytics.orders",
            "error_text": "column \"customer_id\" does not exist",
            "adapter": "postgres",
            "source": "airflow"
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), 202);
}
