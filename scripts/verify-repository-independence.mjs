#!/usr/bin/env node
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { loadPolicy, PolicyError, runIndependenceCheck } from "./lib/independence-check.mjs";

const scriptRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
function option(name, fallback) { const index = args.indexOf(name); return index >= 0 ? path.resolve(args[index + 1]) : fallback; }
const root = option("--root", scriptRoot);
const policyPath = option("--policy", path.join(root, "independence-policy.json"));
const shouldWrite = !args.includes("--no-write");

try {
  const policy = await loadPolicy(policyPath);
  const result = await runIndependenceCheck({ root, policy });
  if (shouldWrite) {
    const evidenceDir = path.join(root, "docs", "03_evidence", "release_1", "R1-M1-04");
    await mkdir(evidenceDir, { recursive: true });
    await writeFile(path.join(evidenceDir, "dependency-graph.json"), `${JSON.stringify(result.graph, null, 2)}\n`);
    await writeFile(path.join(evidenceDir, "violations.json"), `${JSON.stringify({ schema_version: "1.0", violation_count: result.violations.length, violations: result.violations }, null, 2)}\n`);
  }
  console.log(`components=${result.stats.component_count} edges=${result.stats.edge_count} scanned_files=${result.stats.scanned_files} violations=${result.violations.length}`);
  for (const item of result.violations) console.error(`${item.rule_id} ${item.file}:${item.line} ${item.evidence}`);
  process.exitCode = result.violations.length === 0 ? 0 : 1;
} catch (error) {
  console.error(`POLICY_OR_SCAN_ERROR ${error.message}`);
  process.exitCode = error instanceof PolicyError ? 2 : 2;
}
