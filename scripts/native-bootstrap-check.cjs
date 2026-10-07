async (page) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const status = () => page.evaluate(() => window.__TAURI_INTERNALS__.invoke('bootstrap_status'));
  await page.getByRole('progressbar', { name: 'Current preparation progress' }).waitFor({ timeout: 30000 });
  let observed = await status();
  const initialDeadline = Date.now() + 60000;
  while (Date.now() < initialDeadline && !['downloading','verifying','installing','failed'].includes(observed.status)) {
    await page.waitForTimeout(100); observed = await status();
  }
  if (observed.status === 'failed') throw Error(observed.error);
  if (!['downloading','verifying','installing'].includes(observed.status)) throw Error('The cold-start engine stage was not observed');
  await page.getByRole('button', { name: 'Pause download' }).click();
  await page.getByRole('button', { name: 'Resume download' }).waitFor({ timeout: 60000 });
  const paused = await status();
  if (paused.status !== 'paused') throw Error('The engine download did not pause');
  if (await page.getByRole('textbox', { name: 'Message MacBot' }).count()) throw Error('Chat opened before the engine stage finished');
  await page.setViewportSize({ width: 760, height: 900 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  if (overflow) throw Error('The preparation screen overflows at 760 pixels');
  await page.screenshot({ path: 'logs/quality/native/bootstrap-paused.png' });
  await page.setViewportSize({ width: 1380, height: 900 });
  const started = Date.now();
  await page.getByRole('button', { name: 'Resume download' }).click();
  const deadline = Date.now() + 360000;
  let completed;
  while (Date.now() < deadline) {
    completed = await status();
    if (completed.status === 'failed') throw Error(completed.error);
    if (completed.status === 'ready') break;
    await page.waitForTimeout(500);
  }
  if (completed?.status !== 'ready') throw Error('The engine preparation did not complete within six minutes');
  await page.getByRole('textbox', { name: 'Message MacBot' }).waitFor({ timeout: 90000 });
  const health = await page.evaluate(async () => {
    const connection = await window.__TAURI_INTERNALS__.invoke('connection');
    const response = await fetch(connection.baseUrl + '/health', { headers: { Authorization: `Bearer ${connection.token}` } });
    return { status: response.status, cache: response.headers.get('cache-control') };
  });
  if (health.status !== 200 || health.cache !== 'no-store' || errors.length) throw Error('The prepared private backend is not healthy');
  await page.screenshot({ path: 'logs/quality/native/bootstrap-ready.png' });
  return { directUpstreamEngines:true, observed, paused, completed, resumeToReadySeconds: (Date.now() - started) / 1000, health, narrowOverflow: overflow, errors, modelCache: 'Existing verified model cache linked into isolated QA data; models were not redownloaded' };
}
