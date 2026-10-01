/**
 * tests/unit/gates/entitlementGates.test.jsx — the frontend gate layer.
 *
 * WHAT IS UNDER TEST, AND WHAT IS EXPLICITLY NOT
 * ----------------------------------------------
 * `FeatureGate`, `UsageLimit`, `UpgradePrompt` and the two hooks decide what to SHOW. They do not
 * decide what is ALLOWED — every gated action is independently refused by
 * `backend_app/core/subscription_dependencies.py`, and `tests/test_saas_entitlements_gating_audit.py`
 * is where that enforcement is asserted. So nothing here proves a permission; what it proves is
 * that the UI renders the server's verdict faithfully and, where it cannot tell, fails toward the
 * locked state.
 *
 * THE THREE PROPERTIES THAT MATTER
 * --------------------------------
 *   1. **The copy comes from the server.** Every sentence and every button label in a locked panel
 *      is read out of `locked_features[feature]` / `limit_refusals[resource]` — the same
 *      `EntitlementRefusal` payload the 403s carry. A test that accepted locally-composed copy
 *      would permit a bundle to quote a price the checkout does not charge.
 *   2. **Failing closed.** Before entitlements are known, and after a read that failed, a gate
 *      renders locked. The two directions of error are not symmetric: a wrongly-locked control
 *      shows a trader an upgrade panel they can question, while a wrongly-unlocked one invites
 *      them to press a button that 403s for reasons they were given no warning of.
 *   3. **An unreadable figure is not a zero.** `0 / 10` is a claim about consumption; "we could not
 *      read this" is the absence of one. They look identical on screen, so `UsageLimit` must render
 *      the server's reason rather than a number it does not have.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { api } from '../../../src/api';
import * as apiModule from '../../../src/api';
import {
  CapacityNotice,
  FeatureGate,
  PlanGate,
  UpgradePrompt,
  UsageLimit,
} from '../../../src/components/gates';
import { clearEntitlements } from '../../../src/hooks/useEntitlements';
import { allowance, hasFeature, overCapacity, readRefusal } from '../../../src/design/entitlements';

/** The refusal shape the backend actually sends, transcribed from `EntitlementRefusal`. */
const ML_REFUSAL = {
  code: 'ML_NOT_INCLUDED',
  message: 'ML Strategy Nodes are available with Pro Quant.',
  plan: 'starter',
  tier: 'TRADER',
  feature: 'ml_training',
  required_plan: 'pro',
  required_tier: 'PRO_QUANT',
  upgrade_message: 'Pro Quant — Build. Optimize. Publish. Earn.',
  cta_label: 'Upgrade to Pro Quant — ₹999',
  contact_sales: false,
};

const STRATEGY_LIMIT_REFUSAL = {
  code: 'STRATEGY_LIMIT_REACHED',
  message: "You've reached your Trader capacity for active strategies.",
  plan: 'starter',
  tier: 'TRADER',
  resource: 'strategies',
  current: 3,
  limit: 3,
  required_plan: 'pro',
  required_tier: 'PRO_QUANT',
  upgrade_message: 'Upgrade to Pro Quant to run up to 10 active strategies.',
  cta_label: 'Upgrade to Pro Quant — ₹999',
  contact_sales: false,
};

const SALES_REFUSAL = {
  code: 'STRATEGY_LIMIT_REACHED',
  message: "You've reached your Business capacity for active strategies.",
  plan: 'enterprise',
  tier: 'BUSINESS',
  resource: 'strategies',
  current: 25,
  limit: 25,
  upgrade_message: 'Contact us for custom capacity.',
  cta_label: 'Talk to Sales',
  contact_sales: true,
};

/** A `/api/billing/entitlements` body for a Trader account at its strategy capacity. */
const traderBody = (overrides = {}) => ({
  plan: 'starter',
  tier: 'TRADER',
  display_name: 'Trader',
  features: [
    'strategy_builder',
    'backtesting',
    'paper_trading',
    'marketplace_browse',
    'live_trading',
    'optimization',
    'marketplace_subscribe',
  ],
  quotas: { strategies: 3, backtests: 100, ml_trainings: 0 },
  usage: { strategies: 3, backtests: 42, ml_trainings: 0 },
  metered_resources: ['backtests', 'optimizations', 'ml_trainings'],
  usage_unavailable: {},
  over_capacity: {},
  locked_features: { ml_training: ML_REFUSAL, marketplace_publish: { ...ML_REFUSAL, code: 'MARKETPLACE_PUBLISH_NOT_INCLUDED', message: 'Marketplace publishing starts with Pro Quant.' } },
  limit_refusals: { strategies: STRATEGY_LIMIT_REFUSAL },
  ...overrides,
});

const mount = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe('Frontend entitlement gates', () => {
  beforeEach(() => {
    clearEntitlements();
    // The hook issues no request for an unauthenticated visitor, so a session is simulated.
    vi.spyOn(apiModule, 'isAuthenticated').mockReturnValue(true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    clearEntitlements();
  });

  // ── 1. The copy is the server's ────────────────────────────────────────────

  describe('UpgradePrompt renders only what the server said', () => {
    it('shows the refusal message, the upgrade sentence and the server CTA', () => {
      mount(<UpgradePrompt refusal={ML_REFUSAL} />);

      expect(screen.getByText(ML_REFUSAL.message)).toBeDefined();
      expect(screen.getByText(ML_REFUSAL.upgrade_message)).toBeDefined();
      expect(screen.getByText(ML_REFUSAL.cta_label)).toBeDefined();
    });

    it('renders nothing at all without a refusal', () => {
      // A bare "Upgrade" with no reason is an advertisement, not an explanation. This component
      // exists instead of one, so it must decline to render rather than invent a prompt.
      const { container } = mount(<UpgradePrompt refusal={null} />);
      expect(container.querySelector('[data-gate="upgrade-prompt"]')).toBeNull();
    });

    it('shows measured usage only when the server supplied both sides of it', () => {
      mount(<UpgradePrompt refusal={STRATEGY_LIMIT_REFUSAL} />);
      expect(screen.getByText('3 / 3 used')).toBeDefined();
    });

    it('omits the usage figure for a zero allowance rather than printing 0 / 0', () => {
      mount(
        <UpgradePrompt
          refusal={{ ...STRATEGY_LIMIT_REFUSAL, current: 0, limit: 0 }}
        />,
      );
      expect(screen.queryByText(/0 \/ 0 used/)).toBeNull();
    });

    it('sends a self-serve upgrade to the billing page', () => {
      mount(<UpgradePrompt refusal={ML_REFUSAL} />);
      // `getAttribute`, not `toHaveAttribute`: this project's vitest config sets `setupFiles: []`,
      // so `@testing-library/jest-dom`'s matchers are not registered.
      expect(
        screen.getByText(ML_REFUSAL.cta_label).closest('a').getAttribute('href'),
      ).toBe('/app/billing');
    });

    it('sends a sales conversation to a mail client, never to a route that does not exist', () => {
      // `App.jsx`'s catch-all redirects an unknown path to `/`, so a "Talk to Sales" link pointing
      // at `/contact` would look functional and silently drop the trader on the landing page.
      mount(<UpgradePrompt refusal={SALES_REFUSAL} />);
      const href = screen.getByText('Talk to Sales').closest('a').getAttribute('href');
      expect(href.startsWith('mailto:')).toBe(true);
    });
  });

  // ── 2. FeatureGate ────────────────────────────────────────────────────────

  describe('FeatureGate', () => {
    it('renders children for a capability the plan includes', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(traderBody());

      mount(
        <FeatureGate feature="live_trading">
          <p>Deploy live</p>
        </FeatureGate>,
      );

      await waitFor(() => expect(screen.getByText('Deploy live')).toBeDefined());
    });

    it('hides children and shows the server refusal for a capability it does not', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(traderBody());

      mount(
        <FeatureGate feature="ml_training">
          <p>Train a model</p>
        </FeatureGate>,
      );

      await waitFor(() => expect(screen.getByText(ML_REFUSAL.message)).toBeDefined());
      expect(screen.queryByText('Train a model')).toBeNull();
    });

    it('fails CLOSED while entitlements are still unknown', () => {
      // Never resolves: the mount-time state, before any answer.
      vi.spyOn(api.billing, 'getEntitlements').mockReturnValue(new Promise(() => {}));

      mount(
        <FeatureGate feature="live_trading">
          <p>Deploy live</p>
        </FeatureGate>,
      );

      expect(screen.queryByText('Deploy live')).toBeNull();
    });

    it('fails CLOSED when the entitlements read failed', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockRejectedValue(new Error('boom'));

      mount(
        <FeatureGate feature="live_trading">
          <p>Deploy live</p>
        </FeatureGate>,
      );

      await waitFor(() =>
        expect(screen.queryByText('Deploy live'), 'a failed read must not unlock').toBeNull(),
      );
    });

    it('renders nothing for variant="hidden" rather than a locked panel', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(traderBody());

      const { container } = mount(
        <FeatureGate feature="ml_training" variant="hidden">
          <p>Train a model</p>
        </FeatureGate>,
      );

      await waitFor(() => expect(screen.queryByText('Train a model')).toBeNull());
      expect(container.querySelector('[data-gate="upgrade-prompt"]')).toBeNull();
    });

    it('renders a disabled control carrying the reason for variant="command"', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(traderBody());

      mount(
        <FeatureGate feature="marketplace_publish" variant="command" commandLabel="Publish">
          <p>unused</p>
        </FeatureGate>,
      );

      await waitFor(() => expect(screen.getByText('Publish')).toBeDefined());
      // `ds/CommandButton` renders `disabledReason` as visible text, because a disabled button can
      // be neither hovered nor focused — a `title` attribute would reach nobody.
      expect(screen.getByText(/Marketplace publishing starts with Pro Quant/)).toBeDefined();
    });
  });

  // ── 3. UsageLimit ─────────────────────────────────────────────────────────

  describe('UsageLimit', () => {
    it('renders a measured allowance as used over limit', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(traderBody());

      mount(<UsageLimit resource="backtests" />);

      await waitFor(() => expect(screen.getByText('42')).toBeDefined());
      expect(screen.getByText('/ 100')).toBeDefined();
    });

    it('labels a monthly allowance as monthly', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(traderBody());

      mount(<UsageLimit resource="backtests" />);

      // A monthly figure read as a lifetime total understates consumption by an order of
      // magnitude, which is why the period is part of the label and not a footnote.
      await waitFor(() => expect(screen.getByText('this month')).toBeDefined());
    });

    it('does not label a counted allowance as monthly', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(traderBody());

      mount(<UsageLimit resource="strategies" />);

      await waitFor(() => expect(screen.getByText('/ 3')).toBeDefined());
      expect(screen.queryByText('this month')).toBeNull();
    });

    it('renders the server reason instead of a number when the figure is unreadable', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(
        traderBody({
          usage: { backtests: null },
          usage_unavailable: { backtests: 'This figure could not be read.' },
        }),
      );

      mount(<UsageLimit resource="backtests" />);

      await waitFor(() => expect(screen.getByText('Not available')).toBeDefined());
      // The whole point: no fabricated zero beside the limit.
      expect(screen.queryByText('0')).toBeNull();
    });

    it('shows the server upgrade prompt once an allowance is exhausted', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(traderBody());

      mount(<UsageLimit resource="strategies" />);

      await waitFor(() =>
        expect(screen.getByText(STRATEGY_LIMIT_REFUSAL.message)).toBeDefined(),
      );
    });

    it('draws no bar for an allowance with no measurable ratio', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(
        traderBody({ quotas: { strategies: -1 }, usage: { strategies: 4 } }),
      );

      const { container } = mount(<UsageLimit resource="strategies" />);

      await waitFor(() => expect(screen.getByText('/ Custom')).toBeDefined());
      // A bar at an invented width would be a claim about capacity nobody published.
      expect(container.querySelector('[role="progressbar"]')).toBeNull();
    });
  });

  // ── 4. CapacityNotice and PlanGate ────────────────────────────────────────

  describe('CapacityNotice', () => {
    it('renders nothing while the allowance has room', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(
        traderBody({ limit_refusals: {} }),
      );

      const { container } = mount(<CapacityNotice resource="strategies" />);

      await waitFor(() => expect(container.textContent).toBe(''));
    });

    it('renders the refusal once the server reports the allowance spent', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(traderBody());

      mount(<CapacityNotice resource="strategies" />);

      await waitFor(() =>
        expect(screen.getByText(STRATEGY_LIMIT_REFUSAL.message)).toBeDefined(),
      );
    });
  });

  describe('PlanGate', () => {
    it('admits an account at or above the requested tier', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(traderBody());

      mount(
        <PlanGate minimumTier="TRADER">
          <p>Paid content</p>
        </PlanGate>,
      );

      await waitFor(() => expect(screen.getByText('Paid content')).toBeDefined());
    });

    it('refuses an account below it', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(traderBody());

      mount(
        <PlanGate minimumTier="PRO_QUANT" fallback={<p>Locked</p>}>
          <p>Paid content</p>
        </PlanGate>,
      );

      await waitFor(() => expect(screen.getByText('Locked')).toBeDefined());
      expect(screen.queryByText('Paid content')).toBeNull();
    });

    it('fails closed on an unrecognised tier on either side', async () => {
      vi.spyOn(api.billing, 'getEntitlements').mockResolvedValue(
        traderBody({ tier: 'NOT_A_TIER' }),
      );

      mount(
        <PlanGate minimumTier="TRADER" fallback={<p>Locked</p>}>
          <p>Paid content</p>
        </PlanGate>,
      );

      await waitFor(() => expect(screen.getByText('Locked')).toBeDefined());
    });
  });

  // ── 5. One shared read ────────────────────────────────────────────────────

  describe('The shared entitlement read', () => {
    it('issues ONE request for many gates mounted together', async () => {
      const getEntitlements = vi
        .spyOn(api.billing, 'getEntitlements')
        .mockResolvedValue(traderBody());

      mount(
        <>
          <FeatureGate feature="live_trading" variant="hidden"><p>a</p></FeatureGate>
          <FeatureGate feature="ml_training" variant="hidden"><p>b</p></FeatureGate>
          <FeatureGate feature="optimization" variant="hidden"><p>c</p></FeatureGate>
          <UsageLimit resource="strategies" />
          <UsageLimit resource="backtests" />
          <CapacityNotice resource="strategies" />
        </>,
      );

      await waitFor(() => expect(screen.getByText('a')).toBeDefined());
      // Six consumers, one read. Four surfaces used to call this endpoint independently, each able
      // to show a different plan than the others for as long as they disagreed.
      expect(getEntitlements).toHaveBeenCalledTimes(1);
    });

    it('issues no request at all for an unauthenticated visitor', async () => {
      vi.spyOn(apiModule, 'isAuthenticated').mockReturnValue(false);
      const getEntitlements = vi
        .spyOn(api.billing, 'getEntitlements')
        .mockResolvedValue(traderBody());

      mount(
        <FeatureGate feature="live_trading" variant="hidden">
          <p>Deploy live</p>
        </FeatureGate>,
      );

      await Promise.resolve();
      expect(getEntitlements).not.toHaveBeenCalled();
      expect(screen.queryByText('Deploy live')).toBeNull();
    });
  });

  // ── 6. The pure readers ───────────────────────────────────────────────────

  describe('design/entitlements readers', () => {
    it('hasFeature fails closed on a malformed payload', () => {
      expect(hasFeature(null, 'live_trading')).toBe(false);
      expect(hasFeature({}, 'live_trading')).toBe(false);
      expect(hasFeature({ features: 'live_trading' }, 'live_trading')).toBe(false);
      expect(hasFeature({ features: ['live_trading'] }, 'live_trading')).toBe(true);
    });

    it('allowance distinguishes an exhausted allowance from one not included', () => {
      const body = traderBody({
        quotas: { strategies: 3, ml_trainings: 0 },
        usage: { strategies: 3, ml_trainings: 0 },
      });

      const strategies = allowance(body, 'strategies');
      expect(strategies.exhausted).toBe(true);
      expect(strategies.notIncluded).toBe(false);

      const ml = allowance(body, 'ml_trainings');
      expect(ml.notIncluded).toBe(true);
    });

    it('allowance never reports exhausted from an unreadable figure', () => {
      const body = traderBody({
        usage: {},
        usage_unavailable: { strategies: 'unreadable' },
      });
      const entry = allowance(body, 'strategies');
      expect(entry.used.available).toBe(false);
      expect(entry.exhausted).toBe(false);
      expect(entry.ratio).toBeNull();
    });

    it('overCapacity reads the server block and ignores malformed entries', () => {
      const body = traderBody({
        over_capacity: {
          strategies: { current: 10, limit: 3 },
          bots: { current: 'lots', limit: 3 },
        },
      });
      const items = overCapacity(body);
      expect(items).toHaveLength(1);
      expect(items[0]).toMatchObject({ resource: 'strategies', current: 10, limit: 3 });
    });

    it('readRefusal finds the payload on both error envelopes and ignores ordinary errors', () => {
      expect(readRefusal({ data: { detail: ML_REFUSAL } })).toEqual(ML_REFUSAL);
      expect(readRefusal({ response: { data: { detail: ML_REFUSAL } } })).toEqual(ML_REFUSAL);
      // A 500 or a validation error is not an upgrade opportunity.
      expect(readRefusal({ data: { detail: 'Internal error' } })).toBeNull();
      expect(readRefusal(new Error('network'))).toBeNull();
      expect(readRefusal(null)).toBeNull();
    });
  });
});
