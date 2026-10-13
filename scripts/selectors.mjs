import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const platformCatalog = JSON.parse(readFileSync(`${root}/config/platforms.json`, 'utf8'));
const selectorFiles = {
  '99freelas': 'selectors.json',
  upwork: 'upwork_selectors.json',
  workana: 'workana_selectors.json',
};
const loadedSelectors = Object.fromEntries(Object.entries(selectorFiles).map(([name, file]) => [
  name, JSON.parse(readFileSync(`${root}/config/${file}`, 'utf8')),
]));

export function resolvePlatform(value = '') {
  const candidate = String(value || '').trim().toLowerCase();
  if (!candidate) return '99freelas';
  if (platformCatalog[candidate]) return candidate;
  try {
    const hostname = new URL(candidate).hostname;
    const found = Object.entries(platformCatalog).find(([, config]) => config.hostnames.includes(hostname))?.[0];
    if (found) return found;
  } catch {
    // Unknown values must not silently select another marketplace.
  }
  throw new Error(`Plataforma desconhecida: ${candidate}`);
}

export function normalizeJobUrl(value, platform) {
  const url = new URL(value, platform.listing_url);
  if (url.protocol !== 'https:' || !platform.hostnames.includes(url.hostname)
      || url.username || url.password || url.port
      || !new RegExp(platform.job_link_pattern, 'i').test(url.pathname)) {
    throw new Error(`URL de vaga inválida para ${platform.name}: ${value}`);
  }
  url.search = '';
  url.hash = '';
  url.hostname = new URL(platform.listing_url).hostname;
  url.pathname = url.pathname.replace(/\/$/, '');
  return url.href;
}

export function getPlatform(value = '') {
  const name = resolvePlatform(value);
  return { name, ...platformCatalog[name] };
}

export function getSelectors(value = '') {
  return loadedSelectors[resolvePlatform(value)] || loadedSelectors['99freelas'];
}

// Backward-compatible default used by local tests and the 99Freelas flow.
export const selectors = loadedSelectors['99freelas'];

export async function firstLocator(page, candidates, { visible = false } = {}) {
  for (const selector of candidates) {
    const locator = page.locator(selector).first();
    if (await locator.count() && (!visible || await locator.isVisible().catch(() => false))) return locator;
  }
  return null;
}

export async function hasAny(page, candidates) {
  for (const selector of candidates) {
    if (await page.locator(selector).first().count()) return selector;
  }
  return null;
}
