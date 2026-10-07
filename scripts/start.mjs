import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

const executable = resolve("release/MacBot-Portable/MacBot.exe");
if (!existsSync(executable)) {
  console.error("Build MacBot first with npm run build:desktop.");
  process.exit(1);
}
const env = { ...process.env };
delete env.MACBOT_TOKEN;
delete env.MACBOT_API_URL;
const app = spawn(executable, [], {
  env,
  detached: true,
  stdio: "ignore",
  windowsHide: false,
});
app.on("error", (error) => {
  console.error(error.message);
  process.exitCode = 1;
});
app.unref();
