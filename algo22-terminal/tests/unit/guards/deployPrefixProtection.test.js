/**
 * ═══════════════════════════════════════════════════════════════════════════════════════
 * The deploy sync must not delete the desktop installers
 * ═══════════════════════════════════════════════════════════════════════════════════════
 *
 * WHY THIS GUARD EXISTS
 * ---------------------
 * The installers advertised on the download page are static objects in the same S3 bucket
 * that serves the SPA, under the `downloads/` prefix. They are NOT produced by
 * `npm run build`, so they do not appear in `dist/` — and `aws s3 sync dist/ --delete`
 * removes anything in the destination that the source does not contain.
 *
 * This repository has already been bitten by exactly that. `landingSections.test.jsx`
 * records the post-mortem of the previous download page, which advertised same-origin
 * `/releases/...` installers that returned 403: "CI's `dist/` carries no `releases/`
 * directory, so `aws s3 sync dist/ --delete` removes that prefix from the bucket on every
 * deploy." The response at the time was to move the artifacts to GitHub releases rather
 * than to fix the sync, so the sync stayed broken for any future prefix.
 *
 * The fix is one `--exclude "downloads/*"` on the one `--delete` pass that can reach the
 * prefix. One flag on one line is precisely the kind of thing a later edit drops without
 * noticing, and the symptom — four dead download buttons — appears only in production and
 * only after the next unrelated frontend deploy. So the flag is asserted here instead of
 * trusted.
 *
 * WHAT IT CHECKS
 * --------------
 * Every `aws s3 sync ... --delete` in `06-frontend-deploy.yml` must be unable to reach the
 * protected prefix, by one of the two mechanisms the file actually uses:
 *
 *   1. it excludes the prefix outright (`--exclude "downloads/*"`), or
 *   2. it is narrowed to an allow-list (`--exclude "*"` plus `--include` patterns), in
 *      which case no include may match a key under the prefix.
 *
 * Both are accepted because the workflow legitimately uses both: the hashed-asset pass and
 * the HTML pass are allow-listed, and the unhashed pass is not.
 *
 * The protected prefix is not hardcoded twice: it is derived from the URL the download
 * surface actually advertises, so moving the installers without updating the workflow
 * fails here rather than in production.
 */

import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { DESKTOP_ARTIFACTS, DESKTOP_PLATFORM_ORDER } from '../../../src/design/desktopReleases.js';

const TERMINAL_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const WORKFLOW_PATH = join(TERMINAL_ROOT, '..', '.github', 'workflows', '06-frontend-deploy.yml');
const workflow = readFileSync(WORKFLOW_PATH, 'utf8');

/** The bucket the SPA is served from, as the workflow declares it. */
const BUCKET = /^\s*S3_BUCKET:\s*(\S+)\s*$/m.exec(workflow)?.[1];

/**
 * Collapse shell line continuations so a multi-line `aws s3 sync` reads as one command,
 * then return every `aws s3 sync` invocation found.
 */
const syncCommands = (yaml) => {
  const joined = yaml.replace(/\\\r?\n\s*/g, ' ');
  return joined
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => /^aws\s+s3\s+sync\b/.test(line));
};

/** The `--exclude`/`--include` patterns of one command, in the order given. */
const filtersOf = (cmd) => {
  const out = [];
  const re = /--(exclude|include)\s+"([^"]*)"/g;
  let m = re.exec(cmd);
  while (m !== null) {
    out.push({ kind: m[1], pattern: m[2] });
    m = re.exec(cmd);
  }
  return out;
};

/**
 * Translate one S3 filter pattern into a matcher. The CLI's patterns are shell-style:
 * `*` matches across `/` as well, which is why `assets/*` covers nested keys.
 */
const matches = (pattern, key) => {
  const rx = new RegExp(
    `^${pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`,
  );
  return rx.test(key);
};

/**
 * Would this command delete `key` from the destination?
 *
 * The CLI applies filters in order and the last one to match wins, with everything
 * included by default — the same rule for the delete scan as for the upload scan.
 */
const wouldDelete = (cmd, key) => {
  if (!/--delete\b/.test(cmd)) return false;
  let included = true;
  for (const { kind, pattern } of filtersOf(cmd)) {
    if (matches(pattern, key)) included = kind === 'include';
  }
  return included;
};

/** The prefix the download surface actually points at, derived rather than retyped. */
const advertisedPrefixes = () => {
  const prefixes = new Set();
  for (const platform of DESKTOP_PLATFORM_ORDER) {
    const { url } = DESKTOP_ARTIFACTS[platform];
    const { hostname, pathname } = new URL(url);
    // Only same-origin artifacts live in the bucket; anything else is hosted elsewhere
    // and this workflow cannot delete it.
    if (!/(^|\.)vyomquant\.in$/.test(hostname)) continue;
    prefixes.add(pathname.replace(/^\//, '').split('/')[0]);
  }
  return [...prefixes];
};

describe('06-frontend-deploy.yml cannot delete the published installers', () => {
  it('declares the bucket the download page is served from', () => {
    expect(BUCKET, 'S3_BUCKET is not declared in the workflow').toBe('vyomquant-frontend');
  });

  it('finds the sync passes, so a parsing failure cannot pass this file vacuously', () => {
    const cmds = syncCommands(workflow);
    expect(cmds.length, 'no `aws s3 sync` command was parsed out of the workflow')
      .toBeGreaterThanOrEqual(3);
    expect(
      cmds.filter((c) => /--delete\b/.test(c)).length,
      'no `--delete` pass was parsed, so the assertions below would check nothing',
    ).toBeGreaterThanOrEqual(3);
  });

  it('protects every prefix the download surface advertises same-origin', () => {
    const prefixes = advertisedPrefixes();
    // When the artifacts are hosted off-origin this list is empty and there is nothing in
    // the bucket to protect; the assertion below is then trivially satisfied, which is
    // correct rather than vacuous. The size is reported so the state is visible either way.
    expect(Array.isArray(prefixes)).toBe(true);

    for (const prefix of prefixes) {
      const key = `${prefix}/desktop-v0.0.0/Installer.exe`;
      for (const cmd of syncCommands(workflow)) {
        expect(
          wouldDelete(cmd, key),
          `this pass would delete ${key} on every deploy:\n  ${cmd}`,
        ).toBe(false);
      }
    }
  });

  it('protects the downloads/ prefix specifically, whatever the surface currently says', () => {
    // Pinned independently of `desktopReleases.js` so that pointing the surface back at
    // GitHub does not quietly retire the protection and strand a later re-migration.
    const key = 'downloads/desktop-v0.1.2/VyomQuant_0.1.2_x64-setup.exe';
    for (const cmd of syncCommands(workflow)) {
      expect(wouldDelete(cmd, key), `this pass would delete ${key}:\n  ${cmd}`).toBe(false);
    }
  });

  it('still reports a pass that drops the exclusion as unsafe', () => {
    // Non-vacuity for the matcher itself. If `wouldDelete` were broken in the permissive
    // direction every assertion above would pass against a workflow that deletes everything.
    const unsafe =
      'aws s3 sync dist/ s3://vyomquant-frontend/ --delete '
      + '--cache-control "public, max-age=300, must-revalidate" '
      + '--exclude "assets/*" --exclude "*.html" --exclude "*.map"';
    expect(wouldDelete(unsafe, 'downloads/desktop-v0.1.2/VyomQuant_0.1.2_x64-setup.exe')).toBe(true);

    const safe = `${unsafe} --exclude "downloads/*"`;
    expect(wouldDelete(safe, 'downloads/desktop-v0.1.2/VyomQuant_0.1.2_x64-setup.exe')).toBe(false);

    // An allow-listed pass is safe without naming the prefix at all.
    const allowListed =
      'aws s3 sync dist/ s3://vyomquant-frontend/ --delete --exclude "*" --include "assets/*"';
    expect(wouldDelete(allowListed, 'downloads/x/y.exe')).toBe(false);
    expect(wouldDelete(allowListed, 'assets/index-abc.js')).toBe(true);
  });

  it('keeps deleting the things it is supposed to delete', () => {
    // The exclusion must not have been widened into "never delete anything": stale hashed
    // bundles and stale root files are still pruned, which is why --delete is there.
    const cmds = syncCommands(workflow);
    expect(
      cmds.some((c) => wouldDelete(c, 'assets/index-stale.js')),
      'no pass prunes a stale hashed bundle any more',
    ).toBe(true);
    expect(
      cmds.some((c) => wouldDelete(c, 'robots.txt')),
      'no pass prunes a stale unhashed root object any more',
    ).toBe(true);
  });
});
