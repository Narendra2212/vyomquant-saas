/**
 * Password recovery can be STARTED from the product, which it previously could not.
 *
 * THE DEFECT
 *   The completion half of password recovery already existed and worked: `/reset-password`
 *   is a declared route, `pages/UpdatePasswordPage.jsx` renders the new-password form and
 *   calls `supabase.auth.updateUser`, and `App.jsx` intercepts a `type=recovery` hash from
 *   any path and sends the user there.
 *
 *   Nothing ever sent the email. There was no `resetPasswordForEmail` call anywhere in
 *   `src/` -- a fact recorded in the comments of both `src/supabase.js` ("This app has no
 *   call site for `resetPasswordForEmail` (grep `src/` -- there isn't one)") and
 *   `src/App.jsx`, and in `tests/unit/pages/authRedirectOrigin.test.jsx`, which listed the
 *   method with the note "no call site today" and predicted a forgot-password screen as
 *   one of the two most likely next additions.
 *
 *   So a user who forgot their password had NO WAY to recover their account from the
 *   product. The only route back in was an operator triggering a recovery email by hand
 *   from the Supabase dashboard. For a signed-up account that is a lockout.
 *
 * THE FIX
 *   A *Forgot password?* control on the sign-in form calls
 *   `supabase.auth.resetPasswordForEmail(email, { redirectTo: <origin>/reset-password })`.
 *
 * WHAT THIS FILE PROVES
 *   1. The control exists on the SIGN-IN form and dispatches the recovery email.
 *   2. It names `/reset-password` as the redirect, so the link lands on the form that
 *      completes the flow. This is also why the app can now assert what it previously
 *      could not: which path a legitimate recovery link arrives on.
 *   3. It does NOT disclose whether the address has an account -- the success message is
 *      the same either way. A reset form that says "no account with that email" is an
 *      account-enumeration oracle for anyone who wants a customer list.
 *   4. It does NOT appear on the sign-up form, where there is no account to recover.
 *   5. It is `type="button"`, so pressing it cannot submit the sign-in form instead.
 *   6. A refusal is SURFACED rather than swallowed -- supabase-js resolves with `{ error }`
 *      instead of throwing, which is the exact mistake this page's own header records
 *      having made on the OTP dispatch, where a rate limit was invisible and the screen
 *      still reported success.
 *   7. A rate limit says so, because the user's next action is to wait, not to retry.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import AuthPage from '../../src/pages/AuthPage';
import { supabase } from '../../src/supabase';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const EMAIL = 'trader@vyomquant.io';

function renderSignIn(mode = 'signin') {
  return render(
    <MemoryRouter>
      <AuthPage mode={mode} />
    </MemoryRouter>,
  );
}

const forgotButton = () => screen.getByRole('button', { name: /forgot password/i });

function typeEmail(value = EMAIL) {
  fireEvent.change(screen.getByPlaceholderText(EMAIL), { target: { value } });
}

describe('password recovery can be started from the sign-in form', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockNavigate.mockClear();
    sessionStorage.clear();
  });

  it('dispatches the recovery email for the address in the field', async () => {
    const reset = vi
      .spyOn(supabase.auth, 'resetPasswordForEmail')
      .mockResolvedValueOnce({ data: {}, error: null });

    renderSignIn();
    typeEmail();
    fireEvent.click(forgotButton());

    await waitFor(() => expect(reset).toHaveBeenCalledTimes(1));
    expect(reset.mock.calls[0][0]).toBe(EMAIL);
  });

  it('sends the user to /reset-password, the route that completes the flow', async () => {
    const reset = vi
      .spyOn(supabase.auth, 'resetPasswordForEmail')
      .mockResolvedValueOnce({ data: {}, error: null });

    renderSignIn();
    typeEmail();
    fireEvent.click(forgotButton());

    await waitFor(() => expect(reset).toHaveBeenCalled());

    const options = reset.mock.calls[0][1];
    expect(options, 'no options object: the redirect was omitted').toBeDefined();
    expect(options.redirectTo, 'redirectTo is absent').toBeDefined();

    // An ABSOLUTE url on the page's own origin, ending at the completion route. Omitting
    // it made Supabase fall back to the project's dashboard Site URL -- historically a
    // developer's laptop -- so a production user got a connection refusal holding a
    // perfectly valid recovery token.
    const url = new URL(options.redirectTo);
    expect(url.origin).toBe(window.location.origin);
    expect(url.pathname).toBe('/reset-password');
  });

  it('does not reveal whether the address has an account', async () => {
    vi.spyOn(supabase.auth, 'resetPasswordForEmail')
      .mockResolvedValueOnce({ data: {}, error: null });

    renderSignIn();
    typeEmail('definitely-not-registered@example.com');
    fireEvent.click(forgotButton());

    // Queried on the conditional phrase itself, not on /reset link/: the control's own
    // loading label is "Sending reset link...", so the looser pattern matched the BUTTON
    // and asserted against its text instead of the message.
    const message = await screen.findByText(/if an account exists/i);
    const text = message.textContent.toLowerCase();

    // Conditional, not a confirmation. Supabase answers identically for a registered and
    // an unregistered address on purpose; the UI must not undo that.
    expect(text).toMatch(/if an account exists/);
    for (const leak of ['no account', 'not registered', 'does not exist', "doesn't exist", 'unknown email']) {
      expect(text, `the message discloses account existence: ${leak}`).not.toContain(leak);
    }
  });

  it('surfaces a refusal instead of reporting success', async () => {
    // supabase-js RESOLVES with `{ error }` rather than throwing. A handler that awaits the
    // call without binding the result reports success on a refusal -- the defect this
    // page's header records having shipped on the OTP dispatch.
    vi.spyOn(supabase.auth, 'resetPasswordForEmail').mockResolvedValueOnce({
      data: {},
      error: new Error('Signups not allowed for this instance'),
    });

    renderSignIn();
    typeEmail();
    fireEvent.click(forgotButton());

    await screen.findByText(/signups not allowed/i);
    expect(screen.queryByText(/if an account exists/i)).toBeNull();
  });

  it('says a rate limit is a rate limit, because the action is to wait', async () => {
    vi.spyOn(supabase.auth, 'resetPasswordForEmail').mockResolvedValueOnce({
      data: {},
      error: new Error('For security purposes, you can only request this after 60 seconds. Rate limit exceeded'),
    });

    renderSignIn();
    typeEmail();
    fireEvent.click(forgotButton());

    const message = await screen.findByText(/too many reset requests/i);
    expect(message.textContent.toLowerCase()).toMatch(/wait/);
  });

  it('refuses an empty or malformed address without calling Supabase', async () => {
    const reset = vi.spyOn(supabase.auth, 'resetPasswordForEmail');

    renderSignIn();
    // No email typed at all.
    fireEvent.click(forgotButton());
    await screen.findByText(/enter the email address/i);
    expect(reset).not.toHaveBeenCalled();

    // Present but not an address.
    typeEmail('not-an-email');
    fireEvent.click(forgotButton());
    await screen.findByText(/enter the email address/i);
    expect(reset).not.toHaveBeenCalled();
  });

  it('is not offered on the sign-up form, where there is no account to recover', () => {
    renderSignIn('signup');
    expect(screen.queryByRole('button', { name: /forgot password/i })).toBeNull();
  });

  it('does not submit the sign-in form when pressed', async () => {
    // A <button> inside a <form> defaults to type="submit". Without type="button" a press
    // would run the sign-in handler against whatever is in the password field, attempting
    // an authentication the user did not ask for -- and, on a correct password, signing
    // them in instead of emailing them a reset link.
    const reset = vi
      .spyOn(supabase.auth, 'resetPasswordForEmail')
      .mockResolvedValueOnce({ data: {}, error: null });
    const signIn = vi.spyOn(supabase.auth, 'signInWithPassword');

    renderSignIn();
    typeEmail();
    fireEvent.change(screen.getByLabelText(/^Password$/i), { target: { value: 'whatever-is-typed' } });
    fireEvent.click(forgotButton());

    await waitFor(() => expect(reset).toHaveBeenCalled());
    expect(signIn, 'the forgot-password control submitted the sign-in form').not.toHaveBeenCalled();
    // Read the attribute directly. This suite does not load @testing-library/jest-dom, so
    // its DOM matchers are not registered and using one raises "Invalid Chai property"
    // instead of failing on the attribute.
    expect(forgotButton().getAttribute('type')).toBe('button');
  });
});
