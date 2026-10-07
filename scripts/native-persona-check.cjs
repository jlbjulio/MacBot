async (page) => {
  const request=async(path,body,method=body?'POST':'GET')=>page.evaluate(async({path,body,method})=>{
    const connection=await window.__TAURI_INTERNALS__.invoke('connection');
    const response=await fetch(connection.baseUrl+path,{method,headers:{Authorization:`Bearer ${connection.token}`,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
    if(!response.ok)throw Error(`${path}: ${response.status}: ${await response.text()}`);return response.json();
  },{path,body,method});
  await request('/persona',{enabled:true},'PUT');
  await page.getByRole('button',{name:/New conversation/}).click();
  await page.getByRole('combobox',{name:'MacBot task'}).selectOption('chat');
  await page.getByRole('textbox',{name:'Message MacBot'}).fill('Give me one short, friendly line about getting started with a sketch.');
  const started=Date.now();
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.getByRole('button',{name:'Stop response',exact:true}).waitFor();
  await page.getByRole('button',{name:'Send message',exact:true}).waitFor({timeout:180000});
  if(await page.locator('.error-notice').count())throw Error(await page.locator('.error-notice').allTextContents());
  const answer=await page.locator('.message.assistant').last().innerText();
  const state=await request('/persona');
  if(!state.enabled||!state.evaluation.passed)throw Error('Persona adapter was not enabled');
  const bytes=await page.evaluate(async()=>{
    const connection=await window.__TAURI_INTERNALS__.invoke('connection');
    const response=await fetch(connection.baseUrl+'/persona/export',{headers:{Authorization:`Bearer ${connection.token}`}});
    if(!response.ok)throw Error('Adapter export failed');return (await response.arrayBuffer()).byteLength;
  });
  if(bytes<100000)throw Error('Adapter export is incomplete');
  await request('/persona',{enabled:false},'PUT');
  return {answer,seconds:(Date.now()-started)/1000,exportBytes:bytes,defaultRestored:true};
}
