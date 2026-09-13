import React, { useState, useEffect, useCallback } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell
} from "recharts";
import { TrendingUp, Target, Activity, RefreshCw, FlaskConical, AlertTriangle } from "lucide-react";
import { C, SectionH, PanelTitle, CustomTooltip } from "../components/ui-legacy/primitives";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { api } from "../api";
import { Metric } from "../components/ds/Metric";
import { Panel } from "../components/ds/Panel";
import { PAGES, PAGE_FIELDS_BY_PAGE, VERDICT } from "../design/pageFields";
import {
  PAGE_HIERARCHY_BY_PAGE,
  TIER_ATTRIBUTE,
  TIER_PAGE_ATTRIBUTE,
} from "../design/pageHierarchy";
import { fromNullable, unavailable } from "../design/reported";
import { PANEL_STATES } from "../hooks/usePanelState";

/* ══════════════════════════════════════════════════════════════════════════
 * TIER 1 — the declaration, and the one read behind it (task 16.1)
 * ══════════════════════════════════════════════════════════════════════════
 *
 * `design/pageHierarchy.js` says WHICH figures are tier 1 and in what order;
 * `design/pageFields.js` says where each one's value comes from, what its label is, and
 * what sentence a trader reads when the server reported nothing. Neither is restated here:
 * the row below is rendered by walking the declaration, so a figure cannot be dropped from
 * the row without being dropped from the declaration, and a figure cannot be added to it
 * without a declared source path.
 *
 * That is the whole of what Requirement 10.2 needed. `currentDrawdown` is a tier-1 entry,
 * so it renders in the tier-1 container beside the exposure it qualifies, rather than in a
 * lower risk section where a trader deciding whether to cut size has to scroll to find it.
 */

const PORTFOLIO_FIELDS = PAGE_FIELDS_BY_PAGE[PAGES.PORTFOLIO] ?? [];

/** §7.6's tier 1, in declaration order. */
const TIER_ONE = (PAGE_HIERARCHY_BY_PAGE[PAGES.PORTFOLIO]?.tiers ?? [])
  .filter((entry) => entry.tier === 1);

/** One field's declaration. */
const fieldEntry = (field) => PORTFOLIO_FIELDS.find((entry) => entry.field === field) ?? null;

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
 * them as `ds/Metric`'s `unit`. It replaces the hardcoded `$` these cards used to carry,
 * which was a claim the response contradicts: the live account reports `USDT` and the paper
 * account `USD`. `ds/Metric` renders a unit only beside a real figure, so an absent
 * currency costs nothing and no marker is labelled with a denomination.
 *
 * @param {unknown} body
 * @returns {string|null}
 */
const readCurrency = (body) => {
  const value = readPath(body, "overview.currency");
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
};

/**
 * The two additive provenance fields, read off a paper response body and nothing else.
 *
 * `backend_app/routers/paper_trading.py` returns `execution_environment` and `is_simulated`
 * on every paper body (task 23.3): on the `/api/paper/summary` body itself, through
 * `PaperTradingService._paper_provenance`, and on the `/api/paper/positions` envelope beside
 * `positions` and `count`. Nothing here defaults or invents either field - a body that
 * carries neither yields `null`, and the indicator says so, rather than a label being
 * manufactured on the client (Requirement 28.5).
 *
 * @param {any} body - A resolved paper response body.
 * @returns {{execution_environment: (string|null), is_simulated: boolean}|null}
 */
const readPaperProvenance = (body) => {
  if (!body || typeof body !== "object") return null;
  const env = typeof body.execution_environment === "string" && body.execution_environment
    ? body.execution_environment
    : null;
  const flagged = body.is_simulated === true;
  if (!env && !flagged) return null;
  return { execution_environment: env, is_simulated: flagged };
};

/**
 * Requirement 28.5 - the two sentences this page shows in place of a figure it does not have.
 *
 * `PaperTrading.jsx` established this vocabulary for the paper session page ("Not reported" /
 * "Not computed" for an absent figure, and the error panel for a read that did not complete),
 * and the two pages are kept in the same words so a reader moving between them does not have to
 * learn a second dialect. `StrategyMarketplace.jsx` spells the same absence as an em dash in a
 * table cell; here the figures are headline cards, so the words are written out.
 *
 * The distinction that matters: NOT_REPORTED means the response arrived and carried no such
 * figure; a request that did not complete renders a failure notice for its region instead.
 * Neither is a zero, and a genuine zero renders as `$0.00`.
 *
 * TIER 1 NO LONGER USES THIS VOCABULARY. Task 16.1 rebuilt that row on `ds/Metric`, whose
 * not-available marker carries the reason `pageFields.js` declares for the field — a sentence
 * about that figure rather than one of two page-wide phrases. What remains here belongs to the
 * regions task 16.2 rebuilds: the positions ledger heading and the allocation legend.
 */
const NOT_REPORTED = "Not reported";

/**
 * The first candidate that is a finite number, or `null`.
 *
 * Replaces the `?? 0` / `?? 100000` chains this page used to end its field reads with. Those
 * chains turned "the server sent no such field" into a displayed balance, which is exactly what
 * Requirement 28.5 forbids; `null` here travels to the card and is rendered as
 * {@link NOT_REPORTED} instead.
 *
 * @param {...unknown} candidates - Field readings, in precedence order.
 * @returns {number|null}
 */
const readNumber = (...candidates) => {
  for (const candidate of candidates) {
    if (candidate === null || candidate === undefined || candidate === "") continue;
    if (typeof candidate === "boolean") continue;
    const parsed = typeof candidate === "number" ? candidate : Number(String(candidate).trim());
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
};

/**
 * The positions list off a response body, or `null` when the body carries no list at all.
 *
 * Two shapes reach this page and both are read here rather than one being assumed:
 *
 * * **An envelope.** `GET /api/paper/positions`
 *   (`backend_app/routers/paper_trading.py`) answers
 *   `{positions, count, execution_environment, is_simulated, session_id}`, and the shared client's
 *   `get` resolves to the response BODY - so `api.paper.getPositions()` resolves to that object,
 *   never to an array. The internal portfolio-management `GET /positions` answers
 *   `{count, positions}` in the same style.
 * * **A bare array.** The live portfolio reads (`allocation`, `equity-curve`, `heatmap`) all
 *   answer bare lists, so a positions read answering one is entirely plausible.
 *
 * `GET /api/dashboard` - which is where the LIVE positions read now points (task 13.1) - answers
 * the envelope form: `positions` sits beside `overview`, `risk`, `degraded` and the rest, so it is
 * read by the same `body.positions` branch the paper envelope uses.
 *
 * `null` for anything else is deliberate: a body that carries neither shape is a read this page
 * cannot interpret, and it is reported as such rather than as "you hold no positions" - an empty
 * ledger is a claim, and an unreadable body did not make it.
 *
 * @param {unknown} body - A resolved response body.
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
 * `dashboard_aggregation_service.py` publishes one top-level `degraded` key: `None` when every
 * read behind the response succeeded, and
 * `{positions: "unreadable", environment, reason}` when the positions read did not. `positions`
 * is `[]` in BOTH cases - the list itself does not lie, it is simply empty - so this marker is
 * the only thing that tells "the account holds nothing" apart from "nobody could find out", and
 * a client that renders an empty table without consulting it publishes an outage as a fact about
 * the account (design.md §1.6, Requirement 14.5).
 *
 * The returned string is the server's own prose `reason`, rendered verbatim. It is not
 * paraphrased here: the server knows which environment failed and why, and a sentence composed
 * on the client would be a second, drifting account of the same event. A marker that arrives
 * without a reason still yields a failure - the absence of the explanation is reported rather
 * than filled in.
 *
 * @param {unknown} body - A resolved `GET /api/dashboard` body.
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
 * BC-2's second channel, from the other end of the response: this field is an `int` when the
 * positions were counted and `null` when they could not be read - never `0` as a stand-in,
 * because a count of zero is the *safest-looking* reading a broken positions read could publish.
 * `null` travels to the ledger heading and is rendered as {@link NOT_REPORTED}.
 *
 * The count is taken from the server rather than from `positions.length` so the figure on screen
 * is the one the server computed. The two cannot disagree - BC-2 hands `get_risk_metrics` the
 * same gathered list `positions` comes from - but reading the reported field is what makes the
 * `null` case reachable at all.
 *
 * @param {unknown} body - A resolved `GET /api/dashboard` body.
 * @returns {number|null}
 */
const readOpenPositionsCount = (body) => {
  const risk = body && typeof body === "object" ? body.risk : null;
  const count = risk && typeof risk === "object" ? risk.open_positions_count : null;
  return typeof count === "number" && Number.isFinite(count) ? count : null;
};

/**
 * The positions region's failure sentence: this page's framing, plus the detail it was given.
 *
 * Used for a read that did not complete at all, where there is no server account of what
 * happened - only a transport error. A BC-2 `degraded` marker does NOT go through here: its
 * `reason` already says both of these things in the server's own words, and wrapping it would
 * state the same fact twice.
 *
 * @param {string} detail
 * @returns {string}
 */
const positionsFailureSentence = (detail) =>
  "Open positions could not be read, so none are listed. This is not a statement that the " +
  `account holds none. ${detail}`;

/**
 * A rejected `Promise.allSettled` entry's reason as one sentence.
 *
 * The shared client rejects with an `ApiError` carrying the server's own message, so that
 * message is shown verbatim rather than replaced with a generic line.
 *
 * @param {unknown} reason
 * @returns {string}
 */
const failureSentence = (reason) => {
  const message = typeof reason?.message === "string" ? reason.message.trim() : "";
  return message || "The request did not complete.";
};

/*
 * `signTone`, `SignIcon` and `CardFigure` were here, and task 16.1 deleted all three.
 *
 * Each was a hand-rolled piece of what `ds/Metric` now does for the tier-1 row: the tone
 * function coloured every figure whether or not it reported a state (Requirement 1.5's
 * budget, spent on a portfolio value); the icon drew a trend arrow beside figures that have
 * no trend; and `CardFigure`'s four-way branch was this page's private version of the one
 * availability decision `design/reported.readReported` makes for the whole app. The two
 * absence phrases it rendered — "Not reported" and "Unavailable — read failed" — said the
 * same two things about every figure on the page; the marker now carries the reason the
 * field's own declaration gives.
 */

/** A read that did not complete, said in a sentence beside a shape and a word. */
function ReadFailureNotice({ children, span = false }) {
  return (
    <div
      role="status"
      style={{
        ...(span ? { gridColumn: "1 / -1" } : {}),
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "8px 10px",
        borderRadius: "6px",
        background: "rgba(245, 158, 11, 0.10)",
        border: "1px solid rgba(245, 158, 11, 0.40)",
        color: "#fbbf24",
        fontSize: "0.6875rem",
        lineHeight: 1.5,
      }}
    >
      <AlertTriangle size={13} aria-hidden="true" style={{ flexShrink: 0 }} />
      <span>{children}</span>
    </div>
  );
}

/*
 * `money` and `signedAmount` went with the cards that used them.
 *
 * Both called `toLocaleString`, whose separators are locale-dependent — a terminal rendering
 * `1.234,5` in one panel and `1,234.5` in another manufactures a hazard out of a formatting
 * default. `ds/Metric` groups by hand for exactly that reason, and rounds only when a
 * `precision` says to.
 */

/**
 * Requirement 13.6 / 20.6 / 28.1 - the simulated indicator.
 *
 * It is rendered from the server's own `execution_environment` / `is_simulated` fields, it
 * carries its meaning in text plus a shape rather than in colour alone, and each instance sits
 * **inside the region holding the figures it qualifies** - not in the page header, which a
 * reader who has scrolled to the positions table cannot see.
 *
 * A `null` provenance in the PAPER branch means the response arrived without those fields. The
 * indicator then reports the label as unavailable instead of asserting a server label that was
 * never sent, and still marks the region, because an unlabelled PAPER figure is what
 * Requirement 28.4 forbids.
 */
function SimulatedIndicator({ provenance, announce = false }) {
  const env = provenance?.execution_environment ?? null;
  const flagged = provenance?.is_simulated === true;
  const known = Boolean(env) || flagged;
  const label = env && flagged
    ? `${env} · SIMULATED`
    : env || (flagged ? "SIMULATED" : "SIMULATED · SERVER LABEL UNAVAILABLE");
  const tone = known
    ? { fg: "#818cf8", bg: "rgba(99, 102, 241, 0.12)", border: "rgba(99, 102, 241, 0.45)" }
    : { fg: "#fbbf24", bg: "rgba(245, 158, 11, 0.12)", border: "rgba(245, 158, 11, 0.45)" };

  return (
    <span
      role={announce ? "status" : undefined}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        padding: "3px 8px",
        borderRadius: "6px",
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        color: tone.fg,
        fontFamily: "monospace",
        fontSize: "0.625rem",
        fontWeight: 800,
        letterSpacing: 1.2,
        textTransform: "uppercase",
        whiteSpace: "nowrap",
      }}
    >
      <FlaskConical size={11} aria-hidden="true" />
      {label}
    </span>
  );
}

export default function Portfolio() {
  const [environment, setEnvironment] = useState("live");
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
  const [positions, setPositions] = useState([]);
  const [equityCurve, setEquityCurve] = useState([]);
  const [allocation, setAllocation] = useState([]);
  const [heatmapData, setHeatmapData] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  // The complete sentence the positions region renders when it has no ledger to render. Composed
  // where the read is interpreted rather than at the render site, because only there is it known
  // whether the server supplied its own account of the failure (BC-2's `degraded.reason`, shown
  // verbatim) or whether the request simply did not complete (this page's framing plus the
  // transport error).
  const [positionsError, setPositionsError] = useState(null);
  // The server's `risk.open_positions_count`: a number when counted, `null` when the server could
  // not count it, and `null` for a read that did not complete. Never 0 as a stand-in for either
  // (BC-2, Requirement 19.2).
  const [positionsCount, setPositionsCount] = useState(null);
  // Per-region provenance: the summary body labels the equity/P&L/cash cards and the
  // allocation figure derived from them, the positions envelope labels the positions ledger.
  // They are kept apart so one region never borrows the other region's label.
  const [paperProvenance, setPaperProvenance] = useState({ summary: null, positions: null });
  const COLORS = [C.orange, C.purple, C.cyan, C.gold, C.t3];

  const loadPortfolioData = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    setTierOne(null);
    setTierOneError(null);
    setCurrency(null);
    setPositionsError(null);
    setPositionsCount(null);

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

    try {
      if (environment === "live") {
        // No paper provenance on the LIVE branch: the label belongs to paper figures, and this
        // branch holds none. The two branches are mutually exclusive, so a LIVE total is never
        // computed from a figure this reader labelled as simulated (Requirement 13.6).
        setPaperProvenance({ summary: null, positions: null });

        // 1. Fetch live portfolio analytics concurrently
        //
        // ── task 13.1: where the LIVE positions read points ──────────────────────────────
        // It used to be `api.portfolio.getOpenPositions().catch(() => api.portfolio.getPositions())`.
        // `backend_app/routers/portfolio.py` registers six routes - `/summary`, `/equity-curve`,
        // `/allocation`, `/heatmap`, `/recent-transactions`, `/close-all` - and neither positions
        // path is among them, so that chain 404d, then 404d again, and this table was empty for
        // every trader on every load however many positions they held (design.md §1.4). The only
        // other `GET /positions` in the tree belongs to `backend_app/backend/portfolio_management.py`,
        // mounted at `/api/internal/portfolio-mgmt` behind `Depends(get_admin_user)` - a different
        // prefix, and unreachable for a trader either way.
        //
        // `GET /api/dashboard` is the endpoint that actually serves positions to a trader: a real
        // normalised `positions[]` carrying `symbol side contracts entry_price mark_price notional
        // leverage unrealized_pnl liquidation_price margin exchange_id environment` (design.md
        // §7.1, §7.6). This is a re-point, not a workaround and not a new endpoint.
        //
        // The other four reads are unchanged. `/summary`, `/equity-curve`, `/allocation` and
        // `/heatmap` all exist and all answer, so re-pointing them would be churn.
        // ── task 16.1: `GET /api/portfolio/summary` is no longer read here ───────────────
        // Every tier-1 field is declared against the dashboard read, and the two figures the
        // requirements name that `/summary` cannot serve are why: `available_balance` is not on
        // that route (it answers `total_equity total_pnl pnl_pct total_exposure` and nothing
        // else) and neither is the drawdown. Reading `total_equity` from `/summary` and the
        // other seven from the dashboard would put two readings of one account in one row,
        // taken at two instants. One read, one row (§7.6). The route and its client method are
        // untouched — this page simply has nothing left to ask it.
        const [dashRes, equityRes, allocRes, heatmapRes] = await Promise.allSettled([
          api.dashboard.getDashboard({ environment: "live" }),
          api.portfolio.getEquityCurve(90),
          api.portfolio.getAllocation(),
          api.portfolio.getHeatmap(3),
        ]);

        // TIER 1 and the positions ledger are built from THE SAME settled read. The dashboard
        // body carries `overview` and `risk` for the row and `positions` / `degraded` /
        // `risk.open_positions_count` for the ledger below it, so the split is at
        // interpretation and not at the network: one request, two view models, and no way for
        // the row and the table to disagree about which instant they describe.
        applyTierOne(dashRes);

        // Process Open Positions
        //
        // Three outcomes, in the order they have to be tested:
        //
        //  1. the request did not complete       -> the transport error, framed by this page;
        //  2. it completed and `degraded` says the positions read failed -> the SERVER's reason.
        //     `positions` is `[]` on that response and rendering it as an empty ledger would
        //     publish an outage as a fact about the account, which is the whole reason BC-2
        //     exists (design.md §1.6, Requirement 14.5). The marker is therefore tested BEFORE
        //     the list, because the list looks perfectly healthy in this case;
        //  3. it completed and `degraded` is null -> `positions` is the truth, `[]` included.
        //
        // The count in the ledger heading comes from `risk.open_positions_count`, which is `null`
        // rather than 0 when unreadable - the same distinction from the other end of the response.
        const liveDegradedReason = dashRes.status === "fulfilled"
          ? readPositionsDegradation(dashRes.value)
          : null;
        const livePositions = dashRes.status === "fulfilled" && liveDegradedReason === null
          ? readPositionsList(dashRes.value)
          : null;
        if (livePositions) {
          setPositions(livePositions.map((p, i) => ({
            id: p.id || p.position_id || `pos_${i}`,
            symbol: p.symbol || "UNKNOWN",
            exchange: p.exchange_id || p.exchange || "binance",
            side: (p.side || "long").toLowerCase(),
            size: parseFloat(p.contracts || p.size || p.quantity || 0),
            entryPrice: parseFloat(p.entry_price || p.entryPrice || 0),
            markPrice: parseFloat(p.mark_price || p.currentPrice || p.entry_price || 0),
            unrealizedPnl: parseFloat(p.unrealized_pnl || p.unrealizedPnl || 0),
          })));
          setPositionsCount(readOpenPositionsCount(dashRes.value));
        } else {
          setPositions([]);
          setPositionsCount(null);
          if (liveDegradedReason !== null) {
            setPositionsError(liveDegradedReason);
          } else {
            setPositionsError(positionsFailureSentence(
              dashRes.status === "rejected"
                ? failureSentence(dashRes.reason)
                : "The response carried no positions list."
            ));
          }
        }

        // Process Equity Curve
        if (equityRes.status === "fulfilled" && Array.isArray(equityRes.value)) {
          const mappedCurve = equityRes.value.map(row => ({
            date: row.timestamp
              ? new Date(row.timestamp).toLocaleDateString("en-US", { month: "short", day: "numeric" })
              : (row.date || ""),
            value: parseFloat(row.equity ?? row.value ?? 0),
          }));
          setEquityCurve(mappedCurve);
        } else {
          setEquityCurve([]);
        }

        // Process Asset Allocation
        if (allocRes.status === "fulfilled" && Array.isArray(allocRes.value)) {
          // An allocation row whose share or value the response did not carry keeps `null` here
          // and says so in the legend (Requirement 28.5); a 0.0% share is now the server's own.
          const mappedAlloc = allocRes.value.map(a => ({
            asset: a.asset || "USDT",
            percentage: readNumber(a.pct, a.percentage),
            value_usd: readNumber(a.value_usd, a.value),
          }));
          setAllocation(mappedAlloc);
        } else {
          setAllocation([]);
        }

        // Process Heatmap Data
        if (heatmapRes.status === "fulfilled" && Array.isArray(heatmapRes.value)) {
          const mappedHeatmap = heatmapRes.value.map(h => ({
            date: h.date,
            pnl: h.pnl_usd !== undefined ? parseFloat(h.pnl_usd) : (h.pnl !== undefined ? parseFloat(h.pnl) : null),
          }));
          setHeatmapData(mappedHeatmap);
        } else {
          setHeatmapData([]);
        }
      } else {
        // 2. Fetch Paper Trading portfolio analytics
        //
        // ── task 16.1: how tier 1's read splits from the rest on this branch ─────────────
        // Tier 1 reads `GET /api/dashboard?environment=paper`, because that is where its eight
        // declared paths are and because three of them are on no paper body at all:
        // `get_portfolio_overview`'s paper branch computes `used_balance`, `total_exposure` and
        // BC-5's lifetime `realized_pnl` for the paper account, and `/api/paper/summary` answers
        // `{account, total_pnl, roi_pct, …counts}` with none of the three. Pointing the row at
        // the paper summary would mean naming paths §7.6 never audited for figures the response
        // does not carry.
        //
        // The other two reads stay exactly as they were, and they serve the regions task 16.2
        // owns: `getPositions()` the ledger, and `getSummary()` both the ledger's provenance
        // label and the allocation row's equity figure. So this branch makes three reads, each
        // feeding a different region, and no region borrows another's body.
        const [paperDashRes, paperSumRes, paperPosRes] = await Promise.allSettled([
          api.dashboard.getDashboard({ environment: "paper" }),
          api.paper.getSummary(),
          api.paper.getPositions(),
        ]);

        applyTierOne(paperDashRes);

        setPaperProvenance({
          summary: paperSumRes.status === "fulfilled" ? readPaperProvenance(paperSumRes.value) : null,
          positions: paperPosRes.status === "fulfilled" ? readPaperProvenance(paperPosRes.value) : null,
        });

        // Requirement 28.5: no `?? 100000`. The 100000 was the default opening capital of a paper
        // account, not a balance anybody read, and rendering it as an equity figure stated a
        // simulated balance the account may never have held. An unread figure stays `null` and
        // the allocation legend says "Not reported" for it.
        let paperTotalEquity = null;
        if (paperSumRes.status === "fulfilled" && paperSumRes.value) {
          const pSum = paperSumRes.value;
          paperTotalEquity = readNumber(pSum.total_equity, pSum.balance);
        }

        // `api.paper.getPositions()` resolves to the response BODY, and `GET /api/paper/positions`
        // answers the envelope `{positions, count, execution_environment, is_simulated,
        // session_id}` - never an array. The previous `Array.isArray(paperPosRes.value)` guard was
        // therefore false for every successful read, and this ledger was permanently empty however
        // many positions the account held. The list is read off the envelope.
        const paperPositions = paperPosRes.status === "fulfilled"
          ? readPositionsList(paperPosRes.value)
          : null;
        if (paperPositions) {
          setPositions(paperPositions.map((p, i) => ({
            id: p.id || `paper_pos_${i}`,
            symbol: p.symbol || "UNKNOWN",
            exchange: "paper",
            side: (p.side || "long").toLowerCase(),
            size: parseFloat(p.quantity || p.size || 0),
            entryPrice: parseFloat(p.entry_price || 0),
            markPrice: parseFloat(p.current_price || p.entry_price || 0),
            unrealizedPnl: parseFloat(p.unrealized_pnl || 0),
          })));
          // `GET /api/paper/positions` reports `count` beside `positions`. The list this branch
          // just read is the count when the envelope does not carry one - not a stand-in for a
          // figure nobody read, but the length of the very list about to be rendered.
          setPositionsCount(
            typeof paperPosRes.value?.count === "number" && Number.isFinite(paperPosRes.value.count)
              ? paperPosRes.value.count
              : paperPositions.length
          );
        } else {
          setPositions([]);
          setPositionsCount(null);
          setPositionsError(positionsFailureSentence(
            paperPosRes.status === "rejected"
              ? failureSentence(paperPosRes.reason)
              : "The response carried no positions list."
          ));
        }

        setEquityCurve([]);
        // The row's figure is `paperTotalEquity` - the equity THIS branch just read - or `null`.
        // It used to read a `summary` state that this branch had not yet written, so on a
        // LIVE -> PAPER flip it was the LIVE equity: a live figure sitting in a PAPER structure,
        // which is what Requirement 13.6 forbids combining. Its fallback was the same fabricated
        // 100000. Neither can occur now: the figure is the paper one or absent. (That state is
        // gone entirely as of task 16.1 — tier 1 holds `Reported<T>`s built from one read.)
        setAllocation([
          { asset: "USD (Simulated)", percentage: 100, value_usd: paperTotalEquity }
        ]);
        setHeatmapData([]);
      }

    } catch (err) {
      console.error("Portfolio data load error:", err);
      setLoadError("Failed to fetch live portfolio metrics.");
    } finally {
      setIsLoading(false);
    }
  }, [environment]);

  useEffect(() => {
    loadPortfolioData();
  }, [loadPortfolioData]);

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

  return (
    <div style={{ padding: "20px", overflowY: "auto", flex: 1, background: "#080a0e", color: "#e2e8f0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", flexWrap: "wrap", gap: "10px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <h1 style={{ fontSize: "1.25rem", fontWeight: 800, color: "#f8fafc", margin: 0, letterSpacing: "-0.02em" }}>
              Portfolio Analytics
            </h1>

            {/* LIVE / PAPER Environment Toggle */}
            <div style={{
              display: "inline-flex",
              alignItems: "center",
              background: "#0f141c",
              padding: "2px",
              borderRadius: "8px",
              border: "1px solid #1e293b"
            }}>
              <button
                onClick={() => setEnvironment("live")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.375rem",
                  padding: "4px 10px",
                  borderRadius: "6px",
                  fontSize: "0.6875rem",
                  fontWeight: 700,
                  border: "none",
                  cursor: "pointer",
                  background: environment === "live" ? "rgba(16, 185, 129, 0.2)" : "transparent",
                  color: environment === "live" ? "#10b981" : "#64748b",
                  boxShadow: environment === "live" ? "inset 0 0 0 1px #10b981" : "none"
                }}
              >
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: environment === "live" ? "#10b981" : "#475569" }} />
                LIVE
              </button>

              <button
                onClick={() => setEnvironment("paper")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.375rem",
                  padding: "4px 10px",
                  borderRadius: "6px",
                  fontSize: "0.6875rem",
                  fontWeight: 700,
                  border: "none",
                  cursor: "pointer",
                  background: environment === "paper" ? "rgba(99, 102, 241, 0.2)" : "transparent",
                  color: environment === "paper" ? "#818cf8" : "#64748b",
                  boxShadow: environment === "paper" ? "inset 0 0 0 1px #6366f1" : "none"
                }}
              >
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: environment === "paper" ? "#818cf8" : "#475569" }} />
                PAPER
              </button>
            </div>
          </div>
          <p style={{ fontSize: "0.75rem", color: "#64748b", margin: "4px 0 0" }}>
            Institutional capital allocation, asset distribution, and historical performance ledger
          </p>
        </div>

        <Button
          variant="outline"
          size="xs"
          onClick={loadPortfolioData}
          disabled={isLoading}
          className="flex items-center gap-1.5"
        >
          <RefreshCw size={12} className={isLoading ? "animate-spin" : ""} />
          <span>Refresh</span>
        </Button>
      </div>

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
          set of words cover both.

          `ds/Panel` with `money` carries the environment badge, which is Requirement 12.2 for
          this row and replaces the hand-rolled indicator the old grid spanned itself with. */}
      <Panel
        title="Portfolio summary"
        money
        environment={environment === "paper" ? "PAPER" : "LIVE"}
        state={tierOneState}
        loading={{ kind: "skeleton-metric", rows: 2, columns: 4 }}
        error={{ error: tierOneError, context: "portfolio", onRetry: loadPortfolioData }}
        className="mb-4"
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

      {/* Open Positions Deep-Dive Table */}
      <Card className="p-4 mb-4 bg-[#0c1017] border-[#1e293b]">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
          <div>
            <h2 style={{ fontSize: "0.875rem", fontWeight: 800, color: "#f8fafc", margin: 0, textTransform: "uppercase", letterSpacing: "0.03em" }}>
              {/* A count of 0 for a read that did not complete is a figure nobody measured, so the
                  heading says the list was not read instead (Requirement 28.5).

                  `positionsCount` is the server's `risk.open_positions_count` on the LIVE branch,
                  which BC-2 publishes as `null` - never 0 - when the positions could not be
                  counted. A response that carried a ledger but no count for it therefore reads
                  "Not reported" here rather than claiming a total this page derived itself. */}
              Open Positions Ledger ({positionsError
                ? "not read"
                : positionsCount === null ? NOT_REPORTED : positionsCount})
            </h2>
            <span style={{ fontSize: "0.6875rem", color: "#64748b" }}>
              {environment === "paper" ? "Simulated mark-to-market valuations" : "Live mark-to-market valuations"}
            </span>
          </div>
          {/* Requirement 13.6: inside the positions card, beside its own heading, because a
              reader scrolled down to this table cannot see the page header. */}
          {environment === "paper" && <SimulatedIndicator provenance={paperProvenance.positions} />}
        </div>

        {/* Three states, and the first two are different facts. "No open positions" is a claim
            about the account; a read that did not complete made no such claim, so it gets its own
            wording and its own colour and shape rather than borrowing the empty state's. */}
        {positionsError ? (
          <div style={{ padding: "0.75rem 0" }}>
            {/* One complete sentence, composed where the read was interpreted. For a BC-2
                `degraded` marker it is the server's own `reason`, verbatim; for a request that
                did not complete it is this page's framing plus the transport error. Either way
                the ledger below is not rendered, so an unreadable read can never appear as an
                empty table (Requirement 14.5). */}
            <ReadFailureNotice>{positionsError}</ReadFailureNotice>
          </div>
        ) : positions.length === 0 ? (
          <div style={{ padding: "1.5rem", textAlign: "center", color: "#64748b", fontSize: "0.75rem" }}>
            No open positions currently held in {environment.toUpperCase()} mode.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", minWidth: 650, borderCollapse: "collapse", fontSize: "0.75rem", fontFamily: "monospace" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #1e293b", color: "#64748b", textAlign: "left" }}>
                  <th style={{ padding: "8px 10px", fontWeight: 600 }}>Symbol</th>
                  <th style={{ padding: "8px 10px", fontWeight: 600 }}>Venue</th>
                  <th style={{ padding: "8px 10px", fontWeight: 600 }}>Side</th>
                  <th style={{ padding: "8px 10px", fontWeight: 600, textAlign: "right" }}>Contracts</th>
                  <th style={{ padding: "8px 10px", fontWeight: 600, textAlign: "right" }}>Entry Price</th>
                  <th style={{ padding: "8px 10px", fontWeight: 600, textAlign: "right" }}>Mark Price</th>
                  <th style={{ padding: "8px 10px", fontWeight: 600, textAlign: "right" }}>Unrealized P&L</th>
                </tr>
              </thead>
              <tbody>
                {positions.map(pos => (
                  <tr key={pos.id} style={{ borderBottom: "1px solid rgba(30,41,59,0.5)" }}>
                    <td style={{ padding: "8px 10px", color: "#f8fafc", fontWeight: 700 }}>{pos.symbol}</td>
                    <td style={{ padding: "8px 10px" }}>
                      <span style={{ fontSize: "0.625rem", padding: "2px 6px", borderRadius: 4, background: "#0f172a", border: "1px solid #334155", color: "#cbd5e1", textTransform: "uppercase", fontWeight: 700 }}>
                        {pos.exchange}
                      </span>
                    </td>
                    <td style={{ padding: "8px 10px" }}>
                      <span style={{ fontSize: "0.625rem", padding: "2px 6px", borderRadius: 4, background: pos.side === "long" ? "rgba(16,185,129,0.15)" : "rgba(239,68,68,0.15)", color: pos.side === "long" ? "#10b981" : "#ef4444", fontWeight: 700, textTransform: "uppercase" }}>
                        {pos.side}
                      </span>
                    </td>
                    <td style={{ padding: "8px 10px", textAlign: "right", color: "#f8fafc", fontWeight: 600 }}>{pos.size}</td>
                    <td style={{ padding: "8px 10px", textAlign: "right", color: "#94a3b8" }}>${Number(pos?.entryPrice ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                    <td style={{ padding: "8px 10px", textAlign: "right", color: "#f8fafc", fontWeight: 600 }}>${Number(pos?.markPrice ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                    <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 700, color: (pos?.unrealizedPnl ?? 0) >= 0 ? "#10b981" : "#ef4444" }}>
                      {(pos?.unrealizedPnl ?? 0) >= 0 ? "+" : ""}${Number(pos?.unrealizedPnl ?? 0).toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Analytics Grid: Equity Curve & Allocation */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 260px", gap: "12px", marginBottom: "12px" }}>
        {/* Equity Curve Chart */}
        <Card className="p-4 bg-[#0c1017] border-[#1e293b]">
          <PanelTitle title="Equity Curve" sub="90-day institutional portfolio trajectory" />
          {equityCurve.length === 0 ? (
            <div style={{ height: 240, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "#64748b", fontFamily: "monospace" }}>
              <TrendingUp size={32} style={{ color: "#334155", marginBottom: "12px", opacity: 0.5 }} />
              <span className="text-body">No equity curve data available</span>
              <span className="text-micro" style={{ color: "#475569", marginTop: "4px" }}>Connect an exchange and launch a strategy to track growth</span>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={equityCurve}>
                <defs>
                  <linearGradient id="eg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10B981" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="#10B981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                <XAxis dataKey="date" stroke="#64748b" fontSize={10} tickLine={false} />
                <YAxis domain={["auto", "auto"]} stroke="#64748b" fontSize={10} tickLine={false} tickFormatter={v => `$${Number(v ?? 0).toLocaleString()}`} />
                <Tooltip content={<CustomTooltip prefix="$" />} />
                <Area dataKey="value" stroke="#10B981" strokeWidth={2} fill="url(#eg)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>

        {/* Asset Allocation Donut */}
        <Card className="p-4 bg-[#0c1017] border-[#1e293b]">
          <PanelTitle title="Asset Allocation" sub="Capital distribution" />
          {/* Requirement 13.6: the allocation figure is derived from the paper summary's equity,
              so it carries the summary's label in its own region. */}
          {environment === "paper" && (
            <div style={{ marginBottom: "8px" }}>
              <SimulatedIndicator provenance={paperProvenance.summary} />
            </div>
          )}
          {allocation.length === 0 ? (
            <div style={{ height: 240, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "#64748b", fontFamily: "monospace" }}>
              <Target size={32} style={{ color: "#334155", marginBottom: "12px", opacity: 0.5 }} />
              <span className="text-body">No allocation data</span>
              <span className="text-micro" style={{ color: "#475569", marginTop: "4px" }}>Open positions will appear here</span>
            </div>
          ) : (
            <>
              <div style={{ display: "flex", justifyContent: "center", marginBottom: "8px" }}>
                <PieChart width={160} height={160}>
                  <Pie data={allocation} cx={80} cy={80} innerRadius={48} outerRadius={72} dataKey="percentage" strokeWidth={0}>
                    {allocation.map((e, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                  </Pie>
                </PieChart>
              </div>
              <ul style={{ display: "flex", flexDirection: "column", gap: "6px", listStyle: "none", padding: 0, margin: 0 }} role="list">
                {allocation.map((a, i) => (
                  <li key={a.asset} style={{ display: "flex", alignItems: "center", gap: "8px" }} role="listitem">
                    <div style={{ width: 8, height: 8, borderRadius: 2, background: COLORS[i % COLORS.length], flexShrink: 0 }} aria-hidden="true" />
                    <span className="text-micro" style={{ color: "#94a3b8", fontFamily: "monospace", flex: 1 }}>{a.asset}</span>
                    <span
                      className="text-micro"
                      style={{
                        color: a.percentage === null ? "#64748b" : "#f8fafc",
                        fontFamily: "monospace",
                        fontWeight: 700,
                      }}
                    >
                      {a.percentage === null ? NOT_REPORTED : `${a.percentage.toFixed(1)}%`}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>

      {/* P&L Heatmap */}
      <Card className="p-4 bg-[#0c1017] border-[#1e293b]">
        <PanelTitle title="P&L Heatmap" sub="Daily execution performance calendar and trade history" />
        {heatmapData.length === 0 ? (
          <div style={{ height: 160, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "#64748b", fontFamily: "monospace" }}>
            <Activity size={28} style={{ color: "#334155", marginBottom: "10px", opacity: 0.5 }} />
            <span className="text-body">No daily trade P&L records</span>
            <span className="text-micro" style={{ color: "#475569", marginTop: "4px" }}>Executed trades and trade history from live and paper bots will appear here</span>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: "4px", marginTop: 8 }}>
            {["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"].map(d => (
              <div key={d} className="text-micro" style={{ color: "#64748b", fontFamily: "monospace", textAlign: "center", padding: "2px 0", letterSpacing: 1 }}>{d}</div>
            ))}
            {heatmapData.map((d, i) => (
              <div
                key={i}
                className="text-micro"
                style={{
                  height: 32,
                  borderRadius: 4,
                  background: d.pnl === null ? "#080a0e" : d.pnl > 200 ? `#10B981aa` : d.pnl > 50 ? `#10B98155` : d.pnl > 0 ? `#10B98122` : d.pnl < -200 ? `#EF4444aa` : d.pnl < -50 ? `#EF444455` : `#EF444422`,
                  border: `1px solid ${d.pnl === null ? "#1e293b" : d.pnl > 0 ? `#10B98140` : `#EF444440`}`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontFamily: "monospace",
                  color: d.pnl === null ? "#475569" : d.pnl > 0 ? "#10B981" : "#EF4444",
                  cursor: d.pnl !== null ? "pointer" : "default",
                  fontWeight: 700,
                }}
                title={d.pnl !== null ? `${d.date}: ${d.pnl >= 0 ? "+" : ""}$${d.pnl.toFixed(2)}` : d.date || ""}
              >
                {d.pnl !== null ? `${d.pnl >= 0 ? "+" : ""}${d.pnl.toFixed(0)}` : ""}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
