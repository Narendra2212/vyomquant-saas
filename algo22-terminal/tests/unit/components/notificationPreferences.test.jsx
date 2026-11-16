/**
 * ═══════════════════════════════════════════════════════════════════════════
 * notificationPreferences.test.jsx — PROFILE-REORG-01
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * These cases MOVED here from the Profile suites when Task 13 relocated the preferences panel
 * to the Notifications module. They are not new coverage and they are not rewritten coverage:
 * the assertions are the ones `profile_phase5b_remediation.test.jsx` (cases 12-16) and
 * `profile_phase5e_trader_ux.test.jsx` (cases 7-9) already made. Only the component rendered
 * differs, because the panel reads and writes its own state now.
 *
 * WHY THE EXACT-PAYLOAD CASE IS THE IMPORTANT ONE
 * ---------------------------------------------------------------------------
 * `handleNotificationSettingChange` sends the WHOLE settings object on every toggle, because
 * `NotificationSettingsRequest` validates the whole object. So a one-switch change writes
 * eight event flags and three channels, and any drift in how that payload is assembled
 * silently rewrites preferences the user did not touch.
 *
 * That is the one thing the extraction could have broken, and `toHaveBeenCalledWith` on the
 * full literal — not `objectContaining` — is what proves it did not. Keeping it exact is
 * deliberate: a partial matcher would pass against a payload that had quietly dropped a key.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

import { NotificationPreferences } from '../../../src/components/NotificationPreferences';
import * as userModule from '../../../src/api/modules/user';

/** The stored settings these cases toggle from. Email on, so a toggle writes `false`. */
const storedSettings = {
  channels: { email: true, telegram: false, mobile: false },
  events: {
    trade_executed: true,
    stop_loss_triggered: true,
    daily_pnl_summary: true,
    bot_state_change: false,
    kill_switch_activated: true,
    new_login_detected: true,
    api_key_expiring: true,
    backtest_complete: false,
  },
};

/** The switch rows are the one inline-styled construct in the panel. */
const switchFor = (label) =>
  screen.getByText(label).closest('div[style*="cursor: pointer"]');

describe('PROFILE-REORG-01: notification preferences, in their new home', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(userModule.userApi, 'getNotificationSettings').mockResolvedValue(storedSettings);
  });
  afterEach(() => cleanup());

  it('renders the categorised switches', async () => {
    render(<NotificationPreferences />);

    expect(await screen.findByText(/Notification Dispatch Preferences/i)).toBeDefined();
    expect(screen.getByText('Trade Executions')).toBeDefined();
    expect(screen.getByText('Daily PnL Summaries')).toBeDefined();
    expect(screen.getByText('Stop Loss & Margin Alerts')).toBeDefined();
    expect(screen.getByText('Emergency Kill Switch & Liquidations')).toBeDefined();
    expect(screen.getByText('Email Notifications')).toBeDefined();
    expect(screen.getByText('Strategy Lifecycle State Changes')).toBeDefined();
    expect(screen.getByText('Security & Login Events')).toBeDefined();
  });

  it('states that mandatory risk alerts cannot be switched off', async () => {
    render(<NotificationPreferences />);
    // Carried across because it is a claim about what the platform does whatever the switches
    // say, and the panel would be misleading without it.
    expect(
      await screen.findByText(/Mandatory risk alerts \(global kill switch triggers\) cannot be disabled/i),
    ).toBeDefined();
  });

  // Was profile_phase5b case 12, 13, 14.
  it('sends the exact boolean channels and events to the backend', async () => {
    const updateNotifSpy = vi
      .spyOn(userModule.userApi, 'updateNotificationSettings')
      .mockResolvedValue({ status: 'ok' });

    render(<NotificationPreferences />);
    await screen.findByText('Email Notifications');

    fireEvent.click(switchFor('Email Notifications'));

    await waitFor(() => {
      expect(updateNotifSpy).toHaveBeenCalledWith({
        channels: {
          email: false,
          telegram: false,
          mobile: false,
        },
        events: {
          trade_executed: true,
          stop_loss_triggered: true,
          daily_pnl_summary: true,
          bot_state_change: false,
          kill_switch_activated: true,
          new_login_detected: true,
          api_key_expiring: true,
          backtest_complete: false,
        },
      });
    });
  });

  // Was profile_phase5e case 7, 8, 9.
  it('writes the toggled event without disturbing the others', async () => {
    const updateNotifSpy = vi
      .spyOn(userModule.userApi, 'updateNotificationSettings')
      .mockResolvedValue({ status: 'ok' });

    render(<NotificationPreferences />);
    await screen.findByText('Trade Executions');

    fireEvent.click(switchFor('Trade Executions'));

    await waitFor(() => {
      expect(updateNotifSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          events: expect.objectContaining({ trade_executed: false }),
        }),
      );
    });
    // The channels are sent too, unchanged — the server validates the whole object.
    expect(updateNotifSpy.mock.calls[0][0].channels).toEqual({
      email: true,
      telegram: false,
      mobile: false,
    });
  });

  // Was profile_phase5b case 15, 16.
  it('puts a failed preference back, and says so', async () => {
    const updateNotifSpy = vi
      .spyOn(userModule.userApi, 'updateNotificationSettings')
      .mockRejectedValue(new Error('Update failed'));

    render(<NotificationPreferences />);
    await screen.findByText('Email Notifications');

    fireEvent.click(switchFor('Email Notifications'));

    await waitFor(() => expect(updateNotifSpy).toHaveBeenCalled());

    /*
      The original case asserted only that the call happened. The revert itself is the
      behaviour worth holding — before the Profile work added it, a failed write left the
      switch silently sprung back with no explanation, which is Requirement 11.3's complaint.
      So this also asserts the sentence.
    */
    expect(
      await screen.findByText(/That preference could not be saved. It has been put back/i),
    ).toBeDefined();
  });

  it('reports a failed read as a recoverable panel rather than blank switches', async () => {
    userModule.userApi.getNotificationSettings.mockRejectedValue(new Error('Notif down'));

    render(<NotificationPreferences />);

    // `ds/Panel`'s error state owns the retry; the switches must not render over a read that
    // failed, because every one of them would be showing a contract default as if it were
    // this account's stored position.
    await waitFor(() => expect(screen.queryByText('Email Notifications')).toBeNull());
  });
});
