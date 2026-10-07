async (page) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const composer = page.getByRole('textbox', { name: 'Message MacBot' });
  await composer.waitFor({timeout:90000});
  await page.getByText('Your space stays local', {exact:false}).waitFor({timeout:90000});
  if (!await page.evaluate(() => Boolean(window.__TAURI_INTERNALS__))) throw Error('Expected a native Tauri window');
  const request = async (path, body, method = body ? 'POST' : 'GET') => page.evaluate(async ({path,body,method}) => {
    const connection = await window.__TAURI_INTERNALS__.invoke('connection');
    const response = await fetch(connection.baseUrl + path, {method,headers:{Authorization:`Bearer ${connection.token}`,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
    const text = await response.text();
    if(!response.ok) throw Error(`${path}: ${response.status}: ${text}`);
    return JSON.parse(text);
  },{path,body,method});
  const ready = await request('/capabilities');
  if(ready.embedding_dimensions !== 768 || !ready.portable) throw Error('Portable multimodal runtime is not ready');
  await composer.fill('A draft that must stay here.');
  await page.getByRole('button',{name:/Settings/}).first().click();
  await page.getByRole('dialog').waitFor();
  if(await page.locator('#model').inputValue() !== 'qwen3.5:4b') throw Error('Unexpected main model');
  await page.getByRole('heading',{name:'Connected tools'}).waitFor();
  await page.getByRole('heading',{name:'Your creative voice'}).waitFor();
  await page.keyboard.press('Escape');
  if(await composer.inputValue() !== 'A draft that must stay here.') throw Error('Settings lost the draft');
  await composer.fill('Calculate 17 times 23. Reply only with the number.');
  let started = Date.now();
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.getByRole('button',{name:'Stop response',exact:true}).waitFor();
  await page.getByRole('button',{name:'Send message',exact:true}).waitFor({timeout:180000});
  const chatSeconds = (Date.now()-started)/1000;
  if(!await page.locator('.message.assistant').last().innerText().then(t=>t.includes('391'))) throw Error('Arithmetic smoke failed');
  await page.screenshot({path:'logs/quality/native/product-desktop.png'});
  const layouts=[];
  for(const width of [760,1000,1380]) {
    await page.setViewportSize({width,height:900});
    layouts.push(await page.evaluate(() => ({width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,focusVisible:!!document.querySelector('textarea')})));
    if(layouts.at(-1).overflow) throw Error(`Overflow at ${width}px`);
  }
  await page.setViewportSize({width:1380,height:900});
  const servers=await request('/mcp/servers');
  const workspace=servers.find(s=>s.id==='workspace');
  await request('/mcp/servers/workspace',{...workspace,enabled:true},'PUT');
  const inventory=await request('/mcp/servers/workspace/inspect',{});
  if(!inventory.tools.some(t=>t.name==='write_file'))throw Error('Built-in MCP tool discovery failed');
  const persona=await request('/persona');
  const performance=[];
  for(let i=0;i<20;i++) {const t=Date.now();await request('/health');performance.push(Date.now()-t);}
  const sorted=performance.toSorted((a,b)=>a-b);
  if(errors.length) throw Error(errors.join('\n'));
  return {native:true,chatSeconds,models:ready.models,layouts,tools:inventory.tools.map(t=>t.name),personaChecksPassed:persona.evaluation?.passed ?? null,healthP50Ms:sorted[10],healthP95Ms:sorted[18],errors};
}
