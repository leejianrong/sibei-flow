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

    // --- In scope: schema drift (missing / mismatched column or relation) --
    // Adapter-aware phrasings across Postgres / Snowflake / BigQuery. Each is a
    // *confident* drift signal; anything not listed falls through to drop below.
    if
    // Missing column
    e.contains("does not exist") && e.contains("column")   // Postgres: column "x" does not exist
        || e.contains("column \"")                         // Postgres (quoted column)
        || e.contains("undefined column")                  // Postgres (UndefinedColumn sqlstate text)
        || e.contains("no such column")                    // SQLite / Spark / DuckDB
        || e.contains("could not find column")             // generic engines
        || e.contains("invalid identifier")                // Snowflake: invalid identifier 'X'
        || e.contains("unrecognized name")                 // BigQuery: Unrecognized name: x
        || e.contains("not found inside")                  // BigQuery: Name x not found inside y
        // Missing relation / table (upstream renamed or dropped)
        || (e.contains("relation") && e.contains("does not exist"))     // Postgres: relation "x" ...
        || e.contains("not found: table")                  // BigQuery: Not found: Table proj.ds.x
        || e.contains("does not exist or not authorized")  // Snowflake: Object '...' does not exist ...
        // Type / retype mismatch (worker may recommend a prod action on incremental models)
        || e.contains("datatype mismatch")
        || e.contains("cannot cast")
        || e.contains("cannot coerce")                     // BigQuery: Cannot coerce expression ... to type
        || e.contains("but expression is of type")         // Postgres: column x is of type int but ...
        || e.contains("does not match column data type")   // Snowflake: Expression type does not match ...
        // BigQuery: Value of type X cannot be assigned to Y
        || e.contains("cannot be assigned to")
    {
        return Classification::in_scope("schema_drift");
    }

    // --- In scope: code / SQL error ---------------------------------------
    // Postgres: "syntax error at or near" · Snowflake: "syntax error line N at
    // position M" / "unexpected '<x>'" · BigQuery: "Syntax error: Unexpected ..."
    if e.contains("syntax error at or near")
        || e.contains("syntax error")
        || e.contains("compilation error")
        || e.contains("parse error")
        || e.contains("traceback (most recent call")
        || e.contains("syntaxerror")
        || e.contains("unexpected token")
        || e.contains("unexpected keyword")                // Snowflake
        // BigQuery: Syntax error: Unexpected identifier
        || e.contains("unexpected identifier")
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
        let c = classify(
            "SQL compilation error: invalid identifier 'ORDER_TS'",
            "snowflake",
        );
        assert_eq!(c.failure_class, "schema_drift");
    }

    // --- Story 15: expanded drift phrasings across dialects ----------------

    #[test]
    fn bigquery_unrecognized_name_is_schema_drift() {
        let c = classify("Unrecognized name: order_ts at [4:5]", "bigquery");
        assert_eq!(c.failure_class, "schema_drift");
        assert!(c.in_scope);
    }

    #[test]
    fn bigquery_name_not_found_inside_is_schema_drift() {
        let c = classify("Name customer_id not found inside customers", "bigquery");
        assert_eq!(c.failure_class, "schema_drift");
    }

    #[test]
    fn postgres_retype_is_schema_drift() {
        let c = classify(
            r#"column "amount" is of type numeric but expression is of type text"#,
            "postgres",
        );
        assert_eq!(c.failure_class, "schema_drift");
    }

    #[test]
    fn snowflake_type_mismatch_is_schema_drift() {
        let c = classify(
            "Expression type does not match column data type, expecting NUMBER but got VARCHAR",
            "snowflake",
        );
        assert_eq!(c.failure_class, "schema_drift");
    }

    #[test]
    fn bigquery_cannot_coerce_is_schema_drift() {
        let c = classify("Cannot coerce expression amount to type INT64", "bigquery");
        assert_eq!(c.failure_class, "schema_drift");
    }

    #[test]
    fn postgres_missing_relation_is_schema_drift() {
        let c = classify(r#"relation "raw.raw_orders" does not exist"#, "postgres");
        assert_eq!(c.failure_class, "schema_drift");
    }

    #[test]
    fn bigquery_table_not_found_is_schema_drift() {
        let c = classify("Not found: Table my_project.ds.orders", "bigquery");
        assert_eq!(c.failure_class, "schema_drift");
    }

    #[test]
    fn snowflake_object_missing_is_schema_drift() {
        let c = classify(
            "SQL compilation error: Object 'ANALYTICS.PUBLIC.ORDERS' does not exist or not authorized.",
            "snowflake",
        );
        assert_eq!(c.failure_class, "schema_drift");
    }

    #[test]
    fn snowflake_unexpected_keyword_is_code_sql() {
        let c = classify(
            "SQL compilation error: syntax error line 3 at position 7 unexpected keyword 'FROM'",
            "snowflake",
        );
        // The generic "syntax error" also matches; either way it is in-scope code.
        assert_eq!(c.failure_class, "code_sql");
        assert!(c.in_scope);
    }

    // --- Story 15: unknown / out-of-scope stay dropped after expansion -----

    #[test]
    fn representative_out_of_scope_errors_still_drop_after_expansion() {
        // Permission, connection, and arithmetic errors are none of our confident
        // in-scope signals: they must remain out_of_scope and never be dispatched.
        for err in [
            "permission denied for table orders",
            "insufficient privileges to operate on schema analytics",
            "could not connect to server: Connection refused",
            "division by zero",
            "duplicate key value violates unique constraint",
            "deadlock detected",
            "disk quota exceeded",
        ] {
            let c = classify(err, "postgres");
            assert!(
                c.failure_class.starts_with("out_of_scope"),
                "expected out_of_scope for {err:?}, got {}",
                c.failure_class
            );
            assert!(!c.in_scope, "{err:?} must never be dispatched");
        }
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
