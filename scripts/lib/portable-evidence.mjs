import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PORTABLE_UTF8_LF = "portable_utf8_lf";
const RAW = "raw";
const TEXT_EXTENSIONS = new Set([".css", ".js", ".json", ".jsx", ".md", ".mjs", ".ts", ".tsx"]);
const BINARY_EXTENSIONS = new Set([".jpeg", ".jpg", ".png"]);

class PortableEvidenceError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "PortableEvidenceError";
    this.code = code;
  }
}

function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex").toUpperCase();
}

function portableUtf8Lf(bytes) {
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    throw new PortableEvidenceError("INVALID_UTF8_ROUND_TRIP", "Artifact is not strict UTF-8.");
  }
  if (!Buffer.from(text, "utf8").equals(bytes)) {
    throw new PortableEvidenceError("INVALID_UTF8_ROUND_TRIP", "Artifact does not round-trip through UTF-8.");
  }
  if (/\r(?!\n)/u.test(text)) {
    throw new PortableEvidenceError("LONE_CR_NOT_ALLOWED", "Only CRLF line endings may be normalized.");
  }
  return Buffer.from(text.replace(/\r\n/gu, "\n"), "utf8");
}

export function digestBytes(input, representation) {
  const bytes = Buffer.isBuffer(input) ? input : Buffer.from(input);
  let represented;
  if (representation === RAW) represented = bytes;
  else if (representation === PORTABLE_UTF8_LF) represented = portableUtf8Lf(bytes);
  else throw new PortableEvidenceError("UNSUPPORTED_REPRESENTATION", `Unsupported representation: ${representation}`);
  return Object.freeze({ representation, bytes: represented.length, sha256: sha256(represented) });
}

export function digestFile(file, representation) {
  return digestBytes(fs.readFileSync(file), representation);
}

function validExpected(artifact) {
  return Number.isInteger(artifact?.bytes)
    && artifact.bytes >= 0
    && typeof artifact?.sha256 === "string"
    && /^[0-9a-f]{64}$/iu.test(artifact.sha256);
}

function representationRequirement(artifactPath, representation) {
  const extension = path.extname(artifactPath).toLowerCase();
  if (TEXT_EXTENSIONS.has(extension) && representation !== PORTABLE_UTF8_LF) return "TEXT_REPRESENTATION_REQUIRED";
  if (BINARY_EXTENSIONS.has(extension) && representation !== RAW) return "BINARY_RAW_REPRESENTATION_REQUIRED";
  if (!TEXT_EXTENSIONS.has(extension) && !BINARY_EXTENSIONS.has(extension)) return "UNSUPPORTED_ARTIFACT_TYPE";
  return null;
}

function artifactPathFailure(artifactPath) {
  if (!artifactPath || path.isAbsolute(artifactPath)) return "INVALID_ARTIFACT_PATH";
  if (artifactPath.includes("\\")) return "NON_CANONICAL_ARTIFACT_PATH";
  const segments = artifactPath.split("/");
  if (segments.some((segment) => !segment || segment === "." || segment === "..")) {
    return "NON_CANONICAL_ARTIFACT_PATH";
  }
  return path.posix.normalize(artifactPath) === artifactPath ? null : "NON_CANONICAL_ARTIFACT_PATH";
}

function comparableRealPath(realPath, caseInsensitivePaths) {
  const normalized = path.normalize(realPath);
  return caseInsensitivePaths ? normalized.toLowerCase() : normalized;
}

function isInsideRoot(realRoot, realArtifact, caseInsensitivePaths) {
  const rootKey = comparableRealPath(realRoot, caseInsensitivePaths);
  const artifactKey = comparableRealPath(realArtifact, caseInsensitivePaths);
  return artifactKey === rootKey || artifactKey.startsWith(`${rootKey}${path.sep}`);
}

export function validateEvidenceManifest(
  manifest,
  { root = process.cwd(), caseInsensitivePaths = process.platform === "win32" } = {}
) {
  const failures = [];
  const artifacts = Array.isArray(manifest?.artifacts) ? manifest.artifacts : [];
  if (manifest?.schema_version !== "2.0") failures.push({ path: null, code: "UNSUPPORTED_MANIFEST_SCHEMA" });
  if (!Number.isInteger(manifest?.artifact_count) || manifest.artifact_count !== artifacts.length) {
    failures.push({ path: null, code: "ARTIFACT_COUNT_MISMATCH" });
  }
  const resolvedRoot = path.resolve(root);
  let realRoot;
  try {
    realRoot = fs.realpathSync.native(resolvedRoot);
  } catch {
    failures.push({ path: null, code: "INVALID_EVIDENCE_ROOT" });
    return { valid: false, checked: artifacts.length, failures };
  }
  const seen = new Set();
  for (const artifact of artifacts) {
    const artifactPath = typeof artifact?.path === "string" ? artifact.path : "";
    const pathFailure = artifactPathFailure(artifactPath);
    if (pathFailure) {
      failures.push({ path: artifactPath || null, code: pathFailure });
      continue;
    }
    if (![PORTABLE_UTF8_LF, RAW].includes(artifact.representation)) {
      failures.push({ path: artifactPath, code: "UNSUPPORTED_REPRESENTATION" });
      continue;
    }
    const representationFailure = representationRequirement(artifactPath, artifact.representation);
    if (representationFailure) {
      failures.push({ path: artifactPath, code: representationFailure });
      continue;
    }
    if (!validExpected(artifact)) {
      failures.push({ path: artifactPath, code: "INVALID_ARTIFACT_EXPECTATION" });
      continue;
    }
    const resolvedArtifact = path.resolve(resolvedRoot, artifactPath);
    if (!isInsideRoot(resolvedRoot, resolvedArtifact, caseInsensitivePaths)) {
      failures.push({ path: artifactPath, code: "ARTIFACT_OUTSIDE_ROOT" });
      continue;
    }
    if (!fs.existsSync(resolvedArtifact)) {
      failures.push({ path: artifactPath, code: "ARTIFACT_MISSING" });
      continue;
    }
    let realArtifact;
    try {
      realArtifact = fs.realpathSync.native(resolvedArtifact);
    } catch {
      failures.push({ path: artifactPath, code: "ARTIFACT_READ_ERROR" });
      continue;
    }
    if (!isInsideRoot(realRoot, realArtifact, caseInsensitivePaths)) {
      failures.push({ path: artifactPath, code: "ARTIFACT_OUTSIDE_ROOT" });
      continue;
    }
    const realPathKey = comparableRealPath(realArtifact, caseInsensitivePaths);
    if (seen.has(realPathKey)) {
      failures.push({ path: artifactPath, code: "DUPLICATE_ARTIFACT_PATH" });
      continue;
    }
    seen.add(realPathKey);
    const canonicalRelativePath = path.relative(realRoot, realArtifact).split(path.sep).join("/");
    if (canonicalRelativePath !== artifactPath) {
      failures.push({ path: artifactPath, code: "NON_CANONICAL_ARTIFACT_PATH" });
      continue;
    }
    if (!fs.statSync(realArtifact).isFile()) {
      failures.push({ path: artifactPath, code: "ARTIFACT_NOT_REGULAR_FILE" });
      continue;
    }
    let actual;
    try {
      actual = digestFile(resolvedArtifact, artifact.representation);
    } catch (error) {
      failures.push({ path: artifactPath, code: error?.code ?? "ARTIFACT_READ_ERROR" });
      continue;
    }
    if (actual.sha256 !== artifact.sha256.toUpperCase()) failures.push({ path: artifactPath, code: "ARTIFACT_HASH_MISMATCH" });
    if (actual.bytes !== artifact.bytes) failures.push({ path: artifactPath, code: "ARTIFACT_BYTE_MISMATCH" });
  }
  return { valid: failures.length === 0, checked: artifacts.length, failures };
}

export function verifyEvidenceManifestFile(manifestPath, options = {}) {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  return validateEvidenceManifest(manifest, options);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const manifestPath = process.argv[2];
  if (!manifestPath) {
    console.error("PORTABLE_EVIDENCE_USAGE node scripts/lib/portable-evidence.mjs <manifest.json>");
    process.exitCode = 2;
  } else {
    try {
      const result = verifyEvidenceManifestFile(manifestPath);
      console.log(JSON.stringify(result, null, 2));
      process.exitCode = result.valid ? 0 : 1;
    } catch (error) {
      console.error(`PORTABLE_EVIDENCE_ERROR ${error?.code ?? error?.name ?? "UNKNOWN"}`);
      process.exitCode = 2;
    }
  }
}
