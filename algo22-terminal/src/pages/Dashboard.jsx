/**
 * ═══════════════════════════════════════════════════════════════════════════
 * pages/Dashboard — the command center (`/app/dashboard`)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 19.1, **part A**: the single read, the one failure state and
 * tier 1. design.md §7.1, §11.1. Requirements 3.1, 3.4, 3.5, 3.6, 14.5.
 *
 * Part B rebuilds tier 2 — active strategies, open positions, exchange health, recent
 * signals and orders, the equity curve — onto `ds/Panel`, `ds/DataTable` and a lazy
 * `ds/Chart`. Until it lands, every zone below tier 1 keeps the markup it has and reads it
 * off the one read this part introduces, so the page is never half-built between the two
 * commits. The zones are marked `PART B` where they stand.
 *
 * ONE READ, ONE FAILURE STATE
 * ---------------------------
 * `GET /api/dashboard` is a single aggregated read that 503s as a whole, so there is one
 * failure and the page renders ONE page-level `ds/ErrorState` with retry (§7.1,
 * Requirement 3.6). Per-panel error states would imply independent reads that do not
 * exist, and a per-panel fallback to a cached value is the previously-cached-as-live
 * rendering Requirement 14.5 forbids. Three things make that structural rather than
 * remembered:
 *
 *   1. `usePanelState` drops `data` on failure — see its docblock's three inversions of
 *      `usePolling`, which keeps the last payload and merely sets `error`. That is also
 *      why this page does not use `usePolling`: it lists `data` in its `fetch` dependency
 *      array, so its interval is torn down and re-created on every tick (§1.12).
 *   2. The projection effect below clears every derived view model when `data` is `null`,
 *      so no zone holds a value from a read that has since failed.
 *   3. The failure branch renders the error state INSTEAD of the body, so no zone, table
 *      or figure exists in the DOM at all while the read is broken.
 *
 * The period control belongs to the read, not to the chart. It sits in `PageHeader` beside
 * the environment switch because `equity_days` is a parameter of the one read: the
 * in-panel timeframe selector this replaces issued a SECOND `getDashboard` call and wrote
 * the equity series from it, which is two reads and two failure surfaces for one figure.
 *
 * TIER 1 — ONE CONTAINER, FOUR FIGURES
 * ------------------------------------
 * `design/pageHierarchy.js` declares which figure belongs to which tier and
 * `design/pageFields.js` declares where each one's value comes from and what a trader
 * reads when there is none. Neither is restated here: the row is rendered by walking the
 * declared tier-1 list, so a fifth figure cannot appear without being declared, and
 * `Metric tier={1}` appears nowhere else on the page. That is how Requirement 3.4 — one
 * row of equally-weighted figures, not two — is satisfied structurally (§7.1). The
 * container carries `data-page` + `data-page-tier` so the claim is decidable from the
 * rendered DOM (Property 4, task 19.5).
 *
 * §7.1 has no tier 3, and one is not invented here to fill the shape.
 *
 * Two of the four figures are read exactly as the server states them, and the reason is in
 * `pageFields`' notes rather than here: `overview.today_pnl` is `today_realized_pnl +
 * unrealized_pnl` computed server-side and is NOT recomputed from the two parts, and
 * `overview.cumulative_pnl` includes mark-to-market on open positions and is therefore not
 * labelled a realised figure. `risk.current_drawdown_pct_v2` is BC-1's field: `null` —
 * never `0.0` — when the equity series is absent, one point long or has no positive peak,
 * and `null` renders the not-available marker with the declared reason. Its deprecated
 * neighbour `risk.current_drawdown_pct` publishes today's return and is read by nothing.
 *
 * THE KILL SWITCH IS UNTOUCHED
 * ----------------------------
 * Task 19.2 owns it, and Requirement 19.1 forbids changing a risk control's logic. Its
 * trigger, its confirmation and `riskApi.killSwitch()` / `recoverKillSwitch()` are exactly
 * as they were; only the container they sit in moved.
 *
 * @module pages/Dashboard
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ShieldCheck, AlertTriangle, ArrowRight,
  RefreshCw, BarChart2, Server,
  Play, Pause, ShieldAlert, AlertOctagon
} from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip
} from "recharts";
import { dashboardApi } from "../api/modules/dashboard";
import { riskApi } from "../api/modules/risk";
import wsClient from "../websocketClient";
import { CommandButton } from "../components/ds/CommandButton";
import { ErrorState } from "../components/ds/ErrorState";
import { Metric } from "../components/ds/Metric";
import { PageHeader } from "../components/ds/PageHeader";
import { Panel } from "../components/ds/Panel";
import { PAGES, PAGE_FIELDS_BY_PAGE } from "../design/pageFields";
import {
  PAGE_HIERARCHY_BY_PAGE,
  TIER_ATTRIBUTE,
  TIER_PAGE_ATTRIBUTE,
} from "../design/pageHierarchy";
import { fromNullable } from "../design/reported";
import { PANEL_STATES, usePanelState } from "../hooks/usePanelState";

// Safe float conversion helper
export function floatVal(v) {
  const num = parseFloat(v);
  if (isNaN(num) || !isFinite(num)) return 0.00;
  return num;
}

// Compute Liquidation Distance % for derivatives
export function computeLiquidationDistance(markPrice, liqPrice, side, marketType) {
  if (marketType === "spot" || liqPrice == null || liqPrice <= 0 || markPrice <= 0) {
    return null;
  }
  if (side === "long") {
    return ((markPrice - liqPrice) / markPrice) * 100;
  } else {
    return ((liqPrice - markPrice) / markPrice) * 100;
  }
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE DECLARATION — read, never restated
 * ══════════════════════════════════════════════════════════════════════════ */

const DASHBOARD_FIELDS = PAGE_FIELDS_BY_PAGE[PAGES.DASHBOARD] ?? [];

/** §7.1's tiers. There are two of them; the layout has no tier 3 and none is invented. */
const TIERS = PAGE_HIERARCHY_BY_PAGE[PAGES.DASHBOARD]?.tiers ?? [];

/** §7.1's tier 1, in declaration order: portfolio value, today's P&L, total P&L, drawdown. */
const TIER_ONE = TIERS.filter((entry) => entry.tier === 1);

/** One field's declaration. */
const fieldEntry = (field) => DASHBOARD_FIELDS.find((entry) => entry.field === field) ?? null;

/**
 * How each tier-1 figure is formatted. The ONLY per-field thing this page decides.
 *
 * Not in `pageFields.js` because a format is a rendering choice and that module holds none.
 * `precision: 2` on the three money figures is a balance; the drawdown is already in percent
 * units on the wire and `ds/Metric` does not multiply by 100 — a 3.2% drawdown rendered as
 * 320% would be read as a wiped-out account.
 */
const TIER_ONE_FORMAT = Object.freeze({
  portfolioValue: Object.freeze({ format: "currency", precision: 2 }),
  todayPnl: Object.freeze({ format: "currency", precision: 2 }),
  totalPnl: Object.freeze({ format: "currency", precision: 2 }),
  currentDrawdown: Object.freeze({ format: "percent", precision: 2 }),
});

/** A dotted path off a response body, or `undefined`. Every tier-1 path is a scalar. */
const readPath = (body, dottedPath) =>
  dottedPath.split(".").reduce(
    (node, key) => (node && typeof node === "object" ? node[key] : undefined),
    body,
  );

/**
 * `GET /api/dashboard` → the tier-1 view model: one `Reported<T>` per declared field.
 *
 * Nothing here defaults and nothing substitutes. A field the response did not carry becomes
 * the unavailable arm with the entry's own reason, which `ds/Metric` renders as the marker
 * plus that sentence — never as `0` (Requirements 14.5, 19.3). `floatVal` is deliberately
 * NOT used: it answers `0.00` for an absent field, which is the fabricated zero this whole
 * declaration exists to keep off the page.
 *
 * @param {unknown} body A resolved `GET /api/dashboard` body, or `null`.
 * @returns {Object<string, {available: boolean}>}
 */
const buildTierOne = (body) => {
  const model = {};
  for (const { key } of TIER_ONE) {
    const entry = fieldEntry(key);
    model[key] = fromNullable(readPath(body, entry.path), entry.reason ?? undefined);
  }
  return model;
};

/**
 * `overview.currency`, or `null`.
 *
 * The denomination of the money figures in the same `overview` block, rendered beside them
 * as `ds/Metric`'s `unit`. It replaces the `ov.currency || (env === "paper" ? "USD" :
 * "USDT")` fallback, which stated a denomination the response did not report. `ds/Metric`
 * renders a unit only beside a real figure, so an absent currency costs nothing.
 */
const readCurrency = (body) => {
  const value = readPath(body, "overview.currency");
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
};

/* ══════════════════════════════════════════════════════════════════════════
 * PAGE CHROME — the two controls that select what the one read asks for
 * ══════════════════════════════════════════════════════════════════════════ */

/** The two ledgers, and the `ds/Panel` environment each one declares. */
const LEDGERS = Object.freeze([
  Object.freeze({ value: "live", label: "Live", environment: "LIVE" }),
  Object.freeze({ value: "paper", label: "Paper", environment: "PAPER" }),
]);

/**
 * The equity window, which is a parameter of the ONE read (`equity_days`).
 *
 * The same five options the in-panel selector offered, with the same day counts, so no
 * period a trader had disappears — only the second request behind it.
 */
const PERIODS = Object.freeze([
  Object.freeze({ value: "1D", label: "1D", days: 1 }),
  Object.freeze({ value: "1W", label: "1W", days: 7 }),
  Object.freeze({ value: "1M", label: "1M", days: 30 }),
  Object.freeze({ value: "3M", label: "3M", days: 90 }),
  Object.freeze({ value: "ALL", label: "ALL", days: 365 }),
]);

const DEFAULT_PERIOD = "1M";

const periodOf = (value) => PERIODS.find((entry) => entry.value === value) ?? PERIODS[2];

/**
 * The one empty list, shared.
 *
 * Every zone's state starts here rather than at a fresh `[]`, so "no read has answered" has
 * a stable identity and a re-render does not change the input of every derived value.
 */
const NO_ROWS = Object.freeze([]);

const CHIP_CLASSES =
  "inline-flex cursor-pointer items-center rounded-sm border border-line-default px-2 py-0.5 "
  + "text-micro font-mono font-bold uppercase tracking-wide text-content-secondary "
  + "transition-colors hover:border-line-strong hover:text-content-primary "
  + "peer-checked:border-brand peer-checked:bg-brand-wash peer-checked:text-brand "
  // The radio is `sr-only`, so the focus ring is drawn on the chip the trader can see.
  + "peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 "
  + "peer-focus-visible:outline-brand";

/**
 * A labelled radio group rendered as chips.
 *
 * `Portfolio.jsx`'s control for the same job, in the same markup: a `<fieldset>` with a
 * `<legend>` and one `<input type="radio">` per option, which gets arrow-key movement,
 * single selection, one tab stop and a real label association from the browser. It replaces
 * this page's two hand-styled LIVE/PAPER `<button>`s, where the selected one was a control
 * that did nothing when pressed (Requirement 19.4), and the five timeframe buttons that were
 * the same shape. A shared `ds/` primitive is the right eventual home for it; §5's primitive
 * list does not have one yet, and adding one is not part of this task.
 */
function ChipRadioGroup({ legend, options, value, onChange, name }) {
  const groupId = useId();

  return (
    <fieldset className="flex min-w-0 flex-col gap-1 border-0 p-0">
      <legend className="p-0 text-micro font-medium uppercase tracking-wider text-content-secondary">
        {legend}
      </legend>
      <div className="flex items-center gap-1">
        {options.map((option) => {
          const optionId = `${groupId}-${option.value}`;
          return (
            <div key={option.value} className="relative">
              <input
                type="radio"
                id={optionId}
                name={`${groupId}-${name}`}
                value={option.value}
                checked={value === option.value}
                onChange={() => onChange(option.value)}
                className="peer sr-only"
              />
              <label htmlFor={optionId} className={CHIP_CLASSES}>
                {option.label}
              </label>
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE TIER-2 PROJECTIONS — PART B's zones, off part A's one read
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Lifted out of the old `loadDashboardData` unchanged, expression for expression, so that
 * the effect that runs them is small enough to read and so the zones they feed render
 * exactly what they rendered before. Their `?? 0` defaults and their substituted venue
 * names are part B's to remove — `pageFields`' `openPositions` and `recentOrders` entries
 * already say what each field really is.
 */

const readPositions = (body, environment) => {
  const raw = Array.isArray(body?.positions) ? body.positions : NO_ROWS;
  return raw.map((pos) => {
    const mType = pos.market_type || "spot";
    const side = (pos.side || "long").toLowerCase();
    const entryP = floatVal(pos.entry_price || 0);
    const markP = floatVal(pos.mark_price || entryP);
    const liqP = pos.liquidation_price != null ? floatVal(pos.liquidation_price) : null;

    return {
      id: pos.id || `${pos.exchange_id}_${pos.symbol}`,
      symbol: pos.symbol || "UNKNOWN",
      exchangeId: pos.exchange_id || (environment === "paper" ? "paper" : "binance"),
      side,
      marketType: mType,
      marginType: (pos.margin_type || "cross").toLowerCase(),
      contracts: floatVal(pos.contracts || pos.quantity || 0),
      entryPrice: entryP,
      markPrice: markP,
      unrealizedPnl: floatVal(pos.unrealized_pnl || pos.unrealized_pnl_usd || 0),
      unrealizedPnlPct: floatVal(pos.unrealized_pnl_pct || 0),
      leverage: pos.leverage ? parseInt(pos.leverage, 10) : 1,
      liquidationPrice: liqP,
      liquidationDistancePct: computeLiquidationDistance(markP, liqP, side, mType),
    };
  });
};

const readStrategies = (body) => (body?.strategies?.items || NO_ROWS).map((s) => ({
  id: s.id,
  name: s.name || "Automated Strategy",
  pair: s.pair || s.symbol || "BTC/USDT",
  status: s.status || "paused",
  health: s.health || "idle",
  errorMessage: s.error_message || s.error || s.reason || null,
  todayPnl: floatVal(s.today_pnl || 0.00),
  todayReturnPct: floatVal(s.today_return_pct || 0.00),
  lastSignalTime: s.last_signal_time
    ? new Date(s.last_signal_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "No signals",
}));

const readExecutions = (body, environment) => {
  const raw = Array.isArray(body?.executions) ? body.executions : NO_ROWS;
  return raw.slice(0, 5).map((e) => ({
    id: e.id,
    symbol: e.symbol || "BTC/USDT",
    exchangeId: e.exchange_id || (environment === "paper" ? "paper" : "binance"),
    side: (e.side || "buy").toLowerCase(),
    price: floatVal(e.price || 0),
    amount: floatVal(e.amount || 0),
    cost: floatVal(e.cost || (e.amount * e.price) || 0),
    fee: floatVal(e.fee || 0),
    realizedPnl: floatVal(e.realized_pnl || 0),
    timestamp: e.timestamp
      ? new Date(e.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
      : "Just now",
  }));
};

const readInsights = (body) => body?.recent_activity?.insights || NO_ROWS;

/** The warning-and-worse subset of the insights, as the banner's rows. */
const readCriticalAlerts = (insights) => insights
  .filter((ins) => ins.type === "warning" || ins.type === "error" || ins.type === "critical")
  .map((ins, index) => ({
    id: ins.id || `ins_${index}`,
    severity: ins.type === "error" ? "critical" : "warning",
    title: ins.type === "error" ? "Execution Alert" : "Risk Notice",
    message: ins.text,
    actionPath: ins.actionPath,
    actionText: ins.actionText,
    timestamp: "Active",
  }));

const readEquityCurve = (body) => (Array.isArray(body?.equity_curve) ? body.equity_curve : NO_ROWS)
  .map((row) => ({
    d: row.timestamp
      ? new Date(row.timestamp).toLocaleDateString("en-US", { month: "short", day: "numeric" })
      : "",
    v: parseFloat(row.equity ?? row.value ?? 0),
  }));

const readExchanges = (body) =>
  (Array.isArray(body?.exchange?.exchanges) ? body.exchange.exchanges : NO_ROWS);

export default function Dashboard() {
  const navigate = useNavigate();

  // The two page controls, and the only two things that select what the one read asks for.
  const [environment, setEnvironment] = useState("live");
  const [timeframe, setTimeframe] = useState(DEFAULT_PERIOD);
  const equityDays = periodOf(timeframe).days;

  const [showStatusModal, setShowStatusModal] = useState(false);
  const [wsStatus, setWsStatus] = useState("connected");

  // Authoritative sync timestamp to prevent stale WebSocket overwrites (2D.4)
  const lastSyncTimestampRef = useRef(Date.now());

  // Emergency Halt Modal State (P0.1 / 2D.5) — task 19.2's, untouched here
  const [showKillSwitchModal, setShowKillSwitchModal] = useState(false);
  const [killSwitchAction, setKillSwitchAction] = useState("activate"); // "activate" or "recover"
  const [isKillSwitchProcessing, setIsKillSwitchProcessing] = useState(false);
  const [killSwitchError, setKillSwitchError] = useState(null);

  // Operational Critical Alerts State (P0.2 / 2D.2)
  const [criticalAlerts, setCriticalAlerts] = useState(NO_ROWS);

  // Open Positions State (Zone 3 / P0.3 / 2D.3)
  const [positions, setPositions] = useState(NO_ROWS);

  // Active Strategies State (Zone 4 / P1.3)
  const [strategies, setStrategies] = useState(NO_ROWS);

  // Risk & Safety State (Zone 5)
  const [riskState, setRiskState] = useState(null);

  // Exchange Health State (Zone 6)
  const [exchangeConnections, setExchangeConnections] = useState(NO_ROWS);
  const [systemHealth, setSystemHealth] = useState(null);

  // Recent Executions State (Zone 7)
  const [executions, setExecutions] = useState(NO_ROWS);
  const [tradingInsights, setTradingInsights] = useState(NO_ROWS);

  // Equity Curve State (Zone 8)
  const [equityCurve, setEquityCurve] = useState(NO_ROWS);

  /*
   * THE ONE READ (§7.1).
   *
   * `usePanelState` rather than `usePolling`: the hook drops `data` on failure, so no zone
   * can render a value from a read that has since broken (Requirement 14.5), and its
   * `refetch` identity survives every payload, so nothing this page does re-creates a timer
   * (§1.12). `deps` is the question being asked — a different environment or a different
   * equity window is a NEW question, and the previous answer is discarded rather than shown
   * under the new controls.
   */
  const readDashboard = useCallback(
    () => dashboardApi.getDashboard({ environment, equity_days: equityDays }),
    [environment, equityDays],
  );
  const dashboard = usePanelState(readDashboard, { deps: [environment, equityDays] });
  // `payload`, not `data`: the WebSocket handlers below already name their frame `data`, and
  // one identifier meaning "the REST body" in one scope and "this tick" in another is how a
  // tick ends up written where a read belongs.
  const { data: payload, error: readError, refetch, state: readState } = dashboard;

  /*
   * THE PROJECTION — one payload, one place it is read (Requirement 14.5).
   *
   * `data` is `null` in every state except `ready` and `refreshing`, and the null arm clears
   * every zone rather than leaving the previous read's rows behind: a figure from a read that
   * has since failed is exactly the cached-as-live rendering Requirement 14.5 forbids, and
   * `usePanelState` has already dropped the payload at the hook. The WebSocket handlers below
   * patch these same view models, which is why they are state and not memos — task 19.3 moves
   * those subscriptions down into the leaves that render the value.
   */
  useEffect(() => {
    if (payload === null || payload === undefined) {
      setPositions(NO_ROWS);
      setStrategies(NO_ROWS);
      setExecutions(NO_ROWS);
      setTradingInsights(NO_ROWS);
      setCriticalAlerts(NO_ROWS);
      setEquityCurve(NO_ROWS);
      setExchangeConnections(NO_ROWS);
      setRiskState(null);
      setSystemHealth(null);
      return;
    }

    // The freshness floor every WebSocket frame is measured against (2D.4).
    lastSyncTimestampRef.current = Date.now();

    const insights = readInsights(payload);
    setPositions(readPositions(payload, environment));
    setStrategies(readStrategies(payload));
    setExecutions(readExecutions(payload, environment));
    setTradingInsights(insights.slice(0, 3));
    setCriticalAlerts(readCriticalAlerts(insights));
    setEquityCurve(readEquityCurve(payload));
    setExchangeConnections(readExchanges(payload));
    setRiskState(payload.risk ?? null);
    setSystemHealth(payload.health ?? null);
  }, [payload, environment]);

  /** Tier 1's four figures, as `Reported<T>`s. A `null` payload is four markers, not zeros. */
  const tierOne = useMemo(() => buildTierOne(payload), [payload]);

  /** The denomination the server reported, or `null`. Never a guessed one. */
  const currency = useMemo(() => readCurrency(payload), [payload]);

  // Real-time WebSocket Subscriptions & Reconnect Reconciliation (2D.4)
  useEffect(() => {
    // Helper to filter out stale WebSocket events
    const isEventFresh = (data) => {
      if (!data) return true;
      if (data.environment && data.environment !== environment) return false;
      if (data.timestamp) {
        const eventTime = new Date(data.timestamp).getTime();
        if (!isNaN(eventTime) && eventTime < lastSyncTimestampRef.current - 1000) {
          console.warn("[WS/Dashboard] Discarding stale event:", data);
          return false;
        }
      }
      return true;
    };

    // 1. Reconnect & Open Reconciliation Handler (2D.4)
    // `refetch` re-issues the ONE read. Its identity survives every payload, so this effect
    // is not re-run by a read completing and the subscriptions are established once.
    const unsubOpen = typeof wsClient.onOpen === "function"
      ? wsClient.onOpen(() => {
          console.log("[WS/Dashboard] Connection re-established. Reconciling with authoritative server state...");
          refetch();
        })
      : null;

    // 2. Connection Status Tracker
    const unsubStatus = typeof wsClient.onStatusChange === "function"
      ? wsClient.onStatusChange((status) => {
          setWsStatus(status);
        })
      : null;

    // 3. Risk Kill Switch Activated Event
    const unsubRiskActivated = wsClient.subscribe("risk.kill_switch_activated", (data) => {
      if (!isEventFresh(data)) return;
      console.log("[WS/Dashboard] Risk kill switch activated event received:", data);
      setRiskState(prev => ({
        ...prev,
        kill_switch_active: true,
        risk_level: "blocked"
      }));
      setCriticalAlerts(prev => [
        {
          id: `ks_${Date.now()}`,
          severity: "critical",
          title: "Emergency Kill Switch Activated",
          message: data?.message || "Emergency Kill Switch is ACTIVE. All trading executions are halted.",
          timestamp: "Just now",
          actionPath: "/app/risk",
          actionText: "Risk Controls"
        },
        ...prev
      ]);
    });

    // 4. Risk Kill Switch Recovered Event
    const unsubRiskRecovered = wsClient.subscribe("risk.kill_switch_recovered", (data) => {
      if (!isEventFresh(data)) return;
      console.log("[WS/Dashboard] Risk kill switch recovered event received");
      setRiskState(prev => ({
        ...prev,
        kill_switch_active: false,
        risk_level: "low"
      }));
      setCriticalAlerts(prev => prev.filter(a => !a.title.includes("Kill Switch")));
    });

    // 5. Strategy Status Changes
    const unsubStrategy = wsClient.subscribe("strategy_status", (data) => {
      if (!isEventFresh(data)) return;
      if (data?.strategy_id) {
        setStrategies(prev => prev.map(s => s.id === data.strategy_id ? {
          ...s,
          status: data.status || s.status,
          health: data.health || s.health,
          errorMessage: data.error || s.errorMessage
        } : s));
      }
    });

    // 6. Exchange Health Changes
    const unsubExchange = wsClient.subscribe("exchange_health", (data) => {
      if (!isEventFresh(data)) return;
      if (data?.exchange_id) {
        setExchangeConnections(prev => prev.map(ex => (ex.exchange_id === data.exchange_id || ex.id === data.exchange_id) ? {
          ...ex,
          ...data
        } : ex));
      }
    });

    // 7. Critical Notifications Broadcast
    const unsubNotif = wsClient.subscribe("notification", (notif) => {
      if (!isEventFresh(notif)) return;
      if (notif?.severity === "critical" || notif?.severity === "warning") {
        setCriticalAlerts(prev => [
          {
            id: notif.id || `notif_${Date.now()}`,
            severity: notif.severity,
            title: notif.title || "Trading Alert",
            message: notif.message,
            timestamp: "Just now"
          },
          ...prev.slice(0, 4)
        ]);
      }
    });

    return () => {
      if (unsubOpen) unsubOpen();
      if (unsubStatus) unsubStatus();
      if (unsubRiskActivated) unsubRiskActivated();
      if (unsubRiskRecovered) unsubRiskRecovered();
      if (unsubStrategy) unsubStrategy();
      if (unsubExchange) unsubExchange();
      if (unsubNotif) unsubNotif();
    };
  }, [environment, refetch]);

  /*
   * The two page controls.
   *
   * `handleTimeframeChange` used to be an async second read: it called `getDashboard` again
   * with a different `equity_days` and wrote `equityCurve` from the result, swallowing its
   * own failure into a `console.error`. The period is now a dependency of the one read, so
   * changing it re-asks the whole question and its failure is the page's one failure state.
   */
  const handleEnvironmentChange = useCallback((next) => setEnvironment(next), []);
  const handlePeriodChange = useCallback((next) => setTimeframe(next), []);

  // Strategy pause/resume handler
  const handleToggleStrategy = (id) => {
    setStrategies(prev => prev.map(s => {
      if (s.id === id) {
        const newStatus = s.status === "active" || s.status === "running" ? "paused" : "active";
        return {
          ...s,
          status: newStatus,
          health: newStatus === "active" ? "healthy" : "idle"
        };
      }
      return s;
    }));
  };

  // Emergency Halt / Kill Switch Actions (P0.1 / 2D.5)
  const handleConfirmKillSwitchAction = async () => {
    setIsKillSwitchProcessing(true);
    setKillSwitchError(null);
    try {
      if (killSwitchAction === "activate") {
        const res = await riskApi.killSwitch({
          reason: `Emergency manual halt triggered from Dashboard (${environment.toUpperCase()})`
        });
        if (res && res.kill_switch_active) {
          setRiskState(prev => ({
            ...prev,
            kill_switch_active: true,
            risk_level: "blocked"
          }));
          setShowKillSwitchModal(false);
        }
      } else {
        const res = await riskApi.recoverKillSwitch();
        if (res && !res.kill_switch_active) {
          setRiskState(prev => ({
            ...prev,
            kill_switch_active: false,
            risk_level: "low"
          }));
          setShowKillSwitchModal(false);
        }
      }
    } catch (err) {
      console.error("Kill switch operation failed:", err);
      setKillSwitchError(err?.response?.data?.message || err?.message || "Operation failed. Please check network or risk settings.");
    } finally {
      setIsKillSwitchProcessing(false);
    }
  };

  // The two risk readings the panels below still report. `risk_level` and `risk_score` left
  // with the Risk Guard hero card: both were rendered with a substituted default — `"low"`
  // and `25` for an account nothing had been read for — and neither is a §7.1 field.
  const isCircuitBreakerArmed = riskState?.circuit_breaker_armed ?? true;
  const isKillSwitchActive = riskState?.kill_switch_active ?? false;

  /*
   * THE PAGE'S THREE RENDERINGS, from the one read's state.
   *
   * `error` and `unauthorised` are the failure: one read failed, so the page has one failure
   * state (§7.1). `empty` is NOT among them — a 2xx that carried no account state is a
   * successful read of nothing, and it renders the four tier-1 markers with their declared
   * reasons rather than a failure the server did not report. `refreshing` keeps the previous
   * figures on screen with `ds/Panel`'s inline affordance, and §11.1 makes it reachable only
   * from `ready`, so it can never be a stale render over a failure.
   */
  const isReading = readState === PANEL_STATES.LOADING
    || readState === PANEL_STATES.IDLE
    || readState === PANEL_STATES.REFRESHING;
  const pageFailed = readState === PANEL_STATES.ERROR
    || readState === PANEL_STATES.UNAUTHORISED;
  const tierOneState = readState === PANEL_STATES.IDLE || readState === PANEL_STATES.LOADING
    ? PANEL_STATES.LOADING
    : (readState === PANEL_STATES.REFRESHING ? PANEL_STATES.REFRESHING : PANEL_STATES.READY);

  /** The ledger's `ds/Panel` / `ds/TradingEnvironmentBadge` environment (§8.2). */
  const panelEnvironment = (LEDGERS.find((entry) => entry.value === environment) ?? LEDGERS[0])
    .environment;

  return (
    // The page shell, on tokens: `bg-surface-canvas` and `text-content-primary` replace the
    // `#080a0e` / `#e2e8f0` pair this page set by hand, and the `1.25rem 1.75rem` padding
    // becomes `p-5` so the spacing comes from the scale rather than from a literal.
    //
    // Task 3.3's TEMPORARY page-level `font-mono` moved down to the tier-2 grid, which is
    // the part of the page that inherited mono from the old shell wrapper. This component
    // also serves /app/live-trading until task 20.1 gives that route its own page.
    <div className="flex min-w-0 flex-col gap-4 overflow-y-auto bg-surface-canvas p-5 text-content-primary">

      {/* ═══ PAGE CHROME — the title, the environment switch, the period, the refresh ═══
          §7.1's header row. The environment switch and the period are the two inputs of
          the ONE read, which is why both live here rather than beside the figures they
          change: `equity_days` is a parameter of `GET /api/dashboard`, not a chart
          setting. `PageHeader` renders the page's only `<h1>` and, from `environment`,
          the `TradingEnvironmentBadge` that replaces the two hand-styled
          REAL CAPITAL ACTIVE / SIMULATED EXECUTION spans (§8.2). */}
      <PageHeader
        title="Command Center"
        subtitle="Capital, exposure and execution across this account"
        environment={panelEnvironment}
        meta={(
          <div className="flex items-center gap-3">
            <ChipRadioGroup
              legend="Environment"
              name="environment"
              options={LEDGERS}
              value={environment}
              onChange={handleEnvironmentChange}
            />
            <ChipRadioGroup
              legend="Equity period"
              name="period"
              options={PERIODS}
              value={timeframe}
              onChange={handlePeriodChange}
            />
          </div>
        )}
        actions={(
          <CommandButton
            intent="secondary"
            icon={RefreshCw}
            loading={isReading}
            loadingLabel="Refreshing"
            onClick={refetch}
          >
            Refresh
          </CommandButton>
        )}
      />
      {/* ═══ OPERATIONAL CONTROLS ══════════════════════════════════════════════════
          The kill switch and the diagnostics popover, unchanged. The kill switch is a risk
          control: task 19.2 routes it through `ds/ConfirmDialog` and Requirement 19.1
          forbids touching its logic, so it keeps its trigger, its confirmation and its two
          `riskApi` calls exactly as they are and only its container moved. The diagnostics
          popover reports three tier-2 fields — `health.exchange_api_latency_ms`,
          `health.order_state_sync_status` and the connected-venue count — which part B
          folds into §7.1's System & exchange health panel. It sits outside `PageHeader`
          because that primitive clips overflow to hold the fixed 64px route header
          (Requirement 2.2), and an absolutely positioned popover inside it would be cut off. */}
      <div className="relative flex flex-wrap items-center justify-end gap-2">

          {/* P0.1 EMERGENCY HALT / RESUME BUTTON */}
          {!isKillSwitchActive ? (
            <button
              onClick={() => {
                setKillSwitchAction("activate");
                setKillSwitchError(null);
                setShowKillSwitchModal(true);
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.375rem",
                padding: "0.375rem 0.875rem",
                background: "rgba(239, 68, 68, 0.15)",
                border: "1px solid #ef4444",
                borderRadius: 8,
                color: "#ef4444",
                fontSize: "0.75rem",
                fontWeight: 700,
                cursor: "pointer",
                transition: "all 0.15s ease"
              }}
            >
              <ShieldAlert size={14} />
              EMERGENCY HALT
            </button>
          ) : (
            <button
              onClick={() => {
                setKillSwitchAction("recover");
                setKillSwitchError(null);
                setShowKillSwitchModal(true);
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.375rem",
                padding: "0.375rem 0.875rem",
                background: "rgba(234, 179, 8, 0.2)",
                border: "1px solid #eab308",
                borderRadius: 8,
                color: "#eab308",
                fontSize: "0.75rem",
                fontWeight: 700,
                cursor: "pointer"
              }}
            >
              <AlertOctagon size={14} />
              RESUME TRADING
            </button>
          )}

          {/* Diagnostics Popover Trigger */}
          <div style={{ position: "relative" }}>
            <button
              onClick={() => setShowStatusModal(!showStatusModal)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                padding: "0.375rem 0.875rem",
                background: isKillSwitchActive ? "rgba(239,68,68,0.15)" : "#0f141c",
                border: `1px solid ${isKillSwitchActive ? "#ef4444" : "#1e293b"}`,
                borderRadius: 8,
                color: isKillSwitchActive ? "#ef4444" : "#94a3b8",
                fontSize: "0.75rem",
                fontWeight: 600,
                cursor: "pointer"
              }}
            >
              <span style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                background: isKillSwitchActive ? "#ef4444" : wsStatus === "connected" ? "#10b981" : "#eab308",
                boxShadow: isKillSwitchActive ? "0 0 6px #ef4444" : wsStatus === "connected" ? "0 0 6px #10b981" : "0 0 6px #eab308"
              }} />
              <span>{isKillSwitchActive ? "Trading Blocked" : wsStatus === "connected" ? "Engine Operational" : "Stream Connecting"}</span>
            </button>

            {/* Diagnostics Popover Modal */}
            {showStatusModal && (
              <div style={{
                position: "absolute",
                top: 38,
                right: 0,
                width: 300,
                background: "#0b0f17",
                border: "1px solid #1e293b",
                borderRadius: 10,
                padding: "1rem",
                boxShadow: "0 20px 25px -5px rgba(0,0,0,0.7)",
                zIndex: 100
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
                  <span style={{ fontSize: "0.6875rem", fontWeight: 700, color: "#64748b", textTransform: "uppercase" }}>
                    Diagnostics ({environment.toUpperCase()})
                  </span>
                  <button
                    onClick={() => setShowStatusModal(false)}
                    style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer", fontSize: "0.875rem" }}
                  >
                    ✕
                  </button>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: "0.625rem", fontSize: "0.75rem" }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#94a3b8" }}>Exchange API Latency</span>
                    <span style={{
                      color: systemHealth?.exchange_api_latency_ms != null ? "#10b981" : "#94a3b8",
                      fontWeight: 600
                    }}>
                      {systemHealth?.exchange_api_latency_ms != null
                        ? `${systemHealth.exchange_api_latency_ms} ms (${systemHealth.exchange_api_latency_status || "optimal"})`
                        : "Latency unavailable"}
                    </span>
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#94a3b8" }}>Real-time Stream</span>
                    <span style={{ color: wsStatus === "connected" ? "#10b981" : "#eab308", fontWeight: 600, textTransform: "capitalize" }}>
                      {wsStatus === "connected" ? "Live Connected" : "Reconnecting..."}
                    </span>
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#94a3b8" }}>Circuit Breaker</span>
                    <span style={{ color: isCircuitBreakerArmed ? "#10b981" : "#ef4444", fontWeight: 600 }}>
                      {isCircuitBreakerArmed ? "Armed / 0 Breaches" : "Triggered"}
                    </span>
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#94a3b8" }}>Order State Sync</span>
                    <span style={{ color: "#10b981", fontWeight: 600 }}>
                      {systemHealth?.order_state_sync_status || "Synchronized"}
                    </span>
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#94a3b8" }}>Connected Venues</span>
                    <span style={{ color: "#f8fafc", fontWeight: 600 }}>
                      {exchangeConnections.length} Active
                    </span>
                  </div>
                </div>
              </div>
            )}
          </div>
      </div>

      {/* ── P0.1 / 2D.5 EMERGENCY KILL SWITCH CONFIRMATION MODAL ───────────────── */}
      {showKillSwitchModal && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: "rgba(0, 0, 0, 0.75)",
          backdropFilter: "blur(4px)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1000,
          padding: "1rem"
        }}>
          <div style={{
            background: "#0c1017",
            border: `1px solid ${killSwitchAction === "activate" ? "#ef4444" : "#eab308"}`,
            borderRadius: 14,
            padding: "1.5rem",
            maxWidth: 480,
            width: "100%",
            boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.8)"
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "1rem" }}>
              <div style={{
                padding: "0.5rem",
                borderRadius: "50%",
                background: killSwitchAction === "activate" ? "rgba(239, 68, 68, 0.2)" : "rgba(234, 179, 8, 0.2)"
              }}>
                <ShieldAlert size={24} color={killSwitchAction === "activate" ? "#ef4444" : "#eab308"} />
              </div>
              <div>
                <h3 style={{ fontSize: "1rem", fontWeight: 800, color: "#f8fafc", margin: 0 }}>
                  {killSwitchAction === "activate" ? "Activate Emergency Kill Switch" : "Deactivate Emergency Kill Switch"}
                </h3>
                <span style={{ fontSize: "0.6875rem", color: "#94a3b8" }}>
                  Environment Context: <strong style={{ color: environment === "live" ? "#10b981" : "#818cf8" }}>
                    {environment === "live" ? "LIVE TRADING (REAL CAPITAL)" : "PAPER SIMULATION"}
                  </strong>
                </span>
              </div>
            </div>

            <p style={{ fontSize: "0.8125rem", color: "#cbd5e1", lineHeight: 1.5, marginBottom: "1.25rem" }}>
              {killSwitchAction === "activate" ? (
                environment === "live" ? (
                  "⚠️ WARNING: Activating the Emergency Kill Switch will IMMEDIATELY halt all live strategy execution loops and block all new order submissions on connected live exchanges."
                ) : (
                  "Activating the Emergency Kill Switch will freeze all paper simulation bots and prevent new simulated trades."
                )
              ) : (
                "Deactivating the Emergency Kill Switch will resume normal algorithmic execution and order submissions."
              )}
            </p>

            {killSwitchError && (
              <div style={{
                padding: "0.625rem",
                background: "rgba(239, 68, 68, 0.15)",
                border: "1px solid #ef4444",
                borderRadius: 6,
                fontSize: "0.75rem",
                color: "#f8fafc",
                marginBottom: "1rem"
              }}>
                {killSwitchError}
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem" }}>
              <button
                onClick={() => setShowKillSwitchModal(false)}
                disabled={isKillSwitchProcessing}
                style={{
                  padding: "0.5rem 1rem",
                  background: "#1e293b",
                  border: "none",
                  borderRadius: 8,
                  color: "#94a3b8",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                Cancel
              </button>

              <button
                onClick={handleConfirmKillSwitchAction}
                disabled={isKillSwitchProcessing}
                style={{
                  padding: "0.5rem 1.25rem",
                  background: killSwitchAction === "activate" ? "#ef4444" : "#eab308",
                  border: "none",
                  borderRadius: 8,
                  color: killSwitchAction === "activate" ? "#fff" : "#000",
                  fontSize: "0.75rem",
                  fontWeight: 700,
                  cursor: isKillSwitchProcessing ? "not-allowed" : "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "0.375rem"
                }}
              >
                {isKillSwitchProcessing ? (
                  <>
                    <RefreshCw size={13} style={{ animation: "spin 1s linear infinite" }} />
                    Processing...
                  </>
                ) : (
                  killSwitchAction === "activate" ? "Yes, HALT TRADING IMMEDIATELY" : "Resume Operations"
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ═══ THE ONE FAILURE STATE (§7.1, Requirements 3.6, 14.5) ══════════════════
          One read means one failure, so the whole body is replaced by ONE `ds/ErrorState`
          with retry rather than by a per-panel error in each zone: per-panel errors would
          imply independent reads that do not exist. Because this is a branch and not a
          banner, no zone, table or figure exists in the DOM at all while the read is
          broken — there is no markup that could render the previous payload under an error
          indicator. `ds/ErrorState` reads only `translateError` output, and offers the
          retry only when the failure is retryable (a 503 is; an expired session is not). */}
      {pageFailed ? (
        <ErrorState
          error={readError}
          context="dashboard"
          onRetry={refetch}
          data-region="page-error"
        />
      ) : (
        <>

      {/* ── P0.2 / 2D.2 HIGH-VISIBILITY CRITICAL OPERATIONAL ALERT BANNER ──────── */}
      {(isKillSwitchActive || !isCircuitBreakerArmed || criticalAlerts.length > 0) && (
        <div style={{
          display: "flex",
          flexDirection: "column",
          gap: "0.5rem",
          marginBottom: "1.25rem"
        }}>
          {/* Active Kill Switch Alert */}
          {isKillSwitchActive && (
            <div style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "0.75rem 1.25rem",
              background: "rgba(239, 68, 68, 0.15)",
              border: "1px solid #ef4444",
              borderRadius: 10
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                <ShieldAlert size={20} color="#ef4444" />
                <div>
                  <div style={{ fontSize: "0.8125rem", fontWeight: 800, color: "#f8fafc" }}>
                    EMERGENCY KILL SWITCH ACTIVE — ALL EXECUTIONS HALTED
                  </div>
                  <div style={{ fontSize: "0.6875rem", color: "#94a3b8", marginTop: 2 }}>
                    All algorithmic order placements are blocked by institutional safety guard.
                  </div>
                </div>
              </div>
              <button
                onClick={() => {
                  setKillSwitchAction("recover");
                  setKillSwitchError(null);
                  setShowKillSwitchModal(true);
                }}
                style={{
                  padding: "4px 12px",
                  background: "#eab308",
                  border: "none",
                  borderRadius: 6,
                  color: "#000",
                  fontSize: "0.6875rem",
                  fontWeight: 700,
                  cursor: "pointer"
                }}
              >
                Resume Trading
              </button>
            </div>
          )}

          {/* Circuit Breaker Breach Alert */}
          {!isCircuitBreakerArmed && (
            <div style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "0.75rem 1.25rem",
              background: "rgba(234, 179, 8, 0.15)",
              border: "1px solid #eab308",
              borderRadius: 10
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                <AlertTriangle size={20} color="#eab308" />
                <div>
                  <div style={{ fontSize: "0.8125rem", fontWeight: 800, color: "#f8fafc" }}>
                    RISK CIRCUIT BREAKER TRIGGERED
                  </div>
                  <div style={{ fontSize: "0.6875rem", color: "#94a3b8", marginTop: 2 }}>
                    Daily loss threshold or max drawdown reached. Review open risk parameters.
                  </div>
                </div>
              </div>
              <button
                onClick={() => navigate("/app/risk")}
                style={{
                  padding: "4px 12px",
                  background: "#1e293b",
                  border: "1px solid #334155",
                  borderRadius: 6,
                  color: "#f8fafc",
                  fontSize: "0.6875rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                Review Risk Settings
              </button>
            </div>
          )}

          {/* Dynamic Execution / Order Failure Alerts */}
          {criticalAlerts.map(alert => (
            <div
              key={alert.id}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "0.625rem 1.25rem",
                background: alert.severity === "critical" ? "rgba(239, 68, 68, 0.12)" : "rgba(234, 179, 8, 0.12)",
                border: `1px solid ${alert.severity === "critical" ? "#ef4444" : "#eab308"}`,
                borderRadius: 8
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "0.625rem" }}>
                <AlertTriangle size={16} color={alert.severity === "critical" ? "#ef4444" : "#eab308"} />
                <span style={{ fontSize: "0.75rem", color: "#f8fafc", fontWeight: 600 }}>
                  {alert.message}
                </span>
              </div>
              {alert.actionPath && (
                <button
                  onClick={() => navigate(alert.actionPath)}
                  style={{
                    background: "none",
                    border: "none",
                    color: "#38bdf8",
                    fontSize: "0.6875rem",
                    fontWeight: 700,
                    cursor: "pointer"
                  }}
                >
                  {alert.actionText || "View Details"} →
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ═══ TIER 1 — Requirements 3.1 and 3.4 (task 19.1) ═════════════════════════
          ONE container, four figures, and no second row of equally-weighted cards.

          Requirement 3.4 is satisfied STRUCTURALLY, not by review: the row is rendered by
          walking `design/pageHierarchy.js`'s tier-1 list, so a figure cannot appear here
          without being declared and cannot be declared twice; `Metric tier={1}` appears
          nowhere else on this page; and `data-page-tier="1"` marks the single container
          Property 4 (task 19.5) asserts every tier-1 figure is inside.

          The five hero cards this replaces were Total Equity, Available Liquidity, Today's
          Total P&L, Market Exposure and Risk Guard State — five equally weighted cards in a
          `repeat(auto-fit, minmax(200px, 1fr))` grid that wrapped to a second row below
          about 1100px, which is the two-rows-of-tier-1 arrangement Requirement 3.4 rules
          out. Liquidity and exposure are §7.6's figures and are on Portfolio; the risk guard
          state stays on this page in the Risk & Safety panel below, where it was already
          reported. Every figure that was `?? 0` is now a declared `Reported<T>`: absent
          renders the marker with the field's own reason, never a zero (Requirement 14.5). */}
      <Panel
        title="Account summary"
        money
        environment={panelEnvironment}
        state={tierOneState}
        loading={{ kind: "skeleton-metric", rows: 1, columns: 4 }}
        data-region="tier-1"
      >
        <div
          {...{ [TIER_PAGE_ATTRIBUTE]: PAGES.DASHBOARD, [TIER_ATTRIBUTE]: 1 }}
          className="grid grid-cols-4 gap-4"
        >
          {TIER_ONE.map(({ key, label }) => {
            const { format, precision } = TIER_ONE_FORMAT[key];
            return (
              <Metric
                key={key}
                tier={1}
                label={label}
                value={tierOne[key]}
                format={format}
                precision={precision}
                // The denomination as the server reported it, and only beside a real figure.
                unit={format === "currency" ? currency ?? undefined : undefined}
                hint={fieldEntry(key)?.tooltip ?? undefined}
              />
            );
          })}
        </div>
      </Panel>

      {/* ── COCKPIT MAIN LAYOUT GRID (LEFT 65% / RIGHT 35%) — PART B's tier 2 ──────
          `font-mono` moved here from the page root (task 3.3's temporary page-level mono).
          It is the zones below that inherited mono from the old shell wrapper — the two
          tables and their figure rows — and holding it on this container keeps them looking
          as they do today while the header and tier 1 render in the shell's Inter, which is
          what `ds/PageHeader` and `ds/Metric` are built for. Part B removes it entirely when
          these tables become `ds/DataTable`, which puts mono on the numeric cells that
          should carry it. */}
      <div className="font-mono" style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1.8fr) minmax(0, 1.2fr)",
        gap: "1.25rem",
        marginBottom: "1.5rem"
      }}>

        {/* ── LEFT COLUMN: POSITIONS, PERFORMANCE & EXECUTIONS ───────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem", minWidth: 0 }}>

          {/* ── ZONE 3 / P0.3 / 2D.3: OPEN POSITIONS LIVE TABLE ────────────── */}
          <div style={{
            background: "#0c1017",
            border: "1px solid #1e293b",
            borderRadius: 12,
            padding: "1.25rem"
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "0.75rem"
            }}>
              <div>
                <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Open Positions ({positions.length})
                </h2>
                <span style={{ fontSize: "0.6875rem", color: "#64748b" }}>Live mark-to-market valuations</span>
              </div>

              <button
                onClick={() => navigate("/app/portfolio")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.25rem",
                  background: "none",
                  border: "none",
                  color: "#38bdf8",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                View all in Portfolio <ArrowRight size={12} />
              </button>
            </div>

            {positions.length === 0 ? (
              <div style={{
                padding: "2rem 1rem",
                textAlign: "center",
                background: "#080a0e",
                borderRadius: 8,
                border: "1px dashed #1e293b"
              }}>
                <p style={{ fontSize: "0.8125rem", color: "#94a3b8", margin: 0, fontWeight: 500 }}>
                  No open positions currently held
                </p>
                <p style={{ fontSize: "0.6875rem", color: "#64748b", marginTop: "0.25rem" }}>
                  Deploy an automated strategy or connect exchange keys to begin trading.
                </p>
                <button
                  onClick={() => navigate("/app/strategies")}
                  style={{
                    marginTop: "0.75rem",
                    padding: "4px 12px",
                    background: "#1e293b",
                    border: "1px solid #334155",
                    borderRadius: 6,
                    color: "#f8fafc",
                    fontSize: "0.6875rem",
                    fontWeight: 600,
                    cursor: "pointer"
                  }}
                >
                  View Strategy Hub
                </button>
              </div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", minWidth: 780, borderCollapse: "collapse", fontSize: "0.75rem" }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid #1e293b", color: "#64748b", textAlign: "left" }}>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600 }}>Symbol / Market</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600 }}>Venue</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600 }}>Mode</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600 }}>Side</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Contracts</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Entry</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Mark</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>uPnL</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Liq. Price</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Liq. Dist</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map(pos => {
                      const isDeriv = pos.marketType === "future" || pos.marketType === "swap" || pos.liquidationPrice != null;
                      const dist = pos.liquidationDistancePct;

                      return (
                        <tr key={pos.id} style={{ borderBottom: "1px solid rgba(30,41,59,0.5)" }}>
                          <td style={{ padding: "0.625rem 0.75rem", color: "#f8fafc", fontWeight: 700 }}>
                            {pos.symbol}
                            <span style={{ fontSize: "0.625rem", color: "#64748b", marginLeft: "0.375rem", textTransform: "uppercase" }}>
                              {pos.marketType}
                            </span>
                          </td>
                          <td style={{ padding: "0.625rem 0.75rem" }}>
                            <span style={{
                              fontSize: "0.625rem",
                              padding: "2px 6px",
                              borderRadius: 4,
                              background: "#0f172a",
                              border: "1px solid #334155",
                              color: "#cbd5e1",
                              textTransform: "uppercase",
                              fontWeight: 700
                            }}>
                              {pos.exchangeId}
                            </span>
                          </td>
                          <td style={{ padding: "0.625rem 0.75rem" }}>
                            <span style={{
                              fontSize: "0.625rem",
                              color: "#94a3b8",
                              fontWeight: 600,
                              textTransform: "uppercase"
                            }}>
                              {isDeriv ? pos.marginType : "SPOT"}
                            </span>
                          </td>
                          <td style={{ padding: "0.625rem 0.75rem" }}>
                            <span style={{
                              fontSize: "0.625rem",
                              padding: "2px 6px",
                              borderRadius: 4,
                              background: pos.side === "long" ? "rgba(16,185,129,0.15)" : "rgba(239,68,68,0.15)",
                              color: pos.side === "long" ? "#10b981" : "#ef4444",
                              fontWeight: 700,
                              textTransform: "uppercase"
                            }}>
                              {pos.side} {pos.leverage > 1 ? `${pos.leverage}x` : ""}
                            </span>
                          </td>
                          <td style={{ padding: "0.625rem 0.75rem", textAlign: "right", color: "#f8fafc", fontWeight: 600 }}>
                            {pos.contracts}
                          </td>
                          <td style={{ padding: "0.625rem 0.75rem", textAlign: "right", color: "#94a3b8" }}>
                            ${Number(pos?.entryPrice ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
                          </td>
                          <td style={{ padding: "0.625rem 0.75rem", textAlign: "right", color: "#f8fafc", fontWeight: 600 }}>
                            ${Number(pos?.markPrice ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
                          </td>
                          <td style={{
                            padding: "0.625rem 0.75rem",
                            textAlign: "right",
                            fontWeight: 700,
                            color: pos.unrealizedPnl >= 0 ? "#10b981" : "#ef4444"
                          }}>
                            {pos.unrealizedPnl >= 0 ? "+" : ""}${pos.unrealizedPnl.toFixed(2)}
                            <span style={{ fontSize: "0.625rem", display: "block" }}>
                              ({pos.unrealizedPnlPct >= 0 ? "+" : ""}{pos.unrealizedPnlPct}%)
                            </span>
                          </td>
                          <td style={{ padding: "0.625rem 0.75rem", textAlign: "right", color: "#64748b" }}>
                            {pos.liquidationPrice != null ? `$${pos.liquidationPrice.toFixed(2)}` : "—"}
                          </td>
                          <td style={{ padding: "0.625rem 0.75rem", textAlign: "right", fontWeight: 700 }}>
                            {dist != null ? (
                              <span style={{
                                color: dist < 10 ? "#ef4444" : dist < 20 ? "#eab308" : "#10b981"
                              }}>
                                {dist < 10 ? `⚠️ ${dist.toFixed(1)}%` : `${dist.toFixed(1)}%`}
                              </span>
                            ) : (
                              <span style={{ color: "#64748b" }}>—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* ── ZONE 8: EQUITY PERFORMANCE CURVE ───────────────────────────── */}
          <div style={{
            background: "#0c1017",
            border: "1px solid #1e293b",
            borderRadius: 12,
            padding: "1.25rem"
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "0.75rem"
            }}>
              <div>
                <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  {/* The denomination only when the server reported one: the previous title
                      printed `USDT` for live and `USD` for paper whether or not `overview`
                      carried a currency. */}
                  Equity Trajectory{currency === null ? "" : ` (${currency})`}
                </h2>
                <span style={{ fontSize: "0.6875rem", color: "#64748b" }}>Historical NAV progression</span>
              </div>

              {/* The period selector stood here. It is in `PageHeader` now, because
                  `equity_days` is a parameter of the page's one read: this control issued a
                  SECOND `getDashboard` call for the same account and wrote the series from
                  it, which is two reads and two failure surfaces for one figure (§7.1). */}
            </div>

            <div style={{ height: 180, position: "relative" }}>
              {equityCurve.length === 0 ? (
                <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: "0.375rem", color: "#475569" }}>
                  <BarChart2 size={24} style={{ opacity: 0.4 }} />
                  <span style={{ fontSize: "0.75rem" }}>No historical curve points yet.</span>
                </div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={equityCurve}>
                    <defs>
                      <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#0284c7" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#0284c7" stopOpacity={0.0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="d" stroke="#334155" fontSize={10} tickLine={false} axisLine={false} />
                    <YAxis domain={["auto", "auto"]} hide />
                    <Tooltip
                      contentStyle={{ background: "#090d16", border: "1px solid #1e293b", borderRadius: 6, fontSize: "0.6875rem" }}
                      formatter={(val) => [`$${Number(val ?? 0).toLocaleString()}`, "Equity"]}
                    />
                    <Area type="monotone" dataKey="v" stroke="#0284c7" strokeWidth={2} fill="url(#equityGrad)" />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* ── ZONE 7: RECENT EXECUTIONS LIVE TABLE ───────────────────────── */}
          <div style={{
            background: "#0c1017",
            border: "1px solid #1e293b",
            borderRadius: 12,
            padding: "1.25rem"
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "0.75rem"
            }}>
              <div>
                <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Recent Order Executions ({executions.length})
                </h2>
                <span style={{ fontSize: "0.6875rem", color: "#64748b" }}>Audited exchange fills</span>
              </div>

              <button
                onClick={() => navigate("/app/trades")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.25rem",
                  background: "none",
                  border: "none",
                  color: "#38bdf8",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                Trade History <ArrowRight size={12} />
              </button>
            </div>

            {executions.length === 0 ? (
              <div style={{ padding: "1.5rem", textAlign: "center", color: "#64748b", fontSize: "0.75rem" }}>
                No recent order executions recorded for this session.
              </div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", minWidth: 600, borderCollapse: "collapse", fontSize: "0.75rem" }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid #1e293b", color: "#64748b", textAlign: "left" }}>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600 }}>Time</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600 }}>Symbol</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600 }}>Venue</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600 }}>Side</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Price</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Amount</th>
                      <th style={{ padding: "0.5rem 0.75rem", fontWeight: 600, textAlign: "right" }}>Realized P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {executions.map(exec => (
                      <tr key={exec.id} style={{ borderBottom: "1px solid rgba(30,41,59,0.5)" }}>
                        <td style={{ padding: "0.5rem 0.75rem", color: "#64748b" }}>
                          {exec.timestamp}
                        </td>
                        <td style={{ padding: "0.5rem 0.75rem", color: "#f8fafc", fontWeight: 700 }}>
                          {exec.symbol}
                        </td>
                        <td style={{ padding: "0.5rem 0.75rem" }}>
                          <span style={{
                            fontSize: "0.625rem",
                            padding: "2px 5px",
                            borderRadius: 4,
                            background: "#0f172a",
                            border: "1px solid #334155",
                            color: "#cbd5e1",
                            textTransform: "uppercase",
                            fontWeight: 700
                          }}>
                            {exec.exchangeId}
                          </span>
                        </td>
                        <td style={{ padding: "0.5rem 0.75rem" }}>
                          <span style={{
                            fontSize: "0.625rem",
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: exec.side === "buy" ? "rgba(16,185,129,0.15)" : "rgba(239,68,68,0.15)",
                            color: exec.side === "buy" ? "#10b981" : "#ef4444",
                            fontWeight: 700,
                            textTransform: "uppercase"
                          }}>
                            {exec.side}
                          </span>
                        </td>
                        <td style={{ padding: "0.5rem 0.75rem", textAlign: "right", color: "#f8fafc", fontWeight: 600 }}>
                          ${Number(exec?.price ?? 0).toLocaleString("en-US", { minimumFractionDigits: 2 })}
                        </td>
                        <td style={{ padding: "0.5rem 0.75rem", textAlign: "right", color: "#94a3b8" }}>
                          {exec.amount}
                        </td>
                        <td style={{
                          padding: "0.5rem 0.75rem",
                          textAlign: "right",
                          fontWeight: 700,
                          color: exec.realizedPnl > 0 ? "#10b981" : exec.realizedPnl < 0 ? "#ef4444" : "#64748b"
                        }}>
                          {exec.realizedPnl > 0 ? "+" : ""}${exec.realizedPnl.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

        </div>

        {/* ── RIGHT COLUMN: STRATEGIES, RISK, HEALTH & ACTIONS ───────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem", minWidth: 0 }}>

          {/* ── ZONE 4 / P1.3: ACTIVE STRATEGIES / BOTS ─────────────────────── */}
          <div style={{
            background: "#0c1017",
            border: "1px solid #1e293b",
            borderRadius: 12,
            padding: "1.25rem"
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "0.75rem"
            }}>
              <div>
                <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Active Bot Fleet ({strategies.filter(s => s.status === "active" || s.status === "running").length})
                </h2>
                <span style={{ fontSize: "0.6875rem", color: "#64748b" }}>Algorithmic deployment instances</span>
              </div>

              <button
                onClick={() => navigate("/app/strategies")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.25rem",
                  background: "none",
                  border: "none",
                  color: "#38bdf8",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                Manage All <ArrowRight size={12} />
              </button>
            </div>

            {strategies.length === 0 ? (
              <div style={{ padding: "1.5rem", textAlign: "center", color: "#64748b", fontSize: "0.75rem" }}>
                No active strategy bots deployed.
                <button
                  onClick={() => navigate("/app/builder")}
                  style={{
                    display: "block",
                    margin: "0.5rem auto 0",
                    padding: "4px 12px",
                    background: "#1e293b",
                    border: "1px solid #334155",
                    borderRadius: 6,
                    color: "#f8fafc",
                    fontSize: "0.6875rem",
                    fontWeight: 600,
                    cursor: "pointer"
                  }}
                >
                  Create Strategy
                </button>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                {strategies.map(strat => {
                  const isRunning = strat.status === "active" || strat.status === "running";
                  const isError = strat.status === "error" || strat.status === "failed" || strat.health === "error";

                  return (
                    <div
                      key={strat.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        padding: "0.625rem 0.875rem",
                        background: "#080a0e",
                        border: `1px solid ${isError ? "#ef4444" : "#1e293b"}`,
                        borderRadius: 8
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: "0.625rem" }}>
                        <span style={{
                          width: 7,
                          height: 7,
                          borderRadius: "50%",
                          background: isError ? "#ef4444" : isRunning ? "#10b981" : "#eab308"
                        }} />
                        <div>
                          <div style={{ fontSize: "0.75rem", fontWeight: 700, color: "#f8fafc" }}>
                            {strat.name}
                            {isError && (
                              <span style={{
                                fontSize: "0.5625rem",
                                padding: "1px 4px",
                                borderRadius: 3,
                                background: "rgba(239, 68, 68, 0.2)",
                                color: "#ef4444",
                                marginLeft: "0.375rem",
                                fontWeight: 800
                              }}>
                                FAILED
                              </span>
                            )}
                          </div>
                          <div style={{ fontSize: "0.625rem", color: isError ? "#ef4444" : "#64748b", marginTop: "0.125rem" }}>
                            {isError && strat.errorMessage ? strat.errorMessage : `${strat.pair} • ${strat.lastSignalTime}`}
                          </div>
                        </div>
                      </div>

                      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                        <div style={{ textAlign: "right" }}>
                          <div style={{
                            fontSize: "0.75rem",
                            fontWeight: 700,
                            color: strat.todayPnl >= 0 ? "#10b981" : "#ef4444"
                          }}>
                            {strat.todayPnl >= 0 ? "+" : ""}${strat.todayPnl.toFixed(2)}
                          </div>
                          <div style={{ fontSize: "0.5625rem", color: "#64748b" }}>Today's P&L</div>
                        </div>

                        {isError ? (
                          <button
                            onClick={() => navigate("/app/strategies")}
                            style={{
                              padding: "4px 8px",
                              borderRadius: 6,
                              fontSize: "0.625rem",
                              fontWeight: 700,
                              border: "1px solid #ef4444",
                              background: "rgba(239,68,68,0.15)",
                              color: "#ef4444",
                              cursor: "pointer"
                            }}
                          >
                            Inspect
                          </button>
                        ) : (
                          <button
                            onClick={() => handleToggleStrategy(strat.id)}
                            style={{
                              padding: "4px 8px",
                              borderRadius: 6,
                              fontSize: "0.625rem",
                              fontWeight: 700,
                              border: "1px solid #334155",
                              background: "#0f172a",
                              color: "#f8fafc",
                              cursor: "pointer",
                              display: "flex",
                              alignItems: "center",
                              gap: 3
                            }}
                          >
                            {isRunning ? <Pause size={10} /> : <Play size={10} />}
                            {isRunning ? "Pause" : "Run"}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* ── ZONE 5: RISK & SAFETY MONITOR ──────────────────────────────── */}
          <div style={{
            background: "#0c1017",
            border: "1px solid #1e293b",
            borderRadius: 12,
            padding: "1.25rem"
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "0.75rem"
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <ShieldCheck size={16} color="#10b981" />
                <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Risk & Safety Matrix
                </h2>
              </div>

              <button
                onClick={() => navigate("/app/risk")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.25rem",
                  background: "none",
                  border: "none",
                  color: "#38bdf8",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                Controls <ArrowRight size={12} />
              </button>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", fontSize: "0.75rem" }}>
              {/* Daily Loss Utilization Bar */}
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.25rem" }}>
                  <span style={{ color: "#94a3b8" }}>Daily Loss Limit Utilized</span>
                  <span style={{ color: "#f8fafc", fontWeight: 600 }}>
                    ${riskState?.daily_loss_utilized != null ? floatVal(riskState.daily_loss_utilized).toFixed(2) : "0.00"} / ${riskState?.max_daily_loss != null ? floatVal(riskState.max_daily_loss).toFixed(2) : "500.00"}
                  </span>
                </div>
                <div style={{ height: 5, background: "#1e293b", borderRadius: 3, overflow: "hidden" }}>
                  <div style={{
                    height: "100%",
                    width: `${Math.min(100, Math.max(0, ((riskState?.daily_loss_utilized || 0) / (riskState?.max_daily_loss || 500)) * 100))}%`,
                    background: riskState?.daily_loss_utilized > (riskState?.max_daily_loss * 0.8) ? "#ef4444" : "#10b981"
                  }} />
                </div>
              </div>

              {/* Position Capacity */}
              <div style={{ display: "flex", justifyContent: "space-between", padding: "0.3125rem 0", borderBottom: "1px solid rgba(30,41,59,0.5)" }}>
                <span style={{ color: "#94a3b8" }}>Open Position Capacity</span>
                <span style={{ color: "#f8fafc", fontWeight: 600 }}>
                  {positions.length} / {riskState?.max_positions || 10} Slots
                </span>
              </div>

              {/* A second "Current Drawdown" row stood here, reading
                  `risk.current_drawdown_pct` and falling back to `"0.00%"`. Both halves of
                  that are now wrong on the same page: the field is BC-1's deprecated
                  neighbour, which publishes `today_return_pct` — so a profitable day
                  rendered as a positive drawdown — and `"0.00%"` claimed an account at its
                  peak whenever nothing had been read. Drawdown is a tier-1 figure and is
                  reported once, from `risk.current_drawdown_pct_v2`, above. */}

              {/* Emergency Kill Switch */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: "0.25rem" }}>
                <span style={{ color: "#94a3b8" }}>Emergency Kill Switch</span>
                <span style={{
                  padding: "2px 6px",
                  borderRadius: 4,
                  fontSize: "0.625rem",
                  fontWeight: 700,
                  background: isKillSwitchActive ? "rgba(239,68,68,0.2)" : "rgba(16,185,129,0.2)",
                  color: isKillSwitchActive ? "#ef4444" : "#10b981"
                }}>
                  {isKillSwitchActive ? "TRIGGERED (BLOCKED)" : "STANDBY (READY)"}
                </span>
              </div>
            </div>
          </div>

          {/* ── ZONE 6: EXCHANGE HEALTH & CONNECTIVITY ─────────────────────── */}
          <div style={{
            background: "#0c1017",
            border: "1px solid #1e293b",
            borderRadius: 12,
            padding: "1.25rem"
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "0.75rem"
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <Server size={16} color="#38bdf8" />
                <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
                  Exchange Venues ({exchangeConnections.length})
                </h2>
              </div>

              <button
                onClick={() => navigate("/app/exchange")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.25rem",
                  background: "none",
                  border: "none",
                  color: "#38bdf8",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: "pointer"
                }}
              >
                Manage <ArrowRight size={12} />
              </button>
            </div>

            {exchangeConnections.length === 0 ? (
              <div style={{ padding: "1rem", textAlign: "center", background: "#080a0e", borderRadius: 8, border: "1px dashed #1e293b" }}>
                <p style={{ fontSize: "0.75rem", color: "#94a3b8", margin: 0 }}>
                  No exchange accounts connected
                </p>
                <button
                  onClick={() => navigate("/app/exchange")}
                  style={{
                    marginTop: "0.5rem",
                    padding: "4px 12px",
                    background: "#0284c7",
                    border: "none",
                    borderRadius: 6,
                    color: "#fff",
                    fontSize: "0.6875rem",
                    fontWeight: 700,
                    cursor: "pointer"
                  }}
                >
                  Connect Exchange Keys
                </button>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                {exchangeConnections.map(ex => (
                  <div
                    key={ex.exchange_id || ex.id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "0.5rem 0.75rem",
                      background: "#080a0e",
                      borderRadius: 6,
                      border: "1px solid #1e293b"
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      <span style={{
                        width: 7,
                        height: 7,
                        borderRadius: "50%",
                        background: ex.status === "connected" ? "#10b981" : "#ef4444"
                      }} />
                      <span style={{ fontSize: "0.75rem", color: "#f8fafc", fontWeight: 700, textTransform: "uppercase" }}>
                        {ex.exchange_id || ex.name}
                      </span>
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                      <span style={{
                        fontSize: "0.625rem",
                        color: ex.latency_ms != null ? "#10b981" : "#64748b",
                        fontWeight: 600
                      }}>
                        {ex.latency_ms != null ? `${ex.latency_ms} ms` : "Latency unavailable"}
                      </span>
                      <span style={{
                        fontSize: "0.625rem",
                        padding: "1px 5px",
                        borderRadius: 3,
                        background: ex.status === "connected" ? "rgba(16,185,129,0.15)" : "rgba(239,68,68,0.15)",
                        color: ex.status === "connected" ? "#10b981" : "#ef4444",
                        textTransform: "uppercase",
                        fontWeight: 700
                      }}>
                        {ex.status}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ── ZONE 7 (PART B): ACTIONABLE NOTIFICATIONS / INSIGHTS ───────── */}
          {tradingInsights.length > 0 && (
            <div style={{
              background: "#0c1017",
              border: "1px solid #1e293b",
              borderRadius: 12,
              padding: "1.25rem"
            }}>
              <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, marginBottom: "0.75rem", textTransform: "uppercase", letterSpacing: "0.03em" }}>
                Operational Insights
              </h2>

              <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                {tradingInsights.map(item => (
                  <div
                    key={item.id}
                    style={{
                      padding: "0.625rem 0.75rem",
                      background: "#080a0e",
                      borderLeft: `3px solid ${item.type === "warning" ? "#eab308" : item.type === "info" ? "#38bdf8" : "#10b981"}`,
                      borderRadius: "0 6px 6px 0",
                      fontSize: "0.6875rem"
                    }}
                  >
                    <p style={{ color: "#cbd5e1", margin: 0, lineHeight: 1.4 }}>
                      {item.text}
                    </p>
                    {item.actionPath && (
                      <button
                        onClick={() => navigate(item.actionPath)}
                        style={{
                          background: "none",
                          border: "none",
                          color: "#38bdf8",
                          fontSize: "0.625rem",
                          fontWeight: 700,
                          padding: 0,
                          marginTop: 4,
                          cursor: "pointer"
                        }}
                      >
                        {item.actionText || "Action"} →
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

        </div>
      </div>

        </>
      )}

    </div>
  );
}
