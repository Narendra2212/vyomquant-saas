/**
 * FE-04 -- a successful password check no longer leaves a live, usable session before the
 * OTP step completes.
 *
 * THE DEFECT
 *   `supabase.auth.signInWithPassword` succeeding is a complete Supabase sign-in, not
 *   merely a password check: with `persistSession: true` (src/supabase.js), the SDK
 *   immediately writes a live, fully-privileged session into its own storage. The sign-in
 *   handler's comment ("DO NOT grant application access or persist session to
 *   sessionStorage yet") only ever controlled THIS APP's OWN bookkeeping -- it never
 *   touched the session Supabase itself had already established. The 6-digit email-OTP
 *   step that followed was therefore decorative for general account access: anyone who
 *   could read that already-live session (this tab, a shared-storage tab, a future bug)
 *   had full access with zero second factor, directly contradicting the sign-in page's own
 *   "two-factor authentication" framing. (`require_aal2`-gated routes, SEC-05/SEC-15, were
 *   never affected by this -- AAL stays 1 regardless, since this flow never touches
 *   `supabase.auth.mfa.*`. The exposure is to everything NOT gated by `require_aal2`.)
 *
 * THE FIX
 *   `supabase.auth.signOut()` is now called immediately after a successful
 *   `signInWithPassword` (and after `signUp`, for the same reason if email confirmation
 *   happens to be off for this project), before the OTP screen is ever shown. Nothing
 *   live exists between the password check and a successful `verifyOtp`, which establishes
 *   its own fresh session -- the only one this flow keeps.
 *
 * WHAT THIS FILE PROVES
 *   * a successful password check calls `signOut` before the OTP screen renders;
 *   * `signOut` is called before `verifyOtp` is ever reached (ordering, not just
 *     "eventually called");
 *   * the full sign-in flow (password -> OTP -> verifyOtp) still ends in a real session
 *     written to sessionStorage -- this fix tears down the INTERIM session, not the whole
 *     feature.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import AuthPage from '../../src/pages/AuthPage';
import { supabase } from '../../src/supabase';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

describe('FE-04: the OTP step is a real gate, not decoration', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockNavigate.mockClear();
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const signInWithEmailAndPassword = async (email, password) => {
    render(
      <MemoryRouter>
        <AuthPage mode="signin" />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByPlaceholderText('trader@vyomquant.io'), { target: { value: email } });
    fireEvent.change(screen.getByLabelText(/^Password$/i), { target: { value: password } });
    fireEvent.click(screen.getByRole('button', { name: /SIGN IN →/i }));
  };

  it('signs out the interim session the moment the password check succeeds', async () => {
    vi.spyOn(supabase.auth, 'signInWithPassword').mockResolvedValueOnce({
      data: {
        user: { id: 'usr_1', email: 'trader@vyomquant.io' },
        session: { access_token: 'interim_session_that_must_not_survive' },
      },
      error: null,
    });
    const signOutSpy = vi.spyOn(supabase.auth, 'signOut');
    vi.spyOn(supabase.auth, 'signInWithOtp').mockResolvedValueOnce({ data: {}, error: null });

    await signInWithEmailAndPassword('trader@vyomquant.io', 'CorrectPassword123!');

    await screen.findByRole('heading', { name: /Verify Your Identity/i });
    expect(signOutSpy).toHaveBeenCalled();
  });

  it('calls signOut before the OTP screen is shown, not after -- ordering, not just presence', async () => {
    const callOrder = [];
    vi.spyOn(supabase.auth, 'signInWithPassword').mockImplementation(async () => {
      callOrder.push('signInWithPassword');
      return {
        data: { user: { id: 'usr_1' }, session: { access_token: 'interim' } },
        error: null,
      };
    });
    vi.spyOn(supabase.auth, 'signOut').mockImplementation(async () => {
      callOrder.push('signOut');
      return { error: null };
    });
    vi.spyOn(supabase.auth, 'signInWithOtp').mockImplementation(async () => {
      callOrder.push('signInWithOtp');
      return { data: {}, error: null };
    });

    await signInWithEmailAndPassword('trader@vyomquant.io', 'CorrectPassword123!');

    await screen.findByRole('heading', { name: /Verify Your Identity/i });
    expect(callOrder.indexOf('signOut')).toBeGreaterThan(callOrder.indexOf('signInWithPassword'));
    expect(callOrder.indexOf('signOut')).toBeLessThan(callOrder.indexOf('signInWithOtp'));
  });

  it('still establishes a real session once the OTP is actually verified', async () => {
    vi.spyOn(supabase.auth, 'signInWithPassword').mockResolvedValueOnce({
      data: { user: { id: 'usr_1' }, session: { access_token: 'interim' } },
      error: null,
    });
    vi.spyOn(supabase.auth, 'signOut').mockResolvedValue({ error: null });
    vi.spyOn(supabase.auth, 'signInWithOtp').mockResolvedValueOnce({ data: {}, error: null });

    await signInWithEmailAndPassword('trader@vyomquant.io', 'CorrectPassword123!');
    await screen.findByRole('heading', { name: /Verify Your Identity/i });

    vi.spyOn(supabase.auth, 'verifyOtp').mockResolvedValueOnce({
      data: { session: { access_token: 'real_verified_session_token' } },
      error: null,
    });

    const digitInputs = screen.getAllByLabelText(/Digit \d of 6/i);
    '123456'.split('').forEach((digit, i) => fireEvent.change(digitInputs[i], { target: { value: digit } }));
    fireEvent.click(screen.getByRole('button', { name: /VERIFY/i }));

    await waitFor(() => expect(sessionStorage.getItem('token')).toBe('real_verified_session_token'));
    expect(mockNavigate).toHaveBeenCalledWith('/app/dashboard');
  });
});
