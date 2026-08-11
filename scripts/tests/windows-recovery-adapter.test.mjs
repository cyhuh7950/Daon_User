import assert from "node:assert/strict";
import test from "node:test";

const adapterModule = new URL("../../apps/desktop/src/windows-recovery-adapter.js", import.meta.url);
const sessionModule = new URL("../../apps/desktop/src/native-session-bridge.js", import.meta.url);

test("Windows Recovery Adapter는 Cloud 7·Local 3 전용 Command만 exact input으로 호출한다", async () => {
  const { WindowsRecoveryAdapter } = await import(adapterModule);
  const calls = [];
  const adapter = new WindowsRecoveryAdapter({ invoke: async (command, args) => {
    calls.push({ command, args });
    return command.startsWith("recovery_cloud_") ? { data: [], etag: null } : {
      job_id: "fixture-recovery-0123456789abcdef01234567", version: 1,
      state: "repairable", target_id: "fixture-target", journal_present: true,
      recorded_at: "2026-08-11T00:00:00Z", previous_version: null, integrity: "mismatch"
    };
  } });
  await adapter.listBackups("workspace-1");
  await adapter.createBackup({ workspace_id: "workspace-1", trigger: "manual", schema_revision: "0006", retention_watermark: "current-retention", objects: [{ object_id: "object-1", checksum_sha256: "a".repeat(64), byte_size: 1 }] }, "backup-idempotency-1");
  await adapter.getBackup("backup-1");
  await adapter.previewRestore("backup-1", { destination: { tenant_id: "tenant-1", workspace_id: "workspace-1", database_id: "database-1", bucket_id: "bucket-1" }, step_up_authorization_id: "step-up-preview-1" }, "preview-idempotency-1");
  await adapter.getRestore("restore-1");
  await adapter.executeRestore("restore-1", { preview_version: 2, step_up_authorization_id: "step-up-execute-1" }, '"restore:restore-1:2"', "execute-idempotency-1");
  await adapter.cancelRestore("restore-1", '"restore:restore-1:3"', "cancel-idempotency-1");
  await adapter.startRecoveryScan({ workspace_id: "workspace-1", target_id: "fixture-target", snapshot_checksum: "a".repeat(64), metadata_checksum: "b".repeat(64), actual_checksum: "c".repeat(64), journal_present: true });
  await adapter.getRecoveryJob("fixture-recovery-0123456789abcdef01234567");
  await adapter.repairRecoveryJob("fixture-recovery-0123456789abcdef01234567", { workspace_id: "workspace-1", expected_version: 1 });
  assert.deepEqual(calls.map(({ command }) => command), [
    "recovery_cloud_list_backups", "recovery_cloud_create_backup", "recovery_cloud_get_backup",
    "recovery_cloud_preview_restore", "recovery_cloud_get_restore", "recovery_cloud_execute_restore",
    "recovery_cloud_cancel_restore", "recovery_local_start_scan", "recovery_local_get_job",
    "recovery_local_repair_job"
  ]);
  assert.deepEqual(calls[0].args, { input: { workspace_id: "workspace-1" } });
  assert.equal(calls[1].args.input.idempotency_key, "backup-idempotency-1");
  assert.deepEqual(calls[2].args, { input: { backup_id: "backup-1" } });
  assert.equal(calls[3].args.input.step_up_authorization_id, "step-up-preview-1");
  assert.deepEqual(calls[4].args, { input: { restore_request_id: "restore-1" } });
  assert.deepEqual(calls[5].args.input, { restore_request_id: "restore-1", preview_version: 2, step_up_authorization_id: "step-up-execute-1", idempotency_key: "execute-idempotency-1", if_match: '"restore:restore-1:2"' });
  assert.deepEqual(calls[6].args.input, { restore_request_id: "restore-1", idempotency_key: "cancel-idempotency-1", if_match: '"restore:restore-1:3"' });
  assert.equal(calls[7].args.input.journal_present, true);
  assert.deepEqual(calls[8].args, { input: { job_id: "fixture-recovery-0123456789abcdef01234567" } });
  assert.deepEqual(calls[9].args, { input: { job_id: "fixture-recovery-0123456789abcdef01234567", workspace_id: "workspace-1", expected_version: 1 } });
});

test("Adapter는 Session/Local 미준비와 unknown input·unsafe error를 fail-close한다", async () => {
  const { WindowsRecoveryAdapter } = await import(adapterModule);
  await assert.rejects(new WindowsRecoveryAdapter({ invoke: null }).listBackups("workspace-1"), { code: "AUTHENTICATION_REQUIRED" });
  await assert.rejects(new WindowsRecoveryAdapter({ invoke: null }).getRecoveryJob("job-1"), { code: "LOCAL_SERVICE_UNAVAILABLE" });
  const adapter = new WindowsRecoveryAdapter({ invoke: async () => { throw { code: "attacker_error", trace_id: "https://internal.example/token" }; } });
  const unsafeError = await adapter.listBackups("workspace-1").catch((error) => error);
  assert.equal(unsafeError.code, "CLOUD_RECOVERY_RESPONSE_REJECTED");
  assert.equal("traceId" in unsafeError, false);
  await assert.rejects(adapter.listBackups({ workspace_id: "workspace-1", gateway: "https://attacker" }), { code: "INVALID_REQUEST" });
  const unsafeResponse = new WindowsRecoveryAdapter({ invoke: async () => ({ data: [], etag: null, authorization: "secret" }) });
  await assert.rejects(unsafeResponse.listBackups("workspace-1"), { code: "CLOUD_RECOVERY_RESPONSE_REJECTED" });
});

test("Adapter는 승인된 Rust Safe Error를 exact-key로 보존하고 신규 범용 공개 코드를 만들지 않는다", async () => {
  const { WindowsRecoveryAdapter } = await import(adapterModule);
  const cloudCodes = [
    "AUTHENTICATION_REQUIRED", "FORBIDDEN", "CURRENT_ACCESS_DENIED", "STEP_UP_REQUIRED",
    "INVALID_REQUEST", "RESOURCE_UNAVAILABLE", "CONFLICT", "NOT_FOUND",
    "RESTORE_DESTINATION_NOT_ALLOWED", "PRECONDITION_FAILED", "CLOUD_RECOVERY_INPUT_INVALID",
    "CLOUD_RECOVERY_RESPONSE_REJECTED", "CLOUD_RECOVERY_REQUEST_FAILED"
  ];
  for (const code of cloudCodes) {
    const adapter = new WindowsRecoveryAdapter({ invoke: async () => { throw { code, trace_id: "0123456789abcdef0123456789abcdef", retryable: code === "RESOURCE_UNAVAILABLE" }; } });
    const error = await adapter.listBackups("workspace-1").catch((caught) => caught);
    assert.equal(error.code, code);
    assert.equal(error.traceId, "0123456789abcdef0123456789abcdef");
    assert.equal(error.retryable, code === "RESOURCE_UNAVAILABLE");
  }
  const extra = new WindowsRecoveryAdapter({ invoke: async () => { throw { code: "FORBIDDEN", trace_id: "0123456789abcdef0123456789abcdef", retryable: false, gateway: "secret" }; } });
  await assert.rejects(extra.listBackups("workspace-1"), { code: "CLOUD_RECOVERY_RESPONSE_REJECTED" });
  const source = await import("node:fs/promises").then(({ readFile }) => readFile(adapterModule, "utf8"));
  assert.doesNotMatch(source, /"RECOVERY_(?:INPUT_INVALID|RESPONSE_REJECTED|COMMAND_FAILED)"/);
});

test("Adapter는 정확한 32자리 소문자 hex가 아닌 Trace를 원문 반사 없이 거부한다", async () => {
  const { WindowsRecoveryAdapter } = await import(adapterModule);
  for (const traceId of [
    "access-token-0123456789abcdef",
    "0123456789abcdef",
    "0123456789ABCDEF0123456789ABCDEF",
    "01234567.9abcdef0123456789abcdef",
    "01234567:9abcdef0123456789abcdef",
    "01234567_9abcdef0123456789abcdef",
    "01234567-9abcdef0123456789abcdef"
  ]) {
    const adapter = new WindowsRecoveryAdapter({ invoke: async () => {
      throw { code: "FORBIDDEN", trace_id: traceId, retryable: false };
    } });
    const error = await adapter.listBackups("workspace-1").catch((caught) => caught);
    assert.equal(error.code, "CLOUD_RECOVERY_RESPONSE_REJECTED");
    assert.equal("traceId" in error, false);
    assert.doesNotMatch(error.message, new RegExp(traceId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
});

test("Native Session·Recovery Authorization Bridge는 Rust exact-key Safe Projection만 전달한다", async () => {
  const { createNativeSessionBridge } = await import(sessionModule);
  const session = { user_id: "user-1", tenant_id: "tenant-1", workspace_id: "workspace-1", session_id: "session-1", device_id: "device-1", expires_at: "2026-08-11T01:00:00Z" };
  const operations = ["cloud_backup_create", "cloud_backup_get", "cloud_backup_list", "cloud_restore_cancel", "cloud_restore_execute", "cloud_restore_get", "cloud_restore_preview"];
  const calls = [];
  const bridge = createNativeSessionBridge({ invoke: async (command, args) => {
    calls.push({ command, args });
    if (command === "native_recovery_authorization_status") return { recovery_operations: operations };
    if (command === "native_session_status" || command === "native_login") return { authenticated: true, session };
    return { authenticated: false, session: null };
  } });
  assert.deepEqual(await bridge.status(), { authenticated: true, userId: "user-1", tenantId: "tenant-1", workspaceId: "workspace-1", sessionId: "session-1", deviceId: "device-1", expiresAt: "2026-08-11T01:00:00Z" });
  assert.deepEqual(await bridge.recoveryAuthorizationStatus(), { recoveryOperations: operations });
  assert.deepEqual(calls.at(-1), { command: "native_recovery_authorization_status", args: undefined });
  assert.deepEqual(await bridge.login("user-1", "password-value"), { authenticated: true, userId: "user-1", tenantId: "tenant-1", workspaceId: "workspace-1", sessionId: "session-1", deviceId: "device-1", expiresAt: "2026-08-11T01:00:00Z" });
  assert.deepEqual(calls.at(-1), { command: "native_login", args: { loginId: "user-1", password: "password-value" } });
  assert.deepEqual(await bridge.logout(), { authenticated: false });

  const unsafeValues = [
    { authenticated: true, session: { ...session, access_token: "secret" } },
    { authenticated: true, session, authorization: "secret" },
    { authenticated: false },
    { authenticated: false, session: null, role: "admin" }
  ];
  for (const value of unsafeValues) {
    await assert.rejects(createNativeSessionBridge({ invoke: async () => value }).status(), { code: "AUTHENTICATION_REQUIRED" });
  }
  for (const value of [
    { recovery_operations: operations.slice(0, 6) },
    { recovery_operations: [...operations].reverse() },
    { recovery_operations: operations, role: "admin" }
  ]) {
    await assert.rejects(createNativeSessionBridge({ invoke: async () => value }).recoveryAuthorizationStatus(), { code: "AUTHENTICATION_REQUIRED" });
  }

  let updateCount = 0;
  const stop = bridge.watch(() => { updateCount += 1; }, { schedule: () => 1, cancel: () => {} });
  await Promise.resolve();
  stop();
  await Promise.resolve();
  assert.equal(updateCount <= 1, true);
});

test("Native 로그인 제출은 Password input을 즉시 비우고 Logout은 Session을 먼저 제거한다", async () => {
  const { submitNativeLogin, logoutNativeSession } = await import(sessionModule);
  const passwordInput = { value: "password-value" };
  let resolveLogin;
  const changes = [];
  const bridge = {
    login: (loginId, password) => {
      assert.equal(loginId, "user-1");
      assert.equal(password, "password-value");
      return new Promise((resolve) => { resolveLogin = resolve; });
    },
    logout: async () => ({ authenticated: false })
  };
  const pendingLogin = submitNativeLogin({ sessionBridge: bridge, loginId: "user-1", passwordInput, onSessionChange: (value) => changes.push(value) });
  assert.equal(passwordInput.value, "");
  assert.deepEqual(changes, []);
  resolveLogin({ authenticated: true, sessionId: "session-1" });
  await pendingLogin;
  assert.equal(changes[0].sessionId, "session-1");

  await logoutNativeSession({ sessionBridge: bridge, onSessionChange: (value) => changes.push(value) });
  assert.deepEqual(changes.at(-1), { authenticated: false });
});

test("Logout 시작 뒤 Poll은 Native 완료 전의 직전 인증 Session을 다시 노출하지 않는다", async () => {
  const { createNativeSessionBridge } = await import(sessionModule);
  const session = { user_id: "user-1", tenant_id: "tenant-1", workspace_id: "workspace-1", session_id: "session-1", device_id: "device-1", expires_at: "2026-08-11T01:00:00Z" };
  let resolveLogout;
  const bridge = createNativeSessionBridge({ invoke: async (command) => {
    if (command === "native_logout") return new Promise((resolve) => { resolveLogout = resolve; });
    return { authenticated: true, session };
  } });
  const pending = bridge.logout();
  assert.deepEqual(await bridge.status(), { authenticated: false });
  resolveLogout({ authenticated: false, session: null });
  assert.deepEqual(await pending, { authenticated: false });
});
