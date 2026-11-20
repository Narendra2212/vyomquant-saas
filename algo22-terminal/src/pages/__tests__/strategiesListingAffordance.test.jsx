/**
 * MKT-07 / Task 15 B — the Marketplace listing control on an owned strategy row.
 *
 * The brief is specific about the shape, and the specificity is the point:
 *
 *   > "The control must be visible to all subscription tiers. However: Free users: visible but
 *   >  disabled. Trader-tier users: visible but disabled. Eligible higher-tier users: enabled
 *   >  subject to all other publishing requirements. Show a clear reason when the button is
 *   >  disabled. Enforce eligibility on the backend. Do not rely on frontend-only
 *   >  restrictions."
 *
 * So three facts need asserting, and the first two are what a "hide it unless permitted"
 * implementation would get wrong:
 *
 *   1. The control is RENDERED on a plan that cannot publish. A hidden control teaches nobody
 *      that the capability exists.
 *   2. It is programmatically disabled — `disabled`, so neither a click nor a keyboard
 *      activation reaches the handler — and carries the reason as its accessible title.
 *   3. It is enabled on a plan that can publish, which is what stops (1) and (2) from being
 *      satisfied by a control that is simply always dead.
 *
 * WHAT IS MOCKED, AND WHY IT IS NOT THE THING UNDER TEST
 * -----------------------------------------------------
 * `../../api` (the shared module), `../StrategyBuilder` (a heavy tree this page swaps the whole
 * view for) and `../../hooks/useEntitlements` — the last because the entitlement READ is not
 * under test here; what the page does with the answer is. `components/gates/FeatureGate`'s
 * `useFeature` and `design/entitlements.hasFeature` are real, so the shape of the body this
 * test supplies is the shape the server actually sends.
 *
 * THE AUTHORISATION IS NOT HERE, AND THAT IS DELIBERATE
 * ----------------------------------------------------
 * `require_marketplace_publish` on `POST /api/library/submissions`, `POST /api/library` and the
 * price routes is what refuses a caller who reaches them another way, and
 * `check_marketplace_publish_quota` is what refuses one who is over their listing allowance.
 * This suite asserts an affordance. It would be a mistake to read a passing run here as
 * evidence that publishing is gated.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { FEATURES } from '../../design/entitlements';

const { mockApi, mockEntitlements } = vi.hoisted(() => {
  const fns = (names) => Object.fromEntries(names.map((n) => [n, vi.fn()]));
  return {
    mockApi: {
      library: fns(['myStrategies', 'renewSubscription', 'cancelSubscription', 'detail', 'clone']),
      strategies: fns([
        'list',
        'delete',
        'rename',
        'clone',
        'pause',
        'deployVersion',
        'deployPreflight',
      ]),
      exchange: fns(['list']),
    },
    mockEntitlements: { body: null, isReady: false },
  };
});

vi.mock('../../api', () => ({
  default: mockApi,
  api: mockApi,
  endpoints: mockApi,
  isAuthenticated: () => true,
  publicGet: vi.fn(async () => ({ live_trading_available: false })),
}));

vi.mock('../StrategyBuilder', () => ({ default: () => <div data-testid="builder-stub" /> }));

vi.mock('../../hooks/useEntitlements', () => ({
  useEntitlements: () => mockEntitlements,
  refreshEntitlements: vi.fn(),
  default: () => mockEntitlements,
}));

import Strategies from '../Strategies';

/** One owned strategy, enough for the row to render its action cell. */
const OWNED = {
  id: 'strat-1',
  name: 'EMA cross',
  status: 'draft',
  symbol: 'BTC/USDT',
  timeframe: '1h',
  exchange_id: 'binance',
  version: 1,
};

/**
 * The server's entitlements envelope. `features` is the list `hasFeature` reads, and
 * `locked_features` is the structured refusal `UpgradePrompt` and this page's tooltip read —
 * both spelled as `routers/billing.py` sends them rather than invented here.
 */
const entitlements = ({ canPublish }) => ({
  body: {
    features: canPublish ? [FEATURES.MARKETPLACE_PUBLISH] : [],
    locked_features: canPublish
      ? {}
      : {
          [FEATURES.MARKETPLACE_PUBLISH]: {
            code: 'MARKETPLACE_PUBLISH_NOT_INCLUDED',
            message: 'Publishing to the marketplace is not included in your plan.',
            upgrade_message: 'Upgrade to Pro to list strategies.',
            required_plan: 'pro',
          },
        },
    limit_refusals: {},
  },
  isReady: true,
});

const listingControl = () => screen.queryByRole('button', { name: /list on marketplace/i });

const renderPage = () =>
  render(
    <MemoryRouter>
      <Strategies />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.strategies.list.mockResolvedValue({ strategies: [OWNED] });
  mockApi.library.myStrategies.mockResolvedValue({ entries: [], meta: {} });
  mockApi.exchange.list.mockResolvedValue({ exchanges: [] });
  Object.assign(mockEntitlements, { body: null, isReady: false });
});

describe('Task 15 B: the listing control on an owned strategy row', () => {
  it('is rendered, and disabled with the server’s reason, on a plan that cannot publish', async () => {
    Object.assign(mockEntitlements, entitlements({ canPublish: false }));
    renderPage();

    const control = await waitFor(() => {
      const found = listingControl();
      expect(found).not.toBeNull();
      return found;
    });

    // Visible to every tier — the half a "hide unless permitted" implementation gets wrong.
    expect(control).toBeDefined();
    // Programmatically disabled, so neither pointer nor keyboard reaches the handler.
    expect(control.disabled).toBe(true);
    // The reason is the SERVER's sentence, composed the way UpgradePrompt's `command` variant
    // composes it — so the plan named here cannot drift from the plan the 403 would name.
    expect(control.getAttribute('title')).toMatch(/not included in your plan/i);
    expect(control.getAttribute('title')).toMatch(/Upgrade to Pro/i);
  });

  it('is enabled on a plan that can publish', async () => {
    Object.assign(mockEntitlements, entitlements({ canPublish: true }));
    renderPage();

    const control = await waitFor(() => {
      const found = listingControl();
      expect(found).not.toBeNull();
      return found;
    });

    // Without this case, the two assertions above would also pass for a control that is
    // permanently dead.
    expect(control.disabled).toBe(false);
    expect(control.getAttribute('title')).toBeNull();
  });

  it('says the plan is still being read rather than naming a refusal it has not got', async () => {
    // `useFeature` reports `permitted: false` until entitlements are KNOWN, which is the right
    // direction: a control that looks enabled before its plan is known is a control that 403s.
    // What it must not do is show an upgrade sentence it has no answer for yet.
    Object.assign(mockEntitlements, { body: null, isReady: false });
    renderPage();

    const control = await waitFor(() => {
      const found = listingControl();
      expect(found).not.toBeNull();
      return found;
    });

    expect(control.disabled).toBe(true);
    expect(control.getAttribute('title')).toMatch(/checking what your plan includes/i);
    expect(control.getAttribute('title')).not.toMatch(/upgrade/i);
  });
});
