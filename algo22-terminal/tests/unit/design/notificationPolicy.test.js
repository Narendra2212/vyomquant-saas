/**
 * Unit tests for `src/design/notificationPolicy.js` — task 5.8.
 *
 * The event payloads below are the real backend shapes:
 * - `routers/notifications.py::create_notification` broadcasts
 *   `{type: 'notification', data: {…row}}`
 * - `core/realtime_sync.py::broadcast_subscription_change` sends
 *   `{type: 'subscription_update', event, data}`
 * - `ws_channels.py` channel frames carry `{type, channel, payload, strategy_id, …}`
 */

import { describe, it, expect } from 'vitest';
import {
  NOTIFIABLE,
  NOTIFIABLE_KEYS,
  notificationFor,
  normaliseEventKey,
} from '../../../src/design/notificationPolicy';

describe('NOTIFIABLE', () => {
  it('is exactly the seven Requirement 16.1 categories', () => {
    expect(NOTIFIABLE_KEYS).toEqual([
      'DEPLOYMENT_SUCCEEDED',
      'DEPLOYMENT_FAILED',
      'EXCHANGE_DISCONNECTED',
      'ORDER_REJECTED',
      'STRATEGY_STOPPED',
      'BACKTEST_COMPLETED',
      'SUBSCRIPTION_EXPIRED',
    ]);
  });

  it('gives every entry a toast severity and a copy function', () => {
    for (const key of NOTIFIABLE_KEYS) {
      expect(['success', 'error', 'warning', 'info']).toContain(NOTIFIABLE[key].severity);
      expect(NOTIFIABLE[key].copy).toBeTypeOf('function');
    }
  });

  it('is frozen, so a page cannot add to the allowlist at runtime', () => {
    expect(Object.isFrozen(NOTIFIABLE)).toBe(true);
  });
});

describe('normaliseEventKey — real backend values', () => {
  it('maps notification rows by their category + type pair', () => {
    expect(
      normaliseEventKey({
        type: 'notification',
        data: { type: 'strategy_deployed', category: 'strategy', severity: 'info' },
      }),
    ).toBe('DEPLOYMENT_SUCCEEDED');

    expect(
      normaliseEventKey({
        type: 'notification',
        data: { type: 'strategy_stopped', category: 'strategy', severity: 'info' },
      }),
    ).toBe('STRATEGY_STOPPED');

    expect(
      normaliseEventKey({
        type: 'notification',
        data: { type: 'exchange_disconnected', category: 'exchange', severity: 'warning' },
      }),
    ).toBe('EXCHANGE_DISCONNECTED');
  });

  it('maps ws_channels event types', () => {
    expect(normaliseEventKey({ type: 'deploy_success', channel: 'deployment_events' })).toBe(
      'DEPLOYMENT_SUCCEEDED',
    );
    expect(normaliseEventKey({ type: 'deploy_failed', channel: 'deployment_events' })).toBe(
      'DEPLOYMENT_FAILED',
    );
    expect(normaliseEventKey({ type: 'bot_stopped', channel: 'deployment_events' })).toBe(
      'STRATEGY_STOPPED',
    );
    expect(normaliseEventKey({ type: 'order_rejected', channel: 'execution_events' })).toBe(
      'ORDER_REJECTED',
    );
  });

  it('maps the subscription_update envelope by its event field and by a lapsed state', () => {
    expect(
      normaliseEventKey({ type: 'subscription_update', event: 'subscription_expired', data: {} }),
    ).toBe('SUBSCRIPTION_EXPIRED');
    expect(
      normaliseEventKey({ type: 'subscription_update', data: { status: 'expired' } }),
    ).toBe('SUBSCRIPTION_EXPIRED');
    expect(
      normaliseEventKey({
        type: 'subscription_update',
        data: { subscription_status: 'expired' },
      }),
    ).toBe('SUBSCRIPTION_EXPIRED');
  });

  it('does not let a bare lapsed state toast from outside the subscription envelope', () => {
    expect(normaliseEventKey({ type: 'expired' })).toBeNull();
    expect(normaliseEventKey({ type: 'subscription_update', data: { status: 'active' } })).toBeNull();
  });

  it('accepts event_type as well as type', () => {
    expect(normaliseEventKey({ event_type: 'order_rejected' })).toBe('ORDER_REJECTED');
  });

  it('accepts a bare notification row that arrived without its category', () => {
    expect(normaliseEventKey({ type: 'strategy_stopped' })).toBe('STRATEGY_STOPPED');
  });
});

describe('normaliseEventKey — default closed', () => {
  it('returns null for real backend events that are not one of the seven', () => {
    const notNotifiable = [
      { type: 'notification', data: { type: 'strategy_created', category: 'strategy' } },
      { type: 'notification', data: { type: 'exchange_connected', category: 'exchange' } },
      { type: 'notification', data: { type: 'paper_order_placed', category: 'trade' } },
      { type: 'notification', data: { type: 'paper_account_reset', category: 'system' } },
      { type: 'notification', data: { type: 'kill_switch_activated', category: 'risk' } },
      { type: 'notification', data: { type: 'risk_settings_updated', category: 'risk' } },
      { type: 'notification', data: { type: 'profile_updated', category: 'security' } },
      { type: 'notification', data: { type: 'payment_failed', category: 'billing' } },
      { type: 'notification', data: { type: 'subscription_renewed', category: 'billing' } },
      { type: 'notification', data: { type: 'subscription_cancelled', category: 'billing' } },
      { type: 'notification', data: { type: 'support_reply', category: 'support' } },
      { type: 'deploy_started', channel: 'deployment_events' },
      { type: 'bot_started', channel: 'deployment_events' },
      { type: 'bot_disconnected', channel: 'bot_status' },
      { type: 'order_filled', channel: 'execution_events' },
      { type: 'order_partial', channel: 'execution_events' },
      { type: 'order_error', channel: 'execution_events' },
      { type: 'risk_block', channel: 'risk_events' },
      { type: 'kill_switch', channel: 'risk_events' },
      { type: 'drawdown_alert', channel: 'risk_events' },
      { type: 'signal_rejected', channel: 'signal_trace' },
      { type: 'paper_order_rejected' },
      { type: 'paper_session_stopped' },
      { type: 'heartbeat' },
      { type: 'market_tick' },
      { type: 'risk.kill_switch_activated' },
      { type: 'exchange_health' },
      { type: 'strategy_status' },
    ];
    for (const event of notNotifiable) {
      expect(normaliseEventKey(event), JSON.stringify(event)).toBeNull();
    }
  });

  it('will not let a new category reuse an allowlisted type name', () => {
    // `strategy_deployed` is allowlisted only under category `strategy`.
    expect(
      normaliseEventKey({
        type: 'notification',
        data: { type: 'strategy_deployed', category: 'marketing' },
      }),
    ).toBeNull();
  });

  it('returns null for malformed input rather than throwing', () => {
    for (const event of [null, undefined, 0, '', 'order_rejected', [], {}, { type: '' }, { type: 42 }]) {
      expect(normaliseEventKey(event), String(event)).toBeNull();
    }
  });

  it('does not resolve inherited Object.prototype keys', () => {
    for (const type of ['constructor', 'toString', '__proto__', 'hasOwnProperty']) {
      expect(normaliseEventKey({ type }), type).toBeNull();
    }
  });
});

describe('notificationFor', () => {
  it('returns null for anything unmapped', () => {
    expect(notificationFor({ type: 'order_filled' })).toBeNull();
    expect(notificationFor(null)).toBeNull();
  });

  it('reads fields out of the notification row and its metadata', () => {
    expect(
      notificationFor({
        type: 'notification',
        data: {
          type: 'strategy_deployed',
          category: 'strategy',
          strategy_id: 'st_1',
          metadata: { strategy_name: 'RSI Reversion', environment: 'LIVE' },
        },
      }),
    ).toEqual({
      severity: 'success',
      message: 'RSI Reversion is now deployed to LIVE.',
    });
  });

  it('reads fields out of a channel frame payload', () => {
    expect(
      notificationFor({
        type: 'order_rejected',
        channel: 'execution_events',
        payload: { exchange: 'Binance', symbol: 'BTC/USDT' },
      }),
    ).toEqual({
      severity: 'error',
      message: 'Binance rejected an order for BTC/USDT.',
    });
  });

  it('never renders undefined when a field is absent', () => {
    for (const event of [
      { type: 'deploy_success' },
      { type: 'deploy_failed' },
      { type: 'exchange_disconnected' },
      { type: 'order_rejected' },
      { type: 'bot_stopped' },
      { type: 'backtest_complete' },
      { type: 'subscription_update', event: 'subscription_expired' },
    ]) {
      const result = notificationFor(event);
      expect(result, JSON.stringify(event)).not.toBeNull();
      expect(result.message).not.toMatch(/undefined|null|NaN|\[object/);
      expect(result.message.trim()).not.toBe('');
    }
  });

  it('carries the declared severity, not the backend severity', () => {
    // The backend dispatches exchange_disconnected at severity 'warning'; the toast is
    // an error because live strategies have stopped trading.
    expect(
      notificationFor({
        type: 'notification',
        data: { type: 'exchange_disconnected', category: 'exchange', severity: 'warning' },
      }).severity,
    ).toBe('error');
  });
});
