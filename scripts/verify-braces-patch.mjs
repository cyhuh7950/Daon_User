import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { lstat, readFile, readdir, realpath } from "node:fs/promises";
import path from "node:path";
import { gunzipSync } from "node:zlib";
import { fileURLToPath } from "node:url";

const archiveSpec = "file:vendor/braces-3.0.3.tgz";
const archiveName = "vendor/braces-3.0.3.tgz";
const sha512 = (bytes) => `sha512-${createHash("sha512").update(bytes).digest("base64")}`;

async function filesUnder(root) {
  const found = new Map();
  async function visit(directory, prefix = "") {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) await visit(absolute, relative);
      else if (entry.isFile()) found.set(relative, await readFile(absolute));
      else throw new Error(`source contains unsupported entry: ${relative}`);
    }
  }
  await visit(root);
  return found;
}

function archiveFiles(archive) {
  const data = gunzipSync(archive);
  const files = new Map();
  let offset = 0;
  while (offset + 512 <= data.length) {
    const header = data.subarray(offset, offset + 512);
    if (header.every((byte) => byte === 0)) break;
    const name = header.subarray(0, 100).toString("utf8").replace(/\0.*$/, "");
    const rawSize = header.subarray(124, 136).toString("ascii").replace(/\0.*$/, "").trim();
    const size = Number.parseInt(rawSize, 8);
    const type = header[156];
    if (!Number.isSafeInteger(size) || size < 0 || !name.startsWith("package/")) throw new Error("source archive format mismatch");
    const start = offset + 512;
    if (start + size > data.length) throw new Error("source archive truncated");
    if (type === 0 || type === 48) files.set(name.slice(8), data.subarray(start, start + size));
    offset = start + Math.ceil(size / 512) * 512;
  }
  return files;
}

function assertFilesMatch(expected, actual, label) {
  if (expected.size !== actual.size) throw new Error(`${label} file list mismatch`);
  for (const [name, content] of expected) {
    if (!actual.has(name) || !actual.get(name).equals(content)) throw new Error(`${label} file mismatch: ${name}`);
  }
}

async function physicalInstallPaths(root) {
  const installs = new Set();
  const pending = [path.join(root, "node_modules")];
  const visited = new Set();
  for (const workspace of ["apps", "packages"]) {
    const parent = path.join(root, workspace);
    try {
      for (const entry of await readdir(parent, { withFileTypes: true })) {
        if (entry.isDirectory()) pending.push(path.join(parent, entry.name, "node_modules"));
      }
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  while (pending.length) {
    const moduleDir = pending.pop();
    let canonical;
    try {
      const info = await lstat(moduleDir);
      canonical = info.isSymbolicLink() ? await realpath(moduleDir) : path.resolve(moduleDir);
      if (!info.isDirectory() && !info.isSymbolicLink()) continue;
    }
    catch (error) { if (error.code === "ENOENT") continue; else throw error; }
    if (visited.has(canonical)) continue;
    visited.add(canonical);
    const entries = await readdir(moduleDir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isDirectory() && !entry.isSymbolicLink()) continue;
      const packageDirs = entry.name.startsWith("@")
        ? (await readdir(path.join(moduleDir, entry.name), { withFileTypes: true })).map((child) => path.join(moduleDir, entry.name, child.name))
        : [path.join(moduleDir, entry.name)];
      for (const packageDir of packageDirs) {
        if (path.basename(packageDir) === "braces") installs.add(path.resolve(packageDir));
        pending.push(path.join(packageDir, "node_modules"));
      }
    }
  }
  return [...installs].sort();
}

function inspectTree(tree) {
  if (!tree || typeof tree !== "object" || (tree.problems?.length ?? 0) > 0) throw new Error("npm tree invalid");
  let found = 0;
  function visit(node) {
    for (const [name, child] of Object.entries(node.dependencies ?? {})) {
      if (name === "braces") {
        found++;
        if (child.version !== "3.0.3") throw new Error("npm tree contains wrong braces version");
      }
      visit(child);
    }
  }
  visit(tree);
  if (found === 0) throw new Error("npm tree has no braces");
}

export async function verifyBracesPatch({ root, npmTree, npmPaths }) {
  const manifest = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));
  if (manifest.overrides?.braces !== archiveSpec || manifest.dependencies?.braces !== archiveSpec)
    throw new Error("braces override or root dependency mismatch");
  const lock = JSON.parse(await readFile(path.join(root, "package-lock.json"), "utf8"));
  const lockEntries = Object.entries(lock.packages ?? {}).filter(([name]) => name === "node_modules/braces" || name.endsWith("/node_modules/braces"));
  if (!lockEntries.length || lock.packages[""]?.dependencies?.braces !== archiveSpec)
    throw new Error("braces lock root dependency mismatch");
  const archive = await readFile(path.join(root, archiveName));
  const integrity = sha512(archive);
  for (const [, entry] of lockEntries) {
    if (entry.resolved !== archiveSpec || entry.integrity !== integrity || entry.link === true)
      throw new Error("braces lock integrity mismatch");
  }
  inspectTree(npmTree);
  const source = await filesUnder(path.join(root, "vendor/braces"));
  const packed = archiveFiles(archive);
  assertFilesMatch(source, packed, "source archive");
  if (!source.has("LICENSE") || !source.get("LICENSE").toString("utf8").includes("MIT License"))
    throw new Error("source license missing");
  const physical = await physicalInstallPaths(root);
  const declared = [...new Set(npmPaths.map((item) => path.resolve(item)))].sort();
  if (!physical.length || JSON.stringify(physical) !== JSON.stringify(declared) || physical.length !== lockEntries.length)
    throw new Error("duplicate or unlisted braces installation");
  for (const install of physical) {
    const info = await lstat(install);
    const canonical = info.isSymbolicLink() ? await realpath(install) : path.resolve(install);
    const relative = path.relative(root, canonical);
    if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative))
      throw new Error("installed braces escapes workspace");
    const installed = await filesUnder(install);
    assertFilesMatch(packed, installed, "installed braces");
  }
  return { fileCount: source.size, installCount: physical.length, integrity };
}

function npmLs(root, format) {
  if (format !== "--json" && format !== "--parseable") throw new Error("unsupported npm output format");
  const command = process.platform === "win32" ? "cmd.exe" : "npm";
  const args = process.platform === "win32"
    ? ["/d", "/s", "/c", `npm ls braces --all ${format}`]
    : ["ls", "braces", "--all", format];
  const result = spawnSync(command, args, {
    cwd: root, encoding: "utf8", timeout: 30_000, maxBuffer: 4_000_000
  });
  if (result.error || result.status !== 0) throw new Error("npm tree command failed");
  return result.stdout;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  try {
    const npmTree = JSON.parse(npmLs(root, "--json"));
    const npmPaths = npmLs(root, "--parseable").trim().split(/\r?\n/).filter(Boolean);
    const result = await verifyBracesPatch({ root, npmTree, npmPaths });
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    process.stderr.write(`braces patch verification failed: ${error.message}\n`);
    process.exitCode = 1;
  }
}
