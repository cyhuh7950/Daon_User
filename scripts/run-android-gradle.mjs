import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const androidRoot = path.join(root, "apps", "mobile", "android");
const task = process.argv[2];
const javaHome = process.env.DAON_ANDROID_JAVA_HOME;
const sdkRoot = process.env.DAON_ANDROID_SDK_ROOT;

if (!task || !/^[A-Za-z][A-Za-z0-9]*$/.test(task)) throw new Error("ANDROID_GRADLE_TASK_REQUIRED");
if (!javaHome || !path.isAbsolute(javaHome) || !existsSync(path.join(javaHome, "bin", "java.exe"))) throw new Error("DAON_ANDROID_JAVA_HOME_ABSOLUTE_JBR_REQUIRED");
if (!sdkRoot || !path.isAbsolute(sdkRoot) || !existsSync(path.join(sdkRoot, "platform-tools", "adb.exe"))) throw new Error("DAON_ANDROID_SDK_ROOT_ABSOLUTE_REQUIRED");

const gradle = path.join(androidRoot, "gradlew.bat");
const result = spawnSync(gradle, [task, "--no-daemon", "--stacktrace", "-PreactNativeArchitectures=x86_64"], {
  cwd: androidRoot,
  stdio: "inherit",
  windowsHide: true,
  shell: true,
  env: {
    ...process.env,
    JAVA_HOME: javaHome,
    ANDROID_HOME: sdkRoot,
    ANDROID_SDK_ROOT: sdkRoot,
    PATH: `${path.join(javaHome, "bin")};${path.join(sdkRoot, "platform-tools")};${process.env.PATH ?? ""}`,
  },
});

if (result.error) throw result.error;
process.exit(result.status ?? 1);
