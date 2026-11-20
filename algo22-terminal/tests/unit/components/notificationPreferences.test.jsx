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

/**
 * The switch row, by its accessible name.
 *
 * NOTIF-02 changed this from `getByText(label).closest('div[style*="cursor: pointer"]')`. That
 * form stopped finding the three mandatory rows the moment they rendered `cursor: not-allowed`
 * — it located a control by a presentational detail, so a styling change read as a missing
 * control. `role="switch"` plus `aria-label` is the contract the panel documents and the one a
 * screen-reader user actually has.
 */
const switchFor = (label) => screen.getByRole('switch', { name: label });

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

  it('states that mandatory alerts cannot be switched off, and names all three', async () => {
    render(<NotificationPreferences />);
    // NOTIF-02 widened the sentence. It used to name only the kill switch while three alerts
    // are on the floor, so a customer would reasonably read the other two as theirs to mute.
    const sentence = await screen.findByText(/cannot be disabled/i);
    expect(sentence.textContent).toMatch(/Emergency halt/i);
    expect(sentence.textContent).toMatch(/stop-loss/i);
    expect(sentence.textContent).toMatch(/new sign-in/i);
  });

  it('locks the three mandatory switches, and only those three', async () => {
    render(<NotificationPreferences />);
    await screen.findByText('Email Notifications');

    // The defect NOTIF-02 fixed was a sentence and a control disagreeing: the panel said
    // mandatory alerts could not be disabled and rendered the switch that disabled them.
    for (const label of [
      'Emergency Kill Switch & Liquidations',
      'Stop Loss & Margin Alerts',
      'Security & Login Events',
    ]) {
      expect(switchFor(label).getAttribute('aria-disabled')).toBe('true');
    }
    for (const label of [
      'Trade Executions',
      'Daily PnL Summaries',
      'Strategy Lifecycle State Changes',
    ]) {
      expect(switchFor(label).getAttribute('aria-disabled')).toBeNull();
    }
  });

  it('locks Email Notifications for the opposite reason, and says which', async () => {
    /*
      EMAIL-01. Two kinds of lock now exist on this panel and they must not be conflated:

        MANDATORY  — on, and cannot be turned OFF. The platform delivers it regardless.
        EMAIL      — cannot be turned ON, because nothing would carry it.

      Measured both ways: the customer notification path has NO email hop (neither
      `core/notification_dispatcher.py` nor `routers/notifications.py` references any of
      the three modules holding `aiosmtplib`, and those three are the operator alerting
      path), and no SMTP host or credential is set on any production task definition.

      The switch was operable before this, so turning it on was an instruction the platform
      accepted and then did not carry out — the brief's "never make a control appear
      functional when its backend operation is broken".
    */
    render(<NotificationPreferences />);
    await screen.findByText('Email Notifications');

    expect(switchFor('Email Notifications').getAttribute('aria-disabled')).toBe('true');

    // The reason must name the gap AND what still works, or a reader concludes their
    // alerts are off altogether. And it must not reuse the mandatory wording, which
    // would read as "always on" — the opposite of the truth here.
    const panel = document.body.textContent ?? '';
    expect(panel).toMatch(/not configured/i);
    expect(panel).toMatch(/in-app/i);
    expect(panel).not.toMatch(/Receive verified critical alerts/i);
  });

  it('a locked switch issues no write when clicked or keyed', async () => {
    const updateNotifSpy = vi
      .spyOn(userModule.userApi, 'updateNotificationSettings')
      .mockResolvedValue({ status: 'ok' });

    render(<NotificationPreferences />);
    await screen.findByText('Emergency Kill Switch & Liquidations');

    const control = switchFor('Emergency Kill Switch & Liquidations');
    fireEvent.click(control);
    fireEvent.keyDown(control, { key: 'Enter' });
    fireEvent.keyDown(control, { key: ' ' });

    // Still on, and nothing was sent. A control that moved and then sprang back would be the
    // worse outcome: the customer would believe the write happened.
    expect(control.getAttribute('aria-checked')).toBe('true');
    expect(updateNotifSpy).not.toHaveBeenCalled();
  });

  // Was profile_phase5b case 12, 13, 14.
  it('sends the exact boolean channels and events to the backend', async () => {
    const updateNotifSpy = vi
      .spyOn(userModule.userApi, 'updateNotificationSettings')
      .mockResolvedValue({ status: 'ok' });

    render(<NotificationPreferences />);
    await screen.findByText('Email Notifications');

    // EMAIL-01: was `Email Notifications`, which is now LOCKED (no transport exists), so
    // clicking it issues no write — which is what `a locked switch issues no write when
    // clicked or keyed` asserts. Driven from an unlocked EVENT switch instead, because
    // what this test is really about is the exact payload shape, not which control sent
    // it. `trade_executed` starts true, so one click sends it false.
    fireEvent.click(switchFor('Trade Executions'));

    await waitFor(() => {
      expect(updateNotifSpy).toHaveBeenCalledWith({
        channels: {
          email: true,
          telegram: false,
          mobile: false,
        },
        events: {
          trade_executed: false,
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

    // EMAIL-01: driven from an unlocked switch. `Email Notifications` is locked now, so
    // clicking it issues no write at all and there would be nothing to revert.
    fireEvent.click(switchFor('Trade Executions'));

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
