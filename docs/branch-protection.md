# Branch protection for `main` (card #4)

Make `main` protected and PR-only: no direct pushes, changes land through pull
requests, and the Track A CI jobs must be green before merge.

> **Human / confirm step.** This changes outward repo configuration on GitHub
> and needs an account with admin rights on the repo. Do **not** let an agent
> run it automatically — review and apply it yourself.

## Required status checks

The check names below must match the **CI job names** produced by Track A
(`.github/workflows/…`) **exactly** — GitHub matches on the job name string, so
a typo silently means "no check required". The three jobs are:

- `brain`
- `worker`
- `security`

If Track A renames a job, update the JSON body and the Settings UI to match.

## Option A — `gh` CLI (recommended)

Repo is `leejianrong/sibei-flow`. The `-X PUT …/branches/main/protection`
endpoint replaces the whole protection object, so send the full body:

```bash
gh api -X PUT repos/leejianrong/sibei-flow/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["brain", "worker", "security"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

What the body enforces:

- `required_status_checks.contexts` — `brain`, `worker`, `security` must pass.
- `required_status_checks.strict: true` — branch must be **up to date** with
  `main` before merge (re-run checks after rebasing).
- `required_pull_request_reviews` — merges go through a PR with ≥1 approval;
  stale approvals are dismissed on new pushes. (Solo repo? You can drop the
  review requirement, but keep the PR flow so checks always run.)
- `enforce_admins: true` — admins are held to the same rules (no bypass).
- `allow_force_pushes` / `allow_deletions: false` — no force-push or deleting
  `main`.
- `restrictions: null` — no user/team push allowlist (required key; `null` = not
  restricted, but direct pushes are still blocked by the PR requirement).

Verify afterward:

```bash
gh api repos/leejianrong/sibei-flow/branches/main/protection
```

## Option B — GitHub Settings UI (click-path)

1. Repo **Settings** → **Branches** (left sidebar, under "Code and automation").
2. Under **Branch protection rules**, click **Add branch ruleset** (or the
   classic **Add rule**).
3. **Branch name pattern:** `main`.
4. Enable **Require a pull request before merging** → set **Required approvals**
   to `1` and tick **Dismiss stale pull request approvals when new commits are
   pushed**.
5. Enable **Require status checks to pass before merging** and tick **Require
   branches to be up to date before merging**. In the search box add the three
   checks: **`brain`**, **`worker`**, **`security`** (they appear once each job
   has run at least once on a PR).
6. Enable **Do not allow bypassing the above settings** (classic: **Include
   administrators**).
7. Leave force pushes and deletions **disabled**.
8. **Create** / **Save changes**.

## Notes

- Status checks only show up in the picker after the corresponding job has run
  at least once — open a throwaway PR first if the names don't appear.
- Names must match Track A's job names character-for-character. If a check never
  reports, confirm the workflow job `name:` equals `brain` / `worker` /
  `security`.
