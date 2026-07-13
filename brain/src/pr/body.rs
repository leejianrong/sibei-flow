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
pub fn render_body(job: &JobRow) -> String {
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

    if let Some(transcript) = get("transcript").and_then(Value::as_array) {
        if !transcript.is_empty() {
            let joined = transcript
                .iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join("\n");
            s.push_str("<details>\n<summary>🧠 Reasoning transcript</summary>\n\n");
            s.push_str("```\n");
            s.push_str(joined.trim_end());
            s.push_str("\n```\n\n</details>\n\n");
        }
    }

    s.push_str("---\n");
    s.push_str(
        "**Rollback** = `git revert` this PR's merge commit. sibei-flow holds no \
         prod-write credentials; the only write it ever performs is opening this \
         branch + PR (ADR-0005).\n",
    );
    s
}

/// The verification evidence table (honest disclosure of what actually ran).
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
                "transcript": ["assistant: reading orders.sql", "→ edit_file(...)"],
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

    #[test]
    fn title_names_class_and_node() {
        let t = render_title(&sample_job());
        assert_eq!(t, "sbflow: auto-fix schema_drift in model.analytics.orders");
    }

    #[test]
    fn body_carries_everything_the_reviewer_needs() {
        let b = render_body(&sample_job());
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

    #[test]
    fn evidence_discloses_unconfigured_sample() {
        let mut job = sample_job();
        job.result = Some(serde_json::json!({
            "outcome": "pr_proposed",
            "evidence": {
                "tier1": {"ran": true, "passed": true, "log": ""},
                "tier2": {"ran": false, "passed": null, "log": ""},
                "output_schema": {"changed": null, "detail": "undetermined"}
            }
        }));
        let b = render_body(&job);
        assert!(b.contains("⚠️ not configured"));
        assert!(b.contains("⚠️ undetermined"));
    }
}
