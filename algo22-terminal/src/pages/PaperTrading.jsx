/**
 * PaperTrading.jsx — the Paper_Trading_UI (Requirements 20.1 – 20.6, 18.11, 18.15).
 *
 * Routed at `/app/paper-trading` inside the existing `<AuthGuard>` → `<AppShell>` tree, lazily
 * loaded from `src/App.jsx` beside the other authenticated pages, and reachable from the sidebar.
 *
 * WHAT THIS PAGE DOES NOT BUILD
 * -----------------------------
 * No HTTP client, no base URL, no host (Requirement 20.2). Every call travels `api.paper` and
 * `api.library`, which travel the shared `../apiClient` — so the token header, the retries, the
 * circuit breaker and the error envelope are the same ones the rest of the product gets. The
 * design system is `../components/ui-legacy/primitives` plus `../components/ui/*`, the
 * notification mechanism is `window.showToast(type, message)` that `AppShell` installs, and the
 * state management is `useAppState`.
 *
 * WHERE EVERY DISPLAYED FIGURE COMES FROM
 * ---------------------------------------
 * | display                        | source                                                     |
 * |--------------------------------|------------------------------------------------------------|
 * | market-data status             | `sessions.get()` → `feed_state`, `feed_transport`, `market_data_source` |
 * | session status                 | `sessions.get()` → `session_state`                         |
 * | current price                  | `sessions.events()` → latest `market_tick.close`           |
 * | equity                         | `sessions.equity()` → latest snapshot `total_equity`       |
 * | cash                           | `sessions.equity()` → `available_balance` / `locked_balance`|
 * | unrealized PnL                 | `sessions.metrics()` → `unrealized_pnl`                    |
 * | realized PnL                   | `sessions.metrics()` → `realized_pnl`                      |
 * | total return                   | `sessions.metrics()` → `total_return_pct`                   |
 * | max drawdown                   | `sessions.metrics()` → `max_drawdown_amount` / `_fraction` |
 * | win rate                       | `sessions.metrics()` → `win_rate`                          |
 * | trade count                    | `sessions.metrics()` → `closed_trade_count`, `sessions.trades()` → `count` |
 * | open positions                 | `sessions.positions()`                                     |
 * | open orders                    | `sessions.orders()`, non-terminal `order_state`            |
 * | completed trades               | `sessions.trades()`                                        |
 * | equity curve                   | `sessions.equity()` — the persisted snapshots (Req 18.11)   |
 * | PnL chart                      | `sessions.events()` → `paper_pnl_updated`                  |
 * | drawdown chart                 | `sessions.events()` → `paper_drawdown_updated`             |
 * | price series + trade markers   | `sessions.events()` → `market_tick` + `paper_order_*filled`|
 * | signal stream                  | `sessions.events()` → `signal_generated`                   |
 * | execution events               | `sessions.events()` → the order and session lifecycle frames |
 * | feed latency and health        | `sessions.events()` → `market_tick.latency_ms`, `feed_state` |
 *
 * There is no display on this page without a server-side source, and no figure is derived by
 * arithmetic on another. In particular:
 *
 * * **The equity curve's data source is the equity read and nothing else.** `equityCurvePoints`
 *   takes the `equity` array as its only argument (Requirement 18.11), so a reconnect or a
 *   remount redraws the same curve — it has no access to event state to accumulate.
 * * **`metrics: null` with `computed: false` renders as "not computed", never as zero**, and the
 *   same holds field by field: a `null` `win_rate` is an absent win rate, not a zero one
 *   (Requirements 18.10, 28.5).
 * * **A `stale` figure renders its last validated price instant** rather than presenting itself
 *   as current (Requirement 18.15).
 * * **Money is formatted, never computed.** See `./paperTradingFormat` — integer Minor_Units are
 *   decimal-shifted by moving characters and exact `NUMERIC` decimals are rendered digit for
 *   digit. No total on this page is a float.
 *
 * THE EIGHT PANEL STATES (task 32.3, Requirement 20.5) AND THE SIGNAL EACH IS DERIVED FROM
 * ----------------------------------------------------------------------------------------
 * {@link PanelBody} is the ONE renderer for all eight; every panel on this page routes through
 * it. No branch of it renders a stale or fabricated value in place of an error — an error state
 * is what the panel shows, not a zero.
 *
 * | state                  | derived from                                                      |
 * |------------------------|-------------------------------------------------------------------|
 * | `loading`              | the read's own `status`, before it settled                        |
 * | `empty`                | a **settled** read whose collection or series is empty            |
 * | `error-with-retry`     | `classifyReadFailure` fell through: a status that is not 401/403 and no recognised code |
 * | `disabled`             | `session_state` admits none of pause / resume / stop / reset       |
 * | `unauthorised`         | a 401 or 403 on the read, or a `subscription_refused` frame carrying `CHANNEL_FORBIDDEN` / `CHANNEL_UNAUTHENTICATED` |
 * | `expired-subscription` | the server's own reason code — `MARKETPLACE_SUBSCRIPTION_EXPIRED` / `_NOT_SUBSCRIBED` / `_OPERATION_NOT_PERMITTED`, `PAPER_START_REFUSED` with `details.reason`, or a `my-strategies` entry's `entitling: false` |
 * | `unavailable-strategy` | `MARKETPLACE_STRATEGY_UNAVAILABLE`, `PAPER_START_REFUSED` with `details.validation = STRATEGY_NOT_EXECUTABLE`, or an entry's `unavailable_reason` |
 * | `feed-disconnected`    | `session.feed_state` is not in `TRADEABLE_FEED_STATES`             |
 *
 * The `entitling` / `unavailable_reason` vocabulary is the server's own and is read exactly as
 * `Strategies.jsx` reads it, so the two pages and the eventual refusal name one condition.
 *
 * The reconnecting indicator is `websocketClient.onStatusChange` reporting `connecting` or
 * `reconnecting` — see {@link RECONNECTING_SOCKET_STATUSES} for why both spellings are read.
 *
 * THE REALTIME WIRING AND ITS BOUNDS (task 32.4, Requirements 19.8, 20.8, 27.5)
 * ----------------------------------------------------------------------------
 * One `useEffect`, keyed on `sessionId`, holds everything the route creates for a session and
 * disposes of all of it — see THE SINGLE SESSION EFFECT below for the full list. Live frames go
 * through `retainFrames`, which is the single place Requirement 27.5's bounds are enforced (500
 * events, 1000 ticks) and is pure, so a test asserts the bounds without rendering. Each chart
 * series is bounded to {@link MAX_CHART_POINTS_PER_SERIES} where it is built, and the retained
 * counts are disclosed on screen rather than being an invisible policy.
 *
 * THE RESPONSIVE LAYOUT (task 32.5, Requirement 20.7)
 * ---------------------------------------------------
 * One measurement drives all of it: {@link useObservedWidth} puts a single `ResizeObserver` on the
 * page root and reports the width available to its children — the content box, with the page's own
 * padding already excluded. `layoutModeForWidth` turns that into one of three modes, and every
 * responsive decision on the page reads that mode:
 *
 * | region                                                        | wide (≥ 768) | < 768      | < 640            |
 * |---------------------------------------------------------------|--------------|------------|------------------|
 * | session controls (5 fields, `minmax(180px, 1fr)`)             | auto-fit     | one column | one column       |
 * | last stop / last reset reports (`minmax(220px, 1fr)`)         | auto-fit     | one column | one column       |
 * | session operations (task 32.3's sub-panel)                    | wrapping row | one column | one column       |
 * | status strip (`minmax(230px, 1fr)`)                           | auto-fit     | one column | one column       |
 * | the twelve figures (`minmax(200px, 1fr)`)                     | auto-fit     | one column | one column       |
 * | PnL + drawdown charts (`minmax(320px, 1fr)`)                   | auto-fit     | one column | one column       |
 * | signal stream + execution events (`minmax(320px, 1fr)`)        | auto-fit     | one column | one column       |
 * | the five tables                                                | table        | table      | stacked `<dl>`   |
 *
 * Three things make "no horizontal overflow between 360 px and 1920 px" structural rather than
 * hoped for:
 *
 * * **No grid track can be wider than its container.** `responsiveGridColumns` emits
 *   `minmax(min(Npx, 100%), 1fr)` in the wide layout and `minmax(0, 1fr)` in the collapsed ones.
 *   The first holds even before the first measurement arrives and in an environment with no
 *   `ResizeObserver` at all; the second has no min-content floor, so a `<select>` cannot push it.
 * * **No word can be wider than its line.** `overflowWrap: 'anywhere'` on the page root is
 *   inherited by every descendant, so a 36-character session identifier in a panel subtitle breaks
 *   rather than widening the panel. It affects intrinsic sizing, which `break-word` does not.
 * * **Every table's horizontal scroll stays inside its own panel**, and below 640 px there is no
 *   horizontal scroll at all because {@link DataTable} renders label/value pairs instead. The
 *   pairs are a real `<dl>`, so the column header stays associated with the value — a stacked row
 *   loses the visual header, not the programmatic one.
 *
 * Chart width is `ResponsiveContainer`'s own `ResizeObserver` (recharts 3.8.1 observes its
 * container and re-renders at its width), so this page adds no second observer for it. What a
 * percentage width cannot fix is `YAxis width` and `XAxis minTickGap` — fixed pixel reservations
 * taken out of the plot — and those come from `chartGeometry(layoutMode)`.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Ban,
  CheckCircle2,
  Clock,
  DollarSign,
  FlaskConical,
  Layers,
  Lock,
  Pause,
  Play,
  Radio,
  RefreshCw,
  RotateCcw,
  Square,
  TrendingDown,
  TrendingUp,
  Unplug,
  Wifi,
  WifiOff,
  XCircle,
} from 'lucide-react';

import { C, PanelTitle, Spinner } from '../components/ui-legacy/primitives';
// Task 10.8: every failure on this page is worded by `design/errorCopy.js` and nothing else.
// `extractErrorMessage` used to fill these slots and fell through to `JSON.stringify(detail)`
// and then `err.message`, so an axios message and a FastAPI dump reached the screen verbatim
// (Requirement 14.4). `errorLine` is the one-line form of `translateError`'s authored copy.
import { errorLine } from '../design/errorLine';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
// Task 25.1 / Requirement 12.2. The page-level environment statement is the shared
// `ds/` badge rather than this page's own span, and `ENVIRONMENT.PAPER` is where the
// per-figure tag below now takes its hue, wash and border style from — so the indigo
// treatment lives in `design/semantic.js` and not in two places on this page.
import { TradingEnvironmentBadge } from '../components/ds/TradingEnvironmentBadge';
import { ENVIRONMENT } from '../design/semantic';
import { useAppState } from '../AppState';
import { api } from '../api';
import websocketClient from '../websocketClient';
import {
  CONNECTED_SOCKET_STATUS,
  DEFAULT_CURRENCY,
  EMPTY_RETAINED,
  FEED_STATE_COPY,
  MAX_CHART_POINTS_PER_SERIES,
  MAX_RETAINED_EVENTS,
  MAX_RETAINED_TICKS,
  PANEL_IDLE,
  PANEL_READY,
  PANEL_STATES,
  SESSION_STATE_COPY,
  SUBSCRIPTION_REFUSED_TYPE,
  SUPPORTED_CURRENCIES,
  TRADEABLE_FEED_STATES,
  boundedTail,
  channelRefusalPanelState,
  chartGeometry,
  classifyReadFailure,
  deriveFromFrames,
  entitlementPanelState,
  entitlementReasonText,
  equityCurvePoints,
  formatClock,
  formatCount,
  formatFractionAsPercent,
  formatInstant,
  formatLatency,
  formatMinorUnits,
  formatMoneyDecimal,
  formatPercentValue,
  formatPrice,
  formatQuantity,
  isOpenOrder,
  isReconnectingStatus,
  isSingleColumnLayout,
  isStackedLayout,
  layoutModeForWidth,
  parseCapitalToMinor,
  responsiveGridColumns,
  retainFrames,
  toChartNumber,
} from './paperTradingFormat';

// ═══════════════════════════════════════════════════════════════════════════
// CONSTANTS
// ═══════════════════════════════════════════════════════════════════════════

/** The timeframes the rest of the product offers, unchanged (see `Backtester.jsx`). */
const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d'];

/** The action name `GET /api/library/my-strategies` uses for "may start a Paper_Session". */
const ACTION_START_PAPER = 'start_paper';

/**
 * `ws_channels.PAPER_FAMILY.channel(session_id)` — `paper.{session_id}`.
 *
 * `PAPER_FAMILY.namespace` is `'paper'` and the separator is a dot; the server builds the same
 * name through the family, and `core/websocket_auth` resolves `paper_sessions.user_id` for it.
 * A name is not an authorisation: the server may answer with a `subscription_refused` frame,
 * which is what this page renders its `unauthorised` state from.
 */
const paperChannelName = (sessionId) => `paper.${sessionId}`;

/**
 * The one socket path this app uses, and the reconnect policy a live view needs.
 *
 * Both are `useBuilderRealtime`'s, imported by value rather than re-derived: the ref count that
 * makes "one connection per browser session" true lives on `websocketClient`, so a second path
 * or a second policy here would be a second socket in all but name — which `design.md` forbids.
 * `maxAttempts: Infinity` is only safe because the delay is capped and jittered, which is why
 * the two travel together.
 */
const PAPER_SOCKET_PATH = '/ws/telemetry';
const PAPER_RECONNECT_POLICY = Object.freeze({
  maxAttempts: Infinity,
  baseDelayMs: 500,
  maxDelayMs: 30000,
  jitterMs: 250,
});

/**
 * The safety poll's period.
 *
 * It re-reads the session and its metrics **only while the socket is not connected**, which is
 * the one condition under which a live view would otherwise sit on stale figures reporting
 * itself current. While frames are arriving it does nothing, so a healthy session pays for no
 * extra round trip.
 */
const SAFETY_POLL_INTERVAL_MS = 30000;

/**
 * The session states the safety poll re-reads.
 *
 * `CREATED` has emitted nothing and `STOPPED` will emit nothing more, so re-reading either is a
 * request whose answer is already known. The interval itself still runs and is still cleared on
 * unmount — the gate is on the request, not on the timer, because a timer that is created
 * conditionally is a timer whose teardown is conditional too.
 */
const POLLED_SESSION_STATES = Object.freeze(['RUNNING', 'PAUSED']);

/** The seven session-scoped reads this page issues, and the order it issues them in. */
const READS = {
  session: (id) => api.paper.sessions.get(id),
  equity: (id) => api.paper.sessions.equity(id),
  metrics: (id) => api.paper.sessions.metrics(id),
  positions: (id) => api.paper.sessions.positions(id),
  orders: (id) => api.paper.sessions.orders(id),
  trades: (id) => api.paper.sessions.trades(id),
  events: (id) => api.paper.sessions.events(id, 0),
};

const READ_KEYS = Object.keys(READS);

/**
 * One read's record. `status` is what a panel branches on; it is never inferred from `data`.
 *
 * `failure` is `classifyReadFailure`'s verdict — which of the eight states this read's error puts
 * its panel in, and the server signal that says so. Kept beside the human message rather than
 * replacing it: the message is what a user reads, the verdict is what the page branches on, and
 * flattening one into the other is how a 403 about an expired subscription turns into "sign in
 * again".
 */
const IDLE_READ = Object.freeze({ status: 'idle', data: null, error: null, failure: null });

const initialReads = () =>
  READ_KEYS.reduce((acc, key) => ({ ...acc, [key]: IDLE_READ }), {});

/** The sentence a panel shows in place of a figure that does not exist. */
const NOT_COMPUTED = 'Not computed';
const NOT_REPORTED = 'Not reported';

const TONE_COLOR = {
  good: C.profit,
  warn: C.warning,
  bad: C.loss,
  muted: C.t2,
};

/**
 * The page root's padding, on every side.
 *
 * A constant rather than a mode-dependent value on purpose: the measured width is the root's
 * CONTENT box, so a padding that shrank when the layout collapsed would widen the content box,
 * which could push the width back over the threshold and oscillate between two layouts forever.
 * The padding is fixed and the thresholds are compared against the width it leaves.
 */
const PAGE_PADDING = C.space.xl;

const panelStyle = {
  background: C.bg2,
  border: `1px solid ${C.border}`,
  borderRadius: C.radius.lg,
  padding: C.space.lg,
  minWidth: 0,
};

const labelStyle = {
  color: C.t2,
  fontSize: 9,
  fontFamily: 'monospace',
  fontWeight: 900,
  letterSpacing: 2,
  textTransform: 'uppercase',
  display: 'block',
  marginBottom: 6,
};

const fieldStyle = {
  width: '100%',
  background: C.bg3,
  border: `1px solid ${C.border}`,
  borderRadius: C.radius.lg,
  padding: '8px 10px',
  fontSize: 11,
  fontFamily: 'monospace',
  color: C.t1,
  outline: 'none',
};

const thStyle = {
  color: C.t3,
  fontWeight: 700,
  padding: '8px 10px',
  textAlign: 'left',
  fontSize: 9,
  letterSpacing: 1,
  textTransform: 'uppercase',
  whiteSpace: 'nowrap',
};

const tdStyle = {
  color: C.t1,
  padding: '8px 10px',
  fontSize: 11,
  fontFamily: 'monospace',
  whiteSpace: 'nowrap',
};

/** A table's caption, in both layouts, so the two say the same thing in the same voice. */
const tableCaptionStyle = {
  textAlign: 'left',
  color: C.t3,
  fontSize: 9,
  fontFamily: 'monospace',
  letterSpacing: 1,
  textTransform: 'uppercase',
  paddingBottom: 6,
};

/** One row of a table, below {@link STACKED_TABLE_MAX_WIDTH}: a two-column `<dl>`. */
const stackedRowStyle = {
  display: 'grid',
  gridTemplateColumns: 'minmax(0, 88px) minmax(0, 1fr)',
  columnGap: 10,
  rowGap: 4,
  alignItems: 'baseline',
  margin: 0,
  padding: '8px 10px',
  background: C.bg3,
  border: `1px solid ${C.border}`,
  borderRadius: C.radius.md,
  minWidth: 0,
};

/** The column's header, carried into the stacked row as the `<dt>` it describes. */
const stackedLabelStyle = {
  color: C.t3,
  fontSize: 9,
  fontFamily: 'monospace',
  fontWeight: 700,
  letterSpacing: 1,
  textTransform: 'uppercase',
  minWidth: 0,
};

const stackedValueStyle = {
  color: C.t1,
  fontSize: 11,
  fontFamily: 'monospace',
  margin: 0,
  minWidth: 0,
  // The nowrap of `tdStyle` is a table concern — a stacked value has one column to itself and
  // must wrap into it rather than widen it.
  whiteSpace: 'normal',
};

// ═══════════════════════════════════════════════════════════════════════════
// SMALL PRESENTATION COMPONENTS
// ═══════════════════════════════════════════════════════════════════════════

/**
 * The simulated marker Requirement 20.6 puts on every figure.
 *
 * Text, not a colour and not an icon alone, and rendered inside the same region as the figure it
 * qualifies rather than once in a page header a scrolled user cannot see.
 *
 * TASK 25.1 — WHY THIS IS NOT `ds/TradingEnvironmentBadge`, AND WHAT DID CHANGE
 * ----------------------------------------------------------------------------
 * §7.8 (1) replaces this page's labels with `TradingEnvironmentBadge environment="PAPER"`. The
 * PAGE-LEVEL label is exactly that now — see the header, where the badge's own `strip` variant
 * renders `ENVIRONMENT.PAPER.long`, which is character-for-character the copy this page used to
 * pass in as `children`. The PER-FIGURE label cannot be the badge: the badge's PAPER label is
 * `PAPER TRADING` (`design/semantic.js`'s `ENVIRONMENT.PAPER.label`, §8.2's declared four-axis
 * table), and `PaperTrading.test.jsx` asserts the exact string `Simulated` inside each of the
 * twelve figure regions, the five titled panels and the tables' cards. Rendering the badge in
 * those seats would change that text, and that suite has to keep passing unchanged. Relabelling
 * the badge is not the way out either — `ENVIRONMENT.PAPER.label` is read by every other
 * consumer and asserted by `dashboard_phase2a_ui.test.jsx`.
 *
 * So the WORD stays and the TREATMENT moves: the hue, the wash and the border style are
 * `ENVIRONMENT.PAPER`'s — the `env.paper` token — and the `FlaskConical` glyph that is
 * `ENVIRONMENT.PAPER.icon` is drawn beside the word, which is the badge's own shape axis. The
 * result is that this page names no colour of its own, and the page-level badge and the
 * per-figure tag can no longer drift apart in hue, border style or icon.
 */
const SimulatedTag = () => (
  <span
    className="rounded-sm"
    style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 3,
      fontSize: 8,
      fontFamily: 'monospace',
      fontWeight: 900,
      letterSpacing: 1.5,
      textTransform: 'uppercase',
      color: ENVIRONMENT.PAPER.fg,
      backgroundColor: ENVIRONMENT.PAPER.wash,
      borderWidth: 1,
      borderStyle: ENVIRONMENT.PAPER.border,
      borderColor: ENVIRONMENT.PAPER.fg,
      padding: '1px 5px',
      whiteSpace: 'nowrap',
    }}
  >
    <FlaskConical size={9} strokeWidth={2.5} aria-hidden="true" />
    Simulated
  </span>
);

/**
 * A status pill carrying a shape, a word and a colour — never a colour alone.
 *
 * `tone` drives the colour; `label` is always rendered as text, so the state is legible to a
 * reader who cannot distinguish the two.
 */
const StatusPill = ({ tone = 'muted', label, Icon = null, title }) => (
  <span
    title={title}
    style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 5,
      fontSize: 10,
      fontFamily: 'monospace',
      fontWeight: 800,
      letterSpacing: 0.6,
      textTransform: 'uppercase',
      color: TONE_COLOR[tone] || C.t2,
      border: `1px solid ${TONE_COLOR[tone] || C.t2}55`,
      borderRadius: C.radius.sm,
      padding: '2px 7px',
    }}
  >
    {Icon ? <Icon size={11} aria-hidden="true" /> : null}
    {label}
  </span>
);

/**
 * The last validated price instant, shown when a figure's `stale` flag is true.
 *
 * Requirement 18.15: a stale figure renders the timestamp of the last validated price rather
 * than presenting itself as current. When no such timestamp reached the client, that is said
 * plainly — a substituted "now" would be exactly the value presented as a measurement that is
 * not one.
 */
const StaleNote = ({ lastPriceAt }) => (
  <div
    style={{
      display: 'flex',
      alignItems: 'center',
      gap: 5,
      marginTop: 4,
      color: C.warning,
      fontSize: 9,
      fontFamily: 'monospace',
    }}
  >
    <Clock size={10} aria-hidden="true" />
    <span>
      {lastPriceAt
        ? `Stale — last validated price ${formatInstant(lastPriceAt) || lastPriceAt}`
        : 'Stale — no last validated price instant was reported with this figure'}
    </span>
  </div>
);

/**
 * One figure.
 *
 * `value` of `null` renders `absent` (default "Not computed") rather than a zero: the two are
 * different statements and this component refuses to flatten them.
 */
const Figure = ({
  label,
  value,
  absent = NOT_COMPUTED,
  unit = null,
  tone = null,
  Icon = null,
  stale = false,
  lastPriceAt = null,
  hint = null,
  state = PANEL_READY,
  errorMessage = null,
  errorCode = null,
  onRetry = null,
  emptyText = undefined,
  feedState = null,
  feedNote = null,
}) => {
  const missing = value === null || value === undefined || value === '';
  const header = (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
      <span style={{ ...labelStyle, marginBottom: 0 }}>{label}</span>
      {Icon ? <Icon size={13} aria-hidden="true" style={{ color: C.accent, flexShrink: 0 }} /> : null}
    </div>
  );

  // A figure whose read did not complete, whose subscription does not entitle or whose feed is
  // disconnected routes through the ONE non-ready renderer, so it shows that state rather than a
  // value it does not have.
  if (state !== PANEL_READY) {
    return (
      <div style={panelStyle}>
        {header}
        <PanelBody
          state={state}
          message={errorMessage}
          code={errorCode}
          onRetry={onRetry}
          emptyText={emptyText}
          feedState={feedState}
          feedNote={feedNote}
          lastPriceAt={lastPriceAt}
        />
        <div style={{ marginTop: 8 }}>
          <SimulatedTag />
        </div>
      </div>
    );
  }

  return (
    <div style={panelStyle}>
      {header}
      <div
        style={{
          color: missing ? C.t3 : (tone ? TONE_COLOR[tone] : C.t1),
          fontWeight: 900,
          fontFamily: 'monospace',
          fontSize: missing ? 13 : 18,
          lineHeight: 1.2,
          wordBreak: 'break-all',
        }}
      >
        {missing ? absent : value}
        {!missing && unit ? <span style={{ fontSize: 11, color: C.t2, marginLeft: 4 }}>{unit}</span> : null}
      </div>
      {hint ? (
        <div style={{ color: C.t3, fontSize: 9, fontFamily: 'monospace', marginTop: 4 }}>{hint}</div>
      ) : null}
      {stale ? <StaleNote lastPriceAt={lastPriceAt} /> : null}
      <div style={{ marginTop: 8 }}>
        <SimulatedTag />
      </div>
    </div>
  );
};

/**
 * A non-ready panel's block: an icon, a heading word, a sentence and — where there is one — the
 * server's own code, rendered verbatim.
 *
 * The code is shown because Requirement 20.5's eight states are a *narrowing* of a larger server
 * vocabulary: four distinct refusal codes collapse onto `expired-subscription`, and printing the
 * one that arrived is what keeps the distinction Requirement 7.10 makes on the wire from being
 * lost on screen.
 */
const PanelNotice = ({ tone, Icon, heading, testId, children, code = null, footer = null, role = 'status' }) => (
  <div
    role={role}
    aria-live={role === 'status' ? 'polite' : undefined}
    data-testid={testId}
    data-panel-state={testId}
    style={{ padding: C.space.lg, display: 'flex', flexDirection: 'column', gap: 8 }}
  >
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
      {Icon ? (
        <Icon size={13} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1, color: TONE_COLOR[tone] || C.t2 }} />
      ) : null}
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            color: TONE_COLOR[tone] || C.t2,
            fontSize: 9,
            fontFamily: 'monospace',
            fontWeight: 900,
            letterSpacing: 1.5,
            textTransform: 'uppercase',
            marginBottom: 3,
          }}
        >
          {heading}
        </div>
        <div style={{ color: C.t2, fontSize: 11, fontFamily: 'monospace', lineHeight: 1.5, whiteSpace: 'normal' }}>
          {children}
        </div>
        {code ? (
          <div style={{ color: C.t3, fontSize: 9, fontFamily: 'monospace', marginTop: 4 }}>
            Reported by the server as <code>{code}</code>.
          </div>
        ) : null}
      </div>
    </div>
    {footer}
  </div>
);

/**
 * The one place a panel's non-ready state is rendered — all eight of Requirement 20.5's states,
 * plus `idle` (no session selected) and the pass-through to `children`.
 *
 * There is no second status renderer on this page: every panel, every figure and every chart
 * routes through here, so "an error state is what the panel shows, not a zero" is structural
 * rather than repeated. No branch below reads a figure, a collection or a previous value.
 *
 * @param {Object} props
 * @param {string} props.state - One of {@link PANEL_STATES}, {@link PANEL_IDLE} or {@link PANEL_READY}.
 * @param {string} [props.message] - The server's message for the failure, when it sent one.
 * @param {string} [props.code] - The server's own error / refusal code, rendered verbatim.
 * @param {Function} [props.onRetry] - Re-issues the read. Required for `error-with-retry` to
 *   offer a retry; a panel with no way to retry says so rather than showing a dead button.
 * @param {string} [props.disabledReason] - Why the control is unavailable in the current
 *   `session_state`. Named by the caller from the state, never guessed here.
 * @param {string} [props.feedState] - The recorded `feed_state`, for `feed-disconnected`.
 * @param {string} [props.feedNote] - `FEED_STATE_COPY[feedState].note`, verbatim.
 * @param {string} [props.lastPriceAt] - The last validated price instant (Requirement 18.15).
 */
const PanelBody = ({
  state,
  message,
  code = null,
  onRetry,
  retryLabel = 'Retry',
  emptyText = 'Nothing recorded yet.',
  idleText = 'Select or start a session to populate this panel.',
  disabledReason = null,
  feedState = null,
  feedNote = null,
  lastPriceAt = null,
  children,
}) => {
  if (state === PANEL_IDLE) {
    return (
      <div data-testid="panel-idle" style={{ padding: C.space.lg, color: C.t3, fontSize: 11, fontFamily: 'monospace' }}>
        {idleText}
      </div>
    );
  }

  if (state === PANEL_STATES.LOADING) {
    return (
      <div
        role="status"
        aria-live="polite"
        data-testid="panel-loading"
        data-panel-state="panel-loading"
        style={{ display: 'flex', alignItems: 'center', gap: 8, padding: C.space.lg, color: C.t2, fontSize: 11, fontFamily: 'monospace' }}
      >
        <Spinner size={14} />
        <span>Loading…</span>
      </div>
    );
  }

  if (state === PANEL_STATES.EMPTY) {
    return (
      <div data-testid="panel-empty" data-panel-state="panel-empty" style={{ padding: C.space.lg, color: C.t3, fontSize: 11, fontFamily: 'monospace' }}>
        {emptyText}
      </div>
    );
  }

  if (state === PANEL_STATES.ERROR) {
    return (
      <PanelNotice
        role="alert"
        tone="bad"
        Icon={AlertTriangle}
        heading="This read did not complete"
        testId="panel-error-with-retry"
        code={code}
        footer={
          onRetry ? (
            <div>
              <Button variant="outline" size="xs" onClick={onRetry}>
                {retryLabel}
              </Button>
            </div>
          ) : null
        }
      >
        {message || 'The read failed and no figure is shown in its place.'}{' '}
        {onRetry
          ? 'Retry it below. Nothing stale and nothing zero is standing in for the value.'
          : 'This panel offers no retry of its own — use Refresh at the top of the page.'}
      </PanelNotice>
    );
  }

  if (state === PANEL_STATES.DISABLED) {
    return (
      <PanelNotice tone="muted" Icon={Ban} heading="Not available in this state" testId="panel-disabled">
        {disabledReason
          || 'The session state does not admit this control right now, so it is not offered.'}
      </PanelNotice>
    );
  }

  if (state === PANEL_STATES.UNAUTHORISED) {
    return (
      <PanelNotice role="alert" tone="bad" Icon={Lock} heading="Not authorised" testId="panel-unauthorised" code={code}>
        {message
          || 'This session is only readable while you are signed in as its owner. Sign in again, or open a session of your own.'}
      </PanelNotice>
    );
  }

  if (state === PANEL_STATES.EXPIRED_SUBSCRIPTION) {
    return (
      <PanelNotice
        role="alert"
        tone="warn"
        Icon={Clock}
        heading="Subscription does not entitle"
        testId="panel-expired-subscription"
        code={code}
      >
        {message
          || 'The subscription behind this strategy does not currently entitle you to run it. Renew it from the strategy listing.'}
      </PanelNotice>
    );
  }

  if (state === PANEL_STATES.UNAVAILABLE_STRATEGY) {
    return (
      <PanelNotice
        role="alert"
        tone="warn"
        Icon={XCircle}
        heading="Strategy unavailable"
        testId="panel-unavailable-strategy"
        code={code}
      >
        {message
          || 'The runnable version behind this strategy does not resolve right now, so it cannot be run.'}
      </PanelNotice>
    );
  }

  if (state === PANEL_STATES.FEED_DISCONNECTED) {
    return (
      <PanelNotice
        tone="warn"
        Icon={Unplug}
        heading="Feed disconnected"
        testId="panel-feed-disconnected"
        code={feedState || null}
      >
        {/* Three facts, and none of them is a figure: what the feed state is, what that state
            means, and when the last validated price was recorded. A price is deliberately not
            among them — Requirement 18.15's instruction is to render the INSTANT, and Requirement
            28.5's is not to present a previous value as current. */}
        {feedState
          ? feedNote
            || 'This feed state is not one of the five the platform records, so it is named verbatim above. Execution is admitted on HEALTHY alone, and no figure here is a current one.'
          : 'The session read completed and reported no feed state at all, so freshness has not been demonstrated and nothing here is presented as current.'}{' '}
        {lastPriceAt
          ? `The last validated price was recorded ${formatInstant(lastPriceAt) || lastPriceAt}.`
          : 'No last validated price instant was reported, and none is substituted.'}
      </PanelNotice>
    );
  }

  return children;
};

/** A table wrapper that keeps the horizontal scroll inside the panel rather than the page. */
const TableFrame = ({ caption, children }) => (
  <div style={{ overflowX: 'auto', maxWidth: '100%' }} data-table-layout="table">
    <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 520 }}>
      <caption style={{ captionSide: 'top', ...tableCaptionStyle }}>{caption}</caption>
      {children}
    </table>
  </div>
);

/**
 * The ONE table on this page, in both of its layouts (task 32.5, Requirement 20.7).
 *
 * Above {@link STACKED_TABLE_MAX_WIDTH} it is a `<table>` inside {@link TableFrame}, whose 520 px
 * minimum keeps any horizontal scroll inside the panel rather than on the page. Below it, a row
 * becomes a `<dl>` of the same cells: `<dt>` is the column header the cell sat under and `<dd>` is
 * the cell. The association a stacked row loses is the VISUAL one — `<dt>`/`<dd>` keeps the
 * programmatic one, so the value is still announced with its column name, which a `::before`
 * decoration on a `display: block` cell would not do.
 *
 * Both layouts render from the same `columns` array, so a cell cannot exist in one and not the
 * other, and cannot be formatted differently in the two. `render` produces the cell's content and
 * `cellStyle` its per-row style overrides — the same function feeds `<td>` and `<dd>`.
 *
 * @param {Object} props
 * @param {string} props.caption - The table's caption, rendered in both layouts.
 * @param {Array<{key: string, header: React.ReactNode, render: Function, cellStyle?: Function}>} props.columns
 * @param {Object[]} props.rows
 * @param {Function} props.rowKey - A stable React key for a row.
 * @param {boolean} props.stacked - `isStackedLayout(layoutMode)`, measured once for the page.
 */
const DataTable = ({ caption, columns, rows, rowKey, stacked }) => {
  if (!stacked) {
    return (
      <TableFrame caption={caption}>
        <thead>
          <tr style={{ borderBottom: `1px solid ${C.border}` }}>
            {columns.map((column) => (
              <th key={column.key} scope="col" style={thStyle}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} style={{ borderBottom: `1px solid ${C.border}55` }}>
              {columns.map((column) => (
                <td key={column.key} style={{ ...tdStyle, ...(column.cellStyle ? column.cellStyle(row) : null) }}>
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </TableFrame>
    );
  }

  return (
    <div data-table-layout="stacked" style={{ minWidth: 0 }}>
      <div style={tableCaptionStyle}>{caption}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: C.space.sm, minWidth: 0 }}>
        {rows.map((row) => (
          <dl key={rowKey(row)} style={stackedRowStyle}>
            {columns.map((column) => (
              <React.Fragment key={column.key}>
                <dt style={stackedLabelStyle}>{column.header}</dt>
                <dd style={{ ...stackedValueStyle, ...(column.cellStyle ? column.cellStyle(row) : null), whiteSpace: 'normal' }}>
                  {column.render(row)}
                </dd>
              </React.Fragment>
            ))}
          </dl>
        ))}
      </div>
    </div>
  );
};

/**
 * A chart tooltip that shows the **exact** recorded text, not the number the chart plotted.
 *
 * `rows` names which fields of the point to render and how. The plotted number placed the pixel;
 * this is where the figure a trader reads comes from, and it is the string the server sent.
 */
const ChartTooltip = ({ active, payload, rows }) => {
  if (!active || !payload || !payload.length) return null;
  const point = payload[0].payload || {};
  return (
    <div
      style={{
        background: C.bg1,
        border: `1px solid ${C.borderLight}`,
        borderRadius: C.radius.md,
        padding: '8px 10px',
        fontFamily: 'monospace',
        fontSize: 10,
        color: C.t1,
        boxShadow: C.shadowMd,
      }}
    >
      {rows.map(({ key, label, render }) => {
        const raw = point[key];
        const text = render ? render(raw) : raw;
        return (
          <div key={key} style={{ display: 'flex', gap: 10, justifyContent: 'space-between' }}>
            <span style={{ color: C.t3 }}>{label}</span>
            <span>{text === null || text === undefined || text === '' ? NOT_REPORTED : text}</span>
          </div>
        );
      })}
      <div style={{ marginTop: 6 }}>
        <SimulatedTag />
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// THE ONE MEASUREMENT THE LAYOUT IS DRIVEN FROM
// ═══════════════════════════════════════════════════════════════════════════

/**
 * The width available to `ref`'s children, watched with one `ResizeObserver` (Requirement 20.7).
 *
 * The reported number is the element's CONTENT box — its padding already excluded — because that
 * is the width a grid track has to fit into. `contentRect.width` is that box, and the synchronous
 * seed subtracts {@link PAGE_PADDING} from `clientWidth` so the first paint and every later
 * measurement are the same quantity rather than two that differ by the padding.
 *
 * Why an observer rather than `window.innerWidth` or a `matchMedia` query on the viewport: this
 * page renders inside a shell with a 210 px sidebar, so the viewport is never the width the page
 * actually has. A sidebar that collapses, a scrollbar that appears, or a window that is resized all
 * change the available width without necessarily crossing a viewport breakpoint.
 *
 * TEARDOWN — one of two paths, both complete:
 * * with `ResizeObserver`: `observer.disconnect()`;
 * * without it (jsdom, or a browser old enough to lack it): the `resize` listener that stood in for
 *   it is removed with the matching `removeEventListener`.
 *
 * Nothing else is created here: no timer, no second listener, and no state write after the cleanup
 * has run, because both paths stop the only callers of `setWidth` before React drops the effect.
 *
 * @param {React.RefObject<HTMLElement>} ref
 * @returns {number|null} The available width, or `null` until it has been measured.
 */
function useObservedWidth(ref) {
  const [width, setWidth] = useState(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;

    // Sub-pixel churn from a scrollbar or a zoom level must not re-render the page, so a change
    // smaller than a pixel is not one.
    const report = (next) => {
      if (!Number.isFinite(next)) return;
      setWidth((prev) => (prev !== null && Math.abs(prev - next) < 1 ? prev : next));
    };

    const measureNow = () => report(Math.max(0, node.clientWidth - 2 * PAGE_PADDING));

    // Measured before the observer is installed, so the first paint after mount already uses the
    // right layout rather than laying out wide and correcting itself a frame later.
    measureNow();

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measureNow);
      return () => window.removeEventListener('resize', measureNow);
    }

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      report(entry.contentRect ? entry.contentRect.width : Math.max(0, node.clientWidth - 2 * PAGE_PADDING));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref]);

  return width;
}

// ═══════════════════════════════════════════════════════════════════════════
// THE PAGE
// ═══════════════════════════════════════════════════════════════════════════

export default function PaperTrading() {
  const appState = useAppState();
  // The existing state manager's density switch. Used for the extra envelope columns on the
  // execution log only — never to hide one of Requirement 20.4's displays.
  const uiMode = appState?.uiMode ?? 'pro';

  // ── task 32.5: the one measurement every responsive decision below reads ──
  const pageRef = useRef(null);
  const observedWidth = useObservedWidth(pageRef);
  const layoutMode = layoutModeForWidth(observedWidth);
  const stackedTables = isStackedLayout(layoutMode);
  const singleColumn = isSingleColumnLayout(layoutMode);
  const chartAxis = chartGeometry(layoutMode);
  /** A responsive `grid-template-columns` for a region that aims for `minTrackPx` tracks. */
  const gridColumns = useCallback(
    (minTrackPx) => responsiveGridColumns(minTrackPx, layoutMode),
    [layoutMode],
  );

  const [strategies, setStrategies] = useState(IDLE_READ);
  const [sessionList, setSessionList] = useState(IDLE_READ);
  const [reads, setReads] = useState(initialReads);

  const [sessionId, setSessionId] = useState(null);
  const [busy, setBusy] = useState(null);
  const [stopReport, setStopReport] = useState(null);
  const [resetReport, setResetReport] = useState(null);

  /**
   * The strategy a `start_paper` affordance elsewhere in the app pointed at.
   *
   * `Strategies.jsx`'s `ACTION_CATALOG.start_paper` navigates to
   * `/app/paper-trading?listing_id=…` for a subscribed entry and `?strategy_id=…` for an owned
   * one. Read here so that link resolves, and so an entry the server has marked non-entitling
   * puts this page in its `expired-subscription` / `unavailable-strategy` state on arrival rather
   * than only after a refused start.
   */
  const [searchParams] = useSearchParams();
  const requestedListingId = (searchParams.get('listing_id') || '').trim();
  const requestedStrategyId = (searchParams.get('strategy_id') || '').trim();

  const [entryId, setEntryId] = useState('');
  const [symbol, setSymbol] = useState('');
  const [timeframe, setTimeframe] = useState(TIMEFRAMES[0]);
  const [currency, setCurrency] = useState(DEFAULT_CURRENCY);
  const [capitalText, setCapitalText] = useState('100000.00');
  const [capitalError, setCapitalError] = useState(null);

  // ── task 32.4: the retained live state, and what the socket reports about itself ──

  /**
   * Every Paper_Channel frame this page has applied for the selected session, bounded.
   *
   * One store for both transports. `retainFrames` is the only writer, so Requirement 27.5's
   * bounds and Requirement 19.8's `event_id` deduplication are enforced in one place regardless
   * of whether a frame arrived over the socket or through `GET /sessions/{id}/events`.
   */
  const [retained, setRetained] = useState(EMPTY_RETAINED);

  /** `websocketClient`'s current status, watched rather than polled (Requirement 20.5). */
  const [socketStatus, setSocketStatus] = useState(() => websocketClient.getStatus());

  /** The `subscription_refused` frame, when the server refused this session's channel. */
  const [channelRefusal, setChannelRefusal] = useState(null);

  /** The reconnect gap-close's own failure, kept so it is reported rather than swallowed. */
  const [gapCloseError, setGapCloseError] = useState(null);

  /** How many times the gap after a reconnect has been closed. Disclosed, not inferred. */
  const [gapCloseCount, setGapCloseCount] = useState(0);

  /**
   * The classified refusal the last start attempt carried, or `null`.
   *
   * `POST /sessions` re-resolves entitlement at the admission point, which is the moment the
   * `my-strategies` list this page was built from can have gone stale. A `PAPER_START_REFUSED`
   * naming `ENTITLEMENT` or `STRATEGY_NOT_EXECUTABLE` is therefore the authoritative answer, and
   * it is kept so the selection panel renders the matching state rather than only flashing a
   * toast that scrolls away.
   */
  const [startRefusal, setStartRefusal] = useState(null);

  // Guards a resolved promise from writing into an unmounted tree, and the flag the socket
  // handlers below check before touching state.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  /**
   * The retained state as of this render, for the handlers that must read it without becoming a
   * dependency of the effect that installs them.
   *
   * `onFrame` needs the bounded `Set` and the reconnect needs `lastSequence`; making either a
   * dependency of THE SINGLE SESSION EFFECT would tear the subscription down and rebuild it on
   * every frame — which is exactly the churn Requirement 20.8's single teardown is about.
   */
  const retainedRef = useRef(EMPTY_RETAINED);
  retainedRef.current = retained;

  const socketStatusRef = useRef(socketStatus);
  socketStatusRef.current = socketStatus;

  /**
   * The `session_state` the server last reported, for the safety poll's gate.
   *
   * Assigned further down, once the session read has been unwrapped. A ref rather than a
   * dependency for the same reason as the two above: the poll must not be recreated — and its
   * `clearInterval` re-registered — every time a state label changes.
   */
  const sessionStateRef = useRef('');

  const toast = useCallback((type, message) => {
    if (typeof window !== 'undefined' && typeof window.showToast === 'function') {
      window.showToast(type, message);
    }
  }, []);

  // ── reads ──────────────────────────────────────────────────────────────

  const loadStrategies = useCallback(async () => {
    setStrategies({ status: 'loading', data: null, error: null });
    try {
      const data = await api.library.myStrategies();
      if (!mounted.current) return;
      setStrategies({ status: 'ready', data, error: null, failure: null });
    } catch (err) {
      if (!mounted.current) return;
      setStrategies({
        status: 'error',
        data: null,
        error: errorLine(err, 'strategies'),
        failure: classifyReadFailure(err),
      });
    }
  }, []);

  const loadSessionList = useCallback(async () => {
    setSessionList({ status: 'loading', data: null, error: null });
    try {
      const data = await api.paper.sessions.list();
      if (!mounted.current) return;
      setSessionList({ status: 'ready', data, error: null, failure: null });
    } catch (err) {
      if (!mounted.current) return;
      setSessionList({
        status: 'error',
        data: null,
        error: errorLine(err, 'paper-trading'),
        failure: classifyReadFailure(err),
      });
    }
  }, []);

  /** Run one session-scoped read. A read that did not complete is recorded as an error, never as
   *  an empty collection — "you have no orders" is a claim, and a failed statement did not make
   *  it. */
  const runRead = useCallback(async (key, id) => {
    if (!id) return;
    setReads((prev) => ({ ...prev, [key]: { ...prev[key], status: 'loading', error: null } }));
    try {
      const data = await READS[key](id);
      if (!mounted.current) return;
      setReads((prev) => ({ ...prev, [key]: { status: 'ready', data, error: null, failure: null } }));
    } catch (err) {
      if (!mounted.current) return;
      setReads((prev) => ({
        ...prev,
        [key]: {
          status: 'error',
          data: null,
          error: errorLine(err, 'paper-trading'),
          failure: classifyReadFailure(err),
        },
      }));
    }
  }, []);

  const refreshSession = useCallback(
    (id, keys = READ_KEYS) => Promise.all(keys.map((key) => runRead(key, id))),
    [runRead],
  );

  useEffect(() => {
    loadStrategies();
    loadSessionList();
  }, [loadStrategies, loadSessionList]);

  // Held in refs so the socket effect below depends on `sessionId` alone. A callback identity
  // change must not be able to unsubscribe and resubscribe a live channel.
  const refreshSessionRef = useRef(refreshSession);
  refreshSessionRef.current = refreshSession;

  /**
   * The reconnecting indicator's source, and the ONE listener this page keeps for the whole of
   * its life rather than per session.
   *
   * `onStatusChange(listener)` returns its own unsubscribe function — that return value IS the
   * cleanup, so it is returned directly and there is no second bookkeeping to get wrong. The
   * status is also re-read on mount, because a socket that was already `connected` when this page
   * mounted will emit no transition to tell it so.
   */
  useEffect(() => {
    setSocketStatus(websocketClient.getStatus());
    const stopWatchingStatus = websocketClient.onStatusChange((status) => {
      if (!mounted.current) return;
      setSocketStatus(status);
    });
    return stopWatchingStatus;
  }, []);

  /**
   * One Paper_Channel frame.
   *
   * Three things happen here and nothing else: a refusal is recorded (so the page can say "this
   * is not yours" rather than looking quiet), a frame whose `event_id` is already in the bounded
   * `Set` is discarded, and anything else is merged through `retainFrames` — the same reduction
   * the REST replay goes through, in the same order, under the same bounds.
   *
   * The `Set` test is a cheap pre-check that avoids a pointless state update; `retainFrames`
   * deduplicates again over the merged list, so a duplicate cannot survive even if this check
   * were removed.
   */
  const onFrame = useCallback((frame) => {
    if (!mounted.current || !frame || typeof frame !== 'object') return;

    if (frame.type === SUBSCRIPTION_REFUSED_TYPE) {
      setChannelRefusal(frame);
      return;
    }

    const key = frame.event_id === undefined || frame.event_id === null ? null : String(frame.event_id);
    if (key !== null && retainedRef.current.eventIds.has(key)) return;

    setRetained((prev) => retainFrames(prev.events, prev.ticks, [frame]));
  }, []);

  /**
   * Close the gap a disconnection left, from the last sequence this page applied.
   *
   * `GET /sessions/{id}/events?since_sequence=` is the REST half of `paper_channel.replay`, so
   * the frames it returns are the frames the socket would have delivered. `lastSequence` comes
   * from `retainFrames`, which takes it from the highest sequence SEEN — including a frame the
   * retention bound has since discarded, because asking below that would re-request history this
   * page already applied and then dropped.
   *
   * `history_incomplete: true` is Requirement 19.9's answer: the buffer no longer holds the
   * requested position, so a partial history would be a silently incomplete stream. The retained
   * state is cleared and every read re-issued from the API instead — the reload the server asked
   * for, rather than a stitched-together stream.
   */
  const closeGap = useCallback(
    async (reason) => {
      if (!sessionId || !mounted.current) return;
      const since = retainedRef.current.lastSequence;
      try {
        const data = await api.paper.sessions.events(sessionId, since);
        if (!mounted.current) return;
        setGapCloseError(null);

        if (data?.history_incomplete === true) {
          setRetained(EMPTY_RETAINED);
          toast(
            'error',
            'The event history for this session is incomplete, so session state is being reloaded from the API rather than stitched together.',
          );
          refreshSessionRef.current(sessionId);
          return;
        }

        const frames = Array.isArray(data?.events) ? data.events : [];
        setRetained((prev) => retainFrames(prev.events, prev.ticks, frames));
        setGapCloseCount((count) => count + 1);
        // A reconnect is also a gap in the persisted figures, so the six non-event reads are
        // re-issued. `events` is deliberately not among them: this call IS the events read, and
        // re-running it from sequence 0 would replay a log the gap-close has already applied.
        refreshSessionRef.current(sessionId, ['session', 'equity', 'metrics', 'positions', 'orders', 'trades']);
      } catch (err) {
        if (!mounted.current) return;
        setGapCloseError({
          reason,
          message: errorLine(err, 'paper-trading'),
          failure: classifyReadFailure(err),
        });
      }
    },
    [sessionId, toast],
  );

  /**
   * THE SINGLE SESSION EFFECT — the seven reads, the subscription, the poll and one teardown.
   *
   * Everything this route creates for a session is created here and disposed of here. The full
   * list, and there is no other:
   *
   * 1. `release()` — `subscribeChannel`'s ref-counted releaser for `paper.{sessionId}`. The
   *    server is told to unsubscribe when the last hold on that channel goes.
   * 2. `stopWatchingOpen()` — `onOpen`'s own unsubscribe function.
   * 3. `clearInterval(poll)` — the safety poll.
   * 4. `websocketClient.release()` — this page's hold on the one connection, taken by `acquire`.
   *    The socket closes only when the last hold across the app goes.
   *
   * (The status listener is the one thing NOT held here: it belongs to the page rather than to a
   * session, so it lives in its own effect above with its own cleanup.)
   *
   * The setup runs inside a `try` whose `catch` disposes and re-raises. Without it, a throw
   * between two of the four creations would leave React with no cleanup to call and the earlier
   * creations orphaned — an interval and a subscription surviving the route, which is precisely
   * what Requirement 20.8 forbids.
   */
  useEffect(() => {
    if (!sessionId) {
      setReads(initialReads());
      setRetained(EMPTY_RETAINED);
      setChannelRefusal(null);
      setGapCloseError(null);
      setGapCloseCount(0);
      return undefined;
    }

    // A new session starts from nothing retained: carrying the previous session's frames over
    // would put another session's events in this one's log.
    setRetained(EMPTY_RETAINED);
    setChannelRefusal(null);
    setGapCloseError(null);
    setGapCloseCount(0);

    let release = null;
    let stopWatchingOpen = null;
    let poll = null;
    let held = false;

    const teardown = () => {
      if (release) {
        release();
        release = null;
      }
      if (stopWatchingOpen) {
        stopWatchingOpen();
        stopWatchingOpen = null;
      }
      if (poll !== null) {
        clearInterval(poll);
        poll = null;
      }
      if (held) {
        websocketClient.release();
        held = false;
      }
    };

    try {
      refreshSession(sessionId);

      websocketClient.acquire(PAPER_SOCKET_PATH, PAPER_RECONNECT_POLICY);
      held = true;

      release = websocketClient.subscribeChannel(paperChannelName(sessionId), onFrame);

      // Requirement 19.8's reconnect half. `websocketClient` has already resubscribed the channel
      // from its own registry by the time this fires — the server clears a connection's
      // authorised subscriptions when it closes — so what is left is asking for the frames the
      // drop swallowed.
      stopWatchingOpen = websocketClient.onOpen(() => {
        // A refusal is answered per connection: `websocketClient.handleOpen` clears its own
        // refusal registry and resubscribes, because a token refresh or an applied migration can
        // legitimately change the answer. Holding the previous refusal on screen would report a
        // subscription as refused after the server had accepted it.
        setChannelRefusal(null);
        closeGap('reconnect');
      });

      poll = setInterval(() => {
        // Only while the socket is not carrying frames, and only while the session could still be
        // producing them. A connected session pays for no extra round trip; a `CREATED` or
        // `STOPPED` one has nothing new to report, so re-reading it would be a request whose
        // answer is known.
        if (socketStatusRef.current === CONNECTED_SOCKET_STATUS) return;
        if (!POLLED_SESSION_STATES.includes(sessionStateRef.current)) return;
        refreshSessionRef.current(sessionId, ['session', 'metrics']);
      }, SAFETY_POLL_INTERVAL_MS);
    } catch (error) {
      teardown();
      throw error;
    }

    return teardown;
  }, [sessionId, refreshSession, onFrame, closeGap]);

  /**
   * The REST replay's frames, merged into the one retained store.
   *
   * `GET /sessions/{id}/events` and the socket serve the same frames from the same
   * `paper_channel.replay`, so they are applied through one path and deduplicated on `event_id`
   * rather than kept as two lists a reader would have to reconcile.
   */
  useEffect(() => {
    const frames = reads.events.data?.events;
    if (!Array.isArray(frames) || frames.length === 0) return;
    setRetained((prev) => retainFrames(prev.events, prev.ticks, frames));
  }, [reads.events.data]);

  // ── the strategy entries this page may start ───────────────────────────

  /**
   * The entries whose server-computed `allowed_actions` include `start_paper`.
   *
   * Read **as returned**. The page offers no entry the server would refuse, and infers neither
   * the `ownership` label nor the affordance list for itself — which is also what lets a
   * `SUBSCRIBED` entry be started without the subscriber ever holding the definition: the entry
   * carries a `listing_id` and no strategy definition, and the executable artifact is resolved
   * server-side from that.
   */
  const startableEntries = useMemo(() => {
    const items = strategies.data?.items;
    if (!Array.isArray(items)) return [];
    return items.filter(
      (entry) => Array.isArray(entry?.allowed_actions) && entry.allowed_actions.includes(ACTION_START_PAPER),
    );
  }, [strategies.data]);

  const selectedEntry = useMemo(
    () => startableEntries.find((entry) => entry.entry_id === entryId) || null,
    [startableEntries, entryId],
  );

  /**
   * The entry a `start_paper` link from `Strategies.jsx` named, found in the SAME response the
   * selector is built from.
   *
   * The entry is looked up in `my-strategies`' own items — not fetched separately — because
   * `entitling` and `unavailable_reason` are that response's fields, and re-deriving them here
   * would be a second entitlement decision the browser is in no position to make.
   */
  const requestedEntry = useMemo(() => {
    const items = strategies.data?.items;
    if (!Array.isArray(items)) return null;
    if (requestedListingId) {
      return items.find((entry) => String(entry?.listing_id ?? '') === requestedListingId) || null;
    }
    if (requestedStrategyId) {
      return items.find((entry) => String(entry?.strategy_id ?? '') === requestedStrategyId) || null;
    }
    return null;
  }, [strategies.data, requestedListingId, requestedStrategyId]);

  /**
   * `expired-subscription` or `unavailable-strategy` for that entry, from the server's own
   * `entitling` flag and `unavailable_reason` code — or `null` when it entitles.
   *
   * Exactly the two fields, read exactly the way `Strategies.jsx` reads them, so the two pages
   * and the refusal that follows name one condition.
   */
  const requestedEntryState = useMemo(() => entitlementPanelState(requestedEntry), [requestedEntry]);

  const onSelectEntry = useCallback(
    (nextEntryId) => {
      setEntryId(nextEntryId);
      const entry = startableEntries.find((item) => item.entry_id === nextEntryId);
      if (!entry) return;
      // Prefilled from the entry, and editable: these are execution parameters, not properties of
      // the strategy the server resolves.
      const entrySymbol = entry.ownership === 'SUBSCRIBED' ? entry.listing?.symbol : entry.symbol;
      if (entrySymbol) setSymbol(String(entrySymbol));
      const supported = entry.ownership === 'SUBSCRIBED' ? entry.listing?.supported_timeframes : null;
      const entryTimeframe = entry.ownership === 'SUBSCRIBED'
        ? (Array.isArray(supported) && supported.length ? supported[0] : null)
        : entry.timeframe;
      if (entryTimeframe && TIMEFRAMES.includes(String(entryTimeframe))) {
        setTimeframe(String(entryTimeframe));
      }
      const entryCurrency = entry.ownership === 'SUBSCRIBED' ? entry.listing?.currency : null;
      if (entryCurrency && SUPPORTED_CURRENCIES.includes(String(entryCurrency).toUpperCase())) {
        setCurrency(String(entryCurrency).toUpperCase());
      }
      // The previous entry's refusal is not this entry's state.
      setStartRefusal(null);
    },
    [startableEntries],
  );

  // Held in a ref so the effect below depends on the requested entry rather than on a callback
  // identity that changes whenever the entry list is refetched.
  const onSelectEntryRef = useRef(onSelectEntry);
  onSelectEntryRef.current = onSelectEntry;

  /**
   * Select the entry the inbound link named, once, and only when the server offers `start_paper`
   * for it.
   *
   * A non-entitling entry is deliberately NOT selected: the selection panel is in its
   * `expired-subscription` / `unavailable-strategy` state for it, and selecting it would offer a
   * Start button for a strategy the server has already said it will refuse.
   */
  useEffect(() => {
    if (!requestedEntry || entryId) return;
    if (!startableEntries.some((entry) => entry.entry_id === requestedEntry.entry_id)) return;
    onSelectEntryRef.current(requestedEntry.entry_id);
  }, [requestedEntry, entryId, startableEntries]);

  const onCapitalChange = useCallback(
    (next) => {
      setCapitalText(next);
      const parsed = parseCapitalToMinor(next, currency);
      setCapitalError(parsed.ok ? null : parsed.message);
    },
    [currency],
  );

  const onCurrencyChange = useCallback(
    (next) => {
      setCurrency(next);
      const parsed = parseCapitalToMinor(capitalText, next);
      setCapitalError(parsed.ok ? null : parsed.message);
    },
    [capitalText],
  );

  // ── the five operations ────────────────────────────────────────────────

  const handleStart = useCallback(async () => {
    if (!selectedEntry) {
      toast('error', 'Select a strategy to run.');
      return;
    }
    const trimmedSymbol = symbol.trim();
    if (!trimmedSymbol) {
      toast('error', 'Enter the market to run on, for example BTC/USDT.');
      return;
    }
    // The conversion, refused rather than rounded. `initial_capital_minor` is an exact whole
    // number of Minor_Units on the wire, so a value that is not one never leaves this page.
    const parsed = parseCapitalToMinor(capitalText, currency);
    if (!parsed.ok) {
      setCapitalError(parsed.message);
      toast('error', parsed.message);
      return;
    }
    setCapitalError(null);

    // Exactly one of `listing_id` / `strategy_id`, and no strategy definition, plan or version:
    // the executable artifact is resolved server-side and never travels in either direction.
    const body = {
      symbol: trimmedSymbol,
      timeframe,
      initial_capital_minor: parsed.minor,
      currency,
    };
    if (selectedEntry.ownership === 'SUBSCRIBED') {
      body.listing_id = selectedEntry.listing_id;
    } else {
      body.strategy_id = selectedEntry.strategy_id;
    }

    setBusy('start');
    try {
      const result = await api.paper.sessions.create(body);
      if (!mounted.current) return;
      setStopReport(null);
      setResetReport(null);
      setStartRefusal(null);
      const startedId = result?.session_id || result?.session?.id || null;
      if (startedId) setSessionId(startedId);
      toast('strategy', `Simulated session started on ${trimmedSymbol} ${timeframe}.`);
      loadSessionList();
    } catch (err) {
      if (!mounted.current) return;
      // The refusal is CLASSIFIED and kept, so the selection panel renders the state the server
      // named — an expired subscription, an unresolvable strategy, or a plain failure with a
      // retry — instead of the page losing the reason to a toast that scrolls away.
      const failure = classifyReadFailure(err);
      setStartRefusal({
        ...failure,
        message: failure.message || errorLine(err, 'paper-trading'),
      });
      toast('error', errorLine(err, 'paper-trading'));
    } finally {
      if (mounted.current) setBusy(null);
    }
  }, [selectedEntry, symbol, timeframe, currency, capitalText, toast, loadSessionList]);

  const handlePause = useCallback(async () => {
    if (!sessionId) return;
    setBusy('pause');
    try {
      const result = await api.paper.sessions.pause(sessionId);
      if (!mounted.current) return;
      toast('info', `Session ${String(result?.status ?? result?.session_state ?? 'paused').toUpperCase()}. The market-data subscription stays open.`);
      await refreshSession(sessionId, ['session', 'events']);
      loadSessionList();
    } catch (err) {
      if (!mounted.current) return;
      toast('error', errorLine(err, 'paper-trading'));
    } finally {
      if (mounted.current) setBusy(null);
    }
  }, [sessionId, toast, refreshSession, loadSessionList]);

  const handleResume = useCallback(async () => {
    if (!sessionId) return;
    setBusy('resume');
    try {
      const result = await api.paper.sessions.resume(sessionId);
      if (!mounted.current) return;
      toast('info', `Session ${String(result?.status ?? result?.session_state ?? 'resumed').toUpperCase()}. Candles that closed while it was paused are not back-filled.`);
      await refreshSession(sessionId, ['session', 'events']);
      loadSessionList();
    } catch (err) {
      if (!mounted.current) return;
      toast('error', errorLine(err, 'paper-trading'));
    } finally {
      if (mounted.current) setBusy(null);
    }
  }, [sessionId, toast, refreshSession, loadSessionList]);

  /**
   * Stop, and report what the stop actually did.
   *
   * `POST /sessions/{id}/stop` answers **200** with `complete: false` and a populated
   * `outstanding` when the transition committed but a release step did not. That is a reported
   * state, not a failure — and it is not proof the stop finished either. `complete` is read off
   * the body; nothing here infers success from the call returning.
   */
  const handleStop = useCallback(async () => {
    if (!sessionId) return;
    setBusy('stop');
    try {
      const result = await api.paper.sessions.stop(sessionId);
      if (!mounted.current) return;
      setStopReport(result || null);
      const outstanding = Array.isArray(result?.outstanding) ? result.outstanding : [];
      if (result?.complete === true) {
        toast('info', 'Session STOPPED and the stop completed.');
      } else {
        toast(
          'error',
          `Session STOPPED, but the stop is not complete. Outstanding: ${outstanding.length ? outstanding.join(', ') : 'not itemised by the server'}.`,
        );
      }
      await refreshSession(sessionId);
      loadSessionList();
    } catch (err) {
      if (!mounted.current) return;
      toast('error', errorLine(err, 'paper-trading'));
    } finally {
      if (mounted.current) setBusy(null);
    }
  }, [sessionId, toast, refreshSession, loadSessionList]);

  const handleReset = useCallback(async () => {
    if (!sessionId) return;
    setBusy('reset');
    try {
      const result = await api.paper.sessions.reset(sessionId);
      if (!mounted.current) return;
      setResetReport(result || null);
      setStopReport(null);
      toast('info', 'Session reset to CREATED. Nothing was deleted — the pre-reset history stays readable.');
      await refreshSession(sessionId);
      loadSessionList();
    } catch (err) {
      if (!mounted.current) return;
      toast('error', errorLine(err, 'paper-trading'));
    } finally {
      if (mounted.current) setBusy(null);
    }
  }, [sessionId, toast, refreshSession, loadSessionList]);

  // ── derived views ──────────────────────────────────────────────────────

  const session = reads.session.data?.session ?? null;
  const sessionState = String(session?.session_state ?? '').toUpperCase();
  sessionStateRef.current = sessionState;
  const feedState = String(session?.feed_state ?? '').toUpperCase();
  const sessionCurrency = session?.currency || currency;

  const feedCopy = FEED_STATE_COPY[feedState] || null;
  const sessionCopy = SESSION_STATE_COPY[sessionState] || null;
  const feedTradeable = TRADEABLE_FEED_STATES.includes(feedState);

  /**
   * The equity curve. Its ONLY input is the equity read.
   *
   * This is Requirement 18.11 made structural: `equityCurvePoints` is called with
   * `reads.equity.data.equity` and with nothing else in scope, so no accumulated event state can
   * reach the curve and a remount redraws the identical series.
   */
  const equityView = useMemo(
    () => equityCurvePoints(reads.equity.data?.equity),
    [reads.equity.data],
  );

  /**
   * The equity curve's plotted points, bounded to {@link MAX_CHART_POINTS_PER_SERIES}.
   *
   * The bound is applied to the PLOT and not to `equityView.latest`, which is where the current
   * equity and cash figures come from: dropping the oldest snapshots cannot move the newest one.
   * `equityView.seriesBoundaries` index into the unbounded points, so they are re-based here
   * rather than being drawn at the wrong x positions.
   */
  const equityPoints = useMemo(
    () => boundedTail(equityView.points, MAX_CHART_POINTS_PER_SERIES),
    [equityView.points],
  );
  const equityPointsDropped = equityView.points.length - equityPoints.length;
  const equityBoundaries = useMemo(
    () =>
      equityView.seriesBoundaries
        .map((boundary) => boundary - equityPointsDropped)
        .filter((boundary) => boundary > 0 && boundary < equityPoints.length),
    [equityView.seriesBoundaries, equityPointsDropped, equityPoints.length],
  );

  /**
   * Everything the retained frames carry, deduplicated on `event_id`, in `sequence` order.
   *
   * `retained.frames` is the bounded store both transports write through, so this is one
   * reduction over one list — the REST replay and the socket cannot produce different views of
   * the same session.
   */
  const derived = useMemo(() => deriveFromFrames(retained.frames), [retained.frames]);

  const metricsRow = reads.metrics.data?.metrics ?? null;
  /** Stated by the server, not inferred from `metrics` being null. */
  const metricsComputed = reads.metrics.data?.computed === true;

  const positions = useMemo(
    () => (Array.isArray(reads.positions.data?.positions) ? reads.positions.data.positions : []),
    [reads.positions.data],
  );
  const allOrders = useMemo(
    () => (Array.isArray(reads.orders.data?.orders) ? reads.orders.data.orders : []),
    [reads.orders.data],
  );
  const openOrders = useMemo(() => allOrders.filter(isOpenOrder), [allOrders]);
  const trades = useMemo(
    () => (Array.isArray(reads.trades.data?.trades) ? reads.trades.data.trades : []),
    [reads.trades.data],
  );

  /** The latest snapshot of the persisted equity series — the current equity and cash figures. */
  const latestSnapshot = equityView.latest;
  const equityStale = latestSnapshot?.stale === true;

  /**
   * The most recent validated price instant this page was told about.
   *
   * `paper_positions.price_at`, and the `price_at` a `paper_pnl_updated` or
   * `paper_position_updated` frame carried. `null` when none was reported, which the stale note
   * says rather than papering over.
   */
  const lastPriceAt = useMemo(() => {
    const candidates = [derived.lastPriceAt, ...positions.map((p) => p?.price_at)].filter(Boolean);
    if (!candidates.length) return null;
    return candidates.reduce((latest, value) => {
      const a = new Date(latest).getTime();
      const b = new Date(value).getTime();
      if (Number.isNaN(b)) return latest;
      if (Number.isNaN(a)) return value;
      return b > a ? value : latest;
    });
  }, [derived.lastPriceAt, positions]);

  const latestTick = derived.latestTick;
  // There is no `priceIsStale` flag here any more, and its absence is deliberate: task 32.3 gave
  // the current-price panel a `feed-disconnected` STATE for exactly the condition that flag
  // described (`feed_state` not in `TRADEABLE_FEED_STATES`). Keeping both would leave a branch
  // that could never be reached — the state supersedes the note before the note is consulted —
  // and an unreachable branch is what Requirement 28.6 forbids. The equity and PnL figures keep
  // their own `stale` note, because that flag comes from the SNAPSHOT the server sent rather than
  // from the feed's state and is a different statement.

  /**
   * The price series, with each fill placed on the last tick at or before it.
   *
   * `derived.ticks` is already bounded to {@link MAX_RETAINED_TICKS} by `retainFrames`, and the
   * plot is bounded again to {@link MAX_CHART_POINTS_PER_SERIES} so the per-series bound holds
   * however the retention bounds are later tuned. The fills are placed AFTER the bound, so a
   * marker is never attached to a candle that is no longer plotted.
   */
  const priceChart = useMemo(() => {
    const rows = boundedTail(derived.ticks, MAX_CHART_POINTS_PER_SERIES).map((tick, index) => ({
      index,
      at: tick.at,
      label: formatClock(tick.at),
      closeText: tick.close,
      close: toChartNumber(tick.close),
      latencyMs: tick.latencyMs,
      feedState: tick.feedState,
    }));
    const times = rows.map((row) => new Date(row.at || 0).getTime());
    for (const fill of derived.fills) {
      const price = toChartNumber(fill.avgFillPrice);
      if (price === null || !rows.length) continue;
      const at = new Date(fill.at || 0).getTime();
      let target = 0;
      for (let i = 0; i < times.length; i += 1) {
        if (!Number.isNaN(times[i]) && !Number.isNaN(at) && times[i] <= at) target = i;
        else if (!Number.isNaN(times[i]) && !Number.isNaN(at)) break;
      }
      const sell = String(fill.side ?? '').toUpperCase() === 'SELL';
      rows[target][sell ? 'sellFill' : 'buyFill'] = price;
      rows[target][sell ? 'sellFillText' : 'buyFillText'] = fill.avgFillPrice;
    }
    return rows;
  }, [derived.ticks, derived.fills]);

  const pnlChart = useMemo(
    () =>
      boundedTail(derived.pnlPoints, MAX_CHART_POINTS_PER_SERIES).map((point, index) => ({
        index,
        label: formatClock(point.emittedAt),
        realizedText: point.realized,
        unrealizedText: point.unrealized,
        totalText: point.total,
        stale: point.stale,
        realized: toChartNumber(point.realized),
        unrealized: toChartNumber(point.unrealized),
        total: toChartNumber(point.total),
      })),
    [derived.pnlPoints],
  );

  const drawdownChart = useMemo(
    () =>
      boundedTail(derived.drawdownPoints, MAX_CHART_POINTS_PER_SERIES).map((point, index) => ({
        index,
        label: formatClock(point.emittedAt),
        amountText: point.amount,
        fractionText: point.fraction,
        peakText: point.peakEquity,
        snapshotCount: point.snapshotCount,
        amount: toChartNumber(point.amount),
      })),
    [derived.drawdownPoints],
  );

  const chartHeight = uiMode === 'lite' ? 180 : 220;

  // ── task 32.5: the five tables' columns, defined once for both layouts ──
  //
  // A column carries its header, how its cell renders and the per-row style that cell takes. Both
  // the `<table>` and the stacked `<dl>` are rendered from these arrays by {@link DataTable}, so
  // the two layouts cannot disagree about which cells exist, what they contain or how a figure is
  // formatted — and the header a value is announced with in the stacked layout is the header it sat
  // under in the wide one. Nothing here changes a figure or its source: every `render` below is the
  // cell expression the wide table already had.

  const positionColumns = useMemo(
    () => [
      { key: 'symbol', header: 'Symbol', render: (row) => row.symbol ?? '—' },
      {
        key: 'side',
        header: 'Side',
        render: (row) => (
          <StatusPill
            tone={String(row.side).toUpperCase() === 'SHORT' ? 'bad' : 'good'}
            Icon={String(row.side).toUpperCase() === 'SHORT' ? ArrowDownRight : ArrowUpRight}
            label={row.side ?? NOT_REPORTED}
          />
        ),
      },
      { key: 'size', header: 'Size', render: (row) => formatQuantity(row.size) ?? NOT_REPORTED },
      { key: 'entry', header: 'Entry', render: (row) => formatPrice(row.entry_price) ?? NOT_REPORTED },
      { key: 'current', header: 'Current', render: (row) => formatPrice(row.current_price) ?? NOT_REPORTED },
      {
        key: 'unrealized',
        header: 'Unrealized PnL',
        render: (row) => formatMoneyDecimal(row.unrealized_pnl, sessionCurrency) ?? NOT_COMPUTED,
        cellStyle: (row) => ({ color: String(row.unrealized_pnl ?? '').startsWith('-') ? C.loss : C.t1 }),
      },
      {
        key: 'pricedAt',
        header: 'Priced at',
        render: (row) => formatInstant(row.price_at) ?? NOT_REPORTED,
        cellStyle: () => ({ color: C.t2 }),
      },
    ],
    [sessionCurrency],
  );

  const orderColumns = useMemo(
    () => [
      { key: 'symbol', header: 'Symbol', render: (row) => row.symbol ?? '—' },
      { key: 'side', header: 'Side', render: (row) => row.side ?? NOT_REPORTED },
      { key: 'type', header: 'Type', render: (row) => row.order_type ?? NOT_REPORTED },
      {
        key: 'state',
        header: 'State',
        render: (row) => <StatusPill tone="muted" label={row.order_state ?? NOT_REPORTED} />,
      },
      { key: 'quantity', header: 'Quantity', render: (row) => formatQuantity(row.quantity) ?? NOT_REPORTED },
      { key: 'filled', header: 'Filled', render: (row) => formatQuantity(row.filled_quantity) ?? NOT_REPORTED },
      { key: 'limit', header: 'Limit', render: (row) => formatPrice(row.limit_price) ?? '—' },
      { key: 'avgFill', header: 'Avg fill', render: (row) => formatPrice(row.avg_fill_price) ?? '—' },
      { key: 'fee', header: 'Fee', render: (row) => formatMinorUnits(row.fee_minor, sessionCurrency) ?? NOT_REPORTED },
      {
        key: 'signal',
        header: 'Signal',
        render: (row) => row.signal_id ?? '—',
        cellStyle: () => ({ color: C.t2 }),
      },
    ],
    [sessionCurrency],
  );

  const tradeColumns = useMemo(
    () => [
      { key: 'symbol', header: 'Symbol', render: (row) => row.symbol ?? '—' },
      { key: 'side', header: 'Side', render: (row) => row.side ?? NOT_REPORTED },
      { key: 'quantity', header: 'Quantity', render: (row) => formatQuantity(row.quantity) ?? NOT_REPORTED },
      { key: 'entry', header: 'Entry', render: (row) => formatPrice(row.entry_price) ?? NOT_REPORTED },
      { key: 'exit', header: 'Exit', render: (row) => formatPrice(row.exit_price) ?? NOT_REPORTED },
      {
        key: 'realized',
        header: 'Realized PnL',
        render: (row) => {
          const negative = String(row.realized_pnl ?? '').trim().startsWith('-');
          return (
            <>
              {negative ? '' : '+'}
              {formatMoneyDecimal(row.realized_pnl, sessionCurrency) ?? NOT_REPORTED}
            </>
          );
        },
        cellStyle: (row) => ({
          color: String(row.realized_pnl ?? '').trim().startsWith('-') ? C.loss : C.profit,
        }),
      },
      { key: 'fee', header: 'Fee', render: (row) => formatMinorUnits(row.fee_minor, sessionCurrency) ?? NOT_REPORTED },
      {
        key: 'closedAt',
        header: 'Closed at',
        render: (row) => formatInstant(row.closed_at) ?? NOT_REPORTED,
        cellStyle: () => ({ color: C.t2 }),
      },
    ],
    [sessionCurrency],
  );

  const signalColumns = useMemo(
    () => [
      {
        key: 'at',
        header: 'At',
        render: (signal) => formatInstant(signal.generatedAt ?? signal.emittedAt) ?? NOT_REPORTED,
        cellStyle: () => ({ color: C.t2 }),
      },
      { key: 'decision', header: 'Decision', render: (signal) => signal.decision ?? NOT_REPORTED },
      { key: 'symbol', header: 'Symbol', render: (signal) => signal.symbol ?? '—' },
      { key: 'side', header: 'Side', render: (signal) => signal.side ?? NOT_REPORTED },
      { key: 'quantity', header: 'Quantity', render: (signal) => formatQuantity(signal.quantity) ?? NOT_REPORTED },
      { key: 'price', header: 'Price', render: (signal) => formatPrice(signal.price) ?? NOT_REPORTED },
      {
        key: 'lifecycle',
        header: 'Lifecycle',
        render: (signal) => <StatusPill tone="muted" label={signal.lifecycleState ?? NOT_REPORTED} />,
      },
    ],
    [],
  );

  const executionColumns = useMemo(
    () =>
      [
        // The density switch decides whether the sequence column exists at all, exactly as before:
        // it is envelope detail, not one of Requirement 20.4's displays.
        uiMode === 'pro'
          ? {
              key: 'seq',
              header: 'Seq',
              render: (event) => formatCount(event.sequence),
              cellStyle: () => ({ color: C.t3 }),
            }
          : null,
        {
          key: 'at',
          header: 'At',
          render: (event) => formatInstant(event.at ?? event.emittedAt) ?? NOT_REPORTED,
          cellStyle: () => ({ color: C.t2 }),
        },
        {
          key: 'event',
          header: 'Event',
          render: (event) => (
            <StatusPill
              tone={event.type === 'paper_order_rejected' || event.type === 'paper_error' ? 'bad' : event.type === 'paper_order_filled' ? 'good' : 'muted'}
              Icon={event.type === 'paper_order_rejected' || event.type === 'paper_error' ? XCircle : event.type === 'paper_order_filled' ? CheckCircle2 : Activity}
              label={event.type.replace(/^paper_/, '').replace(/_/g, ' ')}
            />
          ),
        },
        { key: 'symbol', header: 'Symbol', render: (event) => event.symbol ?? '—' },
        { key: 'side', header: 'Side', render: (event) => event.side ?? '—' },
        { key: 'filled', header: 'Filled', render: (event) => formatQuantity(event.filledQuantity) ?? '—' },
        { key: 'avgFill', header: 'Avg fill', render: (event) => formatPrice(event.avgFillPrice) ?? '—' },
        {
          key: 'detail',
          header: 'Detail',
          render: (event) => event.rejectionReason || event.message || event.sessionState || event.orderState || '—',
          cellStyle: () => ({ color: C.t2, whiteSpace: 'normal' }),
        },
      ].filter(Boolean),
    [uiMode],
  );

  /** Newest first, as both streams were already rendered — reversed once instead of per render. */
  const signalRows = useMemo(() => [...derived.signals].reverse(), [derived.signals]);
  const executionRows = useMemo(() => [...derived.executions].reverse(), [derived.executions]);

  /**
   * One read's panel state — one of the eight, or `idle` / `ready`.
   *
   * The failing branch does not decide anything itself: it hands the error to
   * `classifyReadFailure`, which reads the server's own code and status and returns
   * `unauthorised`, `expired-subscription`, `unavailable-strategy` or `error-with-retry`. That
   * function is pure and lives beside the vocabulary it matches, so a panel cannot classify a
   * refusal differently from its neighbour.
   *
   * `empty` is only reachable from a **settled** read. A read that failed never reaches it, so
   * "you have no orders" is never shown for "the orders read did not complete".
   */
  const panelState = useCallback(
    (key, isEmpty) => {
      const record = reads[key];
      if (!sessionId) return PANEL_IDLE;
      if (record.status === 'idle' || record.status === 'loading') return PANEL_STATES.LOADING;
      if (record.status === 'error') return record.failure?.state ?? PANEL_STATES.ERROR;
      return isEmpty ? PANEL_STATES.EMPTY : PANEL_READY;
    },
    [reads, sessionId],
  );

  /**
   * The props `PanelBody` needs to render a failed read's state, from that read's own record.
   *
   * Spread rather than passed one by one, so a panel cannot supply the message of one read and the
   * code of another.
   */
  const failureProps = useCallback(
    (key) => ({
      message: reads[key].failure?.message || reads[key].error,
      code: reads[key].failure?.code ?? null,
    }),
    [reads],
  );

  /** The same pair, spelled as {@link Figure}'s props. */
  const figureFailure = useCallback(
    (key) => ({
      errorMessage: reads[key].failure?.message || reads[key].error,
      errorCode: reads[key].failure?.code ?? null,
    }),
    [reads],
  );

  /**
   * The state of the panels whose figure is only meaningful with a live feed.
   *
   * `feed-disconnected` is derived from one thing: `session.feed_state` is not in
   * `TRADEABLE_FEED_STATES`, which holds `HEALTHY` alone. It takes precedence over `empty` and
   * `ready` and yields to a failed session read, because a read that did not complete reported no
   * feed state at all.
   *
   * The panels it applies to are the ones that would otherwise present a figure as current: the
   * current price, and the feed latency and health. The recorded price series is NOT one of them —
   * those candles are validated measurements that were true when they were recorded, and hiding
   * them would discard real data rather than avoid a fabricated claim.
   */
  const liveFigureState = useCallback(
    (isEmpty) => {
      if (!sessionId) return PANEL_IDLE;
      const record = reads.session;
      if (record.status === 'idle' || record.status === 'loading') return PANEL_STATES.LOADING;
      if (record.status === 'error') return record.failure?.state ?? PANEL_STATES.ERROR;
      if (!feedTradeable) return PANEL_STATES.FEED_DISCONNECTED;
      if (reads.events.status === 'error') {
        return reads.events.failure?.state ?? PANEL_STATES.ERROR;
      }
      if (reads.events.status === 'idle' || reads.events.status === 'loading') {
        return PANEL_STATES.LOADING;
      }
      return isEmpty ? PANEL_STATES.EMPTY : PANEL_READY;
    },
    [sessionId, reads.session, reads.events, feedTradeable],
  );

  // ── the four session operations, and when the session state admits them ──

  /**
   * Which of the four transitions the CURRENT `session_state` admits.
   *
   * `paper_session_state`'s own machine, read off the state the server reported: pause from
   * `RUNNING`, resume from `PAUSED`, stop from either, reset from `STOPPED`. Nothing here is a
   * guess about what the server would accept — `POST /sessions/{id}/pause` on a `CREATED` session
   * answers `PAPER_SESSION_OPERATION_REJECTED` naming both states, and this is the same rule one
   * step earlier so the page does not offer a control the server would refuse.
   */
  const availableOperations = useMemo(() => {
    const available = [];
    if (sessionState === 'RUNNING') available.push('pause');
    if (sessionState === 'PAUSED') available.push('resume');
    if (sessionState === 'RUNNING' || sessionState === 'PAUSED') available.push('stop');
    if (sessionState === 'STOPPED') available.push('reset');
    return available;
  }, [sessionState]);

  /**
   * The session-operations panel's state, including the `disabled` one.
   *
   * `disabled` is derived from a control that is genuinely unavailable: the session state admits
   * none of the four transitions. `CREATED` is that state — a session that has not begun can only
   * be started, and starting creates a new session rather than transitioning this one.
   */
  const operationsState = useMemo(() => {
    if (!sessionId) return PANEL_IDLE;
    if (reads.session.status === 'idle' || reads.session.status === 'loading') {
      return PANEL_STATES.LOADING;
    }
    if (reads.session.status === 'error') {
      return reads.session.failure?.state ?? PANEL_STATES.ERROR;
    }
    return availableOperations.length === 0 ? PANEL_STATES.DISABLED : PANEL_READY;
  }, [sessionId, reads.session, availableOperations]);

  /**
   * The strategy-selection panel's state.
   *
   * The two entitlement states outrank the list's own: an entry the server has said does not
   * entitle is not shown as a selectable option with an error tucked away elsewhere.
   */
  const selectionState = useMemo(() => {
    if (strategies.status === 'loading' || strategies.status === 'idle') return PANEL_STATES.LOADING;
    if (strategies.status === 'error') return strategies.failure?.state ?? PANEL_STATES.ERROR;
    if (startRefusal) return startRefusal.state;
    if (requestedEntryState) return requestedEntryState;
    return startableEntries.length === 0 ? PANEL_STATES.EMPTY : PANEL_READY;
  }, [strategies.status, strategies.failure, startRefusal, requestedEntryState, startableEntries.length]);

  /** The message and code the selection panel's non-ready state carries. */
  const selectionNotice = useMemo(() => {
    if (startRefusal) {
      return { message: startRefusal.message, code: startRefusal.code };
    }
    if (requestedEntryState) {
      return {
        message: entitlementReasonText(requestedEntry?.unavailable_reason),
        code: requestedEntry?.unavailable_reason ?? null,
      };
    }
    return { message: strategies.failure?.message || strategies.error, code: strategies.failure?.code ?? null };
  }, [startRefusal, requestedEntryState, requestedEntry, strategies.failure, strategies.error]);

  // ── the live stream's own state ─────────────────────────────────────────

  const reconnecting = isReconnectingStatus(socketStatus);
  const socketConnected = socketStatus === CONNECTED_SOCKET_STATUS;

  /**
   * The live-stream panel's state.
   *
   * A `subscription_refused` frame carrying `CHANNEL_FORBIDDEN` or `CHANNEL_UNAUTHENTICATED` is
   * the socket's own `unauthorised`; `CHANNEL_OWNER_UNRESOLVED` and `CHANNEL_UNKNOWN` are not
   * answers about the caller, so they are `error-with-retry` instead of being reported as "not
   * yours".
   */
  const liveStreamState = useMemo(() => {
    if (!sessionId) return PANEL_IDLE;
    if (channelRefusal) return channelRefusalPanelState(channelRefusal);
    if (gapCloseError) return gapCloseError.failure?.state ?? PANEL_STATES.ERROR;
    return PANEL_READY;
  }, [sessionId, channelRefusal, gapCloseError]);

  // ── render ─────────────────────────────────────────────────────────────

  const capitalPreview = parseCapitalToMinor(capitalText, currency);

  return (
    <div
      ref={pageRef}
      data-testid="paper-trading-page"
      data-layout-mode={layoutMode}
      data-observed-width={observedWidth === null ? '' : String(Math.round(observedWidth))}
      style={{
        padding: PAGE_PADDING,
        flex: 1,
        background: C.bg0,
        color: C.t1,
        fontFamily: 'monospace',
        // The three page-level guards behind Requirement 20.7. `minWidth: 0` lets this page shrink
        // inside the shell's flex column instead of forcing it wider; `maxWidth: '100%'` keeps it
        // inside the column it was given; `overflowWrap: 'anywhere'` is inherited by every
        // descendant, so an unbreakable 36-character session identifier in a subtitle or a table
        // cell breaks across lines rather than widening the panel that holds it. `anywhere` rather
        // than `break-word` because only `anywhere` also lowers the min-content width that a flex
        // or grid item is sized from — which is the number that decides whether a track overflows.
        minWidth: 0,
        maxWidth: '100%',
        overflowWrap: 'anywhere',
      }}
    >
      {/* ── header ─────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: C.space.md, alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: C.space.lg }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <h1 style={{ fontSize: '1.25rem', fontWeight: 900, color: C.t1, margin: 0, letterSpacing: '-0.02em' }}>
              Paper Trading
            </h1>
          </div>
          <p style={{ fontSize: 11, color: C.t3, margin: '4px 0 0' }}>
            Every figure on this page is produced by the paper simulator against validated market
            data. None of it reaches an exchange.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {/* The reconnecting indicator (Requirement 20.5's last clause). Rendered from
              `websocketClient.onStatusChange`, not from a timer reading `getStatus()`: a
              reconnection that begins and completes between two ticks of a poll would never be
              indicated at all. The word is always present, so the state is legible without
              relying on the colour or the icon. */}
          {reconnecting ? (
            <span
              role="status"
              aria-live="polite"
              data-testid="paper-reconnecting"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                fontSize: 10,
                fontFamily: 'monospace',
                fontWeight: 800,
                letterSpacing: 0.6,
                textTransform: 'uppercase',
                color: C.warning,
                border: `1px solid ${C.warning}55`,
                borderRadius: C.radius.sm,
                padding: '2px 7px',
              }}
            >
              <Spinner size={11} />
              Reconnecting…
            </span>
          ) : (
            <StatusPill
              tone={socketConnected ? 'good' : 'warn'}
              Icon={socketConnected ? Wifi : WifiOff}
              label={socketConnected ? 'Live updates connected' : 'Live updates disconnected'}
              title={`websocketClient status: ${socketStatus}`}
            />
          )}
          <Button
            variant="outline"
            size="xs"
            onClick={() => {
              loadStrategies();
              loadSessionList();
              if (sessionId) refreshSession(sessionId);
            }}
            disabled={busy !== null}
            aria-label="Refresh every reading on this page"
          >
            <RefreshCw size={12} aria-hidden="true" />
            <span style={{ marginLeft: 6 }}>Refresh</span>
          </Button>
        </div>
      </div>

      {/* ── the page's environment statement ───────────────────────────────
          Task 25.1 / Requirement 12.2 / §7.8 (1). This replaces the hand-styled span that used to
          sit beside the `<h1>` reading `Simulated — no live order is ever placed`. That copy is not
          restated here: it IS `ENVIRONMENT.PAPER.long`, so the badge's `strip` variant — §5.1's
          full-width-under-the-header placement, and the only variant that shows the long form as
          text rather than a tooltip — renders the identical sentence, now alongside the `PAPER
          TRADING` label, the `FlaskConical` glyph, the indigo `env.paper` hue and the dashed border
          that are §8.2's other three axes.

          `announce` is set here and nowhere else on the page: this is the first and only
          announcing instance, so a screen reader states the environment once rather than once per
          figure. `environment="PAPER"` is a constant because it is a statement about the page, not
          about a record — every figure below comes from the paper simulator by construction. The
          per-record fields §8.1 talks about are `positions[].environment` and
          `execution_environment`, and nothing here infers either. */}
      <TradingEnvironmentBadge
        environment="PAPER"
        variant="strip"
        announce
        className="mb-4 rounded-sm"
      />

      {/* ── controls ───────────────────────────────────────────────────── */}
      <Card className="mb-4" style={{ background: C.bg2, borderColor: C.border }}>
        <PanelTitle title="Session controls" sub="Strategy, simulated capital, market, timeframe and the five session operations" />

        <PanelBody
          state={selectionState}
          message={selectionNotice.message}
          code={selectionNotice.code}
          onRetry={loadStrategies}
          emptyText="No strategy you own or subscribe to currently offers a paper session."
        >
          <div
            data-responsive-grid="session-controls"
            data-single-column={String(singleColumn)}
            style={{
              display: 'grid',
              gridTemplateColumns: gridColumns(180),
              gap: C.space.md,
              marginBottom: C.space.md,
              minWidth: 0,
            }}
          >
            <div style={{ minWidth: 0 }}>
              <label htmlFor="paper-strategy" style={labelStyle}>
                Strategy
              </label>
              <select
                id="paper-strategy"
                value={entryId}
                onChange={(e) => onSelectEntry(e.target.value)}
                style={fieldStyle}
              >
                <option value="">Select a strategy…</option>
                {startableEntries.map((entry) => (
                  <option key={entry.entry_id} value={entry.entry_id}>
                    {(entry.ownership === 'SUBSCRIBED' ? entry.listing?.name : entry.name) || entry.entry_id}
                    {` — ${entry.ownership}`}
                  </option>
                ))}
              </select>
              {selectedEntry ? (
                <div style={{ color: C.t3, fontSize: 9, marginTop: 4 }}>
                  {selectedEntry.ownership === 'SUBSCRIBED'
                    ? 'Subscribed. The strategy definition stays with its owner — the server resolves it from the Listing.'
                    : 'Owned.'}
                </div>
              ) : null}
            </div>

            <div style={{ minWidth: 0 }}>
              <label htmlFor="paper-symbol" style={labelStyle}>
                Symbol
              </label>
              <input
                id="paper-symbol"
                type="text"
                value={symbol}
                placeholder="BTC/USDT"
                onChange={(e) => setSymbol(e.target.value)}
                style={fieldStyle}
              />
            </div>

            <div style={{ minWidth: 0 }}>
              <label htmlFor="paper-timeframe" style={labelStyle}>
                Timeframe
              </label>
              <select
                id="paper-timeframe"
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
                style={fieldStyle}
              >
                {TIMEFRAMES.map((tf) => (
                  <option key={tf} value={tf}>
                    {tf}
                  </option>
                ))}
              </select>
            </div>

            <div style={{ minWidth: 0 }}>
              <label htmlFor="paper-currency" style={labelStyle}>
                Currency
              </label>
              <select
                id="paper-currency"
                value={currency}
                onChange={(e) => onCurrencyChange(e.target.value)}
                style={fieldStyle}
              >
                {SUPPORTED_CURRENCIES.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </div>

            <div style={{ minWidth: 0 }}>
              <label htmlFor="paper-capital" style={labelStyle}>
                Initial simulated capital
              </label>
              <input
                id="paper-capital"
                type="text"
                inputMode="decimal"
                value={capitalText}
                onChange={(e) => onCapitalChange(e.target.value)}
                aria-describedby="paper-capital-help"
                aria-invalid={capitalError ? 'true' : 'false'}
                style={{
                  ...fieldStyle,
                  borderColor: capitalError ? C.loss : C.border,
                }}
              />
              <div id="paper-capital-help" style={{ fontSize: 9, marginTop: 4, color: capitalError ? C.loss : C.t3 }}>
                {capitalError
                  || (capitalPreview.ok
                    ? `Sent as ${formatCount(capitalPreview.minor)} minor units — an exact whole number, never a rounded float.`
                    : '')}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
            <Button
              variant="primary"
              size="sm"
              onClick={handleStart}
              disabled={busy !== null || !selectedEntry || !symbol.trim() || !capitalPreview.ok}
              aria-label="Start a simulated paper session"
            >
              <Play size={13} aria-hidden="true" />
              <span style={{ marginLeft: 6 }}>{busy === 'start' ? 'Starting…' : 'Start'}</span>
            </Button>
          </div>
        </PanelBody>

        {/* The four transitions, and the `disabled` state of Requirement 20.5.

            Start is above, because it CREATES a session rather than transitioning the selected
            one. These four are decided by `session_state`: a `CREATED` session admits none of
            them, and that is what `disabled` reports — a control that is genuinely unavailable in
            the state the server reported, named rather than silently greyed out. */}
        <div style={{ marginTop: C.space.md, borderTop: `1px solid ${C.border}`, paddingTop: C.space.md }}>
          <span style={labelStyle}>Session operations</span>
          <PanelBody
            state={operationsState}
            {...failureProps('session')}
            onRetry={() => runRead('session', sessionId)}
            idleText="Select or start a session to operate on it."
            disabledReason={`This session is ${sessionState || 'in an unreported state'}, which admits none of pause, resume, stop or reset. Start a session above, or select one that is running, paused or stopped.`}
          >
            {/* Task 32.5: the four buttons wrap in the wide layout and become one full-width
                column below 768 px, where a wrapped row of four would leave two of them stranded
                at the end of a line. The "admitted from" note takes the row after them either way,
                so it is never squeezed to one word per line. */}
            <div
              data-responsive-grid="session-operations"
              data-single-column={String(singleColumn)}
              style={
                singleColumn
                  ? { display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 8, minWidth: 0 }
                  : { display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', minWidth: 0 }
              }
            >
              <Button
                variant="outline"
                size="sm"
                onClick={handlePause}
                disabled={busy !== null || !availableOperations.includes('pause')}
                aria-label="Pause the selected session"
              >
                <Pause size={13} aria-hidden="true" />
                <span style={{ marginLeft: 6 }}>{busy === 'pause' ? 'Pausing…' : 'Pause'}</span>
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={handleResume}
                disabled={busy !== null || !availableOperations.includes('resume')}
                aria-label="Resume the selected session"
              >
                <Play size={13} aria-hidden="true" />
                <span style={{ marginLeft: 6 }}>{busy === 'resume' ? 'Resuming…' : 'Resume'}</span>
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={handleStop}
                disabled={busy !== null || !availableOperations.includes('stop')}
                aria-label="Stop the selected session"
              >
                <Square size={13} aria-hidden="true" />
                <span style={{ marginLeft: 6 }}>{busy === 'stop' ? 'Stopping…' : 'Stop'}</span>
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={handleReset}
                disabled={busy !== null || !availableOperations.includes('reset')}
                aria-label="Reset the selected session to its recorded initial capital"
              >
                <RotateCcw size={13} aria-hidden="true" />
                <span style={{ marginLeft: 6 }}>{busy === 'reset' ? 'Resetting…' : 'Reset'}</span>
              </Button>
              <span style={{ color: C.t3, fontSize: 9, minWidth: 0, flexBasis: singleColumn ? undefined : '100%' }}>
                {`Admitted from ${sessionState || 'the reported state'}: ${availableOperations.join(', ')}.`}
              </span>
            </div>
          </PanelBody>
        </div>

        <div style={{ marginTop: C.space.lg, borderTop: `1px solid ${C.border}`, paddingTop: C.space.md }}>
          <label htmlFor="paper-session" style={labelStyle}>
            Session
          </label>
          <PanelBody
            state={
              sessionList.status === 'loading' || sessionList.status === 'idle'
                ? PANEL_STATES.LOADING
                : sessionList.status === 'error'
                  ? (sessionList.failure?.state ?? PANEL_STATES.ERROR)
                  : (sessionList.data?.sessions?.length ?? 0) === 0
                    ? PANEL_STATES.EMPTY
                    : PANEL_READY
            }
            message={sessionList.failure?.message || sessionList.error}
            code={sessionList.failure?.code ?? null}
            onRetry={loadSessionList}
            emptyText="You have no paper sessions yet. Start one above."
          >
            <select
              id="paper-session"
              value={sessionId || ''}
              onChange={(e) => {
                setStopReport(null);
                setResetReport(null);
                setSessionId(e.target.value || null);
              }}
              style={{ ...fieldStyle, maxWidth: 520 }}
            >
              <option value="">Select a session…</option>
              {(sessionList.data?.sessions ?? []).map((row) => (
                <option key={row.id} value={row.id}>
                  {`${row.symbol ?? '—'} ${row.timeframe ?? ''} · ${row.session_state ?? '—'} · ${formatInstant(row.created_at) || row.id}`}
                </option>
              ))}
            </select>
          </PanelBody>
        </div>
      </Card>

      {/* ── the honest report of the last stop / reset ─────────────────── */}
      {stopReport ? (
        <Card className="mb-4" style={{ background: C.bg2, borderColor: stopReport.complete === true ? C.border : `${C.warning}66` }}>
          <PanelTitle
            title="Last stop"
            sub="Read from the response's own `complete` flag — not inferred from the call returning"
          />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: 8 }}>
            <StatusPill
              tone={stopReport.complete === true ? 'good' : 'warn'}
              Icon={stopReport.complete === true ? CheckCircle2 : AlertTriangle}
              label={stopReport.complete === true ? 'Stop complete' : 'Stop not complete'}
            />
            <StatusPill tone="muted" label={`State ${String(stopReport.session_state ?? stopReport.status ?? '—').toUpperCase()}`} />
            <SimulatedTag />
          </div>
          <ul
            data-responsive-grid="stop-report"
            data-single-column={String(singleColumn)}
            style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gridTemplateColumns: gridColumns(220), gap: 6, fontSize: 10, color: C.t2, minWidth: 0 }}
          >
            <li>
              Outstanding:{' '}
              <span style={{ color: C.t1 }}>
                {Array.isArray(stopReport.outstanding) && stopReport.outstanding.length
                  ? stopReport.outstanding.join(', ')
                  : 'none reported'}
              </span>
            </li>
            <li>
              Finals committed: <span style={{ color: C.t1 }}>{String(stopReport.finals_committed)}</span>
            </li>
            <li>
              Finals reason: <span style={{ color: C.t1 }}>{stopReport.finals_reason ?? 'none'}</span>
            </li>
            <li>
              Finals stale:{' '}
              <span style={{ color: C.t1 }}>
                {stopReport.stale === null || stopReport.stale === undefined ? NOT_REPORTED : String(stopReport.stale)}
              </span>
            </li>
            <li>
              Loop settled: <span style={{ color: C.t1 }}>{String(stopReport.loop_settled)}</span>
            </li>
            <li>
              Channel registrations closed:{' '}
              <span style={{ color: C.t1 }}>
                {stopReport.registrations_closed === null || stopReport.registrations_closed === undefined
                  ? NOT_REPORTED
                  : formatCount(stopReport.registrations_closed)}
              </span>
            </li>
          </ul>
        </Card>
      ) : null}

      {resetReport ? (
        <Card className="mb-4" style={{ background: C.bg2, borderColor: C.border }}>
          <PanelTitle title="Last reset" sub="Nothing was deleted — the pre-reset orders, fills, trades, metrics and equity snapshots stay readable" />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: 8 }}>
            <StatusPill tone="muted" label={`Series ${formatCount(resetReport.previous_series_index) ?? '—'} → ${formatCount(resetReport.series_index) ?? '—'}`} />
            <SimulatedTag />
          </div>
          <ul
            data-responsive-grid="reset-report"
            data-single-column={String(singleColumn)}
            style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gridTemplateColumns: gridColumns(220), gap: 6, fontSize: 10, color: C.t2, minWidth: 0 }}
          >
            <li>
              Restored capital:{' '}
              <span style={{ color: C.t1 }}>
                {formatMinorUnits(resetReport.initial_capital_minor, sessionCurrency) ?? NOT_REPORTED} {sessionCurrency}
              </span>
            </li>
            <li>
              Orders cancelled: <span style={{ color: C.t1 }}>{formatCount(resetReport.cancelled_orders?.length) ?? '0'}</span>
            </li>
            <li>
              Orders not cancellable:{' '}
              <span style={{ color: C.t1 }}>{formatCount(resetReport.orders_not_cancellable?.length) ?? '0'}</span>
            </li>
            <li>
              Positions closed: <span style={{ color: C.t1 }}>{formatCount(resetReport.closed_positions?.length) ?? '0'}</span>
            </li>
          </ul>
        </Card>
      ) : null}

      {/* ── status strip: market data, session, price, feed health ─────── */}
      <div
        data-responsive-grid="status-strip"
        data-single-column={String(singleColumn)}
        style={{
          display: 'grid',
          gridTemplateColumns: gridColumns(230),
          gap: C.space.md,
          marginBottom: C.space.md,
          minWidth: 0,
        }}
      >
        <div style={panelStyle}>
          <span style={labelStyle}>Market-data status</span>
          {/* The feed's own record, which stays readable when the feed is disconnected: this panel
              is where a reader finds OUT that it is. It renders the recorded `feed_state`
              verbatim, so it never needs a `feed-disconnected` state of its own. */}
          <PanelBody state={panelState('session', !session)} {...failureProps('session')} onRetry={() => runRead('session', sessionId)} emptyText="No session selected.">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <StatusPill
                  tone={feedCopy?.tone ?? 'muted'}
                  Icon={feedTradeable ? Wifi : WifiOff}
                  label={feedCopy?.label ?? (feedState || NOT_REPORTED)}
                  title={feedCopy?.note}
                />
                <StatusPill tone="muted" Icon={Radio} label={`Transport ${session?.feed_transport ?? 'not reported'}`} />
              </div>
              <div style={{ color: C.t3, fontSize: 9 }}>
                {feedCopy?.note ?? 'This feed state is not one of the five the platform records; it is shown verbatim.'}
              </div>
              <div style={{ color: C.t2, fontSize: 9 }}>
                Source: <span style={{ color: C.t1 }}>{session?.market_data_source ?? NOT_REPORTED}</span>
              </div>
              <SimulatedTag />
            </div>
          </PanelBody>
        </div>

        <div style={panelStyle}>
          <span style={labelStyle}>Session status</span>
          <PanelBody state={panelState('session', !session)} {...failureProps('session')} onRetry={() => runRead('session', sessionId)} emptyText="No session selected.">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <StatusPill
                  tone={sessionCopy?.tone ?? 'muted'}
                  Icon={sessionState === 'RUNNING' ? Activity : sessionState === 'PAUSED' ? Pause : Layers}
                  label={sessionCopy?.label ?? (sessionState || NOT_REPORTED)}
                />
                <StatusPill tone="muted" label={`Events ${formatCount(session?.event_sequence) ?? '0'}`} />
              </div>
              <div style={{ color: C.t2, fontSize: 9 }}>
                {session?.symbol ?? '—'} · {session?.timeframe ?? '—'} · {session?.exchange_id ?? '—'}
              </div>
              <div style={{ color: C.t2, fontSize: 9 }}>
                Recorded capital{' '}
                <span style={{ color: C.t1 }}>
                  {formatMinorUnits(session?.initial_capital_minor, sessionCurrency) ?? NOT_REPORTED} {sessionCurrency}
                </span>
              </div>
              <SimulatedTag />
            </div>
          </PanelBody>
        </div>

        {/* The current price is the figure a disconnected feed invalidates, so it is one of the
            two panels that render `feed-disconnected` rather than a number. The panel names the
            recorded feed state and the last validated price INSTANT — never the price itself
            presented as current (Requirements 18.15, 20.5, 28.5). */}
        <Figure
          label="Current price"
          Icon={DollarSign}
          state={liveFigureState(false)}
          {...figureFailure(reads.session.status === 'error' ? 'session' : 'events')}
          onRetry={() => refreshSession(sessionId, ['session', 'events'])}
          feedState={feedState || null}
          feedNote={feedCopy?.note ?? null}
          value={latestTick ? formatPrice(latestTick.close) : null}
          absent="No validated market event yet"
          unit={latestTick?.symbol ?? undefined}
          lastPriceAt={latestTick?.at ?? lastPriceAt}
          hint={
            latestTick
              ? `Candle ${formatInstant(latestTick.at) || NOT_REPORTED}`
              : 'A price is shown only once a validated candle has arrived — none is synthesised.'
          }
        />

        <div style={panelStyle}>
          <span style={labelStyle}>Feed latency &amp; health</span>
          <PanelBody
            state={liveFigureState(!latestTick)}
            {...(reads.session.status === 'error' ? failureProps('session') : failureProps('events'))}
            onRetry={() => refreshSession(sessionId, ['session', 'events'])}
            feedState={feedState || null}
            feedNote={feedCopy?.note ?? null}
            lastPriceAt={latestTick?.at ?? lastPriceAt}
            emptyText="No market event has been recorded for this session yet."
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ color: C.t1, fontWeight: 900, fontSize: 16 }}>
                {formatLatency(latestTick?.latencyMs) ?? NOT_REPORTED}
              </div>
              <div style={{ color: C.t3, fontSize: 9 }}>
                Delivery latency of the last validated candle. A negative value means the clocks
                disagree and is shown as recorded; an unmeasured one reads “{NOT_REPORTED}” rather
                than zero.
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <StatusPill
                  tone={feedTradeable ? 'good' : 'warn'}
                  Icon={feedTradeable ? CheckCircle2 : AlertTriangle}
                  label={feedTradeable ? 'Execution admitted' : 'Execution not admitted'}
                />
                {reads.events.data?.history_incomplete === true ? (
                  <StatusPill tone="bad" Icon={AlertTriangle} label="Event history incomplete" />
                ) : null}
                {reads.events.data?.truncated === true ? (
                  <StatusPill tone="warn" Icon={AlertTriangle} label="Replay truncated" />
                ) : null}
              </div>
              <SimulatedTag />
            </div>
          </PanelBody>
        </div>
      </div>

      {/* ── the live stream, its reconnection, and the bounds on what it retains ────────── */}
      <Card className="mb-4" style={{ background: C.bg2, borderColor: channelRefusal ? `${C.loss}66` : C.border }}>
        <PanelTitle
          title="Live event stream"
          sub={`paper.${sessionId ?? '{session}'} — one subscription, released when this route unmounts`}
          right={<SimulatedTag />}
        />
        <PanelBody
          state={liveStreamState}
          message={
            channelRefusal
              ? channelRefusal.reason
                || 'The server refused this session\u2019s live channel. Its own code is shown below.'
              : gapCloseError?.message
          }
          code={channelRefusal ? channelRefusal.code || null : gapCloseError?.failure?.code ?? null}
          onRetry={() => closeGap('manual')}
          retryLabel="Replay from the last applied sequence"
          idleText="No session is selected, so no channel is subscribed and no timer is running."
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              {reconnecting ? (
                <span
                  role="status"
                  aria-live="polite"
                  data-testid="paper-stream-reconnecting"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 5,
                    fontSize: 10,
                    fontFamily: 'monospace',
                    fontWeight: 800,
                    letterSpacing: 0.6,
                    textTransform: 'uppercase',
                    color: C.warning,
                    border: `1px solid ${C.warning}55`,
                    borderRadius: C.radius.sm,
                    padding: '2px 7px',
                  }}
                >
                  <Spinner size={11} />
                  Reconnecting…
                </span>
              ) : (
                <StatusPill
                  tone={socketConnected ? 'good' : 'warn'}
                  Icon={socketConnected ? Wifi : Unplug}
                  label={socketConnected ? 'Frames arriving' : `Socket ${socketStatus}`}
                />
              )}
              <StatusPill tone="muted" label={`Applied through sequence ${formatCount(retained.lastSequence) ?? '0'}`} />
              <StatusPill tone="muted" label={`Gap closes ${formatCount(gapCloseCount) ?? '0'}`} />
              {retained.duplicatesDiscarded > 0 ? (
                <StatusPill
                  tone="muted"
                  label={`Duplicates discarded ${formatCount(retained.duplicatesDiscarded)}`}
                  title="Frames whose event_id had already been applied. Requirement 19.8."
                />
              ) : null}
            </div>

            <div style={{ color: C.t3, fontSize: 9 }}>
              On reconnect the page asks{' '}
              <code>events(sessionId, {formatCount(retained.lastSequence) ?? '0'})</code> for the
              frames the drop swallowed, and discards any whose <code>event_id</code> it has already
              applied. While the socket is down, the session and its metrics are re-read every{' '}
              {formatCount(SAFETY_POLL_INTERVAL_MS / 1000)} seconds so nothing here is presented as
              current when it is not.
            </div>
          </div>
        </PanelBody>

        {/* Requirement 27.5's bounds, disclosed rather than left as an invisible policy — a view
            that silently drops the oldest half of its history is telling the reader something.

            Outside `PanelBody` on purpose: this is a statement about THIS PAGE'S memory, not a
            read of the server's, so it stays true and stays visible while the socket is refused,
            reconnecting or down. */}
        <div
          data-testid="paper-retention"
          data-retained-events={retained.events.length}
          data-retained-ticks={retained.ticks.length}
          data-retained-event-cap={MAX_RETAINED_EVENTS}
          data-retained-tick-cap={MAX_RETAINED_TICKS}
          data-chart-point-cap={MAX_CHART_POINTS_PER_SERIES}
          data-price-chart-points={priceChart.length}
          data-pnl-chart-points={pnlChart.length}
          data-drawdown-chart-points={drawdownChart.length}
          data-equity-chart-points={equityPoints.length}
          data-events-discarded={retained.eventsDiscarded}
          data-ticks-discarded={retained.ticksDiscarded}
          style={{
            color: C.t2,
            fontSize: 10,
            fontFamily: 'monospace',
            lineHeight: 1.6,
            marginTop: C.space.md,
            borderTop: `1px solid ${C.border}`,
            paddingTop: C.space.md,
          }}
        >
          Retained in memory:{' '}
          <span style={{ color: C.t1 }}>
            {formatCount(retained.events.length)} / {formatCount(MAX_RETAINED_EVENTS)} events
          </span>
          ,{' '}
          <span style={{ color: C.t1 }}>
            {formatCount(retained.ticks.length)} / {formatCount(MAX_RETAINED_TICKS)} ticks
          </span>
          , and at most <span style={{ color: C.t1 }}>{formatCount(MAX_CHART_POINTS_PER_SERIES)}</span>{' '}
          points per chart series. The oldest are discarded first, by position in the
          session&rsquo;s sequence rather than by arrival order.
          {retained.eventsDiscarded > 0 || retained.ticksDiscarded > 0
            ? ` ${formatCount(retained.eventsDiscarded)} event(s) and ${formatCount(retained.ticksDiscarded)} tick(s) have been discarded by that bound — they remain readable from the session's event log.`
            : ''}
        </div>
      </Card>

      {/* ── "not computed" is a statement the server makes, and it is shown ── */}
      {sessionId && reads.metrics.status === 'ready' && !metricsComputed ? (
        <div
          role="status"
          style={{
            ...panelStyle,
            borderColor: `${C.warning}55`,
            marginBottom: C.space.md,
            display: 'flex',
            alignItems: 'flex-start',
            gap: 8,
            fontSize: 11,
            color: C.warning,
          }}
        >
          <AlertTriangle size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
          <span>
            The metrics read completed and reported <code>computed: false</code> — no metrics row
            exists for this session yet. The figures below therefore read “{NOT_COMPUTED}”. That is
            not the same statement as “the metrics are zero”, and a zero is not shown in its place.
          </span>
        </div>
      ) : null}

      {/* ── the figures ────────────────────────────────────────────────── */}
      <div
        data-responsive-grid="figures"
        data-single-column={String(singleColumn)}
        style={{
          display: 'grid',
          gridTemplateColumns: gridColumns(200),
          gap: C.space.md,
          marginBottom: C.space.md,
          minWidth: 0,
        }}
      >
        <Figure
          label={`Equity (${sessionCurrency})`}
          Icon={DollarSign}
          state={panelState('equity', false)}
          {...figureFailure('equity')}
          onRetry={() => runRead('equity', sessionId)}
          value={formatMoneyDecimal(latestSnapshot?.total_equity, sessionCurrency)}
          absent="No equity snapshot yet"
          stale={equityStale}
          lastPriceAt={lastPriceAt}
          hint={
            latestSnapshot
              ? `Snapshot ${formatInstant(latestSnapshot.taken_at) || NOT_REPORTED} · cause ${latestSnapshot.cause ?? NOT_REPORTED}`
              : 'Persisted equity snapshots are the only source for this figure.'
          }
        />
        <Figure
          label={`Cash — available (${sessionCurrency})`}
          Icon={DollarSign}
          state={panelState('equity', false)}
          {...figureFailure('equity')}
          onRetry={() => runRead('equity', sessionId)}
          value={formatMoneyDecimal(latestSnapshot?.available_balance, sessionCurrency)}
          absent="No equity snapshot yet"
          stale={equityStale}
          lastPriceAt={lastPriceAt}
          hint={
            latestSnapshot
              ? `Locked ${formatMoneyDecimal(latestSnapshot.locked_balance, sessionCurrency) ?? NOT_REPORTED} · position value ${formatMoneyDecimal(latestSnapshot.position_market_value, sessionCurrency) ?? NOT_REPORTED}`
              : null
          }
        />
        <Figure
          label={`Unrealized PnL (${sessionCurrency})`}
          Icon={TrendingUp}
          state={panelState('metrics', false)}
          {...figureFailure('metrics')}
          onRetry={() => runRead('metrics', sessionId)}
          value={metricsComputed ? formatMoneyDecimal(metricsRow?.unrealized_pnl, sessionCurrency) : null}
          stale={equityStale}
          lastPriceAt={lastPriceAt}
          hint="From open quantity at the latest validated price."
        />
        <Figure
          label={`Realized PnL (${sessionCurrency})`}
          Icon={TrendingDown}
          state={panelState('metrics', false)}
          {...figureFailure('metrics')}
          onRetry={() => runRead('metrics', sessionId)}
          value={metricsComputed ? formatMoneyDecimal(metricsRow?.realized_pnl, sessionCurrency) : null}
          hint="From closed quantity at recorded fill prices."
        />
        <Figure
          label="Total return"
          Icon={Activity}
          state={panelState('metrics', false)}
          {...figureFailure('metrics')}
          onRetry={() => runRead('metrics', sessionId)}
          value={metricsComputed ? formatPercentValue(metricsRow?.total_return_pct) : null}
        />
        <Figure
          label={`Max drawdown (${sessionCurrency})`}
          Icon={TrendingDown}
          state={panelState('metrics', false)}
          {...figureFailure('metrics')}
          onRetry={() => runRead('metrics', sessionId)}
          value={metricsComputed ? formatMoneyDecimal(metricsRow?.max_drawdown_amount, sessionCurrency) : null}
          hint={
            metricsComputed
              ? `Of peak equity: ${formatFractionAsPercent(metricsRow?.max_drawdown_fraction) ?? NOT_COMPUTED}`
              : 'Computed from the persisted equity snapshots; zero while fewer than two exist.'
          }
        />
        <Figure
          label="Win rate"
          Icon={CheckCircle2}
          state={panelState('metrics', false)}
          {...figureFailure('metrics')}
          onRetry={() => runRead('metrics', sessionId)}
          value={metricsComputed ? formatFractionAsPercent(metricsRow?.win_rate) : null}
          hint="Absent — not zero — while no trade has closed."
        />
        <Figure
          label="Closed trade count"
          Icon={Layers}
          // Two candidate sources, and the panel is in an error state only when BOTH reads failed:
          // a computed metrics row is preferred, and the closed round-trip read is a real
          // measurement in its own right rather than a stand-in.
          state={
            reads.metrics.status === 'error' && reads.trades.status === 'error'
              ? (reads.trades.failure?.state ?? PANEL_STATES.ERROR)
              : panelState('trades', false)
          }
          {...figureFailure(reads.trades.status === 'error' ? 'trades' : 'metrics')}
          onRetry={() => refreshSession(sessionId, ['metrics', 'trades'])}
          value={
            metricsComputed && metricsRow?.closed_trade_count !== null && metricsRow?.closed_trade_count !== undefined
              ? formatCount(metricsRow.closed_trade_count)
              : reads.trades.status === 'ready'
                ? formatCount(reads.trades.data?.count)
                : null
          }
          hint={
            metricsComputed && metricsRow?.closed_trade_count !== null && metricsRow?.closed_trade_count !== undefined
              ? 'From the computed metrics row.'
              : 'From the closed round-trip read.'
          }
        />
      </div>

      {/* ── equity curve ───────────────────────────────────────────────── */}
      <Card className="mb-4" style={{ background: C.bg2, borderColor: C.border }}>
        <PanelTitle
          title="Equity curve"
          sub="Drawn from the persisted equity snapshots — a reconnect or a remount redraws the same curve"
          right={<SimulatedTag />}
        />
        <PanelBody
          state={panelState('equity', equityView.points.length === 0)}
          {...failureProps('equity')}
          onRetry={() => runRead('equity', sessionId)}
          emptyText="No equity snapshot has been persisted for this session yet."
        >
          <ResponsiveContainer width="100%" height={chartHeight}>
            <AreaChart data={equityPoints}>
              <defs>
                <linearGradient id="paperEquityFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={C.profit} stopOpacity={0.28} />
                  <stop offset="95%" stopColor={C.profit} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
              <XAxis dataKey="label" stroke={C.t3} fontSize={9} tickLine={false} minTickGap={chartAxis.tickGap} />
              <YAxis stroke={C.t3} fontSize={9} tickLine={false} domain={['auto', 'auto']} width={chartAxis.axisWidth} />
              <Tooltip
                content={
                  <ChartTooltip
                    rows={[
                      { key: 'takenAt', label: 'Taken at', render: (v) => formatInstant(v) },
                      { key: 'totalEquityText', label: 'Total equity', render: (v) => formatMoneyDecimal(v, sessionCurrency) },
                      { key: 'availableText', label: 'Available', render: (v) => formatMoneyDecimal(v, sessionCurrency) },
                      { key: 'lockedText', label: 'Locked', render: (v) => formatMoneyDecimal(v, sessionCurrency) },
                      { key: 'positionValueText', label: 'Position value', render: (v) => formatMoneyDecimal(v, sessionCurrency) },
                      { key: 'seriesIndex', label: 'Series', render: (v) => formatCount(v) },
                      { key: 'cause', label: 'Cause' },
                      { key: 'stale', label: 'Stale', render: (v) => String(Boolean(v)) },
                    ]}
                  />
                }
              />
              {equityBoundaries.map((boundary) => (
                <ReferenceLine
                  key={`series-${boundary}`}
                  x={equityPoints[boundary]?.label}
                  stroke={C.warning}
                  strokeDasharray="4 4"
                />
              ))}
              <Area dataKey="equity" stroke={C.profit} strokeWidth={2} fill="url(#paperEquityFill)" dot={false} connectNulls={false} />
            </AreaChart>
          </ResponsiveContainer>
          <div
            style={{ color: C.t3, fontSize: 9, marginTop: 6 }}
            data-testid="paper-equity-series"
            data-chart-points={equityPoints.length}
            data-chart-point-cap={MAX_CHART_POINTS_PER_SERIES}
            data-chart-points-dropped={equityPointsDropped}
          >
            {equityView.seriesIndices.length > 1
              ? `Series ${equityView.seriesIndices.join(', ')} — a reset begins a new series and the dashed lines mark the boundaries. The snapshots are plotted in the order the server returned them and are not re-sorted.`
              : 'Plotted in the order the server returned them and not re-sorted: the drawdown of a resorted series is not the drawdown of the series that was read.'}
            {equityPointsDropped > 0
              ? ` The plot is bounded to the most recent ${formatCount(MAX_CHART_POINTS_PER_SERIES)} points; ${formatCount(equityPointsDropped)} older snapshot(s) are read but not drawn. The figures above come from the latest snapshot, which the bound cannot reach.`
              : ''}
          </div>
        </PanelBody>
      </Card>

      {/* ── PnL and drawdown ───────────────────────────────────────────── */}
      <div
        data-responsive-grid="pnl-drawdown"
        data-single-column={String(singleColumn)}
        style={{ display: 'grid', gridTemplateColumns: gridColumns(320), gap: C.space.md, marginBottom: C.space.md, minWidth: 0 }}
      >
        {/* `minWidth: 0` on a card that holds a chart is not decoration: a grid item's default
            `min-width: auto` floors the track at the item's min-content width, and a chart inside
            it would then keep the track — and the page — wider than the viewport. */}
        <Card style={{ background: C.bg2, borderColor: C.border, minWidth: 0 }}>
          <PanelTitle title="Profit and loss" sub="From the session's recorded paper_pnl_updated events" right={<SimulatedTag />} />
          <PanelBody
            state={panelState('events', pnlChart.length === 0)}
            {...failureProps('events')}
            onRetry={() => runRead('events', sessionId)}
            emptyText="No profit-and-loss event has been recorded for this session yet."
          >
            <ResponsiveContainer width="100%" height={chartHeight}>
              <ComposedChart data={pnlChart}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                <XAxis dataKey="label" stroke={C.t3} fontSize={9} tickLine={false} minTickGap={chartAxis.tickGap} />
                <YAxis stroke={C.t3} fontSize={9} tickLine={false} domain={['auto', 'auto']} width={chartAxis.axisWidth} />
                <Tooltip
                  content={
                    <ChartTooltip
                      rows={[
                        { key: 'label', label: 'At' },
                        { key: 'realizedText', label: 'Realized', render: (v) => formatMoneyDecimal(v, sessionCurrency) },
                        { key: 'unrealizedText', label: 'Unrealized', render: (v) => formatMoneyDecimal(v, sessionCurrency) },
                        { key: 'totalText', label: 'Total', render: (v) => formatMoneyDecimal(v, sessionCurrency) },
                        { key: 'stale', label: 'Stale', render: (v) => String(Boolean(v)) },
                      ]}
                    />
                  }
                />
                <Line dataKey="realized" name="Realized" stroke={C.accent} strokeWidth={2} dot={false} connectNulls={false} />
                <Line dataKey="unrealized" name="Unrealized" stroke={C.purple} strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls={false} />
                <Line dataKey="total" name="Total" stroke={C.profit} strokeWidth={2} strokeDasharray="1 3" dot={false} connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
            <div style={{ color: C.t3, fontSize: 9, marginTop: 6 }}>
              Realized is solid, unrealized is long-dashed and total is dotted, so the three series
              are distinguishable without relying on colour. An unrealized or total figure that had
              no current price behind it leaves a gap rather than being drawn as zero.
            </div>
          </PanelBody>
        </Card>

        <Card style={{ background: C.bg2, borderColor: C.border, minWidth: 0 }}>
          <PanelTitle title="Drawdown" sub="From the session's recorded paper_drawdown_updated events" right={<SimulatedTag />} />
          <PanelBody
            state={panelState('events', drawdownChart.length === 0)}
            {...failureProps('events')}
            onRetry={() => runRead('events', sessionId)}
            emptyText="No drawdown event has been recorded for this session yet."
          >
            <ResponsiveContainer width="100%" height={chartHeight}>
              <AreaChart data={drawdownChart}>
                <defs>
                  <linearGradient id="paperDrawdownFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={C.loss} stopOpacity={0.28} />
                    <stop offset="95%" stopColor={C.loss} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                <XAxis dataKey="label" stroke={C.t3} fontSize={9} tickLine={false} minTickGap={chartAxis.tickGap} />
                <YAxis stroke={C.t3} fontSize={9} tickLine={false} domain={['auto', 'auto']} width={chartAxis.axisWidth} />
                <Tooltip
                  content={
                    <ChartTooltip
                      rows={[
                        { key: 'label', label: 'At' },
                        { key: 'amountText', label: 'Max drawdown', render: (v) => formatMoneyDecimal(v, sessionCurrency) },
                        { key: 'fractionText', label: 'Of peak', render: (v) => formatFractionAsPercent(v) },
                        { key: 'peakText', label: 'Peak equity', render: (v) => formatMoneyDecimal(v, sessionCurrency) },
                        { key: 'snapshotCount', label: 'Snapshots', render: (v) => formatCount(v) },
                      ]}
                    />
                  }
                />
                <Area dataKey="amount" stroke={C.loss} strokeWidth={2} fill="url(#paperDrawdownFill)" dot={false} connectNulls={false} />
              </AreaChart>
            </ResponsiveContainer>
          </PanelBody>
        </Card>
      </div>

      {/* ── price series with trade markers ────────────────────────────── */}
      <Card className="mb-4" style={{ background: C.bg2, borderColor: C.border }}>
        <PanelTitle
          title="Price series and trade markers"
          sub="Validated candles from the recorded market_tick events; markers are the fills those candles produced"
          right={<SimulatedTag />}
        />
        <PanelBody
          state={panelState('events', priceChart.length === 0)}
          {...failureProps('events')}
          onRetry={() => runRead('events', sessionId)}
          emptyText="No validated candle has been recorded for this session yet."
        >
          <ResponsiveContainer width="100%" height={chartHeight + 40}>
            <ComposedChart data={priceChart}>
              <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
              <XAxis dataKey="label" stroke={C.t3} fontSize={9} tickLine={false} minTickGap={chartAxis.tickGap} />
              <YAxis stroke={C.t3} fontSize={9} tickLine={false} domain={['auto', 'auto']} width={chartAxis.axisWidth} />
              <Tooltip
                content={
                  <ChartTooltip
                    rows={[
                      { key: 'at', label: 'Candle', render: (v) => formatInstant(v) },
                      { key: 'closeText', label: 'Close', render: (v) => formatPrice(v) },
                      { key: 'latencyMs', label: 'Latency', render: (v) => formatLatency(v) },
                      { key: 'feedState', label: 'Feed state' },
                      { key: 'buyFillText', label: 'Buy fill', render: (v) => formatPrice(v) },
                      { key: 'sellFillText', label: 'Sell fill', render: (v) => formatPrice(v) },
                    ]}
                  />
                }
              />
              <Line dataKey="close" name="Close" stroke={C.accent} strokeWidth={2} dot={false} connectNulls={false} />
              <Scatter dataKey="buyFill" name="Buy fill" fill={C.profit} shape="triangle" />
              <Scatter dataKey="sellFill" name="Sell fill" fill={C.loss} shape="diamond" />
            </ComposedChart>
          </ResponsiveContainer>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 6, fontSize: 9, color: C.t3 }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <ArrowUpRight size={11} aria-hidden="true" style={{ color: C.profit }} /> Buy fill — triangle
            </span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <ArrowDownRight size={11} aria-hidden="true" style={{ color: C.loss }} /> Sell fill — diamond
            </span>
            <span>
              {formatCount(derived.fills.length) ?? '0'} fill marker(s) placed on the last candle at or
              before each fill.
            </span>
          </div>
        </PanelBody>
      </Card>

      {/* ── open positions ─────────────────────────────────────────────── */}
      <Card className="mb-4" style={{ background: C.bg2, borderColor: C.border }}>
        <PanelTitle
          title={`Open positions (${formatCount(positions.length) ?? '0'})`}
          sub="Direction is an explicit side; a size is never negative"
          right={<SimulatedTag />}
        />
        <PanelBody
          state={panelState('positions', positions.length === 0)}
          {...failureProps('positions')}
          onRetry={() => runRead('positions', sessionId)}
          emptyText="This session holds no open position."
        >
          <DataTable
            caption="Simulated open positions"
            stacked={stackedTables}
            rows={positions}
            rowKey={(row) => row.id}
            columns={positionColumns}
          />
          <div style={{ color: C.t3, fontSize: 9, marginTop: 6 }}>
            A current price, an unrealized figure or a priced-at instant that the server did not
            report reads “{NOT_REPORTED}” — no price is carried forward and none is synthesised.
          </div>
        </PanelBody>
      </Card>

      {/* ── open orders ────────────────────────────────────────────────── */}
      <Card className="mb-4" style={{ background: C.bg2, borderColor: C.border }}>
        <PanelTitle
          title={`Open orders (${formatCount(openOrders.length) ?? '0'})`}
          sub={`Non-terminal order states out of ${formatCount(allOrders.length) ?? '0'} order(s) recorded for this session`}
          right={<SimulatedTag />}
        />
        <PanelBody
          state={panelState('orders', openOrders.length === 0)}
          {...failureProps('orders')}
          onRetry={() => runRead('orders', sessionId)}
          emptyText={allOrders.length === 0 ? 'This session has placed no order.' : 'Every order this session placed has reached a terminal state.'}
        >
          <DataTable
            caption="Simulated open orders"
            stacked={stackedTables}
            rows={openOrders}
            rowKey={(row) => row.id}
            columns={orderColumns}
          />
          <div style={{ color: C.t3, fontSize: 9, marginTop: 6 }}>
            Fees are the integer Minor_Units the order recorded, decimal-shifted for display only.
          </div>
        </PanelBody>
      </Card>

      {/* ── completed trades ───────────────────────────────────────────── */}
      <Card className="mb-4" style={{ background: C.bg2, borderColor: C.border }}>
        <PanelTitle
          title={`Completed trades (${formatCount(trades.length) ?? '0'})`}
          sub="Closed round-trips — the set the win rate is computed over"
          right={<SimulatedTag />}
        />
        <PanelBody
          state={panelState('trades', trades.length === 0)}
          {...failureProps('trades')}
          onRetry={() => runRead('trades', sessionId)}
          emptyText="No position has reached size zero in this session yet."
        >
          <DataTable
            caption="Simulated completed trades"
            stacked={stackedTables}
            rows={trades}
            rowKey={(row) => row.id}
            columns={tradeColumns}
          />
        </PanelBody>
      </Card>

      {/* ── signal stream and execution events ─────────────────────────── */}
      <div
        data-responsive-grid="streams"
        data-single-column={String(singleColumn)}
        style={{ display: 'grid', gridTemplateColumns: gridColumns(320), gap: C.space.md, minWidth: 0 }}
      >
        <Card style={{ background: C.bg2, borderColor: C.border, minWidth: 0 }}>
          <PanelTitle
            title={`Signal stream (${formatCount(derived.signals.length) ?? '0'})`}
            sub="The recorded signal_generated events — decision, side, quantity and price only"
            right={<SimulatedTag />}
          />
          <PanelBody
            state={panelState('events', derived.signals.length === 0)}
            {...failureProps('events')}
            onRetry={() => runRead('events', sessionId)}
            emptyText="This session has generated no signal yet."
          >
            <DataTable
              caption="Simulated signal stream"
              stacked={stackedTables}
              rows={signalRows}
              rowKey={(signal) => signal.eventId ?? `${signal.sequence}`}
              columns={signalColumns}
            />
            <div style={{ color: C.t3, fontSize: 9, marginTop: 6 }}>
              A signal recorded without a validated price shows “{NOT_REPORTED}” for its price
              rather than a zero.
            </div>
          </PanelBody>
        </Card>

        <Card style={{ background: C.bg2, borderColor: C.border, minWidth: 0 }}>
          <PanelTitle
            title={`Execution events (${formatCount(derived.executions.length) ?? '0'})`}
            sub="Order and session lifecycle frames, newest first"
            right={<SimulatedTag />}
          />
          <PanelBody
            state={panelState('events', derived.executions.length === 0)}
            {...failureProps('events')}
            onRetry={() => runRead('events', sessionId)}
            emptyText="No execution event has been recorded for this session yet."
          >
            <DataTable
              caption="Simulated execution events"
              stacked={stackedTables}
              rows={executionRows}
              rowKey={(event) => event.eventId ?? `${event.sequence}-${event.type}`}
              columns={executionColumns}
            />
            {derived.errors.length ? (
              <div role="alert" style={{ marginTop: 8, color: C.loss, fontSize: 10 }}>
                {formatCount(derived.errors.length)} error frame(s) recorded on this session. The most
                recent: {derived.errors[derived.errors.length - 1].code ?? NOT_REPORTED} —{' '}
                {derived.errors[derived.errors.length - 1].message ?? NOT_REPORTED}
              </div>
            ) : null}
          </PanelBody>
        </Card>
      </div>
    </div>
  );
}
