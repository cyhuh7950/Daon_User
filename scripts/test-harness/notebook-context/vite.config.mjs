import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(root, "../../..");

export default {
  root,
  server: { host: "127.0.0.1", port: 4211, strictPort: true, fs: { allow: [projectRoot] } },
  build: { emptyOutDir: true, outDir: path.resolve(root, ".dist") },
};
