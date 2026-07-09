# ADR-0010: Apache-2.0 core + open-core monetization

- **Status:** Accepted
- **Date:** 2026-07-09

## Context

License choice constrains both adoption and the future business. A solo founder
chasing adoption and funding needs the strongest "safe to build on" signal now,
while preserving a path to revenue.

## Decision

- License the **core under Apache-2.0**.
- Monetize via an **open-core enterprise tier** (self-hosted, customer-operated)
  as the plan of record: gate **SSO/SAML, RBAC, multi-tenancy, audit logging,
  multi-cluster sync, compliance, and priority support**. The self-healing core
  and basic orchestration stay free.
- **Managed cloud** is a **post-funding, phase-2** motion (ops burden is too
  high for a solo founder now).
- v1 ships fully free/OSS; monetization only *informs boundaries* today (design
  gate-able features cleanly; don't give away the enterprise seams).

## Consequences

- Maximum adoption and ecosystem trust; no license friction pre-traction.
- Enterprise features (esp. RBAC/multi-tenancy, deferred in ADR — single-tenant
  v1) must be architected so they can be cleanly gated later.
- Apache-2.0 permits third-party hosting; accepted risk pre-traction, revisited
  if/when a managed offering exists.

## Alternatives considered

- **BSL/SSPL source-available** — rejected for v1: adds adoption friction before
  there's a business to protect.
- **Managed-cloud-first** — rejected: unsustainable ops load for a solo founder.
