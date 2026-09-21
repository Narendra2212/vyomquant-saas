/**
 * `design/pageFields` — vyomquant-ui-redesign task 13.3. design.md §7, §10.1, §18.
 * Requirements 14.5, 19.2, 19.3.
 *
 * These are the structural assertions over the declaration itself: that every entry is
 * complete, that every reachable not-available marker carries a reason, that the six BC
 * fields point at paths the API modules really document, and that no entry reads BC-1's
 * deprecated predecessor.
 *
 * The data-honesty INTEGRATION tests — a failed read renders no figure and no zero, a
 * `degraded` marker renders an error rather than an empty state — are task 13.4's, and
 * Property 5 is task 13.4's too. Nothing here renders anything.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import {
  ABSENCE,
  BACKEND_CHANGE_FIELDS,
  DEPRECATED_PATHS,
  DERIVED_FIELDS,
  LAST_SIGNAL_MIGRATION,
  PAGES,
  PAGE_FIELDS,
  PAGE_FIELDS_BY_PAGE,
  PAGE_FIELD_BY_KEY,
  PENDING_MIGRATION_FIELDS,
  PERMANENT_ABSENCES,
  UNAVAILABLE_FIELDS,
  VERDICT,
  pageFieldKey,
} from '../../../src/design/pageFields';

/** `algo22-terminal/`, two levels above `tests/unit/design/`. */
const FRONTEND_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
  '..',
);

const readSource = (relativePath) =>
  fs.readFileSync(path.join(FRONTEND_ROOT, relativePath), 'utf8');

const hasText = (value) => typeof value === 'string' && value.trim() !== '';

/** The leaf of a dotted path: `risk.current_drawdown_pct_v2` → `current_drawdown_pct_v2`. */
const leafOf = (dottedPath) => {
  const segments = dottedPath.split('.');
  const last = segments[segments.length - 1];
  // `timeline[].event=POSITION_UPDATED` → `POSITION_UPDATED`; `strategies[].name` → `name`.
  return last.includes('=') ? last.split('=')[1] : last.replace(/\[\]$/, '');
};

describe('pageFields: every entry is complete', () => {
  it('declares at least one entry per page in §7', () => {
    for (const page of Object.values(PAGES)) {
      expect(PAGE_FIELDS_BY_PAGE[page].length).toBeGreaterThan(0);
    }
  });

  it.each(PAGE_FIELDS.map((f) => [pageFieldKey(f), f]))(
    '%s carries a page, field, label, requirement and verdict',
    (_key, field) => {
      expect(Object.values(PAGES)).toContain(field.page);
      expect(hasText(field.field)).toBe(true);
      expect(hasText(field.label)).toBe(true);
      // The requirement that NAMES the field, e.g. '3.1' or '11.1'.
      expect(field.requirement).toMatch(/^\d+\.\d+$/);
      expect(Object.values(VERDICT)).toContain(field.verdict);
      expect(Object.values(ABSENCE)).toContain(field.absence);
      // Where it comes from is never left implicit, even for an unavailable field: the read
      // is what a future task widens.
      expect(hasText(field.read)).toBe(true);
      expect(hasText(field.endpoint)).toBe(true);
    },
  );

  it('keys every entry uniquely by page/field', () => {
    expect(Object.keys(PAGE_FIELD_BY_KEY)).toHaveLength(PAGE_FIELDS.length);
  });

  it('freezes the declaration and every entry, so a page cannot edit a verdict', () => {
    expect(Object.isFrozen(PAGE_FIELDS)).toBe(true);
    for (const field of PAGE_FIELDS) {
      expect(Object.isFrozen(field)).toBe(true);
      expect(Object.isFrozen(field.inputs)).toBe(true);
    }
  });
});

describe('pageFields: a path exists exactly when a figure is at one', () => {
  it('gives every available field a non-empty source path', () => {
    for (const field of PAGE_FIELDS.filter((f) => f.verdict === VERDICT.AVAILABLE)) {
      expect(hasText(field.path), pageFieldKey(field)).toBe(true);
    }
  });

  it('gives no derived or unavailable field a path, and every derived field its inputs', () => {
    for (const field of [...DERIVED_FIELDS, ...UNAVAILABLE_FIELDS]) {
      // A derived figure is computed and an unavailable one does not exist; neither is at a
      // path, and declaring one would invite a page to read it.
      expect(field.path, pageFieldKey(field)).toBeNull();
    }
    for (const field of DERIVED_FIELDS) {
      expect(field.inputs.length, pageFieldKey(field)).toBeGreaterThan(0);
      expect(hasText(field.derivation), pageFieldKey(field)).toBe(true);
    }
  });
});

describe('pageFields: Requirement 19.3 — every absence carries a reason', () => {
  it('gives every unavailable entry a non-empty reason', () => {
    expect(UNAVAILABLE_FIELDS.length).toBeGreaterThan(0);
    for (const field of UNAVAILABLE_FIELDS) {
      expect(hasText(field.reason), pageFieldKey(field)).toBe(true);
      // "Not available" alone tells a trader what a blank cell tells them.
      expect(field.reason.trim().length, pageFieldKey(field)).toBeGreaterThan(20);
    }
  });

  it('gives every entry whose marker is reachable a reason', () => {
    // `absence: 'never'` is the claim that the server always reports a value, so no marker
    // can render and no reason is needed. Every other kind needs one.
    for (const field of PAGE_FIELDS.filter((f) => f.absence !== ABSENCE.NEVER)) {
      expect(hasText(field.reason), pageFieldKey(field)).toBe(true);
    }
  });

  it('leaves no reason on an always-reported field, so none is dead copy', () => {
    for (const field of PAGE_FIELDS.filter((f) => f.absence === ABSENCE.NEVER)) {
      expect(field.reason, pageFieldKey(field)).toBeNull();
    }
  });
});

describe('pageFields: permanent absence vs the one pending migration', () => {
  it('names migration 015 on BC-3 and on nothing else', () => {
    expect(PENDING_MIGRATION_FIELDS.map(pageFieldKey)).toEqual(['strategies/lastSignalAt']);
    const [lastSignalAt] = PENDING_MIGRATION_FIELDS;
    expect(lastSignalAt.backendChange).toBe('BC-3');
    expect(lastSignalAt.pendingMigration).toBe(LAST_SIGNAL_MIGRATION);
    // It resolves without a code change, so it is NOT permanent — that is the distinction
    // the release note turns on.
    expect(PERMANENT_ABSENCES).not.toContain(lastSignalAt.absence);
  });

  it('declares a pendingMigration only with the pending-migration absence', () => {
    for (const field of PAGE_FIELDS) {
      if (field.absence === ABSENCE.PENDING_MIGRATION) {
        expect(hasText(field.pendingMigration), pageFieldKey(field)).toBe(true);
      } else {
        expect(field.pendingMigration, pageFieldKey(field)).toBeNull();
      }
    }
  });

  it('marks the four §7 permanent absences as permanent, with their reasons', () => {
    const permanent = [
      // Per-deployment realised P&L: the ❌ row with no registered change.
      'live-trading/realisedPnlPerDeployment',
      // Fields a server genuinely reports as null.
      'live-trading/liquidationDistance',
      'portfolio/positionLiquidationPrice',
      'dashboard/exchangeApiLatencyMs',
      // `dag_nodes` past the trace store's ~1h retention.
      'signal-trace/stage4Logic',
    ];

    for (const key of permanent) {
      const field = PAGE_FIELD_BY_KEY[key];
      expect(field, key).toBeDefined();
      expect(PERMANENT_ABSENCES, key).toContain(field.absence);
      expect(hasText(field.reason), key).toBe(true);
    }

    // The retention case says so, so an older trace does not read as broken.
    expect(PAGE_FIELD_BY_KEY['signal-trace/stage4Logic'].absence).toBe(ABSENCE.RETENTION);
    expect(PAGE_FIELD_BY_KEY['signal-trace/stage4Logic'].reason).toMatch(/hour|retain/i);
  });

  it('renders account-wide realised P&L under its own explicit label (§7.5)', () => {
    // The pair Requirement 14.5 turns on: the account figure IS shown, labelled as such,
    // and the per-deployment figure gets its own marker rather than borrowing it.
    const accountWide = PAGE_FIELD_BY_KEY['live-trading/realisedPnlAccount'];
    expect(accountWide.label).toBe('Realised P&L (account, today)');
    expect(accountWide.verdict).toBe(VERDICT.AVAILABLE);
    expect(accountWide.path).toBe('overview.today_realized_pnl');

    const perDeployment = PAGE_FIELD_BY_KEY['live-trading/realisedPnlPerDeployment'];
    expect(perDeployment.verdict).toBe(VERDICT.UNAVAILABLE);
    expect(perDeployment.absence).toBe(ABSENCE.UNREPORTED);
  });

  it('carries the tooltip copy for the two ⚠️ derivations §7 names', () => {
    const invested = PAGE_FIELD_BY_KEY['portfolio/investedCapital'];
    expect(invested.label).toBe('Invested (capital in use)');
    expect(invested.verdict).toBe(VERDICT.DERIVED);
    expect(invested.inputs).toContain('overview.used_balance');
    expect(hasText(invested.tooltip)).toBe(true);

    const alert = PAGE_FIELD_BY_KEY['dashboard/alertCondition'];
    expect(alert.requirement).toBe('3.3');
    expect(alert.verdict).toBe(VERDICT.DERIVED);
    expect(alert.inputs).toEqual([
      'exchange.exchanges[].status',
      'strategies.items[].status',
      'executions[].status',
    ]);
    expect(hasText(alert.tooltip)).toBe(true);
  });
});

describe('pageFields: the six BC fields point at real documented shapes', () => {
  const EXPECTED = {
    'BC-1': ['risk.current_drawdown_pct_v2'],
    'BC-2': ['degraded', 'risk.open_positions_count'],
    'BC-3': ['strategies[].last_signal_at'],
    'BC-4': ['strategies[].last_execution_at'],
    'BC-5': ['overview.realized_pnl'],
    // BC-6 lands a timeline EVENT, and its `resulting_position` stays declared not-available.
    'BC-6': ['timeline[].event=POSITION_UPDATED', null],
  };

  it('claims all six, and each is consumed by at least one entry', () => {
    for (const [change, paths] of Object.entries(EXPECTED)) {
      const fields = BACKEND_CHANGE_FIELDS[change];
      expect(fields.length, change).toBeGreaterThan(0);
      // Deduped: BC-1's field is read by two pages, and both must read the same path.
      expect([...new Set(fields.map((f) => f.path))].sort(), change).toEqual(
        [...new Set(paths)].sort(),
      );
    }
  });

  it.each([
    ['BC-1', 'src/api/modules/dashboard.js'],
    ['BC-2', 'src/api/modules/dashboard.js'],
    ['BC-3', 'src/api/modules/strategies.js'],
    ['BC-4', 'src/api/modules/strategies.js'],
    ['BC-5', 'src/api/modules/dashboard.js'],
  ])('%s reads a field the %s JSDoc documents', (change, module) => {
    const source = readSource(module);
    for (const field of BACKEND_CHANGE_FIELDS[change]) {
      expect(field.documentedIn, pageFieldKey(field)).toBe(module);
      expect(source, `${pageFieldKey(field)} → ${field.path}`).toContain(leafOf(field.path));
    }
  });

  it('records BC-6 against no frontend module, because none documents that response', () => {
    // `SignalTrace.jsx` calls `get()` directly and there is no signal-trace API module, so a
    // `documentedIn` here would be a claim this test could not check. Task 21.1 owns the
    // page. The exception is declared rather than left implicit: exactly these entries.
    const undocumented = PAGE_FIELDS.filter((f) => f.backendChange && f.documentedIn === null);
    expect(undocumented.map(pageFieldKey)).toEqual([
      'signal-trace/stage9PositionUpdate',
      'signal-trace/resultingPosition',
    ]);
    for (const field of undocumented) {
      expect(field.backendChange).toBe('BC-6');
      expect(hasText(field.note), pageFieldKey(field)).toBe(true);
    }
  });

  it('carries the server-supplied reason for BC-6\'s resulting position', () => {
    const resulting = PAGE_FIELD_BY_KEY['signal-trace/resultingPosition'];
    expect(resulting.verdict).toBe(VERDICT.UNAVAILABLE);
    expect(resulting.inputs).toContain('timeline[].data.not_available_reason');
    // The fallback copy must be the server's sentence, not a second account of it.
    expect(resulting.reason).toMatch(/position change only/i);
    expect(resulting.note).toMatch(/POSITION_RESULTING_UNREPORTED_REASON/);
  });

  it('names a real file wherever it claims a documented shape', () => {
    // Only that the claim is checkable. Asserting every leaf of every entry appears in its
    // module would be a demand to widen five API modules' JSDoc, which is not this task —
    // the six BC paths are, and they are checked above.
    for (const field of PAGE_FIELDS) {
      if (!field.documentedIn) continue;
      expect(
        fs.existsSync(path.join(FRONTEND_ROOT, field.documentedIn)),
        `${pageFieldKey(field)} → ${field.documentedIn}`,
      ).toBe(true);
    }
  });
});

describe('pageFields: no entry reads a deprecated path', () => {
  it('reads current_drawdown_pct_v2 and never current_drawdown_pct', () => {
    const drawdowns = PAGE_FIELDS.filter((f) => f.field === 'currentDrawdown');
    expect(drawdowns).toHaveLength(2); // dashboard tier 1 and portfolio tier 1
    for (const field of drawdowns) {
      expect(field.path, pageFieldKey(field)).toBe('risk.current_drawdown_pct_v2');
      expect(field.backendChange).toBe('BC-1');
    }
  });

  it('names no deprecated path in any path or input, anywhere', () => {
    // `risk.current_drawdown_pct` publishes `today_return_pct`, so a profitable day would
    // render as a positive drawdown. It is still on the wire for its deprecation window,
    // which is why this is asserted rather than assumed.
    for (const field of PAGE_FIELDS) {
      for (const deprecated of DEPRECATED_PATHS) {
        expect([field.path, ...field.inputs], pageFieldKey(field)).not.toContain(deprecated);
      }
    }
  });
});
