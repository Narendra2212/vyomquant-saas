/**
 * Removing one run from "Saved Backtest History" (Requirement 22.1's delete action).
 *
 * The endpoint behind this is `DELETE /api/backtests/{backtest_id}`
 * (`routers/strategy_operations.delete_backtest`), and the one property that shapes every
 * assertion below is that its response is a CONSTANT. Requirement 20.2 requires "this backtest
 * is not yours" and "there is no such backtest" to be indistinguishable, so the handler does
 * not forward whether its ownership-scoped DELETE (`id` AND `user_id`) matched a row: every
 * caller receives `{success: true}`.
 *
 * Two things follow, and they are the subject here:
 *
 *   * **The body cannot be read as "a row was removed."** So the page must not drop the row
 *     from local state on a 200. It re-reads the list, and what the list says is what shows —
 *     which is asserted in both directions, including the case where the server still holds
 *     the row after a `{success: true}`.
 *   * **The request is irreversible**, so it goes behind `ds/ConfirmDialog` at
 *     `intent="destructive"`, and the first click on the row's control must issue nothing.
 *
 * What is NOT asserted here: the ownership predicate itself, which is a server property
 * covered by `tests/sandbox_lifecycle/test_cross_tenant_ownership_matrix.py`, and the history
 * read's own wiring, which is `backtestSaveFlow.test.jsx`'s subject.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { resetOverlayRegistry } from '../../src/components/ds/overlayRegistry';

/* Charts are not the subject, and recharts needs layout APIs jsdom does not implement. See
 * `backtestSaveFlow.test.jsx` on why the stub is `ds/Chart` and not `recharts`. */
vi.mock('../../src/components/ds/Chart', () => {
  const Stub = (props) => <figure data-testid="chart" data-chart-kind={props.kind} />;
  return { __esModule: true, Chart: Stub, default: Stub };
});

/* The two market controls read the venue's asset universe through the shared axios instance
 * rather than through `src/api`. Refused rather than stubbed, for the reason given in
 * `backtestSaveFlow.test.jsx`: market selection is not this suite's subject and a fabricated
 * universe would be a list no trader could have got from the platform. */
vi.mock('../../src/apiClient', () => {
  const refuse = () => Promise.reject(new Error('this suite makes no network requests'));
  return {
    default: { get: refuse, post: refuse, put: refuse, patch: refuse, delete: refuse },
    get: refuse,
    post: refuse,
    put: refuse,
    del: refuse,
    patch: refuse,
    publicGet: refuse,
    ApiError: Error,
    clearApiCache: () => {},
    getMetrics: () => ({}),
    getToken: () => null,
    isAuthenticated: () => false,
    logout: refuse,
    testConnection: refuse,
  };
});

const { mockStrategies, mockExchange } = vi.hoisted(() => ({
  mockStrategies: {
    list: vi.fn(),
    getById: vi.fn(),
    versions: vi.fn(),
    executeBacktest: vi.fn(),
    listBacktests: vi.fn(),
    deleteBacktest: vi.fn(),
  },
  mockExchange: { getSupported: vi.fn() },
}));

vi.mock('../../src/api', () => ({
  endpoints: { strategies: mockStrategies, exchange: mockExchange },
}));

import Backtester from '../../src/pages/Backtester';

/** One persisted `strategy_backtests` row, as `GET /api/backtests` lists it. */
const savedRow = (overrides = {}) => ({
  id: 'bt-9',
  strategy_id: 's-1',
  version: 'v1.0',
  dataset: 'BTC/USDT',
  status: 'completed',
  created_at: '2024-01-02T00:00:00+00:00',
  total_return_pct: 12.5,
  win_rate: 0.55,
  max_drawdown: 0.0825,
  total_trades: 4,
  equity_curve: [{ timestamp: '2024-01-01T00:00:00Z', equity: 10000 }],
  blueprint: { name: 'Momentum v2', nodes: [], edges: [] },
  ...overrides,
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <Backtester strategy={{ id: 's-1', name: 'Momentum v2', nodes: [], edges: [] }} />
    </MemoryRouter>,
  );

/** The history panel only renders when the read answered with rows. */
const historyOnScreen = async () => {
  expect(await screen.findByText('Saved Backtest History')).toBeTruthy();
};

/** The row's own control. Exact, so it cannot match the dialog's `Delete run`. */
const rowDeleteControl = () => screen.getByRole('button', { name: 'Delete' });

/** The confirmation's confirm action. */
const confirmControl = () => screen.getByRole('button', { name: 'Delete run' });

const deleteErrorRegion = () => document.querySelector('[data-region="saved-runs-delete-error"]');

describe('deleting a run from Saved Backtest History', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // No global setup file, so a claim left by a previous test would be refused — and the
    // registry throws on a refusal in development, which includes the test runner.
    resetOverlayRegistry();

    mockStrategies.list.mockResolvedValue([]);
    mockStrategies.versions.mockResolvedValue({ versions: [{ id: 'v-1', is_current: true }] });
    mockStrategies.listBacktests.mockResolvedValue({ backtests: [savedRow()], total: 1 });
    mockStrategies.deleteBacktest.mockResolvedValue({ success: true });
    mockExchange.getSupported.mockResolvedValue({ supported: ['BTC/USDT'] });

    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ valid: true, warnings: [] }),
    }));
  });

  afterEach(() => {
    resetOverlayRegistry();
    vi.restoreAllMocks();
  });

  it('issues nothing on the first click, and asks instead', async () => {
    const user = userEvent.setup();
    renderPage();
    await historyOnScreen();

    await user.click(rowDeleteControl());

    // The whole point of the confirmation: an irreversible request is not sent by the click
    // that opens it.
    expect(mockStrategies.deleteBacktest).not.toHaveBeenCalled();
    expect(screen.getByText('Delete this backtest run?')).toBeTruthy();
  });

  it('names the run in the confirmation, so the trader confirms a specific row', async () => {
    const user = userEvent.setup();
    renderPage();
    await historyOnScreen();

    await user.click(rowDeleteControl());

    // Requirement 8.1's review grid, drawn from the row that was activated.
    const dialog = screen.getByRole('dialog');
    expect(dialog.textContent).toContain('Momentum v2');
    expect(dialog.textContent).toContain('v1.0');
    expect(dialog.textContent).toContain('BTC/USDT');
  });

  it('constructs no real-funds acknowledgement, because nothing here reaches a venue', async () => {
    const user = userEvent.setup();
    renderPage();
    await historyOnScreen();

    await user.click(rowDeleteControl());

    // Requirement 8.4 wants the Live statement never built outside Live, not merely hidden.
    expect(screen.getByRole('dialog').querySelector('input[type="checkbox"]')).toBeNull();
    expect(screen.queryByText(/real funds/i)).toBeNull();
  });

  it('cancelling leaves the run alone', async () => {
    const user = userEvent.setup();
    renderPage();
    await historyOnScreen();

    await user.click(rowDeleteControl());
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(mockStrategies.deleteBacktest).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByRole('cell', { name: 'BTC/USDT' })).toBeTruthy();
  });

  it('deletes the row that was chosen, and only on the explicit confirm', async () => {
    const user = userEvent.setup();
    renderPage();
    await historyOnScreen();

    await user.click(rowDeleteControl());
    await user.click(confirmControl());

    await waitFor(() => expect(mockStrategies.deleteBacktest).toHaveBeenCalledTimes(1));
    expect(mockStrategies.deleteBacktest).toHaveBeenCalledWith('bt-9');
  });

  it('re-reads the history afterwards, because the response cannot confirm the removal', async () => {
    const user = userEvent.setup();
    renderPage();
    await historyOnScreen();
    await waitFor(() => expect(mockStrategies.listBacktests).toHaveBeenCalledTimes(1));

    // The row is gone server-side now, which is the only place that can say so.
    mockStrategies.listBacktests.mockResolvedValue({ backtests: [], total: 0 });

    await user.click(rowDeleteControl());
    await user.click(confirmControl());

    await waitFor(() => expect(mockStrategies.listBacktests).toHaveBeenCalledTimes(2));
    // An empty history is not a statement worth a heading, so the whole panel goes.
    await waitFor(() => expect(screen.queryByText('Saved Backtest History')).toBeNull());
  });

  it('keeps the row when the list still holds it, despite the success response', async () => {
    const user = userEvent.setup();
    renderPage();
    await historyOnScreen();

    // `{success: true}` is what EVERY caller gets, including one whose DELETE matched
    // nothing. A page that trusted the body would blank this row; the list is what decides,
    // and the list still has it.
    await user.click(rowDeleteControl());
    await user.click(confirmControl());

    await waitFor(() => expect(mockStrategies.deleteBacktest).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockStrategies.listBacktests).toHaveBeenCalledTimes(2));
    expect(screen.getByRole('cell', { name: 'BTC/USDT' })).toBeTruthy();
    expect(deleteErrorRegion()).toBeNull();
  });

  it('reports a refused delete without claiming the history is unavailable', async () => {
    const user = userEvent.setup();
    mockStrategies.deleteBacktest.mockRejectedValue(new Error('delete refused'));
    vi.spyOn(console, 'error').mockImplementation(() => {});

    renderPage();
    await historyOnScreen();

    await user.click(rowDeleteControl());
    await user.click(confirmControl());

    // Its own region: the list on screen is current and one row simply did not go, which is
    // not the same claim as "this panel could not be read".
    await waitFor(() => expect(deleteErrorRegion()).not.toBeNull());
    expect(document.querySelector('[data-region="saved-runs-error"]')).toBeNull();
    expect(screen.getByRole('cell', { name: 'BTC/USDT' })).toBeTruthy();
  });

  it('offers the delete for a run that never completed, which Inspect refuses', async () => {
    const user = userEvent.setup();
    mockStrategies.listBacktests.mockResolvedValue({
      backtests: [savedRow({ status: 'failed', total_return_pct: null })],
      total: 1,
    });
    renderPage();
    await historyOnScreen();

    // A failed run holds no result to load, which is what makes inspecting it meaningless —
    // but it is still a row the account owns, and the one most worth removing. So the delete
    // is enabled where Inspect is not, and carries no Requirement 15.3 reason because there
    // is no state in which it is unavailable.
    expect(screen.getByRole('button', { name: 'Inspect' }).disabled).toBe(true);
    expect(rowDeleteControl().disabled).toBe(false);

    await user.click(rowDeleteControl());
    await user.click(confirmControl());

    await waitFor(() => expect(mockStrategies.deleteBacktest).toHaveBeenCalledWith('bt-9'));
  });
});
