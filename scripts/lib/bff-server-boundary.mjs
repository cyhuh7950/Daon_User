import { readFile } from "node:fs/promises";
import path from "node:path";
import { collectImportGraph, inspectModuleImports } from "../verify-product-ui-boundary.mjs";

const BFF = "apps/web/lib/bff-api-proxy.js";
const ROUTES = ["apps/web/app/bff/api/[...path]/route.js", "apps/web/app/api/v1/[...path]/route.js"];
// repo-boundaries.json의 현재 browser application 네 종류와 공유 UI의 실제 진입 경로다.
const BROWSER_ROOTS = ["packages/ui/src/", "apps/desktop/src/", "apps/mobile/src/", "apps/evidence-hub/src/"];

// 내부 주소 판정만 대상이다. Secret 및 다른 파일의 기존 검사는 호출자가 그대로 수행한다.
export async function verifiedServerBff(root, productionFiles) {
  try {
    const files = new Set(productionFiles.map(file => path.resolve(file)));
    const bff = path.resolve(root, BFF);
    if (!files.has(bff)) return false;
    const roots = [];
    for (const file of files) {
      const relative = path.relative(root, file).split(path.sep).join("/");
      if (!/^(?:apps|packages)\//u.test(relative) || !/\.[cm]?[jt]sx?$/u.test(file)) continue;
      const parsed = inspectModuleImports(await readFile(file, "utf8"), file);
      if (parsed.unresolved) return false;
      if (file === bff && parsed.useClient) return false;
      if (parsed.useClient || BROWSER_ROOTS.some(prefix => relative.startsWith(prefix)) || relative === "apps/mobile/index.js") roots.push(file);
    }
    if (!roots.length) return false;
    const errors = [];
    const packagesRoot = path.join(root, "packages");
    const browser = await collectImportGraph({ entryFiles: roots, packagesRoot, boundaryErrors: errors, strict: true });
    if (errors.length || browser.includes(bff)) return false;
    let consumed = false;
    for (const route of ROUTES) {
      const entry = path.resolve(root, route);
      if (!files.has(entry)) continue;
      const graph = await collectImportGraph({ entryFiles: [entry], packagesRoot, boundaryErrors: errors, strict: true });
      if (errors.length) return false;
      if (graph.includes(bff)) consumed = true;
    }
    return consumed;
  } catch { return false; }
}
