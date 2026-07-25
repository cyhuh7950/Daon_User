import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputPath = path.join(
  repositoryRoot,
  "docs",
  "03_evidence",
  "release_1",
  "R1-M3-03",
  "source-manifest.json"
);

const excludedPrefixes = [
  "apps/desktop/dist/",
  "apps/desktop/src-tauri/binaries/",
  "docs/02_work_orders/reports/R1-M3-03",
  "docs/03_evidence/release_1/R1-M3-03/",
  "docs/04_test_reports/release_1/R1-M3-03"
];

function sha256(value) {
  return createHash("sha256").update(value).digest("hex").toUpperCase();
}

function assertRepositoryRelative(candidate) {
  if (
    !candidate
    || path.isAbsolute(candidate)
    || /^[A-Za-z]:/u.test(candidate)
    || candidate.split("/").includes("..")
  ) {
    throw new Error(`source path must be repository-relative: ${candidate}`);
  }
}

export function canonicalSourceEntries(entries) {
  return entries
    .map((entry) => {
      assertRepositoryRelative(entry.path);
      return {
        path: entry.path.replaceAll("\\", "/"),
        bytes: entry.bytes,
        sha256: entry.sha256.toUpperCase()
      };
    })
    .sort((left, right) => left.path.localeCompare(right.path, "en"));
}

export function sourceEntriesHash(entries) {
  return sha256(Buffer.from(JSON.stringify(canonicalSourceEntries(entries)), "utf8"));
}

function git(args, encoding = "utf8") {
  const result = spawnSync("git", args, {
    cwd: repositoryRoot,
    encoding,
    windowsHide: true,
    maxBuffer: 128 * 1024 * 1024
  });
  if (result.error || result.status !== 0) {
    throw new Error(`git ${args.join(" ")} failed with ${result.status ?? "spawn"}`);
  }
  return result.stdout;
}

function sourcePaths() {
  const tracked = git(["diff", "--name-only", "HEAD", "--"])
    .split(/\r?\n/u)
    .filter(Boolean);
  const untracked = git(["ls-files", "--others", "--exclude-standard", "--"])
    .split(/\r?\n/u)
    .filter(Boolean);
  return [...new Set([...tracked, ...untracked])]
    .map((candidate) => candidate.replaceAll("\\", "/"))
    .filter(
      (candidate) =>
        !excludedPrefixes.some((prefix) => candidate.startsWith(prefix))
    )
    .sort((left, right) => left.localeCompare(right, "en"));
}

export async function generateSourceManifest() {
  const files = canonicalSourceEntries(
    await Promise.all(
      sourcePaths().map(async (relativePath) => {
        assertRepositoryRelative(relativePath);
        const bytes = await readFile(path.join(repositoryRoot, relativePath));
        return {
          path: relativePath,
          bytes: bytes.length,
          sha256: sha256(bytes)
        };
      })
    )
  );
  const patch = git(["diff", "--binary", "HEAD", "--"], null);
  const manifest = {
    schema_version: "1.0",
    status: "SNAPSHOT",
    dirty_snapshot: true,
    base_head: git(["rev-parse", "HEAD"]).trim(),
    tracked_binary_patch_sha256: sha256(patch),
    untracked_binding: "file entries include untracked source bytes individually",
    entry_canonicalization: "UTF-8 JSON.stringify(path,bytes,sha256), path ascending",
    source_entries_sha256: sourceEntriesHash(files),
    file_count: files.length,
    files
  };
  await writeFile(outputPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  return manifest;
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  generateSourceManifest()
    .then((manifest) => {
      console.log(
        JSON.stringify({
          output: path.relative(repositoryRoot, outputPath).replaceAll("\\", "/"),
          file_count: manifest.file_count,
          source_entries_sha256: manifest.source_entries_sha256,
          tracked_binary_patch_sha256: manifest.tracked_binary_patch_sha256
        })
      );
    })
    .catch((error) => {
      console.error(`SOURCE_MANIFEST_ERROR ${error.message}`);
      process.exitCode = 1;
    });
}
