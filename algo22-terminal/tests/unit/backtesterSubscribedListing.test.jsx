/**
 * MKT-08 — the subscriber who followed "Run backtest" on a purchased Listing.
 *
 * THE DEFECT
 * ----------
 * `library_entries.allowed_actions_for_subscribed` puts `run_backtest` in a subscriber's
 * `allowed_actions` for an entitling Subscription whose Listing permits it, and
 * `pages/Strategies` links that action to `/app/backtest?listing_id=<id>`.
 *
 * Measured: `pages/Backtester` read only `strategy_id`, and NO backend route accepts a listing
 * for a backtest — every one is keyed on `strategy_id` (`routers/strategies.py:2889` and the
 * seven in `routers/strategy_operations.py`; `routers/library.py` has no backtest verb at all).
 *
 * So a paying subscriber landed on a Backtester whose strategy selector could not contain
 * their listing, with no explanation. MKT-05 keeps the Listing's logic out of the subscriber's
 * tenant, so they own no strategy row for it — the selector is *correctly* empty of it, which
 * is exactly why the page needed to say so rather than look broken.
 *
 * WHAT IS ASSERTED, AND WHAT IS NOT
 * ---------------------------------
 * These cases pin the page's ANSWER, not a capability. Subscriber-run backtesting is still not
 * implemented; `test_the_notice_does_not_claim_the_feature_works` is what stops the notice
 * drifting into a promise. When the feature lands — a route taking `listing_id`, resolving the
 * entitlement and running the owner's version without returning the definition, the shape
 * `POST /api/paper-trading/sessions` already has — this whole file should be replaced by tests
 * of the run, not amended.
 *
 * The two alternatives the notice offers are asserted to be the two that actually work today:
 * the paper session resolves a `listing_id` server-side, and so does the live deployment.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const { mockApi, mockNavigate } = vi.hoisted(() => {
  const fns = (names) => Object.fromEntries(names.map((n) => [n, vi.fn()]));
  return {
    mockApi: {
      strategies: fns(['list', 'getById', 'listBacktests', 'backtest', 'versions']),
      library: fns(['myStrategies']),
    },
    mockNavigate: vi.fn(),
  };
});

vi.mock('../../src/api', () => ({
  default: mockApi,
  api: mockApi,
  endpoints: mockApi,
  isAuthenticated: () => false,
  publicGet: vi.fn(async () => ({})),
}));

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useNavigate: () => mockNavigate };
});

import Backtester from '../../src/pages/Backtester';

const renderAt = (search) =>
  render(
    <MemoryRouter initialEntries={[`/app/backtest${search}`]}>
      <Backtester />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  // An account that owns strategies, so a notice about the listing cannot be mistaken for
  // "this account has nothing to backtest" — the two were conflated before this change.
  mockApi.strategies.list.mockResolvedValue({
    strategies: [{ id: 'own-1', name: 'My own strategy', status: 'draft' }],
  });
  mockApi.strategies.listBacktests.mockResolvedValue({ backtests: [] });
});

describe('MKT-08: ?listing_id= on the Backtester', () => {
  it('answers the subscriber instead of leaving them on an empty selector', async () => {
    renderAt('?listing_id=11111111-1111-4111-8111-111111111111');

    const notice = await screen.findByTestId('backtester-listing-unavailable');
    expect(notice.textContent).toMatch(/not available yet/i);
    // The REASON, not just the refusal: a subscriber told "no" without being told why
    // reasonably concludes the product is broken.
    expect(notice.textContent).toMatch(/creator'?s workspace/i);
    expect(notice.textContent).toMatch(/keeps its logic private/i);
  });

  it('offers the two actions that do work, and routes them with the listing id', async () => {
    const listingId = '11111111-1111-4111-8111-111111111111';
    renderAt(`?listing_id=${listingId}`);
    await screen.findByTestId('backtester-listing-unavailable');

    await userEvent.click(screen.getByRole('button', { name: /start paper trading/i }));
    expect(mockNavigate).toHaveBeenCalledWith(`/app/paper-trading?listing_id=${listingId}`);

    await userEvent.click(screen.getByRole('button', { name: /view the listing/i }));
    expect(mockNavigate).toHaveBeenCalledWith(`/app/marketplace?listing_id=${listingId}`);
  });

  it('the notice does not claim the feature works', async () => {
    renderAt('?listing_id=11111111-1111-4111-8111-111111111111');
    const notice = await screen.findByTestId('backtester-listing-unavailable');

    // A guard against the notice drifting into a promise once someone starts the feature.
    // "Not available yet" is a statement about today; "coming soon" and a run control are not.
    expect(notice.textContent).not.toMatch(/coming soon/i);
    expect(notice.querySelector('[data-testid="run-backtest"]')).toBeNull();
    expect(notice.textContent).not.toMatch(/run the backtest/i);
  });

  it('says nothing about a listing when none was asked for', async () => {
    renderAt('?strategy_id=own-1');
    await waitFor(() => expect(mockApi.strategies.list).toHaveBeenCalled());
    expect(screen.queryByTestId('backtester-listing-unavailable')).toBeNull();
  });

  it('does not issue a strategy read for a listing id', async () => {
    // The page used to treat an unrecognised `selectedId` as "a strategy the list does not
    // carry" and issue `getById`. A listing id is not a strategy id, so that request would
    // 404 — or worse, resolve to an unrelated row — and it must not be sent at all.
    renderAt('?listing_id=11111111-1111-4111-8111-111111111111');
    await screen.findByTestId('backtester-listing-unavailable');
    expect(mockApi.strategies.getById).not.toHaveBeenCalled();
  });
});
