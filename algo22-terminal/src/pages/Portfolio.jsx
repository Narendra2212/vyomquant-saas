/**
 * ═══════════════════════════════════════════════════════════════════════════
 * pages/Portfolio — the account (`/app/portfolio`)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign tasks 16.1 (tier 1) and 16.2 (tiers 2 and 3).
 * design.md §7.6, §11.3, §11.4, §13.2. Requirements 10.1, 10.2, 10.3, 10.4, 14.5, 15.5,
 * 19.3. Properties P4 (task 16.3), P5.
 *
 * THREE TIERS, THREE MARKED CONTAINERS
 * ------------------------------------
 * `design/pageHierarchy.js` declares which figure belongs to which tier and
 * `design/pageFields.js` declares where each one's value comes from and what a trader
 * reads when there is none. Neither is restated here. Each of the three regions below
 * carries `data-page` + `data-page-tier`, so Property 4 (task 16.3) can assert from the
 * rendered DOM that every tier-*n* element precedes every tier-*(n+1)* element:
 *
 *   TIER 1  total value │ available │ invested │ unrealised P&L │ realised P&L (today) │
 *           realised P&L (lifetime) │ total exposure │ current drawdown
 *   TIER 2  open positions — SUMMARY above DETAIL (Requirement 10.3)
 *   TIER 3  allocation │ equity curve │ daily P&L
 *
 * REQUIREMENT 10.3 IS A DOCUMENT-ORDER CLAIM, AND IT IS MADE STRUCTURALLY
 * ----------------------------------------------------------------------
 * The summary row — position count, long/short split, net exposure, largest position —
 * and the per-position table are siblings inside one tier-2 container, summary first.
 * They are not two panels a reviewer has to keep in order: they share a panel, so the
 * summary cannot drift below the table without moving inside the same JSX block.
 *
 * The four summary figures are DERIVED from the rows on screen, and each says so in its
 * tooltip. Two of them can be unknowable while the rows are perfectly readable: a net
 * exposure summed over rows where one row's notional is missing is not a net exposure,
 * and the largest position by notional cannot be picked when no row reports one. Both
 * render the marker with a reason naming how many rows are short, rather than a total
 * computed over the subset that happened to arrive (Requirement 14.5).
 *
 * THE POSITIONS READ HAS THREE OUTCOMES, AND `degraded` IS WHAT SEPARATES TWO OF THEM
 * ----------------------------------------------------------------------------------
 * `GET /api/dashboard` answers `positions: []` BOTH for an account holding nothing and
 * for a positions read that failed, and BC-2's top-level `degraded` marker is the only
 * thing that tells them apart (design.md §1.6, Requirement 14.5). So:
 *
 *   * request did not complete            → `Panel state="error"` → `ds/ErrorState`;
 *   * completed, `degraded.positions` says `"unreadable"` → the SERVER's own `reason`
 *     verbatim in a `ds/Alert`, and the panel in `error` beneath it. The alert exists
 *     because `ds/ErrorState` renders only `translateError` output and that translation
 *     deliberately carries no free-form server prose (Requirement 14.4) — so the
 *     server's account of which environment failed and why would otherwise be lost,
 *     which is what task 13.1 put on screen and this task must not regress;
 *   * completed, `degraded` null          → `positions` is the truth, `[]` included, and
 *     `[]` is `Panel state="empty"` → `ds/EmptyState`.
 *
 * In none of those three does a `<table>` render for a failure. `ds/Panel` does not
 * render children outside `ready`/`refreshing`, so that is structural rather than a
 * condition somebody remembered to write.
 *
 * CHARTS NEVER REFRESH ON A WEBSOCKET TICK (§13.2)
 * -----------------------------------------------
 * Guaranteed twice, because once is a promise and twice is a property:
 *
 *   1. **This page subscribes to nothing.** There is no `wsClient` import, no
 *      `useLiveChannel`, no interval. Every piece of chart state is written in exactly
 *      one place — {@link Portfolio}'s `loadPortfolioData` — which runs on mount, on
 *      Refresh, on a ledger change and on a period change. There is no code path from a
 *      frame to this page's state at all.
 *   2. **The three chart regions are `memo`d on read-derived props.** {@link
 *      AllocationChart}, {@link EquityCurveChart} and {@link DailyPnlChart} receive
 *      `useMemo`'d arrays whose identity changes only when the REST read that produced
 *      them completes. So even a re-render arriving from somewhere else — a future
 *      leaf-level subscription elsewhere on the page, a parent re-render — stops at the
 *      memo boundary and recharts is not re-entered.
 *
 * `ds/Chart` is the one primitive imported lazily rather than by path: recharts is
 * ~350KB and §13.3 keeps it out of the chunk of every page that does not chart. This
 * page charts, so importing it here is correct; importing it *statically* would hoist
 * `vendor-recharts` into the entry graph and defeat the split for the pages that do not.
 *
 * WHAT HAPPENED TO THE ALLOCATION PIE, AND ITS FIVE-COLOUR PALETTE
 * ---------------------------------------------------------------
 * It was `const COLORS = [C.orange, C.purple, C.cyan, C.gold, C.t3]` — five entries
 * rendering four colours, because M1 collapsed `C.orange` and `C.gold` onto the one
 * amber (`status.warning`) and `C.purple` onto neutral. Two slices of a five-slice pie
 * were therefore the same hue, which is worse than four slices.
 *
 * It is not repaired with five different tokens, because there is no honest fifth
 * categorical hue to reach for. `styles/tokens.css` declares six non-surface hues —
 * brand cyan, one teal for live/connected/profit, one red for loss/error, one amber for
 * warning/guidance, one grey neutral and the paper indigo — and every one of them
 * except brand already means something: a green slice for BTC and a red one for ETH
 * read as profit and loss on a page whose whole job is P&L. `ds/Chart` says the same
 * thing in code — it exposes six semantic series tokens and NO colour prop, and it has
 * no `pie` kind at all (`CHART_KINDS` is area | line | bar).
 *
 * So the allocation is a **bar chart with one `brand` series**: assets are told apart by
 * position along a labelled axis, which works for five assets and for fifty, and needs
 * no hue to do it. The `{asset, value_usd, pct}` rows the route actually answers are
 * also rendered as a small `ds/DataTable` beside it, so `value_usd` — which cannot share
 * the chart's y axis, being a different unit from a percentage — is not dropped, and the
 * chart has an accessible tabular equivalent. That table is also what retires the
 * `<ul role="list">` / `<li role="listitem">` legend the a11y ratchet waived two
 * findings for.
 *
 * WHAT PAPER DOES *NOT* HAVE
 * --------------------------
 * `/api/portfolio/allocation`, `/equity-curve` and `/heatmap` are live-account reads.
 * The paper account has no equivalent, so all three tier-3 panels render
 * `Panel state="unavailable"` with that reason (Requirement 19.3). The row this page
 * used to synthesise for paper — `{asset: 'USD (Simulated)', percentage: 100}` with the
 * account's equity as its value — is deleted: a 100% allocation nobody reported is a
 * fabricated reading, and a fabricated reading on this page is the defect the whole
 * `pageFields` declaration exists to prevent.
 *
 * @module pages/Portfolio
 */

import {
  lazy,
  memo,
  Suspense,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useState,
} from "react";
import { Activity, Layers, RefreshCw, TrendingUp, Wallet } from "lucide-react";

import { api } from "../api";
import { Alert } from "../components/ds/Alert";
import { CommandButton } from "../components/ds/CommandButton";
import { DataTable } from "../components/ds/DataTable";
import { LoadingState } from "../components/ds/LoadingState";
import { Metric, NotAvailableMarker } from "../components/ds/Metric";
import { PageHeader } from "../components/ds/PageHeader";
import { Panel } from "../components/ds/Panel";
import { PnLDisplay } from "../components/ds/PnLDisplay";
import { SectionHeader } from "../components/ds/SectionHeader";
import { StatusBadge } from "../components/ds/StatusBadge";
import { TradingEnvironmentBadge } from "../components/ds/TradingEnvironmentBadge";
import { PAGES, PAGE_FIELDS_BY_PAGE, VERDICT } from "../design/pageFields";
import {
  PAGE_HIERARCHY_BY_PAGE,
  TIER_ATTRIBUTE,
  TIER_PAGE_ATTRIBUTE,
} from "../design/pageHierarchy";
import { fromNullable, unavailable } from "../design/reported";
import { PANEL_STATES } from "../hooks/usePanelState";

/**
 * `ds/Chart`, lazily.
 *
 * The one primitive on this page that is not imported by path, and the reason is in
 * `Chart.jsx`'s own docblock: it is the only module in `src/` that may import recharts,
 * it is deliberately absent from `ds/index.js`, and a static import here would hoist
 * `vendor-recharts` into this route's chunk graph. `lazy()` needs a module whose
 * `default` is the component, which `Chart.jsx` provides for exactly this call site.
 */
const Chart = lazy(() => import("../components/ds/Chart"));

/* ══════════════════════════════════════════════════════════════════════════
 * THE DECLARATION
 * ══════════════════════════════════════════════════════════════════════════ */

const PORTFOLIO_FIELDS = PAGE_FIELDS_BY_PAGE[PAGES.PORTFOLIO] ?? [];

/** The declared tiers, split once. */
const TIERS = PAGE_HIERARCHY_BY_PAGE[PAGES.PORTFOLIO]?.tiers ?? [];

/** §7.6's tier 1, in declaration order. */
const TIER_ONE = TIERS.filter((entry) => entry.tier === 1);

/** One field's declaration. */
const fieldEntry = (field) => PORTFOLIO_FIELDS.find((entry) => entry.field === field) ?? null;

/** One field's declared label. Read, never retyped — see `pageHierarchy.js`'s header. */
const labelOf = (field) => fieldEntry(field)?.label ?? field;

/** One field's declared reason, which is the sentence rendered instead of the figure. */
const reasonOf = (field) => fieldEntry(field)?.reason ?? undefined;

/**
 * How each tier-1 figure is formatted. The ONLY per-field thing this page decides.
 *
 * Not in `pageFields.js` because a format is a rendering choice and that module holds none;
 * not derived from the label either, because "Realised P&L (today)" and "Current drawdown"
 * differ in units and nothing in the name says so. `precision: 2` on the money figures is
 * this page's existing spelling of a balance; `ds/Metric` rounds only when asked, so
 * omitting it would print an exchange's ten-decimal figure in full.
 */
const TIER_ONE_FORMAT = Object.freeze({
  totalValue: Object.freeze({ format: "currency", precision: 2 }),
  availableBalance: Object.freeze({ format: "currency", precision: 2 }),
  investedCapital: Object.freeze({ format: "currency", precision: 2 }),
  unrealisedPnl: Object.freeze({ format: "currency", precision: 2 }),
  realisedPnlToday: Object.freeze({ format: "currency", precision: 2 }),
  lifetimeRealizedPnl: Object.freeze({ format: "currency", precision: 2 }),
  totalExposure: Object.freeze({ format: "currency", precision: 2 }),
  // A percentage, already in percent units on the wire. `ds/Metric` does not multiply by
  // 100 — a 3.2% drawdown rendered as 320% would be read as a wiped-out account.
  currentDrawdown: Object.freeze({ format: "percent", precision: 2 }),
});

/**
 * The ⚠️ derived figure's operand, spelled once.
 *
 * `investedCapital` has no path of its own because no backend field is named "invested
 * capital"; `pageFields` declares it derived from `overview.used_balance` and carries both
 * the label ("Invested (capital in use)") and the tooltip stating the derivation. Reading
 * the operand by name here rather than by position in the entry's `inputs` keeps the page
 * honest about which of the three inputs it actually reads.
 */
const TIER_ONE_DERIVATIONS = Object.freeze({
  investedCapital: "overview.used_balance",
});

/* ══════════════════════════════════════════════════════════════════════════
 * READING A RESPONSE — nothing here defaults, and nothing substitutes
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * A dotted path off a response body, or `undefined`.
 *
 * `[]` never appears in a tier-1 path — every one of the eight is a scalar under `overview`
 * or `risk` — so there is no list-element case to handle here.
 *
 * @param {unknown} body
 * @param {string} dottedPath
 */
const readPath = (body, dottedPath) =>
  dottedPath.split(".").reduce(
    (node, key) => (node && typeof node === "object" ? node[key] : undefined),
    body,
  );

/**
 * `GET /api/dashboard` → the tier-1 view model: one `Reported<T>` per declared field.
 *
 * The consumption pattern `pageFields.js`'s header documents, with one addition: a ⚠️
 * derived field reads its declared operand instead of a path of its own. Nothing here
 * defaults, and nothing substitutes: a field the response did not carry becomes the
 * unavailable arm with the entry's own reason, which `ds/Metric` renders as the marker plus
 * that sentence (Requirements 14.5, 19.3).
 *
 * @param {unknown} body A resolved `GET /api/dashboard` body.
 * @returns {Object<string, {available: boolean}>}
 */
const buildTierOne = (body) => {
  const model = {};
  for (const { key } of TIER_ONE) {
    const entry = fieldEntry(key);
    const path = entry.verdict === VERDICT.AVAILABLE
      ? entry.path
      : TIER_ONE_DERIVATIONS[key] ?? null;
    model[key] = path === null
      ? unavailable(entry.reason)
      : fromNullable(readPath(body, path), entry.reason);
  }
  return model;
};

/**
 * `overview.currency`, or `null`.
 *
 * The denomination of the seven money figures in the same `overview` block, rendered beside
 * them as `ds/Metric`'s `unit` and named in the positions table's caption. It replaces the
 * hardcoded `$` these cards used to carry, which was a claim the response contradicts: the
 * live account reports `USDT` and the paper account `USD`. `ds/Metric` renders a unit only
 * beside a real figure, so an absent currency costs nothing and no marker is labelled with a
 * denomination.
 *
 * @param {unknown} body
 * @returns {string|null}
 */
const readCurrency = (body) => {
  const value = readPath(body, "overview.currency");
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
};

/**
 * The first candidate that is a finite number, or `null`.
 *
 * Replaces the `?? 0` / `parseFloat(x || 0)` chains this page used to end its field reads
 * with. Those chains turned "the server sent no such field" into a displayed quantity —
 * a size of 0, an entry price of $0.00, a leverage of 0 — which is what Requirement 14.5
 * forbids. `null` travels to the cell and renders the not-available marker instead.
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

/**
 * The positions list off a response body, or `null` when the body carries no list at all.
 *
 * Two shapes reach this page and both are read here rather than one being assumed:
 *
 * * **An envelope.** `GET /api/paper/positions` answers `{positions, count,
 *   execution_environment, is_simulated, session_id}`, and the shared client's `get`
 *   resolves to the response BODY — so `api.paper.getPositions()` resolves to that object,
 *   never to an array. `GET /api/dashboard`, where the LIVE read points (task 13.1), is the
 *   same form: `positions` sits beside `overview`, `risk` and `degraded`.
 * * **A bare array.** The three live portfolio reads (`allocation`, `equity-curve`,
 *   `heatmap`) all answer bare lists, so a positions read answering one is plausible.
 *
 * `null` for anything else is deliberate: a body that carries neither shape is a read this
 * page cannot interpret, and it is reported as such rather than as "you hold no positions".
 *
 * @param {unknown} body A resolved response body.
 * @returns {Array<Object>|null}
 */
const readPositionsList = (body) => {
  if (Array.isArray(body)) return body;
  if (body && typeof body === "object" && Array.isArray(body.positions)) return body.positions;
  return null;
};

/**
 * BC-2's `degraded` marker, off a `GET /api/dashboard` body.
 *
 * `dashboard_aggregation_service.py` publishes one top-level `degraded` key: `None` when
 * every read behind the response succeeded, and `{positions: "unreadable", environment,
 * reason}` when the positions read did not. `positions` is `[]` in BOTH cases — the list
 * itself does not lie, it is simply empty — so this marker is the only thing that tells "the
 * account holds nothing" apart from "nobody could find out", and a client that renders an
 * empty table without consulting it publishes an outage as a fact about the account
 * (design.md §1.6, Requirement 14.5).
 *
 * The returned string is the server's own prose `reason`, rendered verbatim. It is not
 * paraphrased: the server knows which environment failed and why, and a sentence composed on
 * the client would be a second, drifting account of the same event. A marker that arrives
 * without a reason still yields a failure — the absence of the explanation is reported rather
 * than filled in.
 *
 * @param {unknown} body A resolved `GET /api/dashboard` body.
 * @returns {string|null} The reason to render, or `null` when the positions read was fine.
 */
const readPositionsDegradation = (body) => {
  const degraded = body && typeof body === "object" ? body.degraded : null;
  if (!degraded || typeof degraded !== "object") return null;
  if (degraded.positions !== "unreadable") return null;
  const reason = typeof degraded.reason === "string" ? degraded.reason.trim() : "";
  return reason || "The server reported this positions read as unreadable and gave no reason.";
};

/**
 * `risk.open_positions_count` off a `GET /api/dashboard` body, or `null`.
 *
 * BC-2's second channel, from the other end of the response: an `int` when the positions were
 * counted and `null` when they could not be read — never `0` as a stand-in, because a count
 * of zero is the *safest-looking* figure a broken positions read could publish.
 *
 * The count is taken from the server rather than from `positions.length` so the figure on
 * screen is the one the server computed. The two cannot disagree — BC-2 hands
 * `get_risk_metrics` the same gathered list `positions` comes from — but reading the reported
 * field is what makes the `null` case reachable at all.
 *
 * @param {unknown} body A resolved `GET /api/dashboard` body.
 * @returns {number|null}
 */
const readOpenPositionsCount = (body) => {
  const risk = body && typeof body === "object" ? body.risk : null;
  const count = risk && typeof risk === "object" ? risk.open_positions_count : null;
  return typeof count === "number" && Number.isFinite(count) ? count : null;
};

/**
 * The two additive provenance fields, read off a paper response body and nothing else.
 *
 * `backend_app/routers/paper_trading.py` returns `execution_environment` and `is_simulated`
 * on every paper body (task 23.3), including the `/api/paper/positions` envelope beside
 * `positions` and `count`. Nothing here defaults or invents either field — a body that
 * carries neither yields `null`, and `ds/TradingEnvironmentBadge` says
 * `SIMULATED · SERVER LABEL UNAVAILABLE` or `ENVIRONMENT UNCONFIRMED` rather than a label
 * manufactured on the client (Requirement 28.5, design.md §8.2).
 *
 * @param {any} body A resolved paper response body.
 * @returns {{environment: (string|null), isSimulated: boolean}|null}
 */
const readPaperProvenance = (body) => {
  if (!body || typeof body !== "object") return null;
  const environment = firstText(body.execution_environment);
  const isSimulated = body.is_simulated === true;
  if (!environment && !isSimulated) return null;
  return { environment, isSimulated };
};

/* ══════════════════════════════════════════════════════════════════════════
 * THE POSITIONS VIEW MODEL — Requirement 10.3's ten columns
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * One `NormalizedPosition` → one table row. Every numeric field is `null`-able.
 *
 * The field names are the ones `dashboard_aggregation_service` actually publishes
 * (`pageFields`' `openPositions` note): `symbol side contracts entry_price mark_price
 * notional leverage unrealized_pnl unrealized_pnl_pct liquidation_price margin margin_type
 * exchange_id environment`. The paper envelope spells three of them differently
 * (`quantity`, `current_price`), so both are read — but nothing is *invented*: a field
 * neither shape carries stays `null` and its cell renders the marker.
 *
 * `notionalRead` is kept separately from `notional` because the summary above the table has
 * to know how many rows reported one, and `null` in the row is the same `null` whether the
 * position is spot, paper or simply unreported.
 *
 * @param {Object} position
 * @param {number} index
 * @returns {Object}
 */
const toPositionRow = (position, index) => {
  const source = position && typeof position === "object" ? position : {};
  const side = firstText(source.side);
  const size = firstNumber(source.contracts, source.size, source.quantity);
  const entryPrice = firstNumber(source.entry_price, source.entryPrice);
  const markPrice = firstNumber(source.mark_price, source.current_price, source.markPrice);
  const notional = firstNumber(source.notional);

  return {
    id: firstText(source.id, source.position_id) ?? `position-${index}`,
    // The market, not a guessed one: `"UNKNOWN"` used to be substituted here, which put a
    // tradeable-looking symbol in a cell for a row that named none.
    //
    // `exchange_id` is deliberately NOT read. §7.6's column list is the ten below and a venue
    // is not among them, and the table this replaces defaulted the cell to `"binance"` on live
    // and `"paper"` on paper — a venue name for a row that may have named a different one.
    // Exchange health is a page-level figure from `GET /api/dashboard` (§7.1), not a per-row
    // one, so dropping the column loses no reading a trader could act on.
    market: firstText(source.symbol, source.market),
    side: side === null ? null : side.toLowerCase(),
    size,
    entryPrice,
    markPrice,
    // Reported when the venue reported it, and otherwise derived — but only when BOTH
    // operands are real. `size * markPrice` over a missing mark is an exposure figure
    // computed from nothing, so the `null` survives to the cell and to the summary above it.
    notional: notional !== null
      ? notional
      : (size !== null && markPrice !== null ? size * markPrice : null),
    leverage: firstNumber(source.leverage),
    unrealisedPnl: firstNumber(source.unrealized_pnl, source.unrealizedPnl),
    unrealisedPnlPct: firstNumber(source.unrealized_pnl_pct),
    // Permanently `null` for spot and paper, and for any futures position the venue did not
    // report one for. `pageFields`' `positionLiquidationPrice` entry carries the sentence.
    liquidationPrice: firstNumber(source.liquidation_price, source.liquidationPrice),
    margin: firstNumber(source.margin),
    marginType: firstText(source.margin_type),
  };
};

/** `long` and `short` as a `ds/StatusBadge` state — `semantic.js` maps them to profit/loss. */
function SideCell({ value }) {
  if (value === null || value === undefined) {
    return <NotAvailableMarker label="Side" reason="The position did not report a side." />;
  }
  return <StatusBadge state={value} size="sm" />;
}

/**
 * The liquidation cell. `null` is NOT-APPLICABLE, and it says so in the field's own words.
 *
 * `ds/DataTable`'s built-in marker renders an em-dash with an accessible name of
 * "Not available" and no reason, which for this column would be the least informative thing
 * on screen: a trader looking at a blank liquidation cell needs to know whether the venue
 * failed to report a level or whether the position cannot be liquidated at all. The sentence
 * is `pageFields`' — *"Not applicable — spot positions have no liquidation price."* — read
 * from the declaration rather than written here, so the page and the audit cannot drift.
 */
function LiquidationCell({ value }) {
  if (value === null || value === undefined) {
    return (
      <NotAvailableMarker
        label={labelOf("positionLiquidationPrice")}
        reason={reasonOf("positionLiquidationPrice")}
      />
    );
  }
  return <span>{value}</span>;
}

/**
 * §7.6's ten columns, built once per denomination.
 *
 * `align: 'numeric'` on the eight quantities is Requirement 11.3 — alignment is a column
 * property, so it cannot be decided per cell and cannot be inconsistent. `priority: 3` on
 * leverage, margin and the venue takes them out of the row below `--breakpoint-laptop` and
 * into `ds/DataTable`'s per-row expander (Requirement 17.2); the DOM is the same at every
 * width, the reduction is CSS.
 *
 * @param {string|null} currency The denomination `overview.currency` reported, or `null`.
 */
const positionColumns = (currency) => Object.freeze([
  { key: "market", header: "Market", align: "text", format: "symbol", sortable: true, priority: 1 },
  { key: "side", header: "Side", align: "text", format: "text", sortable: true, render: SideCell, priority: 1 },
  { key: "size", header: "Size", align: "numeric", format: "number", sortable: true, priority: 1 },
  { key: "entryPrice", header: "Entry", align: "numeric", format: "currency", sortable: true, priority: 1 },
  { key: "markPrice", header: "Mark", align: "numeric", format: "currency", sortable: true, priority: 1 },
  { key: "notional", header: "Notional", align: "numeric", format: "currency", sortable: true, priority: 2 },
  { key: "leverage", header: "Leverage", align: "numeric", format: "number", sortable: true, priority: 3 },
  {
    key: "unrealisedPnl",
    header: "Unrealised P&L",
    align: "numeric",
    format: "currency",
    sortable: true,
    priority: 1,
    // `PnLDisplay` takes `value` / `row` / `column` from `DataTable` directly, which is why
    // §11.3 writes `render: PnLDisplay` verbatim. The currency travels with it so a figure
    // is never denominated in a symbol the account does not use.
    render: function UnrealisedPnlCell({ value }) {
      return <PnLDisplay value={value} currency={currency ?? undefined} precision={2} label="Unrealised P&L" />;
    },
  },
  { key: "liquidationPrice", header: "Liquidation", align: "numeric", format: "currency", sortable: true, render: LiquidationCell, priority: 2 },
  { key: "margin", header: "Margin", align: "numeric", format: "currency", sortable: true, priority: 3 },
]);

/* ══════════════════════════════════════════════════════════════════════════
 * REQUIREMENT 10.3's SUMMARY — four figures, each derived, each honest about it
 * ══════════════════════════════════════════════════════════════════════════ */

/** Stated on every summary figure, because all four are computed from the rows on screen. */
const SUMMARY_DERIVATION = "Derived from the open positions listed below.";

const NO_NOTIONAL_REASON =
  "No open position reports a notional value, so there is nothing to total.";

/** How many rows are short of a notional, said as a sentence rather than as a subset total. */
const partialNotionalReason = (missing, total) =>
  `${missing} of ${total} open positions report no notional value, so a total over all of `
  + "them cannot be stated. The rows themselves are listed below.";

/**
 * The four Requirement 10.3 figures, as `Reported<T>`s.
 *
 * The rule the two aggregate figures follow is the same one `TradeHistory` applies to its
 * fee total: a sum is stated only when EVERY row contributes to it. Nine notionals out of
 * ten add up to a number, but that number is not the net exposure, and putting it under
 * that label would be a smaller version of the `?? 0` this page spent task 13.1 removing.
 *
 * `count` prefers the server's own figure (`risk.open_positions_count` live, the envelope's
 * `count` on paper) over `rows.length`, so the figure on screen is the one the server
 * computed; `null` there renders the marker rather than a length this page derived.
 *
 * @param {Array<Object>} rows
 * @param {number|null} reportedCount
 * @returns {{count: Object, split: Object, netExposure: Object, largest: Object}}
 */
const buildPositionsSummary = (rows, reportedCount) => {
  const total = rows.length;
  const longs = rows.filter((row) => row.side === "long").length;
  const shorts = rows.filter((row) => row.side === "short").length;
  const missingNotional = rows.filter((row) => row.notional === null).length;

  const countReason =
    "The server did not report a count for the open positions it returned.";

  const split = total === 0
    ? unavailable("There are no open positions to split.")
    : fromNullable(`${longs} long / ${shorts} short`, "No open position reports a side.");

  // Signed: a short's notional reduces net exposure. Summed over EVERY row or not at all.
  const netExposure = total === 0
    ? unavailable("There are no open positions to total.")
    : (missingNotional > 0
      ? unavailable(missingNotional === total
        ? NO_NOTIONAL_REASON
        : partialNotionalReason(missingNotional, total))
      : fromNullable(
        rows.reduce(
          (sum, row) => sum + (row.side === "short" ? -row.notional : row.notional),
          0,
        ),
        NO_NOTIONAL_REASON,
      ));

  // The largest by ABSOLUTE notional: a large short is a large position. Chosen only among
  // rows that reported one, and reported as not-available when none did — a "largest
  // position" picked by size when the sizes are in different units is not a ranking.
  const ranked = rows
    .filter((row) => row.notional !== null && row.market !== null)
    .sort((a, b) => Math.abs(b.notional) - Math.abs(a.notional));
  const largest = ranked.length === 0
    ? unavailable(total === 0
      ? "There are no open positions to rank."
      : NO_NOTIONAL_REASON)
    : fromNullable(ranked[0].market, NO_NOTIONAL_REASON);

  return {
    count: fromNullable(reportedCount, countReason),
    split,
    netExposure,
    largest,
  };
};

/* ══════════════════════════════════════════════════════════════════════════
 * TIER 3 — the three charts, each memoised at its own boundary
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The `Suspense` fallback height is `skeleton-chart`'s, which is the height `ds/Chart` lays
 * out at, so the panel does not resize when the recharts chunk lands (Requirement 14.2).
 */
function ChartFallback({ label }) {
  return <LoadingState kind="skeleton-chart" label={label} />;
}

/**
 * Allocation — a bar per asset, one `brand` series.
 *
 * A bar chart and not a pie, and one hue and not five: see the module docblock. Assets are
 * distinguished by position along a labelled axis, which needs no categorical palette and
 * does not stop working at the sixth asset.
 *
 * `memo` is the §13.2 boundary. `rows` is a `useMemo`'d array whose identity changes only
 * when the allocation read completes, so nothing short of a new REST read re-enters recharts.
 */
const AllocationChart = memo(function AllocationChart({ rows }) {
  return (
    <Suspense fallback={<ChartFallback label="Loading allocation" />}>
      <Chart
        kind="bar"
        data={rows}
        xAxis={{ key: "asset", label: "Asset", format: "text" }}
        yAxis={{ label: "Share of portfolio (%)", format: "percent" }}
        series={[{ key: "pct", name: "Share", token: "brand" }]}
        emptyMessage="No allocation was reported for this account"
      />
    </Suspense>
  );
});

/** Equity curve — `{timestamp, equity}` in ascending time order, as the route answers it. */
const EquityCurveChart = memo(function EquityCurveChart({ rows, currency }) {
  return (
    <Suspense fallback={<ChartFallback label="Loading equity curve" />}>
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

/**
 * Daily realised P&L — one bar per day with a recorded execution.
 *
 * ONE series, hued `brand`, which is the deliberate choice. `ds/Chart` declares a series'
 * colour once from what the series *is*, and a signed series is not one thing: splitting it
 * into a `profit` series and a `loss` series would leave a genuinely flat day — a day that
 * traded and netted zero — with no bar in either, which reads as a day nobody recorded.
 * `pageFields` is explicit that a day with no executions has NO ROW and is not a zero-P&L
 * day, so the two must stay distinguishable. The sign is carried by the zero baseline and
 * by the tooltip, not by hue.
 *
 * This replaces the seven-column calendar grid, whose every cell colour was an inline
 * `#10B981aa` / `#EF444455` literal and whose only accessible content was a `title`.
 */
const DailyPnlChart = memo(function DailyPnlChart({ rows, currency }) {
  return (
    <Suspense fallback={<ChartFallback label="Loading daily P&L" />}>
      <Chart
        kind="bar"
        data={rows}
        xAxis={{ key: "date", label: "Date", format: "date" }}
        yAxis={{ label: currency ? `Realised P&L (${currency})` : "Realised P&L", format: "currency" }}
        series={[{ key: "pnl", name: "Realised P&L", token: "brand" }]}
        emptyMessage="No daily P&L was recorded for this period"
      />
    </Suspense>
  );
});

/** The allocation table's three columns: the chart's accessible equivalent, plus `value_usd`. */
const ALLOCATION_COLUMNS = Object.freeze([
  { key: "asset", header: "Asset", align: "text", format: "symbol", sortable: true },
  { key: "pct", header: "Share (%)", align: "numeric", format: "number", sortable: true },
  { key: "value_usd", header: "Value (USD)", align: "numeric", format: "currency", sortable: true },
]);

/* ══════════════════════════════════════════════════════════════════════════
 * THE TWO PAGE-LEVEL CONTROLS
 * ══════════════════════════════════════════════════════════════════════════ */

/** The two ledgers, and the `ds/Panel` environment each one declares. */
const LEDGERS = Object.freeze([
  Object.freeze({ value: "live", label: "Live", environment: "LIVE" }),
  Object.freeze({ value: "paper", label: "Paper", environment: "PAPER" }),
]);

/**
 * The history period, which is the ONLY thing besides a REST read that may change a chart.
 *
 * §13.2: "they refresh on the page's REST read or on an explicit period change, never on a
 * WebSocket tick". Both arms are the same arm here — `period` is a dependency of
 * `loadPortfolioData`, so a period change *is* a REST read. That is deliberate: one code
 * path that writes chart state is one path to audit.
 */
const PERIODS = Object.freeze([
  Object.freeze({ value: "30d", label: "30D", days: 30, months: 1 }),
  Object.freeze({ value: "90d", label: "90D", days: 90, months: 3 }),
  Object.freeze({ value: "1y", label: "1Y", days: 365, months: 12 }),
]);

const DEFAULT_PERIOD = "90d";

/**
 * The one empty list, shared.
 *
 * Every region's state starts as `null` — "no read has answered" — and every render needs a
 * list to hand a memo. A fresh `[]` each time would change the identity of every derived
 * array on every render, which would take the `memo` boundary around each chart with it, and
 * the §13.2 guarantee is exactly that boundary holding.
 */
const NO_ROWS = Object.freeze([]);

const periodOf = (value) => PERIODS.find((entry) => entry.value === value) ?? PERIODS[1];

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
 * `TradeHistory`'s `LedgerSwitch` pattern, generalised over both of this page's controls: a
 * `<fieldset>` with a `<legend>` and one `<input type="radio">` per option, which gets
 * arrow-key movement, single selection, one tab stop and a real label association from the
 * browser. Two `<button>`s would have made the selected one a control that does nothing when
 * pressed (Requirement 19.4), and it is what the two hand-styled toggle buttons this
 * replaces were.
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
 * COPY
 * ══════════════════════════════════════════════════════════════════════════ */

const PAPER_HISTORY_UNAVAILABLE =
  "The paper account has no allocation, equity-history or daily-P&L read. "
  + "GET /api/portfolio/allocation, /equity-curve and /heatmap serve the live account only, "
  + "and nothing on the paper side reports the same figures.";

const POSITIONS_EMPTY = Object.freeze({
  icon: Wallet,
  headline: "No open positions",
  body: "Nothing in this account is exposed to the market right now. A position appears here "
    + "as soon as a deployed strategy opens one.",
  action: { label: "Review strategies", to: "/app/strategies" },
});

const ALLOCATION_EMPTY = Object.freeze({
  icon: Layers,
  headline: "No allocation reported",
  body: "The allocation breakdown is built from the assets this account holds. It appears once "
    + "the account holds one.",
  action: { label: "Review strategies", to: "/app/strategies" },
});

const EQUITY_EMPTY = Object.freeze({
  icon: TrendingUp,
  headline: "No equity history for this period",
  body: "The curve is drawn from recorded account equity. Widen the period, or connect an "
    + "exchange and deploy a strategy to start recording it.",
  action: { label: "Review strategies", to: "/app/strategies" },
});

const HEATMAP_EMPTY = Object.freeze({
  icon: Activity,
  headline: "No daily P&L recorded",
  body: "One bar per day on which this account executed a trade. Days with no executions have "
    + "no bar — they are not zero-P&L days.",
  action: { label: "Review trade history", to: "/app/trades" },
});

/* ══════════════════════════════════════════════════════════════════════════
 * THE PAGE
 * ══════════════════════════════════════════════════════════════════════════ */

export default function Portfolio() {
  const [environment, setEnvironment] = useState("live");
  const [period, setPeriod] = useState(DEFAULT_PERIOD);

  // TIER 1 (task 16.1). One `Reported<T>` per declared field, built by `buildTierOne` from the
  // `GET /api/dashboard` body — or `null` when there is no body to build one from, which is the
  // panel's error state and not a row of markers: a failed read is a fact about the read, and
  // eight per-figure "not reported" markers would state it eight times as facts about the
  // account (§7.4's rule for tier-1 figures on a failure, Requirement 14.5).
  const [tierOne, setTierOne] = useState(null);
  // The rejection itself, handed to `ds/Panel` → `ds/ErrorState` → `design/errorCopy`. Not a
  // message string: Requirement 14.4 forbids surfacing the internals, and the translation is
  // the only thing allowed to decide what a trader reads.
  const [tierOneError, setTierOneError] = useState(null);
  // `overview.currency`, the denomination of tier 1's seven money figures.
  const [currency, setCurrency] = useState(null);

  const [isLoading, setIsLoading] = useState(true);

  // TIER 2. `positions` is `null` — never `[]` — until a read has said something, so "the
  // account holds nothing" is never the initial state of this region.
  const [positions, setPositions] = useState(null);
  const [positionsCount, setPositionsCount] = useState(null);
  // The rejection behind a positions read that did not complete. `ds/ErrorState`'s input.
  const [positionsError, setPositionsError] = useState(null);
  // BC-2's server-authored prose, when the read completed and the server said the positions
  // were unreadable. Rendered verbatim in a `ds/Alert`; see the module docblock for why it
  // cannot go through `ds/ErrorState`.
  const [positionsDegraded, setPositionsDegraded] = useState(null);
  // The paper positions envelope's own `execution_environment` / `is_simulated`. Read from the
  // response, never from the switch above or the route.
  const [positionsProvenance, setPositionsProvenance] = useState(null);

  // TIER 3. `null` means no read has answered yet; `[]` means the read answered with nothing.
  const [allocation, setAllocation] = useState(null);
  const [allocationError, setAllocationError] = useState(null);
  const [equityCurve, setEquityCurve] = useState(null);
  const [equityError, setEquityError] = useState(null);
  const [dailyPnl, setDailyPnl] = useState(null);
  const [dailyPnlError, setDailyPnlError] = useState(null);

  const isPaper = environment === "paper";

  const loadPortfolioData = useCallback(async () => {
    const window = periodOf(period);

    setIsLoading(true);
    setTierOne(null);
    setTierOneError(null);
    setCurrency(null);
    setPositions(null);
    setPositionsCount(null);
    setPositionsError(null);
    setPositionsDegraded(null);
    setPositionsProvenance(null);
    setAllocation(null);
    setAllocationError(null);
    setEquityCurve(null);
    setEquityError(null);
    setDailyPnl(null);
    setDailyPnlError(null);

    /**
     * Tier 1, from one settled `GET /api/dashboard` read.
     *
     * Shared by both environments because tier 1's eight paths are the same eight paths in
     * both: `get_portfolio_overview` and `get_risk_metrics` branch on `environment`
     * server-side and answer the same key set, so the isolation Requirement 13.6 is about is
     * the server's and this page cannot get it wrong by mixing two bodies.
     *
     * @param {{status: string, value?: unknown, reason?: unknown}} settled
     */
    const applyTierOne = (settled) => {
      const body = settled.status === "fulfilled" ? settled.value : null;
      if (body && typeof body === "object") {
        setTierOne(buildTierOne(body));
        setCurrency(readCurrency(body));
        setTierOneError(null);
        return;
      }
      setTierOne(null);
      setCurrency(null);
      // `null` for a read that resolved to something unreadable: there is no rejection to
      // translate, and `errorCopy`'s `portfolio` context is the honest last resort.
      setTierOneError(settled.status === "rejected" ? settled.reason : null);
    };

    /**
     * The positions region, from one settled read plus its degradation marker.
     *
     * The three outcomes are tested in this order because the list looks perfectly healthy
     * in the middle one: `positions` is `[]` on a BC-2 degraded response, so the marker has
     * to be consulted BEFORE the list (design.md §1.6, Requirement 14.5).
     *
     * @param {{status: string, value?: unknown, reason?: unknown}} settled
     * @param {string|null} degradedReason
     * @param {number|null} reportedCount
     */
    const applyPositions = (settled, degradedReason, reportedCount) => {
      if (settled.status === "rejected") {
        setPositionsError(settled.reason ?? new Error("The positions read did not complete."));
        return;
      }
      if (degradedReason !== null) {
        // Still an error state for the region — `positionsState` reads this marker — so no
        // table is rendered. `positionsError` stays `null`: there is no rejection to
        // translate, and `errorCopy`'s `portfolio` context default is what `ds/ErrorState`
        // renders beneath the server's own account of the failure.
        setPositionsDegraded(degradedReason);
        return;
      }
      const list = readPositionsList(settled.value);
      if (list === null) {
        setPositionsError(new Error("The response carried no positions list."));
        return;
      }
      setPositions(list.map(toPositionRow));
      setPositionsCount(reportedCount);
    };

    if (environment === "live") {
      // ── task 13.1: where the LIVE positions read points ────────────────────────────────
      // It used to be `api.portfolio.getOpenPositions().catch(() => api.portfolio.getPositions())`.
      // `backend_app/routers/portfolio.py` registers six routes — `/summary`, `/equity-curve`,
      // `/allocation`, `/heatmap`, `/recent-transactions`, `/close-all` — and neither positions
      // path is among them, so that chain 404d, then 404d again, and this table was empty for
      // every trader on every load however many positions they held (design.md §1.4).
      //
      // ── task 16.1: `GET /api/portfolio/summary` is no longer read here ────────────────
      // `available_balance` is not on that route and neither is the drawdown, so tier 1 cannot
      // be assembled from it; reading `total_equity` from `/summary` and the other seven from
      // the dashboard would put two readings of one account in one row, taken at two instants.
      // One read, one row (§7.6). The route and its client method are untouched — this page
      // simply has nothing left to ask it.
      const [dashRes, allocRes, equityRes, heatmapRes] = await Promise.allSettled([
        api.dashboard.getDashboard({ environment: "live" }),
        api.portfolio.getAllocation(),
        api.portfolio.getEquityCurve(window.days),
        api.portfolio.getHeatmap(window.months),
      ]);

      // TIER 1 and the positions ledger are built from THE SAME settled read. The dashboard
      // body carries `overview` and `risk` for the row and `positions` / `degraded` /
      // `risk.open_positions_count` for the region below it, so the split is at
      // interpretation and not at the network: one request, two view models, and no way for
      // the row and the table to disagree about which instant they describe.
      applyTierOne(dashRes);
      applyPositions(
        dashRes,
        dashRes.status === "fulfilled" ? readPositionsDegradation(dashRes.value) : null,
        dashRes.status === "fulfilled" ? readOpenPositionsCount(dashRes.value) : null,
      );

      // ── Allocation ──────────────────────────────────────────────────────────────────
      // `GET /api/portfolio/allocation` answers a BARE ARRAY of `{asset, value_usd, pct}` and
      // raises nothing on a read that produced no dataset, so `[]` cannot be read as "no
      // allocation" with confidence — the empty state says what it can and claims no more.
      if (allocRes.status === "fulfilled" && Array.isArray(allocRes.value)) {
        setAllocation(allocRes.value.map((row) => ({
          asset: firstText(row?.asset),
          pct: firstNumber(row?.pct, row?.percentage),
          value_usd: firstNumber(row?.value_usd, row?.value),
        })));
      } else {
        setAllocationError(allocRes.status === "rejected"
          ? allocRes.reason
          : new Error("The allocation read answered no list."));
      }

      // ── Equity curve ────────────────────────────────────────────────────────────────
      // Rows of `{timestamp, equity}` in ascending time order, kept AS READ. The timestamp is
      // not pre-formatted to "Mar 11" here: `ds/Chart`'s `format: 'date'` renders it in UTC to
      // the day, and a locale-formatted string on the axis is a string the tooltip cannot
      // reinterpret. This route raises 503 on a read error, so `[]` from it means no history.
      if (equityRes.status === "fulfilled" && Array.isArray(equityRes.value)) {
        setEquityCurve(equityRes.value
          .map((row) => ({
            timestamp: firstText(row?.timestamp, row?.date),
            equity: firstNumber(row?.equity, row?.value),
          }))
          .filter((row) => row.timestamp !== null && row.equity !== null));
      } else {
        setEquityError(equityRes.status === "rejected"
          ? equityRes.reason
          : new Error("The equity-curve read answered no list."));
      }

      // ── Daily P&L ───────────────────────────────────────────────────────────────────
      // Rows of `{date, pnl_usd}`, one per day with a recorded execution. A day with no
      // executions has no row and is NOT a zero-P&L day, so a row whose figure is unreadable
      // is dropped from the series rather than plotted at zero.
      if (heatmapRes.status === "fulfilled" && Array.isArray(heatmapRes.value)) {
        setDailyPnl(heatmapRes.value
          .map((row) => ({
            date: firstText(row?.date),
            pnl: firstNumber(row?.pnl_usd, row?.pnl),
          }))
          .filter((row) => row.date !== null && row.pnl !== null));
      } else {
        setDailyPnlError(heatmapRes.status === "rejected"
          ? heatmapRes.reason
          : new Error("The daily P&L read answered no list."));
      }
    } else {
      // ── PAPER ───────────────────────────────────────────────────────────────────────
      // Two reads. Tier 1 takes `GET /api/dashboard?environment=paper`, because that is where
      // its eight declared paths are and because three of them are on no paper body at all:
      // `used_balance`, `total_exposure` and BC-5's lifetime `realized_pnl` are computed for
      // the paper account server-side and `/api/paper/summary` carries none of the three.
      //
      // `api.paper.getSummary()` is NOT read any more. It served two things: the ledger's
      // provenance label — which now comes off the positions envelope, the body that actually
      // carries the positions being labelled — and the equity figure behind the fabricated
      // 100%-USD allocation row, which is deleted. A request whose body nothing renders is a
      // request nobody should pay for.
      const [paperDashRes, paperPosRes] = await Promise.allSettled([
        api.dashboard.getDashboard({ environment: "paper" }),
        api.paper.getPositions(),
      ]);

      applyTierOne(paperDashRes);

      if (paperPosRes.status === "fulfilled") {
        setPositionsProvenance(readPaperProvenance(paperPosRes.value));
      }

      // `GET /api/paper/positions` answers the envelope `{positions, count,
      // execution_environment, is_simulated, session_id}` — never an array. The previous
      // `Array.isArray(value)` guard was therefore false for every successful read, and this
      // ledger was permanently empty however many positions the account held.
      const envelopeCount = firstNumber(paperPosRes.value?.count);
      const list = paperPosRes.status === "fulfilled"
        ? readPositionsList(paperPosRes.value)
        : null;
      applyPositions(
        paperPosRes,
        null,
        // The envelope's own `count` when it reported one, and otherwise the length of the
        // very list about to be rendered — not a stand-in for a figure nobody read.
        envelopeCount !== null ? envelopeCount : (list === null ? null : list.length),
      );

      // Tier 3 is a live-account capability. `unavailable`, with the reason, and no
      // synthesised row: see PAPER_HISTORY_UNAVAILABLE and the module docblock.
      setAllocation(null);
      setEquityCurve(null);
      setDailyPnl(null);
    }

    setIsLoading(false);
  }, [environment, period]);

  useEffect(() => {
    loadPortfolioData();
  }, [loadPortfolioData]);

  const handleLedgerChange = useCallback((next) => setEnvironment(next), []);
  const handlePeriodChange = useCallback((next) => setPeriod(next), []);

  /*
   * Tier 1's panel state — three outcomes and no fourth.
   *
   * `ready` requires a built model. A read that did not complete, or one that resolved to
   * something with no figures in it, is `error`: §7.4 states the rule for a tier-1 row
   * directly — the figures are not rendered AT ALL rather than rendered as zeros — and the
   * same argument rules out eight not-available markers, which would report a transport
   * failure as eight facts about the account.
   */
  const tierOneState = isLoading
    ? PANEL_STATES.LOADING
    : (tierOne === null ? PANEL_STATES.ERROR : PANEL_STATES.READY);

  /*
   * The positions region's state. `error` outranks `empty`, which is the whole of
   * Requirement 14.5 for this region: an unreadable read must never present as an account
   * that holds nothing, and `ds/Panel` renders no children in either state, so no `<table>`
   * exists in the DOM for a failure.
   */
  const positionsState = isLoading
    ? PANEL_STATES.LOADING
    : (positionsError !== null || positionsDegraded !== null
      ? PANEL_STATES.ERROR
      : (positions === null
        ? PANEL_STATES.ERROR
        : (positions.length === 0 ? PANEL_STATES.EMPTY : PANEL_STATES.READY)));

  // `NO_ROWS` rather than a fresh `[]`: a new array literal on every render would give every
  // `useMemo` below a changing dependency, which would defeat the memo boundary the §13.2
  // guarantee rests on. One frozen empty array, module-scope, so "nothing yet" has a stable
  // identity.
  const positionRows = useMemo(() => positions ?? NO_ROWS, [positions]);
  const columns = useMemo(() => positionColumns(currency), [currency]);
  const summary = useMemo(
    () => buildPositionsSummary(positionRows, positionsCount),
    [positionRows, positionsCount],
  );

  /*
   * The chart series, memoised so their identity — and therefore the memo boundary around
   * each chart — changes only when the REST read that produced them completes (§13.2).
   *
   * A row the read did not report a share for is dropped from the CHART and kept in the
   * TABLE, where it renders the marker. A bar cannot represent an unknown height, and
   * plotting it at zero would state a share nobody reported.
   */
  const allocationRows = useMemo(() => allocation ?? NO_ROWS, [allocation]);
  const allocationSeries = useMemo(
    () => allocationRows.filter((row) => row.asset !== null && row.pct !== null),
    [allocationRows],
  );
  const equityRows = useMemo(() => equityCurve ?? NO_ROWS, [equityCurve]);
  const dailyPnlRows = useMemo(() => dailyPnl ?? NO_ROWS, [dailyPnl]);

  /** Tier 3's state, per panel. `unavailable` on paper; otherwise error / empty / ready. */
  const historyState = (rows, error) => {
    if (isPaper) return PANEL_STATES.UNAVAILABLE;
    if (isLoading) return PANEL_STATES.LOADING;
    if (error !== null) return PANEL_STATES.ERROR;
    if (rows === null) return PANEL_STATES.ERROR;
    return rows.length === 0 ? PANEL_STATES.EMPTY : PANEL_STATES.READY;
  };

  const allocationState = historyState(
    allocation === null ? null : allocationSeries,
    allocationError,
  );
  const equityState = historyState(equityCurve, equityError);
  const dailyPnlState = historyState(dailyPnl, dailyPnlError);

  const panelEnvironment = isPaper ? "PAPER" : "LIVE";
  const unavailableReason = { reason: PAPER_HISTORY_UNAVAILABLE };
  const retry = { context: "portfolio", onRetry: loadPortfolioData };

  return (
    <div className="flex min-w-0 flex-col gap-4 overflow-y-auto bg-surface-canvas p-5 text-content-primary">
      <PageHeader
        title="Portfolio"
        subtitle="Capital allocation, exposure and historical performance"
        meta={(
          <ChipRadioGroup
            legend="Ledger"
            name="ledger"
            options={LEDGERS}
            value={environment}
            onChange={handleLedgerChange}
          />
        )}
        actions={(
          <CommandButton
            intent="secondary"
            icon={RefreshCw}
            loading={isLoading}
            loadingLabel="Refreshing"
            onClick={loadPortfolioData}
          >
            Refresh
          </CommandButton>
        )}
      />

      {/* ═══ TIER 1 — Requirements 10.1 and 10.2 (task 16.1) ═══════════════════════════
          ONE container, eight figures, drawdown among them.

          Requirement 10.1 asks for six figures "as the highest-visual-priority elements" and
          10.2 asks for the drawdown "alongside" them "rather than in a separate,
          lower-priority section". Both are ordering claims, so the row is rendered by walking
          `design/pageHierarchy.js`'s tier-1 list: a figure cannot leave this container without
          leaving the declaration, and `data-page-tier="1"` marks the single container Property
          4 (task 16.3) asserts every tier-1 figure is inside.

          Eight, not seven, because "Realised P&L" is two quantities — today's window and
          BC-5's lifetime sum — and `pageFields.js` labels them apart rather than letting one
          set of words cover both. */}
      <Panel
        title="Portfolio summary"
        money
        environment={panelEnvironment}
        state={tierOneState}
        loading={{ kind: "skeleton-metric", rows: 2, columns: 4 }}
        error={{ error: tierOneError, ...retry }}
        data-region="tier-1"
      >
        <div
          {...{ [TIER_PAGE_ATTRIBUTE]: PAGES.PORTFOLIO, [TIER_ATTRIBUTE]: 1 }}
          className="grid grid-cols-4 gap-4"
        >
          {TIER_ONE.map(({ key, label }) => {
            const entry = fieldEntry(key);
            const { format, precision } = TIER_ONE_FORMAT[key];
            return (
              <Metric
                key={key}
                tier={1}
                label={label}
                value={tierOne?.[key]}
                format={format}
                precision={precision}
                // The denomination as the server reported it, and only beside a real figure.
                unit={format === "currency" ? currency ?? undefined : undefined}
                // `investedCapital`'s tooltip states the derivation, which is part of its
                // declaration: `used_balance` labelled "invested capital" with nothing said
                // would misrepresent it, because margin locked against a losing position is
                // in use without being invested (§7.6).
                hint={entry.tooltip ?? undefined}
              />
            );
          })}
        </div>
      </Panel>

      {/* ═══ TIER 2 — Requirements 10.3, 10.4, 14.5 (task 16.2) ════════════════════════
          The summary row and the detail table, in that order, in one container.

          Requirement 10.3 asks for "a summary above a detailed per-position table", which is
          a document-order claim: the two are siblings inside the same panel body, summary
          first, so the order cannot change without moving one of them inside this block.

          A BC-2 `degraded` read renders the server's own account of it ABOVE the panel and
          puts the panel in `error`, so the table does not exist in the DOM at all. */}
      <div {...{ [TIER_PAGE_ATTRIBUTE]: PAGES.PORTFOLIO, [TIER_ATTRIBUTE]: 2 }} className="flex min-w-0 flex-col gap-3">
        {positionsDegraded === null ? null : (
          <Alert
            severity="warning"
            title="The server could not read your open positions"
            action={{ label: "Try again", onClick: loadPortfolioData }}
            data-region="positions-degraded"
          >
            {/* The server's own prose, verbatim. Not paraphrased, and not composed here: the
                server knows which environment failed and why, and `translateError` carries no
                free-form message by design (Requirement 14.4), so this is the only place that
                account can reach the screen. */}
            {positionsDegraded}
          </Alert>
        )}

        <Panel
          title="Open positions"
          money
          // The server's label for the positions on screen, from the body that carried them.
          // `null` on a paper envelope that reported neither field, which renders
          // "unconfirmed" rather than a guessed environment.
          environment={isPaper ? (positionsProvenance?.environment ?? null) : "LIVE"}
          state={positionsState}
          loading={{ kind: "skeleton-table", rows: 5, columns: 10 }}
          empty={POSITIONS_EMPTY}
          error={{ error: positionsError, ...retry }}
          actions={isPaper ? (
            <TradingEnvironmentBadge
              environment={positionsProvenance?.environment ?? null}
              isSimulated={positionsProvenance?.isSimulated === true}
              variant="chip"
            />
          ) : null}
          data-region="positions"
        >
          {/* ── SUMMARY (Requirement 10.3) — above the table, always ────────────────── */}
          {/* `grid-cols-4` with no responsive variant, matching tier 1's row above: this page's
              responsive reduction is `ds/DataTable`'s `priority` mechanism (Requirement 17.2),
              which is pure CSS and already built, and `shell/`'s gate handles the widths below
              which the app does not render a trading page at all. A `laptop:` variant here would
              also be a class the `dead-tailwind` guard cannot verify until the next build. */}
          <div className="mb-4 grid grid-cols-4 gap-4" data-region="positions-summary">
            <Metric
              tier={2}
              label="Positions"
              value={summary.count}
              format="integer"
              hint="The count the server reported for the positions listed below."
            />
            <Metric
              tier={2}
              label="Long / short"
              value={summary.split}
              format="raw"
              hint={SUMMARY_DERIVATION}
            />
            <Metric
              tier={2}
              label="Net exposure"
              value={summary.netExposure}
              format="currency"
              precision={2}
              unit={currency ?? undefined}
              hint={`${SUMMARY_DERIVATION} Long notionals less short notionals, and stated only `
                + "when every listed position reports a notional value."}
            />
            <Metric
              tier={2}
              label="Largest position"
              value={summary.largest}
              format="raw"
              hint={`${SUMMARY_DERIVATION} The market with the greatest notional value, long or short.`}
            />
          </div>

          {/* ── DETAIL — §7.6's ten columns ─────────────────────────────────────────── */}
          <DataTable
            columns={columns}
            rows={positionRows}
            getRowId={(row) => row.id}
            caption={currency
              ? `Open positions, ${panelEnvironment} account, in ${currency}`
              : `Open positions, ${panelEnvironment} account`}
            stickyHeader
            density="compact"
            // One batch, so the whole set is here and pagination is client-side.
            totalCount={positionRows.length}
          />
        </Panel>
      </div>

      {/* ═══ TIER 3 — allocation, equity curve, daily P&L ══════════════════════════════
          Three `ds/Chart`s, each behind its own `memo` boundary, each refreshed only by
          `loadPortfolioData` — which runs on mount, on Refresh, on a ledger change and on a
          period change, and by nothing else. There is no subscription on this page. */}
      <SectionHeader
        title="History"
        subtitle="Allocation, account equity and daily realised P&L"
        right={(
          <ChipRadioGroup
            legend="Period"
            name="period"
            options={PERIODS}
            value={period}
            onChange={handlePeriodChange}
          />
        )}
      />

      <div
        {...{ [TIER_PAGE_ATTRIBUTE]: PAGES.PORTFOLIO, [TIER_ATTRIBUTE]: 3 }}
        className="grid min-w-0 grid-cols-2 gap-4"
      >
        <Panel
          title={labelOf("allocation")}
          state={allocationState}
          loading={{ kind: "skeleton-chart" }}
          empty={ALLOCATION_EMPTY}
          error={{ error: allocationError, ...retry }}
          unavailable={unavailableReason}
          data-region="allocation"
        >
          <AllocationChart rows={allocationSeries} />
          {/* The chart's tabular equivalent, and the only place `value_usd` can be read: it
              is a different unit from a percentage, so it cannot share the chart's y axis.
              This is also what replaced the `<ul role="list">` legend the a11y ratchet
              waived two `no-redundant-roles` findings for. */}
          <DataTable
            className="mt-3"
            columns={ALLOCATION_COLUMNS}
            rows={allocationRows}
            getRowId={(row) => row.asset ?? "unnamed"}
            caption="Portfolio allocation by asset"
            density="compact"
            totalCount={allocationRows.length}
          />
        </Panel>

        <Panel
          title={labelOf("equityCurve")}
          money
          environment={panelEnvironment}
          state={equityState}
          loading={{ kind: "skeleton-chart" }}
          empty={EQUITY_EMPTY}
          error={{ error: equityError, ...retry }}
          unavailable={unavailableReason}
          data-region="equity-curve"
        >
          <EquityCurveChart rows={equityRows} currency={currency} />
        </Panel>

        <Panel
          title={labelOf("heatmap")}
          money
          environment={panelEnvironment}
          state={dailyPnlState}
          loading={{ kind: "skeleton-chart" }}
          empty={HEATMAP_EMPTY}
          error={{ error: dailyPnlError, ...retry }}
          unavailable={unavailableReason}
          className="col-span-2"
          data-region="daily-pnl"
        >
          <DailyPnlChart rows={dailyPnlRows} currency={currency} />
        </Panel>
      </div>
    </div>
  );
}
