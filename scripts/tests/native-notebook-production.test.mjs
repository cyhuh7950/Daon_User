import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { createNativeNotebookBridge } from "../../apps/desktop/src/native-notebook-bridge.js";
import { concealProtectedDesktop, revealProtectedDesktop } from "../../apps/desktop/src/desktop-protected-route.js";

const VIEW = { notebook_id: "notebook-1", title: "Notebook", source_count: 1, output_count: 0, updated_at: "2026-08-20T01:02:03Z", status: "active" };
const CONTEXT = { notebook_id: "notebook-1", sources: [{ source_id: "source-1", source_version_id: "version-1" }], knowledge_context_ids: [], conversation_thread_ids: [], studio_output_ids: [], output_version_ids: [], generation_settings_ids: [], source_deletion_requests: [], conversation: null };

test("Native Notebook Home은 명시 선택만 list/create/get/context exact command로 전송한다", async () => {
  const calls = [];
  const bridge = createNativeNotebookBridge({ invoke: async (command, args) => {
    calls.push([command, args]);
    if (command === "notebook_list") return [VIEW];
    if (command === "notebook_create") return VIEW;
    if (command === "notebook_get") return VIEW;
    if (command === "notebook_context") return CONTEXT;
    throw new Error("unexpected");
  } });
  assert.deepEqual(await bridge.list("workspace-1"), [VIEW]);
  await bridge.create("workspace-1", { title: "Notebook", description: null }, "request-0123456789abcdef");
  await bridge.get("workspace-1", "notebook-1");
  await bridge.context("workspace-1", "notebook-1");
  assert.deepEqual(calls.map(([command]) => command), ["notebook_list", "notebook_create", "notebook_get", "notebook_context"]);
  assert.equal(calls[1][1].input.request_idempotency_key, "request-0123456789abcdef");
  assert.equal(calls[2][1].input.notebook_id, "notebook-1");
  assert.equal(calls[3][1].input.notebook_id, "notebook-1");
});

test("Native Notebook projection은 extra/cross-id를 fail-close한다", async () => {
  const extra = createNativeNotebookBridge({ invoke: async () => ({ ...VIEW, internal_url: "http://internal" }) });
  await assert.rejects(extra.get("workspace-1", "notebook-1"), /NOTEBOOK_RESPONSE_INVALID/u);
  const cross = createNativeNotebookBridge({ invoke: async () => ({ ...CONTEXT, notebook_id: "notebook-other" }) });
  await assert.rejects(cross.context("workspace-1", "notebook-1"), /NOTEBOOK_CONTEXT_INVALID/u);
});

test("Native Notebook Context는 Source 삭제 요청과 다중 대화 thread를 shared projection과 동일하게 보존한다", async () => {
  const context = {
    ...CONTEXT,
    conversation_thread_ids: ["thread-1", "thread-2"],
    source_deletion_requests: [{
      request_id: "request-1", source_id: "source-1", state: "cleanup_pending", version: 1,
      grace_until: "2026-08-23T00:00:00Z", legal_hold_active: false,
    }],
  };
  const bridge = createNativeNotebookBridge({ invoke: async () => context });
  assert.deepEqual(await bridge.context("workspace-1", "notebook-1"), context);
});

test("production Desktop entry는 fixture notebook prop 없이 Home 선택 state를 소유한다", async () => {
  const [entry, shell] = await Promise.all([
    readFile(new URL("../../apps/desktop/src/main.jsx", import.meta.url), "utf8"),
    readFile(new URL("../../apps/desktop/src/desktop-shell.jsx", import.meta.url), "utf8"),
  ]);
  assert.match(entry, /<DesktopShell\s*\/>/u);
  assert.doesNotMatch(entry, /notebookId=/u);
  assert.doesNotMatch(shell, /notebookId\s*=\s*null/u);
  assert.match(shell, /<NotebookHome/u);
  assert.match(shell, /notebookBridge\.get/u);
  assert.match(shell, /notebookBridge\.context/u);
  assert.doesNotMatch(shell, /notebooks\[0\]/u);
  assert.match(shell, /pagehide/u);
  assert.match(shell, /pageshow/u);
});

test("Windows BFCache 보호막은 session 재검증 전에 protected root를 동기 conceal한다", () => {
  const attributes = new Map();
  const element = { setAttribute: (key, value) => attributes.set(key, value), removeAttribute: (key) => attributes.delete(key) };
  const documentRef = { documentElement: element, getElementById: (id) => id === "root" ? element : null };
  concealProtectedDesktop(documentRef);
  assert.equal(attributes.get("data-desktop-protected-concealed"), "true");
  assert.equal(attributes.get("inert"), "");
  revealProtectedDesktop(documentRef);
  assert.equal(attributes.size, 0);
});

test("contract-test Tauri entry는 explicit loopback과 고유 Credential target만 허용한다", async () => {
  const [lib, session, runner] = await Promise.all([
    readFile(new URL("../../apps/desktop/src-tauri/src/lib.rs", import.meta.url), "utf8"),
    readFile(new URL("../../apps/desktop/src-tauri/src/native_session.rs", import.meta.url), "utf8"),
    readFile(new URL("../run-phase-e-windows-evidence.mjs", import.meta.url), "utf8"),
  ]);
  assert.match(lib, /#\[cfg\(feature = "contract-test"\)\][\s\S]*?DAON_CONTRACT_TEST_GATEWAY/u);
  assert.match(lib, /contract_test_runtime_from_env/u);
  assert.match(lib, /daon-phase-e-native-contract\.conf/u);
  assert.match(lib, /DAON_CONTRACT_TEST_LOGIN_ID/u);
  assert.match(lib, /DAON_CONTRACT_TEST_PASSWORD/u);
  assert.match(lib, /remove_var/u);
  assert.match(lib, /zeroize/u);
  assert.match(lib, /runtime\.login/u);
  assert.match(lib, /CONTRACT_TEST_BOOTSTRAP_FAILED/u);
  assert.doesNotMatch(lib, /direct_session_insert/u);
  assert.match(session, /#\[cfg\(feature = "contract-test"\)\][\s\S]*?pub\(crate\) fn for_loopback_contract_test/u);
  assert.match(session, /NativeSessionVault::new\(credential_target\.to_owned\(\)\)/u);
  assert.match(session, /NativeIdentityClient::for_contract_test/u);
  assert.match(session, /NativeWorkspaceClient::for_contract_test/u);
  assert.match(runner, /--restore-session-window/u);
  assert.match(runner, /DAON_CONTRACT_TEST_LOGIN_ID/u);
  assert.match(runner, /DAON_CONTRACT_TEST_PASSWORD/u);
  assert.match(runner, /waitForTargetableWindow\(120_000\)/u);
  assert.match(runner, /EVIDENCE_TARGETABLE_WINDOW_TIMEOUT/u);
  assert.doesNotMatch(runner, /session_seeder/u);
});
