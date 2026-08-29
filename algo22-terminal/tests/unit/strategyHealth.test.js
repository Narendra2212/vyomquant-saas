/**
 * Tests for `src/lib/strategyHealth.js` — the Strategies_Page health indicator
 * (trading-lifecycle-integration task 16.4, Requirements 1.8 and 1.9).
 *
 * What is under test
 * ------------------
 * * **Two sources, and only two.** The indicator is a function of
 *   `most_recent_deployment.status` and `most_recent_backtest.outcome`. A row carrying a
 *   `health` field, a `status` field, or anything else the page happens to have is answered
 *   identically to the same row without them — Requirement 1.8's "no other data source".
 * * **`undetermined` is reached, not defaulted.** A row with neither record reads
 *   `undetermined` (Requirement 1.9), and so does a row whose only readings are
 *   inconclusive; nothing resolves to a cheerful default.
 * * **The deployment record decides where it decides anything**, and the backtest outcome
 *   fills in where it does not.
 * * **Legacy spellings are the backend's.** `active`, `crashed`, `stopping` and the
 *   canonical uppercase names all normalise; an unrecognised spelling reads as unknown
 *   rather than as a guess.
 * * **The page no longer fabricates.** Read off `Strategies.jsx`, so the
 *   `row.health ?? "healthy"` default cannot come back unnoticed.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import {
  HEALTH_DEGRADED,
  HEALTH_ERROR,
  HEALTH_HEALTHY,
  HEALTH_SOURCE_BACKTEST,
  HEALTH_SOURCE_DEPLOYMENT,
  HEALTH_UNDETERMINED,
  STRATEGY_HEALTH_INDICATORS,
  backtestOutcomeOf,
  computeStrategyHealth,
  deploymentStatusOf,
  explainStrategyHealth,
} from '../../src/lib/strategyHealth.js';

/** A strategy row carrying only the two records health is allowed to read. */
const row = (deploymentStatus, backtestOutcome, extra = {}) => ({
  id: 'strat-1',
  name: 'Fixture',
  ...(deploymentStatus === undefined ? {} : { most_recent_deployment: { status: deploymentStatus } }),
  ...(backtestOutcome === undefined ? {} : { most_recent_backtest: { outcome: backtestOutcome } }),
  ...extra,
});

describe('strategyHealth — no records at all (Requirement 1.9)', () => {
  it('reads undetermined, with no deciding source, when neither record exists', () => {
    const explained = explainStrategyHealth(row(undefined, undefined));
    expect(explained.indicator).toBe(HEALTH_UNDETERMINED);
    expect(explained.source).toBeNull();
    expect(explained.deploymentStatus).toBeNull();
    expect(explained.backtestOutcome).toBeNull();
  });

  it('reads undetermined for a null, undefined or non-object row rather than throwing', () => {
    for (const input of [null, undefined, 'strategy', 42, []]) {
      expect(computeStrategyHealth(input)).toBe(HEALTH_UNDETERMINED);
    }
  });

  it('reads undetermined when the records are present but empty of any status', () => {
    expect(
      computeStrategyHealth({ most_recent_deployment: {}, most_recent_backtest: {} }),
    ).toBe(HEALTH_UNDETERMINED);
    expect(
      computeStrategyHealth({
        most_recent_deployment: { status: '   ' },
        most_recent_backtest: { outcome: null },
      }),
    ).toBe(HEALTH_UNDETERMINED);
  });
});

describe('strategyHealth — the backtest outcome alone', () => {
  it('completed reads healthy, failed reads error, and the backtest is named as the source', () => {
    expect(explainStrategyHealth(row(undefined, 'completed'))).toMatchObject({
      indicator: HEALTH_HEALTHY,
      source: HEALTH_SOURCE_BACKTEST,
    });
    expect(explainStrategyHealth(row(undefined, 'failed'))).toMatchObject({
      indicator: HEALTH_ERROR,
      source: HEALTH_SOURCE_BACKTEST,
    });
  });

  it('a backtest still running is inconclusive, not healthy', () => {
    expect(computeStrategyHealth(row(undefined, 'running'))).toBe(HEALTH_UNDETERMINED);
  });

  it('reads `status` as the same field `outcome` names, off the one record', () => {
    expect(computeStrategyHealth({ most_recent_backtest: { status: 'failed' } })).toBe(
      HEALTH_ERROR,
    );
    // `outcome` wins when both spellings are present and disagree.
    expect(
      computeStrategyHealth({
        most_recent_backtest: { outcome: 'completed', status: 'failed' },
      }),
    ).toBe(HEALTH_HEALTHY);
  });
});

describe('strategyHealth — the deployment record decides first', () => {
  it('failed reads error even when the most recent backtest completed', () => {
    expect(explainStrategyHealth(row('failed', 'completed'))).toMatchObject({
      indicator: HEALTH_ERROR,
      source: HEALTH_SOURCE_DEPLOYMENT,
    });
  });

  it('running reads healthy even when the most recent backtest failed', () => {
    expect(explainStrategyHealth(row('running', 'failed'))).toMatchObject({
      indicator: HEALTH_HEALTHY,
      source: HEALTH_SOURCE_DEPLOYMENT,
    });
  });

  it('paused reads degraded: the deployment exists and is not executing', () => {
    expect(computeStrategyHealth(row('paused', 'completed'))).toBe(HEALTH_DEGRADED);
  });

  it('stopped and deploying are inconclusive, so the backtest outcome is consulted', () => {
    expect(explainStrategyHealth(row('stopped', 'completed'))).toMatchObject({
      indicator: HEALTH_HEALTHY,
      source: HEALTH_SOURCE_BACKTEST,
    });
    expect(computeStrategyHealth(row('deploying', 'failed'))).toBe(HEALTH_ERROR);
    // ...and with nothing to fall back to, they stay undetermined.
    expect(computeStrategyHealth(row('stopped', undefined))).toBe(HEALTH_UNDETERMINED);
    expect(computeStrategyHealth(row('deploying', undefined))).toBe(HEALTH_UNDETERMINED);
  });
});

describe('strategyHealth — status spellings are the backend’s', () => {
  it('normalises the legacy lowercase spellings strategy_lifecycle.py already maps', () => {
    expect(computeStrategyHealth(row('active', undefined))).toBe(HEALTH_HEALTHY);
    expect(computeStrategyHealth(row('crashed', undefined))).toBe(HEALTH_ERROR);
    expect(computeStrategyHealth(row('error', undefined))).toBe(HEALTH_ERROR);
    expect(computeStrategyHealth(row('stopping', 'completed'))).toBe(HEALTH_HEALTHY);
  });

  it('accepts the canonical uppercase binding names and mixed case', () => {
    expect(computeStrategyHealth(row('RUNNING', undefined))).toBe(HEALTH_HEALTHY);
    expect(computeStrategyHealth(row('Paused', undefined))).toBe(HEALTH_DEGRADED);
    expect(computeStrategyHealth(row('FAILED', undefined))).toBe(HEALTH_ERROR);
  });

  it('reports an unreadable spelling as unknown rather than guessing a verdict', () => {
    const explained = explainStrategyHealth(row('vaporised', undefined));
    expect(explained.indicator).toBe(HEALTH_UNDETERMINED);
    expect(explained.deploymentState).toBeNull();
    // The raw reading is still reported verbatim, so the UI can say what it could not read.
    expect(explained.deploymentStatus).toBe('vaporised');
    expect(computeStrategyHealth(row('vaporised', 'completed'))).toBe(HEALTH_HEALTHY);
    expect(computeStrategyHealth({ most_recent_backtest: { outcome: 'partial' } })).toBe(
      HEALTH_UNDETERMINED,
    );
  });

  it('reads a numeric status as unreadable, not as a label', () => {
    expect(deploymentStatusOf({ most_recent_deployment: { status: 3 } })).toBeNull();
    expect(backtestOutcomeOf({ most_recent_backtest: { outcome: 0 } })).toBeNull();
  });
});

describe('strategyHealth — purity (Requirement 1.8)', () => {
  it('ignores every field other than the two records', () => {
    const bare = row('failed', undefined);
    const noisy = row('failed', undefined, {
      health: 'healthy',
      status: 'running',
      is_running: true,
      exchange_status: 'connected',
      pnl: 42,
    });
    expect(computeStrategyHealth(noisy)).toBe(computeStrategyHealth(bare));
    expect(computeStrategyHealth(noisy)).toBe(HEALTH_ERROR);
  });

  it('is repeatable and does not mutate its input', () => {
    const input = row('paused', 'completed');
    const before = JSON.stringify(input);
    const first = computeStrategyHealth(input);
    const second = computeStrategyHealth(input);
    expect(second).toBe(first);
    expect(JSON.stringify(input)).toBe(before);
  });

  it('only ever reports a value from the declared vocabulary', () => {
    const statuses = [undefined, 'running', 'paused', 'stopped', 'deploying', 'failed', 'nonsense'];
    const outcomes = [undefined, 'completed', 'failed', 'running', 'nonsense'];
    for (const status of statuses) {
      for (const outcome of outcomes) {
        const { indicator, source } = explainStrategyHealth(row(status, outcome));
        expect(STRATEGY_HEALTH_INDICATORS).toContain(indicator);
        // `source` is null exactly when nothing determined a value.
        expect(source === null).toBe(indicator === HEALTH_UNDETERMINED);
      }
    }
  });
});

describe('Strategies.jsx no longer fabricates a health value', () => {
  /**
   * Comments are stripped before matching, because the page's own comments *quote* the
   * defaulting expression they replaced in order to explain why it was wrong. Only
   * whole-line `//` comments go, so a `https://` inside a string is left alone.
   */
  const stripComments = (text) =>
    text
      .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^[ \t]*\/\/.*$/gm, '');

  const source = stripComments(
    readFileSync(path.resolve(__dirname, '../../src/pages/Strategies.jsx'), 'utf-8'),
  );

  it('computes health through the shared module', () => {
    expect(source).toMatch(/from ["']\.\.\/lib\/strategyHealth["']/);
    expect(source).toMatch(/health:\s*computeStrategyHealth\(row\)/);
  });

  it('reads no `health` field off the API row and defaults none in the render', () => {
    expect(source).not.toMatch(/row\.health/);
    expect(source).not.toMatch(/health\s*\|\|\s*["']healthy["']/);
    expect(source).not.toMatch(/health\s*\?\?\s*["']healthy["']/);
  });
});
