import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';

let server, base;
test.beforeAll(async ({}, info) => {
  const port = 18876 + info.workerIndex; base = `http://127.0.0.1:${port}`;
  server = spawn('python', ['browser-tests/panel_fixture.py', String(port)], {stdio:'pipe'});
  for (let attempt=0; attempt<60; attempt++) {
    try { if ((await fetch(base+'/api/status')).ok) return; } catch {}
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error('Painel de teste não iniciou.');
});
test.afterAll(() => server?.kill('SIGTERM'));

test('revisa proposta, registra resultado, filtra e preserva alterações após recarregar', async ({page,context}) => {
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await context.grantPermissions(['clipboard-read','clipboard-write'], {origin:base});
  await page.goto(base);
  await page.getByRole('button',{name:/API Python <script>/}).click();
  await expect(page.locator('#draft-message')).toHaveValue('Olá! Vamos desenvolver sua API.');
  await page.locator('#draft-message').fill('Olá! Proposta revisada para API e documentação.');
  await page.locator('#draft-outcome').selectOption('negotiating');
  await page.locator('#draft-notes').fill('Cliente pediu dividir em duas entregas.');
  await page.getByRole('button',{name:'Salvar revisão'}).click();
  await expect(page.locator('#review-feedback')).toContainText('Revisão salva');
  await expect(page.locator('#review-summary')).toContainText('1 em negociação');
  await page.getByRole('button',{name:'Copiar texto'}).click();
  await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe('Olá! Proposta revisada para API e documentação.');
  await page.reload();
  await page.getByRole('button',{name:/API Python <script>/}).click();
  await expect(page.locator('#draft-message')).toHaveValue('Olá! Proposta revisada para API e documentação.');
  await expect(page.locator('#draft-notes')).toHaveValue('Cliente pediu dividir em duas entregas.');
  await page.locator('#review-outcome').selectOption('won');
  await expect(page.locator('#review-list')).toContainText('Nenhuma oportunidade');
  await page.locator('#review-outcome').selectOption('negotiating');
  await expect(page.getByRole('button',{name:/API Python <script>/})).toBeVisible();
  for (const width of [1440,768,375]) {
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path:`test-results/panel-review-${width}.png`,fullPage:true});
  }
  expect(errors).toEqual([]);
});
