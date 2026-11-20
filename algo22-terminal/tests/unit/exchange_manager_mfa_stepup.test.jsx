/**
 * ═══════════════════════════════════════════════════════════════════════════
 * exchange_manager_mfa_stepup.test.jsx — MFA-STEPUP-01
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Connecting an exchange is guarded by `Depends(require_aal2)`
 * (`routers/exchange.py:375`), which `core/dependencies.py` implements by reading the `aal`
 * claim off the Supabase JWT and answering 403 unless it is `"aal2"`. A session only reaches
 * `aal2` through a Supabase MFA challenge, and the app ran that challenge in exactly one
 * place: the standalone `/app/2fa` screen.
 *
 * So the page's answer to a refusal it could have cleared was a sentence telling the trader
 * to leave, verify elsewhere, come back and retype their API key. These cases hold the
 * in-flow replacement, and specifically the four things that would make it worse than the
 * sentence it replaced:
 *
 *   1. verifying and then silently doing nothing  (the retry is dropped)
 *   2. verifying and then being refused anyway    (the retry races the token mirror)
 *   3. losing the typed credentials to the dialog (the retry submits an empty body)
 *   4. sending the platform TOTP to the exchange  (two secrets conflated)
 *
 * WHAT IS NOT MOCKED, DELIBERATELY: the refusal. Every case drives the page with a real
 * `ApiError` built the way `apiClient`'s interceptor builds it, so the predicate under test
 * is the one production runs.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

import ExchangeManager from '../../src/pages/ExchangeManager';
import { api } from '../../src/api';
import { ApiError } from '../../src/apiClient';
import { supabase } from '../../src/supabase';

vi.mock('../../src/api', () => ({
  isAuthenticated: () => false,
  api: {
    exchange: {
      list: vi.fn(),
      getSupported: vi.fn(),
      getAuthSchema: vi.fn(),
      testConnection: vi.fn(),
      saveKeys: vi.fn(),
      delete: vi.fn(),
      testStoredConnection: vi.fn(),
      reconnect: vi.fn(),
    },
  },
}));

/**
 * The MFA surface of supabase-js, and nothing else.
 *
 * `onAuthStateChange` is stubbed because `apiClient` subscribes to it at import time; it
 * returns the unsubscribe shape the library returns so the module-level call does not throw.
 */
/*
 * `vi.hoisted`, not a plain `const`. `vi.mock` factories are hoisted above module-level
 * declarations, so a factory closing over a plain `const mfa` throws
 * `ReferenceError: Cannot access 'mfa' before initialization` and the whole suite fails to
 * collect — which is exactly what the first run of this file did.
 */
const mfa = vi.hoisted(() => ({
  listFactors: vi.fn(),
  challenge: vi.fn(),
  verify: vi.fn(),
}));

vi.mock('../../src/supabase', () => {
  const client = {
    auth: {
      mfa,
      onAuthStateChange: vi.fn(() => ({ data: { subscription: { unsubscribe: vi.fn() } } })),
      getSession: vi.fn(async () => ({ data: { session: null }, error: null })),
    },
  };
  return { supabase: client, default: client };
});

const supported = {
  exchanges: [
    { id: 'bybit', display_name: 'Bybit', spot_support: true, futures_support: true, sandbox_support: true },
  ],
};

const bybitSchema = {
  exchange_id: 'bybit',
  display_name: 'Bybit',
  fields: [
    { name: 'api_key', label: 'API Key', type: 'password', required: true, placeholder: 'Enter API Key' },
    { name: 'secret_key', label: 'Secret Key', type: 'password', required: true, placeholder: 'Enter Secret Key' },
  ],
};

/** Exactly what `apiClient`'s response interceptor rejects with. */
const apiError = (status, data) =>
  new ApiError(
    (typeof data?.detail === 'string' ? data.detail : null) || 'Request failed',
    { url: '/api/exchanges/keys', method: 'post', status, data },
  );

/** The refusal `require_aal2` actually writes. */
const AAL2_REFUSAL = apiError(403, {
  detail:
    'Two-factor authentication is required to access this resource. Please complete MFA verification and retry.',
});

const SECRET_KEY = 'secret-value-that-must-survive';

async function openFormAndSave() {
  render(<ExchangeManager />);
  fireEvent.click(await screen.findByText('Bybit'));
  fireEvent.change(await screen.findByPlaceholderText('Enter API Key'), {
    target: { value: 'api-key-value' },
  });
  fireEvent.change(screen.getByPlaceholderText('Enter Secret Key'), {
    target: { value: SECRET_KEY },
  });
  fireEvent.click(screen.getByRole('button', { name: /Save Keys/i }));
}

/**
 * Stand in for `apiClient`'s token mirror moving to the reissued `aal2` JWT.
 *
 * The dialog waits for this before handing control back — see "THE RACE" in
 * `MfaStepUpDialog.jsx`. Production writes it from the `MFA_CHALLENGE_VERIFIED` listener in
 * `apiClient.js`; here the verify stub writes it, which is the same ordering.
 */
const reissueToken = () => window.sessionStorage.setItem('token', `aal2-${Math.random()}`);

describe('MFA-STEPUP-01: the exchange write paths challenge in place', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.setItem('token', 'aal1-token');
    api.exchange.getSupported.mockResolvedValue(supported);
    api.exchange.list.mockResolvedValue([]);
    api.exchange.getAuthSchema.mockResolvedValue(bybitSchema);
    mfa.listFactors.mockResolvedValue({
      data: { totp: [{ id: 'factor-1', status: 'verified' }] },
      error: null,
    });
    mfa.challenge.mockResolvedValue({ data: { id: 'challenge-1' }, error: null });
  });
  afterEach(() => {
    cleanup();
    window.sessionStorage.clear();
  });

  it('retries the save after a successful verification', async () => {
    // Refused once for want of AAL2, accepted on the retry — what the server does.
    api.exchange.saveKeys
      .mockRejectedValueOnce(AAL2_REFUSAL)
      .mockResolvedValueOnce({ status: 'ok' });
    mfa.verify.mockImplementation(async () => {
      reissueToken();
      return { data: { access_token: 'new' }, error: null };
    });

    await openFormAndSave();
    await screen.findByTestId('mfa-stepup-dialog');

    fireEvent.change(screen.getByTestId('mfa-stepup-code'), { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: /Verify and continue/i }));

    // Defect 1: the retry must actually happen.
    await waitFor(() => expect(api.exchange.saveKeys).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/encrypted and stored securely/i)).toBeDefined();
    await waitFor(() => expect(screen.queryByTestId('mfa-stepup-dialog')).toBeNull());
  });

  it('retries with the credentials the trader typed, not an empty body', async () => {
    api.exchange.saveKeys
      .mockRejectedValueOnce(AAL2_REFUSAL)
      .mockResolvedValueOnce({ status: 'ok' });
    mfa.verify.mockImplementation(async () => {
      reissueToken();
      return { data: {}, error: null };
    });

    await openFormAndSave();
    await screen.findByTestId('mfa-stepup-dialog');
    fireEvent.change(screen.getByTestId('mfa-stepup-code'), { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: /Verify and continue/i }));

    await waitFor(() => expect(api.exchange.saveKeys).toHaveBeenCalledTimes(2));

    // Defect 3. The save handler clears the form on SUCCESS; the refusal path must not, or
    // the whole feature amounts to retyping the key with extra steps.
    const retried = api.exchange.saveKeys.mock.calls[1][0];
    expect(retried.secret_key).toBe(SECRET_KEY);
    expect(retried.api_key).toBe('api-key-value');
    expect(retried.exchange_id).toBe('bybit');
  });

  it('does not retry until the reissued token is observable', async () => {
    api.exchange.saveKeys
      .mockRejectedValueOnce(AAL2_REFUSAL)
      .mockResolvedValueOnce({ status: 'ok' });

    // Defect 2. Verification succeeds but the mirror never moves — production's failure mode
    // is `apiClient` still holding the pre-challenge token, so the retry collects a second
    // 403 and the correct code looks rejected. The dialog must not hand back early.
    mfa.verify.mockResolvedValue({ data: {}, error: null });

    await openFormAndSave();
    await screen.findByTestId('mfa-stepup-dialog');
    fireEvent.change(screen.getByTestId('mfa-stepup-code'), { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: /Verify and continue/i }));

    // While settling, the retry has not gone out.
    await waitFor(() =>
      expect(screen.getByTestId('mfa-stepup-dialog').getAttribute('data-status')).toBe('settling'),
    );
    expect(api.exchange.saveKeys).toHaveBeenCalledTimes(1);

    /*
      Then let the mirror move, and watch the retry follow.

      This half is not padding. Without it the poll is still running when the test ends, and
      its budget outlives `cleanup()` — the deferred `onVerified` then fires into the NEXT
      test and calls its freshly-cleared `saveKeys` mock. That is not hypothetical: it is
      what made the following case fail with "unable to find mfa-stepup-dialog" on the first
      green run of this file. Finishing the flow inside the test that starts it keeps the
      suite order-independent.
    */
    reissueToken();
    await waitFor(() => expect(api.exchange.saveKeys).toHaveBeenCalledTimes(2));
  });

  it('reports an invalid code, keeps the dialog open and takes a fresh challenge', async () => {
    api.exchange.saveKeys.mockRejectedValue(AAL2_REFUSAL);
    mfa.verify.mockResolvedValue({ data: null, error: { message: 'Invalid TOTP code entered' } });

    await openFormAndSave();
    await screen.findByTestId('mfa-stepup-dialog');
    fireEvent.change(screen.getByTestId('mfa-stepup-code'), { target: { value: '000000' } });
    fireEvent.click(screen.getByRole('button', { name: /Verify and continue/i }));

    expect(await screen.findByText(/Invalid TOTP code entered/i)).toBeDefined();
    expect(screen.getByTestId('mfa-stepup-dialog')).toBeDefined();
    // The code is cleared, so a stale value cannot be resubmitted by pressing Enter.
    expect(screen.getByTestId('mfa-stepup-code').value).toBe('');
    // Still one save attempt: a failed challenge must not reach the guarded operation.
    expect(api.exchange.saveKeys).toHaveBeenCalledTimes(1);

    // A spent challenge is discarded, so the next attempt asks for a new one.
    fireEvent.change(screen.getByTestId('mfa-stepup-code'), { target: { value: '111111' } });
    fireEvent.click(screen.getByRole('button', { name: /Verify and continue/i }));
    await waitFor(() => expect(mfa.challenge).toHaveBeenCalledTimes(2));
  });

  it('stops offering the control after five wrong codes', async () => {
    api.exchange.saveKeys.mockRejectedValue(AAL2_REFUSAL);
    mfa.verify.mockResolvedValue({ data: null, error: { message: 'Invalid TOTP code entered' } });

    await openFormAndSave();
    await screen.findByTestId('mfa-stepup-dialog');

    for (let attempt = 0; attempt < 5; attempt += 1) {
      fireEvent.change(screen.getByTestId('mfa-stepup-code'), { target: { value: '000000' } });
      fireEvent.click(screen.getByRole('button', { name: /Verify and continue/i }));
      // eslint-disable-next-line no-await-in-loop -- attempts are sequential by definition
      await waitFor(() => expect(mfa.verify).toHaveBeenCalledTimes(attempt + 1));
    }

    await waitFor(() =>
      expect(screen.getByTestId('mfa-stepup-dialog').getAttribute('data-status')).toBe('exhausted'),
    );
    expect(screen.queryByRole('button', { name: /Verify and continue/i })).toBeNull();
    expect(api.exchange.saveKeys).toHaveBeenCalledTimes(1);
  });

  it('explains enrollment rather than challenging a factor that does not exist', async () => {
    api.exchange.saveKeys.mockRejectedValue(AAL2_REFUSAL);
    mfa.listFactors.mockResolvedValue({ data: { totp: [] }, error: null });

    await openFormAndSave();
    await screen.findByTestId('mfa-stepup-dialog');

    expect(await screen.findByText(/No authenticator is set up yet/i)).toBeDefined();
    // Nothing to challenge, so nothing is challenged — an unverified factor would fail in a
    // way that reads as a broken dialog rather than as unfinished setup.
    expect(mfa.challenge).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /Verify and continue/i })).toBeNull();
  });

  it('treats an unverified factor as no factor', async () => {
    api.exchange.saveKeys.mockRejectedValue(AAL2_REFUSAL);
    mfa.listFactors.mockResolvedValue({
      data: { totp: [{ id: 'half-enrolled', status: 'unverified' }] },
      error: null,
    });

    await openFormAndSave();
    expect(await screen.findByText(/No authenticator is set up yet/i)).toBeDefined();
    expect(mfa.challenge).not.toHaveBeenCalled();
  });

  it('cancelling changes nothing and says so', async () => {
    api.exchange.saveKeys.mockRejectedValue(AAL2_REFUSAL);

    await openFormAndSave();
    await screen.findByTestId('mfa-stepup-dialog');
    fireEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));

    await waitFor(() => expect(screen.queryByTestId('mfa-stepup-dialog')).toBeNull());
    expect(await screen.findByText(/Verification was cancelled, so nothing was changed/i)).toBeDefined();
    expect(mfa.verify).not.toHaveBeenCalled();
    expect(api.exchange.saveKeys).toHaveBeenCalledTimes(1);
  });

  it('never sends the platform TOTP code to the exchange', async () => {
    api.exchange.saveKeys
      .mockRejectedValueOnce(AAL2_REFUSAL)
      .mockResolvedValueOnce({ status: 'ok' });
    mfa.verify.mockImplementation(async () => {
      reissueToken();
      return { data: {}, error: null };
    });

    await openFormAndSave();
    await screen.findByTestId('mfa-stepup-dialog');
    fireEvent.change(screen.getByTestId('mfa-stepup-code'), { target: { value: '424242' } });
    fireEvent.click(screen.getByRole('button', { name: /Verify and continue/i }));
    await waitFor(() => expect(api.exchange.saveKeys).toHaveBeenCalledTimes(2));

    /*
      Defect 4, and the reason this case exists rather than being left to review. The
      platform's TOTP and an exchange's API credentials are different secrets protecting
      different things. The code is Supabase's business; it must appear in NO payload bound
      for a venue, under any key name.
    */
    const everyPayload = JSON.stringify(api.exchange.saveKeys.mock.calls);
    expect(everyPayload).not.toContain('424242');
    expect(mfa.verify).toHaveBeenCalledWith(
      expect.objectContaining({ factorId: 'factor-1', challengeId: 'challenge-1', code: '424242' }),
    );
  });
});
