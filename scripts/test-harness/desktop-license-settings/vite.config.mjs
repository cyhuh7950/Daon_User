import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const outDir = process.env.DAON_DESKTOP_LICENSE_EVIDENCE_DIST;
if (!outDir) throw new Error("DAON_DESKTOP_LICENSE_EVIDENCE_DIST is required");
export default { root, build: { emptyOutDir: true, outDir: path.resolve(outDir) } };
