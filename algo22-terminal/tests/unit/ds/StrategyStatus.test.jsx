/**
 * `ds/StrategyStatus` — vyomquant-ui-redesign task 6.6. Requirements 1.2, 1.4, 1.5.
 *
 * The load-bearing case is `undetermined`. `Strategies.jsx` used to write
 * `health: row.health ?? "healthy"` against a list endpoint that reports no `health`
 * field, so every strategy read healthy — including one whose deployment had failed.
 * `lib/strategyHealth.js` fixed that; these tests exist so this component cannot undo it.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import {
  StrategyStatus,
  STRATEGY_STATUSES,
  normaliseStrategyStatus,
  resolveStrategyHealth,
} from '../../../src/components/ds/StrategyStatus';

const root = () => document.querySelector('[data-strategy-status]');

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('StrategyStatus: health is undetermined, not healthy', () => {
  it('reports undetermined for a row carrying neither a deployment nor a backtest', () => {
    // The list endpoint's real shape today: no health field, no records.
    render(<StrategyStatus status="running" health={{ id: 7, name: 'RSI Reversion' }} />);
    expect(root().getAttribute('data-strategy-health')).toBe('undetermined');
    expect(screen.queryByText('Healthy')).toBeNull();
    expect(screen.getByRole('img', { name: /^Health: not available/ })).toBeTruthy();
  });

  it('renders undetermined as the not-available marker, never as a green verdict', () => {
    render(<StrategyStatus status="running" health={undefined} />);
    const marker = screen.getByRole('img', { name: /^Health: not available/ });
    expect(marker.textContent.trim()).toBe('—');
    expect(marker.getAttribute('title')).toMatch(/not a report of good health/);
    expect(screen.queryByText('Healthy')).toBeNull();
  });

  it('does not launder a client-invented health value into healthy', () => {
    // Dashboard.jsx writes `health: s.health || "idle"` and, on a start action,
    // `health: newStatus === "active" ? "healthy" : "idle"`.
    render(<StrategyStatus status="running" health="idle" />);
    expect(root().getAttribute('data-strategy-health')).toBe('undetermined');
    expect(screen.queryByText('Healthy')).toBeNull();
  });

  it('reports error for a failed deployment rather than the old hardcoded healthy', () => {
    render(
      <StrategyStatus
        status="running"
        health={{ most_recent_deployment: { status: 'failed' } }}
      />,
    );
    expect(root().getAttribute('data-strategy-health')).toBe('error');
    expect(screen.getByText('Error')).toBeTruthy();
  });

  it('reports the three determinable verdicts from the two permitted records', () => {
    expect(resolveStrategyHealth({ most_recent_deployment: { status: 'running' } })).toBe('healthy');
    expect(resolveStrategyHealth({ most_recent_deployment: { status: 'paused' } })).toBe('degraded');
    expect(resolveStrategyHealth({ most_recent_backtest: { outcome: 'completed' } })).toBe('healthy');
    // Inconclusive sources are undetermined, not healthy.
    expect(resolveStrategyHealth({ most_recent_deployment: { status: 'deploying' } })).toBe('undetermined');
    expect(resolveStrategyHealth({ most_recent_backtest: { outcome: 'running' } })).toBe('undetermined');
    expect(resolveStrategyHealth(null)).toBe('undetermined');
    expect(resolveStrategyHealth('healthy')).toBe('healthy');
  });

  it('keeps the health text visible in compact mode, dropping only the caption', () => {
    const props = { status: 'paused', health: { most_recent_deployment: { status: 'paused' } } };
    const { container: full } = render(<StrategyStatus {...props} />);
    expect(full.textContent).toContain('Health');
    cleanup();
    const { container: compact } = render(<StrategyStatus {...props} compact />);
    expect(compact.textContent).not.toContain('Health');
    // The verdict itself is still a word, not a hue (Requirement 1.4).
    expect(compact.textContent).toContain('Degraded');
  });
});

describe('StrategyStatus: the status vocabulary', () => {
  it('collapses every spelling _normalizeStatus collapsed', () => {
    expect(STRATEGY_STATUSES).toEqual([
      'running',
      'paused',
      'backtesting',
      'draft',
      'failed',
      'stopped',
    ]);
    expect(normaliseStrategyStatus('ACTIVE')).toBe('running');
    expect(normaliseStrategyStatus('live')).toBe('running');
    expect(normaliseStrategyStatus('started')).toBe('running');
    expect(normaliseStrategyStatus('pause')).toBe('paused');
    expect(normaliseStrategyStatus('testing')).toBe('backtesting');
    expect(normaliseStrategyStatus('error')).toBe('failed');
    expect(normaliseStrategyStatus('inactive')).toBe('stopped');
  });

  it('says the status was not reported rather than claiming stopped', () => {
    // `_normalizeStatus` returned "stopped" for '', undefined and anything unknown.
    expect(normaliseStrategyStatus('')).toBeNull();
    expect(normaliseStrategyStatus(undefined)).toBeNull();
    expect(normaliseStrategyStatus('archived')).toBeNull();
    render(<StrategyStatus status={null} health="healthy" />);
    expect(root().getAttribute('data-strategy-status')).toBe('unreported');
    expect(screen.getByText('Status not reported')).toBeTruthy();
    expect(screen.queryByText('Stopped')).toBeNull();
  });

  it('fills a silent status from is_running, and only its true arm', () => {
    render(<StrategyStatus status={null} isRunning health="healthy" />);
    expect(root().getAttribute('data-strategy-status')).toBe('running');
    cleanup();
    // `false` is not a status: it could be paused, draft, stopped or failed.
    render(<StrategyStatus status={null} isRunning={false} health="healthy" />);
    expect(root().getAttribute('data-strategy-status')).toBe('unreported');
  });

  it('prefers an explicit status over is_running', () => {
    render(<StrategyStatus status="paused" isRunning health="healthy" />);
    expect(root().getAttribute('data-strategy-status')).toBe('paused');
  });

  it('throws when given nothing to report', () => {
    expect(() => render(<StrategyStatus />)).toThrow(/no `status`, no `isRunning` and no `health`/);
  });
});

describe('StrategyStatus: environment and calm', () => {
  it('renders no environment chip when the prop is omitted', () => {
    render(<StrategyStatus status="running" health="healthy" />);
    expect(document.querySelector('[data-strategy-environment]')).toBeNull();
  });

  it('says unconfirmed when the server reported no environment, and never guesses', () => {
    render(<StrategyStatus status="running" health="healthy" environment={null} />);
    const chip = document.querySelector('[data-strategy-environment]');
    expect(chip.getAttribute('data-strategy-environment')).toBe('UNCONFIRMED');
    expect(chip.textContent).toContain('ENVIRONMENT UNCONFIRMED');
    expect(chip.textContent).not.toContain('LIVE');
    expect(chip.textContent).not.toContain('PAPER');
  });

  it('distinguishes live from paper on hue, label, icon and border style', () => {
    const { container: live } = render(<StrategyStatus status="running" health="healthy" environment="LIVE" />);
    const liveChip = live.querySelector('[data-strategy-environment]');
    const liveTreatment = {
      colour: liveChip.style.color,
      label: liveChip.textContent,
      icons: liveChip.querySelectorAll('svg').length,
      border: liveChip.style.borderStyle,
    };
    cleanup();
    const { container: paper } = render(<StrategyStatus status="running" health="healthy" environment="PAPER" />);
    const paperChip = paper.querySelector('[data-strategy-environment]');
    expect(paperChip.style.color).not.toBe(liveTreatment.colour);
    expect(paperChip.textContent).not.toBe(liveTreatment.label);
    expect(paperChip.style.borderStyle).not.toBe(liveTreatment.border);
    expect(liveTreatment.icons).toBe(1);
    expect(paperChip.querySelectorAll('svg').length).toBe(1);
  });

  it('carries no pulse, no blink and no injected keyframes', () => {
    // LiveStatus pulsed its dot; LiveStatusV2 added a blink and both injected @keyframes.
    render(<StrategyStatus status="running" health="healthy" environment="LIVE" />);
    expect(document.querySelectorAll('style').length).toBe(0);
    expect(root().innerHTML).not.toContain('animate');
    expect(root().innerHTML).not.toContain('box-shadow');
  });
});
