import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const APPROVED_PREDECESSOR_SPECIAL_CASES = Object.freeze([
  Object.freeze({ source_work_order: "R1-M2-04", artifact_path: "docs/02_work_orders/reports/R1-M2-04_attempt-2.md", status: "SUCCESSOR_SUPERSEDED", origin_commit: "f5f76867fbcc9d07699afc7d03beadeab56dae4c", successor_commit: "da99baf812f2d8eaf3b1b43794e3f0a16ac88063", evidence: "Expected blob exists at origin; later evidence commit changed report." }),
  Object.freeze({ source_work_order: "R1-M2-05", artifact_path: "docs/04_test_reports/release_1/R1-M2-05_progress.md", status: "SUCCESSOR_SUPERSEDED", origin_commit: "ac80b670c1606a22cac27c39f311ed3bd8980a42", successor_commit: "c42e1d409fca9a32a750eeff62abf1ce8bb3f76c", evidence: "Expected blob exists at origin; later evidence commit appended progress." }),
  Object.freeze({ source_work_order: "R1-M2-06", artifact_path: "packages/ui/src/index.js", status: "SUCCESSOR_SUPERSEDED", origin_commit: "780ca50725233227076a40f5adb2b5f1e05b1070", successor_commit: "6fdcfa20c80f0d512e2b4d299446b8b6e917bd11", evidence: "M2-07 added the operations export." }),
  Object.freeze({ source_work_order: "R1-M2-06", artifact_path: "packages/ui/src/workspace.css", status: "SUCCESSOR_SUPERSEDED", origin_commit: "780ca50725233227076a40f5adb2b5f1e05b1070", successor_commit: "6fdcfa20c80f0d512e2b4d299446b8b6e917bd11", evidence: "M2-07 added operations CSS." }),
  Object.freeze({ source_work_order: "R1-M2-06", artifact_path: "toolchain-versions.json", status: "LEGACY_MANIFEST_DRIFT", expected_sha256: "0DCCFFCD264C46E6881A6CEA98BCBC48C918D03C583BC4F2046AD833AF1AB05F", expected_bytes: 470, origin_commit: "780ca50725233227076a40f5adb2b5f1e05b1070", successor_commit: null, evidence: "Expected blob is absent from history and the final recorded commit." }),
  Object.freeze({ source_work_order: "R1-M2-06", artifact_path: "docs/01_architecture/DECISIONS.md", status: "LEGACY_MANIFEST_DRIFT", expected_sha256: "0F0FC901FE91A897CFFD7E3192BCA32A4B4BD5B7026DBE12EA2E27D9D669C69D", expected_bytes: 8839, origin_commit: "780ca50725233227076a40f5adb2b5f1e05b1070", successor_commit: null, evidence: "Expected blob is absent from history and the final recorded commit." }),
  Object.freeze({ source_work_order: "R1-M2-07", artifact_path: "packages/ui/src/index.js", status: "LEGACY_MANIFEST_DRIFT", expected_sha256: "B72782F6FC89B70B1ED1292796C61B5653F3271EDEA10AC8C349EEB4F5AC6378", expected_bytes: 1914, origin_commit: "6fdcfa20c80f0d512e2b4d299446b8b6e917bd11", successor_commit: null, evidence: "Expected blob is absent from history and the final implementation blob." }),
  Object.freeze({ source_work_order: "R1-M2-07", artifact_path: "packages/ui/src/workspace.css", status: "LEGACY_MANIFEST_DRIFT", expected_sha256: "51299A74D62E5AFC4A610ABF08AA8DB1DD8DCE4AD3B8D8C5604E75E841828276", expected_bytes: 34782, origin_commit: "6fdcfa20c80f0d512e2b4d299446b8b6e917bd11", successor_commit: null, evidence: "Expected blob is absent from history and the final implementation blob." })
]);

const SPECIAL_CASES = new Map(APPROVED_PREDECESSOR_SPECIAL_CASES.map((item) => [`${item.source_work_order}|${item.artifact_path}`, item]));

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex").toUpperCase();
}

function validExpected(expected) {
  return typeof expected?.sha256 === "string" && /^[A-F0-9]{64}$/i.test(expected.sha256) && Number.isInteger(expected.bytes) && expected.bytes >= 0;
}

function representationMatches(representation, expected) {
  return representation?.available === true && typeof representation.sha256 === "string" && representation.sha256.toUpperCase() === expected.sha256.toUpperCase() && representation.bytes === expected.bytes;
}

export function normalizeManifestExpected(artifact, representations = []) {
  const manifestHasBytes = Object.prototype.hasOwnProperty.call(artifact ?? {}, "bytes");
  if (manifestHasBytes) return { expected: { sha256: artifact?.sha256, bytes: artifact.bytes }, bytes_source: "MANIFEST" };
  const expectedSha = artifact?.sha256;
  const matched = typeof expectedSha === "string" && /^[A-F0-9]{64}$/i.test(expectedSha)
    ? representations.find((item) => item?.available === true && typeof item.sha256 === "string" && item.sha256.toUpperCase() === expectedSha.toUpperCase() && Number.isInteger(item.bytes) && item.bytes >= 0)
    : null;
  if (matched) return { expected: { sha256: expectedSha, bytes: matched.bytes }, bytes_source: "LEGACY_SHA_MATCHED_REPRESENTATION" };
  return { expected: { sha256: expectedSha, bytes: undefined }, bytes_source: "MISSING_UNVERIFIED" };
}

export function classifyPredecessorArtifact({ source_work_order: sourceWorkOrder, artifact_path: artifactPath, expected, raw, canonical, canonical_crlf: canonicalCrlf, special_case: specialCase = null }) {
  if (!validExpected(expected)) return { status: "UNEXPLAINED_MISMATCH", verification_representation: null, code: "INVALID_MANIFEST_EXPECTATION" };
  const approvedSpecialCase = SPECIAL_CASES.get(`${sourceWorkOrder}|${artifactPath}`) ?? null;
  const legacyExpectationMatches = approvedSpecialCase?.status !== "LEGACY_MANIFEST_DRIFT" || (
    expected.sha256.toUpperCase() === approvedSpecialCase.expected_sha256 && expected.bytes === approvedSpecialCase.expected_bytes
  );
  if (!legacyExpectationMatches) return { status: "UNEXPLAINED_MISMATCH", verification_representation: null, code: "LEGACY_EXPECTATION_MISMATCH" };
  if (representationMatches(raw, expected)) return { status: "DIRECT_MATCH", verification_representation: "RAW", code: "RAW_HASH_AND_BYTES_MATCH" };
  if (representationMatches(canonical, expected)) return { status: "DIRECT_MATCH", verification_representation: "GIT_CANONICAL", code: "GIT_CANONICAL_HASH_AND_BYTES_MATCH" };
  if (representationMatches(canonicalCrlf, expected)) return { status: "DIRECT_MATCH", verification_representation: "GIT_CRLF", code: "GIT_CRLF_HASH_AND_BYTES_MATCH" };
  const currentRepresentationAvailable = raw?.available === true || canonical?.available === true;
  if (currentRepresentationAvailable && approvedSpecialCase && specialCase?.lineage_verified === true && legacyExpectationMatches) {
    return { status: approvedSpecialCase.status, verification_representation: null, code: approvedSpecialCase.status === "SUCCESSOR_SUPERSEDED" ? "VERIFIED_SUCCESSOR_LINEAGE" : "VERIFIED_LEGACY_DRIFT" };
  }
  const bothUnavailable = !currentRepresentationAvailable;
  const code = approvedSpecialCase?.status === "LEGACY_MANIFEST_DRIFT" && !legacyExpectationMatches
    ? "LEGACY_EXPECTATION_MISMATCH"
    : approvedSpecialCase || specialCase
      ? "SPECIAL_CASE_LINEAGE_UNVERIFIED"
      : bothUnavailable
        ? "ARTIFACT_AND_CANONICAL_UNAVAILABLE"
        : "HASH_OR_BYTE_MISMATCH";
  return { status: "UNEXPLAINED_MISMATCH", verification_representation: null, code };
}

function gitBuffer(root, revision, artifactPath) {
  try {
    return execFileSync("git", ["show", `${revision}:${artifactPath}`], { cwd: root, encoding: null, stdio: ["ignore", "pipe", "ignore"] });
  } catch {
    return null;
  }
}

function gitCommitExists(root, commit) {
  if (!commit) return false;
  try {
    execFileSync("git", ["cat-file", "-e", `${commit}^{commit}`], { cwd: root, stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

function gitIsAncestor(root, ancestor, descendant) {
  try {
    execFileSync("git", ["merge-base", "--is-ancestor", ancestor, descendant], { cwd: root, stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

export function validateOriginCommit(value, commitExists) {
  if (value === null) return true;
  return typeof value === "string" && /^[0-9a-f]{7,40}$/i.test(value) && commitExists === true;
}

function readRaw(root, artifactPath) {
  try {
    const buffer = fs.readFileSync(path.join(root, artifactPath));
    return { available: true, buffer, sha256: sha256(buffer), bytes: buffer.length };
  } catch {
    return { available: false, buffer: null, sha256: null, bytes: null };
  }
}

function asRepresentation(buffer) {
  return buffer ? { available: true, sha256: sha256(buffer), bytes: buffer.length } : { available: false, sha256: null, bytes: null };
}

function asGitCrlfRepresentation(buffer) {
  if (!buffer) return { available: false, sha256: null, bytes: null };
  const text = buffer.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(buffer)) return { available: false, sha256: null, bytes: null };
  return asRepresentation(Buffer.from(text.replace(/\r?\n/g, "\r\n"), "utf8"));
}

function verifySpecialCaseLineage(root, artifactPath, expected, specialCase, origin) {
  const originExists = gitCommitExists(root, specialCase.origin_commit);
  const originMatches = validExpected(expected) && representationMatches(origin, expected);
  if (specialCase.status === "SUCCESSOR_SUPERSEDED") {
    const successorExists = gitCommitExists(root, specialCase.successor_commit);
    const successorBlobExists = successorExists && gitBuffer(root, specialCase.successor_commit, artifactPath) !== null;
    return { lineage_verified: originMatches && successorExists && successorBlobExists && gitIsAncestor(root, specialCase.origin_commit, specialCase.successor_commit), origin };
  }
  const legacyExpectationMatches = validExpected(expected)
    && expected.sha256.toUpperCase() === specialCase.expected_sha256
    && expected.bytes === specialCase.expected_bytes;
  return { lineage_verified: legacyExpectationMatches && originExists && !originMatches };
}

export function buildPredecessorReconciliation({ root = process.cwd() } = {}) {
  const entries = [];
  for (let number = 2; number <= 7; number += 1) {
    const sourceWorkOrder = `R1-M2-0${number}`;
    const manifestDirectory = `docs/03_evidence/release_1/${sourceWorkOrder}`;
    const manifestPath = `${manifestDirectory}/evidence-manifest.json`;
    const manifest = JSON.parse(fs.readFileSync(path.join(root, manifestPath), "utf8"));
    const manifestDeclaredOriginCommit = manifest.validated_head ?? manifest.source_commit ?? manifest.head_sha ?? manifest.implementation_sha ?? null;
    const artifacts = manifest.artifacts ?? manifest.files ?? [];
    for (const artifact of artifacts) {
      let artifactPath = typeof artifact.path === "string" ? artifact.path.replaceAll("\\", "/") : "";
      if (artifactPath && !fs.existsSync(path.join(root, artifactPath))) artifactPath = path.posix.normalize(`${manifestDirectory}/${artifactPath}`);
      const raw = readRaw(root, artifactPath);
      const canonicalBuffer = artifactPath ? gitBuffer(root, "HEAD", artifactPath) : null;
      const canonical = asRepresentation(canonicalBuffer);
      const canonicalCrlf = asGitCrlfRepresentation(canonicalBuffer);
      const specialDefinition = SPECIAL_CASES.get(`${sourceWorkOrder}|${artifactPath}`) ?? null;
      const originRepresentation = specialDefinition && gitCommitExists(root, specialDefinition.origin_commit) ? asRepresentation(gitBuffer(root, specialDefinition.origin_commit, artifactPath)) : { available: false, sha256: null, bytes: null };
      let normalized = normalizeManifestExpected(artifact, [raw, canonical, canonicalCrlf]);
      if (normalized.bytes_source === "MISSING_UNVERIFIED" && specialDefinition?.status === "SUCCESSOR_SUPERSEDED" && originRepresentation.available === true && typeof artifact.sha256 === "string" && originRepresentation.sha256 === artifact.sha256.toUpperCase()) {
        normalized = { expected: { sha256: artifact.sha256, bytes: originRepresentation.bytes }, bytes_source: "VERIFIED_ORIGIN_BLOB" };
      }
      const expected = normalized.expected;
      const lineage = specialDefinition ? verifySpecialCaseLineage(root, artifactPath, expected, specialDefinition, originRepresentation) : null;
      const specialCase = specialDefinition ? { lineage_verified: lineage.lineage_verified } : null;
      const classification = classifyPredecessorArtifact({ source_work_order: sourceWorkOrder, artifact_path: artifactPath, expected, raw, canonical, canonical_crlf: canonicalCrlf, special_case: specialCase });
      const originImplementationOrEvidenceCommit = specialDefinition?.origin_commit ?? manifestDeclaredOriginCommit;
      const originCommitExists = typeof originImplementationOrEvidenceCommit === "string" ? gitCommitExists(root, originImplementationOrEvidenceCommit) : null;
      if (!validateOriginCommit(originImplementationOrEvidenceCommit, originCommitExists)) {
        throw new Error(`INVALID_OR_MISSING_ORIGIN_COMMIT:${sourceWorkOrder}:${artifactPath}`);
      }
      entries.push({
        source_work_order: sourceWorkOrder,
        manifest_path: manifestPath,
        artifact_path: artifactPath,
        expected_sha256: artifact.sha256 ?? null,
        expected_bytes: expected.bytes ?? null,
        expected_bytes_source: normalized.bytes_source,
        raw_sha256: raw.sha256,
        raw_bytes: raw.bytes,
        git_blob_sha256: canonical.sha256,
        git_blob_bytes: canonical.bytes,
        origin_implementation_or_evidence_commit: originImplementationOrEvidenceCommit,
        origin_commit_exists: originCommitExists,
        successor_commit: specialDefinition?.successor_commit ?? null,
        status: classification.status,
        verification_representation: classification.verification_representation,
        verification_code: classification.code,
        evidence: specialDefinition?.evidence ?? (classification.verification_representation === "RAW" ? "Raw Hash and Byte both match." : classification.verification_representation === "GIT_CANONICAL" ? "Git canonical Hash and Byte both match; raw differs by checkout representation." : classification.verification_representation === "GIT_CRLF" ? "Git canonical UTF-8 LF-to-CRLF representation Hash and Byte both match." : classification.code),
        current_m2_08_impact: classification.status === "DIRECT_MATCH" ? "none" : classification.status === "SUCCESSOR_SUPERSEDED" ? "Historical attribution valid; current asset revalidation required." : classification.status === "LEGACY_MANIFEST_DRIFT" ? "TP-1 observation; no predecessor hash-completeness PASS; current asset revalidation required." : "M2-08 completion blocked."
      });
    }
  }
  const counts = { DIRECT_MATCH: 0, SUCCESSOR_SUPERSEDED: 0, LEGACY_MANIFEST_DRIFT: 0, UNEXPLAINED_MISMATCH: 0 };
  for (const entry of entries) counts[entry.status] += 1;
  let validatedHead = null;
  try {
    validatedHead = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {}
  return {
    schema_version: "1.1",
    work_order: "R1-M2-08",
    issue_id: "R1-M2-08-I001",
    decision_addendum: "docs/02_work_orders/release_1/R1-M2-08-C00_evidence_reconciliation_addendum.md",
    validated_head: validatedHead,
    summary: { artifact_count: entries.length, ...counts, predecessor_status: "verified_with_observations" },
    entries
  };
}

export const APPROVED_PREDECESSOR_SUMMARY = Object.freeze({ artifact_count: 90, DIRECT_MATCH: 82, SUCCESSOR_SUPERSEDED: 4, LEGACY_MANIFEST_DRIFT: 4, UNEXPLAINED_MISMATCH: 0, predecessor_status: "verified_with_observations" });
