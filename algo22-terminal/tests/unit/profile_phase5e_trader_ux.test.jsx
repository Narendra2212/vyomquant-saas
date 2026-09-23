import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Profile from '../../src/pages/Profile';
import * as userModule from '../../src/api/modules/user';
import * as referralModule from '../../src/api/modules/referral';

/**
 * Phase 5E Profile trader-UX battery.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * SIX ASSERTIONS CHANGED AT retail-ui-simplification TASK 7.8 — see the twin note in
 * `profile_phase5b_remediation.test.jsx` for the full argument
 * ═══════════════════════════════════════════════════════════════════════════
 * Four of the six were asserting a FABRICATION, and one of those four is the most
 * consequential one this spec has found:
 *
 *   * `'ACCOUNT ACTIVE'` and `'PROTECTED'` — two green pills rendered as static JSX. Neither
 *     had a read behind it. Both are declared ❌ UNAVAILABLE in `design/pageFields.js` now,
 *     and the assertions are on the markers' accessible names.
 *   * `'Automation Account Context'` — the panel title. Its four figures came from
 *     `GET /api/stats`, which `backend_app/main.py:938` declares WITHOUT
 *     `Depends(get_current_user)` and calls with the literal user id `"public"`, so the panel
 *     reported the platform under a title claiming it reported the account. It is
 *     *Platform-wide statistics* now and says so in prose as well.
 *   * `/Total Strategies: 20/` — a label and a figure in one text node. They are a
 *     `ds/Metric` label and value now.
 *   * `'$450.00'` / `'$3200.00'` — `(referral.x || 0).toFixed(2)`. Grouped, with an explicit
 *     `USD`, through `ds/Metric`.
 *   * `'Authentication required…'` and `/Retry/i` — the page's own error screen; see the twin
 *     note.
 *
 * Every navigation assertion in this file is untouched and still passes, which is what proves
 * no route was lost: `/app/2fa`, `/app/security-logs`, `/app/exchange`, `/app/strategies`,
 * `/app/billing`.
 */
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
    // Was `screen.getByText('ACCOUNT ACTIVE')` — a green pill with a green dot, as static
    // JSX. Being able to open the page is not evidence that the account is in good standing,
    // which is what that pill claimed. The marker carries the reason instead.
    expect(screen.getByLabelText('Account status: not available')).toBeDefined();
    expect(screen.queryByText('ACCOUNT ACTIVE')).toBeNull();
    expect(screen.getByText('Enterprise Tier')).toBeDefined();
    // Member since is real and still rendered, now with its own declared reason behind it.
    expect(screen.getByText('Jan 2026')).toBeDefined();
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
    // Was `screen.getByText('PROTECTED')` — a green pill with a tick, as static JSX, beside
    // an *MFA AUTHENTICATION* tile that said *Configured* with equally little behind it. Both
    // are declared UNAVAILABLE now and both markers carry their reason. The MFA one is the
    // worst substitution in this spec: an account with no second factor was told it had one.
    expect(screen.getByLabelText('Security posture: not available')).toBeDefined();
    expect(screen.getByLabelText('Multi-factor authentication: not available')).toBeDefined();
    expect(screen.queryByText('PROTECTED')).toBeNull();
    expect(screen.queryByText('Configured')).toBeNull();
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

    // Was `'Automation Account Context'`. The four figures under that title came from the
    // PLATFORM-WIDE statistics endpoint, so the title was the claim rather than the figures.
    expect(await screen.findByText('Platform-wide statistics')).toBeDefined();
    expect(screen.getByText(/reports across all accounts rather than yours/i)).toBeDefined();
    expect(screen.getByText('8')).toBeDefined(); // Active bots, platform-wide
    // Was `/Total Strategies: 20/` — a label and a figure in one text node. They are a
    // `ds/Metric` label and value now, and the label says whose figure it is.
    expect(screen.getByText('Strategies (platform)')).toBeDefined();
    expect(screen.getByText('20')).toBeDefined();

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
    // Was `'$450.00'` and `'$3200.00'` — `(referral.x || 0).toFixed(2)`, so a failed read
    // published a balance. Grouped, with an explicit unit, through `ds/Metric`.
    expect(screen.getByText('450.00')).toBeDefined();
    expect(screen.getByText('3,200.00')).toBeDefined();
    // The *20% RECURRING* pill was a commission rate `ReferralStatsResponse` does not carry.
    expect(screen.getByLabelText('Commission rate: not available')).toBeDefined();
    expect(screen.queryByText('20% RECURRING')).toBeNull();
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

    // Was the page's own authored string plus *Go to Login* — see the twin note. The identity
    // panel renders `ds/Panel`'s error arm with `design/errorCopy.js`'s authored copy, and
    // the recovery controls sit in its header, which `ds/Panel` renders in every state.
    expect(await screen.findByRole('button', { name: /Reload the page/i })).toBeDefined();
    expect(screen.getByRole('button', { name: /Try again/i })).toBeDefined();
    expect(screen.queryByText('Apex Quant Trader')).toBeNull();
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

    // Was `/Retry/i`. *Retry* and *Retry Connection* were one control under two labels.
    const retryBtn = await screen.findByRole('button', { name: /Try again/i });
    fireEvent.click(retryBtn);

    expect(await screen.findByText('Apex Quant Trader')).toBeDefined();
    expect(profileSpy).toHaveBeenCalledTimes(2);
  });
});
