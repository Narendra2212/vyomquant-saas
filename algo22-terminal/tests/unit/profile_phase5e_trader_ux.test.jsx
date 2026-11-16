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
    // PROFILE-REORG-01: `Tier: Enterprise Tier`. Since the Subscription & Plan panel was
    // removed the tier is reported only by the page header's meta chip, whose text node
    // carries the `Tier:` prefix — so an exact match on the bare label no longer finds it.
    // This is the assertion proving `getBillingPlan` is still read and still rendered.
    expect(screen.getByText(/Tier: Enterprise Tier/)).toBeDefined();
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

    /*
      SECLOG-REMOVED-01 (2026-11-15): the *Review All Logs* assertion that stood here is
      inverted rather than deleted.

      It clicked the command and expected `navigate('/app/security-logs')`. That route is
      withdrawn, so the control is gone too -- leaving it would have navigated to a path the
      `/app/*` catch-all redirects to the dashboard, which is a worse answer than offering
      nothing.

      Asserting its ABSENCE, not just dropping the lines, is what stops the control being
      reintroduced by a later pass that only reads the page and not this history.
    */
    expect(screen.queryByText(/Review All Logs/i)).toBeNull();

    // The panel it sat on is untouched and still reports this account's access events, so the
    // data behind the removed link is still on the page.
    expect(screen.getByText(/Recent access/i)).toBeTruthy();
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

    /*
      PROFILE-REORG-01 INVERTED THIS CASE.

      It asserted the *Platform-wide statistics* panel and its four figures. Task 13 removes
      that panel from Profile: the figures come from `GET /api/stats`, which reports the
      PLATFORM rather than this account, and an account page is the wrong place to report
      somebody else's totals. The prose this case used to match on
      ("reports across all accounts rather than yours") was the panel admitting as much.

      THE TWO NAVIGATION BUTTONS WENT WITH IT, because they were that panel's `actions`.
      That is not a lost destination: `/app/exchange` and `/app/strategies` are both in the
      account menu's "Trading setup" group, and `shell/accountMenu.test.jsx` holds them there
      through its `DEFERRED_ROUTES` list. So the route coverage moved rather than vanished,
      which is why this case does not try to keep a button that no longer has a panel.

      Asserting ABSENCE, not just dropping the lines: that is what stops the panel being
      reinstated by a later pass that reads the page and not this history.
    */
    expect(screen.queryByText('Platform-wide statistics')).toBeNull();
    expect(screen.queryByText(/reports across all accounts rather than yours/i)).toBeNull();
    expect(screen.queryByText('Strategies (platform)')).toBeNull();
    expect(screen.queryByRole('button', { name: /Manage Exchanges/i })).toBeNull();

    // The panels Task 13 keeps are still here, so the removal did not take the page with it.
    expect(screen.getByText('Account identity')).toBeDefined();
    expect(screen.getByText('Security & Access')).toBeDefined();
  });

  /*
    7, 8, 9 MOVED to tests/unit/components/notificationPreferences.test.jsx.

    The categorised switches and the boolean-payload assertion were never about Profile — they
    are about the preferences panel, which PROFILE-REORG-01 relocated to the Notifications
    module (Task 13, and Task 10 from the other side). The assertions are unchanged in their
    new home; only the component rendered differs.

    What stays HERE is the one thing this file can still state about Profile: that the panel
    is no longer on it.
  */
  it('7, 8, 9. the notification preferences are no longer on Profile', async () => {
    vi.spyOn(userModule.userApi, 'getProfile').mockResolvedValue(mockValidProfile);
    vi.spyOn(userModule.userApi, 'getBillingPlan').mockResolvedValue(mockValidBilling);
    vi.spyOn(referralModule.referralApi, 'getStats').mockResolvedValue(mockValidReferral);
    vi.spyOn(userModule.userApi, 'getSecurityLogs').mockResolvedValue(mockValidSecurityLogs);

    const updateNotifSpy = vi
      .spyOn(userModule.userApi, 'updateNotificationSettings')
      .mockResolvedValue({ status: 'ok' });

    render(<MemoryRouter><Profile /></MemoryRouter>);

    expect(await screen.findByText('Account identity')).toBeDefined();
    expect(screen.queryByText(/Notification Dispatch Preferences/i)).toBeNull();
    expect(screen.queryByText('Trade Executions')).toBeNull();
    expect(screen.queryByText('Email Notifications')).toBeNull();
    // And Profile writes no preference, which is the half that matters: two pages writing the
    // same settings through two code paths is what the extraction exists to prevent.
    expect(updateNotifSpy).not.toHaveBeenCalled();
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

    /*
      PROFILE-REORG-01 INVERTED THIS CASE TOO, and this one needs care, because the panel is
      gone but PART of what it showed is not.

      The *Subscription & Plan* panel is removed: the owner confirmed it duplicated the
      Billing & plan page, so it cost a read and a panel to restate what `/app/billing`
      already says, and *Manage Subscription* went with it — `/app/billing` is in the account
      menu's Account group, held there by `shell/accountMenu.test.jsx`.

      BUT `getBillingPlan` IS STILL CALLED and the tier is still rendered, because Account
      identity carries the plan as a chip and Task 13 keeps that panel. So this case asserts
      the panel's absence AND the chip's survival — the asymmetry is the whole point, and a
      test that only checked for absence would have been satisfied by dropping the read too.
    */
    expect(await screen.findByText('Account identity')).toBeDefined();
    expect(screen.queryByText('Subscription & Plan')).toBeNull();
    expect(screen.queryByText(/Dedicated Co-location/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /Manage Subscription/i })).toBeNull();

    // The header's tier chip, which reads the same billing response the removed panel did.
    expect(screen.getByText(/Tier: Enterprise Tier/)).toBeDefined();
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
