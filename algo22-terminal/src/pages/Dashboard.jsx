/**
 * ═══════════════════════════════════════════════════════════════════════════
 * pages/Dashboard — the command center (`/app/dashboard`)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 19.1: the single read, the one failure state, tier 1 (part A)
 * and tier 2 (part B). design.md §7.1, §11.1. Requirements 3.1, 3.2, 3.4, 3.5, 3.6, 14.5.
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
 * TIER 2 — FIVE REGIONS, ONE CONTAINER, EVERY FIGURE DECLARED
 * ----------------------------------------------------------
 * Active strategies, open positions (top five and a link to the rest), system & exchange
 * health, recent signals, recent orders, and the equity curve — §7.1's tier 2, in one
 * `data-page-tier="2"` container that follows tier 1 in document order. Each declared
 * field carries `data-region` spelled as its `pageFields` key, so "the declared element
 * rendered, and it rendered below tier 1" is decidable from the DOM rather than from the
 * JSX (Property 4, task 19.5).
 *
 * Every zone is a `ds/Panel` driven by the ONE read's state, so a zone with nothing in it
 * is `empty` — an `ds/EmptyState` that says what is missing, why it matters and what to do
 * (Requirement 14.1) — and never a table with no rows or a figure reading `0`. None of them
 * is ever `error`: a per-zone error would imply a per-zone read (see above).
 *
 * BC-2 — `degraded` IS WHAT SEPARATES "NO POSITIONS" FROM "COULD NOT READ THEM"
 * ---------------------------------------------------------------------------
 * `positions: []` is what the server answers for an account holding nothing AND for a
 * positions read that failed, and the top-level `degraded` marker is the only thing that
 * tells the two apart (`pageFields`' `openPositions` / `positionsDegraded` notes,
 * Requirement 14.5). Both arms are rendered, and the marker is consulted BEFORE the list:
 *
 *   * `degraded === null` — every read behind the response succeeded. That is a HEALTHY
 *     reading, not an absent value, so it renders no not-available marker at all: `[]`
 *     means the account holds no open positions and the panel is `empty`.
 *   * `degraded.positions === "unreadable"` — the server's own `reason`, verbatim, in a
 *     `ds/Alert` above the panel, and the panel itself in `error` so no table exists in the
 *     DOM. An empty table here would state that the account holds nothing.
 *
 * `risk.open_positions_count` is BC-2's other channel and is `null` — never `0` — when the
 * positions could not be counted, because zero is the safest-looking figure a broken
 * positions read could publish. It is read from the server rather than from
 * `positions.length` so the figure on screen is the one the server computed, and because
 * only the reported field makes the `null` case reachable at all.
 *
 * TWO OF THE FOUR PER-VENUE FIELDS ARE CONSTANTS, AND ARE NOT PRESENTED AS READINGS
 * -------------------------------------------------------------------------------
 * `exchange.exchanges[]` is `{exchange_id, status, latency_ms, last_sync}` built from the
 * caller's `exchange_keys` rows, and `pageFields`' `exchangeHealth` note records that two
 * of those four are constants in the aggregation service: `status` is always `"connected"`
 * and `latency_ms` is always `35`, from no measurement. So neither is passed to
 * `ds/ExchangeStatus`: an omitted `connectionState` renders "Connection not reported" in the
 * neutral group rather than a green CONNECTED chip nothing checked, and an omitted
 * `latencyMs` renders the marker with that component's own reason rather than a fabricated
 * 35 ms. The venue and `last_sync` are real and are rendered. Measured latency is a
 * page-level figure — `health.exchange_api_latency_ms`, genuinely `null` when unmeasured,
 * with `health.exchange_api_latency_status` reading `"unavailable"` beside it — and it
 * renders as one `ds/Metric` that shows the marker, never `0 ms`.
 *
 * THE EQUITY CURVE IS THE ONE LAZY IMPORT, AND IT DOES NOT TICK
 * -----------------------------------------------------------
 * `ds/Chart` is imported with `lazy(() => import(...))` rather than by path, for the reason
 * its own docblock gives: it is the only module in `src/` that may import recharts, and a
 * static import ANYWHERE in the entry graph hoists `vendor-recharts` into it whatever the
 * chunk config says. This page held that import until now, so `Chart.test.jsx`'s pinned
 * importer list comes down by one with this change.
 *
 * The series is projected from `equity_curve` in the one place the payload is read, and no
 * WebSocket handler writes it. A recharts re-render is the most expensive thing on this
 * page and no requirement asks for a tick-live equity curve (§7.1, task 19.3).
 *
 * WHAT TIER 2 NO LONGER RENDERS, AND WHY
 * --------------------------------------
 *   * The Risk & Safety Matrix. Its three rows were a daily-loss bar defaulting to
 *     `$0.00 / $500.00` for an account nothing had been read for, an "Open Position
 *     Capacity" reading `positions.length / (max_positions || 10)`, and a second copy of
 *     the kill-switch state. None of the three is a §7.1 field, the first two stated limits
 *     the server never reported, and the third is already reported by the control itself.
 *     `/app/risk` owns those figures and the page links to it. No risk-control LOGIC is
 *     touched — see below.
 *   * The Operational Insights list. `recent_activity.insights` has no `pageFields` entry
 *     and no §7.1 row; the two of its three items that the service hardcodes ("Risk Circuit
 *     Breakers active", "n active strategy execution bot(s) running") are prose, not
 *     readings. Its warning-and-worse subset still feeds the Requirement 3.3 alert strip,
 *     which is task 19.2's, and is unchanged.
 *   * The per-strategy Pause / Run button. It called no API: it rewrote local state, so the
 *     row said "paused" while the worker kept trading (Requirement 19.4's dead control, and
 *     the most dangerous kind). Deployment control belongs to `/app/strategies`, which the
 *     panel links to.
 *
 * THE KILL SWITCH AND THE ALERT STRIP ARE TASK 19.2's
 * ---------------------------------------------------
 * The halt/resume control, its confirmation, its two `riskApi` calls, the diagnostics
 * popover beside it and the Requirement 3.3 alert strip are exactly as part A left them.
 * Requirement 19.1 forbids changing a risk control's logic, and 19.2 owns routing the
 * switch through `ds/ConfirmDialog` and the strip through `ds/Alert`. The popover therefore
 * still reports latency and order-state sync from `health`, which tier 2 now also reports
 * from the declaration; 19.2 folds the popover into that panel.
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
 * @module pages/Dashboard
 */

import {
  lazy,
  memo,
  Suspense,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  Activity, AlertTriangle, ArrowRight, BarChart2,
  Layers, RefreshCw, Server, Wallet,
  ShieldAlert, AlertOctagon
} from "lucide-react";
import { dashboardApi } from "../api/modules/dashboard";
import { riskApi } from "../api/modules/risk";
import wsClient from "../websocketClient";
import { Alert } from "../components/ds/Alert";
import { CommandButton } from "../components/ds/CommandButton";
import { DataTable } from "../components/ds/DataTable";
import { EmptyState } from "../components/ds/EmptyState";
import { ErrorState } from "../components/ds/ErrorState";
import { ExchangeStatus } from "../components/ds/ExchangeStatus";
import { LoadingState } from "../components/ds/LoadingState";
import { Metric, NotAvailableMarker } from "../components/ds/Metric";
import { PageHeader } from "../components/ds/PageHeader";
import { Panel } from "../components/ds/Panel";
import { PnLDisplay } from "../components/ds/PnLDisplay";
import { StatusBadge } from "../components/ds/StatusBadge";
import { StrategyStatus } from "../components/ds/StrategyStatus";
import { PAGES, PAGE_FIELDS_BY_PAGE } from "../design/pageFields";
import {
  PAGE_HIERARCHY_BY_PAGE,
  TIER_ATTRIBUTE,
  TIER_PAGE_ATTRIBUTE,
} from "../design/pageHierarchy";
import { fromNullable } from "../design/reported";
import { PANEL_STATES, usePanelState } from "../hooks/usePanelState";

/**
 * `ds/Chart`, lazily — the one primitive on this page not imported by path.
 *
 * See the module docblock and `Chart.jsx`'s: it is the only module in `src/` that may
 * import recharts, it is deliberately absent from `ds/index.js`, and a static import here
 * would hoist `vendor-recharts` into this route's chunk graph. `lazy()` needs a module
 * whose `default` is the component, which `Chart.jsx` provides for exactly this call site.
 */
const Chart = lazy(() => import("../components/ds/Chart"));

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

/**
 * A panel's "the rest of these are over there" link.
 *
 * A real `<Link>`, not a `<button onClick={navigate}>`: it is a navigation, so it belongs in
 * the tab order as a link, opens in a new tab on the modifier the browser already knows, and
 * announces as one. The four buttons this replaces each re-implemented that badly.
 *
 * The label always names the destination — `anchor-ambiguous-text` (task 6.27's addition to
 * the a11y ratchet) rejects "View all" standing alone, and rightly: a link whose text names
 * nothing tells a screen-reader user nothing about where it goes.
 */
const PANEL_LINK_CLASSES =
  "inline-flex shrink-0 items-center gap-1 text-micro font-semibold uppercase tracking-wide "
  + "text-brand hover:text-brand-hover focus-visible:outline-2 focus-visible:outline-offset-2 "
  + "focus-visible:outline-brand";

function PanelLink({ to, children }) {
  return (
    <Link to={to} className={PANEL_LINK_CLASSES}>
      {children}
      <ArrowRight size={12} aria-hidden="true" />
    </Link>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * READING A FIELD — the two total helpers every tier-2 projection ends with
 * ══════════════════════════════════════════════════════════════════════════
 *
 * These replace the `?? 0`, `|| "UNKNOWN"` and `floatVal(x || 0)` chains the tier-2 zones
 * used to end their field reads with. Those chains turned "the server sent no such field"
 * into a displayed quantity — a size of 0, an entry price of 0.00, a venue called
 * `binance` for a row that named none — which is exactly what Requirement 14.5 forbids.
 * `null` travels to the cell instead, and `ds/DataTable` renders the marker.
 */

/**
 * The first candidate that is a finite number, or `null`.
 *
 * @param {...unknown} candidates Field readings, in precedence order.
 * @returns {number|null}
 */
const firstNumber = (...candidates) => {
  for (const candidate of candidates) {
    if (candidate === null || candidate === undefined || candidate === "") continue;
    if (typeof candidate === "boolean") continue;
    const parsed = typeof candidate === "number" ? candidate : Number(String(candidate).trim());
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
};

/** The first candidate that is a non-empty string, or `null`. Never a guessed venue. */
const firstText = (...candidates) => {
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim() !== "") return candidate.trim();
  }
  return null;
};

/** A declared field's rendered label, from the declaration rather than retyped here. */
const labelOf = (field) => fieldEntry(field)?.label ?? field;

/** A declared field's not-available sentence (Requirement 19.3). */
const reasonOf = (field) => fieldEntry(field)?.reason ?? undefined;

/* ══════════════════════════════════════════════════════════════════════════
 * TIER 2 — OPEN POSITIONS (`positions`, `degraded`, `risk.open_positions_count`)
 * ══════════════════════════════════════════════════════════════════════════ */

/** §7.1 draws the top five and a link to the rest, which is `/app/portfolio`. */
const POSITIONS_LIMIT = 5;

/**
 * One `NormalizedPosition` → one table row. Every field is `null`-able.
 *
 * The names are the ones `dashboard_aggregation_service` publishes, per `pageFields`'
 * `openPositions` note: `symbol side contracts entry_price mark_price notional leverage
 * unrealized_pnl unrealized_pnl_pct liquidation_price margin margin_type exchange_id
 * environment timestamp`. A field the response did not carry stays `null` and its cell
 * renders the marker — nothing here defaults, and `market_type` in particular is NOT
 * defaulted to `"spot"`, because a market type is what decides whether a position can be
 * liquidated at all.
 *
 * @param {unknown} position
 * @param {number} index
 * @returns {Object}
 */
const toPositionRow = (position, index) => {
  const source = position && typeof position === "object" ? position : {};
  const side = firstText(source.side);
  const marketType = firstText(source.market_type);
  const markPrice = firstNumber(source.mark_price, source.markPrice);
  const liquidationPrice = firstNumber(source.liquidation_price, source.liquidationPrice);

  return {
    id: firstText(source.id, source.position_id) ?? `position-${index}`,
    market: firstText(source.symbol, source.market),
    marketType,
    // The venue as the row reported it. The old cell fell back to `"binance"` on live and
    // `"paper"` on paper, naming a venue for a row that may have named a different one.
    venue: firstText(source.exchange_id),
    marginType: firstText(source.margin_type),
    side: side === null ? null : side.toLowerCase(),
    size: firstNumber(source.contracts, source.quantity, source.size),
    entryPrice: firstNumber(source.entry_price, source.entryPrice),
    markPrice,
    unrealisedPnl: firstNumber(source.unrealized_pnl, source.unrealized_pnl_usd),
    unrealisedPnlPct: firstNumber(source.unrealized_pnl_pct),
    liquidationPrice,
    /*
     * Derived, and only from operands that are real: `computeLiquidationDistance` answers
     * `null` for a spot market, for an absent liquidation level and for an absent mark, so
     * a distance is never computed against a price nobody reported. The threshold hues the
     * old cell painted (red under 10%, amber under 20%) and its ⚠️ prefix are gone with
     * Requirement 1.5's calm default; the figure is the reading.
     */
    liquidationDistancePct: computeLiquidationDistance(
      markPrice, liquidationPrice, side === null ? null : side.toLowerCase(), marketType,
    ),
  };
};

/** `long` / `short` as a `ds/StatusBadge` state — `semantic.js` maps them to profit/loss. */
function SideCell({ value }) {
  if (value === null || value === undefined) {
    return <NotAvailableMarker label="Side" reason="The position did not report a side." />;
  }
  return <StatusBadge state={value} size="sm" />;
}

/** `buy` / `sell`, the same way. An order with no side is not an order to act on. */
function OrderSideCell({ value }) {
  if (value === null || value === undefined) {
    return <NotAvailableMarker label="Side" reason="The execution did not report a side." />;
  }
  return <StatusBadge state={value} size="sm" />;
}

/**
 * The liquidation distance, as a percentage of the mark.
 *
 * One decimal place, which is the precision the level itself is quoted to; `null` is the
 * marker with the reason, because a spot position and a futures position whose venue
 * reported no level are both blank cells and a trader needs to know which.
 */
const NO_LIQUIDATION_DISTANCE_REASON =
  "No liquidation distance can be computed — the position is spot, or the venue reported "
  + "no liquidation price or no mark price.";

function LiquidationDistanceCell({ value }) {
  if (value === null || value === undefined) {
    return (
      <NotAvailableMarker
        label="Liquidation distance"
        reason={NO_LIQUIDATION_DISTANCE_REASON}
      />
    );
  }
  return <span>{`${value.toFixed(1)}%`}</span>;
}

/**
 * §7.1's position columns, built once per denomination.
 *
 * `align: 'numeric'` on the six quantities is Requirement 11.3 — alignment is a column
 * property, so it cannot be decided per cell and cannot be inconsistent. `priority: 3` on
 * the venue and the margin mode takes them out of the row below `--breakpoint-laptop` and
 * into `ds/DataTable`'s per-row expander (Requirement 17.2); the DOM is the same at every
 * width, the reduction is CSS.
 *
 * No column is `sortable`. Five rows chosen by the server are not a set to sort, and
 * `ds/DataTable` renders a header as text rather than as a dead button when no
 * `onSortChange` is given (Requirement 19.4). Sorting the full ledger is `/app/portfolio`'s.
 *
 * @param {string|null} currency The denomination `overview.currency` reported, or `null`.
 */
const positionColumns = (currency) => Object.freeze([
  { key: "market", header: "Market", align: "text", format: "symbol", priority: 1 },
  { key: "marketType", header: "Type", align: "text", format: "text", priority: 2 },
  { key: "venue", header: "Venue", align: "text", format: "text", priority: 3 },
  { key: "marginType", header: "Margin", align: "text", format: "text", priority: 3 },
  { key: "side", header: "Side", align: "text", format: "text", render: SideCell, priority: 1 },
  { key: "size", header: "Size", align: "numeric", format: "number", priority: 1 },
  { key: "entryPrice", header: "Entry", align: "numeric", format: "currency", priority: 2 },
  { key: "markPrice", header: "Mark", align: "numeric", format: "currency", priority: 1 },
  {
    key: "unrealisedPnl",
    header: "Unrealised P&L",
    align: "numeric",
    format: "currency",
    priority: 1,
    /*
     * The percentage comes from `observed`, not from `row`. `ds/DataTable`'s row memo
     * compares the PROJECTED cell values, so a `render` that reads an unprojected field off
     * `row` can hold a stale figure when a tick rebuilds the array — and `observe` is the
     * declared way for a cell to name the rest of what it reads. See the table below.
     */
    render: function UnrealisedPnlCell({ value, observed }) {
      return (
        <PnLDisplay
          value={value}
          percentValue={observed?.unrealisedPnlPct}
          showPercent
          currency={currency ?? undefined}
          precision={2}
          label="Unrealised P&L"
        />
      );
    },
  },
  { key: "liquidationPrice", header: "Liquidation", align: "numeric", format: "currency", priority: 2 },
  {
    key: "liquidationDistancePct",
    header: "Liq. distance",
    align: "numeric",
    format: "number",
    render: LiquidationDistanceCell,
    priority: 2,
  },
]);

/**
 * BC-2's `degraded` marker off a `GET /api/dashboard` body → the server's reason, or `null`.
 *
 * `null` means every read behind the response succeeded, which is a HEALTHY reading and not
 * an absent value: it renders no marker anywhere. Anything else is
 * `{positions: "unreadable", environment, reason}`, and the reason is rendered verbatim
 * because the server knows which environment failed and why, and `translateError` carries no
 * free-form message by design (Requirement 14.4).
 *
 * @param {unknown} body
 * @returns {string|null}
 */
const readPositionsDegradation = (body) => {
  const degraded = body && typeof body === "object" ? body.degraded : null;
  if (!degraded || typeof degraded !== "object") return null;
  if (degraded.positions !== "unreadable") return null;
  const reason = firstText(degraded.reason);
  return reason
    ?? "The server reported this positions read as unreadable and gave no reason.";
};

/* ══════════════════════════════════════════════════════════════════════════
 * TIER 2 — ACTIVE STRATEGIES (`strategies.active`, `.paused`, `.items[].status`)
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * One `strategies.items[]` entry → one row of the fleet list.
 *
 * `status` and `health` are passed to `ds/StrategyStatus` verbatim: it renders "Status not
 * reported" for an absent status rather than the `|| "paused"` this projection used to
 * substitute, which is a claim that a running strategy is stopped.
 */
const toStrategyRow = (item, index) => {
  const source = item && typeof item === "object" ? item : {};
  return {
    id: firstText(source.id) ?? `strategy-${index}`,
    name: firstText(source.name),
    market: firstText(source.pair, source.symbol),
    status: firstText(source.status),
    health: firstText(source.health),
    errorMessage: firstText(source.error_message, source.error, source.reason),
  };
};

/* ══════════════════════════════════════════════════════════════════════════
 * TIER 2 — RECENT SIGNALS AND ORDERS
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * One `recent_activity.signals[]` entry → one row.
 *
 * `get_recent_signals` publishes `{id, time, text, type}`, where `text` is the sentence the
 * service composes from the signal's decision, market, venue and risk verdict. It is
 * rendered as it arrives — recomposing it here would be a second definition of one line.
 */
const toSignalRow = (signal, index) => {
  const source = signal && typeof signal === "object" ? signal : {};
  return {
    id: firstText(source.id) ?? `signal-${index}`,
    time: firstText(source.time, source.generated_at),
    text: firstText(source.text),
  };
};

const SIGNAL_COLUMNS = Object.freeze([
  { key: "time", header: "Time", align: "text", format: "timestamp", priority: 1 },
  { key: "text", header: "Signal", align: "text", format: "text", priority: 1 },
]);

/**
 * One execution → one row.
 *
 * From the top-level `executions` and NOT from `recent_activity.executions`: `pageFields`'
 * `recentOrders` note records that they are the same list, so one of them is read.
 */
const toOrderRow = (execution, index) => {
  const source = execution && typeof execution === "object" ? execution : {};
  const side = firstText(source.side);
  return {
    id: firstText(source.id, source.order_id) ?? `execution-${index}`,
    time: firstText(source.timestamp, source.executed_at),
    market: firstText(source.symbol),
    venue: firstText(source.exchange_id),
    side: side === null ? null : side.toLowerCase(),
    price: firstNumber(source.price),
    amount: firstNumber(source.amount, source.quantity),
    realisedPnl: firstNumber(source.realized_pnl),
  };
};

/** @param {string|null} currency */
const orderColumns = (currency) => Object.freeze([
  { key: "time", header: "Time", align: "text", format: "timestamp", priority: 1 },
  { key: "market", header: "Market", align: "text", format: "symbol", priority: 1 },
  { key: "venue", header: "Venue", align: "text", format: "text", priority: 3 },
  { key: "side", header: "Side", align: "text", format: "text", render: OrderSideCell, priority: 1 },
  { key: "price", header: "Price", align: "numeric", format: "currency", priority: 1 },
  { key: "amount", header: "Amount", align: "numeric", format: "number", priority: 1 },
  {
    key: "realisedPnl",
    header: "Realised P&L",
    align: "numeric",
    format: "currency",
    priority: 2,
    render: function RealisedPnlCell({ value }) {
      return (
        <PnLDisplay
          value={value}
          currency={currency ?? undefined}
          precision={2}
          label="Realised P&L"
        />
      );
    },
  },
]);

/* ══════════════════════════════════════════════════════════════════════════
 * TIER 2 — SYSTEM & EXCHANGE HEALTH
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * One `exchange.exchanges[]` entry → one venue row.
 *
 * `status` and `latency_ms` are deliberately NOT read: both are constants in
 * `get_exchange_health` (`"connected"` and `35`), so neither is a measurement and neither
 * may be presented as one. See the module docblock. What is left is what the row really
 * knows — which venue it is, from `exchange_keys`, and when that record was last written.
 */
const toVenueRow = (venue, index) => {
  const source = venue && typeof venue === "object" ? venue : {};
  return {
    id: firstText(source.exchange_id, source.id, source.name) ?? `venue-${index}`,
    exchange: firstText(source.exchange_id, source.name),
    lastSync: firstText(source.last_sync),
  };
};

/**
 * Why the two per-venue fields render as not reported. Stated on the panel, once.
 *
 * Requirement 19.3 asks for the reason beside the state, and this one is not a fact about
 * the account or the venue — it is a fact about the aggregation service, which a trader
 * reading an empty connection chip has no way to know.
 */
const VENUE_CONSTANT_NOTE =
  "Per-venue connection state and latency are not measured: the aggregation service "
  + "publishes a fixed value for both, so neither is reported here. The measured figure is "
  + "the account's exchange API latency above.";

/** A venue row's `last_sync`, or the marker. Not a guessed time and not \"just now\". */
function VenueLastSync({ lastSync }) {
  return (
    <span className="flex min-w-0 items-center gap-1 text-micro text-content-secondary">
      <span>Last sync</span>
      {lastSync === null ? (
        <NotAvailableMarker
          label="Last sync"
          reason="The exchange connection record carries no last-sync time."
        />
      ) : (
        <time dateTime={lastSync} className="font-mono tabular-nums">{lastSync}</time>
      )}
    </span>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * TIER 2 — THE EQUITY CURVE
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * `equity_curve[]` → the chart's series, in the order it arrived.
 *
 * NOT re-sorted: `pageFields`' `equityCurve` note says why — the drawdown of a resorted
 * series is the drawdown of a different series, and tier 1's `currentDrawdown` is computed
 * from this one server-side.
 *
 * A row missing either operand is dropped from the SERIES rather than plotted: the old
 * projection read `parseFloat(row.equity ?? row.value ?? 0)`, which put an account at zero
 * equity on the chart for a point the server could not value. `timestamp` is handed over as
 * the server wrote it, because `ds/Chart`'s `format: 'date'` renders it in UTC and a
 * locale-formatted string on the axis is a string the tooltip cannot reinterpret.
 *
 * @param {unknown} body
 * @returns {Array<{timestamp: string, equity: number}>}
 */
const readEquitySeries = (body) => {
  const raw = Array.isArray(body?.equity_curve) ? body.equity_curve : NO_ROWS;
  const points = [];
  for (const row of raw) {
    const source = row && typeof row === "object" ? row : {};
    const timestamp = firstText(source.timestamp, source.date);
    const equity = firstNumber(source.equity, source.value);
    if (timestamp === null || equity === null) continue;
    points.push({ timestamp, equity });
  }
  return points.length === 0 ? NO_ROWS : points;
};

/**
 * The equity curve, behind its own `memo` boundary and its own `Suspense` boundary.
 *
 * `memo` with primitive-plus-stable-array props is §13.2's tick isolation applied to the
 * most expensive thing on the page: `rows` is a `useMemo`'d array whose identity changes
 * only when the ONE read completes, and no WebSocket handler writes it, so nothing short of
 * a REST read re-enters recharts (task 19.3).
 *
 * The `Suspense` fallback is `skeleton-chart`, which is the height `ds/Chart` lays out at,
 * so the panel does not resize when the recharts chunk lands (Requirement 14.2).
 */
const EquityCurveChart = memo(function EquityCurveChart({ rows, currency }) {
  return (
    <Suspense fallback={<LoadingState kind="skeleton-chart" label="Loading equity curve" />}>
      <Chart
        kind="area"
        data={rows}
        xAxis={{ key: "timestamp", label: "Date", format: "date" }}
        yAxis={{ label: currency ? `Equity (${currency})` : "Equity", format: "currency" }}
        series={[{ key: "equity", name: "Equity", token: "brand" }]}
        emptyMessage="No equity history for this period"
      />
    </Suspense>
  );
});

/* ══════════════════════════════════════════════════════════════════════════
 * THE FIVE EMPTY STATES — what is missing, why it matters, what to do (Req 14.1)
 * ══════════════════════════════════════════════════════════════════════════ */

const POSITIONS_EMPTY = Object.freeze({
  icon: Wallet,
  headline: "No open positions",
  body: "This account holds nothing right now, so there is no exposure to manage. A "
    + "deployed strategy opens positions on its own signals.",
  action: Object.freeze({ label: "Deploy a strategy", to: "/app/strategies" }),
});

const STRATEGIES_EMPTY = Object.freeze({
  icon: Layers,
  headline: "No strategies deployed",
  body: "Nothing is trading this account. A strategy has to be deployed before any signal "
    + "or order can appear on this page.",
  action: Object.freeze({ label: "Build a strategy", to: "/app/builder" }),
});

const HEALTH_EMPTY = Object.freeze({
  icon: Server,
  headline: "No exchange connected",
  body: "No venue is connected to this account, so no connection or latency can be "
    + "reported and no live order can be placed.",
  action: Object.freeze({ label: "Connect an exchange", to: "/app/exchange" }),
});

const SIGNALS_EMPTY = Object.freeze({
  icon: Activity,
  headline: "No signals yet",
  body: "A deployed strategy records a signal every time it evaluates its entry conditions. "
    + "None has been recorded for this account.",
  action: Object.freeze({ label: "Open Signal Trace", to: "/app/signal-trace" }),
});

const ORDERS_EMPTY = Object.freeze({
  headline: "No orders filled",
  body: "No execution has been recorded for this account, so there is nothing to reconcile "
    + "against the exchange.",
  action: Object.freeze({ label: "Open trade history", to: "/app/trades" }),
});

const EQUITY_EMPTY = Object.freeze({
  icon: BarChart2,
  headline: "No equity history",
  body: "The curve is drawn from account snapshots. None fell inside the selected period, "
    + "so there is nothing to plot.",
  action: Object.freeze({ label: "Open portfolio", to: "/app/portfolio" }),
});

/* ══════════════════════════════════════════════════════════════════════════
 * THE ALERT STRIP's INPUT — task 19.2's, unchanged
 * ══════════════════════════════════════════════════════════════════════════ */

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

/** A list off the body, projected row by row, or the one shared empty list. */
const readList = (raw, project) => {
  if (!Array.isArray(raw) || raw.length === 0) return NO_ROWS;
  return raw.map(project);
};

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

  // Operational Critical Alerts State (P0.2 / 2D.2) — task 19.2's alert strip
  const [criticalAlerts, setCriticalAlerts] = useState(NO_ROWS);

  /*
   * TIER 2's view models. One per §7.1 region, written in exactly one place — the
   * projection effect below — and cleared together when the read has no payload.
   *
   * They are state rather than memos because task 19.3 moves the WebSocket subscriptions
   * into the leaves that render the value and patches these same models on the way; two of
   * the handlers already do (`strategy_status`, `exchange_health`).
   */
  const [positions, setPositions] = useState(NO_ROWS);
  // BC-2's two channels, kept apart from the list they qualify.
  const [positionsDegraded, setPositionsDegraded] = useState(null);
  const [openPositionsCount, setOpenPositionsCount] = useState(null);
  const [strategies, setStrategies] = useState(NO_ROWS);
  const [strategyCounts, setStrategyCounts] = useState(null);
  const [signals, setSignals] = useState(NO_ROWS);
  const [executions, setExecutions] = useState(NO_ROWS);
  const [venues, setVenues] = useState(NO_ROWS);
  const [systemHealth, setSystemHealth] = useState(null);
  const [equityCurve, setEquityCurve] = useState(NO_ROWS);

  // The risk block, for the kill switch and the circuit breaker — task 19.2's two readings.
  const [riskState, setRiskState] = useState(null);

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
      setPositionsDegraded(null);
      setOpenPositionsCount(null);
      setStrategies(NO_ROWS);
      setStrategyCounts(null);
      setSignals(NO_ROWS);
      setExecutions(NO_ROWS);
      setVenues(NO_ROWS);
      setEquityCurve(NO_ROWS);
      setCriticalAlerts(NO_ROWS);
      setRiskState(null);
      setSystemHealth(null);
      return;
    }

    // The freshness floor every WebSocket frame is measured against (2D.4).
    lastSyncTimestampRef.current = Date.now();

    const risk = payload.risk ?? null;

    /*
     * BC-2 IS READ BEFORE THE LIST IT QUALIFIES.
     *
     * `positions: []` is what the server answers for an account holding nothing AND for a
     * failed positions read, so the marker is what decides which of the two the panel
     * renders. The list is projected either way and the panel's state — not this
     * projection — is what keeps a table out of the DOM on the degraded arm.
     */
    setPositionsDegraded(readPositionsDegradation(payload));
    setPositions(readList(payload.positions, toPositionRow));
    // The server's own count, `null` when it could not be taken. Never `positions.length`:
    // a count derived on the client cannot report that the count is unknown.
    setOpenPositionsCount(
      firstNumber(risk && typeof risk === "object" ? risk.open_positions_count : null),
    );

    const strategyBlock = payload.strategies ?? null;
    setStrategies(readList(strategyBlock?.items, toStrategyRow));
    setStrategyCounts(strategyBlock && typeof strategyBlock === "object"
      ? {
        active: firstNumber(strategyBlock.active),
        paused: firstNumber(strategyBlock.paused),
      }
      : null);

    setSignals(readList(payload.recent_activity?.signals, toSignalRow));
    // The top-level list, not `recent_activity.executions` — they are the same rows.
    setExecutions(readList(payload.executions, toOrderRow).slice(0, POSITIONS_LIMIT));
    setVenues(readList(payload.exchange?.exchanges, toVenueRow));
    setEquityCurve(readEquitySeries(payload));

    setCriticalAlerts(readCriticalAlerts(readInsights(payload)));
    setRiskState(risk);
    setSystemHealth(payload.health ?? null);
  }, [payload]);

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

    /*
     * 6. Exchange Health Changes.
     *
     * The frame is merged into the venue row it names, and only into the field this panel
     * reads: `last_sync`. It used to be spread wholesale (`{...ex, ...data}`), which put the
     * frame's `status` and `latency_ms` on the row — and nothing declares that channel's
     * payload, so neither could be presented as a measurement any more than the REST
     * constants they replaced could. Task 19.3 moves this subscription into
     * `ds/ExchangeStatus` itself, which is where a per-venue reading belongs.
     */
    const unsubExchange = wsClient.subscribe("exchange_health", (data) => {
      if (!isEventFresh(data)) return;
      if (data?.exchange_id) {
        setVenues(prev => prev.map(venue => (venue.id === data.exchange_id ? {
          ...venue,
          lastSync: firstText(data.last_sync) ?? venue.lastSync,
        } : venue)));
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

  /*
   * A `handleToggleStrategy` stood here. It called no API: it rewrote `status` and `health`
   * in local state, so pressing Pause left the row reading "paused" while the deployment
   * kept trading, and a refresh silently undid it. That is Requirement 19.4's dead control
   * in its most dangerous form — a risk action that appears to have been taken. Deployment
   * control belongs to `/app/strategies`, which the fleet panel links to.
   */

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

  /**
   * One tier-2 zone's §11.1 state, from the ONE read's state and whether the zone has rows.
   *
   * `error` is deliberately unreachable: one read means one failure, and that failure is the
   * page-level branch below. So a zone is `loading` while the read is in flight, `empty` when
   * the read succeeded and the zone has nothing — which `ds/Panel` renders as an
   * `ds/EmptyState` and NOT as a table with no rows — and `ready` otherwise. `refreshing`
   * needs children on screen (§11.1), so a zone that had nothing to show falls back to
   * `loading` for the duration.
   *
   * @param {boolean} hasContent
   * @returns {string}
   */
  const zoneState = (hasContent) => {
    if (readState === PANEL_STATES.IDLE || readState === PANEL_STATES.LOADING) {
      return PANEL_STATES.LOADING;
    }
    if (readState === PANEL_STATES.REFRESHING) {
      return hasContent ? PANEL_STATES.REFRESHING : PANEL_STATES.LOADING;
    }
    return hasContent ? PANEL_STATES.READY : PANEL_STATES.EMPTY;
  };

  /*
   * BC-2's two arms, as one state each.
   *
   * The degraded arm outranks everything: it is the one case where `positions` being `[]`
   * must NOT read as an account holding nothing, so the panel goes to `error` — `ds/Panel`
   * renders no children there, so no table exists in the DOM — and the server's own reason
   * is rendered above it. The healthy arm is the ordinary empty/ready pair.
   */
  const positionsState = positionsDegraded !== null
    ? PANEL_STATES.ERROR
    : zoneState(positions.length > 0);

  /** The top five §7.1 draws. The link beside them is where the rest are. */
  const positionRows = useMemo(() => positions.slice(0, POSITIONS_LIMIT), [positions]);
  const positionCols = useMemo(() => positionColumns(currency), [currency]);
  const orderCols = useMemo(() => orderColumns(currency), [currency]);

  /** The count the server reported, and the reason it gives when it could not take one. */
  const reportedPositionCount = fromNullable(openPositionsCount, reasonOf("openPositionsCount"));
  const activeStrategies = fromNullable(
    strategyCounts?.active ?? null,
    "The server did not report how many strategies are active.",
  );
  const pausedStrategies = fromNullable(
    strategyCounts?.paused ?? null,
    "The server did not report how many strategies are paused.",
  );

  /*
   * The two `health` figures §7.1 names, each read from its declared path.
   *
   * `exchange_api_latency_ms` is genuinely `null` when nothing measured a round trip, and
   * `health.exchange_api_latency_status` reads `"unavailable"` beside it. It renders the
   * marker with the declared reason and NEVER `0 ms`: a zero-latency exchange call is not a
   * thing, so the figure would be read as a claim about a working connection.
   */
  const exchangeLatency = fromNullable(
    firstNumber(systemHealth?.exchange_api_latency_ms),
    reasonOf("exchangeApiLatencyMs"),
  );
  const orderStateSync = fromNullable(
    firstText(systemHealth?.order_state_sync_status),
    "The engine did not report an order-state sync status.",
  );
  /** `exchange.can_trade` — one flag for the ACCOUNT, so it is reported once, not per venue. */
  const canTrade = typeof payload?.exchange?.can_trade === "boolean"
    ? payload.exchange.can_trade
    : null;

  const healthState = zoneState(systemHealth !== null || venues.length > 0);

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
                    {/* `venues` is the same `exchange.exchanges[]` list the popover read as
                        `exchangeConnections` before part B renamed it; the count is the
                        same count. Task 19.2 folds this row into the health panel. */}
                    <span style={{ color: "#f8fafc", fontWeight: 600 }}>
                      {venues.length} Active
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

      {/* ═══ TIER 2 — Requirements 3.2, 3.5, 14.5 (task 19.1 part B) ════════════════
          §7.1's five regions, in ONE container that follows tier 1 in document order:
          active strategies, open positions, system & exchange health, recent signals,
          recent orders, and the equity curve. Every region is a `ds/Panel` driven by the
          ONE read's state through `zoneState`, so a region with nothing in it says so
          (Requirement 14.1) instead of rendering a table with no rows or a figure at `0`.

          Each declared field carries `data-region` spelled as its `pageFields` key, so
          "the declared element rendered, and it rendered below tier 1" is decidable from
          the DOM (Property 4, task 19.5). §7.1 has no tier 3 and none is invented.

          The eight hand-styled zone shells this replaces each set their own `#0c1017`
          background, `#1e293b` border, radius and padding, and each drew its own heading
          and "view all" button; the page-level `font-mono` that held them together is gone
          too, because `ds/DataTable` puts mono on the numeric columns that should carry it
          and nowhere else. */}
      <div
        {...{ [TIER_PAGE_ATTRIBUTE]: PAGES.DASHBOARD, [TIER_ATTRIBUTE]: 2 }}
        className="flex min-w-0 flex-col gap-4"
      >

        {/* ── ROW 1: the fleet, the positions, the venues ─────────────────────────── */}
        <div className="grid min-w-0 grid-cols-4 gap-4">

          {/* ── ACTIVE STRATEGIES — `strategies.active`, `.paused`, `.items[].status` ──
              `items` IS the fleet: the aggregation service builds `total` as
              `len(strategies)` and `items` as the same list, so a "total" figure beside
              the rows would be the same count said twice. An empty `items` is therefore
              the empty state rather than a row of zeros — "nothing is trading this
              account" is the whole of what `active: 0` means, said in words a trader can
              act on. */}
          <Panel
            title={labelOf("activeStrategyCount")}
            state={zoneState(strategies.length > 0)}
            loading={{ kind: "skeleton-cards", rows: 3 }}
            empty={STRATEGIES_EMPTY}
            actions={<PanelLink to="/app/strategies">Manage strategies</PanelLink>}
            data-region="activeStrategyCount"
          >
            <div className="mb-4 grid grid-cols-2 gap-4">
              <Metric
                tier={2}
                label={labelOf("activeStrategyCount")}
                value={activeStrategies}
                format="integer"
                hint="Strategies the server reports as active on this account."
              />
              <Metric
                tier={2}
                label="Paused strategies"
                value={pausedStrategies}
                format="integer"
                hint="Strategies the server reports as paused. A paused strategy holds its
                  positions and stops acting on signals."
              />
            </div>

            {/* One row per deployment. `ds/StrategyStatus` takes the server's `status` and
                `health` verbatim and says "Status not reported" for an absent one — the
                `|| "paused"` / `|| "idle"` defaults this list used to substitute claimed a
                running strategy was stopped. */}
            <ul className="flex min-w-0 flex-col gap-2">
              {strategies.map((strategy) => (
                <li
                  key={strategy.id}
                  className="flex min-w-0 flex-col gap-1 rounded-sm border border-line-subtle bg-surface-canvas px-3 py-2"
                >
                  <div className="flex min-w-0 items-baseline justify-between gap-2">
                    <span className="truncate text-body font-semibold text-content-primary">
                      {strategy.name === null ? (
                        <NotAvailableMarker
                          label="Strategy name"
                          reason="This deployment reported no name."
                        />
                      ) : strategy.name}
                    </span>
                    {strategy.market === null ? null : (
                      <span className="shrink-0 font-mono text-micro text-content-secondary">
                        {strategy.market}
                      </span>
                    )}
                  </div>
                  <StrategyStatus status={strategy.status} health={strategy.health} compact />
                  {/* The server's own account of the failure, verbatim — it is the only
                      thing on screen that says WHY a strategy stopped. */}
                  {strategy.errorMessage === null ? null : (
                    <p className="text-micro text-content-secondary">{strategy.errorMessage}</p>
                  )}
                </li>
              ))}
            </ul>
          </Panel>

          {/* ── OPEN POSITIONS — BC-2's two arms, then the top five ───────────────────
              The wrapper is the region: it renders in BOTH arms, carries the read state as
              an attribute, and holds the degraded alert ABOVE the panel so the server's
              account of the failure precedes the panel it explains. */}
          <div
            className="col-span-2 flex min-w-0 flex-col gap-3"
            data-region="positionsDegraded"
            data-positions-read={positionsDegraded === null ? "complete" : "degraded"}
          >
            {positionsDegraded === null ? null : (
              <Alert
                severity="warning"
                title="The server could not read your open positions"
                action={{ label: "Try again", onClick: refetch }}
                data-region="positions-degraded"
              >
                {/* Verbatim, and not paraphrased: the server knows which environment failed
                    and why, `translateError` carries no free-form message by design
                    (Requirement 14.4), and this is the only place that account reaches the
                    screen. Without it, `positions: []` and a failed read look identical. */}
                {positionsDegraded}
              </Alert>
            )}

            <Panel
              title={labelOf("openPositions")}
              money
              environment={panelEnvironment}
              state={positionsState}
              loading={{ kind: "skeleton-table", rows: 5, columns: 8 }}
              empty={POSITIONS_EMPTY}
              // No error object to translate: nothing rejected. `ds/ErrorState` renders the
              // `dashboard` context copy beneath the server's own reason above.
              error={{ error: null, context: "dashboard", onRetry: refetch }}
              actions={<PanelLink to="/app/portfolio">View all in Portfolio</PanelLink>}
              data-region="openPositions"
            >
              <div className="mb-4">
                <Metric
                  tier={2}
                  label={labelOf("openPositionsCount")}
                  value={reportedPositionCount}
                  format="integer"
                  hint="The count the server reported. It is `null` — never zero — when the
                    positions could not be read, because a count of zero is the
                    safest-looking figure a broken read could publish."
                  data-region="openPositionsCount"
                />
              </div>

              {/* The top five §7.1 draws, in the order the server returned them. The
                  remaining rows are `/app/portfolio`'s, which is what the link above is
                  for — a truncated list with no way to the rest is a truncated list. */}
              <DataTable
                columns={positionCols}
                rows={positionRows}
                getRowId={(row) => row.id}
                // The percentage is not a projected column, so the cell that shows it beside
                // the P&L declares it here: `observe`'s RESULT is what the row memo compares,
                // so a tick cannot leave a stale percentage in a cell (§13.2c).
                observe={(row) => ({ unrealisedPnlPct: row.unrealisedPnlPct })}
                caption={currency
                  ? `Open positions, ${panelEnvironment} account, in ${currency}`
                  : `Open positions, ${panelEnvironment} account`}
                density="compact"
                totalCount={positionRows.length}
              />
            </Panel>
          </div>

          {/* ── SYSTEM & EXCHANGE HEALTH — the measured figures, then the venues ───── */}
          <Panel
            title="System &amp; exchange health"
            state={healthState}
            loading={{ kind: "skeleton-cards", rows: 3 }}
            empty={HEALTH_EMPTY}
            actions={<PanelLink to="/app/exchange">Manage venues</PanelLink>}
            data-region="exchangeHealth"
          >
            <div className="mb-4 grid grid-cols-2 gap-4">
              <Metric
                tier={2}
                label={labelOf("exchangeApiLatencyMs")}
                value={exchangeLatency}
                format="integer"
                unit="ms"
                hint="A measured round trip to the exchange API. Absent means nobody has
                  measured one — not that the link is instant."
                data-region="exchangeApiLatencyMs"
              />
              <Metric
                tier={2}
                label={labelOf("orderStateSync")}
                value={orderStateSync}
                format="raw"
                hint="Whether the engine's order records are in step with the exchange's."
                data-region="orderStateSync"
              />
            </div>

            {/* `exchange.can_trade` is ONE flag for the account, not one per venue, so it is
                reported once here rather than as a chip on every row — which would read as
                a per-venue permission the response does not carry. */}
            <div className="mb-3 flex min-w-0 items-center gap-2">
              <span className="text-micro uppercase tracking-wide text-content-secondary">
                Trade permission
              </span>
              <StatusBadge
                state={canTrade === true ? "ok" : (canTrade === false ? "blocked" : "unknown")}
                label={canTrade === true
                  ? "Can trade"
                  : (canTrade === false ? "Cannot trade" : "Not reported")}
                size="sm"
              />
            </div>

            {venues.length === 0 ? (
              <EmptyState {...HEALTH_EMPTY} />
            ) : (
              <>
                <ul className="flex min-w-0 flex-col gap-2">
                  {venues.map((venue) => (
                    <li
                      key={venue.id}
                      className="flex min-w-0 flex-col gap-1 rounded-sm border border-line-subtle bg-surface-canvas px-3 py-2"
                    >
                      {/* Neither `connectionState` nor `latencyMs` is passed: both are
                          constants in the aggregation service (`"connected"` and `35`), so
                          `ds/ExchangeStatus` reports each as not reported rather than as a
                          measurement. See the module docblock. */}
                      <ExchangeStatus exchange={venue.exchange ?? venue.id} />
                      <VenueLastSync lastSync={venue.lastSync} />
                    </li>
                  ))}
                </ul>
                <p className="mt-3 text-micro text-content-secondary">{VENUE_CONSTANT_NOTE}</p>
              </>
            )}
          </Panel>
        </div>

        {/* ── ROW 2: what the engine decided, and what the venue filled ───────────────
            Two panels rather than the one box §7.1 draws, because signals and orders are
            two lists that arrive and empty independently: one panel would have to pick a
            single §11.1 state for both, and the first empty list would either suppress the
            other or be described by a message that is only true of half the panel. */}
        <div className="grid min-w-0 grid-cols-2 gap-4">

          <Panel
            title={labelOf("recentSignals")}
            state={zoneState(signals.length > 0)}
            loading={{ kind: "skeleton-table", rows: 5, columns: 2 }}
            empty={SIGNALS_EMPTY}
            actions={<PanelLink to="/app/signal-trace">Open Signal Trace</PanelLink>}
            data-region="recentSignals"
          >
            <DataTable
              columns={SIGNAL_COLUMNS}
              rows={signals}
              getRowId={(row) => row.id}
              caption="Recent signals, most recent first"
              density="compact"
              totalCount={signals.length}
            />
          </Panel>

          <Panel
            title={labelOf("recentOrders")}
            money
            environment={panelEnvironment}
            state={zoneState(executions.length > 0)}
            loading={{ kind: "skeleton-table", rows: 5, columns: 6 }}
            empty={ORDERS_EMPTY}
            actions={<PanelLink to="/app/trades">Open trade history</PanelLink>}
            data-region="recentOrders"
          >
            <DataTable
              columns={orderCols}
              rows={executions}
              getRowId={(row) => row.id}
              caption={currency
                ? `Recent order executions, ${panelEnvironment} account, in ${currency}`
                : `Recent order executions, ${panelEnvironment} account`}
              density="compact"
              totalCount={executions.length}
            />
          </Panel>
        </div>

        {/* ── ROW 3: the equity curve ─────────────────────────────────────────────────
            One `ds/Chart`, imported lazily, behind its own `memo` and `Suspense`
            boundaries. The period that selects the window is in `PageHeader`, because
            `equity_days` is a parameter of the page's ONE read and not a chart setting:
            the in-panel selector this replaces issued a SECOND `getDashboard` and wrote
            the series from it, which is two reads and two failure surfaces for one
            figure. */}
        <Panel
          title={labelOf("equityCurve")}
          money
          environment={panelEnvironment}
          state={zoneState(equityCurve.length > 0)}
          loading={{ kind: "skeleton-chart" }}
          empty={EQUITY_EMPTY}
          data-region="equityCurve"
        >
          <EquityCurveChart rows={equityCurve} currency={currency} />
        </Panel>
      </div>

        </>
      )}

    </div>
  );
}
