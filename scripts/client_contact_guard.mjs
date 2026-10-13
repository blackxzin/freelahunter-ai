// Shared identity matching for the 99Freelas inbox guard.

export function normalizeClientName(value) {
  return String(value || '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLocaleLowerCase('pt-BR');
}

export function normalizeClientUrl(value) {
  if (!value) return '';
  try {
    const parsed = new URL(String(value), 'https://www.99freelas.com.br');
    const host = parsed.hostname.toLowerCase().replace(/^www\./, '');
    const path = parsed.pathname
      .replace(/\/users?\//i, '/user/')
      .replace(/\/{2,}/g, '/')
      .replace(/\/$/, '');
    return `${host}${path}`;
  } catch {
    return '';
  }
}

export function sameClient(target, conversation) {
  const targetUrl = normalizeClientUrl(target?.client_url);
  const conversationUrl = normalizeClientUrl(conversation?.client_url);
  if (targetUrl && conversationUrl) return targetUrl === conversationUrl;

  const targetName = normalizeClientName(target?.client);
  const conversationName = normalizeClientName(conversation?.client);
  return Boolean(targetName && conversationName && targetName === conversationName);
}

export function findExistingClientContact(conversations, target) {
  const matches = (conversations || []).filter((conversation) => sameClient(target, conversation));
  return {
    found: matches.length > 0,
    matches,
  };
}
