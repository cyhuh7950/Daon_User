import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync
} from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

const repositoryRoot = path.resolve(import.meta.dirname, "..", "..");
const helper = path.join(repositoryRoot, "scripts", "stage-local-service-sidecar.ps1");

test("stage helper keeps exact boundary, no-overwrite, integrity, and cleanup contracts", () => {
  const source = readFileSync(helper, "utf8");
  assert.match(source, /Resolve-Path -LiteralPath \$Source/u);
  assert.match(source, /apps[\s\S]*desktop[\s\S]*src-tauri[\s\S]*binaries/u);
  assert.match(source, /Refusing to overwrite an existing generated sidecar/u);
  assert.match(source, /sourceInfo\.Length -ne \$targetInfo\.Length/u);
  assert.match(source, /sourceHash -ne \$targetHash/u);
  assert.match(source, /Remove-Item -LiteralPath \$target -Force/u);
  assert.doesNotMatch(source, /Remove-Item[^\r\n]*-Recurse/u);
});

test(
  "Windows stage helper validates boundaries, refuses overwrite, and preserves bytes",
  { skip: process.platform !== "win32" },
  () => {
  const isolated = mkdtempSync(path.join(os.tmpdir(), "daon-sidecar-stage-test-"));
  const workspace = path.join(isolated, "workspace");
  const source = path.join(isolated, "daon-user-local-service.exe");
  const destinationRoot = path.join(
    workspace,
    "apps",
    "desktop",
    "src-tauri",
    "binaries"
  );
  const target = path.join(
    destinationRoot,
    "daon-user-local-service-x86_64-pc-windows-msvc.exe"
  );
  const payload = Buffer.from("safe-sidecar-stage-contract", "utf8");
  try {
    mkdirSync(workspace, { recursive: true });
    writeFileSync(source, payload);
    const args = [
      "-NoProfile",
      "-NonInteractive",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      helper,
      "-Source",
      source,
      "-DestinationRoot",
      destinationRoot,
      "-WorkspaceRoot",
      workspace
    ];
    const first = spawnSync("powershell.exe", args, {
      cwd: repositoryRoot,
      encoding: "utf8",
      windowsHide: true
    });
    assert.equal(first.status, 0, first.stderr);
    assert.deepEqual(readFileSync(target), payload);
    const result = JSON.parse(first.stdout.trim());
    assert.equal(result.bytes, payload.length);
    assert.equal(
      result.sha256,
      createHash("sha256").update(payload).digest("hex").toUpperCase()
    );

    const second = spawnSync("powershell.exe", args, {
      cwd: repositoryRoot,
      encoding: "utf8",
      windowsHide: true
    });
    assert.notEqual(second.status, 0);
    assert.deepEqual(readFileSync(target), payload);

    const outside = path.join(isolated, "outside");
    const rejected = spawnSync(
      "powershell.exe",
      [
        ...args.slice(0, args.indexOf("-DestinationRoot") + 1),
        outside,
        "-WorkspaceRoot",
        workspace
      ],
      {
        cwd: repositoryRoot,
        encoding: "utf8",
        windowsHide: true
      }
    );
    assert.notEqual(rejected.status, 0);
  } finally {
    rmSync(isolated, { recursive: true, force: true });
  }
  }
);
