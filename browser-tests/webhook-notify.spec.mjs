import { expect, test } from '@playwright/test';
import { sendWebhookEvent, validateWebhookUrl } from '../scripts/webhook_notify.mjs';
import { deliverAlert, replyEvent } from '../scripts/monitor_inbox.mjs';

const hook = 'https://n8n.example.com/webhook/freelahunter';
const conversation = { id: '42', timestamp: '2026-10-14T10:00:00', client: 'Ana', project: 'API',
  preview: 'Olá,   podemos   conversar?', url: 'https://www.99freelas.com.br/messages/inbox/42' };

test('webhook genérico exige HTTPS e não aceita credenciais na URL', () => {
  expect(validateWebhookUrl(hook)).toBe(hook);
  expect(() => validateWebhookUrl('http://n8n.example.com/webhook/x')).toThrow('HTTPS');
  expect(() => validateWebhookUrl('https://user:pass@n8n.example.com/webhook/x')).toThrow('credenciais');
});

test('webhook envia evento JSON com token e repete falha transitória', async () => {
  const calls = [];
  const status = await sendWebhookEvent(hook, 'client_reply', { id: 1 }, {
    token: 'secret-token',
    fetchImpl: async (url, options) => { calls.push({ url, options }); return calls.length < 2 ? { ok: false, status: 503 } : { ok: true, status: 200 }; },
    sleepImpl: async () => {},
  });
  expect(status).toBe(200);
  expect(calls).toHaveLength(2);
  const body = JSON.parse(calls[1].options.body);
  expect(body).toMatchObject({ event: 'client_reply', source: 'freelahunter', data: { id: 1 } });
  expect(calls[1].options.headers.authorization).toBe('Bearer secret-token');
});

test('webhook não repete erro definitivo', async () => {
  let calls = 0;
  await expect(sendWebhookEvent(hook, 'x', {}, {
    fetchImpl: async () => { calls += 1; return { ok: false, status: 400 }; }, sleepImpl: async () => {},
  })).rejects.toThrow('HTTP 400');
  expect(calls).toBe(1);
});

test('alerta de resposta chega pelo webhook mesmo com o Discord fora do ar', async () => {
  const sent = [];
  const logs = [];
  const delivered = await deliverAlert(conversation, {
    discordUrl: 'https://discord.com/api/webhooks/1/x', eventUrl: hook,
    discord: async () => { throw new Error('down'); },
    webhook: async (url, event, data) => { sent.push({ url, event, data }); return 200; },
    log: (event) => logs.push(event),
  });
  expect(delivered).toBe(true);
  expect(sent[0].event).toBe('client_reply');
  expect(sent[0].data.preview).toBe('Olá, podemos conversar?');
  expect(logs).toEqual(['discord_notification_error']);
  expect(replyEvent(conversation).conversation_id).toBe('42');
});

test('alerta sem nenhum canal disponível fica pendente para nova tentativa', async () => {
  const delivered = await deliverAlert(conversation, {
    discordUrl: 'https://discord.com/api/webhooks/1/x', eventUrl: undefined,
    discord: async () => { throw new Error('down'); }, log: () => {},
  });
  expect(delivered).toBe(false);
});
