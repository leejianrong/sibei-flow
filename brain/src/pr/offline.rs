//! The `offline` git-host backend — the DEFAULT (tests / demo).
//!
//! Pushes the fix branch to the bundled bare git remote
//! (`git://git-remote:9418/analytics.git`, stood up by the hero pipeline) and
//! records the branch + a compare reference as the "PR". Fully offline: no
//! GitHub, no credentials, no network egress. The single write action is the
//! branch push (ADR-0005 / R6.1).

use anyhow::Result;

use super::git::GitWorkspace;
use super::githost::{GitHost, PrRef, PrRequest};

pub struct OfflineHost {
    remote_url: String,
}

impl OfflineHost {
    pub fn new(remote_url: String) -> Self {
        Self { remote_url }
    }
}

impl GitHost for OfflineHost {
    fn kind(&self) -> &'static str {
        "offline"
    }

    fn open_pr(&self, req: &PrRequest) -> Result<PrRef> {
        let ws = GitWorkspace::clone(&self.remote_url, &req.base_branch)?;
        ws.create_branch(&req.head_branch)?;
        ws.apply_diff(&req.diff)?;
        // The rendered body is preserved in the commit message body so the
        // offline "PR" still carries the full explanation + evidence in git.
        ws.commit(&format!("{}\n\n{}", req.title, req.body))?;
        ws.push("origin", &req.head_branch)?;
        let sha = ws.head_sha().unwrap_or_default();

        // No web host offline: the "PR" is the pushed branch + a compare ref.
        let url = format!(
            "{remote} (branch {head}; compare {base}...{head} @ {sha})",
            remote = self.remote_url,
            head = req.head_branch,
            base = req.base_branch,
        );
        Ok(PrRef {
            url,
            branch: req.head_branch.clone(),
        })
    }
}
