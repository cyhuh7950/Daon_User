import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const outDir = process.env.DAON_SCREEN_EVIDENCE_DIST;
if (!outDir) throw new Error("DAON_SCREEN_EVIDENCE_DIST is required");

export default {
  root,
  build: {
    emptyOutDir: true,
    outDir: path.resolve(outDir),
  },
};
