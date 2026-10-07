async (page) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.waitForFunction(async () => (await window.__TAURI_INTERNALS__.invoke('bootstrap_status')).status === 'ready', undefined, {timeout:150000,polling:1000});
  const value = await page.evaluate(async () => {
    const c = await window.__TAURI_INTERNALS__.invoke('connection');
    return await (await fetch(c.baseUrl+'/setup', {headers:{Authorization:`Bearer ${c.token}`}})).json();
  });
  if(value.status !== 'ready' || value.models.length !== 6 || !value.models.every(model => model.ready))
    throw Error('The actual model-copy QA profile is not fully prepared');
  let phase = 'waiting';
  const actions = [];
  const pattern = '**/api/setup{,/pause}';
  await page.route(pattern, async route => {
    if (route.request().method() !== 'GET') {
      actions.push(route.request().url().endsWith('/pause') ? 'pause' : 'resume');
      return route.continue();
    }
    const complete = phase === 'ready';
    return route.fulfill({headers:{'Access-Control-Allow-Origin':new URL(page.url()).origin,'Cache-Control':'no-store'}, json:{...value, status:phase === 'inconsistent' ? 'ready' : phase, model:'Image creation', completed:1024*1024, total:8*1024*1024,
      error:phase==='failed' ? 'Test connection interruption. Retry to continue preparation.' : '',
      failures:phase==='failed' ? {image:'Test interruption'} : {}, capabilities:{...value.capabilities, image:complete},
      models:value.models.map(model => model.id==='image' ? {...model,ready:complete} : model)}});
  });
  const composer = page.getByRole('textbox',{name:'Message MacBot'});
  async function blocked() { if(await composer.count()) throw Error('Chat opened before all essential models were ready'); }
  async function waitState(text) {
    await page.getByText(text,{exact:true}).first().waitFor({timeout:25000});
    await blocked();
  }
  try {
    await page.reload();
    await waitState('Checking your local models');
    await page.getByRole('status').filter({hasText:'5 / 6 models ready'}).waitFor();
    await page.screenshot({path:'logs/quality/native/full-preparation-waiting.png'});
    phase='failed';
    await page.getByRole('button',{name:'Try again',exact:true}).waitFor({timeout:25000});
    await page.getByRole('alert').filter({hasText:'Test connection interruption'}).waitFor();
    await blocked();
    await page.getByRole('button',{name:'Try again',exact:true}).click();
    phase='downloading';
    await page.getByRole('button',{name:'Pause download',exact:true}).waitFor({timeout:25000});
    await blocked();
    await page.getByRole('button',{name:'Pause download',exact:true}).click();
    phase='paused';
    await waitState('Model downloads paused');
    const viewports = [];
    for(const width of [760,1000,1380]) {
      await page.setViewportSize({width,height:900});
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
      if(overflow) throw Error('Preparation overflow at '+width);
      viewports.push({width,overflow});
    }
    await page.screenshot({path:'logs/quality/native/full-preparation-paused.png'});
    await page.getByRole('button',{name:'Resume download',exact:true}).focus();
    await page.keyboard.press('Enter');
    phase='inconsistent';
    await waitState('Checking your local models');
    phase='ready';
    await composer.waitFor({timeout:25000});
    await page.waitForFunction(()=>!document.querySelector('option[value="image"]')?.disabled, undefined, {timeout:15000});
    await page.screenshot({path:'logs/quality/native/full-preparation-ready.png'});
    if(!actions.includes('pause') || !actions.includes('resume')) throw Error('Preparation controls did not invoke their endpoints');
    if(errors.length) throw Error(errors.join('\n'));
    return {native:true,states:['waiting','failed','retry','downloading','paused','inconsistent-ready','all-ready'],allModelsRequired:true,actions,viewports,errors,actualPreparation:{status:value.status,models:value.models,reused_bytes:value.reused_bytes},scope:'Controlled API fixtures verify the native UI gate; actual prepared files and prior real cache/download evidence verify the engines.'};
  } finally { await page.unroute(pattern); }
}
