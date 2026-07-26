#!/usr/bin/env node
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const contractPath = path.join(root, "packages/contracts/mobile-studio-actions.json");
const sourcePath = path.join(root, "packages/ui/src/studio-workflow-model.js");
const contract = JSON.parse(await readFile(contractPath, "utf8"));
const source = await import(`${pathToFileURL(sourcePath).href}?verify=${Date.now()}`);

assert.equal(contract.schema_version, "1.0");
assert.equal(contract.generated_from, "packages/ui/src/studio-workflow-model.js");
assert.deepEqual(contract.actions.map((item) => item.action), source.MOBILE_STUDIO_ACTIONS);
assert.equal(contract.actions.length, 15);
for (const item of contract.actions) assert.deepEqual(item.decision, source.evaluateMobileAction(item.action), item.action);
console.log("mobile studio contract verified: 15/15 actions");
