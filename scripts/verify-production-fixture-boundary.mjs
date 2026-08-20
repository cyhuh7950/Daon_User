import { access, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ENTRYPOINTS = Object.freeze([
  "apps/desktop/src/main.jsx",
  "apps/desktop/src/desktop-shell.jsx",
  "apps/web/app/page.jsx",
  "apps/web/app/notebooks/page.jsx",
  "apps/web/app/notebooks/[notebook_id]/page.jsx",
  "apps/web/components/actual-workspace.jsx",
  "packages/ui/src/product-workspace-shell.jsx",
]);
const IMPORT_PATTERN = /(?:import|export)\s+(?:[^"']*?\s+from\s+)?["']([^"']+)["']/gu;
const FORBIDDEN = /(?:^|\/)(?:scripts\/test-harness|test-harness|fixtures?)(?:\/|$)|phase-[a-z0-9-]+-fixture/iu;
const EXTENSIONS = ["", ".js", ".jsx", ".mjs", ".ts", ".tsx"];

async function resolveRelative(fromFile, specifier) {
  const base = path.resolve(path.dirname(fromFile), specifier);
  for (const extension of EXTENSIONS) {
    const candidate = base + extension;
    try { await access(candidate); return candidate; } catch { /* try next exact extension */ }
  }
  for (const extension of [".js", ".jsx", ".mjs", ".ts", ".tsx"]) {
    const candidate = path.join(base, `index${extension}`);
    try { await access(candidate); return candidate; } catch { /* try next index */ }
  }
  return null;
}

export async function verifyProductionFixtureBoundary(rootInput) {
  const root = rootInput instanceof URL ? fileURLToPath(rootInput) : path.resolve(rootInput);
  const pending = ENTRYPOINTS.map((entry) => path.join(root, entry));
  const visited = new Set();
  const violations = [];
  while (pending.length) {
    const file = pending.pop();
    if (!file || visited.has(file)) continue;
    visited.add(file);
    const source = await readFile(file, "utf8");
    for (const match of source.matchAll(IMPORT_PATTERN)) {
      const specifier = match[1].replaceAll("\\", "/");
      if (FORBIDDEN.test(specifier)) violations.push(`${path.relative(root, file)} -> ${specifier}`);
      if (!specifier.startsWith(".")) continue;
      const resolved = await resolveRelative(file, specifier);
      if (resolved && /\.(?:m?js|jsx|tsx?)$/u.test(resolved)) pending.push(resolved);
    }
  }
  return Object.freeze({ visited: visited.size, violations: Object.freeze(violations) });
}

if (import.meta.url === `file://${process.argv[1]?.replaceAll("\\", "/")}`) {
  const result = await verifyProductionFixtureBoundary(new URL("..", import.meta.url));
  if (result.violations.length) {
    process.stderr.write(`${result.violations.join("\n")}\n`);
    process.exitCode = 1;
  } else {
    process.stdout.write(`production fixture boundary verified: modules=${result.visited}\n`);
  }
}
