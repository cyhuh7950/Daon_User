import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const DEFAULT_FILES = [
  "apps/web/app/layout.jsx",
  "apps/web/app/page.jsx",
  "apps/web/app/workspaces/[workspace_id]/page.jsx",
  "apps/web/next.config.mjs",
  "packages/ui/src/adaptive-workspace.jsx",
  "packages/ui/src/index.js",
  "packages/ui/src/workspace-interaction.js",
  "packages/ui/src/workspace-model.js"
];

export function lintSource(file, source) {
  const findings = [];
  const rules = [
    ["debugger", /\bdebugger\s*;/g, "debugger is forbidden"],
    ["eval", /\beval\s*\(/g, "eval is forbidden"],
    ["fetch", /\bfetch\s*\(/g, "direct fetch is forbidden in workspace browser source"],
    ["forbidden-browser-url", /https?:\/\/|localhost|127\.0\.0\.1/g, "absolute or internal browser URL is forbidden"],
    ["forbidden-browser-env", /NEXT_PUBLIC_API_BASE_URL/g, "NEXT_PUBLIC_API_BASE_URL is forbidden"]
  ];
  for (const [rule, pattern, message] of rules) {
    for (const match of source.matchAll(pattern)) {
      const line = source.slice(0, match.index).split("\n").length;
      findings.push({ rule, line, message });
    }
  }
  return findings;
}

async function main() {
  const files = process.argv.slice(2).length > 0 ? process.argv.slice(2) : DEFAULT_FILES;
  const allFindings = [];
  const tsc = path.resolve("node_modules/typescript/bin/tsc");
  const parse = spawnSync(process.execPath, [tsc, "--noEmit", "--noCheck", "--allowJs", "--checkJs", "false", "--jsx", "preserve", "--module", "nodenext", "--moduleResolution", "nodenext", "--target", "es2022", ...files], { cwd: process.cwd(), encoding: "utf8" });
  if (parse.status !== 0) allFindings.push({ file: "typescript", rule: "parse", line: 1, message: (parse.stdout + parse.stderr).trim() || "TypeScript parse failed" });
  for (const file of files) {
    const source = await readFile(path.resolve(file), "utf8");
    for (const finding of lintSource(file, source)) allFindings.push({ file, ...finding });
  }
  if (allFindings.length > 0) {
    for (const finding of allFindings) console.error(`${finding.file}:${finding.line} ${finding.rule} ${finding.message}`);
    process.exitCode = 1;
    return;
  }
  console.log(`workspace lint passed: ${files.length} files`);
}

if (path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
