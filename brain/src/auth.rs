//! Bearer-token gate for the read-only dashboard API (SIB-87).
//!
//! `GET /api/runs` and `GET /api/runs/:id` expose diffs, evidence, transcripts
//! and cost, so they require `Authorization: Bearer <SBFLOW_DASHBOARD_TOKEN>`
//! whenever a token is configured. `/` (the static HTML shell, which contains no
//! run data) and `/healthz` stay open: a browser cannot attach a header to a
//! page load, so the shell prompts for the token and sends it on its own API
//! calls. `POST /webhook` has its own HMAC scheme (KAN-926) and is unaffected.

use axum::{
    extract::{Request, State},
    http::{header, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
};
use sha2::{Digest, Sha256};

use crate::AppState;

/// Constant-time token comparison: hash both sides to a fixed length, then
/// fold the byte differences, so neither the length nor the matching prefix of
/// the expected token leaks through timing.
pub fn token_matches(expected: &str, presented: &str) -> bool {
    let a = Sha256::digest(expected.as_bytes());
    let b = Sha256::digest(presented.as_bytes());
    a.iter()
        .zip(b.iter())
        .fold(0u8, |acc, (x, y)| acc | (x ^ y))
        == 0
}

/// axum middleware: reject with 401 unless the request carries the configured
/// bearer token. A no-op when no token is configured (loopback dev use; the
/// startup guard in `config::require_dashboard_auth` refuses that combination
/// on a non-loopback bind unless explicitly allowed).
pub async fn require_dashboard_token(
    State(state): State<AppState>,
    req: Request,
    next: Next,
) -> Response {
    if let Some(expected) = &state.dashboard_token {
        let presented = req
            .headers()
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.strip_prefix("Bearer "));
        let ok = presented.is_some_and(|t| token_matches(expected, t));
        if !ok {
            return (
                StatusCode::UNAUTHORIZED,
                [(header::WWW_AUTHENTICATE, "Bearer")],
                "unauthorized: missing or invalid dashboard token",
            )
                .into_response();
        }
    }
    next.run(req).await
}

#[cfg(test)]
mod tests {
    use super::token_matches;

    #[test]
    fn equal_tokens_match() {
        assert!(token_matches("s3cret", "s3cret"));
    }

    #[test]
    fn different_tokens_do_not_match() {
        assert!(!token_matches("s3cret", "s3creT"));
        assert!(!token_matches("s3cret", "s3cret "));
        assert!(!token_matches("s3cret", ""));
    }
}
