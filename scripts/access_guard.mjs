const ACCESS_CHALLENGE = /cloudflare|cf-chl|turnstile|captcha|verify you are human|checking your browser|just a moment|access denied/i;

function isUpworkLoginPage(page) {
  return /upwork\.com\/.*(?:account-security\/login|\/login|\/signup)(?:[/?#]|$)/i.test(page.url());
}

function isGooglePage(page) {
  return /accounts\.google\.com/i.test(page.url());
}

export async function selectUpworkGoogleAccount(page, auth = {}, accountLabel = 'Lucas') {
  if (!isUpworkLoginPage(page) && !isGooglePage(page)) return false;
  let authPage = page;
  const googleButtons = auth.google_button || [
    "button:has-text('Continue with Google')",
    "button:has-text('Google')",
    "a:has-text('Google')",
  ];
  if (isUpworkLoginPage(page)) {
    if (/\/signup(?:[/?#]|$)/i.test(page.url())) {
      const loginLink = page.locator("a[href*='account-security/login'], a:has-text('Log In')").first();
      if (await loginLink.count() && await loginLink.isVisible().catch(() => false)) {
        await loginLink.click();
        await page.waitForTimeout(1200);
      }
    }
    for (const selector of googleButtons) {
      const button = page.locator(selector).first();
      if (await button.count() && await button.isVisible().catch(() => false)) {
        const popupPromise = page.waitForEvent('popup', { timeout: 3000 }).catch(() => null);
        await button.click();
        const popup = await popupPromise;
        if (popup) {
          authPage = popup;
          await popup.waitForLoadState('domcontentloaded').catch(() => {});
        }
        await authPage.waitForTimeout(1500);
        break;
      }
    }
  }
  if (!isGooglePage(authPage)) return false;
  const accountSelectors = auth.google_account || ['[data-identifier]', '[role="link"]', '[role="button"]', 'li'];
  const wanted = String(accountLabel || '').trim();
  const escaped = wanted.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const matches = [];
  const matchKeys = new Set();
  for (const selector of accountSelectors) {
    const nodes = authPage.locator(selector);
    for (let index = 0; index < await nodes.count(); index += 1) {
      const node = nodes.nth(index);
      if (!await node.isVisible().catch(() => false)) continue;
      const text = (await node.innerText().catch(() => '')).replace(/\s+/g, ' ').trim();
      const aria = await node.getAttribute('aria-label').catch(() => '') || '';
      const identifier = await node.getAttribute('data-identifier').catch(() => '') || '';
      const candidate = `${text} ${aria} ${identifier}`.replace(/\s+/g, ' ').trim();
      if (new RegExp(`^${escaped}(?:$|\\s)`, 'i').test(candidate)) {
        const email = candidate.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i)?.[0]?.toLowerCase();
        const key = (email || identifier || aria || text).replace(/\s+/g, ' ').trim().toLowerCase();
        if (!matchKeys.has(key)) {
          matchKeys.add(key);
          matches.push(node);
        }
      }
    }
  }
  if (matches.length === 0) return false;
  if (matches.length > 1) throw new Error(`Mais de uma conta Google corresponde a "${wanted}"; seleção automática bloqueada.`);
  await matches[0].click();
  await authPage.waitForTimeout(1500);
  return true;
}

export async function isAccessChallenge(page) {
  const title = await page.title().catch(() => '');
  const body = await page.locator('body').innerText().catch(() => '');
  return ACCESS_CHALLENGE.test(`${title}\n${body.slice(0, 5000)}`);
}

export async function ensureAccessible(page, platform) {
  if (await isAccessChallenge(page)) {
    throw new Error(`A página ${platform} apresentou Cloudflare/CAPTCHA; automação pausada para ação manual.`);
  }
}

export async function waitForManualAccess(page, platform, ask, timeoutMs = 300000) {
  const login = () => /\/(?:login|sign-in|signin|account-security\/login)(?:[/?#]|$)/i.test(page.url());
  if (login()) {
    console.log(`Sessão de ${platform} expirada. Faça login manualmente no navegador.`);
    await ask('Após concluir o login, pressione Enter: ');
    if (login()) throw new Error(`Login de ${platform} ainda não concluído.`);
  }
  if (!(await isAccessChallenge(page))) return;
  console.log(`A página ${platform} apresentou Cloudflare/CAPTCHA. Resolva manualmente no Chromium visível.`);
  await ask('Depois que a página normal aparecer, pressione Enter aqui: ');
  const started = Date.now();
  while (await isAccessChallenge(page)) {
    if (Date.now() - started >= timeoutMs) {
      throw new Error(`O desafio de acesso do ${platform} não foi concluído no tempo esperado.`);
    }
    await page.waitForTimeout(2000);
  }
}
