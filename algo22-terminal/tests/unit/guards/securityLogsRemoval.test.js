/**
 * ═══════════════════════════════════════════════════════════════════════════
 * securityLogsRemoval.test.js — SECLOG-REMOVED-01
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * The user-facing Security log page is withdrawn from the product (2026-11-15). This guard
 * holds the removal complete on the frontend AND holds the line that the removal stops at.
 *
 * WHAT WAS REMOVED
 * ---------------------------------------------------------------------------
 *   src/pages/SecurityLogs.jsx                      the page
 *   src/App.jsx                                     the lazy import and the route
 *   src/components/shell/navigation.js              SECONDARY_ROUTE_TITLES entry
 *   src/components/shell/AccountMenu.jsx            the Account-group row and its icon
 *   src/pages/Profile.jsx                           the *Review All Logs* command
 *   src/pages/Wizard.jsx                            a *Manage* link that pointed at it
 *
 * No redirect was added. `App.jsx`'s `/app/*` catch-all already sends unknown `/app` paths to
 * the dashboard, so an old bookmark lands somewhere real; a dedicated `<Navigate>` would only
 * duplicate that, and this file asserts the page is unreachable rather than asserting how.
 *
 * WHAT WAS NOT REMOVED, AND MUST NOT BE
 * ---------------------------------------------------------------------------
 * Removing a page does not authorise deleting the records behind it. The backend
 * security-event, authentication and operational-audit writes are untouched and are needed
 * for incident investigation, and `api.user.getSecurityLogs` is still both served and USED —
 * Profile's *Recent access* panel reads it.
 *
 * That is the half a later "finish the cleanup" pass is most likely to get wrong, so it is
 * asserted here as positively as the removal is. A guard that only checked for absence would
 * be satisfied by deleting the account's access reporting too.
 *
 * The Python backend is out of this suite's reach (vitest, jsdom), so `routers/user.py` is
 * not asserted here. `api/modules/user.js` keeping `getSecurityLogs` is the frontend-visible
 * proxy for it: the client cannot still call an endpoint that has been deleted.
 */

import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import { SRC, collect, relToSrc, isTestFile, stripComments } from './source-scan.js';

/**
 * Comments stripped, string literals kept.
 *
 * Both halves matter. Several touched files deliberately explain in prose WHY the page is
 * gone and name it while doing so, so an un-stripped scan would report the very references it
 * is checking for. And route paths are string literals, so `codeOnly()` — which masks string
 * spans — would hide `'/app/security-logs'` and pass vacuously.
 */
const code = (abs) => stripComments(fs.readFileSync(abs, 'utf8'));

/** Every non-test source file under `src/`. */
const SOURCES = collect(SRC, ['.js', '.jsx'])
  .filter((abs) => !isTestFile(relToSrc(abs)))
  .map((abs) => ({ relative: relToSrc(abs), source: code(abs) }));

describe('SECLOG-REMOVED-01: the page is gone', () => {
  it('src/pages/SecurityLogs.jsx does not exist', () => {
    expect(fs.existsSync(path.join(SRC, 'pages/SecurityLogs.jsx'))).toBe(false);
  });

  /**
   * An import of, or a JSX usage of, the deleted module — and nothing else.
   *
   * Deliberately NOT a bare `/SecurityLogs/`. That was the first version of this check and it
   * failed, correctly reporting files this removal must not touch:
   *
   *   `getSecurityLogs`  the API method, still served and still called by Profile. The
   *                      substring is inside a longer identifier, and matching it would make
   *                      this guard demand the deletion of the logging the removal preserves.
   *
   *   prose              a dozen comments across `pageFields.js`, `Profile.jsx`,
   *                      `Billing.jsx`, `Backtester.jsx`, `ExchangeManager.jsx` and
   *                      `Wizard.jsx` cite `pages/SecurityLogs.jsx` as the precedent for a
   *                      technique (CSV export, a guarded table row, a substituted field).
   *                      That is history, and this repository keeps such history on purpose —
   *                      the same call `a11y-ratchet` and the colour budget make. Comments are
   *                      stripped before matching, but the point stands for any that are not.
   *
   * What actually breaks a build is a module specifier or a component reference, so those are
   * what this matches.
   */
  const IMPORTS_DELETED_MODULE = [
    /from\s+['"][^'"]*pages\/SecurityLogs['"]/,
    /import\(\s*['"][^'"]*pages\/SecurityLogs['"]\s*\)/,
    /<SecurityLogs[\s/>]/,
  ];

  it('nothing imports it', () => {
    const importers = SOURCES.filter(({ source }) =>
      IMPORTS_DELETED_MODULE.some((pattern) => pattern.test(source)),
    ).map(({ relative }) => relative);

    expect(
      importers,
      `A deleted module cannot be imported — these would fail at build:\n${importers.join('\n')}`,
    ).toEqual([]);
  });

  it('no source navigates to /app/security-logs', () => {
    const offenders = SOURCES.filter(({ source }) => source.includes('/app/security-logs')).map(
      ({ relative }) => relative,
    );
    expect(
      offenders,
      'the route no longer exists, so any link to it now falls through the /app/* catch-all '
        + `to the dashboard — a worse answer than not offering the control:\n${offenders.join('\n')}`,
    ).toEqual([]);
  });

  it('the shell declares no title for it', async () => {
    const { routeTitle, SECONDARY_ROUTE_TITLES } = await import(
      '../../../src/components/shell/navigation.js'
    );
    expect(Object.keys(SECONDARY_ROUTE_TITLES)).not.toContain('/app/security-logs');
    expect(routeTitle('/app/security-logs')).toBeNull();
  });
});

describe('SECLOG-REMOVED-01: the logging behind it is untouched', () => {
  /*
    The removal's boundary. Each of these would be a data-loss or incident-response
    regression, not a tidy-up, and each is the kind of thing a later pass removes "for
    consistency" after seeing the page go.
  */
  it('the client still exposes getSecurityLogs', () => {
    const api = code(path.join(SRC, 'api/modules/user.js'));
    expect(
      api,
      'the endpoint is still served and Profile still calls it; removing the client method '
        + 'would break the account\'s own recent-access reporting',
    ).toMatch(/getSecurityLogs/);
  });

  it('Profile still reads this account access events', () => {
    const profile = code(path.join(SRC, 'pages/Profile.jsx'));
    expect(profile).toMatch(/api\.user\.getSecurityLogs/);
    expect(
      profile,
      'the *Recent access* panel is what remains of this data in the product — the full-page '
        + 'view went, the panel did not',
    ).toMatch(/Recent access/);
  });

  it('Profile offers no Review All Logs command', () => {
    expect(
      code(path.join(SRC, 'pages/Profile.jsx')),
      'it navigated to the withdrawn route',
    ).not.toMatch(/Review All Logs/);
  });
});
