import { firstLocator, selectors } from './selectors.mjs';

// Restrict analysis to the project description, excluding navigation and ads.
function parseAmount(value) {
  const raw = String(value || '').replace(/[^0-9,.-]/g, '');
  if (!raw) return 0;
  const lastComma = raw.lastIndexOf(',');
  const lastDot = raw.lastIndexOf('.');
  if (lastComma >= 0 && lastDot >= 0) {
    return Number(lastComma > lastDot ? raw.replace(/\./g, '').replace(',', '.') : raw.replace(/,/g, '')) || 0;
  }
  if (lastComma >= 0) {
    return Number(raw.length - lastComma - 1 === 3 ? raw.replace(',', '') : raw.replace(',', '.')) || 0;
  }
  if (lastDot >= 0) {
    return Number(raw.length - lastDot - 1 === 3 ? raw.replace('.', '') : raw) || 0;
  }
  return Number(raw) || 0;
}

export async function readSnapshot(page, selectorSet = selectors, platformName = '99freelas') {
  const titleNode = await firstLocator(page, selectorSet.job.title);
  let title;
  if (titleNode) {
    title = await titleNode.textContent();
  } else if (platformName === 'upwork') {
    const documentTitle = await page.title().catch(() => '');
    title = documentTitle.replace(/\s+(?:[-|]|—)\s+(?:Web Development|Upwork).*$/i, '').trim();
    if (!title || /^(?:just a moment|upwork login|search freelance jobs)/i.test(title)) {
      throw new Error('Título da vaga não localizado; revisar seletores antes de orçar.');
    }
  } else {
    throw new Error('Título da vaga não localizado; revisar seletores antes de orçar.');
  }
  const candidates = selectorSet.job.description;
  let description = '';
  for (const selector of candidates) {
    const node = page.locator(selector).first();
    if (await node.count() && await node.isVisible()) {
      description = (await node.innerText()).trim();
      if (description) break;
    }
  }
  if (!description) throw new Error('Descrição isolada da vaga não localizada; revisar seletores antes de orçar.');
  let isPremium = false;
  for (const selector of selectorSet.job.premium_badge) {
    const badge = page.locator(selector);
    for (const node of await badge.all()) {
      if (await node.isVisible()) isPremium = true;
    }
  }
  const budgetNode = await firstLocator(page, selectorSet.job.budget);
  const deadlineNode = await firstLocator(page, selectorSet.job.deadline);
  const clientNode = await firstLocator(page, selectorSet.job.client);
  const clientLink = await firstLocator(page, selectorSet.job.client_link);
  const budgetText = budgetNode ? await budgetNode.innerText().catch(() => '') : '';
  const deadlineText = deadlineNode ? await deadlineNode.innerText().catch(() => '') : '';
  const budgetRaw = budgetNode ? await budgetNode.getAttribute('data-budget-max') || await budgetNode.getAttribute('content') : null;
  const deadlineRaw = deadlineNode ? await deadlineNode.getAttribute('data-deadline-days') || await deadlineNode.getAttribute('content') : null;
  const budgetMatches = [...budgetText.matchAll(/(?:R\$|US\$|\$|€|£|EUR|BRL|USD|GBP)\s*[\d.,]+/gi)];
  const currency = /(?:€|EUR)/i.test(budgetText) ? 'EUR'
    : /(?:£|GBP)/i.test(budgetText) ? 'GBP'
      : /(?:R\$|BRL)/i.test(budgetText) ? 'BRL'
      : /(?:US\$|\$|USD)/i.test(budgetText) ? 'USD'
        : platformName === 'upwork' ? 'USD' : 'BRL';
  const range = budgetText.match(/(?:R\$|US\$|\$|€|£|EUR|BRL|USD|GBP)\s*[\d.,]+\s*(?:-|–|—|a|to|até)\s*([\d.,]+)/i);
  const hourly = /(?:\/\s*(?:h\b|hr\b|hora)|\b(?:hourly|per hour|por hora)\b)/i.test(budgetText);
  const budget = hourly ? 0 : parseAmount(budgetRaw) || parseAmount(range?.[1] || budgetMatches.at(-1)?.[0]);
  const duration = deadlineText.match(/(\d+)(?:\s*[-–]\s*(\d+))?\s*(dias?|days?|weeks?|semanas?)/i);
  const deadline = Number(deadlineRaw) || (duration
    ? Number(duration[2] || duration[1]) * (/week|semana/i.test(duration[3]) ? 7 : 1) : 0);
  const client = clientNode ? (await clientNode.textContent()).trim() : '';
  const clientUrl = clientLink ? await clientLink.getAttribute('href') : '';
  return { title: title.trim(), description, client, url: page.url(), platform: platformName, currency, budget_text: budgetText, is_premium: isPremium,
    client_url: clientUrl ? new URL(clientUrl, page.url()).href : '',
    budget_max: budget > 0 && Number.isFinite(budget) ? budget : null,
    deadline_days: deadline > 0 && Number.isFinite(deadline) ? deadline : null };
}
