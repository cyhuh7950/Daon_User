export function resolveEvidenceMode(args = []) {
  const selected = ["--write", "--no-write", "--check-only"].filter((flag) => args.includes(flag));
  if (selected.length > 1) throw new Error("evidence modes are mutually exclusive");
  if (selected[0] === "--write") return "write";
  if (selected[0] === "--check-only") return "check-only";
  return "compare";
}
