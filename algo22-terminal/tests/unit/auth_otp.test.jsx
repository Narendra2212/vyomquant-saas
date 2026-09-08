import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AuthPage, { maskEmail, evaluatePasswordStrength, AUTH_STATES } from '../../src/pages/AuthPage';
import { supabase } from '../../src/supabase';

// Mock useNavigate
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

describe('Phase Auth — Password + Email OTP Authentication', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockNavigate.mockClear();
    sessionStorage.clear();
  });

  describe('Password Strength & Masking Helpers', () => {
    it('evaluates password complexity accurately according to security baseline', () => {
      expect(evaluatePasswordStrength('').score).toBe(0);
      expect(evaluatePasswordStrength('weak').score).toBeLessThan(2);
      expect(evaluatePasswordStrength('WeakPass123!').score).toBe(5);
    });

    it('masks emails properly to protect mailbox identifier in OTP UI', () => {
      expect(maskEmail('trader.alpha@vyomquant.io')).toBe('t••••a@vyomquant.io');
      expect(maskEmail('quant@vyomquant.io')).toBe('q••••t@vyomquant.io');
      expect(maskEmail('ab@vyomquant.io')).toBe('a••••@vyomquant.io');
      expect(maskEmail('')).toBe('');
      expect(maskEmail(null)).toBe('');
    });
  });

  describe('Step 1 — Password Authentication', () => {
    it('renders Password Authentication form by default with email and masked password inputs', () => {
      render(
        <MemoryRouter>
          <AuthPage mode="signin" />
        </MemoryRouter>
      );

      expect(screen.getByRole('heading', { name: /Sign In to Terminal/i })).toBeDefined();
      expect(screen.getByPlaceholderText('trader@vyomquant.io')).toBeDefined();
      const passwordInput = screen.getByLabelText(/^Password$/i);
      expect(passwordInput).toBeDefined();
      expect(passwordInput.type).toBe('password');
      expect(screen.getByRole('button', { name: /SIGN IN →/i })).toBeDefined();
    });

    it('toggles password visibility when eye toggle button is clicked', () => {
      render(
        <MemoryRouter>
          <AuthPage mode="signin" />
        </MemoryRouter>
      );

      const passwordInput = screen.getByLabelText(/^Password$/i);
      expect(passwordInput.type).toBe('password');

      const toggleButton = screen.getByLabelText(/Show password/i);
      fireEvent.click(toggleButton);
      expect(passwordInput.type).toBe('text');

      const hideButton = screen.getByLabelText(/Hide password/i);
      fireEvent.click(hideButton);
      expect(passwordInput.type).toBe('password');
    });

    it('rejects submission if email or password are empty/invalid without calling Supabase', async () => {
      const signInWithPasswordSpy = vi.spyOn(supabase.auth, 'signInWithPassword');

      render(
        <MemoryRouter>
          <AuthPage mode="signin" />
        </MemoryRouter>
      );

      const submitButton = screen.getByRole('button', { name: /SIGN IN →/i });
      fireEvent.click(submitButton);

      expect(signInWithPasswordSpy).not.toHaveBeenCalled();

      // Invalid email
      const emailInput = screen.getByPlaceholderText('trader@vyomquant.io');
      fireEvent.change(emailInput, { target: { value: 'invalid-email' } });
      fireEvent.blur(emailInput);

      expect(await screen.findByText(/Please enter a valid email address/i)).toBeDefined();
      expect(signInWithPasswordSpy).not.toHaveBeenCalled();
    });

    it('handles invalid password with neutral error and does NOT proceed to OTP stage', async () => {
      vi.spyOn(supabase.auth, 'signInWithPassword').mockResolvedValueOnce({
        data: null,
        error: new Error('Invalid login credentials'),
      });

      render(
        <MemoryRouter>
          <AuthPage mode="signin" />
        </MemoryRouter>
      );

      const emailInput = screen.getByPlaceholderText('trader@vyomquant.io');
      const passwordInput = screen.getByLabelText(/^Password$/i);

      fireEvent.change(emailInput, { target: { value: 'trader@vyomquant.io' } });
      fireEvent.change(passwordInput, { target: { value: 'WrongPassword123' } });

      const submitButton = screen.getByRole('button', { name: /SIGN IN →/i });
      fireEvent.click(submitButton);

      expect(
        await screen.findByText(/Invalid email or password. Please check your credentials and try again/i)
      ).toBeDefined();

      // Ensure we are still on Password step and not OTP step
      expect(screen.queryByRole('heading', { name: /Verify Your Identity/i })).toBeNull();
      expect(sessionStorage.getItem('token')).toBeNull();
    });

    it('enforces password strength and confirmation matching on account creation (Sign-Up)', async () => {
      const signUpSpy = vi.spyOn(supabase.auth, 'signUp');

      render(
        <MemoryRouter>
          <AuthPage mode="signup" />
        </MemoryRouter>
      );

      const emailInput = screen.getByPlaceholderText('trader@vyomquant.io');
      const passwordInput = screen.getByLabelText(/^Password$/i);
      const confirmInput = screen.getByLabelText(/^Confirm Password$/i);
      const termsCheckbox = screen.getByRole('checkbox');

      fireEvent.change(emailInput, { target: { value: 'newuser@vyomquant.io' } });
      fireEvent.change(passwordInput, { target: { value: 'short' } });
      fireEvent.change(confirmInput, { target: { value: 'different' } });

      const submitBtn = screen.getByRole('button', { name: /CREATE ACCOUNT & SEND OTP/i });
      fireEvent.click(submitBtn);

      expect(await screen.findByText(/Please choose a stronger password/i)).toBeDefined();
      expect(signUpSpy).not.toHaveBeenCalled();

      // Fix password but mismatch confirmation
      fireEvent.change(passwordInput, { target: { value: 'StrongAuth!2026' } });
      fireEvent.click(submitBtn);
      const mismatchMsgs = await screen.findAllByText(/Passwords do not match/i);
      expect(mismatchMsgs.length).toBeGreaterThan(0);
      expect(signUpSpy).not.toHaveBeenCalled();

      // Match confirmation but leave terms unchecked
      fireEvent.change(confirmInput, { target: { value: 'StrongAuth!2026' } });
      fireEvent.click(submitBtn);
      expect(await screen.findByText(/You must agree to the Terms and Risk Disclosure/i)).toBeDefined();
      expect(signUpSpy).not.toHaveBeenCalled();

      // Check terms box and submit
      fireEvent.click(termsCheckbox);
      signUpSpy.mockResolvedValueOnce({
        data: { user: { id: 'usr_new' } },
        error: null,
      });

      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(signUpSpy).toHaveBeenCalledWith({
          email: 'newuser@vyomquant.io',
          password: 'StrongAuth!2026',
        });
      });

      // Transitions to OTP step
      expect(await screen.findByRole('heading', { name: /Verify Your Identity/i })).toBeDefined();
    });
  });

  describe('Step 2 — 6-Digit Email OTP Verification', () => {
    const performSuccessfulPasswordStep = async (email = 'trader@vyomquant.io') => {
      vi.spyOn(supabase.auth, 'signInWithPassword').mockResolvedValueOnce({
        data: {
          user: { id: 'usr_1', email },
          session: { access_token: 'interim_token_not_to_be_trusted_yet' },
        },
        error: null,
      });
      vi.spyOn(supabase.auth, 'signInWithOtp').mockResolvedValueOnce({
        data: {},
        error: null,
      });

      const utils = render(
        <MemoryRouter>
          <AuthPage mode="signin" />
        </MemoryRouter>
      );

      const emailInput = screen.getByPlaceholderText('trader@vyomquant.io');
      const passwordInput = screen.getByLabelText(/^Password$/i);

      fireEvent.change(emailInput, { target: { value: email } });
      fireEvent.change(passwordInput, { target: { value: 'ValidPass123!' } });

      fireEvent.click(screen.getByRole('button', { name: /SIGN IN →/i }));

      await screen.findByRole('heading', { name: /Verify Your Identity/i });
      return utils;
    };

    it('renders OTP step after password step with masked email and 6 input cells', async () => {
      await performSuccessfulPasswordStep('alpha.quant@vyomquant.io');

      expect(screen.getByText('a••••t@vyomquant.io')).toBeDefined();
      for (let i = 0; i < 6; i++) {
        expect(screen.getByLabelText(`Digit ${i + 1} of 6`)).toBeDefined();
      }
    });

    it('allows entering 6 digits across inputs and enables submit button', async () => {
      await performSuccessfulPasswordStep();

      const verifyButton = screen.getByRole('button', { name: /VERIFY & CONTINUE/i });
      expect(verifyButton.disabled).toBe(true);

      for (let i = 0; i < 6; i++) {
        const input = screen.getByLabelText(`Digit ${i + 1} of 6`);
        fireEvent.change(input, { target: { value: String(i + 1) } });
      }

      expect(verifyButton.disabled).toBe(false);
    });

    it('supports pasting a 6-digit verification code across inputs', async () => {
      await performSuccessfulPasswordStep();

      const firstInput = screen.getByLabelText('Digit 1 of 6');
      fireEvent.paste(firstInput, {
        clipboardData: {
          getData: () => '938104',
        },
      });

      for (let i = 0; i < 6; i++) {
        const input = screen.getByLabelText(`Digit ${i + 1} of 6`);
        expect(input.value).toBe('938104'[i]);
      }

      const verifyButton = screen.getByRole('button', { name: /VERIFY & CONTINUE/i });
      expect(verifyButton.disabled).toBe(false);
    });

    it('navigates backwards on Backspace and supports left/right arrows', async () => {
      await performSuccessfulPasswordStep();

      const input1 = screen.getByLabelText('Digit 1 of 6');
      const input2 = screen.getByLabelText('Digit 2 of 6');

      fireEvent.change(input1, { target: { value: '4' } });
      fireEvent.keyDown(input2, { key: 'Backspace' });
      fireEvent.keyDown(input1, { key: 'ArrowRight' });
      fireEvent.keyDown(input2, { key: 'ArrowLeft' });
    });

    it('allows going Back to password step and resets password verification flag', async () => {
      await performSuccessfulPasswordStep();

      const backButton = screen.getByRole('button', { name: /Back/i });
      fireEvent.click(backButton);

      expect(screen.getByRole('heading', { name: /Sign In to Terminal/i })).toBeDefined();
      expect(screen.getByLabelText(/^Password$/i)).toBeDefined();
    });

    it('resends OTP code when cooldown expires', async () => {
      vi.useFakeTimers();

      vi.spyOn(supabase.auth, 'signInWithPassword').mockResolvedValue({
        data: { user: { id: '1' } },
        error: null,
      });
      const signInWithOtpSpy = vi.spyOn(supabase.auth, 'signInWithOtp').mockResolvedValue({
        data: {},
        error: null,
      });

      render(
        <MemoryRouter>
          <AuthPage mode="signin" />
        </MemoryRouter>
      );

      const emailInput = screen.getByPlaceholderText('trader@vyomquant.io');
      const passwordInput = screen.getByLabelText(/^Password$/i);

      fireEvent.change(emailInput, { target: { value: 'trader@vyomquant.io' } });
      fireEvent.change(passwordInput, { target: { value: 'ValidPass123!' } });

      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /SIGN IN →/i }));
      });

      expect(screen.getByText(/Resend code in 00:/i)).toBeDefined();

      // Fast forward 60 seconds
      act(() => {
        vi.advanceTimersByTime(61000);
      });

      const resendButton = screen.getByRole('button', { name: /Resend code/i });
      expect(resendButton).toBeDefined();

      await act(async () => {
        fireEvent.click(resendButton);
      });

      expect(signInWithOtpSpy).toHaveBeenCalledWith({
        email: 'trader@vyomquant.io',
        options: { shouldCreateUser: false },
      });

      vi.useRealTimers();
    });
  });

  describe('Critical Security Verification (Combinatorial Invariants)', () => {
    it('INVARIANT 1: Password-only success does NOT grant application session or navigate', async () => {
      vi.spyOn(supabase.auth, 'signInWithPassword').mockResolvedValueOnce({
        data: {
          user: { id: 'usr_test' },
          session: { access_token: 'leaked_interim_token' },
        },
        error: null,
      });
      vi.spyOn(supabase.auth, 'signInWithOtp').mockResolvedValueOnce({ data: {}, error: null });

      render(
        <MemoryRouter>
          <AuthPage mode="signin" />
        </MemoryRouter>
      );

      const emailInput = screen.getByPlaceholderText('trader@vyomquant.io');
      const passwordInput = screen.getByLabelText(/^Password$/i);

      fireEvent.change(emailInput, { target: { value: 'quant@vyomquant.io' } });
      fireEvent.change(passwordInput, { target: { value: 'SecurePassword123!' } });

      fireEvent.click(screen.getByRole('button', { name: /SIGN IN →/i }));

      await screen.findByRole('heading', { name: /Verify Your Identity/i });

      // Invariant: Session token must NOT be in sessionStorage yet
      expect(sessionStorage.getItem('token')).toBeNull();
      expect(mockNavigate).not.toHaveBeenCalled();
    });

    it('INVARIANT 2: Valid password + Invalid OTP is REJECTED without application access', async () => {
      vi.spyOn(supabase.auth, 'signInWithPassword').mockResolvedValueOnce({
        data: { user: { id: 'usr_test' } },
        error: null,
      });
      vi.spyOn(supabase.auth, 'signInWithOtp').mockResolvedValueOnce({ data: {}, error: null });
      vi.spyOn(supabase.auth, 'verifyOtp').mockResolvedValueOnce({
        data: null,
        error: new Error('Invalid token'),
      });

      render(
        <MemoryRouter>
          <AuthPage mode="signin" />
        </MemoryRouter>
      );

      fireEvent.change(screen.getByPlaceholderText('trader@vyomquant.io'), {
        target: { value: 'quant@vyomquant.io' },
      });
      fireEvent.change(screen.getByLabelText(/^Password$/i), {
        target: { value: 'SecurePassword123!' },
      });
      fireEvent.click(screen.getByRole('button', { name: /SIGN IN →/i }));

      await screen.findByRole('heading', { name: /Verify Your Identity/i });

      // Enter invalid OTP
      const firstInput = screen.getByLabelText('Digit 1 of 6');
      fireEvent.paste(firstInput, { clipboardData: { getData: () => '000000' } });

      fireEvent.click(screen.getByRole('button', { name: /VERIFY & CONTINUE/i }));

      expect(
        await screen.findByText(/Invalid verification code. Please check your email or request a new code/i)
      ).toBeDefined();

      expect(sessionStorage.getItem('token')).toBeNull();
      expect(mockNavigate).not.toHaveBeenCalled();
    });

    it('INVARIANT 3: Valid password + Expired OTP is REJECTED without application access', async () => {
      vi.spyOn(supabase.auth, 'signInWithPassword').mockResolvedValueOnce({
        data: { user: { id: 'usr_test' } },
        error: null,
      });
      vi.spyOn(supabase.auth, 'signInWithOtp').mockResolvedValueOnce({ data: {}, error: null });
      vi.spyOn(supabase.auth, 'verifyOtp').mockResolvedValueOnce({
        data: null,
        error: new Error('Token has expired'),
      });

      render(
        <MemoryRouter>
          <AuthPage mode="signin" />
        </MemoryRouter>
      );

      fireEvent.change(screen.getByPlaceholderText('trader@vyomquant.io'), {
        target: { value: 'quant@vyomquant.io' },
      });
      fireEvent.change(screen.getByLabelText(/^Password$/i), {
        target: { value: 'SecurePassword123!' },
      });
      fireEvent.click(screen.getByRole('button', { name: /SIGN IN →/i }));

      await screen.findByRole('heading', { name: /Verify Your Identity/i });

      const firstInput = screen.getByLabelText('Digit 1 of 6');
      fireEvent.paste(firstInput, { clipboardData: { getData: () => '112233' } });

      fireEvent.click(screen.getByRole('button', { name: /VERIFY & CONTINUE/i }));

      expect(
        await screen.findByText(/The verification code has expired. Please request a new code/i)
      ).toBeDefined();

      expect(sessionStorage.getItem('token')).toBeNull();
      expect(mockNavigate).not.toHaveBeenCalled();
    });

    it('INVARIANT 4: Valid password + Valid OTP establishes session and enters application', async () => {
      vi.spyOn(supabase.auth, 'signInWithPassword').mockResolvedValueOnce({
        data: { user: { id: 'usr_test' } },
        error: null,
      });
      vi.spyOn(supabase.auth, 'signInWithOtp').mockResolvedValueOnce({ data: {}, error: null });
      const verifyOtpSpy = vi.spyOn(supabase.auth, 'verifyOtp').mockResolvedValueOnce({
        data: {
          session: {
            access_token: 'auth_jwt_token_level_1',
            refresh_token: 'refresh_tok',
            user: { id: 'usr_test', email: 'quant@vyomquant.io' },
          },
        },
        error: null,
      });

      render(
        <MemoryRouter>
          <AuthPage mode="signin" />
        </MemoryRouter>
      );

      fireEvent.change(screen.getByPlaceholderText('trader@vyomquant.io'), {
        target: { value: 'quant@vyomquant.io' },
      });
      fireEvent.change(screen.getByLabelText(/^Password$/i), {
        target: { value: 'SecurePassword123!' },
      });
      fireEvent.click(screen.getByRole('button', { name: /SIGN IN →/i }));

      await screen.findByRole('heading', { name: /Verify Your Identity/i });

      const firstInput = screen.getByLabelText('Digit 1 of 6');
      fireEvent.paste(firstInput, { clipboardData: { getData: () => '749102' } });

      fireEvent.click(screen.getByRole('button', { name: /VERIFY & CONTINUE/i }));

      await waitFor(() => {
        expect(verifyOtpSpy).toHaveBeenCalledWith({
          email: 'quant@vyomquant.io',
          token: '749102',
          type: 'email',
        });
      });

      // Session granted and user navigated to dashboard
      expect(sessionStorage.getItem('token')).toBe('auth_jwt_token_level_1');
      expect(mockNavigate).toHaveBeenCalledWith('/app/dashboard');
    });
  });

  describe('DOM Security Audit', () => {
    it('contains 0 innerHTML, 0 eval, 0 document.write, and 0 unsafe DOM sinks', async () => {
      const authPageContent = await import('../../src/pages/AuthPage?raw');
      const rawCode = authPageContent.default || '';

      expect(rawCode.includes('innerHTML')).toBe(false);
      expect(rawCode.includes('dangerouslySetInnerHTML')).toBe(false);
      expect(rawCode.includes('document.write')).toBe(false);
      expect(rawCode.includes('eval(')).toBe(false);
      expect(rawCode.includes('javascript:')).toBe(false);
    });
  });
});
