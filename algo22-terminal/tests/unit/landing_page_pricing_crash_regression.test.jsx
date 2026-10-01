/**
 * tests/unit/landing_page_pricing_crash_regression.test.jsx — the INR plan contract.
 *
 * WHAT THIS FILE GUARDS
 * ---------------------
 * The published price list, asserted against the rendered landing section. It began as a
 * `toLocaleString` crash regression suite — the section formatted whatever
 * `GET /api/billing/plans` returned and a null price threw on format — and the figures are now
 * declared in the component, so what is guarded is the contract that replaced the crash: five
 * plans, rupees only, published annual figures, and arithmetic that still cannot throw.
 *
 * WHAT CHANGED WITH THE FIVE-PLAN LADDER, AND WHY EVERY MOVED ASSERTION MOVED
 * -------------------------------------------------------------------------
 * This file previously pinned FOUR tiers — `Free / Sandbox`, `Trader`, `Pro Quant`,
 * `Institutional` — under the heading `Infrastructure Tiers`, with a `Recommended` chip and an
 * annual toggle that applied a 20% reduction computed in the browser. Six of those facts are now
 * different, and none of the changes is cosmetic:
 *
 *   1. **Five plans, not four.** `Business` replaces `Institutional` as the DISPLAY name of the
 *      same ₹2,499 plan (the stored identifier `enterprise` is untouched — see
 *      `backend_app/core/subscription_engine.py`), and a fifth, custom-priced `Enterprise` plan is
 *      added above it. So the card count is 5 and the `/month` count is 4: a quoted plan has no
 *      monthly figure to label.
 *   2. **`Most Popular`, not `Recommended`.** The badge comes from the catalogue's `badge` field.
 *   3. **The annual figures are PUBLISHED, not derived.** The old toggle rendered
 *      `monthly × 12 × 0.8`, which produced ₹4,790 for a plan whose published annual price is
 *      ₹4,990 — the page quoted a number no checkout would charge. The toggle now renders the
 *      published yearly figure and divides it for the per-month equivalent, so ₹4,990 / 12 = ₹416
 *      and the saving works out at 17% (ten months for twelve) rather than a declared 20%.
 *   4. **A capacity table exists**, and the Enterprise column reads `Custom` rather than
 *      `Unlimited`. The backend enforces the Business figure for an Enterprise account with no
 *      contracted capacity recorded, so "Unlimited" would be a promise it does not keep.
 *   5. **Two new anchors** — `#workflow-full` and `#creators` — are scroll targets the page now
 *      renders. A missing id is a navigation control that silently does nothing, which is why the
 *      anchor set is asserted rather than sampled.
 *   6. **`api.billing.getPlans` is still never called**, and that assertion is now stronger than
 *      it was. It used to hold because the endpoint DISAGREED with the published list
 *      (`PricingService` FX-converted the USD base, so ₹999 came back as ~₹865). That defect is
 *      fixed at the root — `FXService.localize_plan_price` charges the published figure — so the
 *      endpoint and this component now agree. The call stays absent because a public price list
 *      should not acquire a loading state or a network dependency, not because the server is wrong.
 *
 * THE RECORD THIS FILE USED TO CARRY
 * ----------------------------------
 * The task 10.1 reproduction record (three outcomes, the refuted `src/pages/Landing.jsx`
 * hypothesis, and the settled `AES-256`-on-a-Fernet-vault content finding) is preserved in
 * `tests/unit/landing/landingSections.test.jsx`'s header, which is where the other two outcomes
 * were already filed. It is not repeated here because none of it concerns the price list, and a
 * reproduction record attached to the wrong subject is a record nobody finds.
 *
 * `tests/test_pricing_ladder.py` asserts the SAME figures against the backend catalogue, from an
 * independent transcription of the published table. The two files are the reason the marketing
 * page and the thing that takes the payment cannot drift apart without a test failing.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LandingPage from '../../src/components/landing/LandingPage';
import Pricing from '../../src/components/landing/Pricing';
import { api } from '../../src/api';

describe('Landing Page & Pricing — INR-only plan contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const renderPricing = () =>
    render(
      <MemoryRouter>
        <Pricing />
      </MemoryRouter>
    );

  describe('The five published plans', () => {
    it('quotes ₹0, ₹499, ₹999 and ₹2,499 per month, and Custom for Enterprise', () => {
      renderPricing();

      expect(screen.getByText('Start Small. Scale When You Need To.')).toBeDefined();
      expect(screen.getByText('₹0')).toBeDefined();
      expect(screen.getByText('₹499')).toBeDefined();
      expect(screen.getByText('₹999')).toBeDefined();
      expect(screen.getByText('₹2,499')).toBeDefined();

      // The quoted plan shows a word, not a figure. `getAllByText` because the capacity table's
      // Enterprise column reads `Custom` on every row too.
      expect(screen.getAllByText('Custom').length).toBeGreaterThan(0);
    });

    it('renders exactly five plan cards, one per published plan', () => {
      const { container } = renderPricing();

      // `data-plan` is the card's own identity attribute, so the count is independent of copy.
      expect(container.querySelectorAll('#pricing [data-plan]')).toHaveLength(5);

      for (const id of ['free', 'starter', 'pro', 'enterprise', 'scale']) {
        expect(
          container.querySelector(`[data-plan="${id}"]`),
          `the ${id} card is missing`,
        ).not.toBeNull();
      }
    });

    it('labels each card with its tier key, so the stored id and the ladder stay linked', () => {
      const { container } = renderPricing();

      // `enterprise` is the BUSINESS tier. This is the assertion that fails if someone "tidies"
      // the identifier to match the display name and silently re-points live subscribers.
      expect(container.querySelector('[data-plan="enterprise"]').dataset.planTier).toBe('BUSINESS');
      expect(container.querySelector('[data-plan="scale"]').dataset.planTier).toBe('ENTERPRISE');
      expect(container.querySelector('[data-plan="starter"]').dataset.planTier).toBe('TRADER');
      expect(container.querySelector('[data-plan="pro"]').dataset.planTier).toBe('PRO_QUANT');
    });

    it('shows four monthly figures — the quoted plan has none to label', () => {
      renderPricing();
      expect(screen.getAllByText('/month')).toHaveLength(4);
    });

    it('marks Pro Quant as the most popular plan', () => {
      const { container } = renderPricing();

      expect(screen.getByText('Most Popular')).toBeDefined();
      // On the Pro Quant card specifically, not merely somewhere in the section.
      expect(
        container.querySelector('[data-plan="pro"]').textContent,
      ).toContain('Most Popular');
    });

    it('names the journey stage above each plan name', () => {
      renderPricing();
      for (const stage of ['Explore', 'Automate', 'Quantify', 'Operate', 'Scale']) {
        expect(screen.getByText(stage), `${stage} is missing`).toBeDefined();
      }
    });
  });

  describe('Rupee is the only currency on the surface', () => {
    it('prints no dollar sign anywhere in the section', () => {
      const { container } = renderPricing();

      expect(container.querySelector('#pricing').textContent).not.toContain('$');
    });

    it('offers no currency selector — there is nothing to switch to', () => {
      renderPricing();

      expect(screen.queryByRole('button', { name: /USD/i })).toBeNull();
      expect(screen.queryByRole('button', { name: /INR/i })).toBeNull();
    });

    it('does not call the billing endpoint for its figures', async () => {
      const getPlans = vi.spyOn(api.billing, 'getPlans').mockResolvedValue({ plans: [] });

      renderPricing();
      // A fetch would land in a microtask after mount, so let the queue drain.
      await Promise.resolve();

      expect(getPlans).not.toHaveBeenCalled();
    });
  });

  describe('Annual billing renders the PUBLISHED yearly price', () => {
    it('quotes ₹4,990, ₹9,990 and ₹24,990 per year, not twelve times the monthly rate', async () => {
      renderPricing();

      fireEvent.click(screen.getByRole('button', { name: /Annual/i }));

      await waitFor(() => {
        expect(screen.getByText(/Billed ₹4,990\/yr/)).toBeDefined();
      });
      expect(screen.getByText(/Billed ₹9,990\/yr/)).toBeDefined();
      expect(screen.getByText(/Billed ₹24,990\/yr/)).toBeDefined();
    });

    it('derives the per-month equivalent from the published yearly figure', async () => {
      renderPricing();

      fireEvent.click(screen.getByRole('button', { name: /Annual/i }));

      await waitFor(() => {
        // 4,990 / 12 = 415.83 → 416. The old 20%-off arithmetic produced ₹399 here, against a
        // yearly price of ₹4,790 that no checkout would ever charge.
        expect(screen.getByText('₹416')).toBeDefined();
      });
      expect(screen.getByText('₹833')).toBeDefined();   // 9,990 / 12
      expect(screen.getByText('₹2,083')).toBeDefined(); // 24,990 / 12
    });

    it('states the saving as 17% — ten months for twelve, computed from both published figures', async () => {
      renderPricing();

      fireEvent.click(screen.getByRole('button', { name: /Annual/i }));

      await waitFor(() => {
        expect(screen.getAllByText(/save 17%/)).toHaveLength(3);
      });
    });

    it('leaves Free at ₹0 and Enterprise quoted, with no annual line on either', async () => {
      renderPricing();

      fireEvent.click(screen.getByRole('button', { name: /Annual/i }));

      await waitFor(() => {
        expect(screen.getByText('₹0')).toBeDefined();
      });
      // Three paid plans carry the billed-at line. Free has no annual price and the custom plan
      // has no published price at all, so neither may show one.
      expect(screen.getAllByText(/Billed ₹/)).toHaveLength(3);
    });

    it('returns to the monthly figures when Monthly is reselected', async () => {
      renderPricing();

      fireEvent.click(screen.getByRole('button', { name: /Annual/i }));
      await waitFor(() => expect(screen.getByText('₹416')).toBeDefined());

      fireEvent.click(screen.getByRole('button', { name: /Monthly/i }));
      await waitFor(() => {
        expect(screen.getByText('₹499')).toBeDefined();
        expect(screen.queryByText(/Billed ₹/)).toBeNull();
      });
    });
  });

  describe('The capacity table states what the backend enforces', () => {
    it('publishes the strategy, marketplace and ML rows the gates enforce', () => {
      renderPricing();

      for (const label of [
        'Active strategies',
        'Paper strategies',
        'Live strategies',
        'Exchange connections',
        'Trading accounts',
        'Backtests',
        'Custom indicators',
        'Strategy versions',
        'ML models',
        'ML training runs',
        'Optimization runs',
        'Marketplace browsing',
        'Marketplace subscriptions',
        'Marketplace listings',
        'Creator revenue share',
      ]) {
        // `getAllByText`, not `getByText`: the four allowances available on every plan appear
        // BOTH as a table row and in the "Included on every plan" list beneath it, which is
        // intentional — the table answers "how much" and the list answers "is it metered at all".
        expect(screen.getAllByText(label).length, `${label} row is missing`).toBeGreaterThan(0);
      }
    });

    it('never advertises an unlimited allowance', () => {
      const { container } = renderPricing();
      const text = container.querySelector('#pricing').textContent;

      // "Unlimited Strategy Bots" was on the old Institutional card and was not implemented.
      expect(text).not.toContain('Unlimited');
      expect(text).not.toContain('unlimited');
    });

    it('does not advertise team, workspace, RBAC or API tiers', () => {
      const { container } = renderPricing();
      const text = container.querySelector('#pricing').textContent;

      // The product is single-user and has no customer API tier, so none of these is a pricing
      // dimension. The old Institutional card listed "Granular Role-Based Access".
      for (const absent of [
        'Team member',
        'team member',
        'Workspace',
        'Role-Based',
        'role-based',
        'RBAC',
        'API access',
        'read-only API',
      ]) {
        expect(text, `"${absent}" must not appear in pricing`).not.toContain(absent);
      }
    });

    it('states that marketplace publishing starts with Pro Quant', () => {
      renderPricing();
      expect(
        screen.getByText(/Marketplace publishing is available to Pro Quant and Business/),
      ).toBeDefined();
    });
  });

  describe('Full Landing Page Integration', () => {
    it('renders entire LandingPage end-to-end without crashing', async () => {
      const { container } = render(
        <MemoryRouter>
          <LandingPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        // `null` is defined, so `toBeDefined()` would pass whether or not the anchor existed.
        // `not.toBeNull()` is what asks the question. Every id below is a scroll target `Navbar`
        // or `Footer` offers, so a missing one is a control that silently does nothing.
        for (const anchor of [
          '#platform',
          '#workflow',
          '#workflow-full',
          '#tour',
          '#marketplace',
          '#creators',
          '#security',
          '#download',
          '#pricing',
          '#faq',
        ]) {
          expect(container.querySelector(anchor), `${anchor} is missing`).not.toBeNull();
        }
        expect(screen.getByText('Start Small. Scale When You Need To.')).toBeDefined();
      });
    });

    it('leads with the Build / Trade / Earn positioning', async () => {
      render(
        <MemoryRouter>
          <LandingPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText(/Build It\. Trade It\./)).toBeDefined();
      });
      expect(screen.getByText('Earn From It.')).toBeDefined();
      expect(
        screen.getByText(/Build your own systematic trading strategies, automate them/),
      ).toBeDefined();
    });

    it('offers the marketplace and the creator economy as first-class sections', async () => {
      render(
        <MemoryRouter>
          <LandingPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText(/Don’t Build Everything From Scratch\./)).toBeDefined();
      });
      expect(screen.getByText('Turn Your Strategy Into Recurring Income')).toBeDefined();
      expect(screen.getByText('From Idea to Trading System')).toBeDefined();

      // The split is stated as a published commercial term. No earnings figure is claimed.
      //
      // `getAllByText`: 90% appears in the creator section's split AND in the pricing table's
      // "Creator revenue share" row, for Pro Quant, Business and Enterprise. The two surfaces
      // quoting one number is the point.
      expect(screen.getAllByText('90%').length).toBeGreaterThan(0);
      expect(screen.getAllByText('10%').length).toBeGreaterThan(0);
    });

    it('qualifies publishing as a Pro Quant and Business capability wherever it is offered', async () => {
      render(
        <MemoryRouter>
          <LandingPage />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(
          screen.getByText('Marketplace publishing is available to Pro Quant and Business users.'),
        ).toBeDefined();
      });
    });

    it('does not read the entitlements endpoint for an anonymous visitor', async () => {
      // `CreatorSection` routes its CTA by entitlement, so it calls `useFeature`. A visitor with
      // no session has no plan to read, and reporting "we could not read your plan" to someone who
      // is not signed in sends them looking for a fault that is not there.
      const getEntitlements = vi
        .spyOn(api.billing, 'getEntitlements')
        .mockResolvedValue({ plan: 'free', features: [] });

      render(
        <MemoryRouter>
          <LandingPage />
        </MemoryRouter>
      );

      await waitFor(() => expect(screen.getByText('Most Popular')).toBeDefined());
      expect(getEntitlements).not.toHaveBeenCalled();
    });

    it('shows no dollar sign anywhere on the landing page', async () => {
      const { container } = render(
        <MemoryRouter>
          <LandingPage />
        </MemoryRouter>
      );

      await waitFor(() =>
        expect(screen.getByText('Start Small. Scale When You Need To.')).toBeDefined(),
      );

      expect(container.textContent).not.toContain('$');
    });
  });
});
