import test from "node:test";
import assert from "node:assert/strict";

import { summarizeConnectionUsage } from "./provider-settings-usage.js";

test("summarizes multiple enabled provider connections as routing candidates", () => {
  assert.deepEqual(
    summarizeConnectionUsage([
      { enabled: true },
      { enabled: false },
      { enabled: true },
    ]),
    {
      activeCount: 2,
      label: "사용 후보 2개",
      description: "활성화된 연결은 Provider 선택 후보로 사용됩니다.",
    },
  );
});

test("reports no candidate when every connection is disabled", () => {
  assert.deepEqual(summarizeConnectionUsage([{ enabled: false }]), {
    activeCount: 0,
    label: "사용 후보 없음",
    description: "연결을 활성화하면 Provider 선택 후보가 됩니다.",
  });
});
