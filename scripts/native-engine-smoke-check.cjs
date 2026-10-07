async (page) => {
  const composer = page.getByRole('textbox', {name:'Message MacBot'});
  await composer.fill('Calculate 17 times 23. Reply only with the number.');
  const started = Date.now();
  await page.getByRole('button', {name:'Send message', exact:true}).click();
  await page.getByRole('button', {name:'Stop response', exact:true}).waitFor();
  await page.getByRole('button', {name:'Send message', exact:true}).waitFor({timeout:180000});
  const reply = await page.locator('.message.assistant').last().innerText();
  if (!reply.includes('391')) throw Error('The prepared runtime did not complete local inference');
  return {chatSeconds:(Date.now()-started)/1000, reply};
}
