# Architecture Decision Records — sibei-flow

Each ADR captures one significant decision: its context, the decision, and
its consequences. Status values: **Proposed · Accepted · Superseded**.

| # | Title | Status |
|---|---|---|
| [0001](0001-control-plane-not-engine.md) | Control plane over own engine; heavy tools as connectors | Accepted |
| [0002](0002-rust-core.md) | Rust for the core brain | Accepted |
| [0003](0003-v1-scope-self-healing-wedge.md) | v1 scope = self-healing wedge (C → B) | Accepted |
| [0004](0004-webhook-first-detection.md) | Webhook-first failure detection | Accepted |
| [0005](0005-git-read-pr-apply-safety.md) | Git-read + PR-based apply; prod-write-never | Accepted |
| [0006](0006-tiered-verification-sandbox.md) | Tiered verification in an ephemeral sandbox | Accepted |
| [0007](0007-llm-strategy-own-loop.md) | Provider-agnostic BYO-key; own Python repair loop | Accepted |
| [0008](0008-pluggable-executor-backends.md) | Pluggable executor backends (local/VM/K8s) | Accepted |
| [0009](0009-state-durability.md) | Postgres-backed, at-least-once state | Accepted |
| [0010](0010-license-open-core.md) | Apache-2.0 core + open-core monetization | Accepted |

All dated 2026-07-09. Decider: solo founder.
