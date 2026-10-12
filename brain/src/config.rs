//! Environment-driven configuration for the brain.

/// Runtime configuration, read from the environment at startup.
pub struct Config {
    /// Postgres connection string (source of truth, ADR-0009).
    pub database_url: String,
    /// Address the HTTP server binds to (webhook + dashboard).
    pub bind_addr: String,
    /// Shared secret for HMAC-SHA256 `POST /webhook` request signing
    /// (KAN-926). `None` (unset) leaves the webhook unauthenticated, which
    /// keeps `make demo` / the default offline compose flow working with no
    /// extra setup. Required when `GIT_HOST=github` (`require_webhook_auth`).
    pub webhook_secret: Option<String>,
    /// Bearer token required on `GET /api/*` (SIB-87). Unset is only allowed on
    /// a loopback bind, or with `SBFLOW_ALLOW_INSECURE_DASHBOARD=1`
    /// (`require_dashboard_auth`).
    pub dashboard_token: Option<String>,
    /// Explicit opt-out of the dashboard-auth startup guard, for dev only.
    pub allow_insecure_dashboard: bool,
}

impl Config {
    /// Load config from the environment, applying sane single-VM defaults.
    pub fn from_env() -> Self {
        let database_url = std::env::var("DATABASE_URL")
            .unwrap_or_else(|_| "postgres://sibei:sibei@localhost:5432/sibei".to_string());
        let bind_addr = std::env::var("BIND_ADDR").unwrap_or_else(|_| "0.0.0.0:8080".to_string());
        let webhook_secret = std::env::var("SBFLOW_WEBHOOK_SECRET")
            .ok()
            .filter(|s| !s.is_empty());
        let dashboard_token = std::env::var("SBFLOW_DASHBOARD_TOKEN")
            .ok()
            .filter(|s| !s.is_empty());
        let allow_insecure_dashboard = std::env::var("SBFLOW_ALLOW_INSECURE_DASHBOARD")
            .map(|v| matches!(v.as_str(), "1" | "true" | "yes"))
            .unwrap_or(false);
        Self {
            database_url,
            bind_addr,
            webhook_secret,
            dashboard_token,
            allow_insecure_dashboard,
        }
    }
}

/// Whether a `BIND_ADDR` listens only on the loopback interface.
fn is_loopback_bind(bind_addr: &str) -> bool {
    match bind_addr.parse::<std::net::SocketAddr>() {
        Ok(addr) => addr.ip().is_loopback(),
        // Not a literal socket address (e.g. `localhost:8080`): loopback only
        // if the host part says so; anything else is treated as exposed.
        Err(_) => bind_addr
            .rsplit_once(':')
            .map(|(host, _)| host.eq_ignore_ascii_case("localhost"))
            .unwrap_or(false),
    }
}

/// Startup guard (SIB-87): the dashboard API serves diffs, evidence,
/// transcripts and cost, so a non-loopback bind needs `SBFLOW_DASHBOARD_TOKEN`
/// unless `SBFLOW_ALLOW_INSECURE_DASHBOARD=1` explicitly opts out. A loopback
/// bind without a token stays fine for local development.
pub fn require_dashboard_auth(
    bind_addr: &str,
    token_set: bool,
    allow_insecure: bool,
) -> Result<(), String> {
    if token_set || allow_insecure || is_loopback_bind(bind_addr) {
        return Ok(());
    }
    Err(format!(
        "refusing to serve the dashboard API on {bind_addr} without authentication: \
         set SBFLOW_DASHBOARD_TOKEN (clients send `Authorization: Bearer <token>`), \
         bind a loopback address, or set SBFLOW_ALLOW_INSECURE_DASHBOARD=1 for dev"
    ))
}

/// Startup guard (KAN-926 / SIB-88): the PR opener may hold a PR-scoped token
/// only if the webhook that feeds it is authenticated. With `GIT_HOST=github`
/// an unauthenticated caller could otherwise steer that token at an
/// attacker-chosen branch/diff, so a missing `SBFLOW_WEBHOOK_SECRET` is a hard
/// startup error rather than a warning. `offline` (the bundled bare remote,
/// no credentials) and an unset git host stay usable without a secret so the
/// local demo keeps working.
pub fn require_webhook_auth(git_host: Option<&str>, secret_set: bool) -> Result<(), String> {
    match git_host {
        Some("github") if !secret_set => Err(
            "GIT_HOST=github requires SBFLOW_WEBHOOK_SECRET: refusing to start with an \
             unauthenticated POST /webhook while a PR-scoped token is configured \
             (set SBFLOW_WEBHOOK_SECRET, and the same value for the sbflow CLI / senders)"
                .to_string(),
        ),
        _ => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::{require_dashboard_auth, require_webhook_auth};

    #[test]
    fn exposed_bind_without_token_is_refused() {
        let err = require_dashboard_auth("0.0.0.0:8080", false, false).unwrap_err();
        assert!(err.contains("SBFLOW_DASHBOARD_TOKEN"));
        assert!(require_dashboard_auth("10.0.0.5:8080", false, false).is_err());
    }

    #[test]
    fn exposed_bind_with_token_or_explicit_opt_out_is_allowed() {
        assert!(require_dashboard_auth("0.0.0.0:8080", true, false).is_ok());
        assert!(require_dashboard_auth("0.0.0.0:8080", false, true).is_ok());
    }

    #[test]
    fn loopback_bind_needs_no_token() {
        assert!(require_dashboard_auth("127.0.0.1:8080", false, false).is_ok());
        assert!(require_dashboard_auth("[::1]:8080", false, false).is_ok());
        assert!(require_dashboard_auth("localhost:8080", false, false).is_ok());
    }

    #[test]
    fn github_without_secret_is_refused() {
        let err = require_webhook_auth(Some("github"), false).unwrap_err();
        assert!(err.contains("SBFLOW_WEBHOOK_SECRET"));
    }

    #[test]
    fn github_with_secret_is_allowed() {
        assert!(require_webhook_auth(Some("github"), true).is_ok());
    }

    #[test]
    fn offline_and_unset_hosts_need_no_secret() {
        assert!(require_webhook_auth(Some("offline"), false).is_ok());
        assert!(require_webhook_auth(None, false).is_ok());
    }
}
