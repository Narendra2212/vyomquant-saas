/**
 * `ds/RiskIndicator` — vyomquant-ui-redesign task 6.6. Requirements 1.2, 1.4, 1.5.
 *
 * The load-bearing case is precedence: `RiskMeter` graded risk client-side on 70/90
 * thresholds while `routers/risk.py` grades it on 60/85, so the same account could read
 * SAFE in a panel and WARNING in the engine about to refuse its order.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import {
  DEFAULT_RISK_THRESHOLDS,
  RISK_LEVELS,
  RiskIndicator,
  deriveRiskLevel,
  normaliseRiskLevel,
} from '../../../src/components/ds/RiskIndicator';

const root = () => document.querySelector('[data-risk-level]');

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('RiskIndicator: the server decides when the server has spoken', () => {
  it('uses the reported level even where a derivation would disagree', () => {
    // 10% utilisation derives SAFE; the server said BLOCKED because the kill switch is on.
    render(<RiskIndicator level="BLOCKED" utilizationPct={10} />);
    expect(root().getAttribute('data-risk-level')).toBe('BLOCKED');
    expect(root().getAttribute('data-risk-source')).toBe('server');
    expect(screen.getByText('Trading blocked')).toBeTruthy();
  });

  it('derives only when no level was reported', () => {
    render(<RiskIndicator utilizationPct={92} />);
    expect(root().getAttribute('data-risk-level')).toBe('CRITICAL');
    expect(root().getAttribute('data-risk-source')).toBe('derived');
  });

  it('derives on risk.py\'s own thresholds, not RiskMeter\'s 70/90', () => {
    expect(DEFAULT_RISK_THRESHOLDS).toEqual({ warn: 0.6, critical: 0.85 });
    // 65% is WARNING under the server's 60, and was SAFE under RiskMeter's 70.
    expect(deriveRiskLevel(65)).toBe('WARNING');
    expect(deriveRiskLevel(59.9)).toBe('SAFE');
    expect(deriveRiskLevel(60)).toBe('WARNING');
    expect(deriveRiskLevel(85)).toBe('CRITICAL');
    expect(deriveRiskLevel(100)).toBe('BLOCKED');
    expect(deriveRiskLevel(140)).toBe('BLOCKED');
    expect(deriveRiskLevel(null)).toBeNull();
    expect(deriveRiskLevel(Number.NaN)).toBeNull();
  });

  it('reads both backend vocabularies onto the four risk.py names', () => {
    expect(RISK_LEVELS).toEqual(['SAFE', 'WARNING', 'CRITICAL', 'BLOCKED']);
    // routers/risk.py's four.
    expect(normaliseRiskLevel('SAFE')).toBe('SAFE');
    expect(normaliseRiskLevel('WARNING')).toBe('WARNING');
    expect(normaliseRiskLevel('CRITICAL')).toBe('CRITICAL');
    expect(normaliseRiskLevel('BLOCKED')).toBe('BLOCKED');
    // dashboard_aggregation_service.py's five, lowercase, which is what the dashboard reads.
    expect(normaliseRiskLevel('low')).toBe('SAFE');
    expect(normaliseRiskLevel('medium')).toBe('WARNING');
    expect(normaliseRiskLevel('high')).toBe('CRITICAL');
    expect(normaliseRiskLevel('blocked')).toBe('BLOCKED');
    // `critical` is not promoted to BLOCKED: only the server may say trading is halted.
    expect(normaliseRiskLevel('critical')).toBe('CRITICAL');
  });

  it('does not substitute a derived level for one it could not read', () => {
    render(<RiskIndicator level="ELEVATED-ISH" utilizationPct={95} />);
    expect(normaliseRiskLevel('ELEVATED-ISH')).toBeNull();
    expect(root().getAttribute('data-risk-level')).toBe('unreported');
    expect(root().getAttribute('data-risk-source')).toBe('unreported');
    // The measured figure is still shown — it is a fact — but no verdict is invented.
    expect(root().textContent).toContain('95.0%');
    expect(screen.getByRole('img', { name: /^Risk level: not available/ })).toBeTruthy();
  });
});

describe('RiskIndicator: an unmeasured utilisation is not 0%', () => {
  it('renders the not-available marker instead of an empty bar', () => {
    render(<RiskIndicator level="SAFE" utilizationPct={null} />);
    expect(root().textContent).not.toContain('0.0%');
    expect(screen.getByRole('img', { name: /^Limit used: not available/ })).toBeTruthy();
    expect(screen.queryByRole('progressbar')).toBeNull();
  });

  it('renders the bar with the measured value once there is one', () => {
    render(<RiskIndicator level="WARNING" utilizationPct={62.5} limitLabel="$500.00 daily loss" />);
    const bar = screen.getByRole('progressbar', { name: 'Utilisation of $500.00 daily loss' });
    expect(bar.getAttribute('aria-valuenow')).toBe('63');
    expect(bar.getAttribute('aria-valuemin')).toBe('0');
    expect(bar.getAttribute('aria-valuemax')).toBe('100');
    expect(root().textContent).toContain('62.5%');
  });

  it('clamps a breach to the track without changing the reported level', () => {
    render(<RiskIndicator level="BLOCKED" utilizationPct={140} />);
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('100');
    expect(root().getAttribute('data-risk-level')).toBe('BLOCKED');
  });

  it('throws when given neither a level nor a utilisation', () => {
    expect(() => render(<RiskIndicator />)).toThrow(/neither `level` nor `utilizationPct`/);
  });
});

describe('RiskIndicator: no glow, no colour prop, words not hues', () => {
  it('names every level in text as well as colour', () => {
    for (const level of RISK_LEVELS) {
      render(<RiskIndicator level={level} utilizationPct={50} />);
      expect(root().textContent.replace(/\s+/g, ' ').trim().length).toBeGreaterThan(0);
      expect(document.querySelector('[data-status-group]').textContent.trim()).not.toBe('');
      cleanup();
    }
  });

  it('distinguishes CRITICAL from BLOCKED, which share the error hue', () => {
    const { container: critical } = render(<RiskIndicator level="CRITICAL" utilizationPct={90} />);
    const criticalText = critical.textContent;
    cleanup();
    const { container: blocked } = render(<RiskIndicator level="BLOCKED" utilizationPct={90} />);
    expect(blocked.textContent).not.toBe(criticalText);
    expect(blocked.textContent).toContain('Trading blocked');
  });

  it('carries no box-shadow anywhere — RiskMeter glowed hardest when the number mattered most', () => {
    render(<RiskIndicator level="CRITICAL" utilizationPct={95} currentLabel="$475.00" limitLabel="$500.00" />);
    expect(root().innerHTML).not.toContain('box-shadow');
    expect(root().innerHTML).not.toContain('animate');
    expect(document.querySelectorAll('style').length).toBe(0);
    // The formatted end labels the caller supplied still render.
    expect(root().textContent).toContain('$475.00');
    expect(root().textContent).toContain('$500.00');
  });
});
