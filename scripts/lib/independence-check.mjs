import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";

const DEP_KEYS = ["dependencies", "devDependencies", "peerDependencies", "optionalDependencies"];
const CODE_EXTENSIONS = new Set([".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".py", ".rs"]);
const JS_CODE_EXTENSIONS = new Set([".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"]);
const JS_IMPORT_PATTERN = /(?:\b(?:import|export)\s+(?:[^"'`\r\n;]*?\s+from\s+)?|\bimport\s*\(\s*|\brequire\s*\(\s*)["']([^"']+)["']/g;
const DAON_PRODUCT_PATTERN = /(?:^|[\\/@._-])daon(?:2(?:\.5)?|3)(?:$|[\\/@._-])/i;
const LOCAL_PACKAGE_PATTERN = /^(?:file:|link:|\.\.?[\\/]|[a-z]:[\\/]|\\\\)/i;
const ABSOLUTE_PATH_PATTERN = /(?:[a-z]:[\\/](?:users|project|workspaces?)[\\/][^\s"'`]+|\/(?:users|home|opt|srv)\/[^\s"'`]+)/i;
const DAON_PATH_PATTERN = /(?:[a-z]:[\\/][^\s"'`]*[\\/]daon(?:2(?:\.5)?|3)(?:[\\/]|\b)|\/(?:[^\s"'`]+\/)*daon(?:2(?:\.5)?|3)(?:\/|\b))/i;
const DIRECT_URL_PATTERN = /(?:https?:\/\/|\blocalhost(?::\d+)?\b|\b127\.0\.0\.1(?::\d+)?\b|NEXT_PUBLIC_API_BASE_URL)/i;
const CONNECTOR_PATTERN = /(?:daon(?:2(?:\.5)?|3)[-_/]?(?:client|sdk|internal|endpoint)|daon[-_/](?:internal|sdk|client|endpoint))/i;

const normalize = (value) => value.replaceAll("\\", "/").replace(/^\.\//, "");

function evidence(line) {
  return line.trim().replace(/(authorization|api[_-]?key|token|password)\s*[:=]\s*[^\s,;]+/gi, "$1=***").slice(0, 180);
}

function violation(ruleId, file, line, detail, source = "") {
  return { rule_id: ruleId, file: normalize(file), line, evidence: evidence(source || detail), remediation: detail };
}

async function exists(file) {
  try { await stat(file); return true; } catch { return false; }
}

async function walk(root, relative, policy, output) {
  const absolute = path.join(root, relative);
  if (!(await exists(absolute))) return;
  for (const entry of await readdir(absolute, { withFileTypes: true })) {
    const child = normalize(path.join(relative, entry.name));
    if (entry.isDirectory()) {
      if (!policy.excluded_directories.includes(entry.name)) await walk(root, child, policy, output);
    } else if (entry.isFile()) {
      output.push(child);
    }
  }
}

async function readJson(file, label) {
  try { return JSON.parse(await readFile(file, "utf8")); }
  catch (error) { throw new PolicyError(`${label} JSON을 읽을 수 없습니다: ${error.message}`); }
}

export class PolicyError extends Error {}

export async function loadPolicy(policyPath) {
  const policy = await readJson(policyPath, "independence-policy");
  const requiredArrays = ["scan_roots", "excluded_directories", "general_scan_exclusions", "package_manifest_names", "browser_path_segments", "server_path_segments", "exceptions"];
  if (policy.schema_version !== "1.0" || !policy.boundary_manifest || !policy.root_package_manifest || !policy.lockfile || !policy.rules || requiredArrays.some((key) => !Array.isArray(policy[key]))) {
    throw new PolicyError("independence-policy schema_version 1.0 필수 필드가 누락되었습니다.");
  }
  if (policy.exceptions.length !== 0) throw new PolicyError("R1-M1-04에서는 제품 예외를 허용하지 않습니다.");
  return policy;
}

function componentFor(file, components) {
  const normalized = normalize(file);
  return components.find((component) => normalized === component.path || normalized.startsWith(`${component.path}/`));
}

function classifyRuntime(file, text, policy) {
  const normalized = `/${normalize(file).toLowerCase()}`;
  const ext = path.extname(normalized);
  if (!CODE_EXTENSIONS.has(ext) || !normalized.startsWith("/apps/web/")) return "non-browser";
  if (/\b["']use server["']/.test(text) || /\/route\.(?:js|jsx|ts|tsx)$/.test(normalized) || normalized.includes(".server.") || policy.server_path_segments.some((segment) => normalized.includes(segment))) return "server";
  if (/\b["']use client["']/.test(text) || policy.browser_path_segments.some((segment) => normalized.includes(segment))) return "browser";
  return "server-default";
}

function lineNumber(text, index) {
  return text.slice(0, index).split(/\r?\n/).length;
}

function addGraphViolation(violations, file, detail, source = detail) {
  violations.push(violation("DEP_GRAPH_BOUNDARY", file, 1, detail, source));
}

function findCycles(componentIds, edges) {
  const adjacent = new Map(componentIds.map((id) => [id, []]));
  for (const edge of edges) adjacent.get(edge.from)?.push(edge.to);
  const visiting = new Set();
  const visited = new Set();
  const cycles = [];
  function visit(id, trail) {
    if (visiting.has(id)) { cycles.push([...trail.slice(trail.indexOf(id)), id]); return; }
    if (visited.has(id)) return;
    visiting.add(id);
    for (const next of adjacent.get(id) || []) visit(next, [...trail, id]);
    visiting.delete(id); visited.add(id);
  }
  for (const id of componentIds) visit(id, []);
  return cycles;
}

function inspectNpmDependencies(file, data, violations, onDependency = () => {}) {
  for (const key of DEP_KEYS) {
    for (const [name, spec] of Object.entries(data[key] || {})) {
      if (DAON_PRODUCT_PATTERN.test(name) || DAON_PRODUCT_PATTERN.test(String(spec)) || LOCAL_PACKAGE_PATTERN.test(String(spec))) {
        violations.push(violation("PACKAGE_DAON_INTERNAL", file, 1, "다른 Daon 제품 또는 저장소 경로 Package 직접 의존을 제거하십시오.", `${name}: ${spec}`));
      }
      onDependency(name, spec, key);
    }
  }
}

function lockPackageName(lockPath, entry) {
  if (entry?.name) return entry.name;
  const marker = "node_modules/";
  const index = lockPath.lastIndexOf(marker);
  return index >= 0 ? lockPath.slice(index + marker.length) : "";
}

function inspectLockfile(file, data, components, violations) {
  if (!data.packages || typeof data.packages !== "object" || Array.isArray(data.packages)) throw new PolicyError("package-lock.json packages 구조가 유효하지 않습니다.");
  const workspacePaths = new Set(components.map((component) => component.path));
  for (const [lockPath, entry] of Object.entries(data.packages)) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) continue;
    const identity = lockPackageName(lockPath, entry);
    if (identity && DAON_PRODUCT_PATTERN.test(identity)) {
      violations.push(violation("PACKAGE_DAON_INTERNAL", file, 1, "Lockfile의 다른 Daon 제품 Package identity를 제거하십시오.", identity));
    }
    inspectNpmDependencies(file, entry, violations);
    if (typeof entry.resolved === "string") {
      const resolved = normalize(entry.resolved);
      const approvedWorkspaceLink = entry.link === true && workspacePaths.has(resolved);
      const forbiddenResolved = DAON_PRODUCT_PATTERN.test(resolved) || LOCAL_PACKAGE_PATTERN.test(entry.resolved) || (entry.link === true && !approvedWorkspaceLink);
      if (!approvedWorkspaceLink && forbiddenResolved) {
        violations.push(violation("PACKAGE_DAON_INTERNAL", file, 1, "Lockfile의 승인되지 않은 저장소 경로 Package를 제거하십시오.", `${lockPath}: ${entry.resolved}`));
      }
    }
  }
}

async function inspectPackages(root, components, violations, policy) {
  const packageNames = new Map();
  const manifests = [];
  for (const component of components) {
    const jsonPath = path.join(root, component.path, "package.json");
    const pyPath = path.join(root, component.path, "pyproject.toml");
    if (await exists(jsonPath)) {
      const data = await readJson(jsonPath, `${component.path}/package.json`);
      manifests.push({ component, file: `${component.path}/package.json`, type: "npm", data });
      if (data.name) packageNames.set(data.name, component.id);
    } else if (await exists(pyPath)) {
      manifests.push({ component, file: `${component.path}/pyproject.toml`, type: "python", text: await readFile(pyPath, "utf8") });
    } else {
      addGraphViolation(violations, component.path, `등록 구성요소 ${component.id}에 package.json 또는 pyproject.toml이 없습니다.`);
    }
  }
  const edges = [];
  for (const manifest of manifests) {
    if (manifest.type === "npm") {
      inspectNpmDependencies(manifest.file, manifest.data, violations, (name, spec, key) => {
        const target = packageNames.get(name);
        if (target) edges.push({ from: manifest.component.id, to: target, package: name, dependency_type: key });
      });
    } else {
      manifest.text.split(/\r?\n/).forEach((line, index) => {
        if (DAON_PRODUCT_PATTERN.test(line) || /(?:path|url)\s*=/.test(line)) violations.push(violation("PACKAGE_DAON_INTERNAL", manifest.file, index + 1, "Python Package의 다른 저장소 직접 의존을 제거하십시오.", line));
      });
    }
  }
  const rootManifestPath = path.join(root, policy.root_package_manifest);
  if (!(await exists(rootManifestPath))) throw new PolicyError(`${policy.root_package_manifest}이 없습니다.`);
  const rootManifest = await readJson(rootManifestPath, policy.root_package_manifest);
  inspectNpmDependencies(policy.root_package_manifest, rootManifest, violations);

  const lockfilePath = path.join(root, policy.lockfile);
  if (!(await exists(lockfilePath))) throw new PolicyError(`승인 기준 ${policy.lockfile}이 없습니다.`);
  const lockfile = await readJson(lockfilePath, policy.lockfile);
  inspectLockfile(policy.lockfile, lockfile, components, violations);
  return { manifests, packageNames, edges, packageStructureFileCount: manifests.length + 2 };
}

function validateGraph(boundaries, edges, violations, rootEntries) {
  const components = boundaries.components;
  const ids = new Set(components.map((item) => item.id));
  for (const item of rootEntries) if (!ids.has(item)) addGraphViolation(violations, "repo-boundaries.json", `미등록 구성요소 ${item}를 repo-boundaries.json에 등록하십시오.`);
  for (const edge of edges) {
    const owner = components.find((item) => item.id === edge.from);
    if (edge.from === edge.to) addGraphViolation(violations, `${owner.path}/package.json`, `${edge.from} 자기 의존은 허용되지 않습니다.`);
    if (owner.forbidden_dependencies.includes(edge.to)) addGraphViolation(violations, `${owner.path}/package.json`, `${edge.from} -> ${edge.to}는 forbidden_dependencies 위반입니다.`);
    else if (!owner.allowed_dependencies.includes(edge.to)) addGraphViolation(violations, `${owner.path}/package.json`, `${edge.from} -> ${edge.to}는 allowed_dependencies 밖입니다.`);
  }
  const cycles = findCycles([...ids], edges);
  for (const cycle of cycles) addGraphViolation(violations, "repo-boundaries.json", `순환 의존: ${cycle.join(" -> ")}`);
  return cycles;
}

async function discoverComponentRoots(root) {
  const output = [];
  for (const group of ["apps", "services", "packages"]) {
    const dir = path.join(root, group);
    if (!(await exists(dir))) continue;
    for (const entry of await readdir(dir, { withFileTypes: true })) if (entry.isDirectory() && entry.name !== "node_modules") output.push(`${group}/${entry.name}`);
  }
  return output;
}

function resolveImportComponent(file, specifier, components, packageNames) {
  if (packageNames.has(specifier)) return packageNames.get(specifier);
  if (!specifier.startsWith(".")) return null;
  const resolved = normalize(path.posix.normalize(path.posix.join(path.posix.dirname(normalize(file)), specifier)));
  return componentFor(resolved, components)?.id || null;
}

function pythonImportSpecifiers(text) {
  const imports = [];
  let tripleQuote = null;
  let offset = 0;
  for (const rawLine of text.split(/\r?\n/)) {
    let line = rawLine;
    if (tripleQuote) {
      const close = line.indexOf(tripleQuote);
      if (close < 0) { offset += rawLine.length + 1; continue; }
      line = line.slice(close + 3);
      tripleQuote = null;
    }
    const triple = line.match(/'''|"""/);
    if (triple) {
      const afterStart = line.slice(triple.index + 3);
      const close = afterStart.indexOf(triple[0]);
      if (close < 0) {
        line = line.slice(0, triple.index);
        tripleQuote = triple[0];
      } else {
        line = `${line.slice(0, triple.index)} ${afterStart.slice(close + 3)}`;
      }
    }
    const statement = line.replace(/#.*$/, "");
    const fromMatch = statement.match(/^\s*from\s+([A-Za-z_][\w.]*)\s+import\b/);
    if (fromMatch) imports.push({ specifier: fromMatch[1], index: offset + statement.indexOf(fromMatch[1]), source: fromMatch[0] });
    const importMatch = statement.match(/^\s*import\s+(.+)$/);
    if (importMatch) {
      for (const item of importMatch[1].split(",")) {
        const specifier = item.trim().split(/\s+/)[0];
        if (specifier) imports.push({ specifier, index: offset + statement.indexOf(specifier), source: item.trim() });
      }
    }
    offset += rawLine.length + 1;
  }
  return imports;
}

function addImportViolation(file, text, match, policy, components, packageNames, violations) {
  const current = componentFor(file, components);
  const specifier = match.specifier;
  const target = resolveImportComponent(file, specifier, components, packageNames);
  if (DAON_PRODUCT_PATTERN.test(specifier) || (current && target && current.id !== target)) {
    const allowedPackage = packageNames.has(specifier) && current?.allowed_dependencies.includes(target);
    if (!allowedPackage) violations.push(violation("SOURCE_IMPORT_BOUNDARY", file, lineNumber(text, match.index), "구성요소 Source 직접 Import를 공개 Package 계약으로 변경하십시오.", match.source));
  }
  if (!normalize(file).startsWith(`${policy.approved_connector_prefix}/`) && CONNECTOR_PATTERN.test(specifier)) {
    violations.push(violation("CONNECTOR_BYPASS", file, lineNumber(text, match.index), "Daon 접근은 services/api의 승인 Connector Adapter를 사용하십시오.", match.source));
  }
}

function inspectGeneralFile(file, text, policy, components, packageNames, violations) {
  const ext = path.extname(file).toLowerCase();
  if (JS_CODE_EXTENSIONS.has(ext)) {
    for (const match of text.matchAll(JS_IMPORT_PATTERN)) addImportViolation(file, text, { specifier: match[1], index: match.index, source: match[0] }, policy, components, packageNames, violations);
  } else if (ext === ".py") {
    for (const match of pythonImportSpecifiers(text)) addImportViolation(file, text, match, policy, components, packageNames, violations);
  }
  text.split(/\r?\n/).forEach((line, index) => {
    if (DAON_PATH_PATTERN.test(line) || ABSOLUTE_PATH_PATTERN.test(line)) violations.push(violation("PATH_EXTERNAL_ABSOLUTE", file, index + 1, "실행 Source/설정의 외부 절대 경로를 제거하십시오.", line));
    const lower = file.toLowerCase();
    if ((path.basename(lower).startsWith("dockerfile") || /(?:compose|\.github\/workflows)/.test(lower)) && /(?:^\s*from\s+|\bimage\s*:).*daon(?:2(?:\.5)?|3)/i.test(line)) {
      violations.push(violation("RUNTIME_IMAGE_DAON", file, index + 1, "다른 Daon 제품 Runtime Image를 제거하십시오.", line));
    }
    if (classifyRuntime(file, text, policy) === "browser" && DIRECT_URL_PATTERN.test(line)) {
      violations.push(violation("BROWSER_DIRECT_API", file, index + 1, "Browser API 호출은 same-origin 상대 경로를 사용하십시오.", line));
    }
    if (!normalize(file).startsWith(`${policy.approved_connector_prefix}/`) && CONNECTOR_PATTERN.test(line) && !/^\s*(?:\/\/|#|\*)/.test(line)) {
      if (!violations.some((item) => item.rule_id === "CONNECTOR_BYPASS" && item.file === normalize(file) && item.line === index + 1)) violations.push(violation("CONNECTOR_BYPASS", file, index + 1, "Daon 접근은 services/api의 승인 Connector Adapter를 사용하십시오.", line));
    }
  });
}

export async function runIndependenceCheck({ root, policy }) {
  const boundaryPath = path.join(root, policy.boundary_manifest);
  const boundaries = await readJson(boundaryPath, "repo-boundaries");
  if (!Array.isArray(boundaries.components) || boundaries.components.length === 0) throw new PolicyError("repo-boundaries.json components가 비어 있습니다.");
  const violations = [];
  const components = boundaries.components.map((item) => ({ ...item, path: normalize(item.path) }));
  const { packageNames, edges, packageStructureFileCount } = await inspectPackages(root, components, violations, policy);
  const rootEntries = await discoverComponentRoots(root);
  const cycles = validateGraph({ components }, edges, violations, rootEntries);
  const files = [];
  for (const scanRoot of policy.scan_roots) await walk(root, scanRoot, policy, files);
  for (const rootFile of policy.root_runtime_files || []) if (await exists(path.join(root, rootFile))) files.push(rootFile);
  const exclusions = new Set(policy.general_scan_exclusions.map(normalize));
  const scanFiles = files.filter((file) => !exclusions.has(file) && !policy.package_manifest_names.includes(path.basename(file)) && (policy.source_extensions.includes(path.extname(file).toLowerCase()) || /(?:dockerfile|compose)/i.test(path.basename(file))));
  for (const file of scanFiles) inspectGeneralFile(file, await readFile(path.join(root, file), "utf8"), policy, components, packageNames, violations);
  violations.sort((a, b) => a.rule_id.localeCompare(b.rule_id) || a.file.localeCompare(b.file) || a.line - b.line);
  const graph = {
    schema_version: "1.0",
    component_count: components.length,
    edge_count: edges.length,
    cycle_count: cycles.length,
    package_structure_file_count: packageStructureFileCount,
    components: components.map(({ id, path: componentPath, kind, runtime, allowed_dependencies, forbidden_dependencies }) => ({ id, path: componentPath, kind, runtime, allowed_dependencies, forbidden_dependencies })),
    edges,
    cycles
  };
  return { graph, violations, stats: { scanned_files: scanFiles.length, manifest_count: packageStructureFileCount, component_count: components.length, edge_count: edges.length } };
}
