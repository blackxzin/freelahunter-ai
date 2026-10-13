import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const script = fileURLToPath(new URL('./interactive_hunt.mjs', import.meta.url));
let child;
let stopping = false;
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => { stopping = true; child?.kill(signal); });
const configured = Number(process.env.BROWSER_RESTART_LIMIT || 3);
const limit = Number.isInteger(configured) && configured >= 0 ? configured : 3;
for (let attempt = 0; ; attempt++) {
  const code = await new Promise(resolve => {
    child = spawn(process.execPath, [script], { stdio: 'inherit', env: process.env });
    child.once('error', error => { console.error(error.message); resolve(1); });
    child.once('exit', code => resolve(code ?? 1));
  });
  if (stopping || code !== 75 || attempt >= limit) { process.exitCode = stopping ? 0 : code; break; }
  console.log(JSON.stringify({ event: 'browser_restart', attempt: attempt + 1, limit }));
  await new Promise(resolve => setTimeout(resolve, Math.min(30000, 5000 * 2 ** attempt)));
  if (stopping) break;
}
