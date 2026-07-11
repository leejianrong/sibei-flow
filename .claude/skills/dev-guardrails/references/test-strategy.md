# Test strategy

The pyramid for sibei-flow, plus the rules that keep the gate fast and honest.

## Layers (fast → slow)

| Layer | What lives here | Where it runs | Command |
|---|---|---|---|
| **Unit** | Pure logic: classifier rules, diff guard, scorer rubric, diff computation | in-process, no I/O | `make test-brain` / `make test-worker` |
| **Integration (contract/seam)** | Seam 2 (webhook→job→dispatch, against real Postgres) and Seam 1 (worker `RepairJob→RepairResult`, against Postgres + warehouse + **real Docker sandbox**) | throwaway containers on the compose net | `make test` |
| **E2E / acceptance** | The full stack: POST a failure → verified `pr_proposed` in the dashboard (the "wow" demo) | `docker compose` stack | `make demo` (assert output) |

Keep the base wide (cheap unit tests) and the top thin (a few decisive e2e
checks). Push a bug down the pyramid: when an integration test catches
something, add the cheapest unit test that would have caught it too.

## Determinism (non-negotiable for the gate)

- The LLM is injected behind `LlmProvider`. The blocking gate (local + CI) uses
  the **record/replay** provider — keyless, deterministic. Tool *results* are
  produced by really running the tools (read_file/get_schema/edit_file/
  run_sandbox); only the model's decisions are canned.
- A **real model** (`LLM_PROVIDER=claude`/`openai`) belongs only in a separate,
  **non-blocking** eval job. Model quality is evaluated; it never gates a merge.

## Contract & invariant tests (regression firewall)

Guard the things that must not silently drift:
- **Frozen seams:** assert the shape of `Failure`, `RepairResult`, and the agent
  tool contract. A shape change must update the contract test *and* carry a note.
- **Safety invariants:** "a fix that fails tier-1 compile is never `pr_proposed`"
  (story 17); "out-of-scope failures are recorded, never dispatched"; "the web
  UI exposes no write endpoints (write verbs → 405)."
- **Golden/replay fixtures:** the flagship rename session is a recorded fixture;
  changing it is a deliberate act, reviewed like code.

## Coverage & flakes

- Track coverage but ratchet, don't chase 100%: never let it *drop* on a PR.
  Cover the durable spine and the safety invariants first.
- A flaky test is a regression in the test, not noise — quarantine it (mark and
  file), never paper over it with a blind retry. Real-Docker/warehouse tests
  need their services healthy first (the Makefile handles ordering).

## Do / don't

- **Do** run the layer that covers your change every time; run the full `make
  test` before any push.
- **Don't** add a test that needs a live LLM key to the blocking gate.
- **Don't** weaken an assertion to make a red test pass — fix the code or the
  fixture deliberately.
