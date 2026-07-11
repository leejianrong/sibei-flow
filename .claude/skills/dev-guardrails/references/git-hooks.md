# Git hooks (local fast feedback)

Use the [`pre-commit`](https://pre-commit.com) framework. Hooks are the first,
cheapest gate — keep them **fast** (seconds), or people disable them.

## Split by cost

- **pre-commit (must be fast):** formatters + linters + hygiene + secret scan.
  Python `ruff`/`ruff-format`, generic hooks (trailing whitespace, EOF,
  large-file, yaml/json), `gitleaks`. No test runs, no Rust build.
- **pre-push (heavier, still bounded):** the fast test layer. The pragmatic
  option here is `make test` (containerized) or at least `make test-worker`;
  if that's too slow for the team, run unit tests only and leave full
  integration to CI.

Rust `cargo fmt --check` / `clippy` are toolchain-dependent and this repo has no
local Rust toolchain (everything is containerized). So keep **Rust checks in CI**
(authoritative) rather than a local hook, unless a contributor has `rustup` — in
which case a `pre-push`, `stages: [pre-push]` local hook is fine.

## Install

```bash
uv tool install pre-commit          # or: pipx install pre-commit
pre-commit install                  # pre-commit stage
pre-commit install --hook-type pre-push
pre-commit run --all-files          # one-time sweep
```

Copy `references/templates/.pre-commit-config.yaml` to the repo root and adjust
`rev:` pins. See that file for the concrete hook set.

## Commit messages

Adopt **Conventional Commits** (`feat:`, `fix:`, `chore:`, `docs:`, `test:`,
`refactor:` …). It's already the de-facto style in this repo's history and it
powers changelog/release automation later. Enforce with a `commit-msg` hook
(commitizen/commitlint) only if the team wants hard enforcement; otherwise treat
it as convention + a PR-title check in CI.

## Bypass discipline

`git commit --no-verify` exists for emergencies; treat every use as a debt to
repay (fix the thing the hook flagged, or the hook itself if it's wrong). Never
make `--no-verify` the habit.
