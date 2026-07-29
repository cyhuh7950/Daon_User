import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { runQualityGate } from "../lib/quality-gate.mjs";

const categories = ["lint", "type", "unit", "contract", "build", "security", "independence"];
const approvedComponents = [
  ["apps/web", "package.json"],
  ["apps/desktop", "package.json"],
  ["apps/mobile", "package.json"],
  ["packages/ui", "package.json"],
  ["packages/contracts", "package.json"],
  ["packages/design-tokens", "package.json"],
  ["services/api", "pyproject.toml"],
  ["services/local-service", "pyproject.toml"]
];

function componentPolicy([root, manifest], commands) {
  return {
    id: root,
    root,
    manifest: `${root}/${manifest}`,
    foundation_allowed_files: ["README.md", manifest],
    capabilities: {
      lint: { signals: { extensions: [".js", ".ts", ".tsx", ".py"], files: ["eslint.config.js", "ruff.toml"] }, command: root === "apps/web" ? commands.lint ?? null : null },
      type: { signals: { extensions: [".ts", ".tsx", ".py"], files: ["tsconfig.json", "pyrightconfig.json"] }, command: root === "apps/web" ? commands.type ?? null : null },
      unit: { signals: { extensions: [".js", ".ts", ".tsx", ".py"], files: [] }, command: root === "apps/web" ? commands.unit ?? null : null },
      contract: { signals: { extensions: [], files: ["openapi.json"] }, command: root === "apps/web" ? commands.contract ?? null : null },
      build: { signals: { extensions: [".js", ".ts", ".tsx", ".py"], files: ["next.config.js"] }, command: root === "apps/web" ? commands.build ?? null : null }
    }
  };
}

function fixturePolicy({ commands = {} } = {}) {
  return {
    schema_version: "1.0",
    report_schema_version: "1.0",
    foundation_status: "NOT_APPLICABLE_FOUNDATION_ONLY",
    categories,
    components: approvedComponents.map((component) => componentPolicy(component, commands)),
    mandatory_checks: [
      { id: "quality-gate-runner-tests", category: "unit", command: ["stub", "runner-tests"], failure_kind: "quality" },
      { id: "toolchain-baseline", category: "build", command: ["stub", "toolchain"], failure_kind: "quality" },
      { id: "production-dependency-audit", category: "security", command: ["stub", "audit"], kind: "npm_audit", failure_kind: "execution" },
      { id: "repository-independence", category: "independence", command: ["stub", "independence"], failure_kind: "quality" },
      { id: "local-service-runtime-verifier-tests", category: "unit", command: ["stub", "local-runtime"], failure_kind: "quality" },
      { id: "local-service-full-environment-audit", category: "security", command: ["stub", "local-audit"], failure_kind: "execution" }
    ],
    security: {
      scan_roots: ["apps", ".github"],
      excluded_files: [],
      secret_patterns: [
        { id: "SECRET_ASSIGNMENT", pattern: "(?:api[_-]?key|token|secret)\\s*[:=]\\s*['\\\"][^'\\\"]{6,}['\\\"]" }
      ],
      forbidden_runtime_patterns: [
        { id: "INTERNAL_ADDRESS", pattern: "https?://(?:localhost|127\\.0\\.0\\.1)(?::\\d+)?" }
      ]
    },
    artifacts: { result: "quality-gate-result.json", summary: "quality-gate-summary.md" }
  };
}

async function makeFixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "daon-quality-gate-"));
  for (const [component, manifest] of approvedComponents) {
    const componentRoot = path.join(root, component);
    await mkdir(componentRoot, { recursive: true });
    await writeFile(path.join(componentRoot, "README.md"), "# foundation\n");
    await writeFile(
      path.join(componentRoot, manifest),
      manifest === "package.json" ? '{"name":"fixture","private":true}\n' : '[project]\nname = "fixture"\nversion = "0.0.0"\n'
    );
  }
  return root;
}

function passingCommandRunner(overrides = {}) {
  return async (check) => overrides[check.id] ?? {
    exitCode: 0,
    stdout: check.kind === "npm_audit"
      ? JSON.stringify({ metadata: { vulnerabilities: { info: 0, low: 0, moderate: 0, high: 0, critical: 0, total: 0 } } })
      : "ok",
    stderr: "",
    spawnError: null
  };
}

async function runFixture(root, policy, commandRunner = passingCommandRunner()) {
  return runQualityGate({
    root,
    policy,
    policyPath: path.join(root, "quality-gate-policy.json"),
    commandRunner,
    writeArtifacts: false,
    gitSha: "fixture-sha"
  });
}

test("foundation 저장소는 정확한 부재 조건만 N/A이고 상시 검사는 PASS다", async (t) => {
  const root = await makeFixture();
  t.after(() => rm(root, { recursive: true, force: true }));
  const { exitCode, report } = await runFixture(root, fixturePolicy());
  assert.equal(exitCode, 0);
  assert.equal(report.overall_status, "PASS");
  assert.equal(report.categories.lint.status, "NOT_APPLICABLE_FOUNDATION_ONLY");
  assert.equal(report.categories.type.status, "NOT_APPLICABLE_FOUNDATION_ONLY");
  assert.equal(report.categories.contract.status, "NOT_APPLICABLE_FOUNDATION_ONLY");
  assert.equal(report.categories.unit.status, "PASS");
  assert.equal(report.categories.build.status, "PASS");
  assert.equal(report.categories.security.status, "PASS");
  assert.equal(report.categories.independence.status, "PASS");
  assert.equal(report.failures.length, 0);
});

test("Local Service Runtime과 전체 Python 환경 감사는 명시적 필수검사다", async (t) => {
  const root = await makeFixture();
  t.after(() => rm(root, { recursive: true, force: true }));
  const policy = fixturePolicy();
  const { exitCode, report } = await runFixture(root, policy);
  assert.equal(exitCode, 0);
  assert.equal(report.overall_status, "PASS");
  const serialized = JSON.stringify(report);
  assert.match(serialized, /local-service-runtime-verifier-tests/);
  assert.match(serialized, /local-service-full-environment-audit/);
});

test("생성 Cache와 compiler target은 보안 Source scan에서 제외한다", async (t) => {
  const root = await makeFixture();
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const directory of ["__pycache__", ".pytest_cache", ".ruff_cache", "target"]) {
    const generated = path.join(root, ".github", directory);
    await mkdir(generated, { recursive: true });
    await writeFile(path.join(generated, "generated.bin"), "http://127.0.0.1:9999");
  }
  const { exitCode, report } = await runFixture(root, fixturePolicy());
  assert.equal(exitCode, 0);
  assert.equal(report.categories.security.status, "PASS");
});

test("Runtime Source가 등장했는데 필수 Capability 명령이 없으면 Exit 1이다", async (t) => {
  const root = await makeFixture();
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, "apps", "web", "src"), { recursive: true });
  await writeFile(path.join(root, "apps", "web", "src", "index.ts"), "export const ready = true;\n");
  const { exitCode, report } = await runFixture(root, fixturePolicy());
  assert.equal(exitCode, 1);
  assert.equal(report.overall_status, "FAIL");
  for (const category of ["lint", "type", "unit", "build"])
    assert.ok(report.failures.some((item) => item.code === "MISSING_REQUIRED_CAPABILITY" && item.category === category));
});

test("구성된 필수 명령 실패는 조용히 N/A로 바뀌지 않고 Exit 1이다", async (t) => {
  const root = await makeFixture();
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, "apps", "web", "src"), { recursive: true });
  await writeFile(path.join(root, "apps", "web", "src", "index.ts"), "export const ready = true;\n");
  const command = (id) => ({ id, command: ["stub", id], failure_kind: "quality" });
  const policy = fixturePolicy({ commands: { lint: command("web-lint"), type: command("web-type"), unit: command("web-unit"), build: command("web-build") } });
  const runner = passingCommandRunner({ "web-lint": { exitCode: 1, stdout: "", stderr: "lint failed", spawnError: null } });
  const { exitCode, report } = await runFixture(root, policy, runner);
  assert.equal(exitCode, 1);
  assert.equal(report.categories.lint.status, "FAIL");
  assert.ok(report.failures.some((item) => item.check_id === "web-lint" && item.code === "COMMAND_FAILED"));
});

test("Policy Schema 오류는 실행 불능 Exit 2다", async (t) => {
  const root = await makeFixture();
  t.after(() => rm(root, { recursive: true, force: true }));
  const policy = fixturePolicy();
  policy.categories = categories.filter((item) => item !== "security");
  const { exitCode, report } = await runFixture(root, policy);
  assert.equal(exitCode, 2);
  assert.equal(report.overall_status, "ERROR");
  assert.ok(report.failures.some((item) => item.code === "POLICY_SCHEMA_ERROR"));
});

test("승인 Policy Matrix와 필수 Check 변형은 모두 Fail-close Exit 2다", async (t) => {
  const cases = [
    ["Component 중복", (policy) => policy.components.push(structuredClone(policy.components[0]))],
    ["Component 삭제", (policy) => policy.components.pop()],
    ["필수 Check 중복", (policy) => policy.mandatory_checks.push(structuredClone(policy.mandatory_checks[0]))],
    ["필수 Check 삭제", (policy) => policy.mandatory_checks.pop()],
    ["필수 Check 범주 변경", (policy) => { policy.mandatory_checks[0].category = "build"; }],
    ["Audit kind 변경", (policy) => { policy.mandatory_checks[2].kind = "custom"; }],
    ["Foundation 상태 변경", (policy) => { policy.foundation_status = "PASS"; }],
    ["필수 Check 빈 명령", (policy) => { policy.mandatory_checks[0].command = []; }],
    ["Capability 빈 명령", (policy) => { policy.components[0].capabilities.lint.command = []; }],
    ["Manifest 부재", (policy) => { policy.components[0].manifest = "apps/web/missing.json"; }],
    ["Manifest가 Component Root 밖", (policy) => { policy.components[0].manifest = "apps/desktop/package.json"; }]
  ];

  for (const [name, mutate] of cases) {
    await t.test(name, async (caseTest) => {
      const root = await makeFixture();
      caseTest.after(() => rm(root, { recursive: true, force: true }));
      const policy = fixturePolicy();
      mutate(policy);
      const { exitCode, report } = await runFixture(root, policy);
      assert.equal(exitCode, 2);
      assert.equal(report.overall_status, "ERROR");
      assert.ok(report.failures.some((item) => item.code === "POLICY_SCHEMA_ERROR"));
    });
  }
});

test("Registry/Network 불능 Audit는 성공으로 처리하지 않고 Exit 2다", async (t) => {
  const root = await makeFixture();
  t.after(() => rm(root, { recursive: true, force: true }));
  const runner = passingCommandRunner({
    "production-dependency-audit": { exitCode: 1, stdout: "", stderr: "network unavailable", spawnError: null }
  });
  const { exitCode, report } = await runFixture(root, fixturePolicy(), runner);
  assert.equal(exitCode, 2);
  assert.equal(report.overall_status, "ERROR");
  assert.ok(report.failures.some((item) => item.code === "AUDIT_EXECUTION_UNAVAILABLE"));
});

test("Secret 의심값은 실패하되 Artifact에 원문 값을 남기지 않는다", async (t) => {
  const root = await makeFixture();
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, "apps", "web", "src"), { recursive: true });
  await writeFile(path.join(root, "apps", "web", "src", "config.js"), 'const apiKey = "super-secret-value";\n');
  const command = (id) => ({ id, command: ["stub", id], failure_kind: "quality" });
  const policy = fixturePolicy({ commands: { lint: command("web-lint"), unit: command("web-unit"), build: command("web-build") } });
  const { exitCode, report } = await runFixture(root, policy);
  assert.equal(exitCode, 1);
  const serialized = JSON.stringify(report);
  assert.match(serialized, /SECRET_ASSIGNMENT/);
  assert.doesNotMatch(serialized, /super-secret-value/);
});

test("GitHub Workflow는 JSON으로도 유효한 YAML 1.2이며 공통 Runner 계약을 사용한다", async () => {
  const workflowPath = path.resolve(".github/workflows/release-1-quality-gate.yml");
  const workflow = JSON.parse(await readFile(workflowPath, "utf8"));
  const approvedToolchains = JSON.parse(await readFile(path.resolve("toolchain-versions.json"), "utf8")).toolchains;
  assert.equal(approvedToolchains.npm, "11.12.1");
  assert.equal(approvedToolchains.corepack, "0.35.0");
  assert.equal(approvedToolchains.uv, "0.11.2");
  assert.deepEqual(workflow.on.pull_request.branches, ["codex/release-1"]);
  assert.ok(workflow.on.workflow_dispatch);
  assert.deepEqual(workflow.permissions, { contents: "read" });
  const job = workflow.jobs["release-1-quality-gate"];
  assert.ok(job);
  assert.equal(job["timeout-minutes"], 60);
  const stepsById = new Map(job.steps.filter((step) => step.id).map((step) => [step.id, step]));
  const requiredStepIds = ["checkout", "clear-evidence", "setup-node", "toolchain-pins", "npm-corepack", "setup-uv", "toolchain-versions", "verify-toolchain", "tauri-linux-prerequisites", "npm-ci", "desktop-rust-type-diagnostic", "quality-gate", "fallback-evidence", "upload-evidence"];
  for (const id of requiredStepIds) assert.ok(stepsById.has(id), `missing workflow step id ${id}`);
  const clearEvidenceIndex = job.steps.findIndex((step) => step.id === "clear-evidence");
  assert.ok(job.steps.findIndex((step) => step.id === "checkout") < clearEvidenceIndex);
  assert.ok(clearEvidenceIndex < job.steps.findIndex((step) => step.id === "toolchain-pins"));
  assert.match(stepsById.get("clear-evidence").run, /rm -f/);
  assert.match(stepsById.get("clear-evidence").run, /quality-gate-result\.json/);
  assert.match(stepsById.get("clear-evidence").run, /quality-gate-summary\.md/);
  const uses = job.steps.map((step) => step.uses).filter(Boolean);
  const runs = job.steps.map((step) => step.run).filter(Boolean);
  const pinStepIndex = job.steps.findIndex((step) => step.id === "toolchain-pins");
  const npmCorepackIndex = job.steps.findIndex((step) => step.name === "Install approved npm and Corepack");
  const uvIndex = job.steps.findIndex((step) => step.uses === "astral-sh/setup-uv@v7");
  const versionOutputIndex = job.steps.findIndex((step) => step.name === "Print approved toolchain versions");
  const verifyToolchainIndex = job.steps.findIndex((step) => step.run === "npm run verify:toolchain");
  const tauriPrerequisiteIndex = job.steps.findIndex((step) => step.id === "tauri-linux-prerequisites");
  const npmCiIndex = job.steps.findIndex((step) => step.run === "npm ci");
  const desktopRustDiagnosticIndex = job.steps.findIndex((step) => step.id === "desktop-rust-type-diagnostic");
  const qualityGateIndex = job.steps.findIndex((step) => step.run === "npm run verify:quality-gate");
  assert.ok(pinStepIndex > 0);
  assert.match(job.steps[pinStepIndex].run, /toolchain-versions\.json/);
  assert.match(job.steps[pinStepIndex].run, /GITHUB_OUTPUT/);
  assert.equal(job.steps[npmCorepackIndex].run, 'npm install --global "npm@${{ steps.toolchain-pins.outputs.npm }}" "corepack@${{ steps.toolchain-pins.outputs.corepack }}"');
  assert.equal(job.steps[uvIndex].with.version, "${{ steps.toolchain-pins.outputs.uv }}");
  assert.equal(job.steps[versionOutputIndex].run, "npm --version\ncorepack --version\nuv --version");
  assert.ok(pinStepIndex < npmCorepackIndex && npmCorepackIndex < uvIndex && uvIndex < versionOutputIndex && versionOutputIndex < verifyToolchainIndex);
  assert.ok(verifyToolchainIndex < tauriPrerequisiteIndex && tauriPrerequisiteIndex < npmCiIndex && npmCiIndex < desktopRustDiagnosticIndex && desktopRustDiagnosticIndex < qualityGateIndex);
  for (const index of [pinStepIndex, npmCorepackIndex, uvIndex, verifyToolchainIndex])
    assert.notEqual(job.steps[index]["continue-on-error"], true);
  assert.ok(runs.includes("npm ci"));
  assert.ok(runs.some((command) => command.includes("npm run verify:quality-gate")));
  assert.ok(uses.includes("actions/upload-artifact@v6"));
  const upload = job.steps.find((step) => step.uses === "actions/upload-artifact@v6");
  assert.equal(upload.if, "${{ always() }}");
  const fallbackIndex = job.steps.findIndex((step) => step.id === "fallback-evidence");
  const uploadIndex = job.steps.findIndex((step) => step.id === "upload-evidence");
  const fallback = stepsById.get("fallback-evidence");
  assert.equal(fallback.if, "${{ always() }}");
  assert.match(fallback.run, /--ci-fallback/);
  assert.match(fallback.run, /--ci-diagnostic/);
  assert.ok(fallback.run.indexOf("--ci-fallback") < fallback.run.indexOf("--ci-diagnostic"));
  const qualityGateCli = await readFile(path.resolve("scripts/verify-quality-gate.mjs"), "utf8");
  assert.match(qualityGateCli, /args\.includes\("--ci-diagnostic"\)/);
  assert.match(qualityGateCli, /GITHUB_STEP_SUMMARY/);
  assert.match(qualityGateCli, /appendFile\(process\.env\.GITHUB_STEP_SUMMARY/);
  assert.equal(fallback.env.CI_GIT_SHA, "${{ github.sha }}");
  for (const id of ["toolchain-pins", "npm-corepack", "setup-uv", "toolchain-versions", "verify-toolchain", "npm-ci", "desktop-rust-type-diagnostic", "quality-gate"])
    assert.equal(fallback.env[`CI_STEP_${id.replaceAll("-", "_").toUpperCase()}`], `\${{ steps.${id}.outcome }}`);
  assert.ok(job.steps.findIndex((step) => step.id === "quality-gate") < fallbackIndex && fallbackIndex < uploadIndex);
});

test("CI Fallback Evidence는 현재 SHA의 유효한 결과만 보존하고 나머지는 현재 SHA ERROR로 교체한다", async (t) => {
  const { ensureCiFallbackEvidence } = await import("../lib/quality-gate.mjs");
  assert.equal(typeof ensureCiFallbackEvidence, "function");
  const root = await mkdtemp(path.join(os.tmpdir(), "daon-quality-gate-fallback-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const evidenceRoot = path.join(root, "docs", "03_evidence", "release_1", "R1-M1-05");
  await mkdir(evidenceRoot, { recursive: true });
  const resultPath = path.join(evidenceRoot, "quality-gate-result.json");
  const summaryPath = path.join(evidenceRoot, "quality-gate-summary.md");
  const categories = ["lint", "type", "unit", "contract", "build", "security", "independence"];
  const validResult = (gitSha) => ({
    schema_version: "1.0",
    policy_path: "quality-gate-policy.json",
    policy_sha256: "A".repeat(64),
    git_sha: gitSha,
    started_at: "2026-07-20T00:00:00.000Z",
    ended_at: "2026-07-20T00:00:01.000Z",
    overall_status: "PASS",
    exit_code: 0,
    categories: Object.fromEntries(categories.map((category) => [category, {
      status: ["lint", "type", "contract"].includes(category) ? "NOT_APPLICABLE_FOUNDATION_ONLY" : "PASS",
      checks: [],
      component_capabilities: []
    }])),
    failures: [],
    limitations: []
  });
  const invoke = () => ensureCiFallbackEvidence({
    root,
    gitSha: "candidate-sha",
    stepOutcomes: { "npm-ci": "success", "desktop-rust-type-diagnostic": "failure", "quality-gate": "skipped token=do-not-record", unknown: "credential-raw-value" }
  });
  const assertFallback = async (result) => {
    assert.equal(result.created, true);
    const report = JSON.parse(await readFile(resultPath, "utf8"));
    const summary = await readFile(summaryPath, "utf8");
    assert.equal(report.git_sha, "candidate-sha");
    assert.equal(report.overall_status, "ERROR");
    assert.equal(report.exit_code, 2);
    assert.equal(report.ci_fallback.step_outcomes["npm-ci"], "success");
    assert.equal(report.ci_fallback.step_outcomes["desktop-rust-type-diagnostic"], "failure");
    assert.equal(report.ci_fallback.step_outcomes["quality-gate"], "unknown");
    assert.equal(report.ci_fallback.step_outcomes.unknown, undefined);
    assert.match(summary, /candidate-sha/);
    assert.doesNotMatch(JSON.stringify(report) + summary, /do-not-record|credential-raw-value/);
  };

  await t.test("현재 SHA의 유효한 Result와 Summary는 그대로 보존한다", async () => {
    const existingResult = `${JSON.stringify(validResult("candidate-sha"), null, 2)}\n`;
    const existingSummary = "existing-current-summary\n";
    await writeFile(resultPath, existingResult);
    await writeFile(summaryPath, existingSummary);
    const preserved = await invoke();
    assert.equal(preserved.created, false);
    assert.equal(await readFile(resultPath, "utf8"), existingResult);
    assert.equal(await readFile(summaryPath, "utf8"), existingSummary);
  });

  await t.test("다른 SHA의 stale Result와 Summary를 현재 SHA Fallback으로 덮어쓴다", async () => {
    await writeFile(resultPath, `${JSON.stringify(validResult("stale-sha"), null, 2)}\n`);
    await writeFile(summaryPath, "stale-summary\n");
    await assertFallback(await invoke());
  });

  await t.test("Malformed Result JSON과 Summary를 현재 SHA Fallback으로 덮어쓴다", async () => {
    await writeFile(resultPath, "{ malformed-json\n");
    await writeFile(summaryPath, "malformed-summary\n");
    await assertFallback(await invoke());
  });

  await t.test("Result만 존재하면 Result와 Summary를 모두 재생성한다", async () => {
    await writeFile(resultPath, `${JSON.stringify(validResult("candidate-sha"), null, 2)}\n`);
    await rm(summaryPath, { force: true });
    await assertFallback(await invoke());
    assert.equal((await stat(resultPath)).isFile(), true);
    assert.equal((await stat(summaryPath)).isFile(), true);
  });

  await t.test("현재 SHA라도 최소 결과 계약이 불완전하면 두 파일을 재생성한다", async () => {
    const invalidResult = validResult("candidate-sha");
    delete invalidResult.categories.security;
    await writeFile(resultPath, `${JSON.stringify(invalidResult, null, 2)}\n`);
    await writeFile(summaryPath, "invalid-contract-summary\n");
    await assertFallback(await invoke());
  });
});

test("CI Quality 진단은 현재 SHA Allowlist만 결정적으로 출력하고 나머지는 Fail-close한다", async (t) => {
  const { renderCurrentQualityGateDiagnostic } = await import("../lib/quality-gate.mjs");
  assert.equal(typeof renderCurrentQualityGateDiagnostic, "function");
  const root = await mkdtemp(path.join(os.tmpdir(), "daon-quality-gate-diagnostic-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const evidenceRoot = path.join(root, "docs", "03_evidence", "release_1", "R1-M1-05");
  await mkdir(evidenceRoot, { recursive: true });
  const resultPath = path.join(evidenceRoot, "quality-gate-result.json");
  const validResult = (overallStatus, exitCode, failures = []) => ({
    schema_version: "1.0",
    policy_path: "quality-gate-policy.json",
    policy_sha256: "A".repeat(64),
    git_sha: "candidate-sha",
    started_at: "2026-07-20T00:00:00.000Z",
    ended_at: "2026-07-20T00:00:01.000Z",
    overall_status: overallStatus,
    exit_code: exitCode,
    categories: Object.fromEntries(categories.map((category) => [category, {
      status: overallStatus === "PASS" ? "PASS" : overallStatus,
      checks: [],
      component_capabilities: []
    }])),
    failures,
    limitations: []
  });
  const writeResult = async (report) => writeFile(resultPath, `${JSON.stringify(report, null, 2)}\n`);
  const render = () => renderCurrentQualityGateDiagnostic({ root, gitSha: "candidate-sha" });

  for (const [status, exitCode] of [["PASS", 0], ["FAIL", 1], ["ERROR", 2]]) {
    await writeResult(validResult(status, exitCode));
    const payload = JSON.parse(await render());
    assert.deepEqual(payload, { CODE: "QUALITY_GATE_CURRENT_RESULT", overall_status: status, exit_code: exitCode, failures: [] });
  }

  const failures = Array.from({ length: 24 }, (_, index) => ({
    category: index % 2 ? "unit" : "build",
    code: `CODE_${String(index).padStart(2, "0")}`,
    check_id: `check-${String(index).padStart(2, "0")}`,
    component: `component-${String(index).padStart(2, "0")}`,
    evidence: ["token=must-not-leak"]
  }));
  failures.push(structuredClone(failures[0]));
  failures.push({ category: "unit\nINJECT", code: "bad secret=value", check_id: "check\rmalicious", component: "component/unsafe", evidence: ["SUPER_SECRET"] });
  await writeResult(validResult("FAIL", 1, failures));
  const first = await render();
  const second = await render();
  assert.equal(first, second);
  assert.equal(first.includes("\n"), false);
  assert.doesNotMatch(first, /must-not-leak|SUPER_SECRET|secret=value|INJECT|malicious|component\/unsafe/);
  const diagnostic = JSON.parse(first);
  assert.equal(diagnostic.failures.length, 20);
  assert.equal(new Set(diagnostic.failures.map((item) => JSON.stringify(item))).size, diagnostic.failures.length);
  assert.deepEqual(Object.keys(diagnostic.failures[0]), ["category", "code", "check_id", "component"]);
  assert.ok(diagnostic.failures.some((item) => Object.values(item).includes("UNAVAILABLE")));

  await writeResult({ ...validResult("PASS", 0), git_sha: "stale-sha" });
  assert.equal(await render(), "QUALITY_GATE_NO_CURRENT_RESULT");
  await writeFile(resultPath, "{ malformed\n");
  assert.equal(await render(), "QUALITY_GATE_NO_CURRENT_RESULT");
  await rm(resultPath, { force: true });
  assert.equal(await render(), "QUALITY_GATE_NO_CURRENT_RESULT");
});
