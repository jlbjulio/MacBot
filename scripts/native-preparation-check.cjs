async (page) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    window.isTauri = true;
    window.setupFixture = { stage: "engines", status: "downloading", actions: [] };
    window.__TAURI_INTERNALS__ = {
      invoke: async command => {
        const fixture = window.setupFixture;
        if (command === "connection") return { baseUrl: "/api" };
        if (command === "bootstrap_status") return { status: fixture.stage === "engines" ? fixture.status : "ready", label: "Installing Python, Node and Ollama", completed: 64 * 1024 ** 2, total: 256 * 1024 ** 2, error: "" };
        if (command === "bootstrap_pause" || command === "bootstrap_resume") {
          fixture.actions.push(command);
          fixture.status = command === "bootstrap_pause" ? "paused" : "downloading";
        }
      }
    };
  });
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const fixture = await page.evaluate(() => window.setupFixture);
    if (path === "/api/setup/pause" || path === "/api/setup" && route.request().method() === "POST") {
      await page.evaluate(path => {
        window.setupFixture.actions.push(path.endsWith("/pause") ? "pause" : "resume");
        window.setupFixture.status = path.endsWith("/pause") ? "paused" : "downloading";
      }, path);
      return route.fulfill({ json: { started: true } });
    }
    if (path === "/api/setup") {
      const ready = fixture.status === "ready";
      return route.fulfill({ json: {
        status: fixture.status === "inconsistent" ? "ready" : fixture.status,
        model: "EmbeddingGemma 2", completed: 128 * 1024 ** 2, total: 1024 ** 3,
        error: fixture.status === "failed" ? "Fixture network interruption: Python model weights failed to download." : "",
        reused_bytes: 1024 ** 3, failures: fixture.status === "failed" ? { image: "Failed" } : {},
        models: ["chat", "voice", "whisper", "embedding", "verifier", "image"].map((id, index) => ({ id, name: `Technical model ${id}`, ready: ready || index < 3 })),
        capabilities: Object.fromEntries(["chat", "research", "image", "audio", "spreadsheet", "presentation", "document", "tools", "attachments", "dictation", "read_aloud"].map(name => [name, ready]))
      } });
    }
    const data = path === "/api/settings" ? { model: "qwen3.5:4b", whisper_model: "base", language: "en" }
      : path === "/api/models" ? { available: true, models: [{ name: "qwen3.5:4b", size: 1024 ** 3 }] }
      : path === "/api/health" ? { status: "ok" } : [];
    return route.fulfill({ json: data });
  });
  await page.goto(process.env.MACBOT_QA_URL || "http://127.0.0.1:5173", { waitUntil: "domcontentloaded", timeout: 60000 });
  const button = name => page.getByRole("button", { name, exact: true });
  const blocked = async () => {
    if (await page.getByRole("textbox", { name: "Message MacBot" }).count()) throw Error("Chat opened before preparation completed");
    const copy = await page.locator(".setup-panel").innerText();
    if (/Gemma|Ollama|Python|Node|Hugging Face|technical model|six essential|libraries/i.test(copy)) throw Error("Technical copy appears in the main preparation screen");
  };
  await page.getByRole("heading", { name: "Getting ready", exact: true }).waitFor();
  await blocked();
  await button("Pause download").click();
  await page.getByRole("heading", { name: "Paused", exact: true }).waitFor();
  await button("Resume download").focus();
  await page.keyboard.press("Enter");
  await button("Pause download").waitFor();
  const viewports = [];
  for (const width of [390, 760, 1380]) {
    await page.setViewportSize({ width, height: 900 });
    const metrics = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth > innerWidth, height: document.querySelector(".setup-panel").getBoundingClientRect().height }));
    if (metrics.overflow) throw Error("Preparation overflows at " + width);
    viewports.push({ width, ...metrics });
    await page.screenshot({ path: `logs/quality/native/minimal-preparation-${width}.png` });
  }
  await page.setViewportSize({ width: 390, height: 900 });
  await page.evaluate(() => document.documentElement.style.zoom = "2");
  await blocked();
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw Error("Preparation overflows at 200% zoom");
  await page.screenshot({ path: "logs/quality/native/minimal-preparation-zoom.png" });
  await page.evaluate(() => document.documentElement.style.zoom = "");
  await page.evaluate(() => { window.setupFixture.stage = "models"; });
  await page.getByText("3 of 6 complete", { exact: true }).waitFor();
  await blocked();
  await button("Pause download").click();
  await button("Resume download").waitFor();
  await button("Resume download").click();
  await button("Pause download").waitFor();
  await page.evaluate(() => { window.setupFixture.status = "failed"; });
  await button("Try again").waitFor();
  await blocked();
  await page.getByRole("alert").waitFor();
  await page.locator("summary").click();
  await page.getByText("Fixture network interruption: Python model weights failed to download.", { exact: true }).waitFor();
  await page.screenshot({ path: "logs/quality/native/minimal-preparation-error.png" });
  await button("Try again").click();
  await button("Pause download").waitFor();
  await page.evaluate(() => { window.setupFixture.status = "inconsistent"; });
  await page.getByText("Checking files", { exact: true }).waitFor();
  await blocked();
  await page.emulateMedia({ reducedMotion: "reduce" });
  const motion = await page.evaluate(() => getComputedStyle(document.querySelector(".setup-record")).animationName);
  if (motion !== "none") throw Error("Reduced motion was not honored");
  await page.evaluate(() => { window.setupFixture.status = "ready"; });
  await page.getByRole("textbox", { name: "Message MacBot" }).waitFor({ timeout: 25000 });
  const actions = await page.evaluate(() => window.setupFixture.actions);
  if (!["bootstrap_pause", "bootstrap_resume", "pause", "resume"].every(value => actions.includes(value))) throw Error("A preparation control did not reach its handler");
  if (errors.length) throw Error(errors.join("\n"));
  return { scope: "Rendered preparation UI with controlled engine and API fixtures; no real downloads", states: ["engines", "models", "paused", "failed", "retry", "inconsistent", "ready"], viewports, zoom: "200%", reducedMotion: true, keyboardResume: true, technicalCopyHidden: true, actions, errors };
}
