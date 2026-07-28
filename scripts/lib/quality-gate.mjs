import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, statSync } from "node:fs";
import { mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";

const REQUIRED_CATEGORIES = ["lint", "type", "unit", "contract", "build", "security", "independence"];
const SOURCE_CATEGORIES = ["lint", "type", "unit", "contract", "build"];
const APPROVED_COMPONENTS = new Map([
  ["apps/web", "apps/web/package.json"],
  ["apps/desktop", "apps/desktop/package.json"],
  ["apps/mobile", "apps/mobile/package.json"],
  ["packages/ui", "packages/ui/package.json"],
  ["packages/contracts", "packages/contracts/package.json"],
  ["packages/design-tokens", "packages/design-tokens/package.json"],
  ["services/api", "services/api/pyproject.toml"],
  ["services/local-service", "services/local-service/pyproject.toml"]
]);
const REQUIRED_MANDATORY_CHECKS = new Map([
  ["quality-gate-runner-tests", { category: "unit", kind: null }],
  ["toolchain-baseline", { category: "build", kind: null }],
  ["production-dependency-audit", { category: "security", kind: "npm_audit" }],
  ["repository-independence", { category: "independence", kind: null }],
  ["local-service-runtime-verifier-tests", { category: "unit", kind: null }],
  ["local-service-full-environment-audit", { category: "security", kind: null }]
]);
export const CI_FALLBACK_STEP_IDS = ["toolchain-pins", "npm-corepack", "setup-uv", "toolchain-versions", "verify-toolchain", "npm-ci", "desktop-rust-type-diagnostic", "quality-gate"];
const CI_STEP_OUTCOMES = new Set(["success", "failure", "cancelled", "skipped"]);
const DEFAULT_RESULT_PATH = "docs/03_evidence/release_1/R1-M1-05/quality-gate-result.json";
const DEFAULT_SUMMARY_PATH = "docs/03_evidence/release_1/R1-M1-05/quality-gate-summary.md";
const MAX_DIAGNOSTIC_FAILURES = 20;
const IGNORED_DIRECTORIES = new Set([".git", "node_modules", ".next", "dist", "build", "coverage", ".cache"]);

const sha256 = (value) => createHash("sha256").update(value).digest("hex").toUpperCase();
const posix = (value) => value.split(path.sep).join("/");
const nowIso = () => new Date().toISOString();

async function listFiles(root) {
  if (!existsSync(root)) return [];
  const output = [];
  async function visit(current) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      if (entry.isDirectory() && IGNORED_DIRECTORIES.has(entry.name)) continue;
      const absolute = path.join(current, entry.name);
      if (entry.isDirectory()) await visit(absolute);
      else if (entry.isFile()) output.push(absolute);
    }
  }
  await visit(root);
  return output;
}

const isCommand = (command) => Array.isArray(command)
  && command.length > 0
  && command.every((item) => typeof item === "string" && item.trim().length > 0);

function validatePolicy(policy, root) {
  const errors = [];
  if (policy?.schema_version !== "1.0") errors.push("schema_version must be 1.0");
  if (JSON.stringify(policy?.categories) !== JSON.stringify(REQUIRED_CATEGORIES)) errors.push("categories must contain the exact seven-category contract");
  if (policy?.foundation_status !== "NOT_APPLICABLE_FOUNDATION_ONLY") errors.push("foundation_status must be NOT_APPLICABLE_FOUNDATION_ONLY");
  if (!Array.isArray(policy?.components) || policy.components.length === 0) errors.push("components must be a non-empty array");
  if (!Array.isArray(policy?.mandatory_checks)) errors.push("mandatory_checks must be an array");
  if (!policy?.security || !Array.isArray(policy.security.scan_roots)) errors.push("security.scan_roots is required");
  if (!policy?.artifacts?.result || !policy?.artifacts?.summary) errors.push("artifact result and summary paths are required");
  const componentCounts = new Map();
  for (const component of policy?.components ?? []) {
    componentCounts.set(component?.id, (componentCounts.get(component?.id) ?? 0) + 1);
    if (!component.id || !component.root || !component.manifest) errors.push("each component requires id, root, and manifest");
    const approvedManifest = APPROVED_COMPONENTS.get(component.id);
    if (!approvedManifest) errors.push(`unapproved component id ${component.id ?? "missing"}`);
    else {
      if (component.root !== component.id) errors.push(`${component.id}.root must equal the approved component id`);
      if (component.manifest !== approvedManifest) errors.push(`${component.id}.manifest must equal ${approvedManifest}`);
    }
    const componentRoot = path.resolve(root, component.root ?? "__missing_component_root__");
    const manifestPath = path.resolve(root, component.manifest ?? "__missing_manifest__");
    const manifestRelative = path.relative(componentRoot, manifestPath);
    if (!existsSync(componentRoot) || !statSync(componentRoot).isDirectory()) errors.push(`${component.id ?? "component"}.root does not exist`);
    if (!existsSync(manifestPath) || !statSync(manifestPath).isFile()) errors.push(`${component.id ?? "component"}.manifest does not exist`);
    if (path.isAbsolute(manifestRelative) || manifestRelative === ".." || manifestRelative.startsWith(`..${path.sep}`))
      errors.push(`${component.id ?? "component"}.manifest is outside component root`);
    for (const category of SOURCE_CATEGORIES) {
      const capability = component.capabilities?.[category];
      if (!capability?.signals) errors.push(`${component.id ?? "component"}.${category} signals are required`);
      if (capability?.command != null && !isCommand(capability.command.command ?? capability.command))
        errors.push(`${component.id ?? "component"}.${category} command must be a non-empty string array`);
    }
  }
  for (const id of APPROVED_COMPONENTS.keys())
    if (componentCounts.get(id) !== 1) errors.push(`approved component ${id} must exist exactly once`);

  const checkCounts = new Map();
  for (const check of policy?.mandatory_checks ?? []) {
    checkCounts.set(check?.id, (checkCounts.get(check?.id) ?? 0) + 1);
    const approved = REQUIRED_MANDATORY_CHECKS.get(check?.id);
    if (!approved) errors.push(`unapproved mandatory check id ${check?.id ?? "missing"}`);
    else {
      if (check.category !== approved.category) errors.push(`${check.id}.category must be ${approved.category}`);
      if ((check.kind ?? null) !== approved.kind) errors.push(`${check.id}.kind must be ${approved.kind ?? "unset"}`);
    }
    if (!isCommand(check?.command)) errors.push(`${check?.id ?? "mandatory check"}.command must be a non-empty string array`);
  }
  for (const id of REQUIRED_MANDATORY_CHECKS.keys())
    if (checkCounts.get(id) !== 1) errors.push(`mandatory check ${id} must exist exactly once`);
  return errors;
}

function quoteWindows(value) {
  if (!/[\s"&|<>^]/.test(value)) return value;
  return `"${value.replace(/(\\*)"/g, "$1$1\\\"").replace(/(\\+)$/, "$1$1")}"`;
}

export async function defaultCommandRunner(check, { root }) {
  const [command, ...args] = check.command;
  const startedAt = nowIso();
  let result;
  if (process.platform === "win32" && ["npm", "npx", "corepack"].includes(command)) {
    const line = [command, ...args].map(quoteWindows).join(" ");
    result = spawnSync(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", line], { cwd: root, encoding: "utf8", windowsHide: true });
  } else {
    result = spawnSync(command, args, { cwd: root, encoding: "utf8", windowsHide: true });
  }
  return {
    exitCode: Number.isInteger(result.status) ? result.status : null,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    spawnError: result.error ? { code: result.error.code ?? "SPAWN_ERROR" } : null,
    startedAt,
    endedAt: nowIso()
  };
}

async function fileEvidence(root, files) {
  const evidence = [];
  for (const relative of files ?? []) {
    const absolute = path.resolve(root, relative);
    if (!existsSync(absolute) || !(await stat(absolute)).isFile()) continue;
    evidence.push({ path: posix(path.relative(root, absolute)), sha256: sha256(await readFile(absolute)) });
  }
  return evidence;
}

function signalMatches(componentRoot, relativeFiles, signals) {
  const extensions = new Set(signals.extensions ?? []);
  const exactFiles = new Set(signals.files ?? []);
  const directories = signals.directories ?? [];
  const matched = relativeFiles.filter((file) =>
    extensions.has(path.extname(file).toLowerCase())
    || exactFiles.has(file)
    || directories.some((directory) => file === directory || file.startsWith(`${directory}/`))
  );
  return { required: matched.length > 0, evidence: matched.slice(0, 20).map((file) => posix(path.join(componentRoot, file))) };
}

function failureRecord({ category, code, checkId, component, evidence = [] }) {
  return { category, code, check_id: checkId ?? null, component: component ?? null, evidence };
}

async function scanSecurity(root, security) {
  const excluded = new Set(security.excluded_files ?? []);
  const rules = [...(security.secret_patterns ?? []), ...(security.forbidden_runtime_patterns ?? [])]
    .map((rule) => ({ ...rule, regex: new RegExp(rule.pattern, "gim") }));
  const violations = [];
  const scanned = [];
  for (const scanRoot of security.scan_roots) {
    for (const absolute of await listFiles(path.resolve(root, scanRoot))) {
      const relative = posix(path.relative(root, absolute));
      if (excluded.has(relative)) continue;
      scanned.push(relative);
      const content = await readFile(absolute, "utf8");
      for (const rule of rules) {
        rule.regex.lastIndex = 0;
        if (rule.regex.test(content)) violations.push({ rule_id: rule.id, path: relative });
      }
    }
  }
  return { scanned, violations };
}

function parseAudit(result) {
  if (result.spawnError) return { status: "ERROR", code: "AUDIT_EXECUTION_UNAVAILABLE" };
  let payload;
  try { payload = JSON.parse(result.stdout); }
  catch { return { status: "ERROR", code: "AUDIT_EXECUTION_UNAVAILABLE" }; }
  const vulnerabilities = payload?.metadata?.vulnerabilities;
  if (!vulnerabilities) return { status: "ERROR", code: "AUDIT_EXECUTION_UNAVAILABLE" };
  const highRisk = Number(vulnerabilities.high ?? 0) + Number(vulnerabilities.critical ?? 0);
  if (highRisk > 0) return { status: "FAIL", code: "HIGH_RISK_PRODUCTION_DEPENDENCY", highRisk };
  if (result.exitCode !== 0) return { status: "ERROR", code: "AUDIT_EXECUTION_UNAVAILABLE" };
  return { status: "PASS", code: null, highRisk: 0 };
}

function renderSummary(report) {
  const lines = [
    "# Release 1 Quality Gate Summary",
    "",
    `- Git SHA: \`${report.git_sha}\``,
    `- Overall: \`${report.overall_status}\``,
    `- Exit Code: \`${report.exit_code}\``,
    `- Policy SHA-256: \`${report.policy_sha256}\``,
    "",
    "| Category | Status | Checks |",
    "| --- | --- | ---: |"
  ];
  for (const category of REQUIRED_CATEGORIES) {
    const item = report.categories[category];
    lines.push(`| ${category} | ${item.status} | ${item.checks.length} |`);
  }
  lines.push("", `Failures: ${report.failures.length}`, "", "Secret values and raw credential-bearing command output are intentionally excluded.", "");
  return lines.join("\n");
}

function safeIdentifier(value) {
  return typeof value === "string" && /^[A-Za-z0-9._-]{1,128}$/.test(value) ? value : "UNAVAILABLE";
}

function isMinimumQualityGateResult(report, expectedGitSha) {
  if (!report || typeof report !== "object" || Array.isArray(report)) return false;
  if (report.schema_version !== "1.0" || report.git_sha !== expectedGitSha) return false;
  if (typeof report.policy_path !== "string" || report.policy_path.length === 0) return false;
  if (typeof report.policy_sha256 !== "string" || report.policy_sha256.length === 0) return false;
  if (typeof report.started_at !== "string" || typeof report.ended_at !== "string") return false;
  const expectedExitCodes = { PASS: 0, FAIL: 1, ERROR: 2 };
  if (expectedExitCodes[report.overall_status] !== report.exit_code) return false;
  if (!Array.isArray(report.failures) || !Array.isArray(report.limitations)) return false;
  if (!report.categories || typeof report.categories !== "object" || Array.isArray(report.categories)) return false;
  const categoryStatuses = new Set(["PASS", "FAIL", "ERROR", "NOT_APPLICABLE_FOUNDATION_ONLY", "NOT_RUN"]);
  return REQUIRED_CATEGORIES.every((category) => {
    const item = report.categories[category];
    return item
      && typeof item === "object"
      && !Array.isArray(item)
      && categoryStatuses.has(item.status)
      && Array.isArray(item.checks)
      && Array.isArray(item.component_capabilities);
  });
}

export async function renderCurrentQualityGateDiagnostic({ root, gitSha }) {
  const noCurrentResult = "QUALITY_GATE_NO_CURRENT_RESULT";
  const expectedGitSha = safeIdentifier(gitSha);
  if (expectedGitSha === "UNAVAILABLE") return noCurrentResult;
  try {
    const resultPath = path.resolve(root, DEFAULT_RESULT_PATH);
    if (!existsSync(resultPath)) return noCurrentResult;
    const report = JSON.parse(await readFile(resultPath, "utf8"));
    if (!isMinimumQualityGateResult(report, expectedGitSha)) return noCurrentResult;
    const failures = [];
    const seen = new Set();
    for (const failure of report.failures) {
      const safeFailure = {
        category: safeIdentifier(failure?.category),
        code: safeIdentifier(failure?.code),
        check_id: safeIdentifier(failure?.check_id),
        component: safeIdentifier(failure?.component)
      };
      const key = JSON.stringify(safeFailure);
      if (!seen.has(key)) {
        seen.add(key);
        failures.push(safeFailure);
      }
    }
    failures.sort((left, right) => {
      const leftKey = JSON.stringify(left);
      const rightKey = JSON.stringify(right);
      return leftKey < rightKey ? -1 : leftKey > rightKey ? 1 : 0;
    });
    return JSON.stringify({
      CODE: "QUALITY_GATE_CURRENT_RESULT",
      overall_status: report.overall_status,
      exit_code: report.exit_code,
      failures: failures.slice(0, MAX_DIAGNOSTIC_FAILURES)
    });
  } catch {
    return noCurrentResult;
  }
}

export async function ensureCiFallbackEvidence({ root, gitSha, stepOutcomes = {}, policyPath = "quality-gate-policy.json" }) {
  const resolvedRoot = path.resolve(root);
  const resultPath = path.resolve(resolvedRoot, DEFAULT_RESULT_PATH);
  const summaryPath = path.resolve(resolvedRoot, DEFAULT_SUMMARY_PATH);
  const currentGitSha = safeIdentifier(gitSha);
  if (existsSync(resultPath) && existsSync(summaryPath)) {
    try {
      const existingReport = JSON.parse(await readFile(resultPath, "utf8"));
      if (isMinimumQualityGateResult(existingReport, currentGitSha)) return { created: false, resultPath, summaryPath };
    } catch {
      // Invalid or unreadable evidence must never preserve a stale CI result.
    }
  }

  const safeOutcomes = Object.fromEntries(CI_FALLBACK_STEP_IDS.map((id) => [
    id,
    CI_STEP_OUTCOMES.has(stepOutcomes[id]) ? stepOutcomes[id] : "unknown"
  ]));
  const resolvedPolicyPath = path.resolve(resolvedRoot, policyPath);
  const policySha = existsSync(resolvedPolicyPath) ? sha256(await readFile(resolvedPolicyPath)) : "UNAVAILABLE";
  const recordedAt = nowIso();
  const categories = Object.fromEntries(REQUIRED_CATEGORIES.map((category) => [
    category,
    { status: "NOT_RUN", checks: [], component_capabilities: [] }
  ]));
  const report = {
    schema_version: "1.0",
    policy_path: posix(path.relative(resolvedRoot, resolvedPolicyPath)),
    policy_sha256: policySha,
    git_sha: currentGitSha,
    started_at: recordedAt,
    ended_at: recordedAt,
    overall_status: "ERROR",
    exit_code: 2,
    categories,
    failures: [failureRecord({
      category: "ci",
      code: "CI_PRE_GATE_FAILURE",
      evidence: CI_FALLBACK_STEP_IDS.map((id) => `${id}:${safeOutcomes[id]}`)
    })],
    ci_fallback: { generated: true, step_outcomes: safeOutcomes },
    limitations: [
      "The common quality gate did not produce both evidence files in this CI run.",
      "Only fixed step IDs and normalized GitHub step outcomes are recorded; raw output and credentials are excluded."
    ]
  };
  const summary = [
    "# Release 1 Quality Gate Summary",
    "",
    `- Git SHA: \`${report.git_sha}\``,
    "- Overall: `ERROR`",
    "- Exit Code: `2`",
    "- Evidence Source: `CI_FALLBACK`",
    "",
    "| Step ID | Outcome |",
    "| --- | --- |",
    ...CI_FALLBACK_STEP_IDS.map((id) => `| ${id} | ${safeOutcomes[id]} |`),
    "",
    "Raw stdout, stderr, secrets, tokens, personal data, and credential values are intentionally excluded.",
    ""
  ].join("\n");
  await mkdir(path.dirname(resultPath), { recursive: true });
  await rm(resultPath, { force: true });
  await rm(summaryPath, { force: true });
  await writeFile(resultPath, `${JSON.stringify(report, null, 2)}\n`);
  await writeFile(summaryPath, summary);
  return { created: true, resultPath, summaryPath, report, summary };
}

function policyErrorReport({ policy, policyPath, gitSha, errors, startedAt }) {
  const categories = Object.fromEntries(REQUIRED_CATEGORIES.map((category) => [category, { status: "NOT_RUN", checks: [], component_capabilities: [] }]));
  return {
    schema_version: policy?.report_schema_version ?? "1.0",
    policy_path: policyPath,
    policy_sha256: sha256(JSON.stringify(policy ?? null)),
    git_sha: gitSha,
    started_at: startedAt,
    ended_at: nowIso(),
    overall_status: "ERROR",
    exit_code: 2,
    categories,
    failures: errors.map((error) => failureRecord({ category: "policy", code: "POLICY_SCHEMA_ERROR", evidence: [error] })),
    limitations: ["Policy schema invalid; no quality command was executed."]
  };
}

export async function runQualityGate({ root, policy, policyPath, commandRunner = defaultCommandRunner, writeArtifacts = true, gitSha = null }) {
  const startedAt = nowIso();
  const resolvedRoot = path.resolve(root);
  const resolvedPolicyPath = path.resolve(policyPath);
  const effectiveGitSha = gitSha ?? (() => {
    const result = spawnSync("git", ["rev-parse", "HEAD"], { cwd: resolvedRoot, encoding: "utf8", windowsHide: true });
    return result.status === 0 ? result.stdout.trim() : "UNAVAILABLE";
  })();
  const validationErrors = validatePolicy(policy, resolvedRoot);
  if (validationErrors.length) {
    const report = policyErrorReport({ policy, policyPath: posix(path.relative(resolvedRoot, resolvedPolicyPath)), gitSha: effectiveGitSha, errors: validationErrors, startedAt });
    return { exitCode: 2, report, summary: renderSummary(report) };
  }

  const categories = Object.fromEntries(REQUIRED_CATEGORIES.map((category) => [category, { status: policy.foundation_status, checks: [], component_capabilities: [] }]));
  const failures = [];
  const queuedChecks = [];

  for (const component of policy.components) {
    const componentRoot = path.resolve(resolvedRoot, component.root);
    const files = (await listFiles(componentRoot)).map((absolute) => posix(path.relative(componentRoot, absolute)));
    const unexpectedFoundationFiles = files.filter((file) => !(component.foundation_allowed_files ?? []).includes(file));
    for (const category of SOURCE_CATEGORIES) {
      const capability = component.capabilities[category];
      const signal = signalMatches(component.root, unexpectedFoundationFiles, capability.signals);
      const observation = {
        component: component.id,
        status: signal.required ? "REQUIRED" : policy.foundation_status,
        evidence: signal.evidence.length ? signal.evidence : [component.manifest, ...(component.foundation_allowed_files ?? []).map((file) => posix(path.join(component.root, file)))]
      };
      categories[category].component_capabilities.push(observation);
      if (!signal.required) continue;
      if (!capability.command) {
        const check = { id: `${component.id}:${category}`, status: "FAIL", exit_code: 1, command: null, started_at: startedAt, ended_at: nowIso(), evidence_files: signal.evidence, code: "MISSING_REQUIRED_CAPABILITY" };
        categories[category].checks.push(check);
        failures.push(failureRecord({ category, code: "MISSING_REQUIRED_CAPABILITY", checkId: check.id, component: component.id, evidence: signal.evidence }));
      } else queuedChecks.push({ ...capability.command, category, component: component.id });
    }
  }

  const securityStarted = nowIso();
  const securityResult = await scanSecurity(resolvedRoot, policy.security);
  const securityCheck = {
    id: "security-static-scan",
    status: securityResult.violations.length ? "FAIL" : "PASS",
    exit_code: securityResult.violations.length ? 1 : 0,
    command: null,
    started_at: securityStarted,
    ended_at: nowIso(),
    evidence_files: securityResult.scanned,
    violation_count: securityResult.violations.length,
    violations: securityResult.violations
  };
  categories.security.checks.push(securityCheck);
  for (const violation of securityResult.violations)
    failures.push(failureRecord({ category: "security", code: violation.rule_id, checkId: securityCheck.id, evidence: [violation.path] }));

  queuedChecks.push(...policy.mandatory_checks);
  for (const check of queuedChecks) {
    const commandStarted = nowIso();
    const result = await commandRunner(check, { root: resolvedRoot });
    let status = "PASS";
    let code = null;
    if (check.kind === "npm_audit") {
      const audit = parseAudit(result);
      status = audit.status;
      code = audit.code;
    } else if (result.spawnError) {
      status = "ERROR";
      code = "COMMAND_EXECUTION_UNAVAILABLE";
    } else if (result.exitCode !== 0) {
      status = check.failure_kind === "execution" ? "ERROR" : "FAIL";
      code = check.failure_kind === "execution" ? "COMMAND_EXECUTION_UNAVAILABLE" : "COMMAND_FAILED";
    }
    const record = {
      id: check.id,
      component: check.component ?? null,
      status,
      exit_code: result.exitCode,
      command: check.command,
      started_at: result.startedAt ?? commandStarted,
      ended_at: result.endedAt ?? nowIso(),
      evidence_files: await fileEvidence(resolvedRoot, check.evidence_files)
    };
    categories[check.category].checks.push(record);
    if (status !== "PASS") failures.push(failureRecord({ category: check.category, code, checkId: check.id, component: check.component }));
  }

  let executionError = false;
  for (const category of REQUIRED_CATEGORIES) {
    const checks = categories[category].checks;
    if (checks.some((check) => check.status === "ERROR")) executionError = true;
    if (checks.some((check) => check.status === "FAIL" || check.status === "ERROR")) categories[category].status = "FAIL";
    else if (checks.some((check) => check.status === "PASS")) categories[category].status = "PASS";
    else categories[category].status = policy.foundation_status;
  }
  const exitCode = executionError ? 2 : failures.length ? 1 : 0;
  let policyBytes;
  try { policyBytes = await readFile(resolvedPolicyPath); }
  catch { policyBytes = Buffer.from(JSON.stringify(policy)); }
  const report = {
    schema_version: policy.report_schema_version,
    policy_path: posix(path.relative(resolvedRoot, resolvedPolicyPath)),
    policy_sha256: sha256(policyBytes),
    git_sha: effectiveGitSha,
    started_at: startedAt,
    ended_at: nowIso(),
    overall_status: exitCode === 0 ? "PASS" : exitCode === 1 ? "FAIL" : "ERROR",
    exit_code: exitCode,
    categories,
    failures,
    limitations: [
      "Source-specific checks are N/A only while exact component signals are absent.",
      "Raw stdout, stderr, secrets, tokens, personal data, and credential values are not stored in artifacts."
    ]
  };
  const summary = renderSummary(report);
  if (writeArtifacts) {
    const resultPath = path.resolve(resolvedRoot, policy.artifacts.result);
    const summaryPath = path.resolve(resolvedRoot, policy.artifacts.summary);
    await mkdir(path.dirname(resultPath), { recursive: true });
    await mkdir(path.dirname(summaryPath), { recursive: true });
    await writeFile(resultPath, `${JSON.stringify(report, null, 2)}\n`);
    await writeFile(summaryPath, summary);
  }
  return { exitCode, report, summary };
}

export async function loadQualityGatePolicy(policyPath) {
  return JSON.parse(await readFile(policyPath, "utf8"));
}
