"""V3 verification sandbox (ADR-0006, B-S2).

The worker owns the sandbox (Shape B). A candidate fix is materialized into a
throwaway project directory and verified in an ephemeral, pre-baked Docker
container (`docker run --rm`) in tiers:

- **tier-1** ``dbt compile`` — always; pass iff the node compiles (exit 0).
- **tier-2** ``dbt build --target sample`` — only when a read-only dev/sample
  connection is configured; pass iff the node's ``run_results`` status is
  ``success``. Absence is *disclosed*, not hidden.

The structured evidence (:mod:`.evidence`) and the confidence/risk score
(:mod:`..agent.score`) are derived from what actually ran.
"""

from __future__ import annotations

from .evidence import build_evidence, output_schema_delta
from .runner import SandboxError, SandboxRunner, TierResult

__all__ = [
    "SandboxRunner",
    "SandboxError",
    "TierResult",
    "build_evidence",
    "output_schema_delta",
]
