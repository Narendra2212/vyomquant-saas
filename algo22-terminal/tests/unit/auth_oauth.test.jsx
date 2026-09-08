import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AuthPage from '../../src/pages/AuthPage';
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

describe('Google OAuth Authentication Integration', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockNavigate.mockClear();
    sessionStorage.clear();
  });

  it('renders "Continue with Google" button on signin and signup modes', () => {
    const { rerender } = render(
      <MemoryRouter>
        <AuthPage mode="signin" />
      </MemoryRouter>
    );

    const googleBtn = screen.getByTestId('google-auth-button');
    expect(googleBtn).toBeDefined();
    expect(googleBtn.textContent).toContain('Continue with Google');

    rerender(
      <MemoryRouter>
        <AuthPage mode="signup" />
      </MemoryRouter>
    );

    const signupGoogleBtn = screen.getByTestId('google-auth-button');
    expect(signupGoogleBtn).toBeDefined();
    expect(signupGoogleBtn.textContent).toContain('Continue with Google');
  });

  it('triggers supabase.auth.signInWithOAuth with provider="google" and redirectTo on click', async () => {
    const mockSignInWithOAuth = vi.fn().mockResolvedValue({ data: { provider: 'google', url: 'https://accounts.google.com/oauth' }, error: null });
    supabase.auth.signInWithOAuth = mockSignInWithOAuth;

    render(
      <MemoryRouter>
        <AuthPage mode="signin" />
      </MemoryRouter>
    );

    const googleBtn = screen.getByTestId('google-auth-button');
    fireEvent.click(googleBtn);

    await waitFor(() => {
      expect(mockSignInWithOAuth).toHaveBeenCalledTimes(1);
      expect(mockSignInWithOAuth).toHaveBeenCalledWith({
        provider: 'google',
        options: {
          redirectTo: expect.stringContaining('/app/dashboard'),
          queryParams: {
            access_type: 'offline',
            prompt: 'consent',
          },
        },
      });
    });
  });

  it('displays error feedback and resets loading state when signInWithOAuth fails', async () => {
    const mockSignInWithOAuth = vi.fn().mockResolvedValue({
      data: null,
      error: new Error('Google OAuth provider is not configured in Supabase'),
    });
    supabase.auth.signInWithOAuth = mockSignInWithOAuth;

    render(
      <MemoryRouter>
        <AuthPage mode="signin" />
      </MemoryRouter>
    );

    const googleBtn = screen.getByTestId('google-auth-button');
    fireEvent.click(googleBtn);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeDefined();
      expect(screen.getByText(/Google OAuth provider is not configured in Supabase/i)).toBeDefined();
    });
  });

  it('disables Google OAuth button while an authentication action is loading', async () => {
    let resolveOAuth;
    const pendingPromise = new Promise((resolve) => {
      resolveOAuth = resolve;
    });
    supabase.auth.signInWithOAuth = vi.fn().mockReturnValue(pendingPromise);

    render(
      <MemoryRouter>
        <AuthPage mode="signin" />
      </MemoryRouter>
    );

    const googleBtn = screen.getByTestId('google-auth-button');
    fireEvent.click(googleBtn);

    expect(googleBtn.disabled).toBe(true);
    expect(screen.getByText(/Redirecting to Google\.\.\./i)).toBeDefined();

    // Resolve and reset
    resolveOAuth({ data: null, error: null });
  });

  it('preserves Password + Email OTP flow alongside Google OAuth', async () => {
    const mockSignInWithPassword = vi.fn().mockResolvedValue({
      data: { user: { id: 'user-001', email: 'trader@vyomquant.io' }, session: null },
      error: null,
    });
    const mockSignInWithOtp = vi.fn().mockResolvedValue({ data: {}, error: null });
    supabase.auth.signInWithPassword = mockSignInWithPassword;
    supabase.auth.signInWithOtp = mockSignInWithOtp;

    render(
      <MemoryRouter>
        <AuthPage mode="signin" />
      </MemoryRouter>
    );

    // Fill in Password flow
    fireEvent.change(screen.getByPlaceholderText('trader@vyomquant.io'), {
      target: { value: 'trader@vyomquant.io' },
    });
    fireEvent.change(screen.getByLabelText(/^Password$/i), {
      target: { value: 'SecureQuant2026!' },
    });

    const submitBtn = screen.getByRole('button', { name: /SIGN IN →/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mockSignInWithPassword).toHaveBeenCalledTimes(1);
      expect(mockSignInWithOtp).toHaveBeenCalledTimes(1);
      // Transitions to OTP step
      expect(screen.getByText(/Enter 6-Digit Email Code/i)).toBeDefined();
    });
  });
});
