const NETWORK_ERROR = /net::ERR_|timeout|ECONNRESET|ENOTFOUND|EAI_AGAIN|socket hang up/i;
export function browserDisconnected(error) {
  return /browser has been closed|browser closed|browser.*disconnected|Target page, context or browser has been closed/i.test(String(error?.message || error));
}
export async function navigateWithRetry(page, url, options = {}, { attempts = 3, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)), log = console.log } = {}) {
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await page.goto(url, options);
      if (response && (response.status() === 429 || response.status() >= 500)) {
        const error = new Error(`HTTP temporário ${response.status()}`);
        error.retryable = true;
        throw error;
      }
      return response;
    } catch (error) {
      if (browserDisconnected(error) || (!error.retryable && !NETWORK_ERROR.test(error.message)) || attempt === attempts) throw error;
      const delay = Math.min(60000, 5000 * 2 ** (attempt - 1));
      log(JSON.stringify({ event: 'navigation_retry', attempt, delay_ms: delay, message: error.message }));
      await sleep(delay);
    }
  }
}
