/**
 * Strategies.test.jsx — the server-derived ownership section of the Strategy Library page.
 *
 * Spec: marketplace-subscriptions-paper-trading task 32.9 (the page it covers is task 32.7).
 * Requirements 12.2, 12.3, 12.4, 12.5, 12.6, 12.8, 28.1, 29.2.
 *
 * WHAT THIS SUITE IS FOR
 * ----------------------
 *  1. **The labels come from the response, not from the page.** `ownership` is rendered exactly
 *     as `GET /api/library/my-strategies` returned it. The fixtures deliberately point the other
 *     way — an entry labelled `SUBSCRIBED` that carries a `strategy_id` and no subscription, an
 *     entry labelled `OWNED` that carries a full subscription triple, and a label this build has
 *     never seen — so any client-side inference from the entry's *shape* would produce a
 *     different label than the one asserted (Requirements 12.2, 12.3).
 *  2. **A `SUBSCRIBED` entry offers exactly the eight permitted actions.** The eight are asserted
 *     present by `data-action`, and each of the thirteen forbidden ones absent. The assertion is
 *     made in **both** directions: with a well-behaved `allowed_actions`, and with an
 *     `allowed_actions` that has been poisoned with all thirteen forbidden names — the page must
 *     still construct no control for any of them, because the catalogue narrows the offer and
 *     can never widen it (Requirement 12.4).
 *  3. **A non-entitling entry is an explicit expired state.** Every execution action is
 *     programmatically disabled — `disabled` *and* `aria-disabled`, with an `aria-describedby`
 *     text reason — and renewal is still offered (Requirement 12.5).
 *  4. **Loading, empty, error-with-retry and unauthorised are states, not stale lists.** An
 *     error clears the entries, so what renders is the error and not a list nobody knows is
 *     still current (Requirement 12.8, Requirement 28.1's no-placeholder rule).
 *
 * HOW EACH ASSERTION IS PROVED CAPABLE OF FAILING
 * ----------------------------------------------
 *  * The forbidden-action assertions are queried with the *same* `[data-action="…"]` selector
 *    that finds the eight permitted controls in the same render, and `describe('controls')`
 *    shows that selector matching a `data-action="edit"` element when one exists. So "no control
 *    for `edit`" is a fact about the page, not a selector that never matches anything.
 *  * The label assertions are paired against shapes that contradict them: if the page inferred
 *    `ownership` from the presence of `subscription`, the two cross-wired fixtures would both
 *    render the opposite label and both tests would fail.
 *  * The stale-list assertion drives a real second read through the page's own
 *    `cancel_renewal` → `reloadOwnership()` path, asserts the entry was on screen first, and
 *    then asserts it is gone once that read fails — so it cannot pass by never having rendered.
 *
 * WHAT IS MOCKED, AND WHY IT IS NOT THE THING UNDER TEST
 * ----------------------------------------------------
 * `../../api` (the shared module — `endpoints.strategies.list` for the owner grid and
 * `api.library.*` for this section) and `../StrategyBuilder` (a heavy tree this page swaps the
 * whole view for, and which the ownership section never reaches). `DeployPreflightPanel`,
 * `react-router-dom` and the ownership section itself are real.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const { mockApi } = vi.hoisted(() => {
  const fns = (names) => Object.fromEntries(names.map((n) => [n, vi.fn()]));
  const api = {
    library: fns(['myStrategies', 'renewSubscription', 'cancelSubscription', 'detail', 'clone']),
    strategies: fns(['list', 'delete', 'rename', 'clone', 'pause', 'deployVersion', 'deployPreflight']),
    exchange: fns(['list']),
  };
  return { mockApi: api };
});

vi.mock('../../api', () => ({
  default: mockApi,
  api: mockApi,
  endpoints: mockApi,
}));

// The builder is an entire alternate view; the ownership section never renders it.
vi.mock('../StrategyBuilder', () => ({
  default: () => <div data-testid="builder-stub" />,
}));

import Strategies from '../Strategies';

// ─────────────────────────────────────────────────────────────────────────────────────────
// The vocabulary, spelled as the design's affordance table spells it
// ─────────────────────────────────────────────────────────────────────────────────────────

/** The eight a `SUBSCRIBED` entry may be offered (design.md, task 32.7). */
const PERMITTED = [
  'view_listing',
  'run_backtest',
  'deploy_live',
  'start_paper',
  'view_performance',
  'view_subscription',
  'renew',
  'cancel_renewal',
];

/** The thirteen it may never be offered, whatever `allowed_actions` says. */
const FORBIDDEN = [
  'edit',
  'open_in_builder',
  'view_graph',
  'edit_blocks',
  'view_indicator_params',
  'edit_indicator_params',
  'view_risk_config',
  'edit_risk_config',
  'export_definition',
  'download_definition',
  'view_model_params',
  're_version',
  'delete',
];

/** The three that execute the strategy, and that Requirement 12.5 disables when unentitled. */
const EXECUTION = ['run_backtest', 'deploy_live', 'start_paper'];

// ─────────────────────────────────────────────────────────────────────────────────────────
// Fixtures — the `library_entries` shapes, field for field
// ─────────────────────────────────────────────────────────────────────────────────────────

const SUB_ID = 'e-sub';

const listing = (overrides = {}) => ({
  name: 'Momentum Alpha',
  symbol: 'BTCUSDT',
  supported_timeframes: ['1h'],
  price_display: '$19.98',
  currency: 'USD',
  price_minor: 1998,
  performance_summary: { total_return_pct: 12.5, win_rate_pct: 58.25, total_trades: 240 },
  risk_metrics: { sharpe_ratio: 1.85, max_drawdown_pct: 8.4 },
  ...overrides,
});

const subscribedEntry = (overrides = {}) => ({
  entry_id: SUB_ID,
  ownership: 'SUBSCRIBED',
  listing_id: 'lst-9',
  subscription_id: 'sub-3',
  entitling: true,
  unavailable_reason: null,
  status: 'ACTIVE',
  timeframe: '1h',
  subscription: {
    state: 'ACTIVE',
    period_expiry: '2024-06-01T00:00:00+00:00',
    renewal_state: 'AUTO_RENEW',
  },
  listing: listing(),
  allowed_actions: [...PERMITTED],
  ...overrides,
});

const ownedEntry = (overrides = {}) => ({
  entry_id: 'e-own',
  ownership: 'OWNED',
  strategy_id: 'strat-1',
  name: 'My Own Strategy',
  entitling: true,
  unavailable_reason: null,
  allowed_actions: ['run_backtest', 'start_paper'],
  ...overrides,
});

const ownershipBody = (items, overrides = {}) => ({
  items,
  total: items.length,
  owned_total: items.filter((i) => i.ownership === 'OWNED').length,
  subscribed_total: items.filter((i) => i.ownership === 'SUBSCRIBED').length,
  as_of: '2024-05-01T00:00:00+00:00',
  ...overrides,
});

/** An `ApiError`-shaped rejection: `.status` is what the page classifies on. */
const apiError = (status, message) =>
  Object.assign(new Error(message), { status, getUserMessage: () => message });

// ─────────────────────────────────────────────────────────────────────────────────────────
// Rendering
// ─────────────────────────────────────────────────────────────────────────────────────────

const renderPage = () => render(
  <MemoryRouter>
    <Strategies />
  </MemoryRouter>,
);

/** Mount and wait until the ownership section itself is on screen. */
const mountPage = async () => {
  const view = renderPage();
  await screen.findByTestId('ownership-section');
  return { ...view, user: userEvent.setup() };
};

/** Mount with one ownership body already stubbed, and wait for the entries to render. */
const mountWith = async (items, bodyOverrides = {}) => {
  mockApi.library.myStrategies.mockResolvedValue(ownershipBody(items, bodyOverrides));
  const view = await mountPage();
  await screen.findByTestId(`ownership-entry-${items[0].entry_id}`);
  return view;
};

/** The `data-action` value of every rendered, clickable action control for one entry. */
const offeredActions = (container, entryId) =>
  [...container.querySelectorAll(`[data-testid^="ownership-action-${entryId}-"]`)]
    .map((el) => el.getAttribute('data-action'))
    .sort();

/** Every control of any kind — enabled or disabled — carrying this `data-action`. */
const controlsFor = (container, action) =>
  [...container.querySelectorAll(`[data-action="${action}"]`)];

beforeEach(() => {
  for (const namespace of Object.values(mockApi)) {
    for (const fn of Object.values(namespace)) fn.mockReset();
  }
  // The owner's card grid read, untouched by task 32.7 and irrelevant to this section.
  mockApi.strategies.list.mockResolvedValue([]);
  mockApi.exchange.list.mockResolvedValue([]);
  mockApi.library.myStrategies.mockResolvedValue(ownershipBody([]));
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'log').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ═════════════════════════════════════════════════════════════════════════════════════════
// 1. The labels are the server's (Requirements 12.2, 12.3)
// ═════════════════════════════════════════════════════════════════════════════════════════

describe('ownership labels come from the response (Requirements 12.2, 12.3)', () => {
  it('reads the combined list through api.library.myStrategies, beside the untouched owner read', async () => {
    await mountWith([subscribedEntry()]);

    expect(mockApi.library.myStrategies).toHaveBeenCalledTimes(1);
    expect(mockApi.library.myStrategies).toHaveBeenCalledWith();
    // The pre-existing read is still the one the owner grid is built from.
    expect(mockApi.strategies.list).toHaveBeenCalledTimes(1);
  });

  it('renders each label verbatim, even when the entry\'s shape points the other way', async () => {
    // Cross-wired on purpose: the first *looks* owned (a strategy_id, no subscription) and is
    // labelled SUBSCRIBED; the second *looks* subscribed (a full subscription triple) and is
    // labelled OWNED. A page that inferred ownership from either field would fail both.
    const looksOwned = subscribedEntry({
      entry_id: 'e-1',
      ownership: 'SUBSCRIBED',
      strategy_id: 'strat-77',
      subscription: null,
      subscription_id: null,
      allowed_actions: ['view_listing'],
    });
    const looksSubscribed = ownedEntry({
      entry_id: 'e-2',
      ownership: 'OWNED',
      subscription: {
        state: 'ACTIVE',
        period_expiry: '2024-06-01T00:00:00+00:00',
        renewal_state: 'AUTO_RENEW',
      },
      subscription_id: 'sub-99',
    });

    await mountWith([looksOwned, looksSubscribed]);

    expect(screen.getByTestId('ownership-label-e-1').textContent).toBe('SUBSCRIBED');
    expect(screen.getByTestId('ownership-label-e-2').textContent).toBe('OWNED');
  });

  it('renders a label this build has never seen rather than normalising it into a known one', async () => {
    await mountWith([subscribedEntry({ entry_id: 'e-3', ownership: 'CONSIGNED' })]);

    // Nothing client-side maps this onto OWNED or SUBSCRIBED, which is the point: the page has
    // no opinion about what `ownership` may contain.
    expect(screen.getByTestId('ownership-label-e-3').textContent).toBe('CONSIGNED');
  });

  it('renders the server\'s own totals rather than counting the rendered rows', async () => {
    // One entry on screen, but the server says five are owned and two subscribed. The page
    // reports what it was told.
    await mountWith([subscribedEntry()], { owned_total: 5, subscribed_total: 2, total: 7 });

    const counts = screen.getByTestId('ownership-counts');
    expect(counts.textContent).toContain('Owned: 5');
    expect(counts.textContent).toContain('Subscribed: 2');
  });

  it('renders the Subscription_State, the period expiry and the renewal state as returned', async () => {
    await mountWith([subscribedEntry()]);

    const summary = screen.getByTestId(`ownership-subscription-${SUB_ID}`);
    expect(summary.textContent).toContain('ACTIVE');
    expect(summary.textContent).toContain('AUTO_RENEW');
    // Requirement 28.1: an absent field reads "not reported", never a default.
    const bare = subscribedEntry({
      entry_id: 'e-bare',
      subscription: { state: null, period_expiry: null, renewal_state: null },
    });
    mockApi.library.myStrategies.mockResolvedValue(ownershipBody([bare]));
    renderPage();
    const bareSummary = await screen.findByTestId('ownership-subscription-e-bare');
    expect(bareSummary.textContent.match(/not reported/g).length).toBe(3);
  });
});

// ═════════════════════════════════════════════════════════════════════════════════════════
// 2. Exactly the eight permitted actions, and none of the thirteen (Requirement 12.4)
// ═════════════════════════════════════════════════════════════════════════════════════════

describe('a SUBSCRIBED entry\'s affordances (Requirement 12.4)', () => {
  it('offers exactly the eight permitted actions', async () => {
    const { container } = await mountWith([subscribedEntry()]);

    expect(offeredActions(container, SUB_ID)).toEqual([...PERMITTED].sort());

    const actions = screen.getByTestId(`ownership-actions-${SUB_ID}`);
    // Eight controls, not eight-plus-something.
    expect(within(actions).getAllByRole('button').length).toBe(8);
  });

  it('constructs no control for any of the thirteen forbidden actions', async () => {
    const { container } = await mountWith([subscribedEntry()]);

    for (const action of FORBIDDEN) {
      expect(controlsFor(container, action)).toEqual([]);
      expect(screen.queryByTestId(`ownership-action-${SUB_ID}-${action}`)).toBeNull();
      expect(screen.queryByTestId(`ownership-disabled-${SUB_ID}-${action}`)).toBeNull();
    }
    // The same selector, in the same render, does find the permitted ones — so the thirteen
    // assertions above are about the page and not about a selector that matches nothing.
    for (const action of PERMITTED) {
      expect(controlsFor(container, action).length).toBe(1);
    }
  });

  it('still constructs none of them when allowed_actions itself names all thirteen', async () => {
    // The inverse direction: the server (or something pretending to be it) returns every
    // forbidden name. The catalogue lookup is what renders a control, so an action it does not
    // describe is iterated over and dropped.
    const { container } = await mountWith([
      subscribedEntry({ allowed_actions: [...PERMITTED, ...FORBIDDEN] }),
    ]);

    for (const action of FORBIDDEN) {
      expect(controlsFor(container, action)).toEqual([]);
    }
    expect(offeredActions(container, SUB_ID)).toEqual([...PERMITTED].sort());
    expect(within(screen.getByTestId(`ownership-actions-${SUB_ID}`)).getAllByRole('button').length)
      .toBe(8);
  });

  it('offers only the subset the server returned, not the whole permitted eight', async () => {
    // Requirement 12.4 is a ceiling, not a floor: `allowed_actions` is what is offered.
    const { container } = await mountWith([
      subscribedEntry({ allowed_actions: ['view_listing', 'view_subscription'] }),
    ]);

    expect(offeredActions(container, SUB_ID)).toEqual(['view_listing', 'view_subscription']);
    for (const action of ['run_backtest', 'deploy_live', 'start_paper', 'renew']) {
      expect(controlsFor(container, action)).toEqual([]);
    }
  });

  it('discloses the performance and subscription panels from fields already in the response', async () => {
    const { user } = await mountWith([subscribedEntry()]);

    await user.click(screen.getByTestId(`ownership-action-${SUB_ID}-view_performance`));
    const performance = await screen.findByTestId(`ownership-performance-${SUB_ID}`);
    expect(performance.textContent).toContain('12.5');
    expect(performance.textContent).toContain('1.85');

    await user.click(screen.getByTestId(`ownership-action-${SUB_ID}-view_subscription`));
    const panel = await screen.findByTestId(`ownership-subscription-panel-${SUB_ID}`);
    expect(panel.textContent).toContain('ACTIVE');
    // The price is the server's, exactly as returned.
    expect(panel.textContent).toContain('$19.98');

    // Disclosure is local: nothing was re-read to render either panel.
    expect(mockApi.library.myStrategies).toHaveBeenCalledTimes(1);
  });

  it('runs renewal through api.library and reports what came back', async () => {
    mockApi.library.renewSubscription.mockResolvedValue({
      status: 'PENDING_PAYMENT',
      amount_minor: 1998,
      currency: 'USD',
    });
    const { user } = await mountWith([subscribedEntry()]);

    await user.click(screen.getByTestId(`ownership-action-${SUB_ID}-renew`));

    await waitFor(() => expect(mockApi.library.renewSubscription).toHaveBeenCalledWith('sub-3'));
    const result = await screen.findByTestId(`ownership-action-result-${SUB_ID}`);
    expect(result.textContent).toContain('PENDING_PAYMENT');
    // The integer Minor_Units, unconverted.
    expect(result.textContent).toContain('1998 USD');
    expect(result.textContent).toMatch(/only once the payment is confirmed/i);
  });
});

// ═════════════════════════════════════════════════════════════════════════════════════════
// 3. A non-entitling entry (Requirement 12.5)
// ═════════════════════════════════════════════════════════════════════════════════════════

describe('a Subscription that does not entitle (Requirement 12.5)', () => {
  /** What the server returns for an expired period: the execution actions are withheld. */
  const expired = (overrides = {}) => subscribedEntry({
    entitling: false,
    unavailable_reason: 'MARKETPLACE_SUBSCRIPTION_EXPIRED',
    subscription: {
      state: 'EXPIRED',
      period_expiry: '2024-04-01T00:00:00+00:00',
      renewal_state: 'CANCELLED',
    },
    allowed_actions: ['view_listing', 'view_subscription', 'renew'],
    ...overrides,
  });

  it('renders the explicit expired state, naming the server\'s own reason', async () => {
    await mountWith([expired()]);

    const state = screen.getByTestId(`ownership-expired-${SUB_ID}`);
    expect(state.textContent).toMatch(/subscription does not entitle/i);
    expect(state.textContent).toMatch(/this subscription period has ended/i);
    expect(state.textContent).toMatch(/every execution action is disabled/i);
    // Not the other non-entitling state.
    expect(screen.queryByTestId(`ownership-unavailable-strategy-${SUB_ID}`)).toBeNull();
  });

  it('disables every execution action programmatically, each with a text reason', async () => {
    const { container } = await mountWith([expired()]);

    for (const action of EXECUTION) {
      const control = screen.getByTestId(`ownership-disabled-${SUB_ID}-${action}`);
      // Programmatically disabled, both ways: the attribute assistive technology reads and the
      // property that stops the activation.
      expect(control.disabled).toBe(true);
      expect(control.getAttribute('aria-disabled')).toBe('true');
      // The reason is text, and it is reachable from the control.
      const describedBy = control.getAttribute('aria-describedby');
      expect(describedBy).toBeTruthy();
      const reason = container.querySelector(`#${describedBy}`);
      expect(reason).not.toBeNull();
      expect(reason.textContent.trim().length).toBeGreaterThan(20);
      // And it is not also offered as a live control elsewhere.
      expect(screen.queryByTestId(`ownership-action-${SUB_ID}-${action}`)).toBeNull();
      expect(controlsFor(container, action).length).toBe(1);
    }
  });

  it('still offers renewal, enabled, and it reaches the subscription endpoint', async () => {
    mockApi.library.renewSubscription.mockResolvedValue({ status: 'PENDING_PAYMENT' });
    const { user } = await mountWith([expired()]);

    const renew = screen.getByTestId(`ownership-action-${SUB_ID}-renew`);
    expect(renew.disabled).toBe(false);

    await user.click(renew);
    await waitFor(() => expect(mockApi.library.renewSubscription).toHaveBeenCalledWith('sub-3'));
  });

  it('renders the unavailable-strategy state when that is the reason the server gave', async () => {
    await mountWith([
      expired({
        unavailable_reason: 'MARKETPLACE_STRATEGY_UNAVAILABLE',
        allowed_actions: ['view_listing', 'renew'],
      }),
    ]);

    const state = screen.getByTestId(`ownership-unavailable-strategy-${SUB_ID}`);
    expect(state.textContent).toMatch(/strategy unavailable/i);
    expect(state.textContent).toMatch(/cannot be resolved/i);
    expect(screen.queryByTestId(`ownership-expired-${SUB_ID}`)).toBeNull();
    // Still disabled, still renewable.
    for (const action of EXECUTION) {
      expect(screen.getByTestId(`ownership-disabled-${SUB_ID}-${action}`).disabled).toBe(true);
    }
    expect(screen.getByTestId(`ownership-action-${SUB_ID}-renew`)).toBeTruthy();
  });

  it('reports an unrecognised reason code verbatim rather than softening it away', async () => {
    await mountWith([expired({ unavailable_reason: 'MARKETPLACE_SOMETHING_NEW' })]);

    expect(screen.getByTestId(`ownership-expired-${SUB_ID}`).textContent)
      .toContain('MARKETPLACE_SOMETHING_NEW');
  });

  it('leaves an entitling entry\'s execution actions live', async () => {
    // The paired positive: the disabled state above is about entitlement, not about the page
    // disabling execution for every subscribed entry.
    const { container } = await mountWith([subscribedEntry()]);

    expect(screen.queryByTestId(`ownership-expired-${SUB_ID}`)).toBeNull();
    for (const action of EXECUTION) {
      expect(screen.queryByTestId(`ownership-disabled-${SUB_ID}-${action}`)).toBeNull();
      expect(controlsFor(container, action)[0].disabled).toBe(false);
    }
  });
});

// ═════════════════════════════════════════════════════════════════════════════════════════
// 4. Loading, empty, error-with-retry, unauthorised — and never a stale list (Req 12.8)
// ═════════════════════════════════════════════════════════════════════════════════════════

describe('the section\'s states (Requirements 12.8, 28.1)', () => {
  it('renders the loading state while the read is in flight, and no list', async () => {
    let settle;
    mockApi.library.myStrategies.mockReturnValue(new Promise((resolve) => { settle = resolve; }));

    await mountPage();

    const loading = screen.getByTestId('ownership-loading');
    expect(loading.getAttribute('role')).toBe('status');
    expect(screen.queryByTestId('ownership-empty')).toBeNull();
    expect(screen.queryByTestId('ownership-error')).toBeNull();
    // Nothing is claimed about counts that have not been read.
    expect(screen.queryByTestId('ownership-counts')).toBeNull();

    settle(ownershipBody([]));
    await screen.findByTestId('ownership-empty');
  });

  it('renders the empty state only when the read completed', async () => {
    await mountPage();

    const empty = await screen.findByTestId('ownership-empty');
    expect(empty.textContent).toMatch(/own no active strategies and hold no marketplace subscriptions/i);
    expect(screen.queryByTestId('ownership-loading')).toBeNull();
  });

  it('renders error-with-retry, and re-reads on retry', async () => {
    mockApi.library.myStrategies.mockRejectedValueOnce(
      apiError(503, 'The ownership list could not be read.'),
    );
    const { user } = await mountPage();

    const error = await screen.findByTestId('ownership-error');
    expect(error.getAttribute('role')).toBe('alert');
    expect(error.textContent).toContain('The ownership list could not be read.');
    expect(screen.queryByTestId('ownership-unauthorised')).toBeNull();

    mockApi.library.myStrategies.mockResolvedValue(ownershipBody([subscribedEntry()]));
    await user.click(screen.getByTestId('ownership-retry'));

    await waitFor(() => expect(mockApi.library.myStrategies).toHaveBeenCalledTimes(2));
    await screen.findByTestId(`ownership-entry-${SUB_ID}`);
    expect(screen.queryByTestId('ownership-error')).toBeNull();
  });

  it('renders the unauthorised state for a 401 rather than an empty list', async () => {
    mockApi.library.myStrategies.mockRejectedValue(apiError(401, 'Not authenticated.'));
    await mountPage();

    const unauthorised = await screen.findByTestId('ownership-unauthorised');
    expect(unauthorised.textContent).toMatch(/only readable while you are signed in/i);
    expect(screen.queryByTestId('ownership-empty')).toBeNull();
    expect(screen.queryByTestId('ownership-error')).toBeNull();
  });

  it('treats a 403 as unauthorised too, and a response with no items array as an error', async () => {
    mockApi.library.myStrategies.mockRejectedValue(apiError(403, 'Forbidden.'));
    renderPage();
    await screen.findByTestId('ownership-unauthorised');

    // A completed read carrying no `items` array is not an empty list — it is a response this
    // page cannot interpret.
    mockApi.library.myStrategies.mockReset();
    mockApi.library.myStrategies.mockResolvedValue({ total: 3 });
    renderPage();
    const error = await screen.findAllByTestId('ownership-error');
    expect(error[0].textContent).toMatch(/shape this page cannot read/i);
    expect(screen.queryByTestId('ownership-empty')).toBeNull();
  });

  it('drops the previous entries when a re-read fails, leaving the error and no stale list', async () => {
    // The page's own second read: `cancel_renewal` re-reads the list so the rendered renewal
    // state is the server's rather than an optimistic guess.
    mockApi.library.cancelSubscription.mockResolvedValue({
      status: 'CANCELLED',
      message: 'Auto-renewal is off.',
    });
    mockApi.library.myStrategies.mockResolvedValueOnce(ownershipBody([subscribedEntry()]));
    const { user } = await mountPage();

    // It was really on screen first — so the assertion below cannot pass vacuously.
    await screen.findByTestId(`ownership-entry-${SUB_ID}`);
    expect(screen.getByTestId(`ownership-label-${SUB_ID}`).textContent).toBe('SUBSCRIBED');

    mockApi.library.myStrategies.mockRejectedValue(apiError(503, 'Upstream read failed.'));
    await user.click(screen.getByTestId(`ownership-action-${SUB_ID}-cancel_renewal`));

    await waitFor(() => expect(mockApi.library.cancelSubscription).toHaveBeenCalledWith('sub-3'));
    await screen.findByTestId('ownership-error');
    // Requirement 12.8: what renders is the error, not the list nobody knows is current.
    expect(screen.queryByTestId(`ownership-entry-${SUB_ID}`)).toBeNull();
    expect(screen.queryByTestId(`ownership-label-${SUB_ID}`)).toBeNull();
    expect(screen.queryByTestId('ownership-counts')).toBeNull();
    expect(screen.queryByTestId('ownership-empty')).toBeNull();
  });

  it('says the running paper-session count is unavailable rather than showing a zero', async () => {
    await mountWith([subscribedEntry()], { running_paper_sessions_available: false });

    const note = screen.getByTestId('ownership-sessions-unavailable');
    expect(note.textContent).toMatch(/unavailable, not zero/i);
  });

  it('keeps the two reads independent: the owner grid failing does not blank this section', async () => {
    mockApi.strategies.list.mockRejectedValue(new Error('the owner list is down'));
    await mountWith([subscribedEntry()]);

    expect(screen.getByTestId(`ownership-label-${SUB_ID}`).textContent).toBe('SUBSCRIBED');
    expect(screen.queryByTestId('ownership-error')).toBeNull();
  });
});

// ═════════════════════════════════════════════════════════════════════════════════════════
// 5. Controls — the absence assertions above are not vacuous
// ═════════════════════════════════════════════════════════════════════════════════════════

describe('controls: the assertions above can fail', () => {
  it('the [data-action] selector matches a forbidden control when one exists', () => {
    // Identical selector, identical container shape. If the page ever rendered an `edit`
    // affordance for a subscribed entry, this is the match the forbidden-action tests would get
    // instead of an empty list.
    const { container } = render(
      <div>
        <button type="button" data-action="edit" data-testid="ownership-action-e-sub-edit">Edit</button>
      </div>,
    );

    expect(controlsFor(container, 'edit').length).toBe(1);
    expect(offeredActions(container, SUB_ID)).toEqual(['edit']);
  });

  it('offeredActions reports the full set it is given, so an equality failure is visible', () => {
    const { container } = render(
      <div>
        {[...PERMITTED, 'delete'].map((action) => (
          <button
            key={action}
            type="button"
            data-action={action}
            data-testid={`ownership-action-${SUB_ID}-${action}`}
          >
            {action}
          </button>
        ))}
      </div>,
    );

    // Nine, not eight — which is exactly the shape the equality assertion in
    // 'offers exactly the eight permitted actions' would fail with.
    expect(offeredActions(container, SUB_ID)).toEqual([...PERMITTED, 'delete'].sort());
    expect(offeredActions(container, SUB_ID)).not.toEqual([...PERMITTED].sort());
  });
});
