"""sibei-flow repair worker (V1 walking skeleton).

Stateless Python claim loop over the Postgres job queue (ADR-0002/0009). It
claims in-scope jobs with ``SELECT … FOR UPDATE SKIP LOCKED`` under a lease and
writes a ``no_fix`` result. The agent is stubbed — no LLM (that is V2).
"""

__version__ = "0.1.0"
