import { createRequire } from "node:module";
import {
  readdirSync,
  existsSync,
  readFileSync,
  mkdirSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";

const cache = join(process.env.LOCALAPPDATA, "npm-cache", "_npx");
const location = readdirSync(cache)
  .map((name) =>
    join(cache, name, "node_modules", "@playwright", "cli", "package.json"),
  )
  .find(
    (path) =>
      existsSync(path) &&
      JSON.parse(readFileSync(path, "utf8")).version === "0.1.21",
  );
if (!location)
  throw new Error(
    "Run npx --yes @playwright/cli@0.1.21 --version first.",
  );
const { chromium } = createRequire(location)("playwright");
const web = process.argv.includes("--web");
const browser = web ? await chromium.launch({ channel: "msedge", headless: true }) : await chromium.connectOverCDP("http://127.0.0.1:9223");
const page = web ? await browser.newPage() : browser
  .contexts()
  .flatMap((context) => context.pages())
  .find((page) => page.url().includes("tauri.localhost"));
if (!page) throw new Error("The native QA window was not found.");
page.setDefaultTimeout(15000);
const kind = ["research", "lifecycle", "product", "capabilities", "attachments", "designed", "bootstrap", "download-states", "engine-smoke", "preparation"].includes(process.argv[2])
  ? process.argv[2]
  : "product";
const fn = new Function(
  `return (${readFileSync(`scripts/native-${kind}-check.cjs`, "utf8")});`,
)();
mkdirSync("logs/quality/native", { recursive: true });
try {
  const report = await fn(page);
  writeFileSync(
    `logs/quality/native/${kind}-result.json`,
    JSON.stringify(report, null, 2),
  );
  console.log(
    `Native ${kind} completed; report: logs/quality/native/${kind}-result.json`,
  );
} catch (error) {
  writeFileSync(`logs/quality/native/${kind}-failure.txt`, String(error.stack));
  await page
    .screenshot({
      path: `logs/quality/native/${kind}-failure.png`,
      timeout: 5000,
    })
    .catch(() => {});
  throw error;
} finally {
  await browser.close();
}
