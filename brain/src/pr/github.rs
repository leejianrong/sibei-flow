//! The `github` git-host backend — opens a REAL Pull Request.
//!
//! Clones over HTTPS with a **PR-scoped** token, pushes the fix branch, then
//! `POST /repos/{owner}/{repo}/pulls`. The token is the *only* credential the
//! system holds, and it can do nothing but open PRs — no warehouse access, no
//! writes to `main` (invariant R6.1 / ADR-0005). The commit lands on a branch;
//! merging (the human's PR review) is the sole path to `main`.

use anyhow::{bail, Context, Result};

use super::git::GitWorkspace;
use super::githost::{GitHost, PrRef, PrRequest};

pub struct GithubHost {
    token: String,
    /// `owner/name`; falls back to the failure payload's repo when unset.
    repo_override: Option<String>,
    api_base: String,
}

impl GithubHost {
    pub fn new(token: String, repo_override: Option<String>, api_base: String) -> Self {
        Self {
            token,
            repo_override,
            api_base: api_base.trim_end_matches('/').to_string(),
        }
    }

    fn repo_slug<'a>(&'a self, req: &'a PrRequest) -> &'a str {
        self.repo_override.as_deref().unwrap_or(&req.repo)
    }

    /// The HTTPS clone/push URL carrying the token as a Basic-auth username
    /// (GitHub's documented `x-access-token` scheme).
    fn authed_clone_url(&self, repo: &str) -> String {
        format!(
            "https://x-access-token:{}@github.com/{}.git",
            self.token, repo
        )
    }

    /// POST the pull request. Split out from git so it is unit-testable against
    /// a mock host (no real GitHub in CI).
    pub fn create_pull(&self, repo: &str, req: &PrRequest) -> Result<String> {
        let client = reqwest::blocking::Client::builder()
            .user_agent("sibei-flow")
            .build()
            .context("building http client")?;
        let resp = client
            .post(pulls_url(&self.api_base, repo))
            .header("Accept", "application/vnd.github+json")
            .header("X-GitHub-Api-Version", "2022-11-28")
            .bearer_auth(&self.token)
            .json(&pull_payload(req))
            .send()
            .context("POST pulls")?;
        let status = resp.status();
        let body: serde_json::Value = resp.json().context("reading pulls response")?;
        if !status.is_success() {
            bail!("github pulls returned {status}: {body}");
        }
        body.get("html_url")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .context("pulls response missing html_url")
    }
}

impl GitHost for GithubHost {
    fn kind(&self) -> &'static str {
        "github"
    }

    fn open_pr(&self, req: &PrRequest) -> Result<PrRef> {
        let repo = self.repo_slug(req).to_string();
        let ws = GitWorkspace::clone(&self.authed_clone_url(&repo), &req.base_branch)?;
        ws.create_branch(&req.head_branch)?;
        ws.apply_diff(&req.diff)?;
        ws.commit(&req.title)?;
        ws.push("origin", &req.head_branch)?;
        let url = self.create_pull(&repo, req)?;
        Ok(PrRef {
            url,
            branch: req.head_branch.clone(),
        })
    }
}

/// The pulls endpoint for a repo (pure — unit-tested).
pub fn pulls_url(api_base: &str, repo: &str) -> String {
    format!("{}/repos/{}/pulls", api_base.trim_end_matches('/'), repo)
}

/// The pull-request JSON body (pure — unit-tested).
pub fn pull_payload(req: &PrRequest) -> serde_json::Value {
    serde_json::json!({
        "title": req.title,
        "head": req.head_branch,
        "base": req.base_branch,
        "body": req.body,
    })
}
