//! The pluggable git-host seam (mirrors ADR-0008's executor-backend pattern).
//!
//! Two backends are selected by config:
//!   * `offline` — push the fix branch to the bundled bare git remote and record
//!     the branch + compare ref as the "PR". Fully offline, no credentials.
//!     This is the DEFAULT (tests / demo).
//!   * `github`  — open a REAL PR via a PR-scoped GitHub token (REST
//!     `POST /repos/{owner}/{repo}/pulls`).
//!
//! The seam is intentionally narrow: a backend can `open_pr`. Nowhere does it
//! accept a warehouse / prod-write credential — the only capability it adds to
//! the system is "push a branch and open a PR" (invariant R6.1 / ADR-0005).

use std::sync::Arc;

use anyhow::{bail, Result};
use uuid::Uuid;

use super::github::GithubHost;
use super::offline::OfflineHost;

/// Everything a backend needs to open one PR. Owned so it can move across the
/// `spawn_blocking` boundary in the poller.
#[derive(Debug, Clone)]
pub struct PrRequest {
    pub job_id: Uuid,
    /// The `owner/name` slug from the failure payload (github repo target).
    pub repo: String,
    pub base_branch: String,
    pub head_branch: String,
    pub title: String,
    pub body: String,
    /// The unified diff to apply (the RepairResult `diff`).
    pub diff: String,
}

/// What a backend returns after opening the PR — persisted onto the job row.
#[derive(Debug, Clone)]
pub struct PrRef {
    /// The host PR URL (github) or an offline compare reference.
    pub url: String,
    pub branch: String,
}

/// A git host that can turn a verified diff into an opened PR-on-a-branch.
pub trait GitHost: Send + Sync {
    /// Backend identifier, for logs/tests (`offline` | `github`).
    fn kind(&self) -> &'static str;
    /// Clone → branch → apply diff → commit → push, then open/record the PR.
    fn open_pr(&self, req: &PrRequest) -> Result<PrRef>;
}

/// PR-opener configuration, read from the environment. Absent (`None`) when
/// `GIT_HOST` is unset — the opener stays dormant.
#[derive(Debug, Clone)]
pub struct PrOpenerConfig {
    pub git_host: String,
    pub base_branch: String,
    pub branch_prefix: String,
    pub poll_interval_secs: f64,
    /// offline backend: bare remote URL to push to.
    pub remote_url: String,
    /// github backend: PR-scoped token (never a prod-write credential).
    pub github_token: Option<String>,
    /// github backend: `owner/name` (falls back to the failure payload's repo).
    pub github_repo: Option<String>,
    /// github backend: API base (overridable so tests hit a mock host).
    pub github_api_base: String,
}

impl PrOpenerConfig {
    /// Load from the environment. Returns `None` when `GIT_HOST` is unset, which
    /// keeps the opener disabled (e.g. core `docker compose up` without a host).
    pub fn from_env() -> Option<Self> {
        let git_host = std::env::var("GIT_HOST").ok()?;
        if git_host.eq_ignore_ascii_case("none") || git_host.is_empty() {
            return None;
        }
        Some(Self {
            git_host: git_host.to_lowercase(),
            base_branch: env_or("GIT_BASE_BRANCH", "main"),
            branch_prefix: env_or("GIT_BRANCH_PREFIX", "sbflow/fix"),
            poll_interval_secs: std::env::var("PR_POLL_INTERVAL")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(2.0),
            remote_url: env_or("GIT_REMOTE_URL", "git://git-remote:9418/analytics.git"),
            github_token: std::env::var("GITHUB_TOKEN").ok().filter(|s| !s.is_empty()),
            github_repo: std::env::var("GIT_REPO").ok().filter(|s| !s.is_empty()),
            github_api_base: env_or("GITHUB_API_URL", "https://api.github.com"),
        })
    }
}

fn env_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

/// Construct the configured backend as a shared trait object.
pub fn build_host(cfg: &PrOpenerConfig) -> Result<Arc<dyn GitHost>> {
    match cfg.git_host.as_str() {
        "offline" => Ok(Arc::new(OfflineHost::new(cfg.remote_url.clone()))),
        "github" => {
            let token = cfg
                .github_token
                .clone()
                .ok_or_else(|| anyhow::anyhow!("GIT_HOST=github requires GITHUB_TOKEN"))?;
            Ok(Arc::new(GithubHost::new(
                token,
                cfg.github_repo.clone(),
                cfg.github_api_base.clone(),
            )))
        }
        other => bail!("unknown GIT_HOST '{other}' (expected offline|github)"),
    }
}
