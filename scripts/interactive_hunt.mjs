import { chromium } from '@playwright/test';
import { createInterface } from 'node:readline/promises';
import { stdin as input, stdout as output } from 'node:process';
import { spawnSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { readSnapshot } from './job_snapshot.mjs';
import { hasExactMessage, hasPriorProjectIntroduction } from './submission_guard.mjs';
import { findExistingClientContact } from './client_contact_guard.mjs';
import { firstLocator, getPlatform, getSelectors, normalizeJobUrl } from './selectors.mjs';
import { saveDraft } from './draft_store.mjs';
import { navigateWithRetry, browserDisconnected } from './recovery.mjs';
import { loadLocalEnv } from './local_env.mjs';
import { sendDiscordNotification } from './discord_notify.mjs';
import { isAccessChallenge, selectUpworkGoogleAccount, waitForManualAccess } from './access_guard.mjs';

const rl = createInterface({ input, output });
const ask = (question) => rl.question(question);
const root = fileURLToPath(new URL('..', import.meta.url));
loadLocalEnv(`${root}/.env`);
const profileDir = process.env.BROWSER_PROFILE_DIR || `${root}/browser-profile`;
mkdirSync(profileDir, { recursive: true });
const platform = getPlatform(process.env.PLATFORM || process.env.JOBS_URL);
const selectors = getSelectors(platform.name);
const listingUrl = process.env.JOBS_URL || platform.listing_url;
const maxJobs = Number(process.env.MAX_JOBS || (platform.name === 'upwork' ? 2 : 5));
const targetJobUrl = process.env.TARGET_JOB_URL ? normalizeJobUrl(process.env.TARGET_JOB_URL, platform) : null;
const automationMode = process.env.AUTOMATION_MODE || 'SEMI_AUTO';
const autoSend = process.env.AUTO_SEND === 'true' && automationMode === 'AUTO';
const dryRun = process.env.DRY_RUN !== 'false';
const killSwitch = process.env.AUTO_SEND_KILL_SWITCH !== 'false';
const minDelay = Math.max(0, Number(process.env.ACTION_DELAY_MIN_MS || 350));
const maxDelay = Math.max(minDelay, Number(process.env.ACTION_DELAY_MAX_MS || 1200));
const runForever = process.env.RUN_FOREVER === 'true';
const configuredPollSeconds = Number(process.env.HUNT_POLL_SECONDS || (Number(process.env.HUNT_INTERVAL_MINUTES || (platform.name === 'upwork' ? 12 : 15)) * 60));
const pollSeconds = Number.isFinite(configuredPollSeconds) ? Math.max(0, configuredPollSeconds) : 900;
const forceProposal = process.env.AUTO_FORCE_PROPOSAL === 'true';
const maxProposalsPerClient = Math.max(1, Number(process.env.MAX_PROPOSALS_PER_CLIENT_24H || 1));
const maxMessagesPerClient = Math.max(1, Number(process.env.MAX_MESSAGES_PER_CLIENT_24H || 1));
const autoMessageOnProposalLimit = process.env.AUTO_MESSAGE_ON_LIMIT !== 'false';
const maxIdleCycles = Math.max(0, Number(process.env.STOP_AFTER_IDLE_CYCLES || 0));
const premium = /premium|projeto exclusivo|bandeira dourada|selo dourado|assinar|assinatura|turbinar/i;

async function ensureAccess(page) {
  if (platform.name === 'workana' && /\/(?:pt\/|en\/|es\/)?(?:login|signup|sign-up|sign-in)(?:[/?#]|$)/i.test(page.url())) {
    await ask('Faça login na Workana no Chromium e pressione Enter para continuar: ');
  }
  if (platform.name === 'upwork') {
    const selected = await selectUpworkGoogleAccount(page, selectors.auth, process.env.UPWORK_GOOGLE_ACCOUNT_LABEL || 'Lucas');
    if (!selected && /accounts\.google\.com/i.test(page.url())) {
      await ask('A conta Lucas não foi identificada automaticamente. Selecione Lucas manualmente e pressione Enter: ');
    }
  }
  await waitForManualAccess(page, platform.name, ask);
}

function generate(snapshot) {
  const result = spawnSync('python', ['scripts/generate_proposal.py'], {
    cwd: root,
    input: JSON.stringify(snapshot),
    env: { ...process.env, HUNT_ANALYSIS_CACHE: 'true' },
    encoding: 'utf8',
  });
  if (result.status !== 0) throw new Error(result.stderr || 'falha ao gerar proposta');
  return JSON.parse(result.stdout);
}

function operations(command, payload) {
  const result = spawnSync('python', ['scripts/operations.py', command], { cwd: root, input: JSON.stringify(payload), encoding: 'utf8' });
  if (result.status !== 0) throw new Error(result.stderr || 'Falha ao acessar fila de revisão.');
  return JSON.parse(result.stdout);
}

function tracking(command, payload = {}) {
  const result = spawnSync('python', ['scripts/proposal_tracking.py', command], {
    cwd: root, input: JSON.stringify(payload), encoding: 'utf8',
  });
  if (result.status !== 0) throw new Error(result.stderr || `falha no tracking (${command})`);
  return result.stdout ? JSON.parse(result.stdout.trim().split('\n').pop()) : {};
}

function markSent(proposalId, externalId, conversationId) {
  const args = ['scripts/proposal_tracking.py', 'sent', '--proposal-id', String(proposalId), '--external-id', externalId || 'browser-confirmed'];
  if (conversationId) args.push('--conversation-id', conversationId);
  const result = spawnSync('python', args, { cwd: root, encoding: 'utf8' });
  if (result.status !== 0) throw new Error(result.stderr || 'falha ao registrar envio');
}

function conversationIdFromUrl(value) {
  const match = String(value || '').match(/\/messages\/(?:inbox\/)?([^/?#]+)/i);
  return match ? match[1] : undefined;
}

function money(value, currency = 'BRL') {
  const amount = Number(value).toLocaleString(currency === 'BRL' ? 'pt-BR' : 'en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency === 'USD' ? `$${amount}` : currency === 'EUR' ? `€${amount}` : currency === 'GBP' ? `£${amount}` : `R$ ${amount}`;
}

function forceProposalDraft(draft, snapshot) {
  if (!forceProposal || draft.action !== 'question' || !Array.isArray(draft.price_range) || !draft.price_range[1]) return draft;
  const scope = (draft.breakdown || []).slice(0, 5).map((item) => item.deliverable).join('; ') || 'desenvolvimento, testes e entrega documentada';
  const days = Array.isArray(draft.days_range) && draft.days_range[1]
    ? Math.ceil((draft.days_range[0] + draft.days_range[1]) / 2) : null;
  const price = Array.isArray(draft.price_range) && draft.price_range[1]
    ? Math.ceil(((draft.price_range[0] + draft.price_range[1]) / 2) / 50) * 50 : null;
  if (price == null) return draft;
  if (!days) return draft;
  const title = String(draft.subject || '').replace(/^Proposta:\s*/i, '');
  draft.action = 'proposal';
  draft.questions = [];
  draft.suggested_price = price;
  draft.estimated_days = days;
  const english = String(snapshot?.platform || '').toLowerCase() === 'upwork';
  draft.message = english
    ? `Hi! We are interested in the project "${title}". We understand the delivery includes ${scope}. `
      + 'We are a two-developer full-stack team working together, with cross-review across frontend and backend. '
      + 'We propose verifiable milestones covering implementation, testing, and documented handoff. '
      + `As an initial estimate, we propose ${money(draft.suggested_price, snapshot?.currency)} and up to ${days} calendar days. `
      + 'The price is negotiable after the final scope and priorities are confirmed. Paid APIs, hosting, domains, and services remain in the client account.'
    : `Olá! Temos interesse no projeto “${title}”. Entendemos que a entrega envolve ${scope}. `
      + 'Somos uma equipe de dois desenvolvedores full stack que trabalham juntos. '
      + 'Propomos executar em etapas verificáveis, com alinhamento, implementação, testes e entrega documentada. '
      + `Como referência inicial, propomos ${money(draft.suggested_price, snapshot?.currency)} e prazo de até ${days} dias corridos. `
      + 'O preço pode ser negociado conforme os detalhes finais do escopo e as prioridades do projeto. '
      + 'APIs, hospedagem, domínio e serviços pagos ficam nas contas do cliente.';
  draft.validation_status = 'PENDING';
  draft.validation_reasons = ['Estimativa automática preliminar; confirmar escopo com o cliente.'];
  return draft;
}

function clientHistory(clientKey) {
  if (!clientKey) return {};
  const result = spawnSync('python', ['scripts/proposal_tracking.py', 'history', '--client-key', clientKey], { cwd: root, encoding: 'utf8' });
  if (result.status !== 0) return {};
  return JSON.parse(result.stdout.trim().split('\n').pop());
}

function messageGate(clientKey) {
  const result = spawnSync('python', ['scripts/proposal_tracking.py', 'message-gate'], {
    cwd: root, input: JSON.stringify({ client_key: clientKey, max_per_day: maxMessagesPerClient }), encoding: 'utf8',
  });
  if (result.status !== 0) return { allowed: false, reason: result.stderr || 'falha na trava de mensagens' };
  return JSON.parse(result.stdout.trim().split('\n').pop());
}

function proposalGate() {
  const result = spawnSync('python', ['scripts/proposal_tracking.py', 'gate',
    '--max-per-hour', process.env.MAX_PROPOSALS_PER_HOUR || '3',
    '--max-per-day', process.env.MAX_PROPOSALS_PER_DAY || '10',
    '--no-response-days', process.env.PAUSE_NO_RESPONSE_DAYS || '7',
    '--consecutive-no-response', process.env.PAUSE_CONSECUTIVE_NO_RESPONSE || '5',
    '--rejection-threshold', process.env.PAUSE_REJECTION_THRESHOLD || '0.6'], {
    cwd: root, encoding: 'utf8',
  });
  const raw = result.stdout?.trim().split('\n').pop();
  try {
    return JSON.parse(raw || '{}');
  } catch {
    return { allowed: false, reason: result.stderr || 'falha na trava de propostas' };
  }
}

function clientContext(clientKey) {
  if (!clientKey) return [];
  const result = spawnSync('python', ['scripts/proposal_tracking.py', 'context', '--client-key', clientKey], { cwd: root, encoding: 'utf8' });
  if (result.status !== 0) return [];
  return JSON.parse(result.stdout.trim().split('\n').pop()).messages || [];
}

function claimSend(proposalId) {
  const result = spawnSync('python', ['scripts/proposal_tracking.py', 'claim', '--proposal-id', String(proposalId),
    '--max-per-client', String(maxProposalsPerClient)], {
    cwd: root, encoding: 'utf8',
  });
  if (result.status !== 0) return false;
  return JSON.parse(result.stdout.trim().split('\n').pop()).claimed === true;
}

async function prepareFallbackMessage(page, jobUrl, draft) {
  const message = draft.fallback_message || draft.question;
  if (!message) return null;
  await page.goto(jobUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  const questionLink = await firstLocator(page, selectors.proposal.question_link);
  if (questionLink) {
    const href = await questionLink.getAttribute('href');
    await page.goto(new URL(href, page.url()).href, { waitUntil: 'domcontentloaded' });
  }
  const messagePageUrl = page.url();
  const question = await visibleLocator(page, selectors.proposal.question);
  if (!question) return null;
  const existing = await alreadyContacted(page, messagePageUrl, {
    subject: draft.subject,
    question: message,
  });
  if (existing) return { skipped: 'O cliente já recebeu contato para esta vaga; mensagem fallback bloqueada.' };
  await question.fill(message);
  await humanPause();
  return { messagePageUrl, message };
}

async function notifyPause(reason) {
  if (!process.env.DISCORD_WEBHOOK_URL) return;
  await sendDiscordNotification(process.env.DISCORD_WEBHOOK_URL, `⏸️ FreelaHunter pausado: ${reason}`);
}

async function visibleLocator(page, selectors) {
  return firstLocator(page, selectors, { visible: true });
}

async function humanPause() {
  const delay = minDelay + Math.random() * (maxDelay - minDelay);
  await new Promise((resolve) => setTimeout(resolve, Math.round(delay)));
}

function selectorList(values = []) {
  return values.filter(Boolean).join(',');
}

async function readInboxConversations(page) {
  const conversations = [];
  const visited = new Set();
  for (let pageNumber = 0; pageNumber < 20; pageNumber += 1) {
    const currentUrl = page.url();
    if (visited.has(currentUrl)) break;
    visited.add(currentUrl);

    let rowSelector = null;
    for (const candidate of selectors.inbox?.row || []) {
      if (await page.locator(candidate).count()) {
        rowSelector = candidate;
        break;
      }
    }
    if (!rowSelector) {
      const body = await page.locator('body').innerText().catch(() => '');
      if (pageNumber === 0 && /nenhuma conversa|sem mensagens|caixa de entrada vazia|não há conversas/i.test(body)) {
        return { checked: true, conversations: [] };
      }
      return pageNumber === 0
        ? { checked: false, reason: 'linhas da caixa de mensagens não localizadas' }
        : { checked: true, conversations };
    }
    const fieldSelectors = {
      client: selectorList(selectors.inbox.client || []),
      project: selectorList(selectors.inbox.project || []),
      preview: selectorList(selectors.inbox.preview || []),
      clientLink: "a[href*='/users/'], a[href*='/user/']",
      conversationLink: "a[href*='/messages/'], a[href*='/message/']",
    };
    conversations.push(...await page.locator(rowSelector).evaluateAll((rows, fields) => rows.map((row) => {
      const text = (selector) => selector ? row.querySelector(selector)?.textContent?.trim() || '' : '';
      const link = (selector) => selector ? row.querySelector(selector)?.getAttribute('href') || '' : '';
      return {
        client: text(fields.client),
        project: text(fields.project),
        preview: text(fields.preview),
        client_url: link(fields.clientLink),
        url: link(fields.conversationLink),
      };
    }), fieldSelectors));

    const next = await firstLocator(page, [
      "a[rel='next']",
      "a[aria-label*='Próxima']",
      "a[aria-label*='Proxima']",
      ".pagination a.next",
      ".pagination a:has-text('Próxima')",
      ".pagination a:has-text('Próximo')",
    ], { visible: true });
    if (!next) break;
    const href = await next.getAttribute('href');
    if (!href) break;
    const nextUrl = new URL(href, page.url()).href;
    if (visited.has(nextUrl)) break;
    await page.goto(nextUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await humanPause();
  }
  return { checked: true, conversations };
}

async function inboxContactForClient(snapshot, jobPage) {
  if (platform.name !== '99freelas') return { checked: true, found: false };
  if (!snapshot?.client && !snapshot?.client_url) {
    return { checked: false, reason: 'cliente não identificado na vaga' };
  }
  const inboxPage = await jobPage.context().newPage();
  try {
    await inboxPage.goto(platform.inbox_url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await ensureAccess(inboxPage);
    await inboxPage.waitForTimeout(1200);
    const inbox = await readInboxConversations(inboxPage);
    if (!inbox.checked) return inbox;
    return { ...findExistingClientContact(inbox.conversations, snapshot), checked: true };
  } catch (error) {
    return { checked: false, reason: `falha ao consultar mensagens: ${error.message}` };
  } finally {
    await inboxPage.close().catch(() => {});
  }
}

async function conversationMessages(page, messagePageUrl) {
  await page.goto(messagePageUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  const conversationLink = await firstLocator(page, selectors.conversation.link);
  if (conversationLink) {
    const href = await conversationLink.getAttribute('href');
    await page.goto(new URL(href, page.url()).href, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await humanPause();
  }
  for (const selector of selectors.conversation.message) {
    const messages = page.locator(selector);
    if (await messages.count()) return messages.allTextContents();
  }
  if (/\/messages\//i.test(page.url())) return [await page.locator('body').innerText()];
  return null;
}

async function alreadyContacted(page, messagePageUrl, draft) {
  const verifier = await page.context().newPage();
  try {
    const messages = await conversationMessages(verifier, messagePageUrl);
    if (!messages) return false;
    const title = draft.subject.replace(/^Proposta:\s*/i, '');
    return hasExactMessage(messages, draft.question) || hasPriorProjectIntroduction(messages, title);
  } finally {
    await verifier.close();
  }
}

async function verifyQuestion(page, messagePageUrl, expected) {
  const messages = await conversationMessages(page, messagePageUrl);
  const wanted = String(expected || '').replace(/\s+/g, ' ').trim();
  if (messages?.some((message) => String(message).replace(/\s+/g, ' ').trim().includes(wanted))) return true;
  await page.goto(platform.inbox_url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await ensureAccess(page);
  const inboxText = (await page.locator('body').innerText()).replace(/\s+/g, ' ').trim();
  return wanted.length > 40 && inboxText.includes(wanted.slice(0, 80));
}

async function fillJob(page, link) {
  await navigateWithRetry(page, link, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await ensureAccess(page);
  if (platform.name === 'upwork' || platform.name === 'workana') {
    await page.waitForTimeout(Math.max(1000, Number(process.env.JOB_RENDER_WAIT_MS || 3000)));
    await ensureAccess(page);
  }
  let snapshot;
  try { snapshot = await readSnapshot(page, selectors, platform.name); }
  catch (error) { return { skipped: `${error.message} (${await page.title().catch(() => '')})` }; }
  snapshot.client_key = snapshot.client_url || snapshot.client;
  const inboxContact = await inboxContactForClient(snapshot, page);
  if (!inboxContact.checked) return { skipped: `Envio bloqueado: não foi possível verificar as mensagens (${inboxContact.reason}).` };
  if (inboxContact.found) {
    return { skipped: 'Envio bloqueado: já existe conversa com este cliente na caixa de mensagens.' };
  }
  snapshot.client_history = clientHistory(snapshot.client_key);
  snapshot.confirmed_context = clientContext(snapshot.client_key);
  const draft = forceProposalDraft(generate(snapshot), snapshot);
  console.log(JSON.stringify({ event: draft.cache_hit ? 'analysis_cached' : 'job_ranked', score: draft.selection?.score, reasons: draft.selection?.reasons, exclusions: draft.selection?.exclusions }));
  if (draft.outcome && draft.outcome !== 'draft') return { skipped: `Vaga já acompanhada no painel: ${draft.outcome}`, cached: draft.cache_hit };
  if (draft.action === 'skip') return { skipped: draft.reason, cached: draft.cache_hit };
  if (draft.reviewed) return { skipped: 'Revisão manual salva no painel; envio deve ser feito após revisão.', cached: draft.cache_hit };
  if (draft.cache_hit && (!platform.allow_submission || dryRun || killSwitch)) return { skipped: 'Vaga sem alterações; rascunho já disponível no painel.', cached: true };
  console.log(JSON.stringify({ tasks: draft.breakdown, price_range: draft.price_range,
    client_total_range: draft.client_total_range, days_range: draft.days_range,
    questions: draft.questions, commercial_reference: draft.knowledge }, null, 2));
  if (!platform.allow_submission || dryRun || killSwitch) {
    return { kind: draft.action === 'question' ? 'question' : 'proposal', draft, snapshot, jobUrl: link };
  }
  if (draft.action === 'question') {
    const questionLink = await firstLocator(page, selectors.proposal.question_link);
    if (questionLink) {
      const href = await questionLink.getAttribute('href');
      await page.goto(new URL(href, page.url()).href, { waitUntil: 'domcontentloaded' });
    }
    const messagePageUrl = page.url();
    if (await alreadyContacted(page, messagePageUrl, draft)) {
      return { skipped: 'Conversa inicial desta vaga já existe; novo envio bloqueado para evitar duplicata.' };
    }
    const question = await visibleLocator(page, selectors.proposal.question);
    if (!question) {
      console.log(draft.question);
      return { skipped: 'Escopo exige esclarecimento; formulário de pergunta não localizado.' };
    }
    await question.fill(draft.question);
    await humanPause();
    return { kind: 'question', draft, messagePageUrl, snapshot, jobUrl: link };
  }
  const priorConversationLink = await firstLocator(page, selectors.proposal.question_link);
  if (priorConversationLink) {
    const href = await priorConversationLink.getAttribute('href');
    const priorMessagesUrl = new URL(href, page.url()).href;
    if (await alreadyContacted(page, priorMessagesUrl, draft)) {
      return { skipped: 'Já existe contato deste bot com o cliente para esta vaga; novo envio bloqueado.' };
    }
  }
  const bidLink = await firstLocator(page, selectors.proposal.bid_link);
  if (!bidLink) {
    return { skipped: 'Candidatura indisponível ou proposta já existente; envio bloqueado para evitar duplicata.' };
  }
  const href = await bidLink.getAttribute('href');
  await page.goto(new URL(href, page.url()).href, { waitUntil: 'domcontentloaded' });
  const proposalBox = await visibleLocator(page, selectors.proposal.message);
  if (proposalBox) {
    const subject = await visibleLocator(page, selectors.proposal.subject);
    const price = await visibleLocator(page, selectors.proposal.price);
    const finalPrice = await visibleLocator(page, selectors.proposal.final_price);
    const duration = await visibleLocator(page, selectors.proposal.duration);
    if (subject) { await subject.fill(draft.subject); await humanPause(); }
    if (price && draft.suggested_price != null) { await price.fill(String(draft.suggested_price)); await humanPause(); }
    if (duration) { await duration.fill(String(draft.estimated_days)); await humanPause(); }
    if (finalPrice && draft.suggested_price != null) await finalPrice.blur();
    await proposalBox.fill(draft.message);
    await humanPause();
    return { kind: 'proposal', draft, snapshot, jobUrl: link };
  }
  const question = await visibleLocator(page, selectors.proposal.question);
  if (question) {
    await question.fill(draft.question);
    await humanPause();
    return { kind: 'question', draft, messagePageUrl: page.url(), snapshot, jobUrl: link };
  }
  return { skipped: 'form not found' };
}

async function main() {
  if (!Number.isInteger(maxJobs) || maxJobs < 1) throw new Error('MAX_JOBS deve ser um inteiro positivo.');
  const listing = new URL(listingUrl);
  if (listing.protocol !== 'https:' || !platform.hostnames.includes(listing.hostname)) {
    throw new Error('JOBS_URL deve pertencer à plataforma selecionada e usar HTTPS.');
  }
  if (autoSend && (dryRun || killSwitch)) {
    throw new Error('AUTO mode exige DRY_RUN=false e AUTO_SEND_KILL_SWITCH=false.');
  }
  console.log(JSON.stringify({ event: 'hunt_started', auto_send: autoSend, dry_run: dryRun,
    run_forever: runForever, force_proposal: forceProposal, poll_seconds: pollSeconds,
    max_jobs: maxJobs, max_proposals_per_client_24h: maxProposalsPerClient,
    stop_after_idle_cycles: maxIdleCycles }));
  let context;
  try {
    context = await chromium.launchPersistentContext(profileDir, {
      headless: false,
      ignoreDefaultArgs: ['--enable-automation'],
      args: ['--disable-blink-features=AutomationControlled'],
    });
    const page = context.pages()[0] || await context.newPage();
    let firstCycle = true;
    let idleCycles = 0;
    while (true) {
    try {
      await navigateWithRetry(page, listingUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    } catch (error) {
      if (browserDisconnected(error) || !runForever) throw error;
      console.error(`Listagem indisponível; nova tentativa em 60 segundos: ${error.message}`);
      await page.waitForTimeout(60000);
      continue;
    }
    await ensureAccess(page);
    if (firstCycle && process.env.AUTO_LOGIN_PROMPT !== 'false' && !autoSend) await ask('Faça login manualmente no Chromium. Quando terminar, pressione Enter aqui: ');
    const links = targetJobUrl ? [{ href: targetJobUrl, text: targetJobUrl }] : await (async () => {
      const collected = [];
      for (const selector of selectors.listing.links) {
        const rows = await page.locator(selector).evaluateAll((anchors) => anchors.map((anchor) => ({ href: anchor.href, text: anchor.innerText })));
        collected.push(...rows);
      }
      const seen = new Set();
      return collected.flatMap(({ href, text }) => {
        try {
          const canonical = normalizeJobUrl(href, platform);
          if (!text.trim() || seen.has(canonical)) return [];
          seen.add(canonical);
          return [{ href: canonical, text }];
        } catch { return []; }
      });
    })();
    if (!links.length && await isAccessChallenge(page)) {
      await ensureAccess(page);
      continue;
    }
    if (!links.length) {
      console.log(JSON.stringify({ event: 'no_job_links', platform: platform.name, url: page.url(), title: await page.title().catch(() => '') }));
    }
    let processed = 0;
    let inspected = 0;
    let analyzed = 0;
    let cycleSent = 0;
    for (const item of operations('order', { links })) {
    if (analyzed >= maxJobs || inspected >= maxJobs * 10) break;
    inspected += 1;
    if (premium.test(item.text)) continue;
    const jobPage = await context.newPage();
    try {
    await jobPage.bringToFront();
    await humanPause();
    const result = await fillJob(jobPage, item.href);
    if (!result.cached) analyzed += 1;
    if (result.skipped) { console.log(`IGNORADA: ${result.skipped} — ${item.href}`); continue; }
    if (result.draft.validation_status && result.draft.validation_status !== 'PASSED' && !forceProposal) {
      console.log(`IGNORADA: proposta não passou validação — ${(result.draft.validation_reasons || []).join('; ')}`);
      continue;
    }
    if (!result.snapshot.client_key) {
      console.log(`AVISO: identificador do cliente não localizado; proteção aplicada pela chave da vaga — ${item.href}`);
    }
    const draftPath = saveDraft(root, platform.name, result.snapshot, result.draft);
    console.log(`${result.kind.toUpperCase()} preparada: ${result.draft.subject || item.href}`);
    console.log(`Rascunho salvo: ${draftPath}`);
    const displayedPrice = result.draft.suggested_price ?? result.draft.fallback_price;
    const displayedMessage = result.kind === 'question'
      ? (displayedPrice != null ? result.draft.fallback_message : result.draft.question)
      : result.draft.message;
    console.log(`Valor sugerido: ${displayedPrice != null ? money(displayedPrice, result.snapshot.currency) : 'a combinar'} | prazo: ${result.draft.estimated_days ?? result.draft.fallback_days ?? 'a confirmar'} dias`);
    console.log(`Texto preparado:\n${displayedMessage}`);
    if (!platform.allow_submission || dryRun || killSwitch) {
      processed += 1;
      console.log(`RASCUNHO PREPARADO para ${platform.name}; envio desativado nesta execução.`);
      continue;
    }
    const clientGate = tracking('client-gate', {
      client_key: result.snapshot.client_key,
      max_per_day: maxProposalsPerClient,
    });
    if (!clientGate.allowed) {
      console.log(`IGNORADA: ${clientGate.reason}; nenhum segundo contato será enviado — ${item.href}`);
      continue;
    }
    let sendKind = result.kind === 'question' ? 'message' : 'proposal';
    let sendMessage = result.kind === 'question'
      ? (result.draft.fallback_price != null ? result.draft.fallback_message : result.draft.question)
      : result.draft.message;
    let sendPrice = result.kind === 'question' ? result.draft.fallback_price : result.draft.suggested_price;
    let sendPageUrl = result.messagePageUrl;
    let fallbackFromProposalLimit = false;
    if (sendKind === 'message' && result.kind === 'question' && sendMessage !== result.draft.question) {
      const question = await visibleLocator(jobPage, selectors.proposal.question);
      if (question) await question.fill(sendMessage);
      await humanPause();
    }
    if (sendKind === 'proposal') {
      const gate = proposalGate();
      if (!gate.allowed) {
        if (!autoMessageOnProposalLimit) {
          console.log(`IGNORADA: ${gate.reason || 'limite de propostas atingido'} — mensagem fallback desativada.`);
          continue;
        }
        if (!/limite (horário|diário)/i.test(gate.reason || '')) {
          console.log(`PAUSADA: ${gate.reason || 'trava de propostas'}`);
          try { await notifyPause(gate.reason || 'trava de propostas'); } catch (error) { console.log(`Falha ao avisar pausa: ${error.message}`); }
          continue;
        }
        const fallback = await prepareFallbackMessage(jobPage, result.jobUrl, result.draft);
        if (!fallback || fallback.skipped) {
          console.log(`IGNORADA: ${fallback?.skipped || 'formulário de mensagens não localizado'} — ${item.href}`);
          continue;
        }
        const fallbackGate = messageGate(result.snapshot.client_key);
        if (!fallbackGate.allowed) {
          console.log(`IGNORADA: ${fallbackGate.reason}; mensagem não será repetida — ${item.href}`);
          continue;
        }
        sendKind = 'message';
        sendMessage = fallback.message;
        sendPrice = result.draft.fallback_price;
        sendPageUrl = fallback.messagePageUrl;
        fallbackFromProposalLimit = true;
        console.log(`LIMITE DE PROPOSTAS: enviando mensagem com estimativa negociável — ${item.href}`);
      }
    } else {
      const messageGateResult = messageGate(result.snapshot.client_key);
      if (!messageGateResult.allowed) {
        console.log(`IGNORADA: ${messageGateResult.reason}; mensagem não será repetida — ${item.href}`);
        continue;
      }
    }
    const tracked = tracking('register', {
      url: result.snapshot.url, title: result.snapshot.title, subject: result.draft.subject,
      message: sendMessage, price: sendPrice, send_kind: sendKind,
      project_type: result.snapshot.category || result.snapshot.title,
      client_key: result.snapshot.client_url || result.snapshot.client,
      validation_status: result.draft.validation_status || 'PENDING',
    });
    if (tracked.already_sent) {
      console.log(`IGNORADA: este cliente/vaga já foi enviado anteriormente — ${item.href}`);
      continue;
    }
    processed += 1;
    if (!platform.allow_submission) {
      console.log(`RASCUNHO PREPARADO para ${platform.name}; envio externo permanece desativado até validação dos seletores e da política da plataforma.`);
      continue;
    }
    const answer = autoSend ? 'ENVIAR' : await ask('Digite ENVIAR para clicar no envio desta vaga, ou Enter para deixar como rascunho: ');
    if (answer.trim() === 'ENVIAR') {
      const submit = await visibleLocator(jobPage, sendKind === 'message' ? selectors.proposal.submit_question : selectors.proposal.submit_proposal);
      for (const selector of selectors.proposal.confirmations) {
        const checkbox = jobPage.locator(selector).first();
        if (await checkbox.count() && await checkbox.isVisible() && !await checkbox.isChecked()) await checkbox.check();
      }
      if (submit) {
        if (!claimSend(tracked.proposal_id)) {
          console.log('IGNORADA: outro processo já reservou ou enviou esta proposta.');
          continue;
        }
        const responsePromise = jobPage.waitForResponse((response) =>
          response.request().method() === 'POST' && /\/services\/project\//.test(response.url()),
        { timeout: 15000 }).catch(() => null);
        await submit.click();
        const continueButton = await visibleLocator(jobPage, selectors.proposal.continue_modal);
        if (continueButton) await continueButton.click();
        const response = await responsePromise;
        await jobPage.waitForLoadState('domcontentloaded').catch(() => {});
        if (response && !response.ok()) throw new Error(`Envio recusado pelo site (HTTP ${response.status()}).`);
        if (sendKind === 'message') {
          const confirmed = await verifyQuestion(jobPage, sendPageUrl, sendMessage);
          if (!confirmed) throw new Error('O site não confirmou a mensagem na conversa; envio não será repetido automaticamente.');
          markSent(tracked.proposal_id, `question-${tracked.proposal_id}`, conversationIdFromUrl(jobPage.url()));
          operations('sent', { url: result.snapshot.url });
          cycleSent += 1;
          console.log(`${fallbackFromProposalLimit ? 'MENSAGEM FALLBACK' : 'MENSAGEM'} CONFIRMADA na conversa: ${jobPage.url()}`);
        } else if (response?.ok()) {
          markSent(tracked.proposal_id, `proposal-${tracked.proposal_id}`, conversationIdFromUrl(jobPage.url()));
          operations('sent', { url: result.snapshot.url });
          cycleSent += 1;
          console.log(`ENVIO CONFIRMADO pelo site (HTTP ${response.status()}). Página atual: ${jobPage.url()}`);
        } else {
          console.log('Clique realizado, mas sem confirmação inequívoca do site; o bot não repetirá automaticamente.');
        }
      }
      else console.log('Botão de envio não localizado; rascunho preservado.');
    }
    } catch (error) {
      if (browserDisconnected(error)) throw error;
      console.error(`FALHA na vaga ${item.href}: ${error.message}`);
    } finally {
      await jobPage.close().catch(() => {});
    }
    }
    idleCycles = (platform.allow_submission && !dryRun && !killSwitch ? cycleSent : processed) === 0 ? idleCycles + 1 : 0;
    console.log(`Ciclo concluído: ${inspected} vaga(s) inspecionada(s), ${cycleSent} envio(s). Chromium permanece aberto.`);
    if (runForever && maxIdleCycles > 0 && idleCycles >= maxIdleCycles) {
      console.log(`Caça encerrada após ${idleCycles} ciclo(s) sem envio para economizar créditos.`);
      break;
    }
    if (!runForever) {
      await ask('Pressione Enter para fechar Chromium: ');
      break;
    }
    firstCycle = false;
    if (pollSeconds > 0) await page.waitForTimeout(pollSeconds * 1000);
    else await page.waitForTimeout(1000);
    }
  } finally {
    if (context) await context.close().catch(() => {});
    rl.close();
  }
}

main().catch((error) => { console.error(error.message); rl.close(); process.exitCode = browserDisconnected(error) ? 75 : 1; });
