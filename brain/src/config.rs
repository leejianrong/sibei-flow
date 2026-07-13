//! Environment-driven configuration for the brain.

/// Runtime configuration, read from the environment at startup.
pub struct Config {
    /// Postgres connection string (source of truth, ADR-0009).
    pub database_url: String,
    /// Address the HTTP server binds to (webhook + dashboard).
    pub bind_addr: String,
}

impl Config {
    /// Load config from the environment, applying sane single-VM defaults.
    pub fn from_env() -> Self {
        let database_url = std::env::var("DATABASE_URL")
            .unwrap_or_else(|_| "postgres://sibei:sibei@localhost:5432/sibei".to_string());
        let bind_addr = std::env::var("BIND_ADDR").unwrap_or_else(|_| "0.0.0.0:8080".to_string());
        Self {
            database_url,
            bind_addr,
        }
    }
}
