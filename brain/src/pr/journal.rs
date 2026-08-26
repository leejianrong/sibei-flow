//! KAN-649 — reads a Satay journal to render `RepairResult.transcript`'s `journal`
//! arm (ADR-0013), replacing `body.rs`'s old placeholder.
//!
//! ADR-0012 decision 3: "sibei-flow may depend on *reading* a Satay journal before
//! it depends on Satay *driving* its execution." This module is exactly that
//! reader — nothing here drives, resumes, or replays a run; it only opens the
//! SQLite file the worker's `agent/satay_loop.py` persisted (KAN-649,
//! `Config.satay_journal_dir`, mounted read-only into this container at the SAME
//! path via the `satay-journal` docker volume — see `docker-compose.yml`) and
//! renders its `events` rows for one `run_id` as readable text.
//!
//! **Deliberately not attempted here** (documented, not silently skipped):
//! - Full `rehydrate()`-style typed reconstruction (satay's own
//!   `satay.journal.codec.rehydrate`, which turns a decoded value back into the
//!   worker's own dataclass/Pydantic/enum Python types using the task's return
//!   annotation). This module only mirrors `decode()` — strip the `$satay` tag
//!   envelope back to a plain, renderable JSON value — which is everything a
//!   *reader* needs and does not require knowing the worker's Python type graph
//!   from Rust.
//! - Satay's own schema migrations (`satay.journal.store.SQLiteStore._migrate`).
//!   This reader only ever issues a `SELECT` against `events`; it never writes,
//!   so it never needs to bring an older on-disk schema forward. If a future
//!   satay schema version renames/removes a column this reader depends on, the
//!   graceful-fallback path (`render`'s `Err` arm) is what a reviewer sees,
//!   not a poller crash.

use std::path::Path;

use serde_json::Value;
use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};
use sqlx::Row;

/// The discriminator key satay's codec tags a non-JSON-native value with
/// (`satay.journal.codec.TAG_KEY`).
const TAG_KEY: &str = "$satay";

/// Fallback journal filename when a `RepairResult.transcript.ref` is somehow
/// absent (defensive — the worker always sets it; see `satay_loop.py`).
const DEFAULT_REF: &str = "satay.db";

/// Cap on how much of one decoded value's rendered JSON is inlined in the PR
/// body. **Not** the same problem `_TRANSCRIPT_CLIP` solved on the `lines` arm
/// (ADR-0013's "lossy by construction" complaint) — the full value stays intact
/// in the journal file, retrievable with `satay runs show <run_id>` against the
/// same mounted volume; only this Markdown excerpt is capped, because an inline
/// journal value can be up to 256 KiB (satay's own blob-spill threshold) and
/// GitHub caps a whole PR body at 65536 characters.
const RENDER_CLIP: usize = 1500;

/// Render `run_id`'s events from `{journal_dir}/{journal_ref}` as a readable
/// timeline, or a graceful fallback string naming the problem (missing file,
/// missing run, an unreadable row). Never panics and never propagates an error —
/// one bad journal read degrades this one PR section, not the whole PR-opener
/// poll (`pr/mod.rs::poll_once` already treats an `open_for_job` failure as
/// retryable, and a rendering hiccup here must not manufacture one).
pub async fn render(journal_dir: &str, run_id: &str, journal_ref: &str) -> String {
    let journal_ref = if journal_ref.is_empty() {
        DEFAULT_REF
    } else {
        journal_ref
    };
    let db_path = Path::new(journal_dir).join(journal_ref);
    match render_inner(&db_path, run_id).await {
        Ok(Some(text)) => text,
        Ok(None) => format!(
            "Satay run `{run_id}` has no events readable in `{journal_ref}` yet \
             (the journal may not have synced, or this container does not have \
             it mounted). Inspect it directly with `satay runs show {run_id}`."
        ),
        Err(e) => format!(
            "Satay run `{run_id}` was recorded, but the journal at `{journal_ref}` \
             could not be read here ({e}). Inspect it directly with \
             `satay runs show {run_id}`."
        ),
    }
}

async fn render_inner(db_path: &Path, run_id: &str) -> Result<Option<String>, sqlx::Error> {
    if !db_path.exists() {
        return Ok(None);
    }
    let opts = SqliteConnectOptions::new()
        .filename(db_path)
        .read_only(true);
    // One short-lived, single-connection pool per render rather than a
    // long-lived shared pool: the journal file may not exist yet at brain
    // startup (the worker creates it lazily, on its first Satay-backed job), and
    // the PR opener renders at most a handful of jobs per ~2s poll tick — SQLite
    // open is cheap, and WAL mode (which the worker's `SQLiteStore.open` already
    // turns on) lets this short reader connection coexist with the worker's own
    // writer without contention. A persistent pool would save a bit of open/close
    // overhead but adds lifecycle complexity (reconnect-on-file-appears,
    // reconnect-on-file-replaced) for a marginal gain at this call volume.
    let pool = SqlitePoolOptions::new()
        .max_connections(1)
        .connect_with(opts)
        .await?;

    let rows =
        sqlx::query("SELECT seq, type, ts, payload_json FROM events WHERE run_id = ? ORDER BY seq")
            .bind(run_id)
            .fetch_all(&pool)
            .await?;
    pool.close().await;

    if rows.is_empty() {
        return Ok(None);
    }

    let blobs_dir = db_path.parent().map(|p| p.join("blobs"));
    let mut lines = vec![format!("Satay run `{run_id}` — {} event(s)", rows.len())];
    for row in &rows {
        let seq: i64 = row.try_get("seq")?;
        let event_type: String = row.try_get("type")?;
        let ts: String = row.try_get("ts")?;
        let payload_json: String = row.try_get("payload_json")?;
        let payload: Value = serde_json::from_str(&payload_json).unwrap_or(Value::Null);
        let summary = summarize(&event_type, &payload, blobs_dir.as_deref());
        let mut line = format!("{seq:>3}  {ts}  {event_type}");
        if !summary.is_empty() {
            line.push_str("  ");
            line.push_str(&summary);
        }
        lines.push(line);
    }
    Ok(Some(lines.join("\n")))
}

/// Render the key payload fields for one event, mirroring the spirit (not the
/// exact text) of satay's own `journal.timeline._summarise_payload` — which
/// event types get a compact identity/attempt/error line, we follow directly, and
/// then go further where the card asks for it: decoded task input/output.
fn summarize(event_type: &str, payload: &Value, blobs_dir: Option<&Path>) -> String {
    match event_type {
        "WorkflowCreated" => format!(
            "workflow={} code_version={}",
            str_field(payload, "workflow_name"),
            str_field(payload, "code_version"),
        ),
        "ChildWorkflowScheduled" => format!(
            "child={} run_id={}",
            str_field(payload, "workflow_name"),
            str_field(payload, "child_run_id"),
        ),
        "TaskScheduled" => {
            let mut parts = vec![
                format!("task={}", str_field(payload, "task_name")),
                call_identity(payload),
            ];
            if let Some(input) = payload.get("input_ref") {
                parts.push(format!("input={}", render_value(input, blobs_dir)));
            }
            parts.join(" ")
        }
        "TaskAttemptStarted" => format!(
            "task={} {} attempt={}",
            str_field(payload, "task_name"),
            call_identity(payload),
            int_field(payload, "attempt"),
        ),
        "TaskAttemptFailed" => format!(
            "task={} {} attempt={} error={}",
            str_field(payload, "task_name"),
            call_identity(payload),
            int_field(payload, "attempt"),
            error_summary(payload),
        ),
        "TaskCompleted" => {
            let mut parts = vec![
                format!("task={}", str_field(payload, "task_name")),
                call_identity(payload),
            ];
            if let Some(output) = payload.get("output_ref") {
                parts.push(format!("output={}", render_value(output, blobs_dir)));
            }
            parts.join(" ")
        }
        "TaskFailed" => format!(
            "task={} {} error={}",
            str_field(payload, "task_name"),
            call_identity(payload),
            error_summary(payload),
        ),
        "WorkflowFailed" => format!("error={}", error_summary(payload)),
        "WorkflowCompleted" => payload
            .get("output_ref")
            .map(|v| format!("outcome={}", render_value(v, blobs_dir)))
            .unwrap_or_default(),
        _ => String::new(),
    }
}

/// The durable-call identity a task-lifecycle event payload carries — a keyed
/// fan-out item by its `key` (`satay.map`/keyed `start_child` items, including
/// this worker's own per-candidate children), else by `ordinal` (mirrors
/// `satay.journal.identity.CallIdentity.payload_fields`).
fn call_identity(payload: &Value) -> String {
    match payload.get("key").and_then(Value::as_str) {
        Some(key) => format!("key={key}"),
        None => format!("ordinal={}", int_field(payload, "ordinal")),
    }
}

fn str_field(payload: &Value, field: &str) -> String {
    payload
        .get(field)
        .and_then(Value::as_str)
        .unwrap_or("?")
        .to_string()
}

fn int_field(payload: &Value, field: &str) -> String {
    payload
        .get(field)
        .map(|v| v.to_string())
        .unwrap_or_else(|| "?".to_string())
}

fn error_summary(payload: &Value) -> String {
    let error = payload.get("error");
    let ty = error
        .and_then(|e| e.get("type"))
        .and_then(Value::as_str)
        .unwrap_or("Error");
    let message = error
        .and_then(|e| e.get("message"))
        .and_then(Value::as_str)
        .unwrap_or("");
    format!("{ty}: {message}")
}

/// Decode a satay-codec value (stripping its `$satay` tag envelope, resolving a
/// spilled blob reference) and render it as clipped, compact JSON.
fn render_value(v: &Value, blobs_dir: Option<&Path>) -> String {
    let decoded = decode_value(v, blobs_dir);
    let rendered = serde_json::to_string(&decoded).unwrap_or_else(|_| "<unrenderable>".into());
    clip(&rendered)
}

fn clip(rendered: &str) -> String {
    if rendered.chars().count() <= RENDER_CLIP {
        return rendered.to_string();
    }
    let clipped: String = rendered.chars().take(RENDER_CLIP).collect();
    format!("{clipped} …[clipped for the PR body — the full value is in the journal]")
}

/// Mirror of `satay.journal.codec.decode`, minus `rehydrate`'s typed
/// reconstruction (see the module doc comment for why that is out of scope), plus
/// blob-reference resolution (`satay.blobs.rehydrate_encoded`'s job on the Python
/// read path, folded in here since this reader has no separate decode/rehydrate
/// split to preserve).
fn decode_value(v: &Value, blobs_dir: Option<&Path>) -> Value {
    match v {
        Value::Array(items) => {
            Value::Array(items.iter().map(|i| decode_value(i, blobs_dir)).collect())
        }
        Value::Object(map) => match map.get(TAG_KEY).and_then(Value::as_str) {
            Some("datetime") | Some("timedelta") => map.get("v").cloned().unwrap_or(Value::Null),
            Some("enum") => map
                .get("v")
                .map(|v| decode_value(v, blobs_dir))
                .unwrap_or(Value::Null),
            Some("dataclass") | Some("model") => {
                let type_name = map.get("type").and_then(Value::as_str).unwrap_or("?");
                let fields = decode_value(map.get("fields").unwrap_or(&Value::Null), blobs_dir);
                let mut obj = serde_json::Map::new();
                obj.insert("$type".to_string(), Value::String(type_name.to_string()));
                if let Value::Object(field_map) = fields {
                    obj.extend(field_map);
                }
                Value::Object(obj)
            }
            Some("blobref") => resolve_blob(map, blobs_dir),
            _ => Value::Object(
                map.iter()
                    .map(|(k, v)| (k.clone(), decode_value(v, blobs_dir)))
                    .collect(),
            ),
        },
        other => other.clone(),
    }
}

/// Resolve a spilled-payload blob reference (`satay.blobs.make_blob_ref`'s
/// `{"$satay":"blobref","id":<sha256>,"size":<bytes>}`) by reading
/// `{blobs_dir}/{id}.blob` — a content-addressed file, so a straightforward local
/// read, not a client library. Degrades to a labelled placeholder (never panics,
/// never propagates an I/O error up) when the directory is unmounted, the blob is
/// missing, or its content is not valid JSON.
fn resolve_blob(map: &serde_json::Map<String, Value>, blobs_dir: Option<&Path>) -> Value {
    let id = map.get("id").and_then(Value::as_str).unwrap_or("?");
    let size = map.get("size").and_then(Value::as_i64).unwrap_or(0);
    let Some(dir) = blobs_dir else {
        return Value::String(format!(
            "<large value stored separately — {size} bytes, blob {id}, no blob directory mounted>"
        ));
    };
    let blob_path = dir.join(format!("{id}.blob"));
    match std::fs::read(&blob_path) {
        Ok(bytes) => match serde_json::from_slice::<Value>(&bytes) {
            Ok(inner) => decode_value(&inner, blobs_dir),
            Err(_) => Value::String(format!(
                "<large value stored separately — {size} bytes, blob {id}, unreadable content>"
            )),
        },
        Err(_) => Value::String(format!(
            "<large value stored separately — {size} bytes, blob {id}, not found>"
        )),
    }
}

/// Test-only fixture builder, reused by `body.rs`'s own tests to prove the
/// `journal` transcript arm's *wiring* (kind routes to this module, output lands
/// inside the collapsible block) without duplicating schema-construction code —
/// this module's own `tests` below cover *rendering correctness* in depth.
#[cfg(test)]
pub(crate) mod fixtures {
    use sqlx::sqlite::SqliteConnectOptions;
    use sqlx::{Executor, SqlitePool};
    use std::str::FromStr;

    /// Write a minimal but real journal (satay's exact `runs`/`events` schema) at
    /// `{dir}/satay.db` with one `WorkflowCreated` + one `WorkflowCompleted` event
    /// for `run_id`.
    pub(crate) async fn write_minimal_journal(dir: &std::path::Path, run_id: &str) {
        let db_path = dir.join("satay.db");
        let opts = SqliteConnectOptions::from_str(&format!("sqlite://{}", db_path.display()))
            .unwrap()
            .create_if_missing(true);
        let pool = SqlitePool::connect_with(opts).await.unwrap();
        pool.execute(
            r#"
            CREATE TABLE events (
                run_id       TEXT NOT NULL,
                seq          INTEGER NOT NULL,
                event_id     TEXT NOT NULL UNIQUE,
                type         TEXT NOT NULL,
                ts           TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, seq)
            );
            "#,
        )
        .await
        .unwrap();
        sqlx::query(
            "INSERT INTO events VALUES (?, 1, ?, 'WorkflowCreated', '2026-08-26T00:00:00+00:00', \
             '{\"workflow_name\":\"_repair_workflow\",\"code_version\":\"abc\"}')",
        )
        .bind(run_id)
        .bind(format!("{run_id}-1"))
        .execute(&pool)
        .await
        .unwrap();
        sqlx::query(
            "INSERT INTO events VALUES (?, 2, ?, 'WorkflowCompleted', '2026-08-26T00:00:01+00:00', \
             '{\"output_ref\":{\"outcome\":\"pr_proposed\"}}')",
        )
        .bind(run_id)
        .bind(format!("{run_id}-2"))
        .execute(&pool)
        .await
        .unwrap();
        pool.close().await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use sqlx::sqlite::SqliteConnectOptions;
    use sqlx::{Executor, SqlitePool};
    use std::str::FromStr;

    /// Build a small fixture journal by hand-constructing satay's exact schema
    /// (`satay.journal.store::_SCHEMA`) and inserting a few representative rows,
    /// rather than checking in a binary SQLite fixture generated by shelling out
    /// to the worker's Python venv. Chosen because: (1) `make test-brain` is a
    /// Rust-only lane (throwaway Postgres, no Python) — a binary fixture would
    /// either need regenerating by hand whenever satay's schema changes (opaque,
    /// easy to go stale unnoticed) or would make brain's test suite depend on a
    /// working worker venv, which is a layering violation this repo's test
    /// layering (`CLAUDE.md`) doesn't otherwise have; (2) a hand-built fixture is
    /// self-documenting in code review — the exact rows under test are visible as
    /// Rust literals, not a diff of a `.db` file's bytes.
    async fn fixture_pool(path: &std::path::Path) -> SqlitePool {
        let opts = SqliteConnectOptions::from_str(&format!("sqlite://{}", path.display()))
            .unwrap()
            .create_if_missing(true);
        let pool = SqlitePool::connect_with(opts).await.unwrap();
        pool.execute(
            r#"
            CREATE TABLE runs (
                run_id          TEXT PRIMARY KEY,
                workflow_name   TEXT NOT NULL,
                status          TEXT NOT NULL,
                code_version    TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                idempotency_key TEXT
            );
            CREATE TABLE events (
                run_id       TEXT NOT NULL,
                seq          INTEGER NOT NULL,
                event_id     TEXT NOT NULL UNIQUE,
                type         TEXT NOT NULL,
                ts           TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, seq)
            );
            "#,
        )
        .await
        .unwrap();
        pool
    }

    async fn insert_event(
        pool: &SqlitePool,
        run_id: &str,
        seq: i64,
        event_type: &str,
        payload_json: &str,
    ) {
        sqlx::query(
            "INSERT INTO events (run_id, seq, event_id, type, ts, payload_json) \
             VALUES (?, ?, ?, ?, '2026-08-26T00:00:00+00:00', ?)",
        )
        .bind(run_id)
        .bind(seq)
        .bind(format!("{run_id}-{seq}"))
        .bind(event_type)
        .bind(payload_json)
        .execute(pool)
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn render_unknown_run_degrades_gracefully() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("satay.db");
        let pool = fixture_pool(&db_path).await;
        pool.close().await;

        let out = render(dir.path().to_str().unwrap(), "does-not-exist", "satay.db").await;
        assert!(out.contains("does-not-exist"));
        assert!(out.contains("no events readable"));
    }

    #[tokio::test]
    async fn render_missing_file_degrades_gracefully() {
        let dir = tempfile::tempdir().unwrap();
        let out = render(dir.path().to_str().unwrap(), "some-run", "satay.db").await;
        assert!(out.contains("some-run"));
        assert!(out.contains("no events readable"));
    }

    #[tokio::test]
    async fn render_a_real_journal_shows_turns_tools_and_terminal_status() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("satay.db");
        let pool = fixture_pool(&db_path).await;

        let run_id = "r1";
        insert_event(
            &pool,
            run_id,
            1,
            "WorkflowCreated",
            r#"{"workflow_name":"_repair_workflow","input_ref":{"task":"x"},"code_version":"abc123"}"#,
        )
        .await;
        insert_event(
            &pool,
            run_id,
            2,
            "TaskScheduled",
            r#"{"task_name":"_dispatch","ordinal":0,"input_ref":{"$satay":"dataclass","type":"sbflow_worker.llm.base.ToolCall","fields":{"name":"edit_file","input":{"path":"models/orders.sql"},"id":"call_1"}}}"#,
        )
        .await;
        insert_event(
            &pool,
            run_id,
            3,
            "TaskAttemptStarted",
            r#"{"task_name":"_dispatch","ordinal":0,"attempt":1}"#,
        )
        .await;
        insert_event(
            &pool,
            run_id,
            4,
            "TaskAttemptFailed",
            r#"{"task_name":"_dispatch","ordinal":0,"attempt":1,"error":{"type":"RuntimeError","message":"boom"},"next_delay":0.1}"#,
        )
        .await;
        insert_event(
            &pool,
            run_id,
            5,
            "TaskCompleted",
            r#"{"task_name":"_dispatch","ordinal":0,"output_ref":{"content":"edited models/orders.sql","is_error":false}}"#,
        )
        .await;
        insert_event(
            &pool,
            run_id,
            6,
            "WorkflowCompleted",
            r#"{"output_ref":{"outcome":"pr_proposed","diff":"--- a\n+++ b\n"}}"#,
        )
        .await;
        pool.close().await;

        let out = render(dir.path().to_str().unwrap(), run_id, "satay.db").await;

        assert!(out.contains("Satay run `r1` — 6 event(s)"));
        assert!(out.contains("WorkflowCreated"));
        assert!(out.contains("workflow=_repair_workflow"));
        assert!(out.contains("task=_dispatch"));
        assert!(out.contains("attempt=1"));
        assert!(out.contains("error=RuntimeError: boom"));
        assert!(out.contains("edit_file"));
        assert!(out.contains("edited models/orders.sql"));
        assert!(out.contains("pr_proposed"));
    }

    #[tokio::test]
    async fn render_resolves_a_spilled_blob_from_the_sibling_blobs_dir() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("satay.db");
        let pool = fixture_pool(&db_path).await;

        let blobs_dir = dir.path().join("blobs");
        std::fs::create_dir_all(&blobs_dir).unwrap();
        let blob_content = r#"{"schema":["order_id","customer_id","amount"]}"#;
        std::fs::write(blobs_dir.join("deadbeef.blob"), blob_content).unwrap();

        let run_id = "r2";
        insert_event(
            &pool,
            run_id,
            1,
            "TaskCompleted",
            r#"{"task_name":"_dispatch","ordinal":0,"output_ref":{"$satay":"blobref","id":"deadbeef","size":9999999}}"#,
        )
        .await;
        pool.close().await;

        let out = render(dir.path().to_str().unwrap(), run_id, "satay.db").await;
        assert!(out.contains("order_id"));
        assert!(out.contains("customer_id"));
    }

    #[tokio::test]
    async fn render_missing_blob_degrades_to_a_labelled_placeholder_not_a_crash() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("satay.db");
        let pool = fixture_pool(&db_path).await;

        let run_id = "r3";
        insert_event(
            &pool,
            run_id,
            1,
            "TaskCompleted",
            r#"{"task_name":"_dispatch","ordinal":0,"output_ref":{"$satay":"blobref","id":"missing","size":300000}}"#,
        )
        .await;
        pool.close().await;

        let out = render(dir.path().to_str().unwrap(), run_id, "satay.db").await;
        assert!(out.contains("large value stored separately"));
        assert!(out.contains("not found"));
    }
}
