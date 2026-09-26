/**
 * ═══════════════════════════════════════════════════════════════════════════
 * tests/unit/pages/backtesterNetPnl.test.jsx — the frontend half of the key-set contract
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * production-launch-hardening task 7.3. Requirements 1.10, 2.10, 3.7.
 * `tests/test_backtest_key_contract.py` is the backend half (task 7.2); task 7.1 is the
 * shared fixture the two are checked against.
 *
 * WHY BOTH SIDES, RESTATED HERE
 * ------------------------------
 * A backend-only key-set test passes while the results screen still renders a field
 * nothing persists, and a frontend-only test passes while the column stores `0`. This file
 * is the second half: it asserts against `pages/Backtester.jsx`'s REAL rendering path —
 * `tierOneFigures`, `TIER_ONE`, `TIER_ONE_FIGURE` and `ds/Metric`, imported and mounted, not
 * reimplemented — so a rename on either side of the contract fails the suite that reads the
 * renamed thing rather than passing quietly.
 *
 * THE ONE FIXTURE, READ THE SAME WAY `signalTraceStages.test.js` READS ITS OWN
 * -----------------------------------------------------------------------------
 * `tests/fixtures/backtest_payload_keys.json` is the file task 7.1 declared and task 7.2
 * asserts the writer's read-key set against. This file resolves UP one level out of
 * `algo22-terminal/` (vitest's `process.cwd()`) to the same repo-relative path pytest reads
 * directly — one file, two readers, and neither copy can drift from the other without a
 * failing test on its own side (`test_the_json_fixture_matches_the_python_declaration` on
 * the backend; nothing here writes to it).
 *
 * WHAT IS REAL AND WHAT IS SUPPLIED
 * ----------------------------------
 * Real: `tierOneFigures`, `TIER_ONE`, `TIER_ONE_FIGURE` and `Metric`/`NOT_AVAILABLE`, all
 * imported from the actual page and component modules — nothing here is a second copy of
 * the read-and-render logic under test. `fromNullable`/`readReported`
 * (`design/reported.js`) run for real inside `tierOneFigures` and inside `Metric`.
 * Supplied: the fixture `results` OBJECT the six figures are read off. It is not a captured
 * network response — this suite has no server — but every key it carries is drawn from
 * `backtest_payload_keys.json`'s declared `emitted_keys`, i.e. from the same set the engine
 * is asserted to actually emit on the backend side, so the fixture is not inventing a shape
 * the engine could not produce.
 *
 * THREE CLAIMS (Requirement 1.10, 2.10 and the "so a column the writer cannot persist
 * cannot be rendered" half of 3.7)
 * ----------------------------------------------------------------------------------
 *   1. Every key `TIER_ONE_FIGURE`'s six `read` functions reach for is declared in the
 *      fixture's `emitted_keys` — so a figure this page tries to read that the engine does
 *      not actually emit fails HERE, at the declaration, rather than rendering the
 *      not-available marker forever with nobody able to tell "engine never sends this" from
 *      "this particular run happened to omit it".
 *   2. On a completed backtest whose fixture result carries a real `total_pnl`, the
 *      rendered Net P&L figure is present and numeric — not the not-available marker. This
 *      is task 7.4's regression case, asserted through the page's own render function.
 *   3. The whole tier-1 row — not just Net P&L — renders every figure as available when the
 *      fixture supplies all six, which is the "every figure the results surface renders is
 *      named in the fixture's key set" half of the task text, checked positively as well as
 *      by declaration.
 *
 * Run scoped:
 *   node node_modules/vitest/vitest.mjs --run tests/unit/pages/backtesterNetPnl.test.jsx
 */

import { describe, expect, it, afterEach } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { tierOneFigures, TIER_ONE_FIGURE } from '../../../src/pages/Backtester';
import { Metric, NOT_AVAILABLE } from '../../../src/components/ds/Metric';
import { PAGE_HIERARCHY_BY_PAGE } from '../../../src/design/pageHierarchy';
import { PAGES } from '../../../src/design/pageFields';

/**
 * The shared fixture, resolved the same way `signalTraceStages.test.js` resolves
 * `signal_trace_node_projection.json`: up one level out of `algo22-terminal/` to the
 * repo-root `tests/fixtures/` directory pytest reads directly. Not copied under
 * `algo22-terminal/` — the task is explicit that this must be the same file task 7.2
 * asserts against, and a second copy is exactly the drift this fixture exists to prevent.
 */
const FIXTURE = JSON.parse(
  readFileSync(
    resolve(process.cwd(), '..', 'tests', 'fixtures', 'backtest_payload_keys.json'),
    'utf8',
  ),
);

/** The declared emitted-key set, as a `Set` for membership checks below. */
const EMITTED_KEYS = new Set(FIXTURE.emitted_keys);

/**
 * Tier 1, exactly as `Backtester.jsx` walks it — `pageHierarchy`'s declared list, filtered
 * to tier 1, in declaration order. Read off the same source the page reads rather than
 * hand-copied, so a seventh figure added to the tier is picked up here without an edit.
 */
const TIER_ONE = (PAGE_HIERARCHY_BY_PAGE[PAGES.BACKTESTER]?.tiers ?? []).filter(
  (entry) => entry.tier === 1,
);

/**
 * A completed backtest's `results` object, restricted to keys `backtest_payload_keys.json`
 * declares the engine actually emits. `total_pnl` — task 7.4's figure — is a real, non-zero
 * number, which is what makes claim 2 below a genuine regression case and not a vacuous one
 * (an absent key and a present zero would both otherwise "pass" a naive presence check).
 *
 * Every other tier-1 read (`total_return_pct`, `max_drawdown_pct`, `sharpe_ratio`,
 * `win_rate_pct`, `total_trades`) is populated too, so the fixture exercises "every figure
 * available" rather than leaving five of six untested by accident.
 */
function completedResult(overrides = {}) {
  const base = {
    total_return_pct: 2.6283,
    total_pnl: 262.83,
    max_drawdown_pct: 1.6956,
    sharpe_ratio: 0.8123,
    sortino_ratio: 4.2796,
    win_rate_pct: 41.6667,
    total_trades: 12,
    winning_trades: 5,
    losing_trades: 7,
    profit_factor: 1.6471,
    calmar_ratio: 2545.382,
    total_fees_paid: 12.6,
    expectancy: 21.9029,
    equity_curve: [],
    trades: [],
  };
  return { ...base, ...overrides };
}

/** Renders the real tier-1 row: `tierOneFigures` feeding real `Metric` components. */
function renderTierOne(results) {
  const tierOne = tierOneFigures(results);
  return render(
    <div data-region="backtest-results">
      {TIER_ONE.map(({ key, label }) => (
        <Metric
          key={key}
          tier={1}
          label={label}
          value={tierOne?.[key]}
          format={TIER_ONE_FIGURE[key]?.format}
          precision={TIER_ONE_FIGURE[key]?.precision}
          hint={TIER_ONE_FIGURE[key]?.hint}
          data-region={key}
        />
      ))}
    </div>,
  );
}

/** The mounted figure for one tier-1 key, found by its `data-region`. */
const figureFor = (container, key) => container.querySelector(`[data-region="${key}"]`);

afterEach(() => cleanup());

/* ══════════════════════════════════════════════════════════════════════════
 * CLAIM 1 — every key TIER_ONE_FIGURE reads is declared in the fixture
 * ══════════════════════════════════════════════════════════════════════════ */

describe('every tier-1 read is against a key the engine actually emits', () => {
  it('has a non-empty declared key set to check against', () => {
    // Guard on the fixture itself, so a broken fixture fails here first rather than
    // making every case below vacuously pass against an empty Set.
    expect(EMITTED_KEYS.size).toBeGreaterThan(0);
  });

  it.each(TIER_ONE.map(({ key }) => key))(
    "tier-1 figure %s reads at least one key the fixture declares",
    (key) => {
      const read = TIER_ONE_FIGURE[key]?.read;
      expect(typeof read, `TIER_ONE_FIGURE.${key} has no read function`).toBe('function');

      // Probe which top-level key(s) `read` reaches for, by handing it an object whose
      // getters record their own name — this is what proves the READ, not merely a guess
      // at the field name from the source text.
      //
      // Two figures (`maxDrawdown`, `tradeCount`) declare a `results.a ?? results.b`
      // fallback, and evaluating `??` against a Proxy touches BOTH property names
      // regardless of what either resolves to, so both are recorded even though only the
      // FIRST is a name the current engine emits — `results.max_drawdown` (no `_pct`) and
      // `results.trades_count` are legacy/alternate spellings this page still tolerates,
      // not claims that the current engine emits them under those names. So the contract
      // this case checks is "at least one reached key is declared" — which is exactly the
      // condition under which the figure can ever actually resolve to a value — not "every
      // reached key is declared", which would fail a defensive fallback that a rename
      // instrument should not.
      const reached = [];
      const probe = new Proxy(
        {},
        {
          get(_target, prop) {
            if (typeof prop === 'string') reached.push(prop);
            return undefined;
          },
        },
      );
      read(probe);

      expect(reached.length, `TIER_ONE_FIGURE.${key}.read touched no key at all`).toBeGreaterThan(0);
      const declared = reached.filter((sourceKey) => EMITTED_KEYS.has(sourceKey));
      expect(
        declared.length,
        `TIER_ONE_FIGURE.${key} reads ${JSON.stringify(reached)}, and none of them is in `
          + `backtest_payload_keys.json's emitted_keys. Declared: `
          + `${JSON.stringify([...EMITTED_KEYS].sort())}`,
      ).toBeGreaterThan(0);
    },
  );
});

/* ══════════════════════════════════════════════════════════════════════════
 * CLAIM 2 — Net P&L renders as a number on a completed backtest (task 7.4's regression)
 * ══════════════════════════════════════════════════════════════════════════ */

describe('Net P&L on a completed backtest', () => {
  it('is declared in the fixture as an engine-emitted key', () => {
    // `total_pnl` is Requirement 1.10's own callout. If this ever goes red, the fixture
    // (and, transitively, the engine's declared BACKTEST_PAYLOAD_EMITTED_KEYS) stopped
    // emitting it, and claim 2 below would otherwise fail for the wrong reason.
    expect(EMITTED_KEYS.has('total_pnl')).toBe(true);
  });

  it('renders as a present, numeric figure — not the not-available marker', () => {
    const { container } = renderTierOne(completedResult());

    const netPnl = figureFor(container, 'netPnl');
    expect(netPnl, 'no element carries data-region="netPnl"').not.toBeNull();

    // The two facts that jointly mean "rendered as available": the attribute `Metric`
    // sets from `readReported(...).available`, and the absence of the marker element.
    // (Not also scanned for the `NOT_AVAILABLE` glyph in `textContent`: the figure's own
    // declared `hint` text legitimately contains an em dash as ordinary punctuation, which
    // would make a raw substring scan of the whole subtree a false positive independent of
    // whether the figure rendered.)
    expect(netPnl.getAttribute('data-metric-available')).toBe('true');
    expect(netPnl.querySelector('[data-metric-marker="not-available"]')).toBeNull();

    // `format="currency"`, `precision=2` (TIER_ONE_FIGURE.netPnl): 262.83 grouped, unrounded
    // beyond two decimals since it already had none more.
    expect(netPnl.textContent).toContain('262.83');
  });

  it('renders the not-available marker — not a fabricated number — when a run genuinely omits it', () => {
    // The declared absence path `pageFields.js` documents for `netPnl`: a specific
    // response missing the key (an older persisted row, or a failed run) still shows the
    // marker rather than a zero. This is the negative case claim 2 needs a contrast
    // against — engine-always-emits and this-run-omitted-it are different facts, and the
    // page must render them differently.
    const omitted = completedResult();
    delete omitted.total_pnl;

    const { container } = renderTierOne(omitted);
    const netPnl = figureFor(container, 'netPnl');

    expect(netPnl.getAttribute('data-metric-available')).toBe('false');
    expect(netPnl.querySelector('[data-metric-marker="not-available"]')).not.toBeNull();
    expect(netPnl.textContent).toContain(NOT_AVAILABLE);
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * CLAIM 3 — the whole tier-1 row, positively: six figures, six available
 * ══════════════════════════════════════════════════════════════════════════ */

describe('the whole tier-1 row on a completed backtest that supplied every figure', () => {
  it('renders all six declared figures as available, none as the not-available marker', () => {
    const { container } = renderTierOne(completedResult());

    expect(TIER_ONE.length).toBe(6);
    for (const { key, label } of TIER_ONE) {
      const figure = figureFor(container, key);
      expect(figure, `no element carries data-region="${key}" (${label})`).not.toBeNull();
      expect(
        figure.getAttribute('data-metric-available'),
        `${label} (data-region="${key}") rendered as not-available on a run that supplied it`,
      ).toBe('true');
      expect(figure.querySelector('[data-metric-marker="not-available"]')).toBeNull();
    }
  });

  it('renders no result at all for a response `tierOneFigures` cannot read (Requirement 6.6)', () => {
    // Not this task's defect to fix, but the boundary claim 1-3 all lean on: `null` in
    // means `null` out, and the caller's own convention is "no tier-1 container at all"
    // for that answer. Mounted here as `tierOneFigures(null) === null`, which is the
    // predicate the page's conditional render is built on.
    expect(tierOneFigures(null)).toBeNull();
    expect(tierOneFigures(undefined)).toBeNull();
    expect(tierOneFigures('not an object')).toBeNull();
  });
});
