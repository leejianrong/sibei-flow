# Show HN draft + launch checklist (KAN-224)

> **Status: draft for human review. NOT posted anywhere.** This file is the
> artifact for KAN-224. Final wording and the actual posting are a human call
> (per the card). Nothing here has been published to Hacker News or anywhere
> else.

## Open questions for the human reviewer

- The "webhook → PR" timing number below is written as a hedged range because
  KAN-222 (PR #32, open at draft time) has the README publishing "~12s" while
  the live site still says the more conservative "~90s" and flags the exact
  number as pending confirmation. Once KAN-222 lands, replace the hedge with
  whatever number that PR settles on.
- The repo link is a placeholder (`<REPO_URL>`); fill in the actual public
  GitHub URL before posting.
- Confirm the repo is actually public before this goes anywhere near HN.

---

## Show HN title

```
Show HN: sibei-flow – auto-heal broken dbt pipelines with a verified PR
```

Alternates, in case the primary reads as too broad:

```
Show HN: sibei-flow – dbt schema drift breaks your pipeline, this opens the fix as a PR
Show HN: sibei-flow – a verified, auto-opened PR for a broken dbt run
```

## Show HN body

I built sibei-flow because most of the 3am pipeline pages I've gotten over the
years turned out to be the same failure: a column got renamed upstream, dbt
choked on it downstream, and the actual fix was one line. By the time I was
awake enough to read the stack trace, I already knew what the diff was going
to look like. So I built something that does that part for me.

sibei-flow sits behind a webhook. When a dbt-in-Airflow run fails, it
classifies the failure (schema drift, a SQL error, or out of scope, which it
just records and never touches), then a bounded agent loop reads the failing
model and the live schema and drafts a minimal edit. Before you ever see that
draft, it's compiled in an ephemeral Docker container, and if it doesn't even
compile, it's suppressed. Nothing gets proposed unless it passed.

A draft that survives verification becomes an actual pull request: the diff,
a plain-English explanation, the reasoning transcript, and the verification
evidence (compiled, ran on a sample if you've wired one up, output schema
unchanged) all attached, plus a confidence/risk label. You still review and
merge it yourself. Opening that PR is the only thing in the whole system that
writes anything, anywhere. Everything upstream of it, source and warehouse
both, is read-only.

It's a Rust service for the webhook/classify/enqueue path and a Python worker
for the agent loop and the sandbox, sitting on Postgres. The whole thing comes
up with one `docker compose up`, no cluster, no new DSL to learn, and the
demo runs against a bundled keyless LLM provider, so you don't need an API key
to try it. In our own runs the webhook-to-PR loop typically finishes in
single digits to the low teens of seconds (we're still nailing down the exact
number to publish, but it's comfortably under the minute mark we'd set as
the bar).

What it doesn't do: it drafts one fix and verifies that one, not several
candidates in parallel. It only understands schema drift and SQL errors right
now, everything else gets logged as out of scope rather than acted on. And it
never merges anything itself, on purpose. It's Apache-2.0 and the whole loop
is meant to be read, not trusted blind.

Repo: <REPO_URL>

Happy to answer questions about the sandboxing, the classifier, or why it
holds no warehouse credentials.

---

## Launch-day checklist

### Pre-launch

- [ ] **Demo GIF is live** (KAN-221): the README currently has a placeholder
      comment (`<!-- ![sibei-flow: a schema-drift failure healed...] -->`) with
      a "demo GIF coming" note. Confirm the recorded GIF/video is committed to
      `docs/assets/hero-demo.gif` (or wherever it lands) and the placeholder is
      swapped for the real embed in both the README and `site/index.html`
      before the HN link goes out. A Show HN post without a visual under the
      fold is a much harder sell.
- [ ] **README + landing copy final** (KAN-222 / PR #32): merge that PR (or
      whatever supersedes it) first. In particular, resolve the "12s vs 90s"
      timing discrepancy between the README draft and the live site, and make
      sure this Show HN post's timing line matches whatever number ships.
- [ ] **LICENSE / security clean** (KAN-223): confirm Apache-2.0 matches
      ADR-0010's open-core intent, run the security-review skill over the
      public surface, and confirm there are no secrets, tokens, or internal
      URLs anywhere in the repo history that a Show HN crowd will absolutely
      go looking through.
- [ ] **Tag a release**, if there's a meaningful commit to pin the post to
      (e.g. `v1.0.0` marking the full V1–V5 scope). Makes "what exact code did
      they see" answerable later, and gives the PR/release notes a stable link.
- [ ] Read the post out loud once more, specifically checking the timing
      number against whatever KAN-222 landed on.
- [ ] Do a cold clone + `make up && make demo` on a machine that's never seen
      this repo, timed, to make sure the quickstart in the README still works
      exactly as written.

### Launch

- [ ] **Post time**: weekday, US morning Pacific (roughly 7–9am PT) tends to
      catch both US and EU/APAC readers who are still awake. Avoid Friday and
      weekends.
- [ ] **Cross-post**: a short note in relevant subreddits (e.g. r/dataengineering,
      r/dbt_analytics_engineering, check current names/rules first) and a post
      on X/Bluesky/LinkedIn linking back to the same HN thread, not a separate
      pitch, so discussion doesn't fragment. Don't cross-post before the HN
      thread has some organic traction; don't post to multiple subreddits
      simultaneously.
- [ ] **Who to notify**: anyone who gave early feedback on the repo, plus a
      short heads-up to whoever's around to help watch the thread the first
      few hours (comments move fast in the first hour or two and that's when
      a post lives or dies).

### Day-of

- [ ] **Monitor comments** closely for the first 2-3 hours. Reply promptly,
      plainly, and in first person; a maintainer voice reads better on HN than
      a corporate one.
- [ ] Have answers ready for the questions that are almost certainly coming:
  - **"Why not just use dbt's own retry / dbt-checkpoint / a warehouse-native
    schema check?"** Those catch that something broke; they don't draft the
    fix. sibei-flow's value is going from "your pipeline failed" to "here's a
    verified PR", not detecting the failure itself.
  - **"Is this safe to run against prod?"** Point at R6.1: the system holds no
    prod-write credentials, source and warehouse access are read-only, tier-2
    verification only ever builds into a dev/sample schema, and the only write
    action anywhere is opening the PR. It never touches prod, full stop.
  - **"What LLM does it use, and what does it cost?"** The default is a bundled
    keyless replay provider (deterministic, for the demo/tests, no API key,
    no cost). For a real fix, you point it at `LLM_PROVIDER=claude` +
    your own `ANTHROPIC_API_KEY`, or an OpenAI-compatible endpoint. You bring
    your own key and pay your own usage; sibei-flow doesn't meter or resell it.
  - **"What happens if the fix is wrong?"** It's a PR, not a merge. Nothing
    lands until a human approves it, same as any other PR in the repo.
  - **"Does this only work for dbt?"** Today, yes: dbt-in-Airflow is the
    shipped adapter. The `Failure` contract is generic; the codebase is honest
    that other pipeline frameworks aren't wired up yet.
  - **"Why not auto-merge if confidence is high?"** Deliberately out of scope
    for v1; see the "what it doesn't do" section of the post. Propose-and-approve
    only, on purpose, for now.
- [ ] Fix anything embarrassing fast (broken quickstart command, a typo'd
      link) but don't ship unrelated feature work mid-thread; a quick doc/README
      patch is fine, a new feature is not.
- [ ] Keep replies short. HN rewards someone who answers the actual question
      over someone who repastes the pitch.

### Post-launch

- [ ] **Triage feedback into the Pandan board.** Anything that's a real bug or
      a small doc fix becomes its own card, referencing the HN thread. Anything
      that's a feature request or an architecture question gets logged but
      checked against ADR-0012 (the capability freeze) before it becomes a
      card: a lot of "what about workflows / retries / scheduling" requests
      are going to be Satay's territory, not this repo's.
- [ ] Skim the thread once more the next morning; HN threads pick up a second
      wave once it's been linked elsewhere.
- [ ] Note what questions came up repeatedly; that's next README/landing-page
      material, and a signal for what the next launch (if there is one) needs
      to lead with.
