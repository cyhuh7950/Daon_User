import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

const runner = await readFile(new URL("../run-phase-e-windows-evidence.mjs", import.meta.url), "utf8");

test("Windows evidence runner는 targetable window 대기를 120초로 제한한다", () => {
  assert.match(runner, /const waitForTargetableWindow = async \(timeoutMs = 120_000\)/u);
  assert.match(runner, /await waitForTargetableWindow\(120_000\)/u);
  assert.match(runner, /EVIDENCE_TARGETABLE_WINDOW_TIMEOUT/u);
});

test("Windows evidence runner는 target 확보 전 credential 입력을 수행하지 않는다", () => {
  const target = runner.indexOf("cdp = await connectCdp()");
  const form = runner.indexOf("await waitFor(cdp, `Boolean(document.querySelector('form[aria-label=\"Windows Native 로그인\"]'))`");
  const input = runner.indexOf("await cdp.eval(setValue('input[name=\"login-id\"]'");
  assert.ok(target >= 0 && form > target && input > form);
});

test("Windows evidence runner는 성공·실패 모두 finally에서 owned 자산을 정리한다", () => {
  const finallyBlock = runner.slice(runner.lastIndexOf("} finally {"));
  assert.match(finallyBlock, /for \(const child of children\.reverse\(\)\) await stop\(child\)/u);
  assert.match(finallyBlock, /await close\(api\)/u);
  assert.match(finallyBlock, /await revokeTestCredential\(credentialTarget\)/u);
  assert.match(finallyBlock, /unlink\(nativeContractPath\)/u);
  assert.match(runner, /taskkill\.exe[\s\S]*?\/PID[\s\S]*?\/T[\s\S]*?\/F/u);
});

test("Windows evidence runner는 standalone seeder와 obsolete targetable_windows marker를 포함하지 않는다", () => {
  assert.doesNotMatch(runner, /session_seeder|targetable_windows/u);
  assert.match(runner, /identity\.password = ""; identity\.access = ""; identity\.refresh = ""; identity\.login = ""/u);
});

test("Windows evidence runner는 GUI 승인 환경에도 Cargo user bin을 명시적으로 전달한다", () => {
  assert.match(runner, /const cargoHome = process\.env\.CARGO_HOME \|\| path\.join\(process\.env\.USERPROFILE/u);
  assert.match(runner, /const cargoBin = path\.join\(cargoHome, "bin"\)/u);
  assert.match(runner, /cargoEnvironment\.PATH = `\$\{cargoBin\};\$\{msvcBin\}/u);
});

test("Windows evidence runner의 기존 Notebook fixture는 Thread와 safe Conversation을 함께 보존한다", () => {
  assert.match(runner, /conversation_thread_ids: \[existing\.thread\][\s\S]*?conversation: \{\s*conversation_thread_id: existing\.thread,\s*answer: \{/u);
  assert.match(runner, /answer: "보존된 대화 답변입니다\."/u);
  assert.match(runner, /citations: \[citation\(\)\]/u);
  assert.doesNotMatch(runner, /conversation_thread_ids: \[existing\.thread\][\s\S]{0,300}?conversation: null/u);
});

test("Windows evidence runner 실패 진단은 safe stage와 요청 종류만 출력한다", () => {
  assert.match(runner, /EVIDENCE_SAFE_DIAGNOSTIC/u);
  assert.match(runner, /request_kinds: Object\.fromEntries\(requestKinds\)/u);
  assert.match(runner, /textarea_present/u);
  assert.match(runner, /console\.error\(JSON\.stringify\(\{ code: "EVIDENCE_SAFE_DIAGNOSTIC", stage: gateStage, request_kinds: Object\.fromEntries\(requestKinds\), dom \}\)\)/u);
});
