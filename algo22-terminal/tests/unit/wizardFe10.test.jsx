/**
 * FE-10 -- the onboarding wizard's "Choose Plan" step no longer sends a trader to the
 * dashboard when their checkout failed to open.
 *
 * THE DEFECT
 *   `handleSelectPlan` called `navigate("/app/dashboard")` on BOTH the checkout request
 *   succeeding but returning no `checkoutUrl`, and the request throwing outright -- the
 *   exact same call the SUCCESS path never makes (success leaves the SPA via
 *   `window.location.href`). A trader whose paid-plan checkout failed to open was silently
 *   sent to the dashboard as though they had successfully selected and paid for a plan.
 *
 * THE FIX
 *   Both failure branches now set an inline `checkoutError` instead of navigating anywhere.
 *   The step renders it as a `ds/Alert` (`severity="error"`) with a `Retry` action that
 *   calls `handleSelectPlan` again for the same plan -- the trader stays mid-wizard. Only a
 *   real `checkoutUrl` still sends the browser anywhere, via `window.location.href`.
 *
 * WHAT THIS FILE PROVES
 *   * a successful checkout (URL returned) still redirects via `window.location.href`, and
 *     `navigate` is never called -- no regression;
 *   * a checkout that returns no URL shows a visible error and does NOT navigate to the
 *     dashboard;
 *   * a checkout that throws shows a visible error and does NOT navigate to the dashboard;
 *   * the error state offers a retry that attempts the same checkout again.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const createCheckout = vi.fn();

vi.mock('../../src/api', () => ({
  api: {
    strategies: { backtest: vi.fn(async () => ({ data: { trades: 12, total_return_pct: 4.2 } })) },
    billing: {
      getPlans: vi.fn(async () => ({
        data: {
          plans: [
            {
              id: 'pro',
              recommended: true,
              localized_price: 999,
              currency: 'INR',
              currency_symbol: '₹',
              features: ['Feature one'],
            },
          ],
        },
      })),
      createCheckout: (...args) => createCheckout(...args),
    },
  },
  isAuthenticated: () => false,
}));

vi.mock('../../src/supabase', () => ({
  supabase: {
    auth: {
      getUser: vi.fn(async () => ({ data: { user: { id: 'u1', email: 'trader@vyomquant.io' } }, error: null })),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn(async () => ({ data: { currentLevel: 'aal1' }, error: null })) },
    },
  },
}));

import Wizard from '../../src/pages/Wizard';

const advanceToChoosePlanStep = async () => {
  render(
    <MemoryRouter>
      <Wizard />
    </MemoryRouter>,
  );

  fireEvent.click(await screen.findByRole('button', { name: /Continue →/i }));
  fireEvent.click(await screen.findByRole('button', { name: /Run Real Backtest/i }));
  fireEvent.click(await screen.findByRole('button', { name: /Next: Connect Exchange/i }));
  fireEvent.click(await screen.findByRole('button', { name: /Next: Choose Plan/i }));

  await screen.findByRole('heading', { name: /Choose Your Plan/i });
};

describe('FE-10: a checkout failure never navigates to the dashboard', () => {
  let originalLocation;

  beforeEach(() => {
    mockNavigate.mockClear();
    createCheckout.mockReset();
    originalLocation = window.location;
    delete window.location;
    window.location = { ...originalLocation, href: '' };
  });

  afterEach(() => {
    window.location = originalLocation;
  });

  it('still redirects via window.location.href on a successful checkout (no regression)', async () => {
    createCheckout.mockResolvedValue({ checkoutUrl: 'https://checkout.example/pay/pro' });
    await advanceToChoosePlanStep();

    fireEvent.click(await screen.findByRole('button', { name: /Select →/i }));

    await waitFor(() => expect(window.location.href).toBe('https://checkout.example/pay/pro'));
    expect(mockNavigate).not.toHaveBeenCalledWith('/app/dashboard');
  });

  it('shows a visible error and does not navigate when the response has no checkout URL', async () => {
    createCheckout.mockResolvedValue({});
    await advanceToChoosePlanStep();

    fireEvent.click(await screen.findByRole('button', { name: /Select →/i }));

    await screen.findByText(/Checkout could not be opened/i);
    expect(mockNavigate).not.toHaveBeenCalledWith('/app/dashboard');
    expect(window.location.href).toBe('');
  });

  it('shows a visible error and does not navigate when the checkout request throws', async () => {
    createCheckout.mockRejectedValue(new Error('network down'));
    await advanceToChoosePlanStep();

    fireEvent.click(await screen.findByRole('button', { name: /Select →/i }));

    await screen.findByText(/Checkout could not be opened/i);
    expect(mockNavigate).not.toHaveBeenCalledWith('/app/dashboard');
    expect(window.location.href).toBe('');
  });

  it('offers a retry that attempts the checkout again for the same plan', async () => {
    createCheckout.mockRejectedValueOnce(new Error('network down'));
    createCheckout.mockResolvedValueOnce({ checkoutUrl: 'https://checkout.example/pay/pro' });
    await advanceToChoosePlanStep();

    fireEvent.click(await screen.findByRole('button', { name: /Select →/i }));
    await screen.findByText(/Checkout could not be opened/i);

    fireEvent.click(await screen.findByRole('button', { name: /Retry/i }));

    await waitFor(() => expect(window.location.href).toBe('https://checkout.example/pay/pro'));
    expect(createCheckout).toHaveBeenCalledTimes(2);
    expect(mockNavigate).not.toHaveBeenCalledWith('/app/dashboard');
  });
});
