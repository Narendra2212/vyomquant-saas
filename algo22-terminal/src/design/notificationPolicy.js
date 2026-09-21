/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/design/notificationPolicy.js — the Requirement 16 allowlist
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign §11.5. Requirements 16.1, 16.2.
 *
 * Requirement 16.1 names seven categories of event that may interrupt a trader.
 * Requirement 16.2 says nothing else may. Both are declared here, as data, in one file,
 * and this table is the only thing on an in-scope page that may raise a notification for
 * a backend event.
 *
 * **Default-closed is the point.** `notificationFor` returns `null` for anything it does
 * not recognise, so an event type added to the backend tomorrow cannot start toasting
 * without being added to `NOTIFIABLE` first. Requirement 16.2 is therefore satisfied
 * structurally, rather than by chasing individual noisy toasts around the pages.
 *
 * Structurally, the other half: the toast transport is called from exactly one place. A
 * single `useNotificationStream` hook subscribes to the socket, passes every event
 * through `notificationFor`, and calls `window.showToast` only on a non-null result.
 * Pages do not call `window.showToast` for *backend* events. They may still call it for
 * their own action outcomes ("Saved as version 7"), which are not backend events and are
 * not what Requirement 16.2 is about.
 *
 * The `NotificationCenter` page keeps showing the full history. This allowlist governs
 * *interruption*, not the record.
 *
 * This module has no imports and no colour concern. Severity here is a toast severity
 * declared per key — it is deliberately NOT read from the event, because the whole point
 * of an allowlist is that the client decides how loudly to speak.
 */

/*
 * ── Field access ──────────────────────────────────────────────────────────
 *
 * The copy functions read server fields, and real payloads do not all carry the same
 * names. `notifications` rows carry `strategy_id` and `title` but no `strategy_name`;
 * `ws_channels` frames carry `strategy_id`, `bot_id` and a nested `payload`; exchange
 * fields appear as both `exchange` and `exchange_id`.
 *
 * So each field is looked up across the real spellings and falls back to an honest
 * phrase. A toast reading "undefined rejected an order for undefined" is exactly the
 * fabrication Requirement 14.5 exists to prevent, and it is worse than a slightly vaguer
 * sentence.
 */
const pick = (event, names, fallback) => {
  for (const name of names) {
    const value = event[name];
    if (typeof value === 'string' && value.trim() !== '') return value.trim();
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  }
  return fallback;
};

const strategyOf = (e) => pick(e, ['strategy_name', 'strategyName', 'name'], 'A strategy');
const exchangeOf = (e) => pick(e, ['exchange', 'exchange_id', 'exchangeId'], 'An exchange');
const symbolOf = (e) => pick(e, ['symbol', 'market', 'pair'], 'a market');
const environmentOf = (e) =>
  pick(
    e,
    ['environment', 'execution_environment', 'executionEnvironment', 'mode'],
    'an unconfirmed environment',
  );
const listingOf = (e) =>
  pick(e, ['listing_name', 'listingName', 'strategy_name', 'name'], 'a marketplace strategy');

/**
 * The seven Requirement 16.1 categories. Nothing else may interrupt.
 *
 * `severity` is one of `success | error | warning | info` — the toast vocabulary, not the
 * backend's (`info warning critical emergency`).
 */
export const NOTIFIABLE = Object.freeze({
  DEPLOYMENT_SUCCEEDED: Object.freeze({
    severity: 'success',
    copy: (e) => `${strategyOf(e)} is now deployed to ${environmentOf(e)}.`,
  }),
  DEPLOYMENT_FAILED: Object.freeze({
    severity: 'error',
    copy: (e) => `${strategyOf(e)} could not be deployed.`,
  }),
  EXCHANGE_DISCONNECTED: Object.freeze({
    severity: 'error',
    copy: (e) => `${exchangeOf(e)} disconnected. Live strategies are not trading.`,
  }),
  ORDER_REJECTED: Object.freeze({
    severity: 'error',
    copy: (e) => `${exchangeOf(e)} rejected an order for ${symbolOf(e)}.`,
  }),
  STRATEGY_STOPPED: Object.freeze({
    severity: 'warning',
    copy: (e) => `${strategyOf(e)} stopped.`,
  }),
  BACKTEST_COMPLETED: Object.freeze({
    severity: 'info',
    copy: (e) => `Backtest finished for ${strategyOf(e)}.`,
  }),
  SUBSCRIPTION_EXPIRED: Object.freeze({
    severity: 'warning',
    copy: (e) => `Your subscription to ${listingOf(e)} has expired.`,
  }),
});

/** The seven keys, in Requirement 16.1's order. */
export const NOTIFIABLE_KEYS = Object.freeze(Object.keys(NOTIFIABLE));

/*
 * ── Envelope shapes the backend actually sends ────────────────────────────
 *
 * `notification` — `routers/notifications.py::create_notification` broadcasts
 *   `{type: 'notification', data: {id, user_id, type, category, severity, title,
 *   message, strategy_id, exchange, metadata, read, created_at}}`. `TopBar.jsx`,
 *   `Sidebar.jsx`, `NotificationCenter.jsx` and `Dashboard.jsx` all already subscribe to
 *   `'notification'`. The discriminator is the *inner* row's `type` + `category`.
 *
 * `subscription_update` — `core/realtime_sync.py::broadcast_subscription_change` sends
 *   `{type: 'subscription_update', event: <event_type>, data: {...}}`. The discriminator
 *   is `event`, not `type`.
 *
 * Everything else (`ws_channels.py` channel frames, `paper_events.py`) carries its own
 * event name on `type`, alongside `channel` and a nested `payload`.
 */
const ENVELOPE_TYPES = Object.freeze({
  notification: 'inner',
  subscription_update: 'event',
});

/**
 * Backend `notifications.category` + `type` pair → allowlist key.
 *
 * Every pair below is a real `dispatch_user_notification(...)` call site in
 * `backend_app/`. The pair is keyed rather than the type alone because `category` is what
 * makes the type unambiguous — `strategy_stopped` in `strategy` is a deployment ending,
 * and the same word in another category would not be.
 */
export const CATEGORY_TYPE_KEY = Object.freeze({
  // routers/strategies.py — dispatch_user_notification(event_type=…, category='strategy')
  'strategy:strategy_deployed': 'DEPLOYMENT_SUCCEEDED',
  'strategy:strategy_stopped': 'STRATEGY_STOPPED',
  // routers/exchange.py — severity 'warning' on the backend; 'error' here, because a
  // disconnected exchange means live strategies have stopped trading.
  'exchange:exchange_disconnected': 'EXCHANGE_DISCONNECTED',
});

/**
 * WebSocket / pipeline event type → allowlist key.
 *
 * Every name below is a real backend constant:
 * - `deploy_success`, `deploy_failed`, `bot_stopped` — `ws_channels.EventType`
 *   (`DEPLOYMENT_EVENTS` channel)
 * - `order_rejected` — `ws_channels.EventType`, `core/event_pipeline.EventType`, and
 *   `exchange_telemetry.emit_order_rejected`
 * - `exchange_disconnected`, `strategy_deployed`, `strategy_stopped` — the
 *   `dispatch_user_notification` event types. These also appear in `CATEGORY_TYPE_KEY`
 *   below, which is the path a full notification row takes. They are repeated here so a
 *   row that arrives without its `category` (the in-memory fallback store, or a partial
 *   frame) still resolves rather than being dropped for a missing field.
 * - `subscription_expired` — the lowercase form of
 *   `marketplace/expiry_sweep.AUDIT_REASON_CODE` (`'SUBSCRIPTION_EXPIRED'`), the name the
 *   backend gives the act of a subscription lapsing
 * - `backtest_complete` — `core/models/pydantic_models.NotificationPreferences`. There is
 *   no server-pushed backtest event: `GET /api/strategies/backtest/{job_id}` is polled
 *   from a Redis status hash whose `status` goes `running → completed | failed`. The
 *   Backtester page's poller raises `{type: 'backtest_complete', …}` on that transition,
 *   which is why the flag's own name is the key here rather than an invented event name.
 */
export const EVENT_TYPE_KEY = Object.freeze({
  deploy_success: 'DEPLOYMENT_SUCCEEDED',
  strategy_deployed: 'DEPLOYMENT_SUCCEEDED',

  deploy_failed: 'DEPLOYMENT_FAILED',

  exchange_disconnected: 'EXCHANGE_DISCONNECTED',

  order_rejected: 'ORDER_REJECTED',

  strategy_stopped: 'STRATEGY_STOPPED',
  bot_stopped: 'STRATEGY_STOPPED',

  backtest_complete: 'BACKTEST_COMPLETED',

  subscription_expired: 'SUBSCRIPTION_EXPIRED',
});

/*
 * ── Deliberately NOT mapped ───────────────────────────────────────────────
 *
 * Default-closed means an exclusion has to be a decision, so the near misses are written
 * down rather than left to look like oversights:
 *
 * - `paper_order_rejected`, `paper_session_stopped` and the rest of
 *   `paper_events.PaperEvent`: a simulated refusal changes nothing a trader must act on,
 *   and `PaperTrading.jsx` already renders session events inline on its own channel.
 * - `deploy_started`, `bot_started`, `bot_connected`, `exchange_connected`,
 *   `strategy_created`: progress and success-of-the-expected. Requirement 16.1's seven
 *   are all events that change what a trader has to do.
 * - `order_submitted`, `order_filled`, `order_partial`, `order_error`: a fill is the
 *   normal case and belongs on the Live Trading page, not in a toast. `order_error` is a
 *   transport failure, reported by the panel that issued it.
 * - `risk_block`, `risk_warning`, `kill_switch`, `position_limit`, `drawdown_alert`,
 *   `risk.kill_switch_activated`: the risk surface is Requirement 3's, and the kill
 *   switch already has its own modal. Not one of the seven.
 * - `payment_failed`, `subscription_cancelled`, `subscription_renewed`,
 *   `cancellation_reversed`, `support_reply`, `profile_updated`, `heartbeat`,
 *   `market_tick`: real backend notifications, none of them one of the seven.
 * - HTTP error codes (`STRATEGY_DEPLOY_FAILED`, `MARKETPLACE_SUBSCRIPTION_EXPIRED`, …)
 *   are not events. They are an `ApiError` on a request the trader just made, and
 *   `design/errorCopy.js` translates them in place.
 */

/** The record a copy function reads: the envelope flattened over its nested payload. */
const fieldsOf = (event) => {
  const inner = event && typeof event.data === 'object' && event.data !== null ? event.data : null;
  const payload =
    event && typeof event.payload === 'object' && event.payload !== null ? event.payload : null;
  const innerPayload =
    inner && typeof inner.payload === 'object' && inner.payload !== null ? inner.payload : null;
  const metadata =
    inner && typeof inner.metadata === 'object' && inner.metadata !== null ? inner.metadata : null;
  return { ...event, ...inner, ...metadata, ...innerPayload, ...payload };
};

const asKey = (value) => (typeof value === 'string' ? value.trim().toLowerCase() : '');

/**
 * Map a backend event onto one of the seven allowlist keys, or `null`.
 *
 * Resolution order, each step reading a real field:
 *   1. unwrap a known envelope — `notification` carries the row on `data`;
 *      `subscription_update` carries its discriminator on `event`
 *   2. the `category` + `type` pair, when the payload is a notification row
 *   3. the event type on its own, for channel frames and pipeline events
 *   4. otherwise `null`
 *
 * Total over any input. A `null`, a non-object, an unknown type and an event carrying a
 * category with no type all return `null`, which `notificationFor` reads as "do not
 * notify".
 *
 * @param {unknown} event
 * @returns {string | null}
 */
export function normaliseEventKey(event) {
  if (!event || typeof event !== 'object') return null;

  const outerType = asKey(event.type) || asKey(event.event_type);
  const envelope = Object.prototype.hasOwnProperty.call(ENVELOPE_TYPES, outerType)
    ? ENVELOPE_TYPES[outerType]
    : null;

  let row = event;
  let type = outerType;

  if (envelope === 'inner') {
    // `{type: 'notification', data: {…the row}}`
    row = event.data && typeof event.data === 'object' ? event.data : {};
    type = asKey(row.type) || asKey(row.event_type);
  } else if (envelope === 'event') {
    // `{type: 'subscription_update', event: 'subscription_expired', data: {…}}`
    row = event.data && typeof event.data === 'object' ? event.data : {};
    /*
     * Some `sync_subscription_change` call sites name the act on `event`; others only
     * carry the resulting state on `data.status` / `data.subscription_status` (the
     * lowercase column spellings from `marketplace/subscription_state.py` and
     * `profiles.subscription_status`). A lapsed state is normalised to the act's own
     * name here, inside the envelope branch, rather than by putting the bare word
     * `expired` in the type table — a bare `{type: 'expired'}` from anywhere else must
     * not toast.
     */
    const state = asKey(row.status) || asKey(row.subscription_status);
    type = asKey(event.event) || (state === 'expired' ? 'subscription_expired' : state);
  }

  if (type === '') return null;

  const category = asKey(row.category);
  if (category !== '') {
    const pairKey = `${category}:${type}`;
    if (Object.prototype.hasOwnProperty.call(CATEGORY_TYPE_KEY, pairKey)) {
      return CATEGORY_TYPE_KEY[pairKey];
    }
    /*
     * A notification row whose category is known but whose (category, type) pair is not
     * stops here. It does NOT fall through to the type-only table: the pair is the more
     * specific statement, and letting a row bypass it would mean a new
     * `dispatch_user_notification` category could reuse an allowlisted type name and
     * toast without being declared. That is the hole Requirement 16.2 closes.
     */
    return null;
  }

  return Object.prototype.hasOwnProperty.call(EVENT_TYPE_KEY, type)
    ? EVENT_TYPE_KEY[type]
    : null;
}

/**
 * The one decision: does this backend event interrupt the trader, and if so, saying what?
 *
 * @param {unknown} event
 * @returns {{severity: string, message: string} | null} `null` means: do not notify.
 */
export function notificationFor(event) {
  const key = normaliseEventKey(event);
  if (key === null) return null;
  const entry = NOTIFIABLE[key];
  if (!entry) return null;
  return { severity: entry.severity, message: entry.copy(fieldsOf(event)) };
}
