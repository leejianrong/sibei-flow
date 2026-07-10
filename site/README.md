# sibei-flow — marketing site

A self-contained static landing page for sibei-flow. One file, no build step, no
dependencies: `site/index.html` (inline CSS + a few lines of vanilla JS). Web
fonts load from Google Fonts; everything else is inline. All asset paths are
relative, so it works served from a project subpath (e.g. `/sibei-flow/`).

## Preview locally

```bash
cd site
python3 -m http.server 8000
# open http://localhost:8000
```

Or just open `site/index.html` directly in a browser.

## Publish to GitHub Pages

Two options — pick one.

### Option A — GitHub Actions (recommended, already wired)

This repo includes `.github/workflows/pages.yml`, which publishes the `site/`
directory on every push to `main`.

1. Repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. Push to `main` (or run the workflow manually from the **Actions** tab).
3. The site goes live at `https://leejianrong.github.io/sibei-flow/`.

### Option B — serve a folder, no Actions

1. Repo **Settings → Pages → Source: Deploy from a branch**.
2. Branch: `main`, folder: `/site` isn't selectable directly (Pages only offers
   `/` or `/docs`). If you prefer this route, either move the site into `docs/`
   or copy `index.html` to the repo root on a `gh-pages` branch. Option A avoids
   that constraint, which is why it's the default.

## Editing

Everything is in `site/index.html`. Design tokens (color, type, spacing) live in
the `:root` block at the top of the `<style>` tag; light-theme overrides sit just
below in the `prefers-color-scheme` and `[data-theme="light"]` blocks. Copy is
inline in the markup, section by section.
