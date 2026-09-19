import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../../apps/web/components/provider-settings-workspace.jsx", import.meta.url), "utf8");

test("provider connection draft exposes a provider endpoint and removes legacy default-model controls", () => {
  assert.match(source, /base_url:\s*connection\.base_url\s*\|\|\s*""/u);
  assert.doesNotMatch(source, /기능별 모델 선택|모델 기능 보정/u);
  assert.doesNotMatch(source, /stepUp|step_up|관리자 확인|관리자 비밀번호|현재 비밀번호|추가 암호/u);
});
