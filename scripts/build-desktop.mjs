import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import {
  mkdirSync,
  openSync,
  copyFileSync,
} from "node:fs";
import { join, resolve } from "node:path";
import { homedir } from "node:os";

const require = createRequire(import.meta.url);
mkdirSync("logs", { recursive: true });
const env = {
  ...process.env,
  PATH: `${join(homedir(), ".cargo", "bin")};${process.env.PATH}`,
  CARGO_BUILD_JOBS: "1",
};
const runtimeLog = openSync("logs/portable-payload.log", "w");
const runtime = spawnSync("backend/.venv/Scripts/python.exe", ["scripts/prepare-portable.py"], {
  env, stdio: ["ignore", runtimeLog, runtimeLog], windowsHide: true,
});
if (runtime.status !== 0) { console.error("Portable preparation failed. See logs/portable-payload.log"); process.exit(runtime.status ?? 1); }
const log = openSync("logs/build-desktop.log", "w");
const result = spawnSync(
  process.execPath,
  [require.resolve("@tauri-apps/cli/tauri.js"), "build", "--no-bundle"],
  { env, stdio: ["ignore", log, log], windowsHide: true },
);
if (result.status !== 0) {
  console.error("Tauri build failed. See logs/build-desktop.log");
  process.exit(result.status ?? 1);
}
mkdirSync("release/MacBot-Portable/MacBot", { recursive: true });
copyFileSync(
  "src-tauri/target/release/macbot.exe",
  "release/MacBot-Portable/MacBot/MacBot.exe",
);
const launcher = spawnSync("backend/.venv/Scripts/python.exe", ["scripts/build-launcher.py"], {
  env, stdio: "inherit", windowsHide: true,
});
if (launcher.status !== 0) process.exit(launcher.status ?? 1);
const notices = spawnSync("backend/.venv/Scripts/python.exe", ["scripts/write-notices.py"], {
  env, stdio: "inherit", windowsHide: true,
});
if (notices.status !== 0) process.exit(notices.status ?? 1);
console.log(`MacBot portable: ${resolve("release/MacBot-Portable/MacBot.exe")}`);
