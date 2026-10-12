# web — dashboard frontend

Svelte 5 + Vite 8 + TypeScript. Scaffold only: the real run list and detail views
arrive with the dashboard port (SIB-93). The current dashboard is still the
vanilla `brain/static/index.html`.

```bash
make web-lint web-typecheck web-test web-build   # from the repo root (installs deps on first use)
cd web && npm run dev                            # Vite dev server
```

`make lint`, `make typecheck` and `make test-fast` include these (skipped with a
message if `npm` is missing); CI runs them in the `web` job.

## Why it is built the way it is

- **One self-contained `dist/index.html`.** The Rust brain serves the dashboard
  with `include_str!`, so there are no sibling JS/CSS files to mount. A small
  plugin in `vite.config.ts` inlines the bundle, and `scripts/check-dist.mjs`
  (run by `npm run build`) fails if the output ever stops being a single file.
  Wiring the built file into the brain's Docker build is part of SIB-93.
- **TypeScript is pinned to 6.0.x**, not 7. `typescript-eslint` 8.71 peers
  `typescript <6.1.0` and `svelte-check` 4.7 peers `^5 || ^6`, so 7.x breaks lint
  and type-check today. Dependabot ignores TypeScript majors; revisit when both
  tools support 7.
- **No SvelteKit.** There is no routing or server rendering; the brain serves one page.
- **Exact pins** in `package.json` plus a committed lockfile; `npm ci` in CI.
- **Node** `^22.22.2 || ^24.15.0 || >=26` (jsdom 30's floor); `.nvmrc` pins 24 for CI.
- **Read-only UI.** No write actions, and no secrets in the bundle (project invariant).
