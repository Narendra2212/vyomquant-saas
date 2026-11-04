#!/usr/bin/env node
/**
 * ═══════════════════════════════════════════════════════════════════════════════════════
 * Does the download page's promise actually hold for a visitor?
 * ═══════════════════════════════════════════════════════════════════════════════════════
 *
 * WHY THIS EXISTS
 * ---------------
 * Every previous way this page has been wrong was invisible to CI:
 *
 *   - four `/releases/...` URLs that returned 403 for two years, because `aws s3 sync
 *     dist/ --delete` removed the prefix on every deploy;
 *   - advertised sizes of 84.2 / 78.5 / 75.4 / 68.2 MB for files that were 64-69 byte text
 *     placeholders, one with the SHA-256 of the EMPTY STRING;
 *   - and then four `github.com/.../releases/download/...` URLs that returned 404 to every
 *     visitor, because that repository is PRIVATE -- which no unit test could see, since
 *     the URLs were perfectly well-formed.
 *
 * The common thread is that the declaration was checked and the SERVED BYTES were not. So
 * this script fetches what the page advertises, WITH NO CREDENTIALS, exactly as a visitor
 * would, and compares the bytes to the digest written beside the button.
 *
 * It reads `src/design/desktopReleases.js` rather than taking arguments, so there is no
 * second list of URLs to keep in step; if the declaration changes, this follows.
 *
 * COST. The four artifacts total ~98 MB, which is a fraction of a US cent of CloudFront
 * egress per deploy. Verifying `Content-Length` alone would be cheaper and would catch a
 * 404 or a truncated object, but not a corrupted or substituted one -- and a substituted
 * installer is the failure that matters most on a page that publishes checksums.
 *
 * Usage:  node scripts/verify-published-downloads.mjs
 * Exit:   0 if every artifact is reachable, complete and matches its digest; 1 otherwise.
 */

import { createHash } from 'node:crypto';

import {
  DESKTOP_ARTIFACTS,
  DESKTOP_PLATFORM_ORDER,
  DESKTOP_RELEASE,
} from '../src/design/desktopReleases.js';

const UA = 'vyomquant-deploy-verify';

/** Fetch with no Authorization header, following redirects, returning bytes and headers. */
const fetchBytes = async (url) => {
  const res = await fetch(url, { headers: { 'User-Agent': UA }, redirect: 'follow' });
  const buf = Buffer.from(await res.arrayBuffer());
  return {
    status: res.status,
    contentLength: res.headers.get('content-length'),
    // When a response is encoded, `Content-Length` counts the ENCODED bytes and comparing
    // it to the file size is wrong rather than informative. CloudFront does not compress
    // `application/octet-stream`, so this is not expected to fire -- but a byte count that
    // silently means something else is exactly how a check starts lying.
    contentEncoding: res.headers.get('content-encoding'),
    buf,
  };
};

/** First bytes of each container, so an HTML error page served with 200 cannot pass. */
const MAGIC = {
  '.exe': Buffer.from('MZ', 'ascii'),
  '.msi': Buffer.from([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]),
  '.deb': Buffer.from('!<arch>\n', 'ascii'),
  '.rpm': Buffer.from([0xed, 0xab, 0xee, 0xdb]),
  '.AppImage': Buffer.from([0x7f, 0x45, 0x4c, 0x46]),
};

const magicHolds = (filename, buf) => {
  const ext = filename.slice(filename.lastIndexOf('.'));
  if (ext === '.dmg') {
    // UDIF stores its trailer, not a header signature.
    return buf.subarray(Math.max(0, buf.length - 512)).includes(Buffer.from('koly', 'ascii'));
  }
  const want = MAGIC[ext];
  return want ? buf.subarray(0, want.length).equals(want) : false;
};

const results = [];
let ok = true;

console.log('='.repeat(78));
console.log(`VERIFYING THE PUBLISHED DOWNLOADS FOR ${DESKTOP_RELEASE.tag}`);
console.log('='.repeat(78));

for (const platform of DESKTOP_PLATFORM_ORDER) {
  const a = DESKTOP_ARTIFACTS[platform];
  const started = Date.now();
  let row;
  try {
    const { status, contentLength, contentEncoding, buf } = await fetchBytes(a.url);
    const digest = createHash('sha256').update(buf).digest('hex');
    row = {
      platform,
      url: a.url,
      status,
      statusOk: status === 200,
      lengthHeader: contentEncoding ? `${contentLength} (${contentEncoding})` : contentLength,
      lengthOk: contentEncoding
        ? true
        : contentLength !== null && Number(contentLength) === a.bytes,
      bytesOk: buf.length === a.bytes,
      digest,
      digestOk: digest === a.sha256,
      magicOk: magicHolds(a.filename, buf),
      seconds: (Date.now() - started) / 1000,
    };
  } catch (err) {
    row = { platform, url: a.url, error: String(err) };
  }
  row.pass = Boolean(
    row.statusOk && row.lengthOk && row.bytesOk && row.digestOk && row.magicOk,
  );
  if (!row.pass) ok = false;
  results.push(row);

  console.log('');
  console.log(`  ${platform}  ${a.filename}`);
  console.log(`    url            : ${row.url}`);
  if (row.error) {
    console.log(`    FETCH FAILED   : ${row.error}`);
  } else {
    console.log(`    http           : ${row.status} (200 expected: ${row.statusOk})`);
    console.log(
      `    Content-Length : ${row.lengthHeader} (declared ${a.bytes}: ${row.lengthOk})`,
    );
    console.log(`    bytes received : ${row.bytesOk} in ${row.seconds.toFixed(1)}s`);
    console.log(`    sha256 served  : ${row.digest}`);
    console.log(`    sha256 declared: ${a.sha256}`);
    console.log(`    digest match   : ${row.digestOk}   magic bytes: ${row.magicOk}`);
  }
  console.log(`    VERDICT        : ${row.pass ? 'PASS' : 'FAIL'}`);
}

// The release notes are a control the page links; a 404 there is the same class of defect.
console.log('');
let notesOk = false;
try {
  const { status, buf } = await fetchBytes(DESKTOP_RELEASE.notesUrl);
  // Must be the notes, not an SPA shell or an error page: the digest block is the tell.
  const text = buf.toString('utf8');
  notesOk = status === 200 && text.includes('SHA-256') && text.includes(DESKTOP_RELEASE.version);
  console.log(`  release notes    : http ${status}, ${buf.length} bytes, usable: ${notesOk}`);
  console.log(`    ${DESKTOP_RELEASE.notesUrl}`);
} catch (err) {
  console.log(`  release notes    : FETCH FAILED ${err}`);
}
if (!notesOk) ok = false;

// Non-vacuity. A filename that is not published must NOT return 200 with content, or the
// checks above would pass against a CDN that answers everything.
console.log('');
const probe = DESKTOP_ARTIFACTS[DESKTOP_PLATFORM_ORDER[0]].url.replace(
  DESKTOP_RELEASE.version,
  '9.9.9',
);
const probeFirst = DESKTOP_ARTIFACTS[DESKTOP_PLATFORM_ORDER[0]];
let negOk;
try {
  const { status, buf } = await fetchBytes(probe);
  // Not simply "status is not 200". If a CloudFront custom error response is ever added to
  // serve index.html for a missing key -- the usual SPA deep-link fix -- this would start
  // returning 200 and a status-only check would fail the deploy for no reason. What must be
  // true is that the CDN does not serve A VALID INSTALLER under a name nobody published.
  const looksLikeTheInstaller =
    status === 200
    && buf.length === probeFirst.bytes
    && magicHolds(probeFirst.filename, buf);
  negOk = !looksLikeTheInstaller;
  console.log(
    `  non-vacuity probe: http ${status}, ${buf.length} bytes for an unpublished name; `
    + `not served as an installer: ${negOk}`,
  );
  console.log(`    ${probe}`);
} catch {
  negOk = true;
  console.log('  non-vacuity probe: request failed, which is also not a published installer');
}
if (!negOk) ok = false;

const passed = results.filter((r) => r.pass).length;
console.log('');
console.log('='.repeat(78));
console.log(
  `artifacts passing: ${passed} of ${DESKTOP_PLATFORM_ORDER.length}   `
  + `notes reachable: ${notesOk}   non-vacuity: ${negOk}`,
);
console.log(`OVERALL: ${ok ? 'PASS' : 'FAIL'}`);
console.log('='.repeat(78));

if (!ok) {
  console.error('');
  console.error('The download page is advertising at least one artifact a visitor cannot get,');
  console.error('or cannot verify against the checksum printed beside the button.');
  process.exit(1);
}
