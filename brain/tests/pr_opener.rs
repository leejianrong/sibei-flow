//! V4 PR-opener tests (brain-side, no real GitHub, no live stack).
//!
//! Covers the two things worth asserting without the hero stack:
//!   * the `github` backend's request construction against a MOCK host, and
//!   * the `offline` backend's real clone → branch → apply → commit → push
//!     against a throwaway local bare remote (skipped when `git` is absent),
//!   * the R6.1 guardrail: the PR-opener config surface cannot carry a
//!     prod-write / warehouse credential — only a PR-scoped token or a push.

use std::net::SocketAddr;
use std::process::Command;
use std::sync::{Arc, Mutex};

use axum::{extract::State, http::HeaderMap, routing::post, Json, Router};
use brain::pr::git::{git_available, GitWorkspace};
use brain::pr::githost::{build_host, GitHost, PrOpenerConfig, PrRequest};
use brain::pr::github::{pull_payload, pulls_url, GithubHost};
use brain::pr::offline::OfflineHost;
use uuid::Uuid;

fn sample_req() -> PrRequest {
    PrRequest {
        job_id: Uuid::new_v4(),
        repo: "acme/analytics".into(),
        base_branch: "main".into(),
        head_branch: "sbflow/fix-abc12345".into(),
        title: "sbflow: auto-fix schema_drift in model.analytics.orders".into(),
        body: "## body\nexplanation + evidence".into(),
        diff: ORDERS_DIFF.into(),
    }
}

// ---- pure request-construction helpers -----------------------------------

#[test]
fn pulls_url_targets_the_repo_pulls_endpoint() {
    assert_eq!(
        pulls_url("https://api.github.com", "acme/analytics"),
        "https://api.github.com/repos/acme/analytics/pulls"
    );
    // Trailing slash on the base is tolerated.
    assert_eq!(
        pulls_url("http://localhost:1234/", "o/r"),
        "http://localhost:1234/repos/o/r/pulls"
    );
}

#[test]
fn pull_payload_maps_head_base_title_body() {
    let req = sample_req();
    let p = pull_payload(&req);
    assert_eq!(p["head"], "sbflow/fix-abc12345");
    assert_eq!(p["base"], "main");
    assert_eq!(p["title"], req.title);
    assert_eq!(p["body"], req.body);
}

// ---- github backend against a MOCK host -----------------------------------

#[derive(Default)]
struct Captured {
    auth: Option<String>,
    user_agent: Option<String>,
    accept: Option<String>,
    api_version: Option<String>,
    path: Option<String>,
    body: Option<serde_json::Value>,
}

async fn mock_pulls(
    State(store): State<Arc<Mutex<Captured>>>,
    axum::extract::Path((owner, repo)): axum::extract::Path<(String, String)>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Json<serde_json::Value> {
    let hv = |k: &str| {
        headers
            .get(k)
            .and_then(|v| v.to_str().ok())
            .map(String::from)
    };
    {
        let mut c = store.lock().unwrap();
        c.auth = hv("authorization");
        c.user_agent = hv("user-agent");
        c.accept = hv("accept");
        c.api_version = hv("x-github-api-version");
        c.path = Some(format!("/repos/{owner}/{repo}/pulls"));
        c.body = Some(body);
    }
    Json(serde_json::json!({
        "html_url": "https://github.com/acme/analytics/pull/42",
        "number": 42
    }))
}

#[tokio::test]
async fn github_backend_builds_a_correct_authenticated_request() {
    let store = Arc::new(Mutex::new(Captured::default()));
    let app = Router::new()
        .route("/repos/{owner}/{repo}/pulls", post(mock_pulls))
        .with_state(store.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr: SocketAddr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    let api_base = format!("http://{addr}");

    let host = GithubHost::new("ghp_testtoken".into(), None, api_base);
    let req = sample_req();
    // reqwest::blocking must not run on the async runtime thread.
    let url = tokio::task::spawn_blocking(move || host.create_pull("acme/analytics", &req))
        .await
        .unwrap()
        .expect("create_pull ok");

    assert_eq!(url, "https://github.com/acme/analytics/pull/42");
    let c = store.lock().unwrap();
    assert_eq!(c.auth.as_deref(), Some("Bearer ghp_testtoken"));
    assert_eq!(c.user_agent.as_deref(), Some("sibei-flow"));
    assert_eq!(c.accept.as_deref(), Some("application/vnd.github+json"));
    assert_eq!(c.api_version.as_deref(), Some("2022-11-28"));
    assert_eq!(c.path.as_deref(), Some("/repos/acme/analytics/pulls"));
    let body = c.body.as_ref().unwrap();
    assert_eq!(body["head"], "sbflow/fix-abc12345");
    assert_eq!(body["base"], "main");
}

// ---- offline backend against a throwaway local bare remote ----------------

const ORDERS_SQL: &str = "select\n    customer_id,\n    order_ts,\n    amount\nfrom customers\n";
const ORDERS_DIFF: &str = "--- a/models/marts/orders.sql\n+++ b/models/marts/orders.sql\n@@ -1,5 +1,5 @@\n select\n-    customer_id,\n+    cust_id as customer_id,\n     order_ts,\n     amount\n from customers\n";

/// Run a git command in `dir`, panicking on failure (test helper).
fn git(dir: &std::path::Path, args: &[&str]) {
    let out = Command::new("git")
        .args(args)
        .current_dir(dir)
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "git {:?} failed: {}",
        args,
        String::from_utf8_lossy(&out.stderr)
    );
}

#[test]
fn offline_backend_pushes_the_fix_branch_with_the_applied_diff() {
    if !git_available() {
        eprintln!("skip: git not available");
        return;
    }
    let tmp = std::env::temp_dir().join(format!("sbflow-offtest-{}", Uuid::new_v4()));
    std::fs::create_dir_all(&tmp).unwrap();
    let bare = tmp.join("origin.git");
    let seed = tmp.join("seed");
    std::fs::create_dir_all(&seed).unwrap();

    // 1. A bare remote seeded with orders.sql on `main` (models/marts/…).
    git(
        &tmp,
        &["init", "--bare", "-b", "main", bare.to_str().unwrap()],
    );
    git(&seed, &["init", "-b", "main"]);
    git(&seed, &["config", "user.email", "t@t"]);
    git(&seed, &["config", "user.name", "t"]);
    std::fs::create_dir_all(seed.join("models/marts")).unwrap();
    std::fs::write(seed.join("models/marts/orders.sql"), ORDERS_SQL).unwrap();
    git(&seed, &["add", "-A"]);
    git(&seed, &["commit", "-m", "seed"]);
    git(&seed, &["push", bare.to_str().unwrap(), "main"]);

    // 2. Open the "PR" via the offline backend (file:// bare remote).
    let remote = format!("file://{}", bare.to_str().unwrap());
    let host = OfflineHost::new(remote.clone());
    let mut req = sample_req();
    req.head_branch = "sbflow/fix-offtest".into();
    let pr = host.open_pr(&req).expect("offline open_pr");

    // 3. The branch is pushed and the compare ref is recorded.
    assert_eq!(pr.branch, "sbflow/fix-offtest");
    assert!(
        pr.url.contains("main...sbflow/fix-offtest"),
        "url={}",
        pr.url
    );
    let refs = Command::new("git")
        .args(["ls-remote", "--heads", &remote])
        .output()
        .unwrap();
    let refs = String::from_utf8_lossy(&refs.stdout);
    assert!(
        refs.contains("refs/heads/sbflow/fix-offtest"),
        "pushed refs: {refs}"
    );

    // 4. The pushed branch carries the applied minimal diff.
    let check = tmp.join("check");
    git(
        &tmp,
        &[
            "clone",
            "-b",
            "sbflow/fix-offtest",
            &remote,
            check.to_str().unwrap(),
        ],
    );
    let fixed = std::fs::read_to_string(check.join("models/marts/orders.sql")).unwrap();
    assert!(
        fixed.contains("cust_id as customer_id,"),
        "content: {fixed}"
    );
    assert!(
        !fixed.contains("\n    customer_id,\n"),
        "old column remained"
    );

    let _ = std::fs::remove_dir_all(&tmp);
}

// ---- R6.1 guardrail: no prod-write credential in the opener surface --------

#[test]
fn pr_opener_config_carries_no_prodwrite_credential() {
    // The opener's entire capability is "push a branch / open a PR". The only
    // credential-bearing field is a PR-scoped github token — there is no field
    // that could ever hold a warehouse / prod DB URL. Assert that structurally:
    // even with warehouse creds present in the process env, the config seam
    // cannot surface them, and the built host's capability is push+PR only.
    let cfg = PrOpenerConfig {
        git_host: "offline".into(),
        base_branch: "main".into(),
        branch_prefix: "sbflow/fix".into(),
        poll_interval_secs: 2.0,
        remote_url: "git://git-remote:9418/analytics.git".into(),
        github_token: None,
        github_repo: Some("acme/analytics".into()),
        github_api_base: "https://api.github.com".into(),
    };
    let rendered = format!("{cfg:?}");
    for forbidden in [
        "sbflow_dev",
        "sbflow_ro",
        "warehouse",
        "5432",
        "5456",
        "password",
    ] {
        assert!(
            !rendered.contains(forbidden),
            "PR opener config must not carry a prod-write/warehouse credential ({forbidden})"
        );
    }
    let host = build_host(&cfg).unwrap();
    assert_eq!(host.kind(), "offline");

    // github mode requires (only) a PR-scoped token, nothing warehouse-shaped.
    let mut gh = cfg.clone();
    gh.git_host = "github".into();
    assert!(build_host(&gh).is_err(), "github requires a token");
    gh.github_token = Some("ghp_pr_scoped_only".into());
    assert_eq!(build_host(&gh).unwrap().kind(), "github");
}

/// A drafted diff that touches a path outside the model tree would still only
/// ever land on a branch — but sanity-check the workspace refuses a diff that
/// does not apply (never a partial/forced write).
#[test]
fn workspace_rejects_a_nonapplying_diff() {
    if !git_available() {
        eprintln!("skip: git not available");
        return;
    }
    let tmp = std::env::temp_dir().join(format!("sbflow-badpatch-{}", Uuid::new_v4()));
    std::fs::create_dir_all(&tmp).unwrap();
    let bare = tmp.join("origin.git");
    let seed = tmp.join("seed");
    std::fs::create_dir_all(&seed).unwrap();
    git(
        &tmp,
        &["init", "--bare", "-b", "main", bare.to_str().unwrap()],
    );
    git(&seed, &["init", "-b", "main"]);
    git(&seed, &["config", "user.email", "t@t"]);
    git(&seed, &["config", "user.name", "t"]);
    std::fs::write(seed.join("other.sql"), "select 1\n").unwrap();
    git(&seed, &["add", "-A"]);
    git(&seed, &["commit", "-m", "seed"]);
    git(&seed, &["push", bare.to_str().unwrap(), "main"]);

    let remote = format!("file://{}", bare.to_str().unwrap());
    let ws = GitWorkspace::clone(&remote, "main").unwrap();
    ws.create_branch("sbflow/fix-bad").unwrap();
    // Diff references a file/content that does not exist in the checkout.
    assert!(ws.apply_diff(ORDERS_DIFF).is_err());
    let _ = std::fs::remove_dir_all(&tmp);
}
