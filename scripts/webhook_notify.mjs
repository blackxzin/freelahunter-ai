// Generic JSON webhook (e.g. an n8n Webhook node that forwards to WhatsApp or a CRM).
// Optional: everything keeps working when NOTIFY_WEBHOOK_URL is not set.

export function validateWebhookUrl(value) {
  let url;
  try { url = new URL(value); }
  catch { throw new Error('NOTIFY_WEBHOOK_URL inválida.'); }
  if (url.protocol !== 'https:' || !url.hostname || url.username || url.password || url.hash) {
    throw new Error('NOTIFY_WEBHOOK_URL deve ser HTTPS, sem credenciais na URL.');
  }
  return url.toString();
}

export async function sendWebhookEvent(webhookUrl, event, data = {}, {
  fetchImpl = fetch,
  sleepImpl = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  token = process.env.NOTIFY_WEBHOOK_TOKEN,
  timeoutMs = 10000,
} = {}) {
  const target = validateWebhookUrl(webhookUrl);
  if (!event || typeof event !== 'string') throw new Error('Evento do webhook inválido.');
  const body = JSON.stringify({ event, source: 'freelahunter', timestamp: new Date().toISOString(), data });
  if (body.length > 20000) throw new Error('Conteúdo do webhook muito grande.');
  const headers = { 'content-type': 'application/json' };
  if (token) headers.authorization = `Bearer ${token}`;
  const retryable = new Set([408, 429, 500, 502, 503, 504]);
  for (let attempt = 0; attempt < 3; attempt += 1) {
    let response;
    try {
      response = await fetchImpl(target, { method: 'POST', headers, body, signal: AbortSignal.timeout(timeoutMs) });
    } catch (error) {
      if (attempt === 2) throw new Error(`Webhook indisponível: ${error.message}`);
      await sleepImpl(500 * (2 ** attempt));
      continue;
    }
    if (response.ok) return response.status;
    if (!retryable.has(response.status) || attempt === 2) {
      throw new Error(`Webhook recusou o evento (HTTP ${response.status}).`);
    }
    await sleepImpl(500 * (2 ** attempt));
  }
  throw new Error('Webhook recusou o evento.');
}

// Fire-and-forget helper for the hunter: a notification failure never stops a hunt.
export async function notifyWebhook(event, data) {
  const url = process.env.NOTIFY_WEBHOOK_URL;
  if (!url) return false;
  try {
    await sendWebhookEvent(url, event, data);
    return true;
  } catch (error) {
    console.log(JSON.stringify({ event: 'webhook_notification_error', type: event, error: error.message }));
    return false;
  }
}
