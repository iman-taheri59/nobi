/**
 * Lint the dashboard's inline `<script>` block with the same ESLint config as
 * `nbx-core.js`.
 *
 * The dashboard logic — 4,000 lines of it — lives inline in `index.html`.
 * `eslint index.html` cannot read it, so it was never linted; a typo in a
 * helper name there was only caught by loading the page and watching the
 * console. This extracts each inline block, lints it as `index.inline.js`
 * (whose globals are declared in `eslint.config.mjs`), and reports the
 * findings against the real line numbers in `index.html`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { ESLint } from "eslint";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const file = path.join(ROOT, "index.html");
const html = readFileSync(file, "utf8");

// Only blocks without a `src`: the CDN tags and the two module files are
// linted on their own.
const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)]
  .map((m) => ({
    code: m[1],
    line: html.slice(0, m.index).split("\n").length,
  }));

const eslint = new ESLint({ cwd: ROOT });
let errors = 0;
let warnings = 0;

for (const [i, block] of blocks.entries()) {
  const results = await eslint.lintText(block.code, { filePath: "index.inline.js" });
  for (const r of results) {
    for (const m of r.messages) {
      const where = `index.html:${block.line + m.line - 1}:${m.column}`;
      const kind = m.severity === 2 ? "error" : "warning";
      if (m.severity === 2) errors += 1;
      else warnings += 1;
      // Ids are printed so the block can be located in the minified build too.
      console.log(`${where}  ${kind}  ${m.message}  [${m.ruleId}] (block ${i + 1})`);
    }
  }
}

console.log(`\n${errors} error(s), ${warnings} warning(s) in ${blocks.length} inline block(s)`);
process.exit(errors ? 1 : 0);
