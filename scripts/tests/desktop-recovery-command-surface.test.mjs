import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");
const COMMANDS = [
  "recovery_cloud_create_backup",
  "recovery_cloud_list_backups",
  "recovery_cloud_get_backup",
  "recovery_cloud_preview_restore",
  "recovery_cloud_get_restore",
  "recovery_cloud_execute_restore",
  "recovery_cloud_cancel_restore",
  "recovery_local_start_scan",
  "recovery_local_get_job",
  "recovery_local_repair_job"
];

test("Recovery Tauri Command surface는 전용 Cloud 7·Local 3 Command와 앱 수명 Runtime만 노출한다", async () => {
  const lib = await read("apps/desktop/src-tauri/src/lib.rs");
  const bridge = await read("apps/desktop/src-tauri/src/recovery_bridge.rs");
  for (const command of COMMANDS) assert.match(lib, new RegExp(`\\b${command}\\b`, "u"));
  const handler = lib.match(/tauri::generate_handler!\[(?<commands>[\s\S]*?)\]\)/u);
  assert.ok(handler);
  const registeredRecoveryCommands = [
    ...handler.groups.commands.matchAll(/\b(recovery_(?:cloud|local)_[a-z_]+)\b/gu)
  ].map((match) => match[1]);
  assert.deepEqual(registeredRecoveryCommands, COMMANDS);
  assert.match(lib, /app\.manage\(NativeRecoveryRuntime::new\(\)\)/u);
  assert.match(bridge, /pub struct NativeRecoveryRuntime/u);
  assert.match(bridge, /#\[serde\(deny_unknown_fields\)\]/u);
  assert.doesNotMatch(lib, /recovery_(?:cloud|local)_execute\b|recovery_request\b/u);
  const inputStructs = [...bridge.matchAll(
    /#\[serde\(deny_unknown_fields\)\]\s*pub struct \w+CommandInput\s*\{(?<fields>[\s\S]*?)\n\}/gu
  )];
  assert.equal(inputStructs.length, 10);
  for (const input of inputStructs) {
    assert.doesNotMatch(input.groups.fields, /\b(?:gateway|authorization|method|path|body)\s*:/u);
  }
  for (const command of COMMANDS.filter((name) => name.startsWith("recovery_local_"))) {
    assert.match(bridge, new RegExp(`pub async fn ${command}\\b`, "u"));
  }
  assert.equal((bridge.match(/tauri::async_runtime::spawn_blocking\(/gu) ?? []).length, 3);
});
