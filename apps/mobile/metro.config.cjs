const path = require("node:path");
const { getDefaultConfig, mergeConfig } = require("@react-native/metro-config");

const projectRoot = __dirname;
const repositoryRoot = path.resolve(projectRoot, "../..");

module.exports = mergeConfig(getDefaultConfig(projectRoot), {
  projectRoot,
  watchFolders: [repositoryRoot],
  resolver: { nodeModulesPaths: [path.join(repositoryRoot, "node_modules"), path.join(projectRoot, "node_modules")] }
});
