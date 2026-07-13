//! Thin wrapper over the `git` CLI for the one write action in the system:
//! clone a repo, branch, apply the drafted unified diff, commit, and push a
//! branch. No global git config is touched — identity is passed per-commit.
//!
//! This is deliberately process-based rather than a libgit2 binding: the
//! surface is tiny (five commands) and shelling out keeps the dependency /
//! security footprint minimal (R6.1 — the only capability added is "push a
//! branch", never a warehouse or prod-write credential).

use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{bail, Context, Result};

/// A throwaway working directory that is recursively removed on drop.
pub struct Workdir {
    path: PathBuf,
}

impl Workdir {
    /// Create a fresh unique working directory under the system temp dir.
    pub fn new() -> Result<Self> {
        let mut path = std::env::temp_dir();
        path.push(format!("sbflow-pr-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&path).with_context(|| format!("creating {}", path.display()))?;
        Ok(Self { path })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for Workdir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// A cloned git checkout the opener operates on.
pub struct GitWorkspace {
    dir: Workdir,
}

/// Commit identity (kept local to the checkout; never written to global config).
pub const AUTHOR_NAME: &str = "sibei-flow";
pub const AUTHOR_EMAIL: &str = "bot@sibei-flow.local";

impl GitWorkspace {
    /// Clone `url` at `base_branch` into a fresh temp dir (shallow).
    pub fn clone(url: &str, base_branch: &str) -> Result<Self> {
        let dir = Workdir::new()?;
        let dest = dir.path().join("repo");
        run(
            Path::new("."),
            &[
                "clone",
                "--depth",
                "1",
                "--branch",
                base_branch,
                url,
                &dest.to_string_lossy(),
            ],
        )
        .with_context(|| format!("git clone {url} @ {base_branch}"))?;
        Ok(Self { dir })
    }

    fn repo(&self) -> PathBuf {
        self.dir.path().join("repo")
    }

    /// Create and switch to a new branch off the current HEAD.
    pub fn create_branch(&self, name: &str) -> Result<()> {
        run(&self.repo(), &["checkout", "-b", name]).with_context(|| format!("branch {name}"))
    }

    /// Apply a unified diff (the RepairResult `diff`, `a/… b/…` prefixed).
    pub fn apply_diff(&self, diff: &str) -> Result<()> {
        let patch = self.repo().join(".sbflow.patch");
        std::fs::write(&patch, diff).context("writing patch file")?;
        let res = run(
            &self.repo(),
            &["apply", "--whitespace=nowarn", &patch.to_string_lossy()],
        );
        let _ = std::fs::remove_file(&patch);
        res.context("git apply (the drafted diff did not apply cleanly)")
    }

    /// Stage everything and commit with a local identity.
    pub fn commit(&self, message: &str) -> Result<()> {
        run(&self.repo(), &["add", "-A"]).context("git add")?;
        run(
            &self.repo(),
            &[
                "-c",
                &format!("user.name={AUTHOR_NAME}"),
                "-c",
                &format!("user.email={AUTHOR_EMAIL}"),
                "commit",
                "-m",
                message,
            ],
        )
        .context("git commit")
    }

    /// Push `branch` to `remote` (the origin the checkout was cloned from,
    /// unless a distinct authed URL is given).
    pub fn push(&self, remote: &str, branch: &str) -> Result<()> {
        run(&self.repo(), &["push", remote, branch]).with_context(|| format!("git push {branch}"))
    }

    /// The short SHA of the current HEAD (for the compare reference).
    pub fn head_sha(&self) -> Result<String> {
        let out = capture(&self.repo(), &["rev-parse", "--short", "HEAD"])?;
        Ok(out.trim().to_string())
    }
}

/// Run a git command in `cwd`, failing with stderr on a non-zero exit.
fn run(cwd: &Path, args: &[&str]) -> Result<()> {
    let output = Command::new("git")
        .args(args)
        .current_dir(cwd)
        .output()
        .with_context(|| format!("spawning `git {}`", args.join(" ")))?;
    if !output.status.success() {
        bail!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(())
}

/// Run a git command and capture stdout.
fn capture(cwd: &Path, args: &[&str]) -> Result<String> {
    let output = Command::new("git")
        .args(args)
        .current_dir(cwd)
        .output()
        .with_context(|| format!("spawning `git {}`", args.join(" ")))?;
    if !output.status.success() {
        bail!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

/// True when a `git` binary is available on PATH (tests skip when it is not).
pub fn git_available() -> bool {
    Command::new("git")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}
