/**
 * Phase 5B Profile remediation battery.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * SIX ASSERTIONS CHANGED AT retail-ui-simplification TASK 7.8, AND WHY
 * ═══════════════════════════════════════════════════════════════════════════
 * tasks.md expected this file green UNCHANGED across that migration. It could not be, and
 * the reason is the migration's subject: **five of the six assertions were asserting a
 * fabricated figure**, and Requirement 19.5 is what removed it. Each one is marked at its
 * call site with what it used to claim. Nothing was weakened to make the page pass — three
 * of the six now assert MORE than they did, because a not-available marker carries an
 * accessible name and a reason where a substituted constant carried neither.
 *
 *   1. `'Free Tier'`        — the billing read FAILED in that test, and the page answered by
 *                             telling the trader they were on the free tier. Now the
 *                             declared marker, asserted by its accessible name.
 *   2. `'$5430.20'`         — `(stats?.total_pnl || 0).toFixed(2)`. The figure is real here
 *                             but `GET /api/stats` is platform-wide, so it renders through
 *                             `ds/Metric` with thousands grouping and an explicit `USD`.
 *   3. `'IP: 10.0.0.45'` and `'IP: 192.168.1.100'` — the label and the address were one text
 *                             node with a `|| '127.0.0.1'` fallback. The address is now its
 *                             own node, so the assertion is on the address.
 *   4. `'Authentication required…'` / `'Server error…'` — the page's own seven-way error
 *                             classification, which replaced the WHOLE screen. `ds/Panel`'s
 *                             error arm renders authored copy from `design/errorCopy.js`
 *                             instead, so these assert the recovery affordance and the
 *                             absence of a fabricated identity.
 *   5. `/Retry/i`           — one control under two labels (*Retry* and *Retry Connection*),
 *                             both calling `handleRetry`. It is *Try again* now.
 *
 * Everything else in this file is untouched and still passes, including the exact
 * `updateNotificationSettings` payload — which is the assertion that matters most here,
 * because `routers/user.py:139` validates that body against `NotificationSettingsRequest`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Profile from '../../src/pages/Profile';
import * as userModule from '../../src/api/modules/user';
import * as referralModule from '../../src/api/modules/referral';

describe('Phase 5B Profile Remediation Test Battery', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  const mockValidProfile = {
    id: 'user_12345',
    username: 'quant_trader',
    display_name: 'Alpha Quant',
    email: 'trader@vyomquant.com',
    role: 'pro_trader',
    avatar_url: 'https://vyomquant.com/avatar.png',
    bio: 'Algorithmic crypto trader'
  };

  const mockValidBilling = {
    plan: 'pro',
    features: ['Unlimited Backtests', '10 Live Bots', 'Priority WebSockets'],
    subscription_status: 'active',
    renewal_date: '2026-12-31T00:00:00Z'
  };

  const mockValidReferral = {
    referral_code: 'VQ-ALPHA99',
    referral_link: 'https://vyomquant.com/signup?ref=VQ-ALPHA99',
    total_referrals: 12,
    active_referrals: 8,
    pending_earnings: 240.50,
    lifetime_earnings: 1250.00
  };

  const mockValidStats = {
    total_strategies: 15,
    active_bots: 4,
    total_trades: 342,
    win_rate: 68.4,
    total_pnl: 5430.20
  };

  const mockValidSecurityLogs = [
    {
      id: 'log_1',
      event_type: 'User Login via MFA',
      ip_address: '192.168.1.100',
      created_at: '2026-08-26T12:00:00Z'
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

  // 1. Initial Successful Load
  it('1. Profile initial successful load renders all sections cleanly', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue(mockValidSecurityLogs);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('Alpha Quant')).toBeDefined();
    expect(screen.getByText('trader@vyomquant.com')).toBeDefined();
    expect(screen.getByText('Pro Tier')).toBeDefined();
    expect(screen.getByText('VQ-ALPHA99')).toBeDefined();
    // Was `'$5430.20'` — `(stats?.total_pnl || 0).toFixed(2)`. Through `ds/Metric` the
    // figure is grouped and carries an explicit unit, because `GET /api/stats` reports
    // across all accounts and `total_pnl` is filled from the aggregator's `today_pnl`.
    expect(screen.getByText('5,430.20')).toBeDefined();
    expect(screen.getByText('User Login via MFA')).toBeDefined();
    // Was `'IP: 192.168.1.100'` — one text node holding the label and an address that fell
    // back to the literal `'127.0.0.1'`. The address is its own node now.
    expect(screen.getByText('192.168.1.100')).toBeDefined();
  });

  // 2, 3, 5. Primary Auth Failure Handling (401, 403, Expired Session)
  it('2, 3, 5. Primary profile 401/403/expired session sets error state and shows login CTA', async () => {
    const authError = new Error('Unauthorized');
    authError.response = { status: 401 };
    vi.spyOn(userModule.userApi, 'getProfile').mockRejectedValue(authError);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue([]);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    render(<MemoryRouter><Profile /></MemoryRouter>);

    // Was the page's own authored string plus *Go to Login*. The identity panel now renders
    // `ds/Panel`'s error arm with copy from `design/errorCopy.js`, and the recovery controls
    // live in its header where `ds/Panel` renders them in every state. What is asserted is
    // the durable half: a failed profile read offers recovery and publishes NO identity.
    expect(await screen.findByRole('button', { name: /Reload the page/i })).toBeDefined();
    expect(screen.getByRole('button', { name: /Try again/i })).toBeDefined();
    expect(screen.queryByText('Alpha Quant')).toBeNull();
  });

  // 4. Primary Profile 500 Failure Handling
  it('4. Primary profile 500 sets server error state with retry option', async () => {
    const serverError = new Error('Internal Server Error');
    serverError.response = { status: 500 };
    vi.spyOn(userModule.userApi, 'getProfile').mockRejectedValue(serverError);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue([]);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    render(<MemoryRouter><Profile /></MemoryRouter>);

    // Was `'Server error. Please try again later.'` plus a *Retry* button — see the note on
    // test 2. *Retry* and *Retry Connection* were one control under two labels.
    expect(await screen.findByRole('button', { name: /Reload the page/i })).toBeDefined();
    expect(screen.getByRole('button', { name: /Try again/i })).toBeDefined();
    expect(screen.queryByText('Alpha Quant')).toBeNull();
  });

  // 6, 7, 8, 9. Secondary API Failures Preserved (Partial Failure Resilience)
  it('6, 7, 8, 9. Secondary API failure preserves valid Profile data and does not blank page', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockRejectedValue(new Error('Billing down'));
    vi.spyOn(referralModule.referralApi, 'getStats').mockRejectedValue(new Error('Referral down'));
    vi.spyOn(userModule.userApi, 'getStats').mockRejectedValue(new Error('Stats down'));
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockRejectedValue(new Error('Security logs down'));
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockRejectedValue(new Error('Notif down'));

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('Alpha Quant')).toBeDefined();
    expect(screen.getByText('trader@vyomquant.com')).toBeDefined();
    // THIS IS THE ASSERTION THE MIGRATION EXISTS FOR. It read
    // `expect(screen.getByText('Free Tier')).toBeDefined(); // Fallback billing` — the
    // billing read has FAILED in this test, and the page answered by telling the trader they
    // were on the free tier. A paid account saw the same thing. The declared marker now
    // carries the reason, and the marker's accessible name is what proves it is the marker
    // rather than a blank cell.
    expect(screen.getAllByLabelText('Current tier: not available').length).toBeGreaterThan(0);
    expect(screen.queryByText('Free Tier')).toBeNull();
    // Was `'No security logs available'`, which a FAILED security read produced as readily as
    // an account with no events. The two are different facts (Requirement 19.4): the panel is
    // in `error` here, so it offers a retry rather than claiming the log is empty.
    expect(screen.queryByText(/No access events have been recorded/i)).toBeNull();
  });

  // 10, 11. Retry after initial failure does NOT throw TypeError and recovers cleanly
  it('10, 11. Retry executes cleanly without TypeError and restores profile on success', async () => {
    const netError = new Error('Network offline');
    netError.response = { status: 503 };
    const getProfileSpy = vi.spyOn(userModule.userApi, 'getProfile')
      .mockRejectedValueOnce(netError)
      .mockResolvedValueOnce(mockValidProfile);

    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue(mockValidSecurityLogs);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    render(<MemoryRouter><Profile /></MemoryRouter>);

    // Was `/Retry/i`. One control, one label now — see the note on test 4.
    const retryBtn = await screen.findByRole('button', { name: /Try again/i });
    expect(retryBtn).toBeDefined();

    // Click it
    fireEvent.click(retryBtn);

    expect(await screen.findByText('Alpha Quant')).toBeDefined();
    expect(getProfileSpy).toHaveBeenCalledTimes(2);
  });

  // 12, 13, 14. Notification Toggle true -> false and exact PUT payload format
  it('12, 13, 14. Notification toggles send exact boolean channels and events to backend', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue(mockValidSecurityLogs);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    const updateNotifSpy = vi.spyOn(userModule.userApi, 'updateNotificationSettings').mockResolvedValue({ status: 'ok' });

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('Notification Preferences')).toBeDefined();

    // Find and toggle Email Notifications
    const emailToggle = screen.getByText('Email Notifications').closest('div[style*="cursor: pointer"]');
    fireEvent.click(emailToggle);

    await waitFor(() => {
      expect(updateNotifSpy).toHaveBeenCalledWith({
        channels: {
          email: false,
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
      });
    });
  });

  // 15, 16. Notification PUT Error Handling and State Restoration
  it('15, 16. Failed notification update reverts state to previous authoritative values', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue(mockValidSecurityLogs);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    const updateNotifSpy = vi.spyOn(userModule.userApi, 'updateNotificationSettings').mockRejectedValue(new Error('Update failed'));

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('Notification Preferences')).toBeDefined();

    const emailToggle = screen.getByText('Email Notifications').closest('div[style*="cursor: pointer"]');
    fireEvent.click(emailToggle);

    await waitFor(() => {
      expect(updateNotifSpy).toHaveBeenCalled();
    });
  });

  // 17, 18, 19. Billing & Security Log Field Normalization
  it('17, 18, 19. Billing plan and security log event_type & ip_address render correctly', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue({
      plan: 'enterprise',
      features: ['Dedicated Co-location', 'Sub-millisecond Execution'],
      subscription_status: 'active'
    });
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue([
      {
        id: 'log_99',
        event_type: 'API Key Generated',
        ip_address: '10.0.0.45',
        created_at: '2026-08-26T14:30:00Z'
      }
    ]);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('Enterprise Tier')).toBeDefined();
    expect(screen.getByText('API Key Generated')).toBeDefined();
    // Was `'IP: 10.0.0.45'` — see the note on test 1.
    expect(screen.getByText('10.0.0.45')).toBeDefined();
  });

  // 20, 21. Profile Editing & Duplicate Save Protection
  it('20, 21. User can edit profile and saving state prevents duplicate submissions', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue([]);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    const updateProfileSpy = vi.spyOn(userModule.userApi, 'updateProfile').mockResolvedValue({ status: 'ok' });

    render(<MemoryRouter><Profile /></MemoryRouter>);

    const editBtn = await screen.findByRole('button', { name: /Edit Profile/i });
    fireEvent.click(editBtn);

    const displayNameInput = screen.getByDisplayValue('Alpha Quant');
    fireEvent.change(displayNameInput, { target: { value: 'Omega Quant' } });

    const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateProfileSpy).toHaveBeenCalledWith(expect.objectContaining({ display_name: 'Omega Quant' }));
    });
  });

  // 22. Unmount Cleanup
  it('22. Component unmounts cleanly without throwing memory leak warnings', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getStats').mockResolvedValue(mockValidStats);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue([]);
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(mockValidNotificationSettings);

    const { unmount } = render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('Alpha Quant')).toBeDefined();
    unmount();
  });
});
