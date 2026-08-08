import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("가입은 token을 제외한 정확한 세 필드만 전송한다", async () => {
  const source = await readFile(path.join(root, "apps/web/lib/auth-pane.jsx"), "utf8");

  assert.match(
    source,
    /run\("signup",\s*\{\s*login_id:\s*form\.login_id,\s*email:\s*form\.email,\s*password:\s*form\.password\s*\}/s,
  );
  assert.doesNotMatch(source, /run\("signup",\s*form\b/);
});

test("로그인·이메일 인증·비밀번호 재설정 payload 계약을 보존한다", async () => {
  const source = await readFile(path.join(root, "apps/web/lib/auth-pane.jsx"), "utf8");

  assert.match(source, /run\("login",\s*\{\s*login_id:\s*form\.login_id,\s*password:\s*form\.password\s*\}/s);
  assert.match(source, /run\("verify-email",\s*\{\s*token:\s*form\.token\s*\}/s);
  assert.match(source, /run\("resend-verification",\s*\{\s*identifier:\s*form\.login_id\s*\|\|\s*form\.email\s*\}/s);
  assert.match(source, /run\("password-reset\/request",\s*\{\s*identifier:\s*form\.login_id\s*\|\|\s*form\.email\s*\}/s);
  assert.match(source, /run\("password-reset\/confirm",\s*\{\s*token:\s*form\.token,\s*new_password:\s*form\.password\s*\}/s);
});

test("비밀번호 입력은 서버 정책과 같은 최소 12자 HTML 제약을 제공한다", async () => {
  const source = await readFile(path.join(root, "apps/web/lib/auth-pane.jsx"), "utf8");

  assert.match(source, /type="password"[^>]*minLength=\{12\}/);
});
