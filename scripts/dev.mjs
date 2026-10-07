import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createRequire } from "node:module";
import { existsSync, mkdirSync, openSync } from "node:fs";
import { resolve } from "node:path";
import { homedir } from "node:os";

const production = process.argv.includes("--production");
const webOnly = process.argv.includes("--web-only");
const require = createRequire(import.meta.url);
const token = randomBytes(32).toString("hex");
const env = {
  ...process.env,
  MACBOT_TOKEN: token,
  MACBOT_API_URL: "http://127.0.0.1:8765/api",
  PATH: `${resolve(homedir(), ".cargo", "bin")};${process.env.PATH}`,
  CARGO_BUILD_JOBS: "1",
};
mkdirSync("logs", { recursive: true });
const children = [];
let stopping = false;
function launch(command, args, name) {
  const fd = openSync(`logs/${name}.log`, "a");
  const child = spawn(command, args, {
    env,
    stdio: ["ignore", fd, fd],
    windowsHide: true,
  });
  children.push(child);
  child.on("error", (error) => {
    console.error(`${name}: ${error.message}`);
    stop(1);
  });
  child.on("exit", (code) => {
    if (!stopping) {
      console.log(`${name} stopped (${code}). See logs/${name}.log`);
      stop(code ?? 1);
    }
  });
  return child;
}
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) {
    if (child.exitCode !== null) continue;
    if (process.platform === "win32")
      spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
        stdio: "ignore",
        windowsHide: true,
      });
    else child.kill("SIGTERM");
  }
  setTimeout(() => process.exit(code), 700);
}
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
async function waitFor(url, headers = {}) {
  for (let i = 0; i < 90; i++) {
    if (stopping) throw new Error("Startup interrupted");
    try {
      if ((await fetch(url, { headers, signal: AbortSignal.timeout(1000) })).ok)
        return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`${url} did not respond. See logs/.`);
}
try {
  if (
    !webOnly &&
    !production &&
    spawnSync("cargo", ["--version"], { env, windowsHide: true }).error
  ) {
    throw new Error(
      "Install Rust/Cargo to run Tauri, or use npm run dev:browser for the web interface.",
    );
  }
  if (production && !existsSync("src-tauri/target/release/macbot.exe")) {
    throw new Error(
      "The Tauri executable is missing. Run npm run build:desktop before npm start.",
    );
  }
  launch(
    "uv",
    [
      "run",
      "--directory",
      "backend",
      "uvicorn",
      "macbot.api:app",
      "--host",
      "127.0.0.1",
      "--port",
      "8765",
    ],
    "backend",
  );
  await waitFor("http://127.0.0.1:8765/api/health", {
    Authorization: `Bearer ${token}`,
  });
  if (!production) {
    launch(
      process.execPath,
      [resolve("node_modules/vite/bin/vite.js"), "--host", "127.0.0.1"],
      "frontend",
    );
    await waitFor("http://127.0.0.1:5173");
  }
  if (!webOnly) {
    if (production)
      launch(resolve("src-tauri/target/release/macbot.exe"), [], "tauri");
    else
      launch(
        process.execPath,
        [require.resolve("@tauri-apps/cli/tauri.js"), "dev"],
        "tauri",
      );
  }
  console.log(
    `MacBot is ready. ${webOnly ? "http://127.0.0.1:5173" : "Starting Tauri; see logs/tauri.log."} Logs: ${resolve("logs")}`,
  );
} catch (error) {
  console.error(error.message);
  stop(1);
}
