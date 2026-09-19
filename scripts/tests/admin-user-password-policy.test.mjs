import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

test("관리자 사용자 등록은 서버 비밀번호 정책인 12자 이상을 화면에서 요구한다", async () => {
  const source = await readFile(path.resolve(import.meta.dirname, "../../apps/web/components/admin-user-console.jsx"), "utf8");
  assert.match(source, /type="password" minLength=\{12\}/u);
});
