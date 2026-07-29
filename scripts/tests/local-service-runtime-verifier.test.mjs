import assert from "node:assert/strict";
import test from "node:test";

import {
  assertLoopbackListener,
  assertRuntimeOutputBoundary,
  runPackagedLocalServiceLifecycle
} from "../verify-local-service-runtime.mjs";

test("listener attestation rejects non-loopback bindings", () => {
  assert.throws(
    () =>
      assertLoopbackListener([
        { LocalAddress: "127.0.0.1", LocalPort: 41001, State: "Listen" },
        { LocalAddress: "0.0.0.0", LocalPort: 41001, State: "Listen" }
      ]),
    /non-loopback listener/u
  );
});

test("packaged lifecycle verifier is an exported callable contract", () => {
  assert.equal(typeof runPackagedLocalServiceLifecycle, "function");
});

test("runtime output inspection permits the protocol instance only in ready and rejects secrets", () => {
  const instance = "instance-expected";
  const rootSecret = "root-secret";
  const requestToken = "request-token";
  const ready = `${JSON.stringify({
    event: "ready",
    protocol_version: "1.1",
    app_instance_id: instance,
    port: 48123
  })}\n`;
  assert.deepEqual(
    assertRuntimeOutputBoundary(
      { stdout: ready, stderr: "", stdoutTruncated: false, stderrTruncated: false },
      { rootSecret, requestTokens: [requestToken], appInstanceId: instance }
    ),
    {
      token_emitted: false,
      instance_ready_envelope_only: true,
      output_truncated: false
    }
  );
  assert.throws(
    () =>
      assertRuntimeOutputBoundary(
        {
          stdout: ready,
          stderr: `debug ${rootSecret}`,
          stdoutTruncated: false,
          stderrTruncated: false
        },
        { rootSecret, requestTokens: [requestToken], appInstanceId: instance }
      ),
    /credential appeared in runtime output/u
  );
  assert.throws(
    () =>
      assertRuntimeOutputBoundary(
        {
          stdout: ready,
          stderr: `debug ${requestToken}`,
          stdoutTruncated: false,
          stderrTruncated: false
        },
        { rootSecret, requestTokens: [requestToken], appInstanceId: instance }
      ),
    /credential appeared in runtime output/u
  );
  assert.throws(
    () =>
      assertRuntimeOutputBoundary(
        {
          stdout: `${ready}debug ${instance}`,
          stderr: "",
          stdoutTruncated: false,
          stderrTruncated: false
        },
        { rootSecret, requestTokens: [requestToken], appInstanceId: instance }
      ),
    /instance appeared outside ready envelope/u
  );
});
