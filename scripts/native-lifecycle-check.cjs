async (page) => {
  const result = await page.evaluate(async () => {
    const connection = await window.__TAURI_INTERNALS__.invoke("connection");
    const headers = { Authorization: `Bearer ${connection.token}` };
    const health = await fetch(connection.baseUrl + "/health", { headers });
    const models = await fetch(connection.baseUrl + "/models", { headers });
    const preparation = await (await fetch(connection.baseUrl + "/setup", { headers })).json();
    const timings = [];
    for (let i = 0; i < 20; i++) {
      const start = performance.now();
      const response = await fetch(connection.baseUrl + "/health", { headers });
      if (response.status !== 200) throw Error("Health sample failed");
      await response.json();
      timings.push(performance.now() - start);
    }
    timings.sort((a, b) => a - b);
    return {
      authenticatedHealth: health.status,
      historyCacheControl: health.headers.get("cache-control"),
      modelsStatus: models.status,
      models: await models.json(),
      preparation: {status:preparation.status, capabilities:preparation.capabilities},
      idleHealth: {samples:20, p50_ms:Math.round(timings[9]), p95_ms:Math.round(timings[18]), scope:"Sequential idle health requests on this shared Windows PC"},
      overflow: document.documentElement.scrollWidth > window.innerWidth,
    };
  });
  if (result.authenticatedHealth !== 200 || result.modelsStatus !== 200)
    throw new Error("The packaged application is not ready.");
  if (result.historyCacheControl !== "no-store")
    throw new Error("Private API responses must not enter the browser cache.");
  if (!JSON.stringify(result.models).includes("macbot-4b"))
    throw new Error("The installed local model is missing.");
  if (result.overflow) throw new Error("The native window has horizontal overflow.");
  if (result.preparation.status !== "ready" || !Object.values(result.preparation.capabilities).every(Boolean))
    throw new Error("Required local capabilities are not prepared.");
  return result;
}
