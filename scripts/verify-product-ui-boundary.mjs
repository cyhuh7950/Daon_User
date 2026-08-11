import { lstat, readFile, readdir } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

export const FORBIDDEN_PRODUCT_UI_TOKENS = Object.freeze([
  "ProductionBoundEvidenceHub",
  "prototype_fixture",
  "deferred_actual",
  "Mock Adapter",
  "@daon-user/evidence-hub",
  ".evidence-hub",
  ".evidence-route-strip",
  ".evidence-journey-grid"
]);

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const textExtensions = new Set([".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".json", ".html", ".css", ".map"]);
const ignoredSourceDirectories = new Set([".next", "dist", "node_modules", "target"]);

async function pathType(candidate) {
  try {
    const stats = await lstat(candidate);
    if (stats.isSymbolicLink()) return "symlink";
    if (stats.isDirectory()) return "directory";
    if (stats.isFile()) return "file";
    return "other";
  } catch (error) {
    if (error?.code === "ENOENT") return "missing";
    throw error;
  }
}

async function collectTextFiles(candidate, { source = false, required = true, boundaryErrors = [] } = {}) {
  const type = await pathType(candidate);
  if (type === "missing") {
    if (required) boundaryErrors.push({ path: candidate, code: "REQUIRED_ROOT_MISSING" });
    return [];
  }
  if (type === "symlink") {
    boundaryErrors.push({ path: candidate, code: "SYMLINK_NOT_ALLOWED" });
    return [];
  }
  if (type === "other") {
    boundaryErrors.push({ path: candidate, code: "REQUIRED_ROOT_INVALID" });
    return [];
  }
  if (type === "file") return textExtensions.has(path.extname(candidate).toLowerCase()) ? [candidate] : [];

  const entries = await readdir(candidate, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => {
    if (source && entry.isDirectory() && ignoredSourceDirectories.has(entry.name)) return [];
    if (source && ignoredSourceDirectories.has(entry.name)) return [];
    const nestedCandidate = path.join(candidate, entry.name);
    if (entry.isSymbolicLink()) {
      boundaryErrors.push({ path: nestedCandidate, code: "SYMLINK_NOT_ALLOWED" });
      return [];
    }
    return collectTextFiles(nestedCandidate, { source, required: false, boundaryErrors });
  }));
  return nested.flat();
}

async function collectRequiredRoot(candidate, options) {
  const before = options.boundaryErrors.length;
  const files = await collectTextFiles(candidate, options);
  if (files.length === 0 && options.boundaryErrors.length === before) {
    options.boundaryErrors.push({ path: candidate, code: "REQUIRED_ROOT_EMPTY" });
  }
  return files;
}

async function validateRequiredArtifact(artifact, boundaryErrors) {
  const candidate = path.resolve(artifact.path);
  const codePrefix = artifact.codePrefix ?? "REQUIRED_ASSET";
  const type = await pathType(candidate);
  if (type === "missing") {
    boundaryErrors.push({ path: candidate, code: `${codePrefix}_MISSING` });
    return;
  }
  if (type === "symlink") {
    boundaryErrors.push({ path: candidate, code: artifact.codePrefix ? `${codePrefix}_SYMLINK` : "SYMLINK_NOT_ALLOWED" });
    return;
  }
  if (artifact.type && artifact.type !== type) {
    boundaryErrors.push({ path: candidate, code: `${codePrefix}_INVALID` });
    return;
  }
  if (type === "file" && artifact.readable) {
    try {
      await readFile(candidate, "utf8");
    } catch {
      boundaryErrors.push({ path: candidate, code: `${codePrefix}_UNREADABLE` });
      return;
    }
  }
  if (type === "directory" && artifact.extensions?.length) {
    const files = await collectTextFiles(candidate, { required: false, boundaryErrors });
    for (const extension of artifact.extensions) {
      if (!files.some((file) => path.extname(file).toLowerCase() === extension)) {
        boundaryErrors.push({ path: candidate, code: `REPRESENTATIVE_ASSET_MISSING:${extension}` });
      }
    }
  }
}

const moduleExtensions = Object.freeze([".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".json", ".css"]);

async function resolveModuleFile(candidate, boundaryErrors) {
  const candidates = [candidate];
  if (!path.extname(candidate)) {
    candidates.push(...moduleExtensions.map((extension) => `${candidate}${extension}`));
    candidates.push(...moduleExtensions.map((extension) => path.join(candidate, `index${extension}`)));
  }
  for (const target of candidates) {
    const type = await pathType(target);
    if (type === "file") return target;
    if (type === "symlink") {
      boundaryErrors.push({ path: target, code: "SYMLINK_NOT_ALLOWED" });
      return null;
    }
  }
  return null;
}

function collectImportSpecifiers(content) {
  const specifiers = new Set();
  const patterns = [
    /(?:import|export)\s+(?:[^"']*?\s+from\s*)?["']([^"']+)["']/gu,
    /import\s*\(\s*["']([^"']+)["']\s*\)/gu,
    /@import\s+(?:url\()?\s*["']?([^"')\s;]+)["']?/gu
  ];
  for (const pattern of patterns) {
    for (const match of content.matchAll(pattern)) specifiers.add(match[1]);
  }
  return [...specifiers];
}

function packageExportTarget(value) {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  for (const key of ["import", "default", "browser", "node"]) {
    const target = packageExportTarget(value[key]);
    if (target) return target;
  }
  return null;
}

async function loadWorkspacePackages(packagesRoot, boundaryErrors) {
  const packages = new Map();
  if (!packagesRoot) return packages;
  const type = await pathType(packagesRoot);
  if (type !== "directory") {
    boundaryErrors.push({ path: packagesRoot, code: type === "missing" ? "WORKSPACE_PACKAGES_MISSING" : "WORKSPACE_PACKAGES_INVALID" });
    return packages;
  }
  for (const entry of await readdir(packagesRoot, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.isSymbolicLink()) continue;
    const packageRoot = path.join(packagesRoot, entry.name);
    const manifestPath = path.join(packageRoot, "package.json");
    if (await pathType(manifestPath) !== "file") continue;
    try {
      const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
      if (typeof manifest?.name === "string") packages.set(manifest.name, { root: packageRoot, exports: manifest.exports });
    } catch {
      boundaryErrors.push({ path: manifestPath, code: "WORKSPACE_PACKAGE_MANIFEST_INVALID" });
    }
  }
  return packages;
}

function splitWorkspaceSpecifier(specifier) {
  if (!specifier.startsWith("@daon-user/")) return null;
  const parts = specifier.split("/");
  return { packageName: parts.slice(0, 2).join("/"), exportKey: parts.length === 2 ? "." : `./${parts.slice(2).join("/")}` };
}

async function collectImportGraph({ entryFiles, packagesRoot, boundaryErrors }) {
  const workspacePackages = await loadWorkspacePackages(packagesRoot, boundaryErrors);
  const queue = [...entryFiles.map((file) => path.resolve(file))];
  const visited = new Set();
  while (queue.length) {
    const current = queue.shift();
    if (visited.has(current)) continue;
    const currentType = await pathType(current);
    if (currentType !== "file") {
      boundaryErrors.push({ path: current, code: currentType === "missing" ? "IMPORT_ENTRY_MISSING" : "IMPORT_ENTRY_INVALID" });
      continue;
    }
    visited.add(current);
    if (!textExtensions.has(path.extname(current).toLowerCase())) continue;
    const content = await readFile(current, "utf8");
    for (const specifier of collectImportSpecifiers(content)) {
      let target = null;
      if (specifier.startsWith(".")) {
        target = await resolveModuleFile(path.resolve(path.dirname(current), specifier), boundaryErrors);
      } else {
        const workspaceSpecifier = splitWorkspaceSpecifier(specifier);
        if (!workspaceSpecifier) continue;
        const workspacePackage = workspacePackages.get(workspaceSpecifier.packageName);
        const exportValue = workspacePackage?.exports?.[workspaceSpecifier.exportKey];
        const exportTarget = packageExportTarget(exportValue);
        if (workspacePackage && exportTarget) {
          target = await resolveModuleFile(path.resolve(workspacePackage.root, exportTarget), boundaryErrors);
        }
      }
      if (!target) {
        boundaryErrors.push({ path: current, code: `IMPORT_TARGET_MISSING:${specifier}` });
        continue;
      }
      queue.push(target);
    }
  }
  return [...visited];
}

async function validateManifestAsset(candidate, boundaryErrors) {
  const type = await pathType(candidate);
  if (type === "file") return;
  boundaryErrors.push({ path: candidate, code: type === "missing" ? "MANIFEST_ASSET_MISSING" : type === "symlink" ? "SYMLINK_NOT_ALLOWED" : "MANIFEST_ASSET_INVALID" });
}

function collectJsonStrings(value, output = []) {
  if (typeof value === "string") output.push(value);
  else if (Array.isArray(value)) value.forEach((item) => collectJsonStrings(item, output));
  else if (value && typeof value === "object") Object.values(value).forEach((item) => collectJsonStrings(item, output));
  return output;
}

async function readJsonManifest(manifestPath, boundaryErrors) {
  try {
    return JSON.parse(await readFile(manifestPath, "utf8"));
  } catch {
    boundaryErrors.push({ path: manifestPath, code: "MANIFEST_INVALID" });
    return null;
  }
}

async function validateNextManifestReferences(nextRoot, boundaryErrors) {
  if (!nextRoot) return;
  const rootBuildManifest = path.join(nextRoot, "build-manifest.json");
  const serverAppRoot = path.join(nextRoot, "server/app");
  const manifestFiles = await collectTextFiles(serverAppRoot, { required: false, boundaryErrors });
  const buildManifests = [rootBuildManifest, ...manifestFiles.filter((file) => path.basename(file) === "build-manifest.json")];
  const appPathManifests = manifestFiles.filter((file) => path.basename(file) === "app-paths-manifest.json");
  const serverAppPaths = path.join(nextRoot, "server/app-paths-manifest.json");
  if (await pathType(serverAppPaths) === "file") appPathManifests.push(serverAppPaths);

  for (const manifestPath of [...new Set(buildManifests)]) {
    const manifest = await readJsonManifest(manifestPath, boundaryErrors);
    if (!manifest) continue;
    for (const reference of collectJsonStrings(manifest).filter((value) => /^static\/.+\.(?:js|css)$/u.test(value))) {
      await validateManifestAsset(path.join(nextRoot, ...reference.split("/")), boundaryErrors);
    }
  }
  for (const manifestPath of [...new Set(appPathManifests)]) {
    const manifest = await readJsonManifest(manifestPath, boundaryErrors);
    if (!manifest) continue;
    for (const reference of collectJsonStrings(manifest).filter((value) => /^app\/.+\.(?:js|css)$/u.test(value))) {
      await validateManifestAsset(path.join(nextRoot, "server", ...reference.split("/")), boundaryErrors);
    }
  }
  for (const manifestPath of manifestFiles.filter((file) => file.endsWith(".nft.json"))) {
    const manifest = await readJsonManifest(manifestPath, boundaryErrors);
    if (!manifest) continue;
    for (const reference of Array.isArray(manifest.files) ? manifest.files : []) {
      await validateManifestAsset(path.resolve(path.dirname(manifestPath), ...String(reference).split("/")), boundaryErrors);
    }
  }
  for (const manifestPath of manifestFiles.filter((file) => file.endsWith("_client-reference-manifest.js"))) {
    const content = await readFile(manifestPath, "utf8");
    const references = new Set();
    for (const match of content.matchAll(/["'](?:\/_next\/)?(static\/[^"']+?\.(?:js|css))["']/gu)) references.add(path.join(nextRoot, ...match[1].split("/")));
    for (const match of content.matchAll(/["'](server\/chunks\/[^"']+?\.js)["']/gu)) references.add(path.join(nextRoot, ...match[1].split("/")));
    for (const reference of references) await validateManifestAsset(reference, boundaryErrors);
  }
}

async function validateViteManifestReferences(distRoot, boundaryErrors) {
  if (!distRoot) return;
  const indexPath = path.join(distRoot, "index.html");
  if (await pathType(indexPath) !== "file") return;
  const content = await readFile(indexPath, "utf8");
  const references = new Set();
  for (const match of content.matchAll(/(?:src|href)=["']([^"']+)["']/gu)) {
    const reference = match[1];
    if (/^(?:https?:|data:|#)/u.test(reference) || !/\.(?:js|css)$/u.test(reference)) continue;
    references.add(path.join(distRoot, ...reference.replace(/^\//u, "").split("/")));
  }
  for (const reference of references) await validateManifestAsset(reference, boundaryErrors);
}

function isWithin(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
}

async function resolveExactBffBundleExceptions(root) {
  const routeRoot = path.join(root, "apps/web/.next/server/app/bff/shell/runtime");
  const routeFile = path.join(routeRoot, "route.js");
  const nftFile = path.join(routeRoot, "route.js.nft.json");
  const chunksRoot = path.join(root, "apps/web/.next/server/chunks");
  if (await pathType(routeFile) !== "file" || await pathType(nftFile) !== "file") return [];

  let nft;
  try {
    nft = JSON.parse(await readFile(nftFile, "utf8"));
  } catch {
    return [];
  }
  const routeSource = await readFile(routeFile, "utf8");
  const routeReferences = new Set([...routeSource.matchAll(/R\.c\("(server\/chunks\/[^"\r\n]+\.js)"\)/gu)]
    .map((match) => path.resolve(path.join(root, "apps/web/.next"), ...match[1].split("/"))));
  const nftReferences = new Set((Array.isArray(nft?.files) ? nft.files : [])
    .map((file) => path.resolve(routeRoot, ...String(file).split("/"))));
  const allowed = [];

  for (const candidate of routeReferences) {
    if (!nftReferences.has(candidate) || !isWithin(chunksRoot, candidate) || await pathType(candidate) !== "file") continue;
    const content = await readFile(candidate, "utf8");
    const presentTokens = FORBIDDEN_PRODUCT_UI_TOKENS.filter((token) => content.includes(token));
    if (presentTokens.length !== 1 || presentTokens[0] !== "deferred_actual") continue;
    const mapFile = `${candidate}.map`;
    if (await pathType(mapFile) !== "file") continue;
    let sourceMap;
    try {
      sourceMap = JSON.parse(await readFile(mapFile, "utf8"));
    } catch {
      continue;
    }
    const projectSources = (Array.isArray(sourceMap?.sources) ? sourceMap.sources : [])
      .map((source) => String(source).replaceAll("\\", "/"))
      .filter((source) => source.includes("apps/web/") || source.includes("packages/"));
    const exactSources = ["apps/web/lib/web-shell-runtime.js", "apps/web/app/bff/shell/runtime/route.js"];
    if (
      !projectSources.some((source) => source.endsWith(exactSources[0]))
      || projectSources.some((source) => !exactSources.some((exact) => source.endsWith(exact)))
    ) continue;
    allowed.push(candidate, mapFile);
  }
  return allowed;
}

export async function scanProductUiBoundary({ sourceRoots, bundleRoots, commonSourceFiles, sourceEntryFiles = [], workspacePackagesRoot = null, nextBuildRoot = null, viteDistRoot = null, requiredArtifacts = [], excludedSourceFiles = [], excludedBundleFiles = [], reportRoot = repositoryRoot }) {
  const boundaryErrors = [];
  const sourceFiles = (await Promise.all(sourceRoots.map((root) => collectRequiredRoot(path.resolve(root), { source: true, required: true, boundaryErrors })))).flat();
  const bundleFiles = (await Promise.all(bundleRoots.map((root) => collectRequiredRoot(path.resolve(root), { source: false, required: true, boundaryErrors })))).flat();
  const commonFiles = (await Promise.all(commonSourceFiles.map((file) => collectRequiredRoot(path.resolve(file), { source: true, required: true, boundaryErrors })))).flat();
  const graphFiles = await collectImportGraph({ entryFiles: sourceEntryFiles, packagesRoot: workspacePackagesRoot, boundaryErrors });
  await Promise.all(requiredArtifacts.map((artifact) => validateRequiredArtifact(artifact, boundaryErrors)));
  await validateNextManifestReferences(nextBuildRoot, boundaryErrors);
  await validateViteManifestReferences(viteDistRoot, boundaryErrors);
  const excludedSources = new Set(excludedSourceFiles.map((file) => path.resolve(file)));
  const excludedBundles = new Set(excludedBundleFiles.map((file) => path.resolve(file)));
  const files = [...new Set([
    ...sourceFiles.filter((file) => !excludedSources.has(file)),
    ...bundleFiles.filter((file) => !excludedBundles.has(file)),
    ...commonFiles,
    ...graphFiles.filter((file) => !excludedSources.has(file))
  ])].sort();
  const violations = [];

  for (const file of files) {
    const content = await readFile(file, "utf8");
    for (const token of FORBIDDEN_PRODUCT_UI_TOKENS) {
      if (content.includes(token)) {
        violations.push({
          file: path.relative(reportRoot, file).split(path.sep).join("/"),
          token
        });
      }
    }
  }

  const normalizedErrors = boundaryErrors.map((error) => ({
    ...error,
    path: path.relative(reportRoot, error.path).split(path.sep).join("/")
  }));
  return { ok: violations.length === 0 && normalizedErrors.length === 0, scannedFiles: files.length, violations, boundaryErrors: normalizedErrors };
}

export async function scanDefaultProductUiBoundary({ root = repositoryRoot, target = "all" } = {}) {
  if (!new Set(["all", "web", "desktop"]).has(target)) throw new Error("PRODUCT_UI_BOUNDARY_TARGET_INVALID");
  const includeWeb = target === "all" || target === "web";
  const includeDesktop = target === "all" || target === "desktop";
  const sourceRoots = [];
  const bundleRoots = [];
  const sourceEntryFiles = [];
  const requiredArtifacts = [];
  let nextBuildRoot = null;
  let viteDistRoot = null;
  if (includeWeb) {
    const webAppRoot = path.join(root, "apps/web/app");
    const requiredProductEntries = [
      path.join(webAppRoot, "settings/account/page.jsx"),
      path.join(webAppRoot, "settings/organization/page.jsx")
    ];
    nextBuildRoot = path.join(root, "apps/web/.next");
    sourceRoots.push(webAppRoot, path.join(root, "apps/web/components"), path.join(root, "apps/web/lib"));
    bundleRoots.push(path.join(nextBuildRoot, "static"), path.join(nextBuildRoot, "server/app"), path.join(nextBuildRoot, "server/chunks"));
    const entryCandidates = await collectTextFiles(webAppRoot, { source: true, required: true, boundaryErrors: [] });
    sourceEntryFiles.push(
      ...entryCandidates.filter((file) => /(?:^|[\\/])(?:page|layout|route|loading|error|not-found)\.(?:js|jsx|mjs|cjs|ts|tsx)$/u.test(file)),
      ...requiredProductEntries
    );
    requiredArtifacts.push(
      ...requiredProductEntries.map((entryPath) => ({
        path: entryPath,
        type: "file",
        readable: true,
        codePrefix: "REQUIRED_PRODUCT_ENTRY"
      })),
      { path: path.join(nextBuildRoot, "BUILD_ID"), type: "file" },
      { path: path.join(nextBuildRoot, "build-manifest.json"), type: "file" },
      { path: path.join(nextBuildRoot, "server/app-paths-manifest.json"), type: "file" },
      { path: path.join(nextBuildRoot, "static"), type: "directory", extensions: [".js"] },
      { path: path.join(nextBuildRoot, "server/app/page.js"), type: "file" },
      { path: path.join(nextBuildRoot, "server/chunks"), type: "directory", extensions: [".js"] }
    );
  }
  if (includeDesktop) {
    viteDistRoot = path.join(root, "apps/desktop/dist");
    sourceRoots.push(path.join(root, "apps/desktop/src"));
    sourceEntryFiles.push(path.join(root, "apps/desktop/src/main.jsx"));
    bundleRoots.push(viteDistRoot);
    requiredArtifacts.push(
      { path: path.join(viteDistRoot, "index.html"), type: "file" },
      { path: path.join(viteDistRoot, "assets"), type: "directory", extensions: [".js", ".css"] }
    );
  }
  const exactBffBundleExceptions = includeWeb ? await resolveExactBffBundleExceptions(root) : [];
  return scanProductUiBoundary({
    sourceRoots,
    bundleRoots,
    sourceEntryFiles,
    workspacePackagesRoot: path.join(root, "packages"),
    nextBuildRoot,
    viteDistRoot,
    commonSourceFiles: [
      path.join(root, "packages/ui/src/index.js"),
      path.join(root, "packages/ui/src/product-workspace-shell.jsx"),
      path.join(root, "packages/ui/src/product-workspace-model.js"),
      path.join(root, "packages/ui/src/workspace.css")
    ],
    requiredArtifacts,
    reportRoot: root,
    // 어울1 승인 exact allowlist: 서버 전용 BFF Runtime은 사용자 UI 경계와 분리한다.
    excludedSourceFiles: [
      path.join(root, "apps/web/app/bff/shell/runtime/route.js"),
      path.join(root, "apps/web/lib/web-shell-runtime.js")
    ],
    excludedBundleFiles: [
      path.join(root, "apps/web/.next/server/app/bff/shell/runtime/route.js"),
      path.join(root, "apps/web/.next/server/app/bff/shell/runtime/route.js.map"),
      path.join(root, "apps/web/.next/server/app/bff/shell/runtime/route.js.nft.json"),
      ...exactBffBundleExceptions
    ]
  });
}

async function main() {
  const targetIndex = process.argv.indexOf("--target");
  const target = targetIndex === -1 ? "all" : process.argv[targetIndex + 1];
  const result = await scanDefaultProductUiBoundary({ target });
  const output = `${JSON.stringify(result, null, 2)}\n`;
  if (result.ok) process.stdout.write(output); else process.stderr.write(output);
  process.exitCode = result.ok ? 0 : 1;
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(`PRODUCT_UI_BOUNDARY_ERROR ${error?.message ?? "unknown"}`);
    process.exitCode = 2;
  });
}
