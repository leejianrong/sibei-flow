//! Serves the minimal read-only web UI (U4 history + U5 detail).
//!
//! The HTML/JS is embedded at compile time so the binary is self-contained
//! (no static-dir mount needed in the container). The UI has **zero write
//! actions** — it only reads the dashboard API.

use axum::response::Html;

const INDEX_HTML: &str = include_str!("../static/index.html");

/// `GET /` — the single-page read-only dashboard.
pub async fn index() -> Html<&'static str> {
    Html(INDEX_HTML)
}
