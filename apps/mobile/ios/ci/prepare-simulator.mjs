import { appendFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

const runtimes = JSON.parse(execFileSync("xcrun", ["simctl", "list", "runtimes", "-j"], { encoding: "utf8" })).runtimes
  .filter((runtime) => runtime.isAvailable && runtime.identifier.includes(".SimRuntime.iOS-"))
  .sort((left, right) => right.version.localeCompare(left.version, undefined, { numeric: true }));
if (runtimes.length === 0) throw new Error("IOS_SIMULATOR_RUNTIME_UNAVAILABLE");
const runtime = runtimes[0];
const deviceType = "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro";
const devices = JSON.parse(execFileSync("xcrun", ["simctl", "list", "devicetypes", "-j"], { encoding: "utf8" })).devicetypes;
if (!devices.some((device) => device.identifier === deviceType)) throw new Error("IOS_SIMULATOR_DEVICE_TYPE_UNAVAILABLE");
const name = `Daon Phase A ${process.env.GITHUB_RUN_ID ?? "local"}`;
const udid = execFileSync("xcrun", ["simctl", "create", name, deviceType, runtime.identifier], { encoding: "utf8" }).trim();
try {
  execFileSync("xcrun", ["simctl", "boot", udid]);
} catch (error) {
  execFileSync("xcrun", ["simctl", "delete", udid]);
  throw error;
}
const output = process.env.GITHUB_OUTPUT;
if (!output) throw new Error("GITHUB_OUTPUT_UNAVAILABLE");
appendFileSync(output, `udid=${udid}\nruntime=${runtime.name} ${runtime.version}\ndevice=iPhone 17 Pro\n`);
