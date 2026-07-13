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

    // Retry the initial connection — in `docker compose up` Postgres may still
    // be coming up when the brain starts.
    let pool = connect_with_retry(&cfg.database_url).await?;

    brain::run_migrations(&pool)
        .await
        .context("running migrations")?;
    tracing::info!("migrations applied");

    // V4: the PR opener. Runs only when a git host is configured (GIT_HOST);
    // otherwise the brain stays read-only-plus-enqueue as in V1–V3.
    match brain::pr::PrOpenerConfig::from_env() {
        Some(pr_cfg) => {
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

    axum::serve(listener, brain::app(pool))
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
