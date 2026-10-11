/**
 * Reports the intrinsic properties of a candidate logo file, and measures them against
 * what `src-tauri/tauri.conf.json` and the web surfaces actually require.
 *
 * READ-ONLY. It copies, converts and writes nothing: identifying an asset and adopting
 * one are separate decisions, and adopting one is gated on approval and on the asset being
 * technically sufficient. This script exists to establish whether it IS sufficient.
 *
 * Dimensions are parsed from the file header directly rather than through an image
 * library, because adding an image dependency to inspect one file would be a worse trade
 * than 40 lines of header parsing.
 */

import { readFileSync, existsSync } from 'node:fs';

const path = process.argv[2];
if (!path || !existsSync(path)) {
  console.error(`No such file: ${path}`);
  process.exit(1);
}

const buf = readFileSync(path);

/** PNG: width/height are big-endian u32 at byte 16 and 20 of the IHDR chunk. */
function png(b) {
  if (b.length < 24) return null;
  const sig = b.subarray(0, 8).toString('hex');
  if (sig !== '89504e470d0a1a0a') return null;
  return { format: 'png', width: b.readUInt32BE(16), height: b.readUInt32BE(20) };
}

/**
 * JPEG: walk the marker segments to the first SOFn, whose payload carries height then
 * width as big-endian u16. Markers without a payload (RSTn, SOI, EOI, TEM) are skipped.
 */
function jpeg(b) {
  if (b.length < 4 || b[0] !== 0xff || b[1] !== 0xd8) return null;
  let i = 2;
  while (i < b.length - 9) {
    if (b[i] !== 0xff) { i += 1; continue; }
    const marker = b[i + 1];
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
      i += 2;
      continue;
    }
    const len = b.readUInt16BE(i + 2);
    // SOF0..SOF3, SOF5..SOF7, SOF9..SOF11, SOF13..SOF15 — all carry the frame dimensions.
    const isSOF =
      (marker >= 0xc0 && marker <= 0xc3)
      || (marker >= 0xc5 && marker <= 0xc7)
      || (marker >= 0xc9 && marker <= 0xcb)
      || (marker >= 0xcd && marker <= 0xcf);
    if (isSOF) {
      return { format: 'jpeg', height: b.readUInt16BE(i + 5), width: b.readUInt16BE(i + 7) };
    }
    i += 2 + len;
  }
  return { format: 'jpeg', width: null, height: null };
}

function svg(b) {
  const head = b.subarray(0, 2048).toString('utf8');
  if (!head.includes('<svg')) return null;
  const vb = /viewBox\s*=\s*["']([^"']+)["']/.exec(head);
  return { format: 'svg', width: 'vector', height: 'vector', viewBox: vb ? vb[1] : null };
}

const info = png(buf) ?? jpeg(buf) ?? svg(buf) ?? { format: 'unknown', width: null, height: null };

console.log('ASSET');
console.log(`  path          ${path}`);
console.log(`  bytes         ${buf.length}`);
console.log(`  format        ${info.format}`);
console.log(`  dimensions    ${info.width} x ${info.height}`);
console.log(`  alpha channel ${info.format === 'png' ? `colour-type ${buf[25]} (6 or 4 = has alpha)` : info.format === 'svg' ? 'vector (alpha native)' : 'NO — JPEG has no alpha channel'}`);
console.log('');

// Every raster size the repository actually asks for.
const REQUIRED = [
  ['src-tauri/icons/32x32.png', 32],
  ['src-tauri/icons/128x128.png', 128],
  ['src-tauri/icons/128x128@2x.png', 256],
  ['src-tauri/icons/icon.png', 512],
  ['src-tauri/icons/icon.ico (largest frame)', 256],
  ['src-tauri/icons/icon.icns (largest frame)', 1024],
  ['src-tauri/icons/Square30x30Logo.png', 30],
  ['src-tauri/icons/Square44x44Logo.png', 44],
  ['src-tauri/icons/Square71x71Logo.png', 71],
  ['src-tauri/icons/Square89x89Logo.png', 89],
  ['src-tauri/icons/Square107x107Logo.png', 107],
  ['src-tauri/icons/Square142x142Logo.png', 142],
  ['src-tauri/icons/Square150x150Logo.png', 150],
  ['src-tauri/icons/Square284x284Logo.png', 284],
  ['src-tauri/icons/Square310x310Logo.png', 310],
  ['src-tauri/icons/StoreLogo.png', 50],
  ['public/og-image.png (width)', 1200],
];

const side = typeof info.width === 'number' ? Math.min(info.width, info.height) : 0;
console.log(`SUFFICIENCY against a ${side}px source (no upscaling permitted)`);
let blocked = 0;
for (const [target, px] of REQUIRED) {
  const ok = side >= px;
  if (!ok) blocked += 1;
  console.log(`  ${ok ? 'OK    ' : 'BLOCK '} ${String(px).padStart(4)}px  ${target}`);
}
console.log('');
console.log(`${REQUIRED.length - blocked}/${REQUIRED.length} targets reachable without upscaling; ${blocked} blocked.`);
