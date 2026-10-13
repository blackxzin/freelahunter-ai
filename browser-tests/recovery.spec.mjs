import { test, expect } from '@playwright/test';
import { navigateWithRetry, browserDisconnected } from '../scripts/recovery.mjs';

test('recupera conexão temporária com espera crescente', async () => {
  const waits = []; let attempts = 0;
  const page = { goto: async () => { if (++attempts < 3) throw new Error('net::ERR_CONNECTION_RESET'); return { status: () => 200 }; } };
  await navigateWithRetry(page, 'https://example.com', {}, {sleep: async ms => waits.push(ms), log: () => {}});
  expect(attempts).toBe(3);expect(waits).toEqual([5000,10000]);
});

test('limita retries de 503 e não repete erros permanentes', async () => {
  let attempts=0;
  await expect(navigateWithRetry({goto:async()=>{attempts++;return {status:()=>503};}}, 'https://example.com', {}, {sleep:async()=>{},log:()=>{}})).rejects.toThrow('503');
  expect(attempts).toBe(3);
  attempts=0;
  await expect(navigateWithRetry({goto:async()=>{attempts++;throw new Error('Invalid URL');}}, 'bad')).rejects.toThrow('Invalid URL');
  expect(attempts).toBe(1);
  expect(browserDisconnected(new Error('Target page, context or browser has been closed'))).toBe(true);
});
