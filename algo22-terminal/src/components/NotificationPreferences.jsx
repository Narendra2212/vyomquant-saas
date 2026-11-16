/**
 * ═══════════════════════════════════════════════════════════════════════════
 * NotificationPreferences.jsx — the dispatch preferences panel (PROFILE-REORG-01)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * WHY THIS IS A COMPONENT AND NOT A SECOND COPY
 * ---------------------------------------------------------------------------
 * This panel lived inside `pages/Profile.jsx`, spread across nine locations: the switch
 * primitive, the contract defaults, the reader, the optimistic-override state, the notice
 * state, the write handler, two derivations and the render. Task 13 moves it to the
 * Notifications module, and Task 10 says the same thing from the other side — "Provide
 * notification preferences in the Notifications module, not Profile".
 *
 * Moving it by copying the markup into `NotificationCenter.jsx` would have duplicated the
 * write handler, and that handler is the one piece here that must not be duplicated: it
 * rebuilds the ENTIRE settings payload on every toggle, because
 * `NotificationSettingsRequest` validates the whole object. Two copies drifting means one of
 * them starts sending a payload the server rejects, or worse, silently resets a preference
 * the other copy owns.
 *
 * So the whole thing is extracted once, state included, and both the old and new homes just
 * render `<NotificationPreferences />`. Profile no longer reads notification settings at all.
 *
 * WHAT IS CARRIED ACROSS BYTE-FOR-BYTE, AND WHY THAT MATTERS
 * ---------------------------------------------------------------------------
 * The payload expression in `handleNotificationSettingChange` is unchanged: both `??`
 * chains, both boolean coercions, the same eight event keys and the same three channels.
 * `routers/user.py` validates it against `NotificationSettingsRequest` and its shape is a
 * contract, so this move deliberately changes nothing about what goes on the wire.
 * `CONTRACT_DEFAULTS` is that same model's field defaults, which is why a missing stored
 * value resolves to it rather than to `false`.
 *
 * The switch's accessibility contract is also carried across intact: the `switch` role,
 * `aria-checked`, the accessible name, a tab stop, and Enter/Space activation that
 * `preventDefault`s Space so the page does not scroll the row out from under the control
 * just toggled.
 *
 * ONE THING THIS PANEL CURRENTLY PROMISES THAT THE BACKEND DOES NOT KEEP
 * ---------------------------------------------------------------------------
 * The *Email Notifications* switch reads "Receive verified critical alerts to your primary
 * email address". As of this commit that is not true: `email_notifications` is written,
 * persisted, and read by nothing that sends mail —
 * `docs/audits/VYOMQUANT_EMAIL_NOTIFICATION_DELIVERY_AUDIT.md` has the four measurements.
 *
 * The switch is moved AS-IS here rather than quietly altered, because changing what the
 * product promises is a product decision and not a side effect of relocating a panel. The
 * audit's §5 names the two honest options and that decision is pending. This note exists so
 * the next reader of this file does not have to rediscover the gap.
 */

import { useCallback, useState } from 'react';
import { Activity, AlertCircle, Bell, Cpu, Lock, Mail, Shield, TrendingUp } from 'lucide-react';

import { Alert } from './ds/Alert';
import { Panel } from './ds/Panel';
import { api } from '../api';
import { token } from '../design/tokens';
import { PANEL_STATES, usePanelState } from '../hooks/usePanelState';

/**
 * `NotificationSettingsRequest`'s own field defaults.
 *
 * Not invented here: these are the positions the server would apply for a value it has not
 * stored, so a switch with no stored state shows what the server would do rather than a
 * guessed `false`.
 */
const CONTRACT_DEFAULTS = Object.freeze({
  channels: Object.freeze({ email: true, telegram: false, mobile: false }),
  events: Object.freeze({
    trade_executed: true,
    stop_loss_triggered: true,
    daily_pnl_summary: true,
    bot_state_change: false,
    kill_switch_activated: true,
    new_login_detected: true,
    api_key_expiring: true,
    backtest_complete: false,
  }),
});

/**
 * The server's own refusal sentence, or the caller's authored fallback.
 *
 * Deliberately does NOT fall through to `err.message`: that is the one leg that could put an
 * exception class in front of a retail trader (Requirement 11.3). `ApiError.getUserMessage()`
 * is the method that already composes the server's `detail`/`error` into something readable.
 */
const writeFailureMessage = (err, fallback) => {
  if (typeof err?.getUserMessage === 'function') {
    const authored = err.getUserMessage();
    if (typeof authored === 'string' && authored.trim()) return authored;
  }
  return fallback;
};

/**
 * The switch a notification preference is.
 *
 * `ds/` has no switch primitive and one is not added from here — adding a shared primitive
 * from a feature commit is how a design system acquires a control nobody reviewed. Every
 * colour comes from `token`.
 */
function NotificationSwitch({ label, description, icon: Icon, checked, onChange }) {
  return (
    <div
      role="switch"
      aria-checked={checked}
      aria-label={label}
      tabIndex={0}
      onClick={() => onChange(!checked)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
          // Space scrolls the page by default, which would move the row out from under the
          // setting just toggled.
          event.preventDefault();
          onChange(!checked);
        }
      }}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '10px 14px',
        background: token.surface.raised,
        border: `1px solid ${checked ? token.brand.wash : token.line.default}`,
        borderRadius: token.radius.lg,
        cursor: 'pointer',
        transition: `all ${token.transition.fast}`,
      }}
    >
      <div
        style={{
          color: checked ? token.brand.base : token.content.muted,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 32,
          height: 32,
          borderRadius: token.radius.md,
          background: checked ? token.brand.wash : token.surface.inset,
          transition: `all ${token.transition.fast}`,
        }}
      >
        {Icon ? <Icon size={16} /> : <Bell size={16} />}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="text-title font-semibold text-content-primary">{label}</div>
        <div className="text-body leading-snug text-content-muted">{description}</div>
      </div>

      <div
        style={{
          width: 38,
          height: 20,
          background: checked ? token.brand.base : token.line.default,
          borderRadius: token.radius.full,
          position: 'relative',
          transition: `background ${token.transition.base}`,
          flexShrink: 0,
        }}
      >
        <div
          style={{
            width: 16,
            height: 16,
            background: token.content.primary,
            borderRadius: token.radius.full,
            position: 'absolute',
            top: 2,
            left: checked ? 20 : 2,
            transition: `left ${token.transition.base}`,
            boxShadow: token.shadow.panel,
          }}
        />
      </div>
    </div>
  );
}

/**
 * Notification dispatch preferences, reading and writing its own state.
 *
 * Self-contained on purpose: the host page passes nothing and owns nothing. That is what lets
 * the same panel sit in the Notifications module without either page knowing how a
 * preference is written.
 *
 * @param {object} props
 * @param {string} [props.className] Spacing from the host's layout, which is the host's
 *   business rather than this panel's.
 */
export function NotificationPreferences({ className }) {
  const [notifOverride, setNotifOverride] = useState(null);
  const [notifNotice, setNotifNotice] = useState(null);

  const readNotif = useCallback(() => api.user.getNotificationSettings(), []);
  const notif = usePanelState(readNotif, { deps: [] });

  /** The stored notification settings, or the position this session has just written. */
  const notifBody = notifOverride ?? (notif.data?.data ?? notif.data);

  /*
    EMPTY folds into READY. An account that has never touched a preference has no stored row,
    and that is not an error and not an empty panel — every switch has a contract default, so
    the panel is fully renderable. Treating it as EMPTY would show a "nothing stored" state
    over eight working controls.
  */
  const notifState = notif.state === PANEL_STATES.EMPTY ? PANEL_STATES.READY : notif.state;

  /** The stored position of one switch, or the contract default. */
  const eventOn = (key, ...aliases) => {
    const events = notifBody?.events;
    if (events && typeof events === 'object') {
      for (const name of [key, ...aliases]) {
        if (typeof events[name] === 'boolean') return events[name];
      }
    }
    return CONTRACT_DEFAULTS.events[key];
  };

  /** The email channel, which the server has been seen to answer as a boolean or an object. */
  const emailChannelOn = (() => {
    const channel = notifBody?.channels?.email;
    if (typeof channel === 'boolean') return channel;
    if (channel && typeof channel === 'object') return Boolean(channel.active ?? true);
    return CONTRACT_DEFAULTS.channels.email;
  })();

  /**
   * Write one switch by sending the WHOLE settings object.
   *
   * The payload is built by exactly the expression this panel has always used — both `??`
   * chains, both boolean coercions, the same eight event keys and the same three channels —
   * because `routers/user.py` validates it against `NotificationSettingsRequest` and its
   * shape is a contract. The pre-toggle state comes from the stored settings, or from
   * `CONTRACT_DEFAULTS`, which is that same model's field defaults.
   *
   * Optimistic: the override is set before the request so the switch moves under the finger,
   * and is PUT BACK on failure with a sentence saying so. A switch that silently springs back
   * was the only signal a trader used to get (Requirement 11.3).
   */
  const handleNotificationSettingChange = async (targetGroup, key, value) => {
    const previousOverride = notifOverride;
    const currentChannels = notifBody?.channels || CONTRACT_DEFAULTS.channels;
    const currentEvents = notifBody?.events || CONTRACT_DEFAULTS.events;

    const updatedChannels = {
      email:
        typeof currentChannels.email === 'boolean'
          ? currentChannels.email
          : Boolean(currentChannels.email?.active ?? true),
      telegram:
        typeof currentChannels.telegram === 'boolean'
          ? currentChannels.telegram
          : Boolean(currentChannels.telegram?.active ?? false),
      mobile:
        typeof currentChannels.mobile === 'boolean'
          ? currentChannels.mobile
          : Boolean(currentChannels.mobile?.active ?? false),
      ...(targetGroup === 'channels' ? { [key]: Boolean(value) } : {}),
    };

    const updatedEvents = {
      trade_executed: Boolean(currentEvents.trade_executed ?? currentEvents.trade ?? true),
      stop_loss_triggered: Boolean(currentEvents.stop_loss_triggered ?? true),
      daily_pnl_summary: Boolean(currentEvents.daily_pnl_summary ?? true),
      bot_state_change: Boolean(currentEvents.bot_state_change ?? currentEvents.bot ?? false),
      kill_switch_activated: Boolean(
        currentEvents.kill_switch_activated ?? currentEvents.margin ?? true,
      ),
      new_login_detected: Boolean(currentEvents.new_login_detected ?? currentEvents.login ?? true),
      api_key_expiring: Boolean(currentEvents.api_key_expiring ?? true),
      backtest_complete: Boolean(currentEvents.backtest_complete ?? false),
      ...(targetGroup === 'events' ? { [key]: Boolean(value) } : {}),
    };

    const payload = { channels: updatedChannels, events: updatedEvents };

    try {
      setNotifNotice(null);
      setNotifOverride(payload);
      await api.user.updateNotificationSettings(payload);
    } catch (err) {
      setNotifOverride(previousOverride);
      setNotifNotice({
        severity: 'error',
        message: writeFailureMessage(
          err,
          'That preference could not be saved. It has been put back.',
        ),
      });
    }
  };

  return (
    <Panel
      className={className}
      title="Notification Preferences"
      state={notifState}
      loading={{
        kind: 'skeleton-cards',
        rows: 7,
        label: 'Loading your notification preferences',
      }}
      empty={{
        headline: 'No notification preferences are stored',
        body: 'Switching any preference below stores the full set against your account.',
        action: { label: 'Read again', onClick: notif.refetch },
      }}
      error={{ error: notif.error, onRetry: notif.refetch }}
      unavailable={{ reason: 'Your notification preferences cannot be read right now.' }}
      unauthorised={{ error: notif.error }}
    >
      <p className="mb-3 text-body text-content-muted">
        Notification Dispatch Preferences: trade execution, risk trigger, and account security
        alert dispatches
      </p>

      {notifNotice ? (
        <Alert
          severity={notifNotice.severity}
          title={notifNotice.message}
          onDismiss={() => setNotifNotice(null)}
          dismissLabel="Dismiss this message"
          className="mb-3"
        />
      ) : null}

      <div className="flex flex-col gap-3.5">
        <div>
          <p className="mb-1.5 text-micro font-bold uppercase tracking-wide text-brand">
            Trading executions
          </p>
          <div className="flex flex-col gap-1.5">
            <NotificationSwitch
              label="Trade Executions"
              description="Real-time order fill alerts and position entries"
              icon={TrendingUp}
              checked={eventOn('trade_executed', 'trade')}
              onChange={(checked) =>
                handleNotificationSettingChange('events', 'trade_executed', checked)
              }
            />
            <NotificationSwitch
              label="Daily PnL Summaries"
              description="Daily performance and closed trade balance recap"
              icon={Activity}
              checked={eventOn('daily_pnl_summary')}
              onChange={(checked) =>
                handleNotificationSettingChange('events', 'daily_pnl_summary', checked)
              }
            />
          </div>
        </div>

        <div>
          {/* The hue is READ from `token.status.loss.fg` rather than written. */}
          <p
            className="mb-1.5 text-micro font-bold uppercase tracking-wide"
            style={{ color: token.status.loss.fg }}
          >
            Risk &amp; circuit breakers
          </p>
          <div className="flex flex-col gap-1.5">
            <NotificationSwitch
              label="Stop Loss & Margin Alerts"
              description="Stop loss execution and drawdown limit notifications"
              icon={AlertCircle}
              checked={eventOn('stop_loss_triggered')}
              onChange={(checked) =>
                handleNotificationSettingChange('events', 'stop_loss_triggered', checked)
              }
            />
            <NotificationSwitch
              label="Emergency Kill Switch & Liquidations"
              description="Venue liquidation proximity and emergency halt activations"
              icon={Shield}
              checked={eventOn('kill_switch_activated', 'margin')}
              onChange={(checked) =>
                handleNotificationSettingChange('events', 'kill_switch_activated', checked)
              }
            />
          </div>
        </div>

        <div>
          <p className="mb-1.5 text-micro font-bold uppercase tracking-wide text-content-secondary">
            Automation &amp; security
          </p>
          <div className="flex flex-col gap-1.5">
            <NotificationSwitch
              label="Email Notifications"
              description="Receive verified critical alerts to your primary email address"
              icon={Mail}
              checked={emailChannelOn}
              onChange={(checked) => handleNotificationSettingChange('channels', 'email', checked)}
            />
            <NotificationSwitch
              label="Strategy Lifecycle State Changes"
              description="Strategy start, pause, runtime errors, and halts"
              icon={Cpu}
              checked={eventOn('bot_state_change', 'bot')}
              onChange={(checked) =>
                handleNotificationSettingChange('events', 'bot_state_change', checked)
              }
            />
            <NotificationSwitch
              label="Security & Login Events"
              description="New login detections and authentication state alterations"
              icon={Lock}
              checked={eventOn('new_login_detected', 'login')}
              onChange={(checked) =>
                handleNotificationSettingChange('events', 'new_login_detected', checked)
              }
            />
          </div>
        </div>
      </div>

      {/* A statement about what the platform will do whatever the switches say, which makes it
          the one sentence in the panel that must not be the smallest text in it. */}
      <p className="mt-3 flex items-start gap-1.5 text-body text-content-muted">
        <Lock size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
        <span>Mandatory risk alerts (global kill switch triggers) cannot be disabled.</span>
      </p>
    </Panel>
  );
}

export default NotificationPreferences;
