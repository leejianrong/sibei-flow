import js from "@eslint/js";
import svelte from "eslint-plugin-svelte";
import { defineConfig, globalIgnores } from "eslint/config";
import globals from "globals";
import ts from "typescript-eslint";

export default defineConfig(
  globalIgnores(["dist/", "node_modules/"]),
  js.configs.recommended,
  ts.configs.recommended,
  svelte.configs.recommended,
  {
    languageOptions: { globals: { ...globals.browser } },
  },
  {
    files: ["**/*.svelte", "**/*.svelte.ts"],
    languageOptions: { parserOptions: { parser: ts.parser } },
  },
  {
    // Config files run in Node.
    files: ["vite.config.ts", "eslint.config.js", "svelte.config.js", "scripts/**/*.mjs"],
    languageOptions: { globals: { ...globals.node } },
  },
);
