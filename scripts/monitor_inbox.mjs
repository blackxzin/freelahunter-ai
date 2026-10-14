import { chromium } from '@playwright/test';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { loadLocalEnv } from './local_env.mjs';
import { sendDiscordNotification } from './discord_notify.mjs';
import { selectors } from './selectors.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
loadLocalEnv(`${root}/.env`);
const profileDir = process.env.BROWSER_PROFILE_DIR || `${root}/browser-profile`;
const stateDir = `${root}/state`;
const statePath = process.env.INBOX_STATE_PATH || `${stateDir}/99freelas-inbox.json`;
const configuredInterval = Number(process.env.INBOX_POLL_SECONDS || 60);
const intervalSeconds = Number.isFinite(configuredInterval) ? Math.max(20, configuredInterval) : 60;
const initializeOnly = process.env.INBOX_INITIALIZE_ONLY === 'true';
const webhookUrl = process.env.DISCORD_WEBHOOK_URL;
const maxSeenEntries = 5000;

export function messageKey(conversation) {
  return createHash('sha256').update(`${conversation.id}|${conversation.timestamp}|${conversation.preview}`).digest('hex');
}

export function incomingConversations(conversations, seen = {}) {
  return conversations.filter((conversation) =>
    conversation.unread && !conversation.sentByMe && !seen[messageKey(conversation)]);
}

export function formatDiscordAlert(conversation) {
  const preview = conversation.preview.replace(/\s+/g, ' ').trim().slice(0, 900);
  return ['🔔 Nova resposta no 99Freelas', '', `Cliente: ${conversation.client || 'Não identificado'}`,
    `Projeto: ${conversation.project || 'Não identificado'}`, '', `Mensagem: ${preview || '(sem prévia)'}`, '',
    `Abrir conversa: ${conversation.url}`].join('\n');
}

function logEvent(event, details = {}) {
  console.log(JSON.stringify({ event, timestamp: new Date().toISOString(), details }));
}

function recordIncomingMessage(conversation) {
  const child = spawnSync('python', ['scripts/proposal_tracking.py', 'message', '--conversation-id', conversation.id, '--direction', 'inbound', '--scope-context', '--text', conversation.preview], { cwd: root, encoding: 'utf8' });
  if (child.status !== 0) logEvent('conversation_persist_error', { conversation_id: conversation.id, error: child.stderr.trim() });
}

function loadState() {
  if (!existsSync(statePath)) return { initialized: false, seen: {} };
  try {
    const state = JSON.parse(readFileSync(statePath, 'utf8'));
    return state && typeof state.seen === 'object'
      ? { initialized: state.initialized === true, seen: state.seen }
      : { initialized: false, seen: {} };
  } catch { return { initialized: false, seen: {} }; }
}

function saveState(state) {
  mkdirSync(dirname(statePath), { recursive: true, mode: 0o700 });
  const temporary = `${statePath}.tmp`;
  writeFileSync(temporary, JSON.stringify(state), { mode: 0o600 });
  renameSync(temporary, statePath);
}

function pruneState(state) {
  const entries = Object.entries(state.seen);
  if (entries.length <= maxSeenEntries) return;
  entries.sort((a, b) => Number(a[1]) - Number(b[1]));
  state.seen = Object.fromEntries(entries.slice(-maxSeenEntries));
}

async function scanInbox(page) {
  await page.goto('https://www.99freelas.com.br/messages/inbox', { waitUntil: 'domcontentloaded', timeout: 30000 });
  let rowSelector = selectors.inbox.row[0];
  for (const candidate of selectors.inbox.row) {
    if (await page.locator(candidate).count()) { rowSelector = candidate; break; }
  }
  await page.waitForSelector(rowSelector, { timeout: 10000 });
  const rows = await page.locator(rowSelector).evaluateAll((rows, fieldSelectors) => rows.map((row) => {
    const text = (key) => row.querySelector(fieldSelectors[key].join(','))?.textContent?.trim() || '';
    const id = row.getAttribute('data-id') || '';
    const timestampNode = row.querySelector(fieldSelectors.timestamp.join(','));
    const timestamp = timestampNode?.getAttribute('cp-datetime') || '';
    return {
      id,
      timestamp,
      project: text('project'),
      client: text('client'),
      preview: text('preview'),
      unread: !row.classList.contains('visualizada'),
      sentByMe: Boolean(row.querySelector(fieldSelectors.sent.join(','))),
      url: `https://www.99freelas.com.br/messages/inbox/${id}`,
    };
  }), { timestamp: selectors.inbox.timestamp, project: selectors.inbox.project, client: selectors.inbox.client,
    preview: selectors.inbox.preview, sent: selectors.inbox.sent } );
  return rows.filter((conversation) => conversation.id && conversation.timestamp);
}

async function main() {
  if (!webhookUrl) throw new Error('Defina DISCORD_WEBHOOK_URL no arquivo .env.');
  let context;
  try {
    context = await chromium.launchPersistentContext(profileDir, {
      headless: true,
      ignoreDefaultArgs: ['--enable-automation'],
      args: ['--disable-blink-features=AutomationControlled'],
    });
    const page = context.pages()[0] || await context.newPage();
    const state = loadState();
    while (true) {
      try {
        const conversations = await scanInbox(page);
        if (initializeOnly) {
          for (const conversation of conversations) state.seen[messageKey(conversation)] = Date.now();
          state.initialized = true;
          pruneState(state);
          saveState(state);
          logEvent('inbox_checked', { conversations: conversations.length, delivered: 0 });
          return;
        }
        const incoming = incomingConversations(conversations, state.seen);
        const incomingKeys = new Set(incoming.map(messageKey));
        let delivered = 0;
        for (const conversation of conversations) {
          const key = messageKey(conversation);
          if (!incomingKeys.has(key)) state.seen[key] = Date.now();
        }
        if (state.initialized) {
          for (const conversation of incoming) {
            try {
              recordIncomingMessage(conversation);
              await sendDiscordNotification(webhookUrl, formatDiscordAlert(conversation));
              state.seen[messageKey(conversation)] = Date.now();
              delivered += 1;
              saveState(state);
            } catch (error) {
              logEvent('discord_notification_error', { conversation_id: conversation.id, error: error.message });
            }
          }
        } else {
          for (const conversation of incoming) state.seen[messageKey(conversation)] = Date.now();
          state.initialized = true;
        }
        pruneState(state);
        saveState(state);
        logEvent('inbox_checked', { conversations: conversations.length, delivered });
      } catch (error) {
        logEvent('inbox_error', { error: error.message });
      }
      await page.waitForTimeout(intervalSeconds * 1000);
    }
  } finally {
    if (context) await context.close();
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => { logEvent('monitor_fatal_error', { error: error.message }); process.exitCode = 1; });
}
