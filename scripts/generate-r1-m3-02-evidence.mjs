import { createHash } from "node:crypto";
import { readFile, readdir, stat, writeFile } from "node:fs/promises";
import path from "node:path";

const root = process.cwd();
const evidenceDir = path.join(root, "docs/03_evidence/release_1/R1-M3-02");
const issueId = "R1-M3-02-SERVER-VALIDATION-EVIDENCE";
const toPosix = (value) => value.split(path.sep).join("/");
const mutableHandoffRecords = [
  "docs/04_test_reports/release_1/R1-M3-02_progress.md",
  "docs/02_work_orders/reports/R1-M3-02_attempt-2.md"
];

async function artifact(relativePath) {
  const absolutePath = path.join(root, relativePath);
  const bytes = await readFile(absolutePath);
  return {
    path: toPosix(relativePath),
    bytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex").toUpperCase()
  };
}

async function filesUnder(relativeDir) {
  const names = await readdir(path.join(root, relativeDir), { withFileTypes: true });
  const results = [];
  for (const entry of names.sort((left, right) => left.name.localeCompare(right.name))) {
    const relativePath = path.join(relativeDir, entry.name);
    if (entry.isDirectory()) results.push(...await filesUnder(relativePath));
    else if (entry.isFile()) results.push(relativePath);
  }
  return results;
}

async function pngDimensions(relativePath) {
  const bytes = await readFile(path.join(root, relativePath));
  if (bytes.readUInt32BE(0) !== 0x89504e47 || bytes.toString("ascii", 1, 4) !== "PNG") {
    throw new Error(`${relativePath} is not a PNG`);
  }
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

async function writeJson(filename, value) {
  await writeFile(path.join(evidenceDir, filename), `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

const desktopInputs = (await filesUnder("apps/desktop"))
  .filter((file) => !file.includes(`${path.sep}dist${path.sep}`))
  .filter((file) => !file.includes(`${path.sep}gen${path.sep}`))
  .filter((file) => !file.endsWith("README.md"));
const sharedInputs = [
  ...(await filesUnder("packages/ui/src")),
  "packages/ui/package.json",
  "packages/ui/accessibility-contract.json",
  "packages/design-tokens/package.json",
  "packages/design-tokens/tokens.css",
  "packages/design-tokens/tokens.json",
  "packages/design-tokens/tokens.ts",
  "packages/contracts/package.json",
  "packages/contracts/navigation.json",
  "packages/contracts/screens.json"
];
const rootInputs = [
  "package.json",
  "package-lock.json",
  "quality-gate-policy.json",
  "rust-toolchain.toml",
  ".node-version",
  "scripts/run-isolated-desktop-cargo.mjs",
  "docs/01_architecture/windows_tauri_shell_contract.md"
];
const validationInputs = [
  "scripts/tests/desktop-tauri-shell.test.mjs",
  "scripts/lib/predecessor-evidence-reconciliation.mjs",
  "scripts/tests/platform-prototype-evidence.test.mjs",
  "scripts/generate-r1-m3-02-evidence.mjs",
  "docs/02_work_orders/release_1/R1-M2-08-C00_evidence_reconciliation_addendum.md",
  "docs/03_evidence/release_1/R1-M2-08/predecessor-evidence-reconciliation.json",
  "docs/03_evidence/release_1/R1-M2-08/evidence-manifest.json",
  "docs/02_work_orders/release_1/R1-M3-02-FIX-03_work_order.md",
  "docs/02_work_orders/release_1/R1-M3-02-FIX-03_prompt.md",
  "docs/02_work_orders/release_1/R1-M3-02-FIX-04_work_order.md",
  "docs/02_work_orders/release_1/R1-M3-02-FIX-04_prompt.md",
  "docs/02_work_orders/release_1/R1-M3-02-FIX-05_work_order.md",
  "docs/02_work_orders/release_1/R1-M3-02-FIX-05_prompt.md",
  "docs/02_work_orders/release_1/R1-M3-02-FIX-06_work_order.md",
  "docs/02_work_orders/release_1/R1-M3-02-FIX-06_prompt.md",
  "docs/03_evidence/release_1/R1-M3-02/server-validation-manifest.json",
  "docs/03_evidence/release_1/R1-M3-02/server-validation-summary.md"
];
const buildInputPaths = [...new Set([...desktopInputs, ...sharedInputs, ...rootInputs])].sort();

const iconFiles = (await readdir(path.join(root, "apps/desktop/src-tauri/icons"))).sort();
if (iconFiles.length !== 2 || iconFiles[0] !== "icon.ico" || iconFiles[1] !== "icon.png") {
  throw new Error(`unexpected Tauri icon inputs: ${iconFiles.join(",")}`);
}
const iconIco = await readFile(path.join(root, "apps/desktop/src-tauri/icons/icon.ico"));
const iconPng = await readFile(path.join(root, "apps/desktop/src-tauri/icons/icon.png"));
const iconPngDimensions = await pngDimensions("apps/desktop/src-tauri/icons/icon.png");
const iconCount = iconIco.readUInt16LE(4);
let sourcePngFrame = null;
for (let index = 0; index < iconCount; index += 1) {
  const offset = 6 + index * 16;
  const width = iconIco[offset] || 256;
  const height = iconIco[offset + 1] || 256;
  if (width !== 256 || height !== 256) continue;
  const bytes = iconIco.readUInt32LE(offset + 8);
  const dataOffset = iconIco.readUInt32LE(offset + 12);
  sourcePngFrame = iconIco.subarray(dataOffset, dataOffset + bytes);
  break;
}
if (!sourcePngFrame?.equals(iconPng) || iconPngDimensions.width !== 256 || iconPngDimensions.height !== 256 || iconPng[24] !== 8 || iconPng[25] !== 6) {
  throw new Error("cross-platform icon.png must be the exact embedded 256x256 8-bit RGBA frame from icon.ico");
}
const schemaDir = path.join(root, "apps/desktop/src-tauri/gen/schemas");
let generatedSchemas = [];
try {
  generatedSchemas = await readdir(schemaDir);
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
if (generatedSchemas.length !== 0) {
  throw new Error(`generated schemas must not be preserved: ${generatedSchemas.join(",")}`);
}

const sourceManifest = {
  schema_version: "1.0",
  work_order: "R1-M3-02",
  issue_id: issueId,
  generated_at: "2026-07-23T22:10:00+09:00",
  build_inputs: await Promise.all(buildInputPaths.map(artifact)),
  validation_inputs: await Promise.all(validationInputs.map(artifact)),
  fix_03_behavior_contract: {
    generated_on_exit_0: "remove_run_generated_gen_only",
    generated_on_exit_23: "preserve_exit_23_and_remove_run_generated_gen_only",
    spawn_error: "stable_exit_2_without_generated_residue",
    preexisting_gen: "fail_close_exit_2_without_child_and_preserve_bytes_hash",
    outside_temp_target_and_other_worktree: "preserve"
  },
  fix_04_behavior_contract: {
    inaccessible_gen_probe: "EACCES_or_EIO_fail_close_exit_2_without_child_or_temp_target",
    postcss_override: "root_postcss_exact_8.5.23",
    npm_ls_equivalent_acceptance: "exit_1_only_for_next_exact_8.5.10_override_invalid_without_other_problems",
    production_audit: "high_0_critical_0"
  },
  fix_05_behavior_contract: {
    predecessor_artifacts: 90,
    direct_match: 80,
    successor_superseded: 6,
    legacy_manifest_drift: 4,
    unexplained_mismatch: 0,
    added_successor_cases: [
      "R1-M2-06|package-lock.json",
      "R1-M2-07|package-lock.json"
    ],
    fail_close_fields: "source_work_order,artifact_path,expected_sha256,expected_bytes,origin_commit,successor_commit,current_sha256,current_bytes"
  },
  fix_06_behavior_contract: {
    server_pre_fix_failure: "ARM64 tauri_generate_context failed because apps/desktop/src-tauri/icons/icon.png was absent",
    source: "exact_embedded_256x256_png_frame_from_existing_icon.ico",
    dimensions: iconPngDimensions,
    png_color: "8_bit_rgba",
    png_sha256: createHash("sha256").update(iconPng).digest("hex").toUpperCase(),
    tauri_bundle_icons: ["icons/icon.ico", "icons/icon.png"],
    windows_nsis_target_preserved: true,
    post_fix_verification_boundary: "local desktop type and quality gate; ysna-server rerun is owned by main designer"
  },
  server_validation_contract: {
    evidence_source: "main_designer_confirmed_server_observation",
    exact_git_sha: "0a4c76b1ba9c165bd0adfbcd62dccdabc8f716d5",
    target: "ysna-server:/home/ubuntu/deploy/daon-user/R1-M3-02",
    architecture: "aarch64",
    quality_gate: "PASS_exit_0_all_7_categories_failures_0",
    database_migration: "N/A",
    developer_subagent_server_rerun: false
  },
  mutable_handoff_records: mutableHandoffRecords,
  generated_artifacts: {
    committed_tauri_schemas: [],
    windows_bundle_icons: ["icon.ico"],
    cross_platform_context_icons: ["icon.png"],
    rule: "Windows NSIS keeps icon.ico; exact source-derived icon.png supports cross-platform Tauri context generation; generated schemas and unrelated platform icons are excluded."
  }
};
await writeJson("source-artifact-manifest.json", sourceManifest);

const captures = [
  ["windows-home-1920x1080.png", "1920x1080", "1922x1112"],
  ["windows-home-1200x900.png", "1200x900", "1202x932"],
  ["windows-home-800x900.png", "800x900", "802x932"],
  ["windows-home-500x900.png", "500x900", "502x932"],
  ["windows-keyboard-tab-focus.png", "500x900", "502x932"],
  ["windows-tooltip-open.png", "500x900", "502x932"],
  ["windows-state-error.png", "500x900", "502x932"],
  ["windows-state-unavailable.png", "500x900", "502x932"]
];
const captureArtifacts = [];
for (const [filename, content, outer] of captures) {
  const relativePath = `docs/03_evidence/release_1/R1-M3-02/${filename}`;
  const item = await artifact(relativePath);
  captureArtifacts.push({ ...item, content_target: content, captured_outer_pixels: outer, dimensions: await pngDimensions(relativePath) });
}

await writeJson("desktop-shell-build.json", {
  schema_version: "1.0",
  work_order: "R1-M3-02",
  issue_id: "R1-M3-02-REVIEW-REPRO-L4",
  status: "PASS",
  commands: {
    desktop_type: "npm run verify:desktop-type",
    installer: "npm run build:desktop-installer",
    cleanup: "npm run cleanup:desktop-cargo -- <exact target>"
  },
  self_contained_cargo_target: true,
  manual_cargo_target_dir_required: false,
  repository_targets_after_commands: 0,
  installer_target: "C:\\Users\\cyhuh\\AppData\\Local\\Temp\\daon-user-desktop-installer-J8IhJv",
  frontend: { bundler: "Vite 8.1.5", modules: 42, production_assets: true },
  rust: { version: "1.97.1", tauri_cli: "2.11.4", profile: "release", locked: true },
  network_boundary: {
    frontend_dist: "../dist",
    dev_url: null,
    before_dev_command: null,
    csp_connect_src: "none",
    local_service_or_loopback: "N/A; R1-M3-03"
  }
});

await writeJson("installer-validation.json", {
  schema_version: "1.0",
  work_order: "R1-M3-02",
  issue_id: "R1-M3-02-REVIEW-REPRO-L4",
  installer: {
    path: "C:\\Users\\cyhuh\\AppData\\Local\\Temp\\daon-user-desktop-installer-J8IhJv\\release\\bundle\\nsis\\Daon 사용자 프로그램_0.1.0_x64-setup.exe",
    bytes: 1366987,
    sha256: "F92AAD047033AC6AD6C06464E5C596F605D5ED94E075EEED807D010669DD2918",
    bundle_type: "NSIS x64",
    signature: "unsigned_development"
  },
  installed_executable: {
    path: "C:\\Users\\cyhuh\\AppData\\Local\\Daon 사용자 프로그램\\daon-user-desktop.exe",
    bytes: 4066304,
    sha256: "0D77B7B1CA1A0724D8D364776384B98FE6AA1305123431CAAEEC141328165348",
    product_version: "0.1.0"
  },
  install: { result: "PASS", registry_entries: 1 },
  retained_for_main_designer_review: false,
  cleanup_status: "removed_after_main_designer_review",
  cleanup: {
    performed_by: "어울1 independent main-designer review",
    uninstaller: "NSIS uninstall.exe /S",
    uninstaller_exit_code: 0,
    installed_directory: {
      path: "C:\\Users\\cyhuh\\AppData\\Local\\Daon 사용자 프로그램",
      exists_after_cleanup: false
    },
    uninstall_registry: {
      path: "HKCU\\Uninstall\\Daon 사용자 프로그램",
      exists_after_cleanup: false
    },
    daon_app_processes_after_cleanup: 0,
    external_installer_target: {
      path: "C:\\Users\\cyhuh\\AppData\\Local\\Temp\\daon-user-desktop-installer-J8IhJv",
      exists_after_cleanup: false
    },
    user_existing_data_or_other_paths_changed: 0
  }
});

await writeJson("app-navigation-validation.json", {
  schema_version: "1.0",
  work_order: "R1-M3-02",
  issue_id: "R1-M3-02-REVIEW-REPRO-L4",
  status: "PASS",
  application: { id: "com.daon.user", build: "installed Release app", observation_owner: "어울1 independent L4 observation" },
  window_states: captureArtifacts.slice(0, 4).map((capture) => ({
    target_content: capture.content_target,
    captured_outer_pixels: capture.captured_outer_pixels,
    frame_delta: `${capture.dimensions.width - Number(capture.content_target.split("x")[0])}x${capture.dimensions.height - Number(capture.content_target.split("x")[1])}`,
    state_preserved: true,
    horizontal_overflow_px: 0,
    screenshot: capture.path
  })),
  routes_at_500x900: [
    ["Home", "Home"],
    ["Workspace", "Workspace"],
    ["Notifications", "Notifications"],
    ["Account", "Account"],
    ["Organization", "Organization"],
    ["Operations", "운영·알림·복구 / 운영 상태·복구"]
  ].map(([label, accessibility_region]) => ({ label, click: "PASS", accessibility_region })),
  accessibility: {
    keyboard_tab: "PASS",
    visible_focus: "double blue focus ring on operations status button",
    button: "45 단추 API·Worker·DB·Object Storage 안전 상태 설명",
    tooltip: "46 도구 설명 API·Worker·DB·Object Storage 안전 상태. 현재 화면은 Production-bound Prototype이며 실제 Adapter 연결은 후속 Work Order가 소유합니다.",
    escape: "tooltip node removed; trigger button retained",
    screenshots: captureArtifacts.slice(4, 6).map((capture) => capture.path)
  },
  negative_states: {
    unavailable: { selected: "Web · unavailable", represented_as_success_or_healthy: false, screenshot: captureArtifacts[7].path },
    error: { selected: "Web · error", represented_as_success_or_healthy: false, screenshot: captureArtifacts[6].path }
  },
  restart: {
    home: "Evidence Hub confirmed",
    workspace: ["WORKSPACE", "Release 1 운영 준비", "실행 unavailable", "two-pane", "프로토타입 데이터"]
  },
  console: { status: "not_observable_in_release_build", pass_inferred: false },
  actual_external_effects: 0
});

await writeJson("app-process-lifecycle.json", {
  schema_version: "1.0",
  work_order: "R1-M3-02",
  issue_id: "R1-M3-02-REVIEW-REPRO-L4",
  runtime: {
    root_pid: 97560,
    client_rect: "1920x1080",
    child_processes: { conhost: 1, microsoft_webview2_runtime: 6, app_defined_local_service_backend_dev_server: 0 },
    tcp_endpoints: 0,
    tcp_listeners: 0,
    remote_connections: 0,
    udp_endpoints: 0,
    remote_content_or_dev_server: 0
  },
  normal_exit: {
    app_is_running: false,
    windows: 0,
    daon_user_desktop_processes: 0,
    related_webview2_processes: 0,
    tcp_endpoints: 0,
    udp_endpoints: 0
  },
  restart: "PASS; installed app Home and Workspace rechecked",
  final_exit: "PASS; process/window/port counts remained zero",
  install_and_build_target_cleanup: "removed_after_main_designer_review",
  cleanup: {
    uninstaller: "NSIS uninstall.exe /S",
    uninstaller_exit_code: 0,
    installed_directory_exists_after_cleanup: false,
    uninstall_registry_exists_after_cleanup: false,
    daon_app_processes_after_cleanup: 0,
    external_installer_target_exists_after_cleanup: false,
    user_existing_data_or_other_paths_changed: 0
  }
});

const evidenceFiles = [
  "source-artifact-manifest.json",
  "desktop-shell-build.json",
  "installer-validation.json",
  "app-navigation-validation.json",
  "app-process-lifecycle.json",
  "dependency-graph.json",
  "independence-violations.json",
  "quality-gate-result.json",
  "quality-gate-summary.md",
  "npm-audit-fix04.json",
  "npm-ls-fix04.json",
  "server-validation-manifest.json",
  "server-validation-summary.md",
  ...captures.map(([filename]) => filename)
];
const evidenceArtifacts = await Promise.all(evidenceFiles.map((filename) => artifact(`docs/03_evidence/release_1/R1-M3-02/${filename}`)));

await writeJson("evidence-manifest.json", {
  schema_version: "2.0",
  work_order: "R1-M3-02",
  issue_id: issueId,
  status: "COMPLETED",
  base_commit: "74febf3bf7a8828d3bb426b74ce2cb510669fb6b",
  environment: "isolated Windows worktree; installed Tauri Release app; no external deployment",
  approval: { gate: "G2-UX", approval_id: "APR-G2-UX-20260723-01", decision: "GO" },
  source_artifact_manifest: await artifact("docs/03_evidence/release_1/R1-M3-02/source-artifact-manifest.json"),
  external_build_artifacts: [
    {
      path: "C:\\Users\\cyhuh\\AppData\\Local\\Temp\\daon-user-desktop-installer-J8IhJv\\release\\bundle\\nsis\\Daon 사용자 프로그램_0.1.0_x64-setup.exe",
      bytes: 1366987,
      sha256: "F92AAD047033AC6AD6C06464E5C596F605D5ED94E075EEED807D010669DD2918",
      bundle_type: "NSIS x64",
      signature: "unsigned_development",
      metadata_role: "historical_reproducibility_record",
      retained_for_main_designer_review: false,
      disposition: "removed_after_main_designer_review",
      exists_after_cleanup: false
    },
    {
      path: "C:\\Users\\cyhuh\\AppData\\Local\\Daon 사용자 프로그램\\daon-user-desktop.exe",
      bytes: 4066304,
      sha256: "0D77B7B1CA1A0724D8D364776384B98FE6AA1305123431CAAEEC141328165348",
      metadata_role: "historical_reproducibility_record",
      retained_for_main_designer_review: false,
      disposition: "removed_after_main_designer_review",
      exists_after_cleanup: false
    }
  ],
  quality_boundaries: {
    production_assets: "actual",
    tauri_window_and_nsis_install: "actual",
    shared_react_ui: "actual",
    backend_db_llm_file_delivery: "deferred_actual or unavailable",
    local_service_ipc_loopback: "N/A; R1-M3-03",
    db_migration: "N/A",
    signature_update_rollback: "deferred",
    console: "not_observable_in_release_build; PASS not inferred",
    server_arm64: "pre-fix missing icon.png failure recorded; post-fix server rerun not performed by developer subagent",
    actual_external_effects: 0
  },
  server_validation: {
    status: "PASS",
    observation_owner: "어울1 main designer",
    exact_git_sha: "0a4c76b1ba9c165bd0adfbcd62dccdabc8f716d5",
    manifest: "docs/03_evidence/release_1/R1-M3-02/server-validation-manifest.json",
    summary: "docs/03_evidence/release_1/R1-M3-02/server-validation-summary.md",
    developer_subagent_server_rerun: false
  },
  evidence_artifacts: evidenceArtifacts,
  mutable_handoff_records: mutableHandoffRecords,
  deployment: { commit: false, push: false, pull_request: false, merge: false, ysna_server: false, oracle_cloud: false },
  cleanup: {
    repository_cargo_targets: 0,
    generated_schemas: 0,
    non_windows_icons: 0,
    cross_platform_source_derived_png: 1,
    fix_03_gen_preservation: "behavior_tested_exit_0_exit_23_spawn_error_preexisting_gen_other_worktree",
    installation_and_external_installer_target: "removed_after_main_designer_review",
    nsis_uninstaller: {
      command: "uninstall.exe /S",
      exit_code: 0
    },
    installed_directory: {
      path: "C:\\Users\\cyhuh\\AppData\\Local\\Daon 사용자 프로그램",
      exists_after_cleanup: false
    },
    uninstall_registry: {
      path: "HKCU\\Uninstall\\Daon 사용자 프로그램",
      exists_after_cleanup: false
    },
    daon_app_processes_after_cleanup: 0,
    external_installer_target: {
      path: "C:\\Users\\cyhuh\\AppData\\Local\\Temp\\daon-user-desktop-installer-J8IhJv",
      exists_after_cleanup: false
    },
    user_existing_data_or_other_paths_changed: 0
  }
});

const manifest = await artifact("docs/03_evidence/release_1/R1-M3-02/evidence-manifest.json");
console.log(JSON.stringify({ source_inputs: sourceManifest.build_inputs.length, validation_inputs: sourceManifest.validation_inputs.length, evidence_artifacts: evidenceArtifacts.length, manifest }, null, 2));
