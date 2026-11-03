/**
 * tests/unit/pages/twoFABackupCodes.test.jsx — SEC-24 / FE-11
 *
 * SEC-24: `pages/TwoFA.jsx` offers backup codes right after a successful verify, and
 * the challenge screen offers a "lost your device" recovery path through
 * `api.security.verifyBackupCode`.
 *
 * FE-11: `handleResetFactor`'s native `window.confirm` is gone. The control opens
 * `ds/ConfirmDialog`; only its own explicit confirm can reach the unenroll/re-enroll
 * call.
 *
 * `supabase.auth.mfa.*` and `supabase.auth.signOut` are spied on the real client
 * (same pattern `tests/unit/authPasswordRecovery.test.jsx` uses for
 * `resetPasswordForEmail`) — the lifecycle calls themselves are not mocked away,
 * only their network effect.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import TwoFA from '../../../src/pages/TwoFA';
import { supabase } from '../../../src/supabase';
import { api } from '../../../src/api';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate, useLocation: () => ({ state: null }) };
});

vi.mock('../../../src/api', () => ({
  api: {
    security: {
      generateBackupCodes: vi.fn(),
      verifyBackupCode: vi.fn(),
    },
  },
  // This page is only reached post-login (MFA setup/challenge for an already-authenticated
  // user), so `true` is the honest signed-in state -- same reasoning as
  // riskSettingsStrictModeSingleWrite.test.jsx. See tests/test_api_mock_factories_declare_isauthenticated.py.
  isAuthenticated: () => true,
}));

function renderTwoFA() {
  return render(
    <MemoryRouter>
      <TwoFA />
    </MemoryRouter>,
  );
}

const VERIFIED_FACTOR = { id: 'factor-1', status: 'verified', factor_type: 'totp' };

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(supabase.auth, 'getUser').mockResolvedValue({
    data: { user: { id: 'user-1', email: 'trader@vyomquant.io' } },
    error: null,
  });
});

afterEach(() => {
  cleanup();
});

describe('FE-11: Reset Authenticator no longer uses a native dialog', () => {
  function mockExistingFactorSession() {
    vi.spyOn(supabase.auth.mfa, 'listFactors').mockResolvedValue({
      data: { totp: [VERIFIED_FACTOR], all: [VERIFIED_FACTOR] },
      error: null,
    });
    vi.spyOn(supabase.auth.mfa, 'challenge').mockResolvedValue({
      data: { id: 'challenge-1' },
      error: null,
    });
  }

  it('opens ds/ConfirmDialog instead of window.confirm, and cancel does nothing', async () => {
    mockExistingFactorSession();
    const confirmSpy = vi.spyOn(window, 'confirm');
    const unenrollSpy = vi.spyOn(supabase.auth.mfa, 'unenroll');

    renderTwoFA();
    const resetButton = await screen.findByRole('button', { name: /Reset Authenticator/i });
    fireEvent.click(resetButton);

    // The in-app dialog renders — never the native one.
    expect(await screen.findByRole('dialog')).toBeDefined();
    expect(confirmSpy).not.toHaveBeenCalled();

    fireEvent.click(document.querySelector('[data-ds="confirm-dialog-cancel"]'));

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull();
    });
    expect(unenrollSpy).not.toHaveBeenCalled();
  });

  it('only the dialog\'s explicit confirm reaches unenroll/re-enroll', async () => {
    mockExistingFactorSession();
    const unenrollSpy = vi.spyOn(supabase.auth.mfa, 'unenroll').mockResolvedValue({ error: null });
    const enrollSpy = vi.spyOn(supabase.auth.mfa, 'enroll').mockResolvedValue({
      data: { id: 'factor-2', totp: { secret: 'NEWSECRET', qr_code: '' } },
      error: null,
    });

    renderTwoFA();
    const resetButton = await screen.findByRole('button', { name: /Reset Authenticator/i });
    fireEvent.click(resetButton);
    await screen.findByRole('dialog');

    fireEvent.click(document.querySelector('[data-ds="confirm-dialog-confirm"]'));

    await waitFor(() => {
      expect(unenrollSpy).toHaveBeenCalledWith({ factorId: 'factor-1' });
      expect(enrollSpy).toHaveBeenCalled();
    });
  });
});

describe('SEC-24: backup codes offered after a successful verify', () => {
  function mockFreshEnrollment() {
    vi.spyOn(supabase.auth.mfa, 'listFactors').mockResolvedValue({
      data: { totp: [], all: [] },
      error: null,
    });
    vi.spyOn(supabase.auth.mfa, 'enroll').mockResolvedValue({
      data: { id: 'factor-new', totp: { secret: 'ABCDEF', qr_code: '' } },
      error: null,
    });
  }

  it('shows the backup-codes screen instead of auto-redirecting, and generate calls the API', async () => {
    mockFreshEnrollment();
    vi.spyOn(supabase.auth.mfa, 'challenge').mockResolvedValue({ data: { id: 'c-1' }, error: null });
    vi.spyOn(supabase.auth.mfa, 'verify').mockResolvedValue({ data: {}, error: null });
    api.security.generateBackupCodes.mockResolvedValue({
      codes: ['AAAAA-AAAAA', 'BBBBB-BBBBB'],
      count: 2,
    });

    renderTwoFA();
    await screen.findByText(/I've Scanned It/i);
    fireEvent.click(screen.getByText(/I've Scanned It/i));

    // Step 2: enter a 6-digit code and submit.
    const digitInputs = await screen.findAllByLabelText(/Verification code digit/i);
    digitInputs.forEach((input, i) => fireEvent.change(input, { target: { value: String((i + 1) % 10) } }));
    fireEvent.click(screen.getByRole('button', { name: /Verify & Complete/i }));

    expect(await screen.findByText(/Generate backup codes/i)).toBeDefined();
    expect(mockNavigate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText(/Generate backup codes/i));

    await waitFor(() => {
      expect(api.security.generateBackupCodes).toHaveBeenCalled();
    });
    expect(await screen.findByText('AAAAA-AAAAA')).toBeDefined();
    expect(await screen.findByText('BBBBB-BBBBB')).toBeDefined();

    fireEvent.click(screen.getByText(/Continue to dashboard/i));
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/app/dashboard', { replace: true });
    });
  });

  it('a failed generate does not block reaching the dashboard', async () => {
    mockFreshEnrollment();
    vi.spyOn(supabase.auth.mfa, 'challenge').mockResolvedValue({ data: { id: 'c-1' }, error: null });
    vi.spyOn(supabase.auth.mfa, 'verify').mockResolvedValue({ data: {}, error: null });
    api.security.generateBackupCodes.mockRejectedValue(new Error('network down'));

    renderTwoFA();
    await screen.findByText(/I've Scanned It/i);
    fireEvent.click(screen.getByText(/I've Scanned It/i));
    const digitInputs = await screen.findAllByLabelText(/Verification code digit/i);
    digitInputs.forEach((input, i) => fireEvent.change(input, { target: { value: String((i + 1) % 10) } }));
    fireEvent.click(screen.getByRole('button', { name: /Verify & Complete/i }));

    fireEvent.click(await screen.findByText(/Generate backup codes/i));
    expect(await screen.findByText(/network down/i)).toBeDefined();

    // "Skip" is always available even when generation failed.
    fireEvent.click(screen.getByText(/Skip for now/i));
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/app/dashboard', { replace: true });
    });
  });
});

describe('SEC-24: lost-device recovery on the challenge screen', () => {
  function mockExistingFactorSession() {
    vi.spyOn(supabase.auth.mfa, 'listFactors').mockResolvedValue({
      data: { totp: [VERIFIED_FACTOR], all: [VERIFIED_FACTOR] },
      error: null,
    });
    vi.spyOn(supabase.auth.mfa, 'challenge').mockResolvedValue({
      data: { id: 'challenge-1' },
      error: null,
    });
  }

  it('offers "lost your device" only once a factor already exists, and calls the recovery API', async () => {
    mockExistingFactorSession();
    vi.spyOn(supabase.auth, 'signOut').mockResolvedValue({ error: null });
    api.security.verifyBackupCode.mockResolvedValue({ success: true, unenrolled_factors: ['factor-1'] });

    renderTwoFA();
    const link = await screen.findByText(/Lost your device\?/i);
    fireEvent.click(link);

    const input = await screen.findByLabelText('Backup code');
    fireEvent.change(input, { target: { value: 'AAAAA-AAAAA' } });
    fireEvent.click(screen.getByText(/Use backup code/i));

    await waitFor(() => {
      expect(api.security.verifyBackupCode).toHaveBeenCalledWith('AAAAA-AAAAA');
    });
    await waitFor(() => {
      expect(supabase.auth.signOut).toHaveBeenCalled();
    });
  });

  it('shows a clear rejection for an invalid or already-used code, and never signs out', async () => {
    mockExistingFactorSession();
    const signOutSpy = vi.spyOn(supabase.auth, 'signOut');
    api.security.verifyBackupCode.mockRejectedValue({ response: { status: 401 } });

    renderTwoFA();
    fireEvent.click(await screen.findByText(/Lost your device\?/i));
    fireEvent.change(await screen.findByLabelText('Backup code'), {
      target: { value: 'WRONG-CODE' },
    });
    fireEvent.click(screen.getByText(/Use backup code/i));

    expect(await screen.findByText(/Invalid or already-used backup code/i)).toBeDefined();
    expect(signOutSpy).not.toHaveBeenCalled();
  });

  it('surfaces a rate-limit response distinctly from an invalid code', async () => {
    mockExistingFactorSession();
    api.security.verifyBackupCode.mockRejectedValue({
      response: { status: 429, data: { detail: 'Too many failed backup-code attempts; try again later.' } },
    });

    renderTwoFA();
    fireEvent.click(await screen.findByText(/Lost your device\?/i));
    fireEvent.change(await screen.findByLabelText('Backup code'), {
      target: { value: 'WRONG-CODE' },
    });
    fireEvent.click(screen.getByText(/Use backup code/i));

    expect(await screen.findByText(/Too many failed backup-code attempts/i)).toBeDefined();
  });
});
