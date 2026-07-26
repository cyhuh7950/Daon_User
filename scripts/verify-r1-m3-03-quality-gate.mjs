#!/usr/bin/env node
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import {
  loadQualityGatePolicy,
  runQualityGate
} from "./lib/quality-gate.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const policyPath = path.join(root, "quality-gate-policy.json");

try {
  const policy = await loadQualityGatePolicy(policyPath);
  policy.artifacts = {
    result: "docs/03_evidence/release_1/R1-M3-03/quality-gate-result.json",
    summary: "docs/03_evidence/release_1/R1-M3-03/quality-gate-summary.md"
  };
  const result = await runQualityGate({ root, policy, policyPath });
  console.log(result.summary);
  process.exitCode = result.exitCode;
} catch (error) {
  console.error(
    `R1_M3_03_QUALITY_GATE_EXECUTION_ERROR ${error?.code ?? error?.name ?? "ERROR"}`
  );
  process.exitCode = 2;
}
