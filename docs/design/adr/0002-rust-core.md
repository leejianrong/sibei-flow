# ADR-0002: Rust for the core brain

- **Status:** Accepted
- **Date:** 2026-07-09

## Context

The core "brain" handles durable state, scheduling, the webhook receiver, and
dispatch. It must be fast, reliable, resource-light (single-VM deployable), and
maintainable long-term by a small team. Go and Rust were the realistic
candidates.

## Decision

Write the core brain in **Rust**.

## Consequences

- Strong reliability and performance guarantees; low memory footprint suits the
  "runs light on one VM" goal (ADR-0008).
- The LLM/agent ecosystem is weaker in Rust, so the **repair worker is a
  separate Python component** (ADR-0007); the brain ⇄ worker boundary is a
  first-class interface.
- Slightly slower iteration than Go for a solo dev; accepted for the long-term
  robustness of the core IP.

## Alternatives considered

- **Go** — faster to build, great concurrency, but weaker guarantees; rejected
  in favour of Rust's reliability for the state engine.
