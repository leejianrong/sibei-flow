// The brain serves the dashboard with include_str!, so the build must be exactly
// one self-contained index.html. Fail loudly if it ever grows sibling files or
// starts referencing local assets.
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const dist = fileURLToPath(new URL("../dist/", import.meta.url));
const files = readdirSync(dist);
const problems = [];

if (files.length !== 1 || files[0] !== "index.html") {
  problems.push(`dist/ must contain only index.html, found: ${files.join(", ") || "(nothing)"}`);
}

if (files.includes("index.html")) {
  const html = readFileSync(`${dist}index.html`, "utf8");
  // A local reference is src/href to a path, not a data:, http(s): or #fragment.
  const local = html.match(/<(?:script|link|img)\b[^>]*\b(?:src|href)="(?!data:|https?:|#)[^"]+"/g);
  if (local) problems.push(`index.html references local assets: ${local.join(" | ")}`);
}

if (problems.length) {
  console.error(problems.map((p) => `check-dist: ${p}`).join("\n"));
  process.exit(1);
}
console.log("check-dist: ok (single self-contained index.html)");
