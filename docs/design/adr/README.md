# Architecture Decision Records — sibei-flow

Each ADR captures one significant decision: its context, the decision, and
its consequences. Status values: **Proposed · Accepted · Superseded**.

| # | Title | Status |
|---|---|---|
| [0001](0001-control-plane-not-engine.md) | Control plane over own engine; heavy tools as connectors | Accepted — engine clause amended by 0012 |
| [0002](0002-rust-core.md) | Rust for the core brain | Accepted |
| [0003](0003-v1-scope-self-healing-wedge.md) | v1 scope = self-healing wedge (C → B) | Accepted |
| [0004](0004-webhook-first-detection.md) | Webhook-first failure detection | Accepted |
| [0005](0005-git-read-pr-apply-safety.md) | Git-read + PR-based apply; prod-write-never | Accepted — extended to hosting by 0014 |
| [0006](0006-tiered-verification-sandbox.md) | Tiered verification in an ephemeral sandbox | Accepted |
| [0007](0007-llm-strategy-own-loop.md) | Provider-agnostic BYO-key; own Python repair loop | Accepted |
| [0008](0008-pluggable-executor-backends.md) | Pluggable executor backends (local/VM/K8s) | Accepted — seam only, unused |
| [0009](0009-state-durability.md) | Postgres-backed, at-least-once state | Accepted — bounded by 0012 |
| [0010](0010-license-open-core.md) | Apache-2.0 core + open-core monetization | Accepted — hosted tier bounded by 0014 |
| [0011](0011-pluggable-git-host.md) | Pluggable git-host seam (`offline` default / `github`) | Accepted |
| [0012](0012-no-second-execution-engine.md) | No second execution engine — Satay is the engine of record | Accepted |
| [0013](0013-transcript-tagged-union.md) | `RepairResult.transcript` becomes a tagged union | Accepted |
| [0014](0014-r61-constrains-hosting.md) | R6.1 constrains hosting; hosting is not an exemption | Accepted |

0001–0010 are dated 2026-07-09; 0011 landed during V4. 0012–0014 are dated
2026-08-05 and record the Satay convergence decision. Decider throughout: solo
founder.

## The direction, in one paragraph

sibei-flow and **Satay Runtime** are two independent products that share one
execution engine and, eventually, one hosted plane. sibei-flow stays the
self-healing layer for data pipelines and never grows a general orchestrator
(0012). Satay owns durable execution, replay and the journal. The first coupling
is the transcript (0013); the port of the repair loop waits for a capability that
actually needs it (0012, decision 4). Hosting, when it comes, must preserve R6.1,
which forces the two hosted products onto one shared plane (0014).
