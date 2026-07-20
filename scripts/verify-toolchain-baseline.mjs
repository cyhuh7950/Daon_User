import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";

const read = (path) => readFileSync(path, "utf8");
const load = (path) => JSON.parse(read(path));
const errors = [];
const assert = (ok, message) => { if (!ok) errors.push(message); };
const baseline = load("toolchain-versions.json");
const t = baseline.toolchains;
const f = baseline.frameworks;

for (const [path, expected] of Object.entries({
  ".node-version": t.node,
  ".python-version": t.python,
  ".postgres-version": t.postgresql,
  ".xcode-version": t.xcode,
  ".cocoapods-version": t.cocoapods
})) assert(read(path).trim() === expected, `${path} mismatch`);

for (const line of [`nodejs ${t.node}`, `python ${t.python}`, `uv ${t.uv}`, `rust ${t.rust}`, `postgres ${t.postgresql}`])
  assert(read(".tool-versions").split(/\r?\n/).includes(line), `.tool-versions missing ${line}`);
assert(read("rust-toolchain.toml").includes(`channel = "${t.rust}"`), "Rust pin mismatch");
for (const line of ["save-exact=true", "engine-strict=true", "ignore-scripts=true"])
  assert(read(".npmrc").split(/\r?\n/).includes(line), `.npmrc missing ${line}`);

const manifests = ["package.json", "apps/web/package.json", "apps/desktop/package.json", "apps/mobile/package.json", "packages/ui/package.json", "packages/contracts/package.json", "packages/design-tokens/package.json"];
const packages = new Map(manifests.map((path) => [path, load(path)]));
for (const [path, pkg] of packages) {
  assert(pkg.private === true, `${path} is not private`);
  for (const group of ["dependencies", "devDependencies", "peerDependencies", "optionalDependencies"])
    for (const [name, version] of Object.entries(pkg[group] ?? {}))
      assert(!/^[~^*]/.test(version) && !/(^|[.-])(latest|x)([.-]|$)/i.test(version), `${path}:${name} uses ${version}`);
}

const root = packages.get("package.json");
assert(root.packageManager === `npm@${t.npm}`, "npm pin mismatch");
assert(root.engines.node === t.node && root.engines.npm === t.npm, "Node engine mismatch");
assert(JSON.stringify(root.workspaces) === JSON.stringify(baseline.workspaces.npm), "npm workspace mismatch");
assert(root.devDependencies.typescript === f.typescript, "TypeScript mismatch");
assert(packages.get("apps/web/package.json").dependencies.next === f.next, "Next mismatch");
assert(packages.get("apps/mobile/package.json").dependencies["react-native"] === f.react_native, "React Native mismatch");
assert(packages.get("apps/desktop/package.json").devDependencies["@tauri-apps/cli"] === f.tauri_cli, "Tauri CLI mismatch");
assert(!root.workspaces.some((item) => item.startsWith("services/")), "npm owns services");

for (const path of ["pyproject.toml", "services/api/pyproject.toml", "services/local-service/pyproject.toml"])
  assert(read(path).includes(`requires-python = "==${t.python}"`), `${path} Python mismatch`);
for (const member of baseline.workspaces.uv) assert(read("pyproject.toml").includes(`"${member}"`), `uv workspace missing ${member}`);

assert(existsSync("package-lock.json"), "package-lock.json missing");
if (existsSync("package-lock.json")) {
  const lock = load("package-lock.json");
  assert(lock.lockfileVersion === 3, "package-lock lockfileVersion mismatch");
  for (const path of manifests.slice(1).map((item) => item.replace(/\/package\.json$/, ""))) assert(lock.packages?.[path], `lock missing ${path}`);
}
assert(existsSync("uv.lock"), "uv.lock missing");
if (existsSync("uv.lock")) assert(read("uv.lock").includes(`requires-python = "==${t.python}"`), "uv.lock Python mismatch");

const version = (command) => {
  const output = process.platform === "win32" && ["npm", "corepack"].includes(command)
    ? execFileSync(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", `${command} --version`], { encoding: "utf8" })
    : execFileSync(command, ["--version"], { encoding: "utf8" });
  return output.trim().replace(/^v/, "");
};
assert(version("node") === t.node, "runtime Node mismatch");
assert(version("npm") === t.npm, "runtime npm mismatch");
assert(version("corepack") === t.corepack, "runtime Corepack mismatch");
assert(version("uv").split(" ")[1] === t.uv, "runtime uv mismatch");

if (errors.length) { console.error(errors.join("\n")); process.exit(1); }
console.log(`toolchain baseline verified: ${manifests.length} npm manifests, exact pins, lockfiles`);
