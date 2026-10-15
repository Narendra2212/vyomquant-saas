/**
 * FE-07 -- the onboarding wizard's "Connect Your Exchange" step no longer pretends to save
 * anything.
 *
 * THE DEFECT
 *   The exchange-select buttons had no `onClick`, the "API Key"/"Secret Key" `Field`s had
 *   no `value`/`onChange` (uncontrolled, capturing nothing a trader typed), and "Test
 *   Connection" had no handler at all. "Next: Choose Plan" advanced regardless of any of
 *   that. A trader who filled the form in reasonably believed they had connected an
 *   exchange with encrypted-at-rest keys, exactly as the step's own copy said; nothing
 *   they typed was kept anywhere.
 *
 * THE FIX
 *   The fake form is gone. The step now says plainly that Paper Trading needs no exchange
 *   keys, and offers one real, working action: a link to Exchange Manager (`/app/exchange`,
 *   where `ExchangeManager`'s actual save/test flow lives, including AUTH-01's AAL2/MFA
 *   step-up on the credential-save endpoint -- a requirement this one-screen wizard step
 *   has no way to satisfy inline). The "Next: Choose Plan" control is unchanged: this step
 *   was never a hard gate and still is not.
 *
 * WHAT THIS FILE PROVES
 *   * no non-functional exchange-select buttons, API-key/secret inputs, or "Test
 *     Connection" control render on this step any more;
 *   * a real, working navigation to /app/exchange exists and is reachable;
 *   * the wizard can still be completed (Next: Choose Plan) without connecting anything,
 *     exactly as before this fix.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock('../../src/api', () => ({
  api: {
    strategies: { backtest: vi.fn(async () => ({ data: { trades: 12, total_return_pct: 4.2 } })) },
    billing: { getPlans: vi.fn(async () => ({ data: { plans: [] } })) },
  },
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

const advanceToConnectExchangeStep = async () => {
  render(
    <MemoryRouter>
      <Wizard />
    </MemoryRouter>,
  );

  fireEvent.click(await screen.findByRole('button', { name: /Continue →/i }));
  fireEvent.click(await screen.findByRole('button', { name: /Run Real Backtest/i }));
  fireEvent.click(await screen.findByRole('button', { name: /Next: Connect Exchange/i }));

  await screen.findByRole('heading', { name: /Connect Your Exchange/i });
};

describe('FE-07: the Connect Exchange step no longer fakes a save', () => {
  beforeEach(() => {
    mockNavigate.mockClear();
  });

  it('renders no exchange-select buttons, key inputs, or Test Connection control', async () => {
    await advanceToConnectExchangeStep();

    expect(screen.queryByText('Binance')).toBeNull();
    expect(screen.queryByLabelText(/API Key/i)).toBeNull();
    expect(screen.queryByLabelText(/Secret Key/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /Test Connection/i })).toBeNull();
  });

  it('offers a real link to Exchange Manager', async () => {
    await advanceToConnectExchangeStep();

    fireEvent.click(await screen.findByRole('button', { name: /Open Exchange Manager/i }));

    expect(mockNavigate).toHaveBeenCalledWith('/app/exchange');
  });

  it('can still be completed without connecting anything', async () => {
    await advanceToConnectExchangeStep();

    fireEvent.click(screen.getByRole('button', { name: /Next: Choose Plan/i }));

    await waitFor(() => expect(screen.getByRole('heading', { name: /Choose Your Plan/i })).toBeTruthy());
  });
});
