import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");

const publicManualRoots = [
  "docs/manual/",
  "apps/web/public/manual/",
];

const documentExtensions = new Set([
  ".md",
  ".mdx",
  ".docx",
  ".pdf",
  ".odt",
  ".rtf",
]);

function isPublicManual(relativePath) {
  return publicManualRoots.some((prefix) => relativePath.startsWith(prefix));
}

function isNonManualDocument(relativePath) {
  if (isPublicManual(relativePath)) {
    return false;
  }

  if (relativePath.startsWith("docs/")) {
    return true;
  }

  return documentExtensions.has(path.posix.extname(relativePath).toLowerCase());
}

test("public Git tracks manuals but excludes every other document and keystore", () => {
  const tracked = execFileSync("git", ["ls-files", "-z"], {
    cwd: root,
    encoding: "utf8",
  })
    .split("\0")
    .filter(Boolean);

  const forbidden = tracked.filter(
    (relativePath) =>
      isNonManualDocument(relativePath) ||
      relativePath.toLowerCase().endsWith(".keystore") ||
      relativePath.toLowerCase().endsWith(".jks"),
  );

  assert.deepEqual(
    forbidden,
    [],
    `public repository contains non-manual documents or keystores:\n${forbidden.join("\n")}`,
  );

  assert.ok(
    tracked.some((relativePath) => relativePath.startsWith("docs/manual/")),
    "canonical manuals must remain tracked",
  );
  assert.ok(
    tracked.some((relativePath) =>
      relativePath.startsWith("apps/web/public/manual/"),
    ),
    "web-published manuals must remain tracked",
  );
});

test("public repository boundary is a mandatory security gate", () => {
  const packageJson = JSON.parse(
    readFileSync(path.join(root, "package.json"), "utf8"),
  );
  assert.equal(
    packageJson.scripts["verify:public-repository-boundary"],
    "node --test scripts/tests/public-repository-boundary.test.mjs",
  );

  const policy = JSON.parse(
    readFileSync(path.join(root, "quality-gate-policy.json"), "utf8"),
  );
  assert.deepEqual(
    policy.mandatory_checks.find(
      (check) => check.id === "public-repository-boundary",
    ),
    {
      id: "public-repository-boundary",
      category: "security",
      command: ["npm", "run", "verify:public-repository-boundary"],
      failure_kind: "quality",
      evidence_files: [
        ".gitignore",
        "scripts/tests/public-repository-boundary.test.mjs",
      ],
    },
  );

  const runner = readFileSync(
    path.join(root, "scripts/lib/quality-gate.mjs"),
    "utf8",
  );
  assert.match(
    runner,
    /\["public-repository-boundary", \{ category: "security", kind: null \}\]/u,
  );
});
