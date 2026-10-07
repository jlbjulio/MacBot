async (page) => {
  const errors=[]; page.on('pageerror',error=>errors.push(error.message));
  const composer=page.getByRole('textbox',{name:'Message MacBot'});
  await composer.waitFor({timeout:90000});
  const request=async(path,body,method=body?'POST':'GET')=>page.evaluate(async({path,body,method})=>{
    const c=await window.__TAURI_INTERNALS__.invoke('connection');
    const response=await fetch(c.baseUrl+path,{method,headers:{Authorization:`Bearer ${c.token}`,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
    const result=await response.json();if(!response.ok)throw Error(`${path}: ${response.status}: ${JSON.stringify(result)}`);return result;
  },{path,body,method});
  const results=[];
  for(const [mode,prompt] of [
    ['chat','Create a polished two-section project proposal as both Word and PDF, with an elegant purple and cream theme, title page, objectives, and a small timeline table. Project: a community music workshop.'],
    ['spreadsheet','Create a budget spreadsheet in a purple and cream theme with Item and Cost columns: Coffee 5, Books 12, Tickets 20. Use currency formatting and a bar chart.'],
    ['presentation','Create four polished slides about a community music workshop: a cover, a comparison of beginner and advanced tracks, a three-step timeline, and a metric slide highlighting 30 participants. Purple and cream theme, concise text and speaker notes.'],
    ['image','Create a watercolor illustration of an orange cat beside a vinyl record. Export exactly 256x384 in WebP format with a grayscale filter.'],
  ]){
    if(process.argv.includes('--files-only') && mode==='image') continue;
    if(process.argv.includes('--slides-only') && mode!=='presentation') continue;
    if(process.argv.includes('--layout-only')) continue;
    await page.getByRole('combobox',{name:'MacBot task'}).selectOption(mode);
    await composer.fill(prompt);const started=Date.now();
    await page.getByRole('button',{name:'Send message',exact:true}).click();
    await page.getByRole('button',{name:'Stop response',exact:true}).waitFor();
    await page.getByRole('button',{name:'Send message',exact:true}).waitFor({timeout:360000});
    if(await page.locator('.error-notice').count())throw Error(await page.locator('.error-notice').allTextContents());
    const chats=await request('/chats');const chat=await request(`/chats/${chats[0].id}`);
    const message=chat.messages.at(-1);
    if(!message.artifacts.length)throw Error(`No artifact produced for ${mode}`);
    results.push({mode,seconds:(Date.now()-started)/1000,content:message.content,artifacts:message.artifacts});
  }
  await page.screenshot({path:'logs/quality/native/designed-creations.png'});
  await page.getByRole('button',{name:/Settings/}).first().click();
  await page.getByText('Your data & privacy',{exact:true}).scrollIntoViewIfNeeded();
  await page.getByText('Erase your personal workspace',{exact:true}).waitFor();
  await page.screenshot({path:'logs/quality/native/privacy-settings.png'});
  const layouts=[];
  for(const width of [760,1000,1380]){
    await page.setViewportSize({width,height:900});
    const result=await page.evaluate(()=>({width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,
      dialogOverflow:document.querySelector('dialog').scrollWidth>document.querySelector('dialog').clientWidth+1}));
    if(result.overflow || result.dialogOverflow)throw Error(`Settings overflow at ${width}px`);
    layouts.push(result);
  }
  await page.getByLabel('Local model',{exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:'logs/quality/native/model-settings-final.png'});
  await page.keyboard.press('Escape');
  return {results,layouts,errors};
}
