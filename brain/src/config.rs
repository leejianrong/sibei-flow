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
        Self {
            database_url,
            bind_addr,
            webhook_secret,
        }
    }
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
    use super::require_webhook_auth;

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
