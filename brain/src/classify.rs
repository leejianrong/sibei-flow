//! Thin, deterministic failure classifier (N2 / B-S1).
//!
//! No LLM. Adapter-aware error-text patterns map a `Failure` to one of the two
//! in-scope classes (`schema_drift`, `code_sql`) or an out-of-scope reason.
//! The **safe default is drop**: anything unmatched is `out_of_scope:unknown`
//! (R2.4 — "act only where competent"). Pattern coverage grows in V5.

/// Result of classification.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Classification {
    /// Stored in `failure_class`: `schema_drift` | `code_sql` |
    /// `out_of_scope:<reason>`.
    pub failure_class: String,
    /// True only for the two acted-on classes; false ⇒ recorded, not dispatched.
    pub in_scope: bool,
}

impl Classification {
    fn in_scope(class: &str) -> Self {
        Self {
            failure_class: class.to_string(),
            in_scope: true,
        }
    }
    fn dropped(reason: &str) -> Self {
        Self {
            failure_class: format!("out_of_scope:{reason}"),
            in_scope: false,
        }
    }
}

/// Classify a failure from its error text (adapter-aware).
///
/// Precedence: definitive out-of-scope signals (OOM / timeout / data-quality)
/// are checked first so they are never mistaken for an in-scope class; then the
/// two in-scope classes; then `unknown → drop`.
pub fn classify(error_text: &str, _adapter: &str) -> Classification {
    let e = error_text.to_lowercase();

    // --- Out of scope: resource / OOM -------------------------------------
    if e.contains("oomkilled")
        || e.contains("out of memory")
        || e.contains("memoryerror")
        || e.contains("exit code 137")
        || e.contains("signal 9")
        || e.contains("killed")
    {
        return Classification::dropped("oom");
    }

    // --- Out of scope: timeout / cancellation (config, not healing) -------
    if e.contains("statement timeout")
        || e.contains("canceling statement due to statement timeout")
        || e.contains("timed out")
        || e.contains("timeout")
        || e.contains("deadline exceeded")
    {
        return Classification::dropped("timeout");
    }

    // --- Out of scope: data-quality (a dbt test failed) -------------------
    if e.contains("failure in test")
        || e.contains("data quality")
        || e.contains("got ") && e.contains("results, configured to fail")
    {
        return Classification::dropped("data_quality");
    }

    // --- In scope: schema drift (missing / mismatched column) -------------
    // Postgres: column "x" does not exist · Snowflake: invalid identifier
    // BigQuery: Unrecognized name · plus type/nullable mismatches.
    if e.contains("does not exist") && e.contains("column")
        || e.contains("column \"")
        || e.contains("invalid identifier")
        || e.contains("unrecognized name")
        || e.contains("undefined column")
        || e.contains("no such column")
        || e.contains("datatype mismatch")
        || e.contains("cannot cast")
    {
        return Classification::in_scope("schema_drift");
    }

    // --- In scope: code / SQL error ---------------------------------------
    if e.contains("syntax error at or near")
        || e.contains("syntax error")
        || e.contains("compilation error")
        || e.contains("parse error")
        || e.contains("traceback (most recent call")
        || e.contains("syntaxerror")
        || e.contains("unexpected token")
    {
        return Classification::in_scope("code_sql");
    }

    // --- Default: unknown → drop, never guess (R2.4) ----------------------
    Classification::dropped("unknown")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn postgres_missing_column_is_schema_drift() {
        let c = classify(r#"column "customer_id" does not exist"#, "postgres");
        assert_eq!(c.failure_class, "schema_drift");
        assert!(c.in_scope);
    }

    #[test]
    fn snowflake_invalid_identifier_is_schema_drift() {
        let c = classify("SQL compilation error: invalid identifier 'ORDER_TS'", "snowflake");
        assert_eq!(c.failure_class, "schema_drift");
    }

    #[test]
    fn syntax_error_is_code_sql() {
        let c = classify("syntax error at or near \"select\"", "postgres");
        assert_eq!(c.failure_class, "code_sql");
        assert!(c.in_scope);
    }

    #[test]
    fn timeout_is_dropped() {
        let c = classify("canceling statement due to statement timeout", "postgres");
        assert_eq!(c.failure_class, "out_of_scope:timeout");
        assert!(!c.in_scope);
    }

    #[test]
    fn oom_is_dropped() {
        let c = classify("Task exited with exit code 137 (OOMKilled)", "postgres");
        assert_eq!(c.failure_class, "out_of_scope:oom");
    }

    #[test]
    fn unknown_is_dropped_not_guessed() {
        let c = classify("some totally novel error we have never seen", "postgres");
        assert_eq!(c.failure_class, "out_of_scope:unknown");
        assert!(!c.in_scope);
    }
}
