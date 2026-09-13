import React, { useState, useEffect, useCallback } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell
} from "recharts";
import { DollarSign, TrendingUp, TrendingDown, Percent, Target, Activity, RefreshCw, FlaskConical, AlertTriangle, Minus } from "lucide-react";
import { C, SectionH, PanelTitle, CustomTooltip } from "../components/ui-legacy/primitives";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { api } from "../api";

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
 * figure; READ_FAILED means the request did not complete, so nothing at all is known. Neither is
 * a zero, and a genuine zero renders as `$0.00`.
 */
const NOT_REPORTED = "Not reported";
const READ_FAILED = "Unavailable — read failed";

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

/** A signed figure's colour. Absent figures get the neutral tone, not the loss tone. */
const signTone = (value) =>
  value === null || value === undefined ? "#94a3b8" : value >= 0 ? "#10b981" : "#ef4444";

/**
 * A signed figure's trend icon.
 *
 * An absent figure gets a neutral dash: the previous `value >= 0 ? up : down` test sent every
 * missing figure down the loss branch, which drew a red downward arrow for a number nobody had.
 */
const SignIcon = ({ value }) => {
  if (value === null || value === undefined) {
    return <Minus size={13} style={{ color: "#64748b" }} aria-hidden="true" />;
  }
  return value >= 0
    ? <TrendingUp size={13} style={{ color: "#10b981" }} aria-hidden="true" />
    : <TrendingDown size={13} style={{ color: "#ef4444" }} aria-hidden="true" />;
};

/**
 * One summary card's value slot: the figure, or - in text, never an empty cell - why there is
 * none (Requirement 28.5).
 *
 * Four outcomes, each visually and textually distinct, so a failed read can never be mistaken
 * for a measurement:
 *
 * | outcome                          | rendering                                |
 * |----------------------------------|------------------------------------------|
 * | the read is in flight            | "Loading..." with the spinner            |
 * | the read did not complete        | amber "Unavailable — read failed" + icon |
 * | the response carried no figure   | muted "Not reported"                     |
 * | a figure arrived, including zero | the formatted figure                     |
 */
function CardFigure({ isLoading, readFailed, value, format }) {
  if (isLoading) {
    return (
      <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        <Activity size={16} className="animate-spin" style={{ color: "#64748b" }} />
        Loading...
      </span>
    );
  }
  if (readFailed) {
    return (
      <span style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        color: "#fbbf24",
        fontSize: "0.8125rem",
        fontWeight: 800,
        fontFamily: "monospace",
      }}>
        <AlertTriangle size={13} aria-hidden="true" />
        {READ_FAILED}
      </span>
    );
  }
  if (value === null || value === undefined) {
    return (
      <span style={{ color: "#64748b", fontSize: "0.8125rem", fontWeight: 700, fontFamily: "monospace" }}>
        {NOT_REPORTED}
      </span>
    );
  }
  return <>{format(value)}</>;
}

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

/** `$1,234.56` - two fraction digits, as every money figure on this page has always rendered. */
const money = (value) =>
  `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** A signed P&L figure, keeping this page's existing unprefixed spelling. */
const signedAmount = (value) =>
  `${value >= 0 ? "+" : ""}${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

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
  const [summary, setSummary] = useState(null);
  const [positions, setPositions] = useState([]);
  const [equityCurve, setEquityCurve] = useState([]);
  const [allocation, setAllocation] = useState([]);
  const [heatmapData, setHeatmapData] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  // Requirement 28.5: a read that did not complete is recorded as a failure, per region, and is
  // rendered as one. Previously both regions fell back to fabricated figures - the summary to a
  // hardcoded 100000/0, the positions ledger to `[]`, which rendered as the honest-looking
  // "no open positions currently held". An empty list is a claim about the account; a failed
  // read did not make it, so the two are kept apart here and on screen.
  const [summaryError, setSummaryError] = useState(null);
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
    setSummaryError(null);
    setPositionsError(null);
    setPositionsCount(null);

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
        const [summaryRes, dashRes, equityRes, allocRes, heatmapRes] = await Promise.allSettled([
          api.portfolio.getSummary(),
          api.dashboard.getDashboard({ environment: "live" }),
          api.portfolio.getEquityCurve(90),
          api.portfolio.getAllocation(),
          api.portfolio.getHeatmap(3),
        ]);

        // Process Summary
        // Requirement 28.5: each field is the figure the response carried or `null`, and a read
        // that did not complete leaves no summary at all rather than a grid of zeros. A zero here
        // now means the server reported zero.
        if (summaryRes.status === "fulfilled" && summaryRes.value) {
          const s = summaryRes.value;
          const acct = s.account || s;
          setSummary({
            total_value: readNumber(acct.total_equity, acct.total_value),
            unrealized_pnl: readNumber(acct.unrealized_pnl, acct.total_pnl),
            realized_pnl: readNumber(acct.realized_pnl),
            available_balance: readNumber(acct.available_balance, acct.total_equity),
            roi_percentage: readNumber(acct.pnl_pct, s.roi_pct),
          });
        } else {
          setSummary(null);
          setSummaryError(
            summaryRes.status === "rejected"
              ? failureSentence(summaryRes.reason)
              : "The response carried no portfolio figures."
          );
        }

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
        const [paperSumRes, paperPosRes] = await Promise.allSettled([
          api.paper.getSummary(),
          api.paper.getPositions(),
        ]);

        setPaperProvenance({
          summary: paperSumRes.status === "fulfilled" ? readPaperProvenance(paperSumRes.value) : null,
          positions: paperPosRes.status === "fulfilled" ? readPaperProvenance(paperPosRes.value) : null,
        });

        // Requirement 28.5: no `?? 100000`. The 100000 was the default opening capital of a paper
        // account, not a balance anybody read, and rendering it as "Total Equity" stated a
        // simulated balance the account may never have held. An unread figure is now `null` and
        // renders as "Not reported"; a read that did not complete renders as a failure.
        let paperTotalEquity = null;
        if (paperSumRes.status === "fulfilled" && paperSumRes.value) {
          const pSum = paperSumRes.value;
          paperTotalEquity = readNumber(pSum.total_equity, pSum.balance);
          setSummary({
            total_value: paperTotalEquity,
            unrealized_pnl: readNumber(pSum.unrealized_pnl),
            realized_pnl: readNumber(pSum.realized_pnl),
            available_balance: readNumber(pSum.available_balance, pSum.balance),
            roi_percentage: readNumber(pSum.roi_pct),
          });
        } else {
          setSummary(null);
          setSummaryError(
            paperSumRes.status === "rejected"
              ? failureSentence(paperSumRes.reason)
              : "The response carried no paper account figures."
          );
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
        // It used to be `summary?.total_value`, and `summary` is the closure value from before this
        // branch ran: on a LIVE -> PAPER flip that was the LIVE equity, a live figure sitting in a
        // PAPER structure, which is what Requirement 13.6 forbids combining. Its fallback was the
        // same fabricated 100000. Neither can occur now: the figure is the paper one or absent.
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

      {/* Top 4 Summary Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "10px", marginBottom: "16px" }}>
        {/* Requirement 13.6: the indicator is the first cell of the figure grid itself, spanning
            it, so equity, unrealized P&L, realized P&L and cash can never be read without it. */}
        {environment === "paper" && (
          <div style={{ gridColumn: "1 / -1", display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
            <SimulatedIndicator provenance={paperProvenance.summary} announce />
            <span style={{ color: "#64748b", fontSize: "0.6875rem" }}>
              Every figure below is simulated. No real capital is held or at risk.
            </span>
          </div>
        )}

        {/* Requirement 28.5: the read that did not complete is said once, in full, in text, and
            spans the grid whose four figures it accounts for. Each card then reads
            "Unavailable — read failed" rather than a number. */}
        {!isLoading && summaryError && (
          <ReadFailureNotice span>
            {environment === "paper" ? "Paper account" : "Portfolio"} figures could not be read, so
            none are shown below. {summaryError}
          </ReadFailureNotice>
        )}

        <Card className="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all bg-[#0c1017] border-[#1e293b]">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <span className="text-micro" style={{ color: "#64748b", fontFamily: "monospace", letterSpacing: 1.5, textTransform: "uppercase" }}>Total Equity</span>
            <DollarSign size={13} style={{ color: "#00d4ff" }} />
          </div>
          <div className="text-section" style={{ color: "#f8fafc", fontWeight: 900, fontFamily: "monospace" }}>
            <CardFigure
              isLoading={isLoading}
              readFailed={Boolean(summaryError)}
              value={summary?.total_value ?? null}
              format={money}
            />
          </div>
        </Card>

        <Card className="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all bg-[#0c1017] border-[#1e293b]">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <span className="text-micro" style={{ color: "#64748b", fontFamily: "monospace", letterSpacing: 1.5, textTransform: "uppercase" }}>Unrealized P&L</span>
            <SignIcon value={summaryError ? null : summary?.unrealized_pnl ?? null} />
          </div>
          <div className="text-section" style={{ color: signTone(summaryError ? null : summary?.unrealized_pnl ?? null), fontWeight: 900, fontFamily: "monospace" }}>
            <CardFigure
              isLoading={isLoading}
              readFailed={Boolean(summaryError)}
              value={summary?.unrealized_pnl ?? null}
              format={signedAmount}
            />
          </div>
        </Card>

        <Card className="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all bg-[#0c1017] border-[#1e293b]">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <span className="text-micro" style={{ color: "#64748b", fontFamily: "monospace", letterSpacing: 1.5, textTransform: "uppercase" }}>Realized P&L</span>
            <SignIcon value={summaryError ? null : summary?.realized_pnl ?? null} />
          </div>
          <div className="text-section" style={{ color: signTone(summaryError ? null : summary?.realized_pnl ?? null), fontWeight: 900, fontFamily: "monospace" }}>
            <CardFigure
              isLoading={isLoading}
              readFailed={Boolean(summaryError)}
              value={summary?.realized_pnl ?? null}
              format={signedAmount}
            />
          </div>
        </Card>

        <Card className="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all bg-[#0c1017] border-[#1e293b]">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <span className="text-micro" style={{ color: "#64748b", fontFamily: "monospace", letterSpacing: 1.5, textTransform: "uppercase" }}>Available Cash</span>
            <DollarSign size={13} style={{ color: "#00d4ff" }} />
          </div>
          <div className="text-section" style={{ color: "#f8fafc", fontWeight: 900, fontFamily: "monospace" }}>
            <CardFigure
              isLoading={isLoading}
              readFailed={Boolean(summaryError)}
              value={summary?.available_balance ?? null}
              format={money}
            />
          </div>
        </Card>
      </div>

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
