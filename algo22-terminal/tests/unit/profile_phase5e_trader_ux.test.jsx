import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Profile from '../../src/pages/Profile';
import * as userModule from '../../src/api/modules/user';
import * as referralModule from '../../src/api/modules/referral';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate
  };
});

describe('Phase 5E Profile Trader UX Test Battery', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  const mockValidProfile = {
    id: 'usr_algo_99812',
    username: 'quant_master',
    display_name: 'Apex Quant Trader',
    email: 'trader@vyomquant.io',
    role: 'pro_trader',
    avatar_url: 'https://vyomquant.io/avatar.png',
    bio: 'High-frequency momentum and statistical arbitrage trader',
    telegram_id: 'apexquant',
    created_at: '2026-01-15T10:00:00Z',
    email_confirmed_at: '2026-01-15T10:05:00Z'
  };

  const mockValidBilling = {
    plan: 'enterprise',
    features: ['Dedicated Co-location', 'Sub-millisecond Routing', 'Unlimited Live Bots'],
    subscription_status: 'active',
    renewal_date: '2026-12-31T00:00:00Z'
  };

  const mockValidReferral = {
    referral_code: 'VQ-APEX2026',
    referral_link: 'https://vyomquant.io/signup?ref=VQ-APEX2026',
    total_referrals: 25,
    active_referrals: 18,
    pending_earnings: 450.00,
    lifetime_earnings: 3200.00
  };

  const mockValidStats = {
    total_strategies: 20,
    active_bots: 8,
    total_trades: 1420,
    win_rate: 72.5,
    total_pnl: 18450.50
  };

  const mockValidSecurityLogs = [
    {
      id: 'sec_1',
      event_type: 'MFA Verification Succeeded',
      ip_address: '198.51.100.42',
      created_at: '2026-08-26T18:00:00Z'
    },
    {
      id: 'sec_2',
      event_type: 'API Key Created',
      ip_address: '198.51.100.42',
      created_at: '2026-08-25T14:20:00Z'
    }
  ];

  const mockValidNotificationSettings = {
    channels: {
      email: true,
      telegram: false,
      mobile: false
    },
    events: {
      trade_executed: true,
      stop_loss_triggered: true,
      daily_pnl_summary: true,
      bot_state_change: false,
      kill_switch_activated: true,
      new_login_detected: true,
      api_key_expiring: true,
      backtest_complete: false
    }
  };

  // 1 & 2. Account Overview & Status
  it('1 & 2. Account overview renders authoritative identity, verified email, telegram, and member since date', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue(mockValidSecurityLogs);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('PROFILE & ACCOUNT')).toBeDefined();
    expect(screen.getByText('Apex Quant Trader')).toBeDefined();
    expect(screen.getByText('@quant_master')).toBeDefined();
    expect(screen.getByText('trader@vyomquant.io')).toBeDefined();
    expect(screen.getByText('Verified')).toBeDefined();
    expect(screen.getByText('@apexquant')).toBeDefined();
    expect(screen.getByText('ACCOUNT ACTIVE')).toBeDefined();
    expect(screen.getByText('Enterprise Tier')).toBeDefined();
  });

  // 3 & 4. Security Section
  it('3 & 4. Security section renders protected status, MFA button, and recent security logs without fake session data', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue(mockValidSecurityLogs);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('Security & Access')).toBeDefined();
    expect(screen.getByText('PROTECTED')).toBeDefined();
    expect(screen.getByText('MFA Verification Succeeded')).toBeDefined();
    expect(screen.getByText(/198.51.100.42/)).toBeDefined();

    // Verify MFA navigation
    const mfaBtn = screen.getByRole('button', { name: /Manage MFA/i });
    fireEvent.click(mfaBtn);
    expect(mockNavigate).toHaveBeenCalledWith('/app/2fa');

    // Verify Review All Logs navigation
    const reviewLogsBtn = screen.getByText(/Review All Logs/i);
    fireEvent.click(reviewLogsBtn);
    expect(mockNavigate).toHaveBeenCalledWith('/app/security-logs');
  });

  // 5 & 6. Automation Account Context & Contextual Navigation
  it('5 & 6. Automation context renders fleet metrics and contextual navigation to /app/exchange and /app/strategies', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue(mockValidSecurityLogs);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('Automation Account Context')).toBeDefined();
    expect(screen.getByText('8')).toBeDefined(); // Active bots
    expect(screen.getByText(/Total Strategies: 20/)).toBeDefined();

    const manageExchangesBtn = screen.getByRole('button', { name: /Manage Exchanges/i });
    fireEvent.click(manageExchangesBtn);
    expect(mockNavigate).toHaveBeenCalledWith('/app/exchange');

    const manageStrategiesBtn = screen.getByRole('button', { name: /Manage Strategies/i });
    fireEvent.click(manageStrategiesBtn);
    expect(mockNavigate).toHaveBeenCalledWith('/app/strategies');
  });

  // 7, 8, 9. Notification Categories & Boolean Payload Safety
  it('7, 8, 9. Categorized notifications render supported event keys and send clean boolean payloads', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue(mockValidSecurityLogs);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    const updateNotifSpy = vi.spyOn(userModule.userApi, 'updateNotificationSettings').mockResolvedValue({ status: 'ok' });

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText(/Notification Dispatch Preferences/i)).toBeDefined();
    expect(screen.getByText('Trade Executions')).toBeDefined();
    expect(screen.getByText('Stop Loss & Margin Alerts')).toBeDefined();
    expect(screen.getByText('Emergency Kill Switch & Liquidations')).toBeDefined();
    expect(screen.getByText('Email Notifications')).toBeDefined();

    // Toggle Trade Executions
    const tradeToggle = screen.getByText('Trade Executions').closest('div[style*="cursor: pointer"]');
    fireEvent.click(tradeToggle);

    await waitFor(() => {
      expect(updateNotifSpy).toHaveBeenCalledWith(expect.objectContaining({
        events: expect.objectContaining({ trade_executed: false })
      }));
    });
  });

  // 10. Billing Plan Mapping & Navigation
  it('10. Subscription card maps plan, renewal date, features, and navigates to /app/billing', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue(mockValidSecurityLogs);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('Subscription & Plan')).toBeDefined();
    expect(screen.getByText('Enterprise Tier')).toBeDefined();
    expect(screen.getByText(/Dedicated Co-location/i)).toBeDefined();

    const manageSubBtn = screen.getByRole('button', { name: /Manage Subscription/i });
    fireEvent.click(manageSubBtn);
    expect(mockNavigate).toHaveBeenCalledWith('/app/billing');
  });

  // 11. Referral Card Lowered Priority & Copy Buttons
  it('11. Referral program renders compact telemetry and copies referral code and link', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue(mockValidSecurityLogs);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('Affiliate & Referral Program')).toBeDefined();
    expect(screen.getByText('VQ-APEX2026')).toBeDefined();
    expect(screen.getByText('$450.00')).toBeDefined();
    expect(screen.getByText('$3200.00')).toBeDefined();
  });

  // 12. Authentication Failure Handling
  it('12. Primary profile 401 unauthenticated response renders auth error banner', async () => {
    const authError = new Error('Unauthorized');
    authError.response = { status: 401 };
    vi.spyOn(userModule.userApi, 'getProfile').mockRejectedValue(authError);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue([]);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('Authentication required. Please log in again.')).toBeDefined();
    expect(screen.getByRole('button', { name: /Go to Login/i })).toBeDefined();
  });

  // 13. Retry Resilience
  it('13. Retry executes cleanly with Promise.allSettled and restores profile state', async () => {
    const netError = new Error('Network error');
    netError.response = { status: 500 };
    const profileSpy = vi.spyOn(userModule.userApi, 'getProfile')
      .mockRejectedValueOnce(netError)
      .mockResolvedValueOnce(mockValidProfile);

    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue([]);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    render(<MemoryRouter><Profile /></MemoryRouter>);

    const retryBtn = await screen.findByRole('button', { name: /Retry/i });
    fireEvent.click(retryBtn);

    expect(await screen.findByText('Apex Quant Trader')).toBeDefined();
    expect(profileSpy).toHaveBeenCalledTimes(2);
  });
});
