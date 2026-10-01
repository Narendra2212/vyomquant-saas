/**
 * tests/unit/landing_page_pricing_crash_regression.test.jsx — the published plan contract, in
 * every currency the page can show it in.
 *
 * WHAT THIS FILE GUARDS
 * ---------------------
 * The published price list, asserted against the rendered landing section: five plans, the rupee
 * figures ₹0 / ₹499 / ₹999 / ₹2,499 and a quoted fifth tier, published annual prices rather than
 * derived ones, and arithmetic that still cannot throw. It began as a `toLocaleString` crash
 * regression suite — the section formatted whatever `GET /api/billing/plans` returned and a null
 * price threw on format — and that is why the no-throw assertions are here.
 *
 * WHAT CHANGED WITH THE SINGLE-BASE-CURRENCY FIX, AND WHY EACH ASSERTION MOVED
 * ---------------------------------------------------------------------------
 * This file previously pinned the section as INR-ONLY: no currency selector, no network read, and
 * no dollar sign anywhere under `#pricing`. Three of those facts are now different, and the reason
 * is a real defect, not a redesign:
 *
 *   1. **The catalogue published TWO base price columns**, INR and USD, about 4% apart, and every
 *      other currency was derived from the DOLLAR one. So the marketing page advertised the rupee
 *      value point while every non-Indian visitor was quoted the dollar value point — two prices
 *      for one plan. `subscription_engine.PRICE_BASE_CURRENCY` now makes the rupee list the single
 *      published price and `FXService.localize_plan_price` converts it; there is one value point.
 *   2. **The hardcoded rupee list was wrong for most of the world.** A visitor in Berlin was
 *      quoted ₹499, which is neither a number they can price a decision on nor what checkout would
 *      charge them. The section now reads the public catalogue, which resolves the currency from
 *      the request's own geography, and offers a selector.
 *   3. **The two surfaces disagreed.** The authenticated billing page DID localise, so the same
 *      plan carried two different-looking prices on two surfaces of the same product. Both now
 *      read the same endpoint.
 *
 * So `#pricing` may now contain a dollar sign — when the visitor is being shown dollars. What is
 * asserted instead, and is the stronger claim, is that **a figure and its symbol always agree**:
 * the rupee constants are rendered only under `₹`, a card awaiting a converted figure shows a
 * placeholder rather than a rupee number wearing another currency's symbol, and a currency the
 * server has not priced yet relabels nothing.
 *
 * WHICH TRANSPORT IS USED, AND WHY IT IS ASSERTED
 * -----------------------------------------------
 * `api.billing.getPublicPlans`, never `api.billing.getPlans`. The endpoint is public, but the
 * authenticated transport attaches whatever token is in `sessionStorage` and routes a 401 into the
 * shell's `auth:expired` handler — so reading a price list as an anonymous visitor would raise a
 * sign-out prompt. `getPlans` being absent from this surface is therefore a behavioural assertion,
 * not a stylistic one.
 *
 * THE RECORD THIS FILE USED TO CARRY
 * ----------------------------------
 * The task 10.1 reproduction record (three outcomes, the refuted `src/pages/Landing.jsx`
 * hypothesis, and the settled `AES-256`-on-a-Fernet-vault content finding) is preserved in
 * `tests/unit/landing/landingSections.test.jsx`'s header, which is where the other two outcomes
 * were already filed.
 *
 * `tests/test_pricing_ladder.py` asserts the SAME rupee figures against the backend catalogue,
 * from an independent transcription of the published table, and asserts that every other currency
 * is a conversion of exactly that list. The two files are the reason the marketing page and the
 * thing that takes the payment cannot drift apart without a test failing.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LandingPage from '../../src/components/landing/LandingPage';
import Pricing from '../../src/components/landing/Pricing';
import { api } from '../../src/api';

/**
 * A `GET /api/billing/plans` response, in the shape `PricingService.get_localized_plans` returns.
 *
 * Only the fields the component reads are built, and they are named exactly as the server names
 * them — a typo here would make this suite pass against a payload the real endpoint never sends.
 * `prices` maps plan id to `[monthly, annualTotal, monthlyEquivalent]` in MAJOR units, which is
 * what `localized_price` carries.
 */
const catalogue = ({ currency, symbol, decimals = 2, prices, supported = null }) => ({
  country: currency === 'INR' ? 'IN' : 'US',
  currency,
  currency_symbol: symbol,
  base_currency: 'INR',
  base_currency_symbol: '₹',
  supported_currencies: supported,
  plans: Object.entries(prices).map(([id, [monthly, annualTotal, monthlyEquivalent]]) => ({
    id,
    currency,
    currency_symbol: symbol,
    decimals,
    base_currency: 'INR',
    localized_price: monthly,
    price_source: currency === 'INR' ? 'published' : 'fx',
    annual:
      annualTotal == null
        ? null
        : {
            localized_price: annualTotal,
            monthly_equivalent: monthlyEquivalent,
            savings_percent: 17,
            price_source: currency === 'INR' ? 'published' : 'fx',
          },
  })),
});

/** The dollar localisation of the published rupee list at the recorded baseline rate. */
const USD_CATALOGUE = catalogue({
  currency: 'USD',
  symbol: '$',
  prices: {
    free: [0, null, null],
    starter: [5.2, 51.98, 4.33],
    pro: [10.41, 104.07, 8.67],
    enterprise: [26.03, 260.32, 21.69],
    scale: [0, null, null],
  },
});

/** The same list in euros, used to prove a re-read actually re-prices. */
const EUR_CATALOGUE = catalogue({
  currency: 'EUR',
  symbol: '€',
  prices: {
    free: [0, null, null],
    starter: [4.58, 45.81, 3.82],
    pro: [9.18, 91.76, 7.65],
    enterprise: [22.96, 229.49, 19.12],
    scale: [0, null, null],
  },
});

describe('Landing Page & Pricing — the published plan contract', () => {
  /** The spy every test gets; individual tests re-point it. */
  let getPublicPlans;

  beforeEach(() => {
    vi.restoreAllMocks();
    // The DEFAULT is a failed read, so the suite's baseline is the offline fallback: the published
    // rupee list, labelled in rupees. A test that wants a localised catalogue says so.
    getPublicPlans = vi
      .spyOn(api.billing, 'getPublicPlans')
      .mockRejectedValue(new Error('offline'));
  });

  const renderPricing = () =>
    render(
      <MemoryRouter>
        <Pricing />
      </MemoryRouter>
    );

  const pricingText = (container) => container.querySelector('#pricing').textContent;

  describe('The five published plans', () => {
    it('quotes ₹0, ₹499, ₹999 and ₹2,499 per month, and Custom for Enterprise', async () => {
      renderPricing();

      expect(screen.getByText('Start Small. Scale When You Need To.')).toBeDefined();
      await waitFor(() => expect(screen.getByText('₹499')).toBeDefined());
      expect(screen.getByText('₹0')).toBeDefined();
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

  describe('Rupees are the published list, and the fallback when the read fails', () => {
    it('reads the PUBLIC catalogue, never the authenticated one', async () => {
      const getPlans = vi.spyOn(api.billing, 'getPlans').mockResolvedValue({ plans: [] });

      renderPricing();
      await waitFor(() => expect(getPublicPlans).toHaveBeenCalled());

      // `getPlans` routes a 401 into the shell's sign-out handler. An anonymous visitor reading a
      // price list must never be able to trigger that.
      expect(getPlans).not.toHaveBeenCalled();
    });

    it('asks for no particular currency first, so the server resolves it from geography', async () => {
      renderPricing();

      await waitFor(() => expect(getPublicPlans).toHaveBeenCalledTimes(1));
      expect(getPublicPlans).toHaveBeenCalledWith(undefined);
    });

    it('falls back to the published rupee list when the read fails, and says it is rupees', async () => {
      const { container } = renderPricing();

      await waitFor(() => expect(getPublicPlans).toHaveBeenCalled());
      expect(screen.getByText('₹499')).toBeDefined();
      expect(screen.getByText(/All prices in Indian Rupees/)).toBeDefined();
      // No fabricated conversion: with no catalogue in hand there is no other currency on screen.
      expect(pricingText(container)).not.toContain('$');
    });

    it('renders rupee figures without a loading placeholder — they are already known', async () => {
      const { container } = renderPricing();

      await waitFor(() => expect(getPublicPlans).toHaveBeenCalled());
      expect(container.querySelectorAll('#pricing [role="status"][aria-label^="Loading"]'))
        .toHaveLength(0);
    });
  });

  describe('The visitor is shown their own currency', () => {
    it('renders the localized figures the server sent, under the server\'s symbol', async () => {
      getPublicPlans.mockResolvedValue(USD_CATALOGUE);
      renderPricing();

      await waitFor(() => expect(screen.getByText('$5.20')).toBeDefined());
      expect(screen.getByText('$10.41')).toBeDefined();
      expect(screen.getByText('$26.03')).toBeDefined();
      expect(screen.getByText('$0')).toBeDefined();
    });

    it('does not leave a rupee figure on screen once dollars have been served', async () => {
      getPublicPlans.mockResolvedValue(USD_CATALOGUE);
      const { container } = renderPricing();

      await waitFor(() => expect(screen.getByText('$5.20')).toBeDefined());
      // The whole defect, in one assertion: the published rupee constants must not survive into a
      // dollar rendering. ₹ still appears in the disclosure sentence below, by design.
      for (const published of ['₹499', '₹999', '₹2,499']) {
        expect(pricingText(container), `${published} is still on screen`).not.toContain(published);
      }
    });

    it('says the figures are converted, and names the currency charged', async () => {
      getPublicPlans.mockResolvedValue(USD_CATALOGUE);
      renderPricing();

      await waitFor(() =>
        expect(
          screen.getByText(/published in Indian Rupees .*and shown in USD at today’s exchange rate/),
        ).toBeDefined(),
      );
      expect(screen.getByText(/You are charged in USD/)).toBeDefined();
    });

    it('quotes the same figure in the CTA as on the card', async () => {
      getPublicPlans.mockResolvedValue(USD_CATALOGUE);
      const { container } = renderPricing();

      await waitFor(() => expect(screen.getByText('$5.20')).toBeDefined());
      // It used to hardcode the rupee constant, which under a dollar heading read
      // "Start Automating — ₹499" beside "$5.20".
      expect(container.querySelector('[data-plan="starter"]').textContent)
        .toContain('Start Automating — $5.20');
      // Free is free in every currency, so its CTA carries no figure at all.
      expect(container.querySelector('[data-plan="free"]').textContent)
        .not.toContain('$0 ');
    });

    it('shows a placeholder, not a rupee constant, for a plan the catalogue did not price', async () => {
      // `pro` omitted entirely: the server answered, but not about this plan.
      getPublicPlans.mockResolvedValue(
        catalogue({
          currency: 'USD',
          symbol: '$',
          prices: { free: [0, null, null], starter: [5.2, 51.98, 4.33] },
        }),
      );
      const { container } = renderPricing();

      await waitFor(() => expect(screen.getByText('$5.20')).toBeDefined());

      const pro = container.querySelector('[data-plan="pro"]');
      expect(pro.querySelector('[role="status"][aria-label="Loading the Pro Quant price in USD"]'))
        .not.toBeNull();
      expect(pro.textContent).not.toContain('999');
    });
  });

  describe('The currency selector', () => {
    const selector = () => screen.getByRole('combobox');

    it('offers a currency selector showing the currency on screen', async () => {
      getPublicPlans.mockResolvedValue(USD_CATALOGUE);
      renderPricing();

      await waitFor(() => expect(selector().value).toBe('USD'));
      expect(screen.getByText('Display currency')).toBeDefined();
    });

    it('offers the server\'s own currency list when it sends one', async () => {
      getPublicPlans.mockResolvedValue(
        catalogue({
          currency: 'INR',
          symbol: '₹',
          prices: { starter: [499, 4990, 416] },
          supported: [
            { code: 'INR', symbol: '₹' },
            { code: 'BRL', symbol: 'R$' },
          ],
        }),
      );
      renderPricing();

      await waitFor(() =>
        expect([...selector().options].map((o) => o.value)).toEqual(['INR', 'BRL']),
      );
    });

    it('falls back to a built-in currency list when the server sends none', async () => {
      renderPricing();

      await waitFor(() => expect(getPublicPlans).toHaveBeenCalled());
      const codes = [...selector().options].map((o) => o.value);
      expect(codes).toContain('INR');
      expect(codes).toContain('USD');
      expect(codes).toContain('EUR');
    });

    it('re-reads the catalogue in the chosen currency', async () => {
      getPublicPlans.mockResolvedValue(USD_CATALOGUE);
      renderPricing();
      await waitFor(() => expect(screen.getByText('$5.20')).toBeDefined());

      getPublicPlans.mockResolvedValue(EUR_CATALOGUE);
      fireEvent.change(selector(), { target: { value: 'EUR' } });

      await waitFor(() => expect(getPublicPlans).toHaveBeenLastCalledWith('EUR'));
      await waitFor(() => expect(screen.getByText('€4.58')).toBeDefined());
      expect(screen.getByText('€9.18')).toBeDefined();
      expect(screen.getByText('€22.96')).toBeDefined();
    });

    it('relabels nothing while the chosen currency is still being priced', async () => {
      getPublicPlans.mockResolvedValue(USD_CATALOGUE);
      const { container } = renderPricing();
      await waitFor(() => expect(screen.getByText('$5.20')).toBeDefined());

      // A read that never answers, so the awaiting state can be observed.
      getPublicPlans.mockReturnValue(new Promise(() => {}));
      fireEvent.change(selector(), { target: { value: 'EUR' } });

      await waitFor(() => expect(screen.getByText(/Converting prices to EUR/)).toBeDefined());
      // The dollar figures are GONE rather than relabelled: showing `$5.20` under a sentence
      // reading "shown in EUR" is the same class of mistake as the hardcoded rupee list.
      expect(pricingText(container)).not.toContain('5.20');
      expect(pricingText(container)).not.toContain('₹499');
      expect(
        container.querySelectorAll('#pricing [role="status"][aria-label^="Loading"]'),
      ).toHaveLength(4);
      // The control acknowledges the pick immediately, so it does not look unresponsive.
      expect(selector().value).toBe('EUR');
    });

    it('snaps to the currency the server actually answered in', async () => {
      getPublicPlans.mockResolvedValue(USD_CATALOGUE);
      renderPricing();
      await waitFor(() => expect(screen.getByText('$5.20')).toBeDefined());

      // The visitor asks for a currency the server does not quote, so it answers in dollars again
      // (`FXService` has no rate for it, or declines to charge in it). The selector must agree with
      // the figures rather than wait forever for a price list that is never coming.
      fireEvent.change(selector(), { target: { value: 'CAD' } });

      await waitFor(() => expect(getPublicPlans).toHaveBeenLastCalledWith('CAD'));
      await waitFor(() => expect(selector().value).toBe('USD'));
      expect(screen.getByText('$5.20')).toBeDefined();
    });

    it('re-reads when the same currency is chosen again, so a failed read can be retried', async () => {
      getPublicPlans.mockRejectedValue(new Error('offline'));
      renderPricing();
      await waitFor(() => expect(getPublicPlans).toHaveBeenCalledTimes(1));

      fireEvent.change(selector(), { target: { value: 'EUR' } });
      await waitFor(() => expect(getPublicPlans).toHaveBeenCalledTimes(2));
      // The failed read put the selector back on INR, so choosing EUR again is the retry. Keying
      // the read on the currency alone would make this a no-op and the control dead.
      await waitFor(() => expect(selector().value).toBe('INR'));

      getPublicPlans.mockResolvedValue(EUR_CATALOGUE);
      fireEvent.change(selector(), { target: { value: 'EUR' } });

      await waitFor(() => expect(screen.getByText('€4.58')).toBeDefined());
      expect(getPublicPlans).toHaveBeenCalledTimes(3);
    });

    it('keeps the figures it has when a re-read fails', async () => {
      getPublicPlans.mockResolvedValue(USD_CATALOGUE);
      renderPricing();
      await waitFor(() => expect(screen.getByText('$5.20')).toBeDefined());

      getPublicPlans.mockRejectedValue(new Error('offline'));
      fireEvent.change(selector(), { target: { value: 'EUR' } });

      await waitFor(() => expect(getPublicPlans).toHaveBeenLastCalledWith('EUR'));
      // Blanking a correct price because a re-read failed would be worse than showing the last
      // thing the server said.
      expect(screen.getByText('$5.20')).toBeDefined();
      expect(screen.getByRole('combobox').value).toBe('USD');
    });
  });

  describe('Annual billing renders the PUBLISHED yearly price', () => {
    const goAnnual = () => fireEvent.click(screen.getByRole('button', { name: /Annual/i }));

    it('quotes ₹4,990, ₹9,990 and ₹24,990 per year, not twelve times the monthly rate', async () => {
      renderPricing();
      await waitFor(() => expect(getPublicPlans).toHaveBeenCalled());

      goAnnual();

      await waitFor(() => {
        expect(screen.getByText(/Billed ₹4,990\/yr/)).toBeDefined();
      });
      expect(screen.getByText(/Billed ₹9,990\/yr/)).toBeDefined();
      expect(screen.getByText(/Billed ₹24,990\/yr/)).toBeDefined();
    });

    it('derives the per-month equivalent from the published yearly figure', async () => {
      renderPricing();
      await waitFor(() => expect(getPublicPlans).toHaveBeenCalled());

      goAnnual();

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
      await waitFor(() => expect(getPublicPlans).toHaveBeenCalled());

      goAnnual();

      await waitFor(() => {
        expect(screen.getAllByText(/save 17%/)).toHaveLength(3);
      });
    });

    it('leaves Free at ₹0 and Enterprise quoted, with no annual line on either', async () => {
      renderPricing();
      await waitFor(() => expect(getPublicPlans).toHaveBeenCalled());

      goAnnual();

      await waitFor(() => {
        expect(screen.getByText('₹0')).toBeDefined();
      });
      // Three paid plans carry the billed-at line. Free has no annual price and the custom plan
      // has no published price at all, so neither may show one.
      expect(screen.getAllByText(/Billed ₹/)).toHaveLength(3);
    });

    it('returns to the monthly figures when Monthly is reselected', async () => {
      renderPricing();
      await waitFor(() => expect(getPublicPlans).toHaveBeenCalled());

      goAnnual();
      await waitFor(() => expect(screen.getByText('₹416')).toBeDefined());

      fireEvent.click(screen.getByRole('button', { name: /Monthly/i }));
      await waitFor(() => {
        expect(screen.getByText('₹499')).toBeDefined();
        expect(screen.queryByText(/Billed ₹/)).toBeNull();
      });
    });

    it('uses the SERVER\'s monthly equivalent in a converted currency, not its own division', async () => {
      getPublicPlans.mockResolvedValue(USD_CATALOGUE);
      renderPricing();
      await waitFor(() => expect(screen.getByText('$5.20')).toBeDefined());

      goAnnual();

      // 51.98 / 12 is 4.3316…, which rounds to 4.33 at two places — but the figure on screen is
      // the one the server computed, because a client dividing would round differently from the
      // charge in a currency with no minor unit.
      await waitFor(() => expect(screen.getByText('$4.33')).toBeDefined());
      expect(screen.getByText(/Billed \$51\.98\/yr/)).toBeDefined();
      expect(screen.getByText(/Billed \$104\.07\/yr/)).toBeDefined();
      expect(screen.getByText(/Billed \$260\.32\/yr/)).toBeDefined();
      expect(screen.getAllByText(/save 17%/)).toHaveLength(3);
    });

    it('prints no decimal places for a currency that has none', async () => {
      getPublicPlans.mockResolvedValue(
        catalogue({
          currency: 'JPY',
          symbol: '¥',
          decimals: 0,
          prices: { starter: [818, 8180, 682] },
        }),
      );
      renderPricing();

      // `¥818.00` would be a conversion artefact for a currency that cannot express a fraction.
      await waitFor(() => expect(screen.getByText('¥818')).toBeDefined());
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
      const text = pricingText(container);

      // "Unlimited Strategy Bots" was on the old Institutional card and was not implemented.
      expect(text).not.toContain('Unlimited');
      expect(text).not.toContain('unlimited');
    });

    it('does not advertise team, workspace, RBAC or API tiers', () => {
      const { container } = renderPricing();
      const text = pricingText(container);

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

    it('quotes one currency at a time across the whole page', async () => {
      getPublicPlans.mockResolvedValue(USD_CATALOGUE);
      const { container } = render(
        <MemoryRouter>
          <LandingPage />
        </MemoryRouter>
      );

      await waitFor(() => expect(screen.getByText('$5.20')).toBeDefined());

      // Every other section of the landing page — FAQ included — must not restate a price in the
      // currency the pricing section is no longer showing. The published-list disclosure under the
      // toggle is the one place `₹` is allowed to appear, and it is labelled as the base currency.
      for (const published of ['₹499', '₹999', '₹2,499', '₹4,990', '₹9,990', '₹24,990']) {
        expect(container.textContent, `${published} is quoted alongside dollars`)
          .not.toContain(published);
      }
    });
  });
});
