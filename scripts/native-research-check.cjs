async (page) => {
  await page.getByRole('textbox', {name:'Message MacBot'}).waitFor({timeout:70000});
  return await page.evaluate(async () => {
    const connection = await window.__TAURI_INTERNALS__.invoke('connection');
    const headers = {Authorization:`Bearer ${connection.token}`, 'Content-Type':'application/json'};
    const chat = await (await fetch(connection.baseUrl+'/chats', {method:'POST', headers})).json();
    const started = performance.now();
    const runResponse = await fetch(connection.baseUrl+'/runs', {method:'POST', headers, body:JSON.stringify({chat_id:chat.id, prompt:"According to official Qdrant Python client documentation, can the Python client run in local mode without running a Qdrant server? Give one short factual statement and its exact supporting quote.", mode:'research', model:'qwen3.5:4b', uploads:[]})});
    if (!runResponse.ok) throw new Error(await runResponse.text());
    const run = await runResponse.json();
    const eventsText = await (await fetch(connection.baseUrl+`/runs/${run.id}/events`, {headers})).text();
    const events = eventsText.split('\n').filter(line=>line.startsWith('data: ')).map(line=>JSON.parse(line.slice(6)));
    const history = await (await fetch(connection.baseUrl+`/chats/${chat.id}`, {headers})).json();
    const errors = events.filter(e=>e.type==='error');
    if(errors.length)throw Error(JSON.stringify(errors));
    return {seconds:(performance.now()-started)/1000, errors, history};
  });
}
