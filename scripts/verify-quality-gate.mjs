#!/usr/bin/env node
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { CI_FALLBACK_STEP_IDS, ensureCiFallbackEvidence, loadQualityGatePolicy, runQualityGate } from "./lib/quality-gate.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2).filter((item) => item !== "--");
const policyIndex = args.indexOf("--policy");
const policyPath = policyIndex >= 0 ? path.resolve(args[policyIndex + 1]) : path.join(root, "quality-gate-policy.json");

try {
  if (args.includes("--ci-fallback")) {
    const stepOutcomes = Object.fromEntries(CI_FALLBACK_STEP_IDS.map((id) => [
      id,
      process.env[`CI_STEP_${id.replaceAll("-", "_").toUpperCase()}`]
    ]));
    const result = await ensureCiFallbackEvidence({ root, gitSha: process.env.CI_GIT_SHA, stepOutcomes, policyPath });
    console.log(result.created ? "CI_FALLBACK_EVIDENCE_CREATED" : "CI_GATE_EVIDENCE_PRESERVED");
  } else {
    const policy = await loadQualityGatePolicy(policyPath);
    const result = await runQualityGate({ root, policy, policyPath });
    console.log(result.summary);
    process.exitCode = result.exitCode;
  }
} catch (error) {
  console.error(`QUALITY_GATE_EXECUTION_ERROR ${error?.code ?? error?.name ?? "ERROR"}`);
  process.exitCode = 2;
}
