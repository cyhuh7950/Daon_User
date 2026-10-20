const args = process.argv.slice(2);
const options = new Map();
for (let index = 0; index < args.length; index += 2) {
  if (!new Set(["--package", "--sha256-cert"]).has(args[index]) || !args[index + 1]
    || options.has(args[index])) {
    process.stderr.write("ANDROID_ASSETLINKS_INPUT_INVALID\n");
    process.exit(1);
  }
  options.set(args[index], args[index + 1]);
}

const packageName = options.get("--package");
const fingerprint = options.get("--sha256-cert");
if (args.length !== 4 || packageName !== "com.sinsan.daon"
  || !/^(?:[0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}$/u.test(fingerprint ?? "")) {
  process.stderr.write("ANDROID_ASSETLINKS_INPUT_INVALID\n");
  process.exit(1);
}

process.stdout.write(`${JSON.stringify([{
  relation: ["delegate_permission/common.handle_all_urls"],
  target: {
    namespace: "android_app", package_name: packageName,
    sha256_cert_fingerprints: [fingerprint.toUpperCase()],
  },
}], null, 2)}\n`);
