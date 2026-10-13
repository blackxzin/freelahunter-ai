import { test, expect } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { getPlatform, getSelectors, normalizeJobUrl } from '../scripts/selectors.mjs';
import { readSnapshot } from '../scripts/job_snapshot.mjs';
import { saveDraft } from '../scripts/draft_store.mjs';

test('Workana prepara orçamento em reais e preserva rascunho para revisão', async ({ page }) => {
  await page.route('https://www.workana.com/**', route => route.fulfill({ contentType: 'text/html', body: `
    <nav>Premium R$ 999</nav><h1 class="project-title">Landing page</h1>
    <section class="project-description"><div class="html-desc">Criar uma landing page responsiva.
    Layout aprovado, textos e imagens fornecidos. Formulário visual, HTML e CSS,
    aceite em desktop e celular com uma revisão.</div></section>
    <div class="project-budget">R$ 1.000,00 - 3.000,00</div>
    <div class="project-deadline">2 semanas</div>
    <div class="client-info"><a class="user-name" href="/client/ana">Ana</a></div>` }));
  await page.goto('https://www.workana.com/job/landing-page');
  const snapshot = await readSnapshot(page, getSelectors('workana'), 'workana');
  expect(snapshot.currency).toBe('BRL');
  expect(snapshot.budget_max).toBe(3000);
  expect(snapshot.deadline_days).toBe(14);
  expect(snapshot.client).toBe('Ana');
  expect(snapshot.description).not.toContain('Premium');
  expect(snapshot.client_url).toBe('https://www.workana.com/client/ana');
  const result = spawnSync('python', ['scripts/generate_proposal.py'], { input: JSON.stringify(snapshot), encoding: 'utf8' });
  expect(result.status, result.stderr).toBe(0);
  const draft = JSON.parse(result.stdout);
  expect(draft.message || draft.question).toContain('Olá');
  expect(draft.fallback_message).toContain('R$');
  const root = mkdtempSync(join(tmpdir(), 'workana-draft-'));
  try {
    const path = saveDraft(root, 'workana', snapshot, draft);
    expect(JSON.parse(readFileSync(path, 'utf8')).snapshot.url).toBe(snapshot.url);
    saveDraft(root, 'workana', snapshot, draft);
    expect(readdirSync(join(root, 'state/drafts/workana'))).toHaveLength(1);
  } finally { rmSync(root, { recursive: true }); }
});

test('links da Workana são deduplicáveis e não aceitam outros hosts', () => {
  const platform = getPlatform('https://www.workana.com/pt/jobs');
  expect(platform.name).toBe('workana');
  expect(platform.allow_submission).toBe(false);
  expect(normalizeJobUrl('/pt/job/api-python?ref=search#details', platform)).toBe('https://www.workana.com/pt/job/api-python');
  expect(() => normalizeJobUrl('https://example.com/job/api', platform)).toThrow('URL de vaga inválida');
  expect(() => normalizeJobUrl('http://www.workana.com/job/api', platform)).toThrow('URL de vaga inválida');
  expect(() => getPlatform('workanna')).toThrow('Plataforma desconhecida');
});

test('taxa por hora não vira orçamento total do projeto', async ({ page }) => {
  await page.setContent('<h1>API Python</h1><div class="project-description">API para relatórios</div><div class="budget">US$ 25 - 50 per hour</div>');
  const snapshot = await readSnapshot(page, getSelectors('workana'), 'workana');
  expect(snapshot.currency).toBe('USD');
  expect(snapshot.budget_max).toBeNull();
});
