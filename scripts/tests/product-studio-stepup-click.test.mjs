import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";

test("민감 Studio 클릭은 재인증 확인·취소·실패와 고정 Version 토큰 전달을 지킨다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(rootPath, ".product-studio-stepup-react-"));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "studio-stepup" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => /\.m?js$/u.test(name));
    const { ProductStudioPane } = await import(pathToFileURL(path.join(output, entry)).href);
    const calls = []; let failure = false; let release;
    const canonicalVersion = (number, approved = false) => ({
      output_version_id: `version-${number}`, content_version: 1, previous_version_id: null,
      status: approved ? "approved" : "in_review", content: { body: `대상 ${number} 정본 본문` },
      revision_type: "initial", change_reason: "initial_generation", settings_snapshot_id: `settings-${number}`,
      citations: [{ citation_id: `citation-${number}`, source_version_id: `source-version-${number}`, evidence_span_id: `span-${number}`,
        origin: "raw_source", locator: { kind: "page", value: "1" } }],
      review_request_id: `review-${number}`, approval_request_id: `request-${number}`,
      approval_id: approved ? `approval-${number}` : null, delivery_id: null, knowledge_registration_id: null,
      output_format: "pdf", approval_request_state: approved ? "approved" : "pending", approval_expires_at: "2099-01-01T00:00:00Z",
    });
    const versions = new Map([["output-1", canonicalVersion(1)], ["output-2", canonicalVersion(2)]]);
    const adapter = {
      listStudioVersions: async (id) => [structuredClone(versions.get(id))],
      issueStudioStepUp: async (group, target, password) => {
        calls.push(["stepup", group, target, password]);
        if (failure) throw new Error("STEP_UP_REQUIRED");
        if (release === "defer") return new Promise((resolve) => { release = resolve; });
        return "opaque-grant";
      },
      createStudioAction: async (action, payload) => {
        calls.push([action, payload]);
        const current = versions.get("output-1");
        assert.equal(payload.output_version_id, current.output_version_id);
        const recordId = `record-${action}`;
        const canonicalAction = { approvals: "approval", deliveries: "delivery", "knowledge-registrations": "knowledge_registration" }[action];
        assert.ok(canonicalAction, "only the intended sensitive action may be written");
        if (action === "approvals") versions.set("output-1", { ...current, status: "approved", approval_id: recordId, approval_request_state: "approved" });
        else if (action === "deliveries") versions.set("output-1", { ...current, status: "delivered", delivery_id: recordId });
        else versions.set("output-1", { ...current, knowledge_registration_id: recordId });
        return { record_id: recordId, action: canonicalAction, status: action === "knowledge-registrations" ? "registered" : "accepted",
          output_version_id: current.output_version_id,
          ...(action === "knowledge-registrations" ? { registered_source_version_id: "knowledge-version-1", searchable: true } : {}) };
      },
    };
    const state = { workspaceId: "workspace-1", status: "ready", locks: [], selectedOutputType: null, settings: {}, outputs: [
      { studio_output_id: "output-1", output_type: "evidence_report", title: "승인 대상", ...canonicalVersion(1) },
      { studio_output_id: "output-2", output_type: "evidence_report", title: "다른 대상", ...canonicalVersion(2) },
    ], selectedOutputId: "output-1", pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => reactRoot.render(createElement(ProductStudioPane, { state, adapter })));
    const click = async (label) => { assert.ok(buttonByText(container, label), label); await act(async () => { buttonByText(container, label).dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); }); };
    const password = () => findElements(container, (node) => node.tagName === "INPUT" && (node.type === "password" || node.getAttribute("type") === "password"))[0];
    const fill = async () => { assert.ok(password()); const element = password(); const props = element[Object.keys(element).find((key) => key.startsWith("__reactProps"))]; await act(async () => props.onChange({ currentTarget: { value: "fixture password only" } })); };
    await click("승인"); assert.equal(calls.length, 0); assert.ok(password());
    const emptyConfirm = buttonByText(container, "추가 인증 확인");
    assert.equal(emptyConfirm.disabled, true);
    await act(async () => emptyConfirm[Object.keys(emptyConfirm).find(key => key.startsWith("__reactProps"))].onClick());
    assert.equal(calls.length, 0, "empty password must not issue a token or write");
    await fill(); await click("추가 인증 취소"); assert.equal(password(), undefined); assert.equal(calls.length, 0);
    for (const [label, action] of [["승인", "approvals"], ["전달 기록 등록", "deliveries"], ["생산 지식 등록", "knowledge-registrations"]]) {
      // Independent action fixtures use valid current lifecycle states; the
      // following canonical GET reflects that action's authoritative outcome.
      versions.set("output-1", canonicalVersion(1, action !== "approvals"));
      await click("다른 대상"); await click("승인 대상");
      await click(label); await fill();
      const confirm = buttonByText(container, "추가 인증 확인");
      const submit = confirm[Object.keys(confirm).find(key => key.startsWith("__reactProps"))].onClick;
      const beforeIssue = calls.filter(item => item[0] === "stepup").length;
      const beforeWrite = calls.filter(item => item[0] !== "stepup").length;
      await act(async () => { submit(); submit(); });
      assert.equal(calls.filter(item => item[0] === "stepup").length, beforeIssue + 1);
      assert.equal(calls.filter(item => item[0] !== "stepup").length, beforeWrite + 1);
      assert.deepEqual(calls.at(-2), ["stepup", "final_approval_or_knowledge_registration", "version-1", "fixture password only"]);
      assert.equal(calls.at(-1)[0], action); assert.equal(calls.at(-1)[1].output_version_id, "version-1");
      assert.equal(calls.at(-1)[1].step_up_authorization, "opaque-grant"); assert.equal(password(), undefined);
      versions.set("output-1", canonicalVersion(1));
      await click("다른 대상"); await click("승인 대상");
    }
    failure = true; const writes = calls.filter((item) => item[0] !== "stepup").length;
    await click("승인"); await fill(); await click("추가 인증 확인");
    assert.match(container.textContent, /추가 인증을 완료하지 못했습니다/); assert.equal(password()?.value ?? "", "");
    assert.equal(calls.filter((item) => item[0] !== "stepup").length, writes);
    await click("추가 인증 취소"); failure = false; release = "defer";
    await click("승인"); await fill(); await click("추가 인증 확인");
    assert.equal(password()?.value ?? "", ""); await click("다른 대상");
    await act(async () => { release("late-grant"); await Promise.resolve(); await Promise.resolve(); });
    assert.equal(calls.filter((item) => item[0] !== "stepup").length, writes);
    assert.equal(password(), undefined);
    release = "defer"; await click("승인"); await fill(); await click("추가 인증 확인");
    await act(async () => reactRoot.render(createElement(ProductStudioPane, { state, adapter: { ...adapter } })));
    assert.equal(password(), undefined);
    assert.equal(buttonByText(container, "승인").disabled, false);
    await act(async () => { release("old-adapter-grant"); await Promise.resolve(); await Promise.resolve(); });
    assert.equal(calls.filter((item) => item[0] !== "stepup").length, writes);
    release = "defer"; await click("승인"); await fill(); await click("추가 인증 확인");
    await click("추가 인증 취소");
    await act(async () => { release("cancelled-grant"); await Promise.resolve(); await Promise.resolve(); });
    assert.equal(calls.filter((item) => item[0] !== "stepup").length, writes);
    assert.equal(password(), undefined);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});
