// Cross-platform launcher: `VAR=x cmd` env prefixes do not work in Windows shells.
const defaults = {
  PLATFORM: 'upwork', MAX_JOBS: '2', HUNT_INTERVAL_MINUTES: '12',
  RUN_FOREVER: 'true', AUTO_SEND: 'false', DRY_RUN: 'true',
};
for (const [key, value] of Object.entries(defaults)) process.env[key] ??= value;
await import('./interactive_hunt.mjs');
