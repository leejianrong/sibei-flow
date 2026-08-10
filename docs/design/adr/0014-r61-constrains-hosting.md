# ADR-0014: R6.1 constrains hosting; hosting is not an exemption from it

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** Jian (leejianrong2@gmail.com)

Bounds [ADR-0010](0010-license-open-core.md) (monetization) and preserves
[ADR-0005](0005-git-read-pr-apply-safety.md)'s hard invariant against a future
hosted product. Both remain Accepted.

## Context

A hosted sibei-flow is on the roadmap. It is also the clearer sale of the two
Abang products: a data team will pay for an AI SRE well before a developer pays to
host workflow journals.

The obvious hosted shape would have us hold, **per tenant**: a PR-scoped git
token, warehouse **read** credentials (needed by `get_schema`), a dev-schema
**write** credential for tier-2 `dbt build --sample`, and the customer's dbt
project executing on our infrastructure. R6.1 as written forbids *prod-write*
credentials, and a PR-scoped token is not one, so nothing in the invariant's
letter stops any of that.

But warehouse read access is the customer's data sitting inside our perimeter, and
it converts the product's central trust claim into a security questionnaire on
every deal. For the stated ICP — a 3-to-15-person data team with no platform group
— warehouse access is the scariest item on that list, and they know it.

The real risk is not that someone decides to weaken R6.1. Nobody will write a card
that says that. What happens is that a credential store becomes pluggable "for the
hosted case", or the invariant gets restated as *"holds no prod-write credentials
**in self-hosted mode**"*. That qualifier is how an invariant dies, and it dies
invisibly, six months from now, in a PR that looks reasonable.

## Decision

**R6.1 is not exempted by hosting. R6.1 is a constraint on what a hosted
sibei-flow may be.**

Concretely, any hosted sibei-flow:

- **holds no warehouse credentials of any tier**, read or dev-write
- **does not execute customer dbt projects** on our infrastructure
- keeps **repair execution on the customer's own runner** — the agent loop and the
  ephemeral sandbox both stay behind their perimeter
- receives only the run's **journal**, redacted **before it leaves the customer's
  process** (see the write-time requirement below)
- holds git credentials that are **PR-scoped only**, per tenant, preferably as a
  GitHub App installation rather than a stored token
- keeps the web surface with **zero write actions**, as the self-hosted UI already
  does

The hosted side is therefore the brain and nothing else: webhook receipt,
classification, PR opening, the review UI, and the two capabilities that genuinely
require pooling runs from more than one team — cross-run policy memory and cost
accounting.

## Consequences

- **The invariant does the architecture for us.** An R6.1-preserving hosted
  sibei-flow *is* Satay's tier-1 hosted journal plane plus a webhook receiver and a
  PR opener. So the two hosted products share one plane, one tenancy model, one
  redaction implementation, one compliance posture and one on-call rotation. That
  reuse is forced by this ADR rather than merely permitted by it.
- **Redaction must move to write time.** Redacting on read leaves unredacted
  prompts, task inputs and business data in the operator's store, which makes the
  operator their custodian. Satay's `Redactor` is read-time today; a write-time
  mode has to exist before any journal leaves a customer process. Tracked on the
  Satay side.
- **"I don't operate anything" is not fully deliverable.** A hosted tenant still
  runs a worker. Accepted, because the alternative is holding warehouse
  credentials, and the trust cost of that is larger than the onboarding cost of a
  worker.
- ADR-0010's open-core plan is unchanged for the self-hosted tier. This ADR
  constrains only what the hosted tier may hold.
- Any future ADR proposing hosted repair execution must **supersede this one
  explicitly** and inherit the whole compliance surface (DPAs, data residency,
  retention, breach blast radius across tenants).

## Alternatives considered

- **Exempt hosting from R6.1** — rejected: R6.1 is the invariant the product's
  credibility rests on, and spending it before hosting demand is validated is the
  wrong order. It can be spent later, deliberately, by superseding this ADR.
- **Never host sibei-flow** — rejected: hosted sibei-flow is the clearer sale of
  the two products, and keeping the constrained shape open costs nothing today.
- **Hold warehouse credentials but encrypt them per tenant** — rejected: it
  answers the wrong question. The problem is not key management, it is that
  possession of read access to N warehouses makes us a breach target out of all
  proportion to the revenue.
