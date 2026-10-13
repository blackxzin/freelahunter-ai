import { createHash } from 'node:crypto';
import { mkdirSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export function saveDraft(root, platform, snapshot, draft) {
  const directory = join(root, 'state', 'drafts', platform);
  mkdirSync(directory, { recursive: true });
  const key = createHash('sha256').update(snapshot.url).digest('hex');
  const path = join(directory, `${key}.json`);
  const temporary = `${path}.${process.pid}.tmp`;
  writeFileSync(temporary, JSON.stringify({ platform, saved_at: new Date().toISOString(), snapshot, draft }, null, 2) + '\n', { mode: 0o600 });
  renameSync(temporary, path);
  return path;
}
