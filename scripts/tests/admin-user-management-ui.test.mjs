import assert from "node:assert/strict";
import test from "node:test";

import { changeCurrentPassword } from "../../apps/web/lib/auth-api.js";
import { changeAdminUserState, listAdminUsers } from "../../apps/web/lib/admin-users-api.js";

const user = Object.freeze({ user_id: "user-1", login_id: "person", has_email: true, state: "active", protected: false });
const pendingEmailUser = Object.freeze({ user_id: "user-2", login_id: "pending-person", has_email: true, state: "pending_email", protected: false });
const meta = Object.freeze({ trace_id: "trace-1" });

test("admin user client는 pending_email 사용자가 포함된 실제 목록 응답을 수용한다", async () => {
  const result = await listAdminUsers({
    fetchImpl: async () => Response.json({ data: { users: [user, pendingEmailUser] }, meta }),
  });
  assert.deepEqual(result, [user, pendingEmailUser]);
});

test("admin user client는 exact same-origin 목록과 상태 변경 계약만 수용한다", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, method: init.method, credentials: init.credentials, headers: init.headers, body: init.body });
    return init.method === "GET"
      ? Response.json({ data: { users: [user] }, meta })
      : Response.json({ data: { user: { ...user, state: JSON.parse(init.body).state }, replayed: false }, meta });
  };
  assert.deepEqual(await listAdminUsers({ fetchImpl }), [user]);
  assert.equal((await changeAdminUserState("user-1", "suspended", { fetchImpl, idempotencyKey: "admin-state-0001" })).state, "suspended");
  assert.equal((await changeAdminUserState("user-1", "active", { fetchImpl, idempotencyKey: "admin-state-0002" })).state, "active");
  assert.deepEqual(calls.map(({ url, method, credentials }) => ({ url, method, credentials })), [
    { url: "/bff/api/admin/users", method: "GET", credentials: "same-origin" },
    { url: "/bff/api/admin/users/user-1/state", method: "PATCH", credentials: "same-origin" },
    { url: "/bff/api/admin/users/user-1/state", method: "PATCH", credentials: "same-origin" },
  ]);
  assert.equal(calls[1].headers["Idempotency-Key"], "admin-state-0001");
  assert.equal(calls[1].body, '{"state":"suspended"}');
  assert.equal(calls[2].headers["Idempotency-Key"], "admin-state-0002");
  assert.equal(calls[2].body, '{"state":"active"}');
  await assert.rejects(listAdminUsers({ fetchImpl: async () => Response.json({ data: { users: [{ ...user, secret: "blocked" }] }, meta }) }), /ADMIN_USERS_RESPONSE_INVALID/u);
  let inputCalls = 0;
  await assert.rejects(changeAdminUserState("../user", "suspended", { fetchImpl: async () => { inputCalls += 1; }, idempotencyKey: "admin-state-0002" }), /ADMIN_USER_INPUT_INVALID/u);
  await assert.rejects(changeAdminUserState("user-2", "pending_email", { fetchImpl: async () => { inputCalls += 1; }, idempotencyKey: "admin-state-0003" }), /ADMIN_USER_INPUT_INVALID/u);
  assert.equal(inputCalls, 0);
  await assert.rejects(changeAdminUserState("user-1", "active", {
    fetchImpl: async () => Response.json({ data: { user: pendingEmailUser, replayed: false }, meta }),
    idempotencyKey: "admin-state-0004",
  }), /ADMIN_USERS_RESPONSE_INVALID/u);
});

test("password change client는 exact payload를 전송하고 safe response만 수용한다", async () => {
  const calls = [];
  const result = await changeCurrentPassword("current value", "replacement value", { fetchImpl: async (url, init) => {
    calls.push({ url, init });
    return Response.json({ data: { status: "password_changed" }, meta });
  } });
  assert.equal(result.status, "password_changed");
  assert.equal(calls[0].url, "/bff/api/auth/password/change");
  assert.equal(calls[0].init.credentials, "same-origin");
  assert.equal(calls[0].init.body, '{"current_password":"current value","new_password":"replacement value"}');
  await assert.rejects(changeCurrentPassword("current value", "replacement value", {
    fetchImpl: async () => Response.json({ data: { status: "password_changed", session: "leaked" }, meta }),
  }), /PASSWORD_CHANGE_RESPONSE_INVALID/u);
});
