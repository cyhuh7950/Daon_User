import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
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

// Hashes pin the reviewed Git bytes (LF); checkout CRLF is normalized below.
// Changing content requires review and an intentional update to its entry.
const reviewedHistoricalDocuments = new Map([
  ["design_change.md", "0dee345fd2a54de5d87053f8b8ef61921cb4180e3f9fbf03c63221b1d8ad957d"],
  ["docs/04_test_reports/release_1/R1-DEFAULT-PROVIDERS_progress.md", "5fc82974583364d1084b2644d9115ca053c6051b4f33381e360aef4b44f1efc7"],
  ["docs/04_test_reports/release_1/R1-PROVIDER-COMPATIBILITY_progress.md", "48afc3fa619918fcafa0346878c7689ea7ac86b4b85571e41993ebab6a9f4ec0"],
  ["docs/04_test_reports/release_1/R1-PROVIDER-CONNECTION-REDESIGN_developer.md", "d2455dfa33afe6b49f1794c99a452de595e06374dfc4f98b6cd04725e2e06036"],
  ["docs/04_test_reports/release_1/R1-ROUTE-DEFAULT-main-qa.md", "8c7714117ce3dbd9f75537e2bcbb350af759c5f05ae073ed13abcf27d90d67ac"],
  ["docs/04_test_reports/release_1/R1-ROUTE-MODELS_progress.md", "906873715820c1ea9c066abe0684490215f4106712c13990c6d0e1ff41cf9ab2"],
  ["docs/04_test_reports/release_1/R1-ROUTER-AUTO_progress.md", "4f73c0209249476da18e154bc5782e5ce15ca290417230a32ba0d48ee0e954db"],
  ["docs/DEVELOPMENT_ENVIRONMENT.md", "95850762af2422d4031e4c39a4f99712cd8e3520a147410d816641dc0a68fee7"],
  ["docs/superpowers/plans/2026-09-19-provider-health-monitor.md", "0f677e99b83d3ff812be52660d00b946b7a859376a5d61e845aa8fd34225c778"],
  ["docs/superpowers/plans/2026-09-20-user-management-settings-ui.md", "4d333d67fc1a3e8a383d2c873c104dbadd48c9b10583494b54ab3350fa095f7b"],
  ["docs/superpowers/plans/2026-10-03-default-provider-templates.md", "ffa21958d89f88e79ca3b3a259b75e8227cc59cf06d5562152a0e2eafc397340"],
  ["docs/superpowers/plans/2026-10-03-provider-compatible-protocols.md", "f2cc7755b33d15c8f478943c1d6f97e6b31b929cd1c7662a18fa3ec34415c8a9"],
  ["docs/superpowers/plans/2026-10-03-provider-connection-access.md", "979364dd5ccda1e7fe330d620dedfd0daed403f565949f8c854f8b8d5efb5e61"],
  ["docs/superpowers/plans/2026-10-04-router-auto-model.md", "4be8de4b0845d5a08570730885fb82874035c6cbbe7f77a42a5894f49a62a659"],
  ["docs/superpowers/specs/2026-09-19-provider-health-monitor-design.md", "4bb5a2d707f7b6de2bee9203fa8b77682c86f385c193017097756e3fc574bd6c"],
  ["docs/superpowers/specs/2026-09-20-user-management-and-settings-ui-design.md", "5523a7b079dc1fe32c156f181f41ec9240056f3ba60e525ce6d528a9d6616f43"],
  ["docs/superpowers/specs/2026-10-03-provider-compatible-protocols-design.md", "d3a55dbb5b8cfa2f27ab2e8dc4a38ed600d7d185fdc1fe93646e946d6f943239"],
  ["docs/superpowers/specs/2026-10-03-provider-connection-access-design.md", "17c7957e1d84c510d95da81bed98740d620dd7c276ad89ba078e00b482c42972"],
  ["docs/superpowers/specs/2026-10-04-router-auto-model-design.md", "ee596ad658c2a3665d5cbaa2297839a57bf50c4f9b9171663d894f60999fa5a7"],
]);

function isPublicManual(relativePath) {
  return publicManualRoots.some((prefix) => relativePath.startsWith(prefix));
}

function readReviewedWorkingOrIndexBytes(relativePath) {
  try {
    return readFileSync(path.join(root, relativePath));
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    // A locally removed file can still be tracked in the index. Verify the
    // exact reviewed index bytes without restoring the user's working tree.
    return execFileSync("git", ["show", `:${relativePath}`], { cwd: root });
  }
}

function isNonManualDocument(relativePath, contentBytes, stagedBytes) {
  const lowerCasePath = relativePath.toLowerCase();
  if (lowerCasePath.endsWith(".keystore") || lowerCasePath.endsWith(".jks")) {
    return true;
  }
  if (reviewedHistoricalDocuments.has(relativePath)) {
    const expectedHash = reviewedHistoricalDocuments.get(relativePath);
    const workingContent = contentBytes ?? readReviewedWorkingOrIndexBytes(relativePath);
    const canonicalContent = Buffer.from(workingContent.toString("utf8").replace(/\r\n/g, "\n"));
    const stagedContent = stagedBytes ?? (contentBytes === undefined
      ? execFileSync("git", ["show", `:${relativePath}`], { cwd: root })
      : canonicalContent);
    return [canonicalContent, stagedContent].some(
      (bytes) => createHash("sha256").update(bytes).digest("hex") !== expectedHash,
    );
  }
  if (isPublicManual(relativePath)) {
    return false;
  }

  if (relativePath.startsWith("docs/")) {
    return true;
  }

  return documentExtensions.has(path.posix.extname(relativePath).toLowerCase());
}

test("only the 19 reviewed document paths are grandfathered", () => {
  const reviewed = [
    "design_change.md",
    "docs/04_test_reports/release_1/R1-DEFAULT-PROVIDERS_progress.md",
    "docs/04_test_reports/release_1/R1-PROVIDER-COMPATIBILITY_progress.md",
    "docs/04_test_reports/release_1/R1-PROVIDER-CONNECTION-REDESIGN_developer.md",
    "docs/04_test_reports/release_1/R1-ROUTE-DEFAULT-main-qa.md",
    "docs/04_test_reports/release_1/R1-ROUTE-MODELS_progress.md",
    "docs/04_test_reports/release_1/R1-ROUTER-AUTO_progress.md",
    "docs/DEVELOPMENT_ENVIRONMENT.md",
    "docs/superpowers/plans/2026-09-19-provider-health-monitor.md",
    "docs/superpowers/plans/2026-09-20-user-management-settings-ui.md",
    "docs/superpowers/plans/2026-10-03-default-provider-templates.md",
    "docs/superpowers/plans/2026-10-03-provider-compatible-protocols.md",
    "docs/superpowers/plans/2026-10-03-provider-connection-access.md",
    "docs/superpowers/plans/2026-10-04-router-auto-model.md",
    "docs/superpowers/specs/2026-09-19-provider-health-monitor-design.md",
    "docs/superpowers/specs/2026-09-20-user-management-and-settings-ui-design.md",
    "docs/superpowers/specs/2026-10-03-provider-compatible-protocols-design.md",
    "docs/superpowers/specs/2026-10-03-provider-connection-access-design.md",
    "docs/superpowers/specs/2026-10-04-router-auto-model-design.md",
  ];

  assert.equal(reviewed.length, 19);
  assert.deepEqual([...reviewedHistoricalDocuments.keys()], reviewed);
  for (const relativePath of reviewed) {
    assert.equal(
      isNonManualDocument(relativePath, readReviewedWorkingOrIndexBytes(relativePath)),
      false,
      relativePath,
    );
  }
  for (const relativePath of [
    "docs/superpowers/plans/new-plan.md",
    "docs/04_test_reports/release_1/R1-ROUTER-AUTO_progress-copy.md",
    "docs/DEVELOPMENT_ENVIRONMENT-copy.md",
    "design_change-copy.md",
    "docs/manual/example.keystore",
    "apps/web/public/manual/example.jks",
  ]) {
    assert.equal(isNonManualDocument(relativePath, Buffer.from("example")), true, relativePath);
  }
  assert.equal(isNonManualDocument("docs/manual/example.md"), false);
  assert.equal(isNonManualDocument("apps/web/public/manual/example.pdf"), false);
});

test("a content change to any grandfathered document requires review", () => {
  for (const relativePath of reviewedHistoricalDocuments.keys()) {
    const current = readReviewedWorkingOrIndexBytes(relativePath);
    const changed = Buffer.concat([current, Buffer.from("\nreview-required\n")]);
    assert.equal(isNonManualDocument(relativePath, changed), true, relativePath);
  }
});

test("a staged change cannot hide behind unchanged working content", () => {
  const relativePath = "docs/DEVELOPMENT_ENVIRONMENT.md";
  const current = readFileSync(path.join(root, relativePath));
  const staged = Buffer.concat([Buffer.from(current.toString("utf8").replace(/\r\n/g, "\n")), Buffer.from("\nreview-required\n")]);
  assert.equal(isNonManualDocument(relativePath, current, staged), true);
});

test("the same Git document bytes pass with LF checkout line endings", () => {
  const relativePath = "docs/DEVELOPMENT_ENVIRONMENT.md";
  const canonical = Buffer.from(readFileSync(path.join(root, relativePath)).toString("utf8").replace(/\r\n/g, "\n"));
  assert.equal(isNonManualDocument(relativePath, canonical, canonical), false);
});

test("public Git tracks manuals but excludes every other document and keystore", () => {
  const tracked = execFileSync("git", ["ls-files", "-z"], {
    cwd: root,
    encoding: "utf8",
  })
    .split("\0")
    .filter(Boolean);

  const forbidden = tracked.filter(
    (relativePath) =>
      isNonManualDocument(relativePath),
  );

  const trackedSet = new Set(tracked);
  assert.deepEqual(
    [...reviewedHistoricalDocuments.keys()].filter((relativePath) => !trackedSet.has(relativePath)),
    [],
    "reviewed historical documents must remain tracked",
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
