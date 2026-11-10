/** MKT-04: the creator panel drives the server's gated path and shows its answers. */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const STRAT = { id: 's-1', name: 'EMA cross', description: 'demo' };
const runs = [1, 2, 3, 4].map((i) => ({
  id: `r-${i}`,
  status: 'completed',
  start_date: `202${i}-01-01T00:00:00+00:00`,
  end_date: `202${i}-06-30T00:00:00+00:00`,
  total_trades: 90 + i,
  executed_bar_count: 4300,
  total_return_pct: 1.5,
}));

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  list: vi.fn(),
  create: vi.fn(),
  priceRange: vi.fn(),
  setPrice: vi.fn(),
  listBacktests: vi.fn(),
}));

vi.mock('../../../src/apiClient', () => ({ get: mocks.get, post: mocks.post }));
vi.mock('../../../src/api', () => ({
  api: {
    library: {
      submissions: {
        list: mocks.list,
        create: mocks.create,
        priceRange: mocks.priceRange,
        setPrice: mocks.setPrice,
      },
    },
    strategies: { listBacktests: mocks.listBacktests },
  },
}));

import CreatorMarketplacePanel from '../../../src/components/marketplace/CreatorMarketplacePanel';

beforeEach(() => {
  Object.values(mocks).forEach((m) => m.mockReset());
  mocks.listBacktests.mockResolvedValue({ backtests: runs });
});

describe('CreatorMarketplacePanel', () => {
  it('offers to create the listing when none exists for this strategy', async () => {
    mocks.get.mockResolvedValue({ items: [{ id: 'l-x', source_strategy_id: 'other', name: 'x', moderation_status: 'approved' }] });
    mocks.list.mockResolvedValue({ submissions: [] });

    render(<CreatorMarketplacePanel strategy={STRAT} />);

    expect(await screen.findByRole('button', { name: 'Create listing' })).toBeDefined();
    expect(screen.getByText('Create the listing first.')).toBeDefined();
  });

  it('lists every eligibility failure in plain words when the gate refuses', async () => {
    mocks.get.mockResolvedValue({ items: [{ id: 'l-1', source_strategy_id: 's-1', name: 'EMA cross', moderation_status: 'pending' }] });
    mocks.list.mockResolvedValue({ submissions: [] });
    mocks.create.mockRejectedValue({
      status: 422,
      data: { error: { code: 'MARKETPLACE_ELIGIBILITY_FAILED', message: 'x', details: { failures: ['EV_DISTINCT', 'EV_TRADES'] } } },
    });

    render(<CreatorMarketplacePanel strategy={STRAT} />);
    const boxes = await screen.findAllByRole('checkbox');
    boxes.slice(0, 3).forEach((box) => fireEvent.click(box));
    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }));

    const list = await screen.findByTestId('eligibility-failures');
    expect(list.textContent).toMatch(/genuinely different conditions/);
    expect(list.textContent).toMatch(/enough trades/);
    expect(mocks.create).toHaveBeenCalledWith({ strategy_id: 's-1', backtest_ids: ['r-1', 'r-2', 'r-3'] });
  });

  it('shows the submission state and computes the price range in exact minor units', async () => {
    mocks.get.mockResolvedValue({ items: [{ id: 'l-1', source_strategy_id: 's-1', name: 'EMA cross', moderation_status: 'pending' }] });
    mocks.list.mockResolvedValue({
      submissions: [{ id: 'sub-1', submission_state: 'SUBMITTED', created_at: '2026-11-10' }],
    });
    mocks.priceRange.mockResolvedValue({
      minimum_price_minor: 999,
      recommended_price_minor: 2499,
      maximum_price_minor: 4999,
    });
    mocks.setPrice.mockResolvedValue({});

    render(<CreatorMarketplacePanel strategy={STRAT} />);
    expect((await screen.findByTestId('submission-state')).textContent).toMatch(/waiting for a reviewer/);

    fireEvent.click(screen.getByRole('button', { name: 'Calculate allowed range' }));
    expect((await screen.findByTestId('price-range')).textContent).toMatch(/Recommended 24\.99 USD/);

    fireEvent.change(screen.getByLabelText('Monthly price'), { target: { value: '19.99' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set price' }));
    await waitFor(() => expect(mocks.setPrice).toHaveBeenCalledWith('sub-1', 1999, 'USD'));
  });
});
