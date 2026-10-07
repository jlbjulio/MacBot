async (page) => {
  const errors=[]; page.on('pageerror',error=>errors.push(error.message));
  const request=async(path,body,method=body?'POST':'GET')=>page.evaluate(async({path,body,method})=>{
    const connection=await window.__TAURI_INTERNALS__.invoke('connection');
    const response=await fetch(connection.baseUrl+path,{method,headers:{Authorization:`Bearer ${connection.token}`,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
    if(!response.ok)throw Error(`${path}: ${response.status}: ${await response.text()}`);return response.json();
  },{path,body,method});
  const settings=await request('/settings');
  await request('/settings',{...settings,language:'en'},'PUT');
  await page.getByRole('button',{name:/New conversation/}).click();
  const composer=page.getByRole('textbox',{name:'Message MacBot'});
  const imports=[];
  for(const file of ['logs/media-smoke/voice.wav','logs/media-smoke/ocr.png','logs/media-smoke/sample.mp4']){
    const started=Date.now();
    await page.locator('input[type=file]').first().setInputFiles(file);
    await page.getByRole('button',{name:'Attach files',exact:true}).waitFor({state:'visible'});
    await page.waitForFunction(()=>!document.querySelector('button[aria-label="Attach files"]')?.disabled,null,{timeout:180000});
    if(await page.locator('.error-notice').count())throw Error(`Attachment failed: ${await page.locator('.error-notice').allTextContents()}`);
    imports.push({file,seconds:(Date.now()-started)/1000});
  }
  await composer.fill('What text appears in the image? What does the audio say? Describe the video colours briefly. Cite any retrieved text.');
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.getByRole('button',{name:'Stop response',exact:true}).waitFor();
  await page.getByRole('button',{name:'Send message',exact:true}).waitFor({timeout:240000});
  if(await page.locator('.error-notice').count())throw Error(await page.locator('.error-notice').allTextContents());
  const answer=await page.locator('.message.assistant').last().innerText();
  if(!answer.includes('42') || !answer.toLowerCase().includes('macbot'))throw Error('The image/audio response did not preserve fixture evidence');
  await page.getByRole('button',{name:'Read aloud',exact:true}).last().click();
  await page.locator('.read-aloud audio').last().waitFor({timeout:90000});
  const voice=await page.locator('.read-aloud audio').last().evaluate(async audio=>{
    if(audio.readyState<1)await new Promise((resolve,reject)=>{audio.addEventListener('loadedmetadata',resolve,{once:true});setTimeout(()=>reject(Error('Audio metadata did not load')),20000)});
    return {duration:audio.duration,readyState:audio.readyState};
  });
  if(!Number.isFinite(voice.duration)||voice.duration<=0)throw Error('Generated speech cannot be played');
  await page.locator('input[type=file]').nth(1).setInputFiles('logs/media-smoke/voice.wav');
  await page.waitForFunction(()=>!!document.querySelector('textarea[aria-label="Message MacBot"]')?.value.trim(),null,{timeout:90000});
  const dictation=await composer.inputValue();
  await page.screenshot({path:'logs/quality/native/attachments-desktop.png'});
  const chats=await request('/chats');
  const messages=(await request(`/chats/${chats[0].id}`)).messages;
  const transcript=messages.findLast(message=>message.role==='assistant');
  const exports=[];
  for(const format of ['md','txt','docx','pdf']){
    const size=await page.evaluate(async({id,format})=>{
      const connection=await window.__TAURI_INTERNALS__.invoke('connection');
      const response=await fetch(`${connection.baseUrl}/messages/${id}/export/${format}`,{method:'POST',headers:{Authorization:`Bearer ${connection.token}`}});
      if(!response.ok)throw Error(`Export ${format} failed`);return (await response.arrayBuffer()).byteLength;
    },{id:transcript.id,format});
    if(size<20)throw Error('Empty export');exports.push({format,bytes:size});
  }
  return {imports,answer,voice,dictation,exports,errors};
}
