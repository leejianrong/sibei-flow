//! Renders the PR title + body from a terminal `pr_proposed` job (U3).
//!
//! The body carries everything the reviewer needs to approve by merging: a
//! plain-English explanation, the minimal diff, a verification evidence table
//! (tier-1 compile / tier-2 sample / output-schema), a confidence + risk badge,
//! a collapsible reasoning transcript, and a "rollback = git revert" footer.
//! It is pure Markdown built from the frozen RepairResult — no contract change.

use serde_json::Value;

use crate::models::JobRow;

/// A concise, deterministic PR title.
pub fn render_title(job: &JobRow) -> String {
    let class = job.failure_class.as_deref().unwrap_or("failure");
    let node = job.node_uid.as_deref().unwrap_or("model");
    format!("sbflow: auto-fix {class} in {node}")
}

/// The full Markdown PR body.
///
/// `journal_dir` is where a `transcript.kind == "journal"` arm's SQLite file is
/// mounted (read-only) in this container — `PrOpenerConfig::satay_journal_dir`,
/// consulted only by `render_transcript`'s `journal` arm (KAN-649).
pub async fn render_body(job: &JobRow, journal_dir: &str) -> String {
    let result = job.result.clone().unwrap_or(Value::Null);
    let get = |k: &str| result.get(k);

    let node = job.node_uid.as_deref().unwrap_or("(unknown)");
    let task = job.task_id.as_deref().unwrap_or("(unknown)");
    let class = job.failure_class.as_deref().unwrap_or("(unknown)");

    let mut s = String::new();
    s.push_str("## 🤖 sibei-flow auto-fix\n\n");
    s.push_str(&format!(
        "A **{class}** failure broke `{node}` (task `{task}`). sibei-flow drafted \
         the minimal fix below, verified it in an ephemeral sandbox, and opened \
         this PR. Approve it by merging; nothing reaches `main` otherwise.\n\n"
    ));

    if let Some(exp) = get("explanation").and_then(Value::as_str) {
        s.push_str("### What changed & why\n\n");
        s.push_str(exp.trim());
        s.push_str("\n\n");
    }

    if let Some(diff) = get("diff").and_then(Value::as_str) {
        s.push_str("### The fix (minimal diff)\n\n");
        s.push_str("```diff\n");
        s.push_str(diff.trim_end());
        s.push_str("\n```\n\n");
    }

    s.push_str(&render_evidence(get("evidence")));
    s.push_str(&render_confidence(
        get("confidence"),
        get("risk_class"),
        get("factors"),
    ));

    s.push_str(&render_transcript(get("transcript"), journal_dir).await);

    s.push_str("---\n");
    s.push_str(
        "**Rollback** = `git revert` this PR's merge commit. sibei-flow holds no \
         prod-write credentials; the only write it ever performs is opening this \
         branch + PR (ADR-0005).\n",
    );
    s
}

/// The verification evidence table (honest disclosure of what actually ran).
/// Render `RepairResult.transcript`, a **discriminated** union (ADR-0013):
///
/// ```text
/// transcript?: {"kind": "lines",   "lines": [str]}
///            | {"kind": "journal", "run_id": str, "ref": str}
/// ```
///
/// Branch on `kind` and never sniff the structure: the whole point of tagging
/// the union is that a reader cannot guess wrong. An unknown `kind` renders
/// nothing rather than guessing, so a newer worker emitting a third arm degrades
/// to a PR without a transcript instead of a PR with a mangled one.
///
/// The `journal` arm (KAN-649) is now a **real read of the journal**, not a
/// parallel artifact that can drift from what the run actually did: it opens the
/// worker's persisted Satay journal (mounted read-only at `journal_dir`, the same
/// path the worker's `SBFLOW_SATAY_JOURNAL_DIR` writes to — see
/// `docker-compose.yml`'s shared `satay-journal` volume) and renders that run's
/// events. See `pr/journal.rs` for the reader and its documented known gaps
/// (no typed reconstruction of the worker's Python types; a spilled blob is
/// resolved by reading the sibling `blobs/` dir directly).
async fn render_transcript(t: Option<&Value>, journal_dir: &str) -> String {
    let Some(t) = t else {
        return String::new();
    };
    let body = match t.get("kind").and_then(Value::as_str) {
        Some("lines") => {
            let lines = t.get("lines").and_then(Value::as_array);
            let joined = lines
                .map(|l| {
                    l.iter()
                        .filter_map(Value::as_str)
                        .collect::<Vec<_>>()
                        .join("\n")
                })
                .unwrap_or_default();
            if joined.trim().is_empty() {
                return String::new();
            }
            format!("```\n{}\n```\n", joined.trim_end())
        }
        Some("journal") => {
            let run_id = t.get("run_id").and_then(Value::as_str).unwrap_or("unknown");
            let journal_ref = t.get("ref").and_then(Value::as_str).unwrap_or("");
            let rendered = super::journal::render(journal_dir, run_id, journal_ref).await;
            format!("```\n{}\n```\n", rendered.trim_end())
        }
        _ => return String::new(),
    };
    format!("<details>\n<summary>🧠 Reasoning transcript</summary>\n\n{body}\n</details>\n\n")
}

fn render_evidence(ev: Option<&Value>) -> String {
    let ev = match ev {
        Some(v) if !v.is_null() => v,
        _ => return String::new(),
    };
    let mut s = String::from("### Verification (before you see it)\n\n");
    s.push_str("| check | result |\n|---|---|\n");

    let t1 = ev.get("tier1");
    let t1_pass = t1.and_then(|t| t.get("passed")).and_then(Value::as_bool);
    s.push_str(&format!(
        "| tier-1 compile (`dbt compile`) | {} |\n",
        match t1_pass {
            Some(true) => "✅ passed",
            Some(false) => "❌ failed",
            None => "—",
        }
    ));

    let t2 = ev.get("tier2");
    let t2_ran = t2.and_then(|t| t.get("ran")).and_then(Value::as_bool);
    let t2_pass = t2.and_then(|t| t.get("passed")).and_then(Value::as_bool);
    s.push_str(&format!(
        "| tier-2 sample (`dbt build --sample`) | {} |\n",
        match (t2_ran, t2_pass) {
            (Some(true), Some(true)) => "✅ passed".to_string(),
            (Some(true), Some(false)) => "❌ failed".to_string(),
            _ => "⚠️ not configured".to_string(),
        }
    ));

    let os = ev.get("output_schema");
    let os_changed = os.and_then(|o| o.get("changed")).and_then(Value::as_bool);
    let os_detail = os
        .and_then(|o| o.get("detail"))
        .and_then(Value::as_str)
        .unwrap_or("");
    s.push_str(&format!(
        "| output schema | {} |\n\n",
        match os_changed {
            Some(false) => format!("✅ unchanged — {os_detail}"),
            Some(true) => format!("⚠️ changed — {os_detail}"),
            None => format!("⚠️ undetermined — {os_detail}"),
        }
    ));
    s
}

/// The confidence percentage + risk badge + contributing factors.
fn render_confidence(
    confidence: Option<&Value>,
    risk: Option<&Value>,
    factors: Option<&Value>,
) -> String {
    let conf = confidence.and_then(Value::as_f64);
    let risk = risk.and_then(Value::as_str);
    if conf.is_none() && risk.is_none() {
        return String::new();
    }
    let mut s = String::from("### Confidence & risk\n\n");
    let conf_str = conf
        .map(|c| format!("{}%", (c * 100.0).round() as i64))
        .unwrap_or_else(|| "—".to_string());
    let badge = match risk {
        Some("low") => "🟢 low",
        Some("medium") => "🟡 medium",
        Some("high") => "🔴 high",
        Some(other) => other,
        None => "—",
    };
    s.push_str(&format!(
        "**Confidence:** {conf_str} · **Risk:** {badge}\n\n"
    ));
    if let Some(arr) = factors.and_then(Value::as_array) {
        let items = arr
            .iter()
            .filter_map(Value::as_str)
            .map(|f| format!("- {f}"))
            .collect::<Vec<_>>()
            .join("\n");
        if !items.is_empty() {
            s.push_str(&items);
            s.push_str("\n\n");
        }
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;
    use uuid::Uuid;

    fn sample_job() -> JobRow {
        JobRow {
            id: Uuid::new_v4(),
            idem_key: None,
            repo: Some("acme/analytics".into()),
            run_id: Some("hero_break__1".into()),
            task_id: Some("dbt_build_orders".into()),
            node_uid: Some("model.analytics.orders".into()),
            failure_class: Some("schema_drift".into()),
            payload: None,
            state: "done".into(),
            lease_expires_at: None,
            result: Some(serde_json::json!({
                "outcome": "pr_proposed",
                "diff": "--- a/models/marts/orders.sql\n+++ b/models/marts/orders.sql\n@@\n-    customer_id,\n+    cust_id as customer_id,\n",
                "explanation": "Upstream renamed customer_id to cust_id; aliased it back.",
                "transcript": {"kind": "lines", "lines": ["assistant: reading orders.sql", "→ edit_file(...)"]},
                "evidence": {
                    "tier1": {"ran": true, "passed": true, "log": "OK"},
                    "tier2": {"ran": true, "passed": true, "log": "10000 rows"},
                    "output_schema": {"changed": false, "detail": "output columns unchanged: customer_id, order_ts, amount"}
                },
                "confidence": 0.82,
                "risk_class": "low",
                "factors": ["+ compiled", "+ ran on sample", "+ output schema unchanged"]
            })),
            pr_url: None,
            pr_branch: None,
            pr_opened_at: None,
            created_at: Utc::now(),
            updated_at: Utc::now(),
        }
    }

    /// A `journal_dir` no test in this module expects to actually resolve — most
    /// of these tests exercise the `lines` arm or the unknown/legacy-shape
    /// fallbacks, none of which ever touch the filesystem, so any path is fine.
    const NO_JOURNAL: &str = "/nonexistent-in-these-tests";

    #[test]
    fn title_names_class_and_node() {
        let t = render_title(&sample_job());
        assert_eq!(t, "sbflow: auto-fix schema_drift in model.analytics.orders");
    }

    #[tokio::test]
    async fn body_carries_everything_the_reviewer_needs() {
        let b = render_body(&sample_job(), NO_JOURNAL).await;
        // explanation + diff fenced as ```diff
        assert!(b.contains("Upstream renamed customer_id to cust_id"));
        assert!(b.contains("```diff"));
        assert!(b.contains("+    cust_id as customer_id,"));
        // evidence table with honest tiers
        assert!(b.contains("tier-1 compile"));
        assert!(b.contains("✅ passed"));
        assert!(b.contains("output schema"));
        assert!(b.contains("✅ unchanged"));
        // confidence + risk badge
        assert!(b.contains("**Confidence:** 82%"));
        assert!(b.contains("🟢 low"));
        // collapsible transcript
        assert!(b.contains("<details>"));
        assert!(b.contains("🧠 Reasoning transcript"));
        // rollback footer
        assert!(b.contains("Rollback") && b.contains("git revert"));
    }

    #[tokio::test]
    async fn evidence_discloses_unconfigured_sample() {
        let mut job = sample_job();
        job.result = Some(serde_json::json!({
            "outcome": "pr_proposed",
            "evidence": {
                "tier1": {"ran": true, "passed": true, "log": ""},
                "tier2": {"ran": false, "passed": null, "log": ""},
                "output_schema": {"changed": null, "detail": "undetermined"}
            }
        }));
        let b = render_body(&job, NO_JOURNAL).await;
        assert!(b.contains("⚠️ not configured"));
        assert!(b.contains("⚠️ undetermined"));
    }

    // --- RepairResult.transcript, the ADR-0013 discriminated union -----------

    #[tokio::test]
    async fn transcript_lines_arm_renders_the_lines() {
        let t = serde_json::json!({"kind": "lines", "lines": ["a", "b"]});
        let out = render_transcript(Some(&t), NO_JOURNAL).await;
        assert!(out.contains("🧠 Reasoning transcript"));
        assert!(out.contains("a\nb"));
    }

    /// KAN-649: the placeholder ("name the run, point at `satay runs show`") is
    /// gone — the `journal` arm now opens the worker's persisted journal (mounted
    /// at `journal_dir`) and renders its actual events. This test writes a real
    /// fixture journal (`journal::fixtures::write_minimal_journal` — satay's exact
    /// schema, shared with `journal.rs`'s own tests) and asserts the rendered PR
    /// body reflects it, proving the `kind == "journal"` wiring end to end.
    #[tokio::test]
    async fn transcript_journal_arm_renders_the_real_journal() {
        let dir = tempfile::tempdir().unwrap();
        super::super::journal::fixtures::write_minimal_journal(dir.path(), "a3f2").await;

        let t = serde_json::json!({"kind": "journal", "run_id": "a3f2", "ref": "satay.db"});
        let out = render_transcript(Some(&t), dir.path().to_str().unwrap()).await;

        assert!(out.contains("🧠 Reasoning transcript"));
        assert!(out.contains("a3f2"));
        assert!(out.contains("WorkflowCreated"));
        assert!(out.contains("_repair_workflow"));
        assert!(out.contains("WorkflowCompleted"));
        assert!(out.contains("pr_proposed"));
        // The old placeholder text must be gone — this is a render, not a pointer.
        assert!(!out.contains("Replay it locally"));
    }

    /// The journal file legitimately may not exist yet (a fresh worker that has
    /// never run a Satay-backed job) — this must degrade to a readable fallback
    /// inside the collapsible block, never crash `render_body`/the PR-opener poll.
    #[tokio::test]
    async fn transcript_journal_arm_degrades_gracefully_when_unmounted() {
        let dir = tempfile::tempdir().unwrap();
        let t = serde_json::json!({"kind": "journal", "run_id": "missing-run", "ref": "satay.db"});
        let out = render_transcript(Some(&t), dir.path().to_str().unwrap()).await;
        assert!(out.contains("🧠 Reasoning transcript"));
        assert!(out.contains("missing-run"));
        assert!(out.contains("satay runs show missing-run"));
    }

    #[tokio::test]
    async fn transcript_unknown_kind_renders_nothing_rather_than_guessing() {
        // A newer worker emitting a third arm should degrade to a PR with no
        // transcript, never a PR with a mangled one.
        let t = serde_json::json!({"kind": "something_new", "payload": [1, 2]});
        assert_eq!(render_transcript(Some(&t), NO_JOURNAL).await, "");
    }

    #[tokio::test]
    async fn transcript_is_never_sniffed_structurally() {
        // The pre-ADR-0013 shape was a bare array. Untagged input must render
        // nothing: discriminating on `kind` is the contract, and falling back to
        // structural sniffing would quietly re-admit the ambiguity the tag exists
        // to remove.
        let legacy = serde_json::json!(["assistant: hi", "→ edit_file(...)"]);
        assert_eq!(render_transcript(Some(&legacy), NO_JOURNAL).await, "");
        assert_eq!(render_transcript(None, NO_JOURNAL).await, "");
    }

    #[tokio::test]
    async fn transcript_empty_lines_render_nothing() {
        let t = serde_json::json!({"kind": "lines", "lines": []});
        assert_eq!(render_transcript(Some(&t), NO_JOURNAL).await, "");
    }
}
