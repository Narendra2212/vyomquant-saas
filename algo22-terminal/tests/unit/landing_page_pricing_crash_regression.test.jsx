import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LandingPage from '../../src/components/landing/LandingPage';
import Pricing from '../../src/components/landing/Pricing';
import { api } from '../../src/api';

/**
 * Landing pricing is INR-only and states four published tiers.
 *
 * This file began as a `toLocaleString` crash regression suite, when the section
 * rendered whatever `GET /api/billing/plans` returned and a null price threw on
 * format. The prices are now declared in the component (see the header comment
 * in `Pricing.jsx`: the endpoint FX-converts the USD base and therefore does not
 * serve the catalogue's INR column), so the crash surface is gone along with the
 * fetch. What is asserted instead is the contract that replaced it — four tiers,
 * rupees only, and arithmetic that still cannot throw.
 */
describe('Landing Page & Pricing — INR-only tier contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const renderPricing = () =>
    render(
      <MemoryRouter>
        <Pricing />
      </MemoryRouter>
    );

  describe('The four published tiers', () => {
    it('quotes ₹0, ₹499, ₹999 and ₹2,499 per month', () => {
      renderPricing();

      expect(screen.getByText('Infrastructure Tiers')).toBeDefined();
      expect(screen.getByText('₹0')).toBeDefined();
      expect(screen.getByText('₹499')).toBeDefined();
      expect(screen.getByText('₹999')).toBeDefined();
      expect(screen.getByText('₹2,499')).toBeDefined();
    });

    it('renders exactly four tier cards, one per published plan', () => {
      const { container } = renderPricing();

      const names = ['Free / Sandbox', 'Trader', 'Pro Quant', 'Institutional'];
      names.forEach((name) => expect(screen.getByText(name)).toBeDefined());

      // One `/month` label per card is the card count, independent of copy.
      expect(container.querySelectorAll('#pricing .card-surface')).toHaveLength(4);
      expect(screen.getAllByText('/month')).toHaveLength(4);
    });

    it('marks Pro Quant as the recommended tier', () => {
      renderPricing();
      expect(screen.getByText('Recommended')).toBeDefined();
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

    it('does not call the FX-localised billing endpoint for its figures', async () => {
      const getPlans = vi.spyOn(api.billing, 'getPlans').mockResolvedValue({ plans: [] });

      renderPricing();
      // A fetch would land in a microtask after mount, so let the queue drain.
      await Promise.resolve();

      expect(getPlans).not.toHaveBeenCalled();
    });
  });

  describe('Annual billing arithmetic', () => {
    it('applies the 20% reduction to every paid tier without throwing', async () => {
      renderPricing();

      fireEvent.click(screen.getByRole('button', { name: /Annual/i }));

      await waitFor(() => {
        // Monthly-equivalent: 499 / 999 / 2499 less 20%, rounded.
        expect(screen.getByText('₹399')).toBeDefined();
        expect(screen.getByText('₹799')).toBeDefined();
        expect(screen.getByText('₹1,999')).toBeDefined();
      });

      // Billed-at lines carry the full year at the same 20% reduction.
      expect(screen.getByText(/Billed at ₹4,790\/yr — save 20%/)).toBeDefined();
      expect(screen.getByText(/Billed at ₹9,590\/yr — save 20%/)).toBeDefined();
      expect(screen.getByText(/Billed at ₹23,990\/yr — save 20%/)).toBeDefined();
    });

    it('leaves the free tier at ₹0 with no annual billing line', async () => {
      renderPricing();

      fireEvent.click(screen.getByRole('button', { name: /Annual/i }));

      await waitFor(() => {
        expect(screen.getByText('₹0')).toBeDefined();
      });
      // Three paid tiers carry the line; the free tier does not.
      expect(screen.getAllByText(/Billed at ₹/)).toHaveLength(3);
    });

    it('returns to the monthly figures when Monthly is reselected', async () => {
      renderPricing();

      fireEvent.click(screen.getByRole('button', { name: /Annual/i }));
      await waitFor(() => expect(screen.getByText('₹399')).toBeDefined());

      fireEvent.click(screen.getByRole('button', { name: /Monthly/i }));
      await waitFor(() => {
        expect(screen.getByText('₹499')).toBeDefined();
        expect(screen.queryByText(/Billed at ₹/)).toBeNull();
      });
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
        expect(container.querySelector('#pricing')).toBeDefined();
        expect(container.querySelector('#architecture')).toBeDefined();
        expect(container.querySelector('#waitlist')).toBeDefined();
        expect(screen.getByText('Infrastructure Tiers')).toBeDefined();
      });
    });

    it('shows no dollar sign anywhere on the landing page', async () => {
      const { container } = render(
        <MemoryRouter>
          <LandingPage />
        </MemoryRouter>
      );

      await waitFor(() => expect(screen.getByText('Infrastructure Tiers')).toBeDefined());

      expect(container.textContent).not.toContain('$');
    });
  });
});
