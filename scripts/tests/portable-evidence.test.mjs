import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const portable = await import("../lib/portable-evidence.mjs").catch(() => ({}));
const missing = () => assert.fail("portable evidence validator is not implemented");
const digestBytes = portable.digestBytes ?? missing;
const digestFile = portable.digestFile ?? missing;
const validateEvidenceManifest = portable.validateEvidenceManifest ?? missing;
const verifyEvidenceManifestFile = portable.verifyEvidenceManifestFile ?? missing;
const sha256 = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex").toUpperCase();

test("portable_utf8_lf는 같은 UTF-8 LF·CRLF Text를 동일 Hash·Byte로 만든다", () => {
  const lf = Buffer.from("첫 줄\nsecond\n", "utf8");
  const crlf = Buffer.from("첫 줄\r\nsecond\r\n", "utf8");
  const expected = { representation: "portable_utf8_lf", bytes: lf.length, sha256: sha256(lf) };
  assert.deepEqual(digestBytes(lf, "portable_utf8_lf"), expected);
  assert.deepEqual(digestBytes(crlf, "portable_utf8_lf"), expected);
});

test("portable_utf8_lf는 내용 변경·Lone CR·비UTF-8·허용되지 않은 표현을 fail-close한다", () => {
  const original = digestBytes(Buffer.from("alpha\r\nbeta\r\n"), "portable_utf8_lf");
  const changed = digestBytes(Buffer.from("alpha\r\nBETa\r\n"), "portable_utf8_lf");
  assert.notEqual(changed.sha256, original.sha256);
  assert.throws(() => digestBytes(Buffer.from("alpha\rbeta"), "portable_utf8_lf"), (error) => error.code === "LONE_CR_NOT_ALLOWED");
  assert.throws(() => digestBytes(Buffer.from([0xc3, 0x28]), "portable_utf8_lf"), (error) => error.code === "INVALID_UTF8_ROUND_TRIP");
  assert.throws(() => digestBytes(Buffer.from("alpha"), "trimmed_utf8"), (error) => error.code === "UNSUPPORTED_REPRESENTATION");
});

test("raw 표현은 Binary byte를 변환하지 않고 Hash와 Byte를 함께 고정한다", () => {
  const binary = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x00, 0x0d, 0x0a]);
  assert.deepEqual(digestBytes(binary, "raw"), { representation: "raw", bytes: binary.length, sha256: sha256(binary) });
});

test("Manifest validator는 선언 표현·파일·UTF-8·Hash·Byte를 모두 검증한다", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "r1-m3-01-portable-"));
  try {
    fs.writeFileSync(path.join(root, "text.md"), "alpha\r\nbeta\r\n", "utf8");
    fs.writeFileSync(path.join(root, "image.png"), Buffer.from([0x89, 0x50, 0x4e, 0x47]));
    const textDigest = digestFile(path.join(root, "text.md"), "portable_utf8_lf");
    const binaryDigest = digestFile(path.join(root, "image.png"), "raw");
    const manifest = {
      schema_version: "2.0",
      artifact_count: 2,
      artifacts: [
        { path: "text.md", ...textDigest },
        { path: "image.png", ...binaryDigest }
      ]
    };
    assert.deepEqual(validateEvidenceManifest(manifest, { root }), { valid: true, checked: 2, failures: [] });

    const cases = [
      [{ ...manifest, artifacts: [{ ...manifest.artifacts[0], representation: "trimmed_utf8" }, manifest.artifacts[1]] }, "UNSUPPORTED_REPRESENTATION"],
      [{ ...manifest, artifacts: [{ ...manifest.artifacts[0], path: "missing.md" }, manifest.artifacts[1]] }, "ARTIFACT_MISSING"],
      [{ ...manifest, artifacts: [{ ...manifest.artifacts[0], sha256: "0".repeat(64) }, manifest.artifacts[1]] }, "ARTIFACT_HASH_MISMATCH"],
      [{ ...manifest, artifacts: [{ ...manifest.artifacts[0], bytes: manifest.artifacts[0].bytes + 1 }, manifest.artifacts[1]] }, "ARTIFACT_BYTE_MISMATCH"],
      [{ ...manifest, artifacts: [{ ...manifest.artifacts[0], representation: "raw" }, manifest.artifacts[1]] }, "TEXT_REPRESENTATION_REQUIRED"],
      [{ ...manifest, artifacts: [manifest.artifacts[0], { ...manifest.artifacts[1], representation: "portable_utf8_lf" }] }, "BINARY_RAW_REPRESENTATION_REQUIRED"]
    ];
    for (const [candidate, code] of cases) {
      const result = validateEvidenceManifest(candidate, { root });
      assert.equal(result.valid, false, code);
      assert.ok(result.failures.some((failure) => failure.code === code), code);
    }

    fs.writeFileSync(path.join(root, "invalid.md"), Buffer.from([0xc3, 0x28]));
    const invalidUtf8 = {
      ...manifest,
      artifact_count: 1,
      artifacts: [{ path: "invalid.md", representation: "portable_utf8_lf", bytes: 2, sha256: "0".repeat(64) }]
    };
    assert.ok(validateEvidenceManifest(invalidUtf8, { root }).failures.some((failure) => failure.code === "INVALID_UTF8_ROUND_TRIP"));

    fs.writeFileSync(path.join(root, "lone.md"), "alpha\rbeta", "utf8");
    const loneCr = {
      ...manifest,
      artifact_count: 1,
      artifacts: [{ path: "lone.md", representation: "portable_utf8_lf", bytes: 10, sha256: "0".repeat(64) }]
    };
    assert.ok(validateEvidenceManifest(loneCr, { root }).failures.some((failure) => failure.code === "LONE_CR_NOT_ALLOWED"));

    const manifestPath = path.join(root, "manifest.json");
    fs.writeFileSync(manifestPath, JSON.stringify(manifest), "utf8");
    assert.deepEqual(verifyEvidenceManifestFile(manifestPath, { root }), { valid: true, checked: 2, failures: [] });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Manifest Artifact 경로는 Repository 상대 Canonical 경로만 허용한다", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "r1-m3-01-canonical-"));
  try {
    fs.mkdirSync(path.join(root, "nested"));
    fs.writeFileSync(path.join(root, "nested", "text.md"), "alpha\n", "utf8");
    const digest = digestFile(path.join(root, "nested", "text.md"), "portable_utf8_lf");
    const candidate = (artifactPath) => ({
      schema_version: "2.0",
      artifact_count: 1,
      artifacts: [{ path: artifactPath, ...digest }]
    });

    assert.deepEqual(
      validateEvidenceManifest(candidate("nested/text.md"), { root }),
      { valid: true, checked: 1, failures: [] }
    );
    for (const artifactPath of [
      "nested/../nested/text.md",
      "./nested/text.md",
      "nested\\text.md",
      "nested//text.md"
    ]) {
      const result = validateEvidenceManifest(candidate(artifactPath), { root });
      assert.ok(
        result.failures.some((failure) => failure.code === "NON_CANONICAL_ARTIFACT_PATH"),
        `${artifactPath} must fail as NON_CANONICAL_ARTIFACT_PATH`
      );
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Canonical Real Path 중복 키는 Windows 대소문자 별칭을 OS 독립적으로 거부한다", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "r1-m3-01-casefold-"));
  try {
    fs.writeFileSync(path.join(root, "Case.md"), "same\n", "utf8");
    if (process.platform !== "win32") fs.writeFileSync(path.join(root, "case.md"), "same\n", "utf8");
    const digest = digestFile(path.join(root, "Case.md"), "portable_utf8_lf");
    const manifest = {
      schema_version: "2.0",
      artifact_count: 2,
      artifacts: [
        { path: "Case.md", ...digest },
        { path: "case.md", ...digest }
      ]
    };
    const result = validateEvidenceManifest(manifest, { root, caseInsensitivePaths: true });
    assert.ok(result.failures.some((failure) => failure.code === "DUPLICATE_ARTIFACT_PATH"));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Root 안 Junction 또는 Symlink가 Root 밖 Artifact를 가리키면 거부한다", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "r1-m3-01-root-"));
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "r1-m3-01-outside-"));
  try {
    fs.writeFileSync(path.join(outside, "outside.md"), "outside\n", "utf8");
    fs.symlinkSync(outside, path.join(root, "escape"), process.platform === "win32" ? "junction" : "dir");
    const digest = digestFile(path.join(outside, "outside.md"), "portable_utf8_lf");
    const manifest = {
      schema_version: "2.0",
      artifact_count: 1,
      artifacts: [{ path: "escape/outside.md", ...digest }]
    };
    const result = validateEvidenceManifest(manifest, { root });
    assert.ok(result.failures.some((failure) => failure.code === "ARTIFACT_OUTSIDE_ROOT"));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(outside, { recursive: true, force: true });
  }
});
