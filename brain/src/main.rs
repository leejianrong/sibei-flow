//! Brain entrypoint: load config → connect Postgres → migrate → serve.

use std::time::Duration;

use anyhow::Context;
use sqlx::postgres::PgPoolOptions;
use tokio::net::TcpListener;

use brain::config::Config;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,brain=info,sqlx=warn".into()),
        )
        .init();

    let cfg = Config::from_env();
    let pr_cfg = brain::pr::PrOpenerConfig::from_env();

    // SIB-88: fail before touching the database if the PR opener would hold a
    // PR-scoped token behind an unauthenticated webhook.
    brain::config::require_webhook_auth(
        pr_cfg.as_ref().map(|c| c.git_host.as_str()),
        cfg.webhook_secret.is_some(),
    )
    .map_err(anyhow::Error::msg)?;

    // SIB-87: same idea for the read API — never serve run data on an exposed
    // address with no token unless the operator explicitly opted out.
    brain::config::require_dashboard_auth(
        &cfg.bind_addr,
        cfg.dashboard_token.is_some(),
        cfg.allow_insecure_dashboard,
    )
    .map_err(anyhow::Error::msg)?;
    if cfg.dashboard_token.is_none() && cfg.allow_insecure_dashboard {
        tracing::warn!(
            "SBFLOW_ALLOW_INSECURE_DASHBOARD is set and SBFLOW_DASHBOARD_TOKEN is not: \
             GET /api/* is unauthenticated"
        );
    }

    // Retry the initial connection — in `docker compose up` Postgres may still
    // be coming up when the brain starts.
    let pool = connect_with_retry(&cfg.database_url).await?;

    brain::run_migrations(&pool)
        .await
        .context("running migrations")?;
    tracing::info!("migrations applied");

    // Crash recovery (V5, R7.1): requeue jobs orphaned by a worker that died
    // mid-run (expired lease in a non-terminal state) so they resume.
    match brain::reconcile_orphaned_jobs(&pool).await {
        Ok(n) => tracing::info!(requeued = n, "startup reconcile complete"),
        Err(e) => tracing::error!(error = %e, "startup reconcile failed; continuing"),
    }

    // V4: the PR opener. Runs only when a git host is configured (GIT_HOST);
    // otherwise the brain stays read-only-plus-enqueue as in V1–V3.
    match pr_cfg {
        Some(pr_cfg) => {
            // (The github-without-webhook-secret combination — the KAN-223
            // audit finding — is already refused at startup above, SIB-88.)
            if let Err(e) = brain::pr::spawn(pool.clone(), pr_cfg) {
                tracing::error!(error = %e, "PR opener failed to start; continuing without it");
            }
        }
        None => tracing::info!("PR opener disabled (set GIT_HOST=offline|github to enable)"),
    }

    let listener = TcpListener::bind(&cfg.bind_addr)
        .await
        .with_context(|| format!("binding {}", cfg.bind_addr))?;
    tracing::info!(addr = %cfg.bind_addr, "brain listening");

    axum::serve(
        listener,
        brain::app_with_auth(pool, cfg.webhook_secret, cfg.dashboard_token),
    )
    .await
    .context("serving")?;
    Ok(())
}

async fn connect_with_retry(url: &str) -> anyhow::Result<sqlx::PgPool> {
    let mut attempt = 0;
    loop {
        attempt += 1;
        match PgPoolOptions::new()
            .max_connections(5)
            .acquire_timeout(Duration::from_secs(5))
            .connect(url)
            .await
        {
            Ok(pool) => return Ok(pool),
            Err(e) if attempt < 30 => {
                tracing::warn!(attempt, error = %e, "postgres not ready, retrying in 1s");
                tokio::time::sleep(Duration::from_secs(1)).await;
            }
            Err(e) => return Err(e).context("connecting to postgres"),
        }
    }
}
