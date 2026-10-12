import { svelte } from "@sveltejs/vite-plugin-svelte";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

/**
 * The brain serves the dashboard with `include_str!("../static/index.html")`, so
 * the build must be ONE self-contained HTML file: no sibling JS/CSS to mount, no
 * asset routes. This inlines the emitted chunk and stylesheet into index.html
 * after bundling. (A tiny in-repo plugin rather than vite-plugin-singlefile,
 * whose dependency chain carries an open `braces` advisory.)
 */
function inlineIntoHtml(): Plugin {
  return {
    name: "sbflow-inline-into-html",
    enforce: "post",
    apply: "build",
    generateBundle(_options, bundle) {
      const html = Object.values(bundle).find(
        (f) => f.type === "asset" && f.fileName.endsWith(".html"),
      );
      if (!html || html.type !== "asset") return;
      let source = String(html.source);

      for (const [name, file] of Object.entries(bundle)) {
        const esc = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        if (file.type === "chunk" && file.isEntry) {
          // `</script` inside inlined code would end the tag early.
          const code = file.code.replace(/<\/script/gi, "<\\/script");
          source = source.replace(
            new RegExp(`<script[^>]*src="[^"]*${esc}"[^>]*></script>`),
            () => `<script type="module">${code}</script>`,
          );
          delete bundle[name];
        } else if (file.type === "asset" && name.endsWith(".css")) {
          source = source.replace(
            new RegExp(`<link[^>]*href="[^"]*${esc}"[^>]*>`),
            () => `<style>${String(file.source)}</style>`,
          );
          delete bundle[name];
        }
      }
      html.source = source;
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [svelte(), inlineIntoHtml()],
  build: {
    // Never emit separate asset files; everything is inlined above.
    assetsInlineLimit: Number.MAX_SAFE_INTEGER,
    cssCodeSplit: false,
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
  // Svelte 5 resolves to its server build under node conditions unless told
  // otherwise, so component tests need the browser build. Only under test: an
  // explicit (even empty) `conditions` in a real build would replace Vite's
  // defaults and drag Svelte's server internals into the bundle.
  ...(mode === "test" ? { resolve: { conditions: ["browser"] } } : {}),
}));
