/**
 * TH-01: the win rate counts only trades that REALISED a profit or a loss.
 *
 * Opening fills realise exactly 0. Counting them as non-wins made a paper ledger of 11 opening
 * buys read "WIN RATE 0.0%" in production (2026-11-13).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const transport = vi.hoisted(() => ({ raw: vi.fn() }));

vi.mock('../../../src/apiClient', async (importOriginal) => {
  const actual = await importOriginal();
  const client = {
    get: (...a) => transport.raw(...a), post: (...a) => transport.raw(...a),
    put: (...a) => transport.raw(...a), delete: (...a) => transport.raw(...a),
    patch: (...a) => transport.raw(...a), request: (...a) => transport.raw(...a),
    defaults: { headers: {} },
    interceptors: { request: { use: () => 0 }, response: { use: () => 0 } },
  };
  return { ...actual, default: client, get: transport.raw, post: transport.raw };
});

vi.mock('../../../src/websocketClient', () => {
  const u = () => {};
  const c = {
    subscribe: vi.fn(() => u), subscribeChannel: vi.fn(() => u), onOpen: vi.fn(() => u),
    onStatusChange: vi.fn(() => u), acquire: vi.fn(), release: vi.fn(), send: vi.fn(),
    getStatus: vi.fn(() => 'idle'), isConnected: vi.fn(() => false),
  };
  return { __esModule: true, default: c, wsClient: c };
});

import { ordersApi } from '../../../src/api/modules/orders';
import { paperApi } from '../../../src/api/modules/paper';
import TradeHistory from '../../../src/pages/TradeHistory';

const trade = (i, pnl) => ({
  id: `t${i}`, symbol: 'BTC-USDT', side: pnl === '0.0' ? 'buy' : 'sell', quantity: '0.0001',
  price: '68000', pnl, realized_pnl: pnl, fee: '0.01', status: 'filled',
  executed_at: `2026-11-13T08:${String(10 + i).padStart(2, '0')}:00Z`,
});

const renderPaper = async (trades) => {
  vi.spyOn(ordersApi, 'getHistory').mockResolvedValue([]);
  vi.spyOn(paperApi, 'getTrades').mockResolvedValue({
    trades, count: trades.length, execution_environment: 'PAPER', is_simulated: true,
  });
  render(<MemoryRouter><TradeHistory /></MemoryRouter>);
  const group = await screen.findByRole('group', { name: /ledger/i });
  fireEvent.click(within(group).getByText('Paper', { exact: true }));
  await waitFor(() => expect(paperApi.getTrades).toHaveBeenCalled());
};

const winRateText = async () => {
  const label = await screen.findByText(/^win rate$/i);
  const card = label.closest('[data-metric], div');
  return (card?.parentElement ?? card).textContent;
};

describe('TH-01 win rate', () => {
  beforeEach(() => { vi.restoreAllMocks(); transport.raw.mockReset(); });

  it('is not available when only opening fills (P&L exactly 0) are in view', async () => {
    await renderPaper([trade(1, '0.0'), trade(2, '0.0'), trade(3, '0.0')]);
    await waitFor(async () => expect(await winRateText()).not.toMatch(/0\.0\s*%/));
  });

  it('is computed over decided trades only, ignoring opening fills', async () => {
    // 2 opening fills (0), 1 win, 1 loss -> 50.0%, not 25.0%.
    await renderPaper([trade(1, '0.0'), trade(2, '0.0'), trade(3, '5.00'), trade(4, '-2.00')]);
    await waitFor(async () => expect(await winRateText()).toMatch(/50\.0\s*%/));
  });
});
