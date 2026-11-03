/**
 * StrategyDetailPayouts.test.jsx — MKT-07's creator payout panel.
 *
 * `PayoutsSection` (`pages/StrategyDetail.jsx`, inside `RevenueTab`) is the new reader for
 * `GET /api/library/creator/payouts` — the half of the creator ledger that reports what the
 * platform has actually PAID, alongside the pre-existing `RevenueTab` figures for what a
 * creator has EARNED. `../apiClient`'s `get` is the only thing mocked: the component under
 * test, `formatMinorUnits` and `ds/Metric`'s `NotAvailableMarker` are all real.
 *
 * Four states, matching `useCreatorAnalytics`'s own vocabulary one row up in this file:
 * ready-with-rows (a creator who has been paid at least once), ready-but-empty (owns a
 * listing, has never been paid), failed (the read did not complete — rendered as absence,
 * never as a zero), and loading. No `@testing-library/jest-dom` matcher is used — this repo
 * does not register one — so every assertion resolves through plain DOM queries, the same
 * convention `StrategyMarketplace.test.jsx` uses.
 */

import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../../apiClient', () => ({
  get: vi.fn(),
  post: vi.fn(),
}));

import { get } from '../../apiClient';
import { PayoutsSection } from '../StrategyDetail';

beforeEach(() => {
  get.mockReset();
});

describe('PayoutsSection — a creator who owns a listing and has been paid', () => {
  it('renders the real payout rows the server returned, honestly formatted', async () => {
    get.mockResolvedValueOnce({
      payouts: [
        {
          id: 'payout-1',
          amount_minor: 150000,
          currency: 'INR',
          status: 'completed',
          period_start: '2026-10-20',
          period_end: '2026-10-27',
          created_at: '2026-10-28T00:00:00Z',
          settled_at: '2026-10-29T12:00:00Z',
          reference: 'UPI-REF-9922',
          reason: null,
        },
        {
          id: 'payout-2',
          amount_minor: 42000,
          currency: 'USD',
          status: 'pending',
          period_start: '2026-10-27',
          period_end: '2026-11-03',
          created_at: '2026-11-03T00:00:00Z',
          settled_at: null,
          reference: null,
          reason: null,
        },
      ],
      count: 2,
    });

    const { container } = render(<PayoutsSection />);

    await waitFor(() => expect(screen.queryByText('completed')).not.toBeNull());

    expect(screen.queryByText('pending')).not.toBeNull();
    // `formatMinorUnits(150000, 'INR')` — real currency math, not a server-formatted string.
    expect(container.textContent).toMatch(/1,500\.00/);
    expect(container.textContent).toMatch(/420\.00/);
    expect(container.textContent).toContain('Ref UPI-REF-9922');
    expect(container.textContent).toContain('2026-10-20');

    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith('/api/library/creator/payouts');
  });

  it('shows the rejection reason on a rejected payout, never a fabricated zero', async () => {
    get.mockResolvedValueOnce({
      payouts: [
        {
          id: 'payout-3',
          amount_minor: 9900,
          currency: 'USD',
          status: 'rejected',
          period_start: '2026-10-13',
          period_end: '2026-10-20',
          created_at: '2026-10-21T00:00:00Z',
          settled_at: null,
          reference: null,
          reason: 'Invalid payout details on file.',
        },
      ],
      count: 1,
    });

    render(<PayoutsSection />);

    await waitFor(() => expect(screen.queryByText('rejected')).not.toBeNull());
    expect(screen.queryByText('Invalid payout details on file.')).not.toBeNull();
  });
});

describe('PayoutsSection — a creator who has never been paid', () => {
  it('renders an honest empty state, not a zero-row table and not a hidden panel', async () => {
    get.mockResolvedValueOnce({ payouts: [], count: 0 });

    const { container } = render(<PayoutsSection />);

    await waitFor(() =>
      expect(container.textContent).toMatch(/No payouts have been created for you yet/i),
    );
  });
});

describe('PayoutsSection — the read fails', () => {
  it('reports the failure rather than rendering an empty list', async () => {
    get.mockRejectedValueOnce(new Error('network down'));

    const { container } = render(<PayoutsSection />);

    await waitFor(() => expect(container.textContent).toMatch(/could not be read/i));
    // Never silently falls through to "no payouts yet" — the two states are kept apart.
    expect(container.textContent).not.toMatch(/No payouts have been created/i);
  });
});

describe('PayoutsSection — while the read is in flight', () => {
  it('shows a loading state rather than an empty one', () => {
    get.mockReturnValueOnce(new Promise(() => {})); // never resolves within this test

    const { container } = render(<PayoutsSection />);

    expect(container.textContent).toMatch(/Reading your payout history/i);
  });
});
