/**
 * Locates a candidate product-logo file on this host.
 *
 * WHY A SCRIPT. The PowerShell wrapper on this host strips `$` from command strings, so
 * `foreach ($p in ...)` and `$_` pipelines cannot be used. This does the same search
 * deterministically and prints sizes and mtimes so a human can judge provenance.
 *
 * It only READS. It copies nothing and writes nothing outside stdout — identifying an
 * asset and adopting one are separate decisions, and adopting one is gated on approval.
 */

import { readdirSync, statSync, existsSync } from 'node:fs';
import { join, extname } from 'node:path';
import { homedir } from 'node:os';

const IMAGE_EXT = new Set(['.png', '.svg', '.jpg', '.jpeg', '.webp', '.ico', '.icns']);

const ROOTS = [
  join(homedir(), 'Downloads'),
  join(homedir(), 'Desktop'),
  join(homedir(), 'Pictures'),
  join(homedir(), 'OneDrive', 'Pictures'),
  join(homedir(), 'OneDrive', 'Desktop'),
  join(homedir(), 'AppData', 'Local', 'Temp'),
  'C:\\temp',
  'C:\\tmp',
  'C:\\mnt',
  'C:\\logo',
  process.cwd(),
];

// Directories that are never a source of a hand-supplied brand asset and are huge.
const SKIP = /node_modules|\.git[\\/]|\\target\\|site-packages|\.hypothesis|\.venv|releases_dist|[\\/]dist[\\/]|\.ccxt_upgrade_probe|Microsoft|Windows Kits|Packages/i;

const found = [];

function walk(dir, depth) {
  if (depth < 0) return;
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return; // unreadable (permissions, junction) — not an error for a search
  }
  for (const entry of entries) {
    const full = join(dir, entry.name);
    if (SKIP.test(full)) continue;
    try {
      if (entry.isDirectory()) {
        walk(full, depth - 1);
      } else if (IMAGE_EXT.has(extname(entry.name).toLowerCase())) {
        const st = statSync(full);
        found.push({ full, size: st.size, mtime: st.mtime });
      }
    } catch {
      /* skip */
    }
  }
}

for (const root of ROOTS) {
  if (existsSync(root)) walk(root, 3);
}

found.sort((a, b) => b.mtime - a.mtime);

console.log(`Searched ${ROOTS.filter((r) => existsSync(r)).length} root(s). Found ${found.length} image file(s).`);
console.log('');
console.log('Most recently modified (top 30):');
for (const f of found.slice(0, 30)) {
  console.log(
    `${f.mtime.toISOString()}  ${String(f.size).padStart(9)}  ${f.full}`,
  );
}

const BRANDISH = found.filter((f) => /logo|brand|vyom|\bvq\b|mark|icon/i.test(f.full));
console.log('');
console.log(`Name suggests branding (${BRANDISH.length}):`);
for (const f of BRANDISH.slice(0, 40)) {
  console.log(`${f.mtime.toISOString()}  ${String(f.size).padStart(9)}  ${f.full}`);
}
