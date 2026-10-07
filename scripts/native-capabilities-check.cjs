async (page) => {
  const errors=[]; page.on('pageerror',error=>errors.push(error.message));
  const request=async(path,body,method=body?'POST':'GET')=>page.evaluate(async({path,body,method})=>{
    const connection=await window.__TAURI_INTERNALS__.invoke('connection');
    const response=await fetch(connection.baseUrl+path,{method,headers:{Authorization:`Bearer ${connection.token}`,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
    const result=await response.json(); if(!response.ok) throw Error(`${path}: ${response.status}: ${JSON.stringify(result)}`); return result;
  },{path,body,method});
  const result=[];
  const priorChats=await request('/chats');
  const verifiedArtifacts=new Map();
  for(const chat of priorChats.slice(0,3)){
    const stored=await request(`/chats/${chat.id}`);
    for(const artifact of stored.messages.flatMap(message=>message.artifacts))verifiedArtifacts.set(artifact.kind,artifact);
  }
  const mode=page.getByRole('combobox',{name:'MacBot task'});
  const composer=page.getByRole('textbox',{name:'Message MacBot'});
  for(const [task,prompt,expected] of [
    ['spreadsheet','Create a budget spreadsheet with Item and Cost columns. Use Coffee 5 and Books 12.','MacBot.xlsx'],
    ['presentation','Create two slides for a weekend music workshop: introduction and practice.','MacBot.pptx'],
    ['audio','Record one short sentence welcoming the listener to MacBot.','MacBot.wav'],
    ['image','A small orange cat beside a vinyl record, warm simple illustration.','MacBot.png'],
  ]){
    if(verifiedArtifacts.has(task) && task!=='audio'){result.push({task,previouslyVerified:true,id:verifiedArtifacts.get(task).id});continue;}
    await mode.selectOption(task); await composer.fill(prompt);
    const started=Date.now();
    await page.getByRole('button',{name:'Send message',exact:true}).click();
    await page.getByRole('button',{name:'Stop response',exact:true}).waitFor();
    await page.getByRole('button',{name:'Send message',exact:true}).waitFor({timeout:300000});
    if(await page.locator('.error-notice').filter({hasText:/failed|could not|error|invalid/i}).count())throw Error(`Creation failed: ${task}: ${await page.locator('.error-notice').allTextContents()}`);
    await page.getByRole('button',{name:expected,exact:true}).last().waitFor();
    const chats=await request('/chats');
    const details=await request(`/chats/${chats[0].id}`);
    const artifact=details.messages.at(-1).artifacts.find(a=>a.kind===task);
    if(!artifact)throw Error(`Missing persisted artifact for ${task}`);
    const bytes=await page.evaluate(async({id})=>{
      const connection=await window.__TAURI_INTERNALS__.invoke('connection');
      const response=await fetch(`${connection.baseUrl}/artifacts/${id}`,{headers:{Authorization:`Bearer ${connection.token}`}});
      if(!response.ok)throw Error('Artifact download failed'); return (await response.arrayBuffer()).byteLength;
    },artifact);
    if(bytes<100)throw Error('Empty artifact');
    result.push({task,bytes,seconds:(Date.now()-started)/1000});
  }
  await page.screenshot({path:'logs/quality/native/creations-desktop.png'});
  await mode.selectOption('tools'); await composer.fill('Use write_file to create native-approved.txt containing exactly: MacBot native tool approval works.');
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.getByRole('region',{name:'Approve a tool call'}).waitFor({timeout:180000});
  const proposed=await page.locator('.approval-card pre').innerText();
  if(!proposed.includes('native-approved.txt'))throw Error('Unexpected tool proposal');
  await page.getByRole('button',{name:'Approve this call',exact:true}).click();
  await page.getByRole('button',{name:'Send message',exact:true}).waitFor({timeout:60000});
  await page.locator('.message.assistant').last().filter({hasText:'completed'}).waitFor();
  result.push({task:'approved MCP',proposal:JSON.parse(proposed)});
  await mode.selectOption('research');
  await composer.fill('According to Qdrant documentation, must the local storage path be a directory, and can two clients open the same directory at the same time?');
  const started=Date.now();
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.getByRole('button',{name:'Stop response',exact:true}).waitFor();
  await page.getByRole('button',{name:'Send message',exact:true}).waitFor({timeout:300000});
  const report=await page.locator('.message.assistant').last().innerText();
  if(await page.locator('.error-notice').count())throw Error(`Research failed: ${await page.locator('.error-notice').allTextContents()}`);
  result.push({task:'research',seconds:(Date.now()-started)/1000,report});
  await page.screenshot({path:'logs/quality/native/research-desktop.png'});
  return {result,errors};
}
