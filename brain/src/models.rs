//! Data contracts and row types.
//!
//! The `Failure` and `RepairResult` shapes are **frozen** here and must stay
//! stable into phase B (see docs/design/V1-plan.md §Contracts).

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::FromRow;
use uuid::Uuid;

/// The normalized failure payload (webhook in). Frozen contract:
/// `{repo, run_id, task_id, node_uid, error_text, adapter, run_results_ref?,
/// source: airflow|dbt|cli}`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Failure {
    pub repo: String,
    pub run_id: String,
    pub task_id: String,
    pub node_uid: String,
    pub error_text: String,
    pub adapter: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub run_results_ref: Option<String>,
    /// One of `airflow | dbt | cli`.
    pub source: String,
}

/// A row of the `repair_jobs` queue table — the durable source of truth.
#[derive(Debug, Clone, FromRow, Serialize)]
pub struct JobRow {
    pub id: Uuid,
    pub idem_key: Option<String>,
    pub repo: Option<String>,
    pub run_id: Option<String>,
    pub task_id: Option<String>,
    pub node_uid: Option<String>,
    pub failure_class: Option<String>,
    pub payload: Option<serde_json::Value>,
    pub state: String,
    pub lease_expires_at: Option<DateTime<Utc>>,
    pub result: Option<serde_json::Value>,
    /// The opened Pull Request URL (github) or offline compare ref (V4). Also
    /// the opener's idempotency guard — non-null means a PR was already opened.
    pub pr_url: Option<String>,
    /// The fix branch pushed for that PR.
    pub pr_branch: Option<String>,
    /// When the opener recorded the PR.
    pub pr_opened_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

impl JobRow {
    /// Pull `result->>'outcome'` for list/detail rendering (nullable until done).
    pub fn outcome(&self) -> Option<String> {
        self.result
            .as_ref()
            .and_then(|r| r.get("outcome"))
            .and_then(|o| o.as_str())
            .map(|s| s.to_string())
    }

    /// Compact summary used by the run-history list (U4).
    pub fn summary(&self) -> serde_json::Value {
        serde_json::json!({
            "id": self.id,
            "repo": self.repo,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "node_uid": self.node_uid,
            "failure_class": self.failure_class,
            "state": self.state,
            "outcome": self.outcome(),
            "pr_url": self.pr_url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        })
    }

    /// Full detail used by the run-detail view (U5) — class, outcome, timing,
    /// plus the normalized payload and raw result.
    pub fn detail(&self) -> serde_json::Value {
        serde_json::json!({
            "id": self.id,
            "idem_key": self.idem_key,
            "repo": self.repo,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "node_uid": self.node_uid,
            "failure_class": self.failure_class,
            "state": self.state,
            "outcome": self.outcome(),
            "lease_expires_at": self.lease_expires_at,
            "payload": self.payload,
            "result": self.result,
            "pr_url": self.pr_url,
            "pr_branch": self.pr_branch,
            "pr_opened_at": self.pr_opened_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        })
    }
}
