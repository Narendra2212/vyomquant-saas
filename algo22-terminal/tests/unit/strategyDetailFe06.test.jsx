/**
 * FE-06 -- `pages/StrategyDetail.jsx` no longer fabricates plausible-looking values when
 * the server reports nothing.
 *
 * THE DEFECT
 *   The Overview tab's Status card defaulted an ABSENT `health`/`worker_status`/
 *   `exchange_status` field to "healthy"/"active"/"connected" -- a trader reading a
 *   response that never carried those fields (or arrived from a broken/partial read) was
 *   told, falsely and reassuringly, that everything was fine. The Overview and Metrics
 *   tabs' figure grids, and the Risk tab's drawdown/exposure/limit figures, used `|| 0` /
 *   `|| 100`, so an absent figure rendered as a genuine-looking zero (or, for the exposure
 *   limit, a genuine-looking "full room to trade").
 *
 * THE FIX
 *   The three status rows now render `ds/Metric`'s `NotAvailableMarker` (the same
 *   not-available pattern already used elsewhere in this app, e.g. Dashboard.jsx /
 *   Portfolio.jsx) when the field is `null`/`undefined`, instead of a reassuring default.
 *   Every figure grid now uses `?? null` instead of `|| 0` (or `|| 100`), and renders
 *   `NotAvailableMarker` for a `null` value instead of the number.
 *
 * WHAT THIS FILE PROVES
 *   * the Status card shows "not available" for health/worker/exchange when the server
 *     sends nothing, not "healthy"/"active"/"connected";
 *   * the Overview grid shows "not available" for P&L/ROI/win rate/Sharpe when
 *     `performance` is `{}` (the real shape MetricsService returns with no data), not 0;
 *   * a GENUINE zero still renders as 0 -- this fix must not swallow real data.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

vi.mock('../../src/components/ResearchConsole', () => ({
  default: () => <div data-testid="research-console-stub" />,
}));
vi.mock('../../src/components/DeploymentConsole', () => ({
  default: () => <div data-testid="deployment-console-stub" />,
}));

const { mockClient } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn() },
}));
vi.mock('../../src/apiClient', () => mockClient);

import StrategyDetail from '../../src/pages/StrategyDetail';

const STRATEGY_ID = 's-fe06';

const jsonResponse = (body) => ({ ok: true, status: 200, json: async () => body });

/** The real shape of `GET /api/strategies/{id}` with no reported health/worker/exchange
 * status and an empty `performance` object -- exactly what MetricsService returns with
 * no data (METRICS-01's fix).
 */
const detailPayloadWithNoTelemetry = () => ({
  strategy: {
    id: STRATEGY_ID,
    name: 'No-telemetry strategy',
    description: 'd',
    status: 'stopped',
    symbol: 'BTCUSDT',
    timeframe: '1h',
    environment: 'paper',
    current_version: '1.0',
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-02T00:00:00Z',
    // No `health`, `worker_status`, `exchange_status` -- the real "server did not report
    // this" shape, not a special test-only value.
  },
  version: { id: 'ver-1' },
  deployments: [],
  backtests: [],
  performance: {},
});

const installFetch = (payload) => {
  const impl = vi.fn(async (url, options = {}) => {
    const target = String(url);
    const method = (options.method ?? 'GET').toUpperCase();
    if (method === 'GET' && target.endsWith(`/api/strategies/${STRATEGY_ID}/versions`)) {
      return jsonResponse({ versions: [] });
    }
    if (method === 'GET' && target.endsWith(`/api/strategies/${STRATEGY_ID}`)) {
      return jsonResponse(payload);
    }
    return jsonResponse({});
  });
  vi.stubGlobal('fetch', impl);
  return impl;
};

const renderPage = async () =>
  render(
    <MemoryRouter initialEntries={[`/app/strategies/${STRATEGY_ID}`]}>
      <Routes>
        <Route path="/app/strategies/:strategyId" element={<StrategyDetail />} />
      </Routes>
    </MemoryRouter>,
  );

describe('FE-06: StrategyDetail does not fabricate plausible values', () => {
  it('renders "not available" for health/worker/exchange status instead of a reassuring default', async () => {
    mockClient.get.mockResolvedValue({});
    mockClient.post.mockResolvedValue({});
    installFetch(detailPayloadWithNoTelemetry());

    await renderPage();

    const statusHeading = await screen.findByText('Status');
    const statusCard = statusHeading.closest('.p-4') ?? statusHeading.parentElement;

    // The three fabricated defaults must never appear for this payload.
    expect(within(statusCard).queryByText('healthy')).toBeNull();
    expect(within(statusCard).queryByText('active')).toBeNull();
    expect(within(statusCard).queryByText('connected')).toBeNull();

    // And the honest marker must be there instead -- three times, one per unreported field.
    const markers = statusCard.querySelectorAll('[data-metric-marker="not-available"]');
    expect(markers.length).toBe(3);
  });

  it('renders "not available" for P&L/ROI/win rate/Sharpe when performance is empty, not 0', async () => {
    mockClient.get.mockResolvedValue({});
    mockClient.post.mockResolvedValue({});
    installFetch(detailPayloadWithNoTelemetry());

    await renderPage();

    await screen.findByText('Performance');

    const perfHeading = screen.getByText('Performance');
    const perfCard = perfHeading.closest('.p-4') ?? perfHeading.parentElement;

    const markers = perfCard.querySelectorAll('[data-metric-marker="not-available"]');
    // Total P&L, ROI, Win Rate, Sharpe.
    expect(markers.length).toBe(4);
    expect(within(perfCard).queryByText('0')).toBeNull();
  });

  it('still renders a genuine zero figure as 0, not "not available"', async () => {
    mockClient.get.mockResolvedValue({});
    mockClient.post.mockResolvedValue({});
    installFetch({
      ...detailPayloadWithNoTelemetry(),
      performance: {
        today_pnl: 0,
        roi_pct: 0,
        win_rate: 0,
        sharpe_ratio: 1.4,
      },
    });

    await renderPage();

    await screen.findByText('Performance');
    const perfHeading = screen.getByText('Performance');
    const perfCard = perfHeading.closest('.p-4') ?? perfHeading.parentElement;

    // A real 0 renders as 0, not as an unavailable marker.
    expect(perfCard.querySelectorAll('[data-metric-marker="not-available"]').length).toBe(0);
    expect(within(perfCard).getAllByText('0').length).toBeGreaterThan(0);
    expect(within(perfCard).getByText('1.4')).toBeTruthy();
  });
});
