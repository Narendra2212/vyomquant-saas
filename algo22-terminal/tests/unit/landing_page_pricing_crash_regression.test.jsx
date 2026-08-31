import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LandingPage from '../../src/components/landing/LandingPage';
import Pricing from '../../src/components/landing/Pricing';
import { api } from '../../src/api';

describe('Landing Page & Pricing — Production toLocaleString Crash Regression Suite', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  describe('Pricing Component Regression Tests', () => {
    it('handles backend API response shape (base_price & localized_price, missing usd/inr) without crashing', async () => {
      // Backend API contract returns base_price and localized_price, but NOT usd / inr
      const mockBackendResponse = {
        currency: 'USD',
        plans: [
          {
            id: 'free',
            name: 'Free / Sandbox',
            description: 'Essential sandbox',
            base_price: 0,
            localized_price: 0,
            currency: 'USD',
            features: ['Visual DAG Strategy Builder'],
            recommended: false,
          },
          {
            id: 'starter',
            name: 'Trader',
            description: 'For active systematic traders',
            base_price: 29,
            localized_price: 29,
            currency: 'USD',
            features: ['5 Active Strategy Bots'],
            recommended: false,
          },
          {
            id: 'pro',
            name: 'Pro Quant',
            description: 'High-capacity execution engine',
            base_price: 79,
            localized_price: 79,
            currency: 'USD',
            features: ['15 Active Strategy Bots'],
            recommended: true,
          },
          {
            id: 'enterprise',
            name: 'Institutional',
            description: 'Dedicated infrastructure',
            base_price: 199,
            localized_price: 199,
            currency: 'USD',
            features: ['Unlimited Strategy Bots'],
            recommended: false,
          },
        ],
      };

      vi.spyOn(api.billing, 'getPlans').mockResolvedValue(mockBackendResponse);

      render(
        <MemoryRouter>
          <Pricing />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Infrastructure Tiers')).toBeDefined();
        // Starter ($29)
        expect(screen.getByText('$29')).toBeDefined();
        // Pro ($79)
        expect(screen.getByText('$79')).toBeDefined();
        // Enterprise ($199)
        expect(screen.getByText('$199')).toBeDefined();
      });
    });

    it('handles zero values explicitly and formats as $0', async () => {
      vi.spyOn(api.billing, 'getPlans').mockResolvedValue({
        plans: [
          {
            id: 'free',
            name: 'Free / Sandbox',
            description: 'Free tier',
            base_price: 0,
            localized_price: 0,
            features: [],
          },
        ],
      });

      render(
        <MemoryRouter>
          <Pricing />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('$0')).toBeDefined();
      });
    });

    it('handles null / undefined / malformed price fields without throwing TypeError', async () => {
      vi.spyOn(api.billing, 'getPlans').mockResolvedValue({
        plans: [
          {
            id: 'corrupt-1',
            name: 'Corrupt Plan 1',
            base_price: null,
            localized_price: undefined,
            usd: null,
            inr: undefined,
            features: null,
          },
          {
            id: 'corrupt-2',
            name: 'Corrupt Plan 2',
            base_price: 'non-numeric',
            features: undefined,
          },
        ],
      });

      expect(() => {
        render(
          <MemoryRouter>
            <Pricing />
          </MemoryRouter>
        );
      }).not.toThrow();

      await waitFor(() => {
        expect(screen.getByText('Corrupt Plan 1')).toBeDefined();
        expect(screen.getByText('Corrupt Plan 2')).toBeDefined();
      });
    });

    it('handles empty API plans array by using fallback plans', async () => {
      vi.spyOn(api.billing, 'getPlans').mockResolvedValue({ plans: [] });

      render(
        <MemoryRouter>
          <Pricing />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Pro Quant')).toBeDefined();
        expect(screen.getByText('$79')).toBeDefined();
      });
    });

    it('toggles currency between USD and INR safely', async () => {
      vi.spyOn(api.billing, 'getPlans').mockImplementation((currency) => {
        if (currency === 'INR') {
          return Promise.resolve({
            plans: [
              {
                id: 'starter',
                name: 'Trader',
                base_price: 29,
                localized_price: 2400,
                currency: 'INR',
                features: [],
              },
            ],
          });
        }
        return Promise.resolve({
          plans: [
            {
              id: 'starter',
              name: 'Trader',
              base_price: 29,
              localized_price: 29,
              currency: 'USD',
              features: [],
            },
          ],
        });
      });

      render(
        <MemoryRouter>
          <Pricing />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('$29')).toBeDefined();
      });

      // Switch to INR
      const inrBtn = screen.getByRole('button', { name: /INR \(₹\)/i });
      fireEvent.click(inrBtn);

      await waitFor(() => {
        expect(screen.getByText('₹2,400')).toBeDefined();
      });
    });

    it('toggles annual billing and calculates 20% discount without TypeError', async () => {
      vi.spyOn(api.billing, 'getPlans').mockResolvedValue({
        plans: [
          {
            id: 'starter',
            name: 'Trader',
            base_price: 100,
            localized_price: 100,
            currency: 'USD',
            features: [],
          },
        ],
      });

      render(
        <MemoryRouter>
          <Pricing />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('$100')).toBeDefined();
      });

      // Switch to Annual
      const annualBtn = screen.getByRole('button', { name: /Annual/i });
      fireEvent.click(annualBtn);

      await waitFor(() => {
        // Annual monthly price: 100 * 0.8 = $80
        expect(screen.getByText('$80')).toBeDefined();
        // Annual billed text: 100 * 12 * 0.8 = $960
        expect(screen.getByText(/Billed at \$960\/yr — save 20%/i)).toBeDefined();
      });
    });
  });

  describe('Full Landing Page Integration', () => {
    it('renders entire LandingPage end-to-end without crashing', async () => {
      vi.spyOn(api.billing, 'getPlans').mockResolvedValue({
        plans: [
          {
            id: 'starter',
            name: 'Trader',
            base_price: 29,
            localized_price: 29,
            currency: 'USD',
            features: ['5 Active Strategy Bots'],
          },
        ],
      });

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
  });
});
