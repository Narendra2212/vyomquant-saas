/**
 * ═══════════════════════════════════════════════════════════════════════════
 * pages/Backtester — one run of one stored version, and its result (`/app/backtest`)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 23.1: the CONFIGURATION FLOW and the RUN ACTION.
 * vyomquant-ui-redesign task 23.2: the RESULT REGION, on §7.4's three declared tiers.
 * design.md §7.4, §11.1, §11.2, §11.4. Requirements 6.1–6.6, 14.5, 15.1, 15.2, 15.3, 15.6,
 * 19.3.
 *
 * ═══ THE RESULT REGION (task 23.2) — THREE TIERS, AND ONE PREDICATE ═══
 *
 *   TIER 1  total return · net P&L · max drawdown · Sharpe · win rate · trades  (Req 6.2)
 *   TIER 2  equity curve │ drawdown curve                                      (Req 6.3)
 *   TIER 3  Tabs: Trades │ Monthly returns │ Extended statistics               (Req 6.4)
 *
 * Each band is one container carrying `design/pageHierarchy.js`'s declared
 * `data-page` + `data-page-tier` pair, in that document order, and each figure carries
 * `data-region` spelled as its `pageFields` key — so "the declared figure rendered, and it
 * rendered in its band" is decidable from the DOM rather than from a reviewer's memory
 * (Property 4, task 23.5). Tier 1's six slots are walked from the tier-1 DECLARATION, so a
 * seventh figure cannot appear there without being declared and the order is not a matter of
 * where a `<Metric>` was pasted.
 *
 * ═══ REQUIREMENT 6.6 IS STRUCTURAL, NOT REMEMBERED ═══
 *
 * On a failed run the tier-1 figures are **not rendered at all** — not zeros, and not a
 * rendered row of em-dashes, which would still assert "this run has results". That is one
 * conditional over one total predicate:
 *
 *   {@link tierOneFigures} answers `null` for anything that is not a result object — which
 *       is exactly what `mapBacktestExecutionToUI` returns for a response it could not read —
 *       and a complete `Reported<T>` per declared field otherwise. There is no arm anywhere
 *       below that reads a metric off `results` directly, so there is no arm that can render
 *       a figure the mapper did not produce.
 *   {@link resultRegionState} is `READY` if and only if that answer is not `null`, and the
 *       three tier containers exist ONLY inside the `READY` arm. Every other state — a run in
 *       flight, a refused run, a run that answered with nothing, no run yet — renders ONE
 *       `ds/Panel` over the whole region in that state, which is §7.4's "`Panel
 *       state="loading"` over the whole result region" and its `ErrorState` on failure.
 *
 * The distinction against the not-available marker is deliberate and is the sharp edge of
 * 6.6: a COMPLETED run that omitted one figure renders the marker for that figure, because
 * the row is readable and one field is missing. A run that FAILED renders no row.
 *
 * ═══ THE DRAWDOWN CURVE IS DERIVED, ONCE, IN `lib/` ═══
 *
 * `src/lib/drawdownSeries.js` (task 23.3) is the whole arithmetic: `absoluteDrawdownSeries`
 * is running peak minus current per point, in the equity curve's own units, and this page
 * consumes it rather than recomputing a second opinion. Tier 1's max drawdown stays the
 * engine's own `max_drawdown_pct` — a percentage derived here could contradict it.
 *
 * An equity point the wire omitted derives `drawdown: null`, which `ds/Chart` draws as a
 * GAP (`connectNulls={false}`), and {@link countUnreadableDrawdownPoints} is why the gap is
 * also stated in words: `REASON_UNREADABLE_EQUITY` renders whenever the count is not zero,
 * because an incomplete curve drawn as complete is the fabrication Requirement 14.5 forbids.
 *
 * `api/modules/strategies.js` was changed for this: it read `Number(value.equity ?? 0)`, so
 * an omitted equity arrived as a genuine-looking `0` — a crash to zero that OVERSTATES the
 * drawdown depth — and no downstream branch could tell it from a real zero. It reads `null`
 * now. See that module's own note; this page and its two suites are its only call sites.
 *
 * ═══ WHAT THE ENGINE DOES NOT PRODUCE (Requirement 19.3) ═══
 *
 *   * **Net P&L.** `pageFields` declares `results.total_pnl` and §7.4's table marks it ✅,
 *     but `backtesting_engine.py`'s result dict carries `final_equity` and no `total_pnl` at
 *     all — it computes `final_equity - initial_equity` for a log line and discards it. So
 *     tier 1's second slot is the declared not-available marker with the declared reason,
 *     and nothing here derives a substitute: a figure labelled "Net P&L" that this page
 *     subtracted for itself is not the engine's answer.
 *   * **Monthly returns.** `BacktestRuntime` publishes `monthly_returns` as bare numbers
 *     with no months attached (and resamples them off a positional index rather than the
 *     curve's own timestamps), so nothing in the payload can be dated to a month. The tab
 *     states that, and invents no chart.
 *   * **A dated x axis for the two curves.** `charts.equity_curve.timestamps` is
 *     `DatetimeIndex.astype('int64')` — nanoseconds — while the same curve's record form
 *     carries ISO strings, so a date axis would need this page to guess a unit. Both curves
 *     are plotted against the point's POSITION in the run, and the axis says so. The trade
 *     table carries the real entry and exit instants.
 *
 * ═══ THE SAVED RUNS TABLE ═══
 *
 * `strategy_backtests` persists `win_rate`, `max_drawdown` and `equity_curve` from keys the
 * engine's result dict does not have (`update_backtest_results` reads `win_rate` against an
 * engine that reports `win_rate_pct`), so those columns are structurally `0`/`[]` for every
 * row ever written. They are not rendered: a column that can only ever read `0` misinforms.
 * The columns that remain are the ones the writer fills from keys the engine really sends,
 * and `Inspect` loads a saved row THROUGH `mapBacktestExecutionToUI` — see
 * {@link savedRunAsExecution}, which carries the same argument field by field.
 *
 * ═══ REQUIREMENT 6.1 — FOUR PANELS, ONE COLUMN, IN THIS ORDER ═══
 *
 *   1. Strategy       — the owned strategy, and the immutable version the run will execute
 *   2. Market & data  — `AssetSelector` + `TimeframeSelector`, the builder's own controls
 *   3. Period         — start / end, with the data-availability note from `dataQualityApi`
 *   4. Capital & risk — capital, risk per trade, max drawdown, daily loss limit
 *                       ↳ Advanced (slippage, commission, spread) COLLAPSED (Req 15.6)
 *   then  [ Run backtest ]
 *
 * They are four `ds/Panel` siblings in one flex column, so the order Requirement 6.1 asks
 * for is document order and a DOM-order test reads it directly rather than inferring it from
 * a grid.
 *
 * ═══ WHAT THE ENDPOINT ACTUALLY ACCEPTS, AND WHY FOUR CONTROLS ARE GONE ═══
 *
 * `BacktestExecuteRequest` is `extra="forbid"` and declares exactly nine fields:
 * `version_id`, `start_date`, `end_date`, `initial_capital`, `commission`, `slippage`,
 * `spread`, `risk_per_trade`, `max_drawdown`, `daily_loss_limit`. Every one of them reaches
 * the `BacktestRuntime` built for that request (`_backtest_runtime_for`), so every one of
 * them changes the simulated result. This form offers those, and only those.
 *
 * The page used to also offer **ML confidence**, **trade size %**, **stop loss %** and
 * **take profit %**. None of the four was ever sent anywhere: they were `useState` values
 * read by no request. A control whose value the engine never sees is the "parameter the
 * engine accepts but does not apply" §7.4 and Requirement 6.2 both rule out, so they are
 * removed rather than re-styled. Position sizing has a real home — `risk_per_trade`, which
 * is in the form below — and stops and targets belong to the version's own RISK block, which
 * the compiled plan carries.
 *
 * **There is no fill-model control**, though task 23.1's text lists one in the advanced set.
 * Nothing in the request model, the runtime constructor or the simulator exposes a fill
 * model: the accepted execution-cost parameters are `slippage`, `commission` and `spread`.
 * The accordion therefore holds those three. Inventing a fourth select whose value could not
 * be sent would be exactly the dead affordance this redesign removes.
 *
 * ═══ THE FORM'S DEFAULTS ARE THE ENDPOINT'S OWN DEFAULTS ═══
 *
 * `initial_capital=10000`, `commission=0.001`, `slippage=0.0005`, `spread=0.0002`,
 * `risk_per_trade=0.01`, `max_drawdown=0.2`, `daily_loss_limit=0.05`. The four rate fields
 * are typed as percentages because that is how a trader says them, and {@link RATE_FIELDS}
 * converts on the way out — so `1` in "Risk per trade" is the `0.01` the model declares, and
 * an untouched form sends what the server would have used anyway. Nothing is rounded on the
 * way in: `ds/Field` hands back the string as typed and {@link parseDecimal} is the only
 * thing that reads it.
 *
 * ═══ THE MARKET CONTROLS SCOPE THE DATA CHECK, NOT THE RUN ═══
 *
 * This is the one place the page has to be explicit rather than convenient. The execute
 * endpoint accepts **no** symbol and **no** timeframe: market identity is the version's own
 * DATA block (SB-06, Requirement 12.1) and the venue is the server's public feed. So the
 * market and interval chosen here cannot change what the run trades, and the panel says so
 * in as many words instead of implying otherwise by sitting above a Run button.
 *
 * What they do change is the availability check, which takes all four — market, interval,
 * start, end — and answers for exactly that combination.
 *
 * That also settles what happened to the pre-run gate. The old flow POSTed the check and
 * REFUSED the run on `valid: false`, which is a gate on the wrong market: it could block a
 * run whose version trades ETH because the trader had left BTC in the selector, and pass one
 * whose actual market has no data at all. The check is informational here, and the authority
 * on whether a window can be backtested is the endpoint's own refusal — which arrives as a
 * structured `detail` and is rendered verbatim (see {@link runFailureMessage}).
 *
 * ═══ THE RUN CONTROL (Requirement 6.5) ═══
 *
 * The trigger's `disabled` and `loading` are BOTH exactly {@link isRunInFlight}, a named
 * total function of the lifecycle value — never an inline `status === 'running'`. Property 11
 * (task 23.4) generates every declared lifecycle value plus arbitrary strings and asserts
 * disabled ⟺ non-terminal, so the predicate has to be a function of that value alone and
 * has to answer for values it does not recognise. It treats an unrecognised value as IN
 * FLIGHT: a run whose state we cannot read is not a run we can say has finished, and
 * re-enabling the trigger on it would let a trader fire a second backtest over the first.
 *
 * For the same reason, nothing ELSE disables the trigger. A missing strategy or an invalid
 * capital figure makes the ACTION refuse, with the reason stated on screen — which is
 * `ds/CommandButton`'s own documented alternative to a disabled control ("leave the control
 * enabled and fail the action with a reason instead"). Adding those to `disabled` would both
 * break the biconditional the property asserts and hide the fix behind a grey button.
 *
 * ═══ FOUR READS, ALL THROUGH `usePanelState` ═══
 *
 *   `strategiesApi.list`      the selector's options
 *   `strategiesApi.getById`   only for a strategy the list does not carry (a deep link to an
 *                             entitled strategy); skipped entirely when the list or the
 *                             caller already named it
 *   `strategiesApi.versions`  the pinned version — `is_current` and nothing else
 *   `dataQualityApi.forBacktestWindow`  the availability note, re-issued whenever the market,
 *                             the interval or either bound changes, because `deps` makes that
 *                             a NEW question and the previous answer is discarded rather than
 *                             left on screen describing a window nobody is looking at
 *
 * `usePanelState` and not `usePolling`: `usePolling` lists `data` in its fetch deps, so it
 * recreates its interval every tick, and it keeps the last payload on failure — a note about
 * a window that can no longer be checked is the same lie in smaller type.
 *
 * There is no page-level "instead of the body" error branch, and that is a departure from
 * `pages/LiveTrading` with a reason: the reads here feed the CONFIGURATION only. A failed
 * strategies list must not erase the saved-run history or a completed result, which are true
 * statements that read successfully. So the failure is rendered in place of the four panels
 * — none of which can do anything without a strategy — and the results region stays.
 */

import { lazy, Suspense, useCallback, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { endpoints } from "../api";
import { dataQualityApi } from "../api/modules/dataQuality";
import { mapBacktestExecutionToUI } from "../api/modules/strategies";
import { AssetSelector } from "../components/builder/AssetSelector";
import { TimeframeSelector } from "../components/builder/TimeframeSelector";
import { Accordion, partitionAdvanced } from "../components/ds/Accordion";
import { Alert } from "../components/ds/Alert";
import { CommandButton } from "../components/ds/CommandButton";
import { DataTable } from "../components/ds/DataTable";
import { EmptyState } from "../components/ds/EmptyState";
import { ErrorState } from "../components/ds/ErrorState";
import { Field } from "../components/ds/Field";
import { LoadingState } from "../components/ds/LoadingState";
import { Metric } from "../components/ds/Metric";
import { PageHeader } from "../components/ds/PageHeader";
import { Panel } from "../components/ds/Panel";
import { Tabs } from "../components/ds/Tabs";
import { PAGE_FIELDS_BY_PAGE, PAGES } from "../design/pageFields";
import {
  PAGE_HIERARCHY_BY_PAGE,
  TIER_ATTRIBUTE,
  TIER_PAGE_ATTRIBUTE,
} from "../design/pageHierarchy";
import { fromNullable } from "../design/reported";
import { PANEL_STATES, usePanelState } from "../hooks/usePanelState";
import {
  REASON_UNREADABLE_EQUITY,
  absoluteDrawdownSeries,
  countUnreadableDrawdownPoints,
} from "../lib/drawdownSeries";

/**
 * `ds/Chart`, lazily — the one primitive on this page not imported by path.
 *
 * It is the only module in `src/` that may import recharts (~350KB before gzip), it is
 * deliberately absent from `ds/index.js`, and a static import here would hoist
 * `vendor-recharts` into this route's chunk graph however `vite.config.js` reads (§13.3).
 * `lazy()` needs a module whose `default` is the component, which `Chart.jsx` provides for
 * exactly this call site. Task 23.2 is what took `pages/Backtester.jsx` off the static
 * recharts importer list `tests/unit/ds/Chart.test.jsx` pins.
 */
const Chart = lazy(() => import("../components/ds/Chart"));

/* ══════════════════════════════════════════════════════════════════════════
 * THE RUN LIFECYCLE (Requirement 6.5, Property 11)
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Every value the run state can hold on this page.
 *
 * Four, because the run is ONE synchronous request:
 * `POST .../backtests/execute` creates the `strategy_backtests` row, runs the DAG engine and
 * the simulator, writes the metrics back and answers with the persisted result. There is no
 * job id, so there is no queued or polling state to declare — inventing one would be a state
 * nothing can ever put the page into.
 */
export const RUN_LIFECYCLE = Object.freeze({
  /** No run has been asked for, or the last one has been cleared. */
  IDLE: "idle",
  /** A request is in flight. */
  RUNNING: "running",
  /** The endpoint answered with a result. */
  COMPLETED: "completed",
  /** The endpoint refused, or the request could not be made. */
  FAILED: "failed",
});

/**
 * The lifecycle values from which a NEW run may be started — i.e. the values that are not a
 * run in flight.
 *
 * `idle` is here alongside the two outcomes because "no run has started" and "the run
 * finished" are the same fact as far as the trigger is concerned: there is nothing in flight
 * to protect. Requirement 6.5's re-enable point is exactly the transition into `completed` or
 * `failed`.
 */
export const SETTLED_RUN_STATES = Object.freeze([
  RUN_LIFECYCLE.IDLE,
  RUN_LIFECYCLE.COMPLETED,
  RUN_LIFECYCLE.FAILED,
]);

/**
 * Is a backtest in flight?
 *
 * **Total over every input**, and the one predicate the run control's `disabled` and
 * `loading` are both derived from. Anything that is not one of {@link SETTLED_RUN_STATES} —
 * an unrecognised string, a number, `null`, `undefined` — answers `true`, i.e. IN FLIGHT.
 *
 * The asymmetry is deliberate and it is the safe direction. Treating an unknown value as
 * settled would re-enable the trigger over a run whose state nobody can read, and a second
 * `POST .../backtests/execute` while the first is still running is a second row, a second
 * VectorBT run and a second answer racing the first onto the screen. Treating it as in
 * flight costs a trader one refresh in a state that should never occur.
 *
 * @param {unknown} state A value from {@link RUN_LIFECYCLE}, or anything at all.
 * @returns {boolean}
 */
export function isRunInFlight(state) {
  return !SETTLED_RUN_STATES.includes(state);
}

/**
 * What the trigger reads while it is inoperable.
 *
 * Total for the same reason as {@link isRunInFlight}: an unrecognised state gets a sentence
 * that says only what is known, which is that the run has not reported yet. Naming it a
 * running backtest would be a claim about a state this page cannot interpret.
 *
 * @param {unknown} state
 * @returns {string}
 */
export function runLoadingLabel(state) {
  return state === RUN_LIFECYCLE.RUNNING
    ? "Running backtest…"
    : "Waiting for the run to report…";
}

/**
 * Requirement 15.3's sentence for the disabled trigger. Names the condition AND the way out,
 * because a disabled button cannot be hovered, focused or interrogated.
 */
export const RUN_DISABLED_REASON =
  "A backtest is in flight. This re-enables the moment the run reports completion or "
  + "failure — starting a second one now would run it over the first.";

/**
 * The run trigger (`§7.4`, Requirements 6.5, 15.3).
 *
 * A separate component so Property 11 can drive it across every lifecycle value without
 * standing up the page's four reads, and so the biconditional it asserts is visible in one
 * place: `disabled`, `loading` and the presence of a reason are all one call to
 * {@link isRunInFlight}.
 *
 * @param {Object} props
 * @param {string} props.runState A {@link RUN_LIFECYCLE} value.
 * @param {Function} props.onRun Invoked on activation when nothing is in flight. It — not
 *   this control — decides whether the configuration is runnable, and says so on screen.
 */
export function RunBacktestControl({ runState, onRun }) {
  const inFlight = isRunInFlight(runState);
  return (
    <CommandButton
      intent="primary"
      loading={inFlight}
      loadingLabel={runLoadingLabel(runState)}
      disabled={inFlight}
      disabledReason={inFlight ? RUN_DISABLED_REASON : undefined}
      onClick={onRun}
      data-run-state={typeof runState === "string" ? runState : "unrecognised"}
      data-testid="backtester-run"
    >
      Run backtest
    </CommandButton>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE CONFIGURATION FORM, AS DATA (Requirement 15.6, §11.2)
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The capital-and-risk form. One entry per control, and `advanced: true` is what puts a
 * control behind the collapsed accordion — `partitionAdvanced` reads this list and
 * `ds/Accordion` publishes the resulting ids as `data-advanced-fields`, so which fields
 * collapse is a declaration rather than a consequence of where a closing tag landed.
 *
 * `body` is the request key each control fills, `scale` is how the typed string becomes that
 * key's value, and `max` is the model's own bound (`ge=0, le=1` on the four rates → 0…100 as
 * a percentage; `initial_capital` is `ge=0` with no ceiling).
 */
const CAPITAL_FORM = Object.freeze([
  Object.freeze({
    id: "bt-initial-capital",
    body: "initial_capital",
    label: "Initial capital",
    unit: "USD",
    scale: "amount",
    min: 0,
    max: null,
    hint: "The simulated account the run starts from.",
  }),
  Object.freeze({
    id: "bt-risk-per-trade",
    body: "risk_per_trade",
    label: "Risk per trade",
    unit: "%",
    scale: "percent",
    min: 0,
    max: 100,
    hint: "Of the account, per position. This is the position-sizing control.",
  }),
  Object.freeze({
    id: "bt-max-drawdown",
    body: "max_drawdown",
    label: "Max drawdown",
    unit: "%",
    scale: "percent",
    min: 0,
    max: 100,
    hint: "The decline at which the run's risk engine stops opening positions.",
  }),
  Object.freeze({
    id: "bt-daily-loss-limit",
    body: "daily_loss_limit",
    label: "Daily loss limit",
    unit: "%",
    scale: "percent",
    min: 0,
    max: 100,
    hint: "The loss in one simulated day at which trading halts for that day.",
  }),
  Object.freeze({
    id: "bt-slippage",
    body: "slippage",
    label: "Slippage",
    unit: "%",
    scale: "percent",
    min: 0,
    max: 100,
    advanced: true,
    hint: "Applied to every simulated fill.",
  }),
  Object.freeze({
    id: "bt-commission",
    body: "commission",
    label: "Commission",
    unit: "%",
    scale: "percent",
    min: 0,
    max: 100,
    advanced: true,
    hint: "The trading fee rate the simulator charges per side.",
  }),
  Object.freeze({
    id: "bt-spread",
    body: "spread",
    label: "Spread",
    unit: "%",
    scale: "percent",
    min: 0,
    max: 100,
    advanced: true,
    hint: "The quoted spread the simulator crosses.",
  }),
]);

/** The three ids `ds/Accordion` collapses, declared rather than implied. */
const ADVANCED_FIELD_IDS = partitionAdvanced(CAPITAL_FORM).advanced;

/** The rate fields, whose typed percentage becomes the model's 0…1 rate. */
const RATE_FIELDS = Object.freeze(CAPITAL_FORM.filter((entry) => entry.scale === "percent"));

/**
 * The endpoint's own defaults, expressed in the units the form types them in. An untouched
 * form therefore sends what `BacktestExecuteRequest` would have defaulted to anyway.
 */
const CAPITAL_DEFAULTS = Object.freeze({
  "bt-initial-capital": "10000",
  "bt-risk-per-trade": "1",
  "bt-max-drawdown": "20",
  "bt-daily-loss-limit": "5",
  "bt-slippage": "0.05",
  "bt-commission": "0.1",
  "bt-spread": "0.02",
});

/** `YYYY-MM-DD`, which is what `<input type="date">` holds and what the model accepts. */
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Digits, one optional decimal point, no exponent and no sign. */
const DECIMAL = /^\d+(\.\d+)?$/;

/** The window the form opens on: the last 30 days, as two dates the trader can change. */
const LOOKBACK_DAYS = 30;
const DAY_MS = 24 * 60 * 60 * 1000;

/** `Date` → `YYYY-MM-DD`, in UTC, which is the axis the candles are indexed on. */
const isoDay = (epochMs) => new Date(epochMs).toISOString().slice(0, 10);

/**
 * A typed number, or `null` when the string is not one.
 *
 * Nothing is coerced or rounded: `Number('')` is `0` and `Number('1e5')` is `100000`, and
 * both would turn a typo into a figure the run is charged with. A string this refuses
 * becomes a stated field error, not a silent zero.
 *
 * @param {unknown} text
 * @returns {number|null}
 */
export function parseDecimal(text) {
  if (typeof text !== "string") return null;
  const trimmed = text.trim();
  if (!DECIMAL.test(trimmed)) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

/**
 * Requirement 15.2's message for one control: the field named, and the reason.
 *
 * @param {{label: string, unit: string, min: number, max: number|null}} entry
 * @param {unknown} text
 * @returns {string|null} `null` when the value is acceptable.
 */
export function capitalFieldError(entry, text) {
  const value = parseDecimal(text);
  const suffix = entry.unit === "%" ? "%" : "";
  if (value === null) {
    return `${entry.label} must be a number, without a sign or an exponent.`;
  }
  if (value < entry.min) return `${entry.label} cannot be below ${entry.min}${suffix}.`;
  if (entry.max !== null && value > entry.max) {
    return `${entry.label} cannot be above ${entry.max}${suffix}.`;
  }
  return null;
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE DATA-AVAILABILITY NOTE (§7.4, Requirements 19.3, 14.5)
 * ══════════════════════════════════════════════════════════════════════════ */

/** Why `total_candles` can be absent. Rendered on `ds/Metric`'s marker, never as a 0. */
const CANDLE_COUNT_REASON =
  "The check did not report how many candles it read for this window.";

/** Why `date_range` can be absent. */
const COVERAGE_REASON =
  "The check did not report which span of candles it actually found.";

/** One `issues[]` or `warnings[]` entry → the sentence the service wrote, or nothing. */
const findingText = (finding) => {
  if (typeof finding === "string" && finding.trim() !== "") return finding.trim();
  if (!finding || typeof finding !== "object") return null;
  if (typeof finding.message === "string" && finding.message.trim() !== "") {
    return finding.message.trim();
  }
  return typeof finding.code === "string" && finding.code.trim() !== "" ? finding.code.trim() : null;
};

/** The `issues[]` / `warnings[]` list as sentences, in the order the service reported them. */
const findingList = (list) => (Array.isArray(list) ? list.map(findingText).filter(Boolean) : []);

/**
 * What the availability check found, or nothing at all.
 *
 * There is no arm of this component that reassures. Before a market and an interval have been
 * chosen the check has not been issued and this renders `null` — not "data looks available",
 * not a dash, not a zero. Every other arm states which of the four honest things is true: the
 * check is running, the check could not be completed, the check answered with no report, or
 * the check answered — in which case the verdict, the two figures and every issue and warning
 * are the service's own.
 *
 * @param {Object} props
 * @param {string} props.state A `usePanelState` state.
 * @param {object|null} props.report The verdict body.
 * @param {*} props.error The rejection, when the read failed.
 * @param {Function} props.onRetry Re-issues the check.
 * @param {string} props.described The market, interval and window in one phrase.
 */
function DataAvailabilityNote({ state, report, error, onRetry, described }) {
  if (state === PANEL_STATES.IDLE) return null;

  if (state === PANEL_STATES.LOADING) {
    return (
      <p
        role="status"
        className="text-small text-content-secondary"
        data-testid="backtester-data-availability"
        data-availability-state={state}
      >
        {`Checking what historical data exists for ${described}…`}
      </p>
    );
  }

  if (
    state === PANEL_STATES.ERROR
    || state === PANEL_STATES.UNAUTHORISED
    || state === PANEL_STATES.UNAVAILABLE
  ) {
    return (
      <div data-testid="backtester-data-availability" data-availability-state={state}>
        <ErrorState error={error} context="backtest" onRetry={onRetry} compact />
      </div>
    );
  }

  if (state === PANEL_STATES.EMPTY) {
    return (
      <p
        role="status"
        className="text-small text-content-secondary"
        data-testid="backtester-data-availability"
        data-availability-state={state}
      >
        {`The data check answered with no report for ${described}, so nothing is known about `
          + "this window."}
      </p>
    );
  }

  const verdict = report?.valid;
  const info = report?.data_info ?? null;
  const issues = findingList(report?.issues);
  const warnings = findingList(report?.warnings);

  return (
    <div
      className="flex min-w-0 flex-col gap-2"
      data-testid="backtester-data-availability"
      data-availability-state={state}
      data-availability-valid={typeof verdict === "boolean" ? String(verdict) : "unreported"}
    >
      <p role="status" className="text-small text-content-secondary">
        {verdict === true
          ? `The data check found no disqualifying problem with ${described}.`
          : verdict === false
            ? `The data check refused ${described}. The reasons are below, as the service `
              + "reported them."
            : `The data check answered for ${described} without stating a verdict, so this `
              + "window is neither confirmed nor refused."}
      </p>

      <div className="grid min-w-0 grid-cols-2 gap-4">
        <Metric
          label="Candles read"
          tier={3}
          format="integer"
          value={fromNullable(info?.total_candles, CANDLE_COUNT_REASON)}
          hint="How many candles the check actually read between the two dates."
          data-region="dataCandleCount"
        />
        <Metric
          label="Coverage found"
          tier={3}
          format="raw"
          value={fromNullable(info?.date_range, COVERAGE_REASON)}
          hint="The first and last candle the check found, which can be narrower than the window."
          data-region="dataCoverage"
        />
      </div>

      {issues.length === 0 ? null : (
        <ul className="flex min-w-0 flex-col gap-1" data-availability-issues={issues.length}>
          {issues.map((issue) => (
            <li key={issue} className="text-small text-content-primary">{issue}</li>
          ))}
        </ul>
      )}

      {warnings.length === 0 ? null : (
        <ul className="flex min-w-0 flex-col gap-1" data-availability-warnings={warnings.length}>
          {warnings.map((warning) => (
            <li key={warning} className="text-small text-content-secondary">{warning}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE RESULT REGION, AS THE DECLARATION HAS IT (task 23.2, §7.4)
 * ══════════════════════════════════════════════════════════════════════════ */

/** Every Backtester field `pageFields` declares. Labels and reasons are READ, never retyped. */
const BACKTESTER_FIELDS = PAGE_FIELDS_BY_PAGE[PAGES.BACKTESTER] ?? [];

/** §7.4's three bands, as `pageHierarchy` declares them. */
const RESULT_TIERS = PAGE_HIERARCHY_BY_PAGE[PAGES.BACKTESTER]?.tiers ?? [];

/** Tier 1, in declaration order. The row below is this list and nothing else (Req 6.2). */
const TIER_ONE = RESULT_TIERS.filter((entry) => entry.tier === 1);

/** One declared field's entry, or `null`. */
const fieldEntry = (field) => BACKTESTER_FIELDS.find((entry) => entry.field === field) ?? null;

/** One field's declared label. Two spellings of one figure are two figures to a test. */
const labelOf = (field) => fieldEntry(field)?.label ?? field;

/** One field's declared reason — the sentence rendered INSTEAD of the figure (Req 19.3). */
const reasonOf = (field) => fieldEntry(field)?.reason ?? undefined;

/**
 * How each tier-1 slot is read off the mapped result, and how it is formatted.
 *
 * `read` is the declared path's last segment, because `mapBacktestExecutionToUI` spreads the
 * engine's `results` FLAT and renames nothing; the two fallbacks — `max_drawdown` and
 * `trades_count` — are the `inputs` the same declarations list, not guesses.
 *
 * Percentages are NOT scaled: every one of these arrives from the engine already in percent
 * units (`Total Return [%]`, `Max Drawdown [%]`, `win_rate * 100`), and `ds/Metric` appends
 * the sign without multiplying. `precision` is presentational and applies only to the
 * rendering; nothing here rounds a value before it is read.
 */
const TIER_ONE_FIGURE = Object.freeze({
  totalReturn: Object.freeze({
    format: "percent",
    precision: 2,
    read: (results) => results.total_return_pct,
    hint: "The run's total return over the window, as the engine reported it.",
  }),
  netPnl: Object.freeze({
    format: "currency",
    precision: 2,
    read: (results) => results.total_pnl,
    hint: "The run's net profit and loss. The engine reports a final equity and no net "
      + "P&L, so this is the not-available marker rather than a figure this page subtracted "
      + "for itself.",
  }),
  maxDrawdown: Object.freeze({
    format: "percent",
    precision: 2,
    read: (results) => results.max_drawdown_pct ?? results.max_drawdown,
    hint: "The worst decline the run went through — the engine's own figure, not the "
      + "drawdown curve's.",
  }),
  sharpe: Object.freeze({
    format: "number",
    precision: 2,
    read: (results) => results.sharpe_ratio,
    hint: "Return per unit of volatility, annualised by the engine.",
  }),
  winRate: Object.freeze({
    format: "percent",
    precision: 2,
    read: (results) => results.win_rate_pct,
    hint: "The share of closed trades that ended in profit.",
  }),
  tradeCount: Object.freeze({
    format: "integer",
    read: (results) => results.total_trades ?? results.trades_count,
    hint: "How many trades the simulation closed. A reported 0 is a real outcome — a "
      + "strategy that never triggered.",
  }),
});

/**
 * The six tier-1 figures, or **`null` when there is no result to render at all**.
 *
 * This is Requirement 6.6's mechanism. `null` in means `null` out, and so does anything that
 * is not a result object — which is exactly what `mapBacktestExecutionToUI` answers for a
 * response it could not read. The caller renders the tier-1 container only for a non-`null`
 * answer, so a failed run has no row rather than a row of zeros or of em-dashes.
 *
 * A non-`null` answer carries one `Reported<T>` per DECLARED field, so a completed run that
 * omitted one figure renders that one figure's marker with the declared reason and the other
 * five as read. Total: nothing here throws, whatever it is handed.
 *
 * Exported for the property tests (23.4, 23.5) — the predicate they assert has to be the
 * predicate the page renders from, not a second copy of it.
 *
 * @param {unknown} results `mapBacktestExecutionToUI`'s answer, or anything at all.
 * @returns {Readonly<Object>|null}
 */
export function tierOneFigures(results) {
  if (results === null || typeof results !== "object" || Array.isArray(results)) return null;

  const figures = {};
  for (const { key } of TIER_ONE) {
    const read = TIER_ONE_FIGURE[key]?.read;
    figures[key] = fromNullable(
      typeof read === "function" ? read(results) : null,
      reasonOf(key),
    );
  }
  return Object.freeze(figures);
}

/**
 * The whole result region's `ds/Panel` state — one function of the run lifecycle and the
 * result, and the only thing that decides whether the three tiers exist.
 *
 * `loading` covers the WHOLE region while a run is in flight (§7.4), `error` is Requirement
 * 6.6's `ErrorState` in place of the figures, and `empty` covers both "no run yet" and "the
 * run answered with nothing renderable" — {@link resultEmptyCopy} tells those two apart in
 * words. `ready` is reachable only when {@link tierOneFigures} answered, which is what makes
 * "no figures at all on failure" structural.
 *
 * @param {unknown} runState A {@link RUN_LIFECYCLE} value, or anything.
 * @param {unknown} results `mapBacktestExecutionToUI`'s answer, or anything.
 * @returns {string} A `PANEL_STATES` value.
 */
export function resultRegionState(runState, results) {
  if (isRunInFlight(runState)) return PANEL_STATES.LOADING;
  if (runState === RUN_LIFECYCLE.FAILED) return PANEL_STATES.ERROR;
  return tierOneFigures(results) === null ? PANEL_STATES.EMPTY : PANEL_STATES.READY;
}

/**
 * What every "there is nothing here" state offers as its next action.
 *
 * Deliberately NOT worded as the run trigger. `RunBacktestControl` is the one control
 * Property 11 (task 23.4) holds the `disabled` ⟺ non-terminal biconditional over, and a
 * second control reading the same words would invite the reading that this one carries the
 * same guarantee. It does not need to: every state that renders this action is unreachable
 * while a run is in flight, because `resultRegionState` answers `loading` for the whole
 * region then and `ds/Panel` renders no other body in that state.
 */
const RETRY_LABEL = "Try the run again";

/**
 * Requirement 14.1's three fields for the two ways this region can be empty.
 *
 * They are different facts and they get different words: a run that COMPLETED and answered
 * with nothing renderable is a statement about that run, while "nothing has been run" is a
 * statement about the page. Neither says anything about performance.
 *
 * @param {unknown} runState
 * @param {Function} onRun
 */
const resultEmptyCopy = (runState, onRun) => (
  runState === RUN_LIFECYCLE.COMPLETED
    ? {
      headline: "This run reported no result",
      body: "The endpoint answered without a result body, so there are no figures, no "
        + "curves and no trades to show. Nothing is substituted for them.",
      action: { label: RETRY_LABEL, onClick: onRun },
    }
    : {
      headline: "No backtest has been run yet",
      body: "The six headline figures, the equity and drawdown curves and the trade list "
        + "all appear here once a run reports. Nothing stands in for them before then.",
      action: { label: "Start the run", onClick: onRun },
    }
);

/* ── TIER 2: the two curves, from one derived series ─────────────────────── */

/**
 * The rows both tier-2 charts are drawn from, plus the two counts the panels need.
 *
 * ONE array for both charts, because it is one derivation: `absoluteDrawdownSeries` emits
 * exactly one point per equity point — carrying the equity it read, or `null` where it could
 * not read one — so the two curves cannot end up with different lengths or a different idea
 * of where the gaps are.
 *
 * `point` is the position in the run, 1-based, and it is the x of both charts. See the module
 * docblock for why not a date: the payload's own timestamps are nanosecond integers in one
 * shape and ISO strings in another, and an axis this page had to guess the unit of is worse
 * than an axis that says exactly what it plots.
 *
 * @param {unknown} results
 * @returns {{rows: Array<Object>, readable: number, unreadable: number}}
 */
export function resultCurves(results) {
  const equity = Array.isArray(results?.equity) ? results.equity : [];
  const derived = absoluteDrawdownSeries(equity);

  return {
    rows: derived.map((entry, index) => ({
      point: index + 1,
      equity: entry.equity,
      drawdown: entry.drawdown,
    })),
    readable: derived.filter((entry) => entry.readable === true).length,
    unreadable: countUnreadableDrawdownPoints(derived),
  };
}

/** Requirement 14.1 for a curve that has no readable point. Never a flat line. */
const CURVE_EMPTY = Object.freeze({
  headline: "This run produced no equity series",
  body: "Both curves are drawn from the equity the engine reported point by point, and this "
    + "run reported none that could be read. A flat line here would claim the account never "
    + "moved.",
});

/* ── TIER 3: the three tabs ──────────────────────────────────────────────── */

/** The tab ids. `trades` is what a new run opens on, which is what "collapsed" means here. */
const DETAIL_TABS = Object.freeze({
  TRADES: "trades",
  MONTHLY: "monthly-returns",
  EXTENDED: "extended-statistics",
});

/** §7.7's page size is 50; a trade list read in one tab is comfortable at 20. */
const TRADES_PER_PAGE = 20;

/**
 * The trade list's columns (Requirement 6.4's detail, behind the tabs).
 *
 * Every key is one `backtesting_engine.py` writes into each `results.trades[]` record.
 * `durationText` is the one projected field — see {@link tradeDetailRows} for why a reported
 * `0` is not rendered as a duration.
 *
 * `priority: 3` drops a column below `--breakpoint-laptop` (Requirement 17.2), and the three
 * that carry it are the ones a trader scanning P&L can lose first.
 */
const TRADE_COLUMNS = Object.freeze([
  { key: "trade_id", header: "#", align: "numeric", format: "number", priority: 3 },
  { key: "entry_time", header: "Entry", format: "timestamp", sortable: true },
  { key: "entry_price", header: "Entry price", align: "numeric", format: "currency" },
  { key: "exit_time", header: "Exit", format: "timestamp" },
  { key: "exit_price", header: "Exit price", align: "numeric", format: "currency" },
  { key: "side", header: "Side", format: "text" },
  { key: "quantity", header: "Quantity", align: "numeric", format: "number" },
  { key: "gross_pnl", header: "Gross P&L", align: "numeric", format: "currency", sortable: true },
  { key: "fees", header: "Fees", align: "numeric", format: "currency" },
  { key: "net_pnl", header: "Net P&L", align: "numeric", format: "currency", sortable: true },
  { key: "return_pct", header: "Return %", align: "numeric", format: "number", priority: 3 },
  { key: "durationText", header: "Held for", format: "text", priority: 3 },
]);

/** `123.4` seconds → `2m 03s`. The engine reports the duration in seconds. */
const secondsHeld = (seconds) => {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) return null;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${String(Math.round(seconds % 60)).padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
};

/**
 * The trade rows, with the one field the table cannot read straight off the record.
 *
 * `duration` is initialised to `0` by the engine and overwritten only when both timestamps
 * parse, so a `0` there is indistinguishable from "not measured" — and "0s" beside a trade
 * that was held for two days is a fabricated figure. A non-positive duration therefore
 * projects to `null`, which `ds/DataTable` renders as the not-available marker.
 *
 * @param {unknown} trades
 * @returns {Array<Object>}
 */
export function tradeDetailRows(trades) {
  if (!Array.isArray(trades)) return [];
  return trades
    .filter((trade) => trade !== null && typeof trade === "object")
    .map((trade) => ({ ...trade, durationText: secondsHeld(trade.duration) }));
}

/**
 * The extended statistics, which are the engine's OWN remaining metrics.
 *
 * `fees` is the one field `pageFields` declares among them (§7.4's table), so its label, its
 * absence reason and its `data-region` come from the declaration. The other eight are keys
 * `backtesting_engine.py` and `backtest_runtime.py` really write — nothing here is a
 * placeholder for a metric that does not exist — and they are declared locally rather than
 * added to `pageFields` on purpose: `pageHierarchy`'s Backtester hierarchy is frozen and
 * `pageHierarchy.test.js` asserts that every declared field is tiered or explicitly
 * untiered, so a new `pageFields` entry would need a tier entry to go with it and that is a
 * change to what §7.4's bands claim, not a rendering decision.
 */
const EXTENDED_STATISTICS = Object.freeze([
  Object.freeze({
    key: "total_fees_paid",
    field: "fees",
    format: "currency",
    precision: 2,
    hint: "Everything the simulator charged across both sides of every fill.",
  }),
  Object.freeze({
    key: "final_equity",
    label: "Final equity",
    format: "currency",
    precision: 2,
    reason: "This run did not report a final equity.",
    hint: "The simulated account at the last point of the run.",
  }),
  Object.freeze({
    key: "profit_factor",
    label: "Profit factor",
    format: "number",
    precision: 2,
    reason: "This run did not report a profit factor.",
    hint: "Gross profit divided by gross loss. The engine reports 9999 for a run with no "
      + "losing trade, which is its stand-in for an infinite ratio.",
  }),
  Object.freeze({
    key: "sortino_ratio",
    label: "Sortino ratio",
    format: "number",
    precision: 2,
    reason: "This run did not report a Sortino ratio.",
    hint: "Return per unit of DOWNSIDE volatility only.",
  }),
  Object.freeze({
    key: "calmar_ratio",
    label: "Calmar ratio",
    format: "number",
    precision: 2,
    reason: "This run did not report a Calmar ratio.",
    hint: "Annualised return over the worst drawdown.",
  }),
  Object.freeze({
    key: "expectancy",
    label: "Expectancy",
    format: "currency",
    precision: 2,
    reason: "This run did not report an expectancy.",
    hint: "The average outcome of one trade at this win rate.",
  }),
  Object.freeze({
    key: "recovery_factor",
    label: "Recovery factor",
    format: "number",
    precision: 2,
    reason: "This run did not report a recovery factor.",
    hint: "Net profit over the worst drawdown.",
  }),
  Object.freeze({
    key: "kelly",
    label: "Kelly fraction",
    format: "number",
    precision: 3,
    reason: "This run did not report a Kelly fraction.",
    hint: "The position fraction the run's own win rate and average outcomes imply.",
  }),
  Object.freeze({
    key: "execution_time_seconds",
    label: "Simulation time",
    format: "number",
    precision: 2,
    unit: "s",
    reason: "This run did not report how long the simulation took.",
    hint: "How long the engine took to run, not anything about the market.",
  }),
]);

/**
 * One saved `strategy_backtests` row → the execute-response shape the mapper reads.
 *
 * `Inspect` goes through `mapBacktestExecutionToUI` like a live run does, so the results
 * region has exactly one input shape and there is no second, hand-assembled object that
 * could drift from it.
 *
 * The row is NOT copied wholesale, and the omissions are the point:
 *
 *   * `win_rate` and `max_drawdown` are dropped. `backtest_service.update_backtest_results`
 *     writes them from `results.get("win_rate", 0)` / `results.get("max_drawdown", 0)`
 *     against an engine dict that carries `win_rate_pct` and `max_drawdown_pct`, so every
 *     persisted row holds `0` for both. Carrying them across would render "0%" — "every
 *     trade lost", "the account never declined" — from a key nobody ever wrote.
 *   * `equity_curve` is carried only when it is a NON-EMPTY array, for the same reason: the
 *     same mismatch (`results.get("equity_curve", [])` against a curve the engine publishes
 *     under `charts.equity_curve`) means the stored column is `[]`, and an empty series
 *     renders the empty state rather than a flat line.
 *
 * @param {unknown} run One row of `GET /api/backtests`.
 * @returns {Object|null} An execute-response-shaped object, or `null` for an unusable row.
 */
export function savedRunAsExecution(run) {
  if (run === null || typeof run !== "object" || Array.isArray(run)) return null;

  const curve = Array.isArray(run.equity_curve) && run.equity_curve.length > 0
    ? { equity_curve: { values: run.equity_curve } }
    : null;

  return {
    backtest_id: run.id ?? null,
    status: run.status ?? null,
    strategy_id: run.strategy_id ?? null,
    version: run.version ?? null,
    results: {
      total_return_pct: run.total_return_pct,
      sharpe_ratio: run.sharpe_ratio,
      sortino_ratio: run.sortino_ratio,
      profit_factor: run.profit_factor,
      total_trades: run.total_trades,
      final_equity: run.final_capital,
      execution_time_seconds: run.execution_time_seconds,
      trades: Array.isArray(run.trades) ? run.trades : undefined,
      ...(curve === null ? {} : { charts: curve }),
    },
  };
}

/**
 * The saved-history table's columns.
 *
 * "Win Rate" and "Max DD" are gone rather than restyled — see {@link savedRunAsExecution}:
 * both columns read a database field written from a key the engine does not send, so both
 * could only ever be `0`. `Trades` and `Sharpe` take their place because those two ARE
 * written from keys the engine sends.
 *
 * The two text columns are projected rather than formatted by the table, because a column
 * that renders `12.5` where a trader expects `12.50%` and one that renders an empty cell for
 * an absent figure are two different mistakes; `null` from the projection reaches
 * `ds/DataTable`'s own not-available marker.
 */
const SAVED_RUN_COLUMNS = Object.freeze([
  { key: "created_at", header: "Created", format: "timestamp", sortable: true },
  { key: "strategy", header: "Strategy", format: "text" },
  { key: "version", header: "Version", format: "text" },
  { key: "dataset", header: "Pair", format: "symbol" },
  { key: "status", header: "Status", format: "text" },
  { key: "returnText", header: "Return %", align: "numeric", format: "text" },
  { key: "total_trades", header: "Trades", align: "numeric", format: "number" },
  { key: "sharpeText", header: "Sharpe", align: "numeric", format: "text" },
]);

/** The one empty list, shared, so a read that answered with nothing keeps its identity. */
const NO_SAVED_RUNS = Object.freeze([]);

/** `12.5` → `"12.50%"`, and anything unreadable → `null`, never `"0.00%"`. */
const percentText = (value) => (
  typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(2)}%` : null
);

/** `1.2345` → `"1.23"`, and anything unreadable → `null`. */
const decimalText = (value, digits) => (
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : null
);

/** The name the saved row itself carries, or the id it was saved under. */
const savedRunName = (run) => {
  const name = run?.blueprint?.name;
  if (typeof name === "string" && name.trim() !== "") return name.trim();
  const id = run?.strategy_id;
  return typeof id === "string" && id !== "" ? `Strategy ${id.slice(0, 8)}` : null;
};

/* ══════════════════════════════════════════════════════════════════════════
 * THE PAGE
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The message a refused or failed run is reported with (task 17.1).
 *
 * The extended execute endpoint refuses with a structured `detail` — `{error, message, …}` —
 * naming the version, the window or the market that could not be resolved (Requirements
 * 5.4, 7.4). `apiClient`'s `ApiError` only lifts `detail` into its own message when the
 * backend sent a bare string, so the object form is read here rather than dropped in favour
 * of a generic "request failed".
 *
 * It stays a direct read rather than going through `ds/ErrorState`: `translateError` never
 * reads an error's message by design, and `errorCopy.js` carries no entry for
 * `BACKTEST_VERSION_UNAVAILABLE` or `BACKTEST_FEED_UNCONFIGURED`, so routing this through it
 * would replace the server's specific sentence — "This strategy has no saved version, so
 * there is nothing to backtest" — with "Nothing is shown rather than a partial reading".
 * These bodies are authored for a trader and carry no status, path or class name.
 */
const runFailureMessage = (error) => {
  const detail = error?.data?.detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") {
    return detail.message;
  }
  if (typeof detail === "string") return detail;
  return error?.message || "The backtest could not be run.";
};

/** A strategies list body in any of the three shapes the endpoint is known to answer with. */
const strategyRowsOf = (payload) => {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === "object") {
    for (const key of ["strategies", "data", "items"]) {
      if (Array.isArray(payload[key])) return payload[key];
    }
  }
  return [];
};

/** One strategy record → the two things this page needs of it. */
const namedStrategy = (record, fallbackId) => {
  if (!record || typeof record !== "object") return null;
  const id = record.id ?? fallbackId;
  if (id === undefined || id === null || String(id) === "") return null;
  const name = record.name ?? record.strategy_name ?? null;
  return { id: String(id), name: typeof name === "string" && name.trim() !== "" ? name.trim() : null };
};

export default function Backtester({ strategy: strategyProp, onBack: onBackProp }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const strategyIdParam = searchParams.get("strategy_id");

  /*
   * Three entry paths, one selection. A direct prop (inline use), route state (a `navigate`
   * from Strategies or the Builder), or `?strategy_id=` (a deep link or a bookmark) all end
   * at the same thing: an id in `selectedId`. Requirement 4.7's "runs identically whichever
   * entry path put a strategy on the page" is that collapse, and it is why the version read
   * and the run below name no entry path at all.
   */
  const handedOver = useMemo(
    () => namedStrategy(strategyProp ?? location.state?.strategy ?? null),
    [strategyProp, location.state],
  );
  const [selectedId, setSelectedId] = useState(
    () => handedOver?.id ?? (strategyIdParam ? String(strategyIdParam) : ""),
  );
  const onBack = onBackProp ?? (() => navigate("/app/strategies"));

  /* ── The selector's options ──────────────────────────────────────────────── */
  const readStrategies = useCallback(() => endpoints.strategies.list(), []);
  const {
    state: listState,
    data: listPayload,
    error: listError,
    refetch: refetchList,
  } = usePanelState(readStrategies);

  const strategyRows = useMemo(() => strategyRowsOf(listPayload), [listPayload]);
  const listedOptions = useMemo(
    () => strategyRows
      .map((row) => namedStrategy(row))
      .filter(Boolean)
      .map((entry) => ({ value: entry.id, label: entry.name ?? `Strategy ${entry.id}` })),
    [strategyRows],
  );

  const listedStrategy = useMemo(
    () => strategyRows.map((row) => namedStrategy(row)).find((entry) => entry?.id === selectedId) ?? null,
    [strategyRows, selectedId],
  );

  /*
   * The one-strategy read, issued ONLY when nothing else can name the selection — a deep link
   * to a strategy the owner listing does not carry, which is what an entitled strategy looks
   * like from here. When the caller handed one over, or the list already holds it, this stays
   * `idle` and no request is made.
   */
  const handedOverSelection = handedOver !== null && handedOver.id === selectedId ? handedOver : null;
  const needsDetail = selectedId !== "" && handedOverSelection === null && listedStrategy === null;
  const readStrategy = useCallback(
    () => endpoints.strategies.getById(selectedId),
    [selectedId],
  );
  const {
    state: detailState,
    data: detailPayload,
    error: detailError,
  } = usePanelState(readStrategy, { deps: [selectedId], enabled: needsDetail });

  const detailStrategy = useMemo(
    () => namedStrategy(detailPayload?.strategy ?? detailPayload, selectedId),
    [detailPayload, selectedId],
  );

  const strategy = handedOverSelection ?? listedStrategy ?? detailStrategy ?? null;

  /*
   * The selected strategy is always one of the offered options, even when the listing does not
   * carry it. A `<select>` whose `value` matches no option renders as though nothing were
   * chosen, so a deep link to an entitled strategy would show "Choose a strategy…" over a
   * configuration that is in fact pinned to a version of that strategy. The entry appended
   * here is a strategy the server named — through the listing, the caller, or the by-id read —
   * and never an id typed into a URL: `strategy` is `null` until one of the three answers.
   */
  const strategyOptions = useMemo(() => {
    if (strategy === null || listedOptions.some((option) => option.value === strategy.id)) {
      return listedOptions;
    }
    return [
      { value: strategy.id, label: strategy.name ?? `Strategy ${strategy.id}` },
      ...listedOptions,
    ];
  }, [listedOptions, strategy]);

  /** The list read failed. A known strategy still runs; the trader simply cannot switch. */
  const listFailed = listState === PANEL_STATES.ERROR
    || listState === PANEL_STATES.UNAUTHORISED
    || listState === PANEL_STATES.UNAVAILABLE;

  /* ── The version the run executes (Requirements 5.2, 5.7) ────────────────── */
  const readVersions = useCallback(
    () => endpoints.strategies.versions(selectedId),
    [selectedId],
  );
  const {
    state: versionState,
    data: versionPayload,
  } = usePanelState(readVersions, { deps: [selectedId], enabled: selectedId !== "" });

  /*
   * Only the version the backend marks `is_current`. A strategy with no current version
   * leaves this `null` and the run is sent without a `version_id`; the endpoint then answers
   * 422 naming the version as unavailable rather than this page guessing which of several
   * persisted versions the trader meant.
   */
  const currentVersion = useMemo(() => {
    const rows = Array.isArray(versionPayload) ? versionPayload : versionPayload?.versions;
    if (!Array.isArray(rows)) return null;
    return rows.find((row) => row?.is_current) ?? null;
  }, [versionPayload]);
  const versionId = currentVersion?.id ?? null;

  /* ── Market & data. Neither is sent with the run; see the module docblock. ── */
  const [market, setMarket] = useState(null);
  const [barInterval, setBarInterval] = useState(null);

  /* ── Period ─────────────────────────────────────────────────────────────── */
  const [startDate, setStartDate] = useState(() => isoDay(Date.now() - LOOKBACK_DAYS * DAY_MS));
  const [endDate, setEndDate] = useState(() => isoDay(Date.now()));

  /* ── Capital & risk ─────────────────────────────────────────────────────── */
  const [capital, setCapital] = useState(() => ({ ...CAPITAL_DEFAULTS }));
  const [submitted, setSubmitted] = useState(false);

  const capitalErrors = useMemo(
    () => Object.fromEntries(
      CAPITAL_FORM.map((entry) => [entry.id, capitalFieldError(entry, capital[entry.id])]),
    ),
    [capital],
  );

  const dateErrors = useMemo(() => {
    const errors = { start: null, end: null };
    if (!ISO_DAY.test(startDate)) errors.start = "Start date must be a date, as YYYY-MM-DD.";
    if (!ISO_DAY.test(endDate)) errors.end = "End date must be a date, as YYYY-MM-DD.";
    if (errors.start === null && errors.end === null && startDate >= endDate) {
      errors.end = "End date must be after the start date.";
    }
    return errors;
  }, [startDate, endDate]);

  /* ── The availability check ──────────────────────────────────────────────── */
  const windowIsAddressable = market !== null
    && barInterval !== null
    && dateErrors.start === null
    && dateErrors.end === null;

  const readAvailability = useCallback(
    () => dataQualityApi.forBacktestWindow({
      symbol: market,
      timeframe: barInterval,
      startDate,
      endDate,
    }),
    [market, barInterval, startDate, endDate],
  );
  const {
    state: availabilityState,
    data: availabilityReport,
    error: availabilityError,
    refetch: refetchAvailability,
  } = usePanelState(readAvailability, {
    deps: [market, barInterval, startDate, endDate],
    enabled: windowIsAddressable,
  });

  const describedWindow = `${market ?? "no market"} ${barInterval ?? "no interval"} between `
    + `${startDate} and ${endDate}`;

  /* ── The run ────────────────────────────────────────────────────────────── */
  const [runState, setRunState] = useState(RUN_LIFECYCLE.IDLE);
  const [runError, setRunError] = useState(null);
  /*
   * The rejection itself, kept alongside its message. The Alert beside the trigger renders
   * the server's own sentence (see {@link runFailureMessage}); the result region renders
   * `ds/ErrorState`, which reads the ERROR — never a message string (Requirements 14.3,
   * 14.4). Two surfaces, two jobs: one says why the run was refused, the other says that
   * there are consequently no figures (Requirement 6.6).
   */
  const [runFailure, setRunFailure] = useState(null);
  const [refusal, setRefusal] = useState(null);
  const [results, setResults] = useState(null);

  /* ── The result region's own view state (task 23.2) ──────────────────────── */
  const [detailTab, setDetailTab] = useState(DETAIL_TABS.TRADES);
  /** 1-based, as `ds/DataTable` counts pages. */
  const [tradesPage, setTradesPage] = useState(1);

  /**
   * "Saved Backtest History" — the rows the backend holds (task 17.2).
   *
   * One read, issued on mount and re-issued when a run completes, because both need the same
   * thing. `POST .../backtests/execute` creates the `strategy_backtests` row, runs, and writes
   * the metrics back before it answers, so a completed run is *already* saved (Requirement
   * 10.1) — this reads it rather than appending a row assembled from what the page happens to
   * have on screen.
   *
   * Through `usePanelState` like every other read here, which is also what makes the failure
   * honest: a refresh that fails drops the list rather than leaving yesterday's rows on screen
   * under a fresh heading, and `refetch` never rejects, so a failed refresh cannot be mistaken
   * for a failed run.
   */
  const readSavedRuns = useCallback(
    () => endpoints.strategies.listBacktests({ limit: 20 }),
    [],
  );
  const {
    state: savedState,
    data: savedPayload,
    error: savedError,
    refetch: refetchSaved,
  } = usePanelState(readSavedRuns);
  /*
   * Memoised because the table's rows are derived from it: a fresh `[]` on every render
   * would change the identity of that derivation every render, and with it every `<tr>`
   * `ds/DataTable` memoises.
   */
  const savedRuns = useMemo(
    () => (Array.isArray(savedPayload?.backtests) ? savedPayload.backtests : NO_SAVED_RUNS),
    [savedPayload],
  );

  const handleLoadSavedRun = useCallback((run) => {
    if (!run || typeof run !== "object") return;
    // A saved row is a run that already happened, so nothing about the run lifecycle moves
    // here — but a previous failure's message describes a different run and must go.
    setRunError(null);
    setRunFailure(null);
    setRefusal(null);
    if (run.strategy_id) setSelectedId(String(run.strategy_id));
    if (typeof run.dataset === "string" && run.dataset.trim() !== "") setMarket(run.dataset.trim());
    if (run.initial_capital !== null && run.initial_capital !== undefined) {
      setCapital((previous) => ({ ...previous, "bt-initial-capital": String(run.initial_capital) }));
    }
    /*
     * Through the same mapper a live run goes through, so the result region has one input
     * shape (task 23.2). Only a COMPLETED row is loaded: a row that failed or is still
     * running holds no metrics to render, and the table's own `Inspect` control is disabled
     * for exactly those rows.
     */
    if (run.status === "completed") {
      setResults(mapBacktestExecutionToUI(savedRunAsExecution(run)));
      // A different run is a different detail: the tabs and the trade page both reset, so
      // page 3 of the previous run's trades cannot be on screen over this one's.
      setDetailTab(DETAIL_TABS.TRADES);
      setTradesPage(1);
    }
  }, []);

  /**
   * What stops this configuration from being run, in the order a trader should fix them.
   *
   * These are the ACTION's refusals and not the trigger's `disabled` — see the module
   * docblock. Each one is a sentence, so the refusal that reaches the screen names what to do
   * rather than greying a button.
   */
  const blockers = useMemo(() => {
    const reasons = [];
    if (strategy === null) {
      reasons.push("Choose a strategy first — a backtest runs one saved version of one strategy.");
    }
    if (dateErrors.start !== null) reasons.push(dateErrors.start);
    if (dateErrors.end !== null) reasons.push(dateErrors.end);
    for (const entry of CAPITAL_FORM) {
      const message = capitalErrors[entry.id];
      if (message !== null) reasons.push(message);
    }
    return reasons;
  }, [strategy, dateErrors, capitalErrors]);

  const runBacktest = useCallback(async () => {
    setSubmitted(true);
    setRefusal(null);

    if (blockers.length > 0) {
      setRefusal(blockers[0]);
      return;
    }

    setRunError(null);
    setRunFailure(null);
    // A new run is a new detail: the tabs open on Trades and the trade list starts at page 1,
    // so nothing from the previous run's list can be on screen under this run's heading.
    setDetailTab(DETAIL_TABS.TRADES);
    setTradesPage(1);
    setRunState(RUN_LIFECYCLE.RUNNING);

    /*
     * Task 17.1: the run is one POST to the canonical execute endpoint, which loads the named
     * version's own persisted compiled plan and runs the same DAG_Engine → BacktestEngine
     * (VectorBT) → RiskEngine pipeline the live runtime uses (Requirements 5.1, 5.2). No
     * graph travels from here, and no market: symbol and timeframe are the version's own DATA
     * block (SB-06), and the request model forbids unknown fields, so every key below is one
     * `BacktestExecuteRequest` declares and `_backtest_runtime_for` reads.
     */
    const payload = {
      ...(versionId ? { version_id: versionId } : {}),
      start_date: startDate,
      end_date: endDate,
      initial_capital: parseDecimal(capital["bt-initial-capital"]),
    };
    for (const entry of RATE_FIELDS) {
      payload[entry.body] = parseDecimal(capital[entry.id]) / 100;
    }

    try {
      // Synchronous: the endpoint creates the `strategy_backtests` row, runs, writes the
      // metrics back, and answers with the persisted result. There is no job id to poll.
      const data = await endpoints.strategies.executeBacktest(strategy.id, payload);
      setResults(mapBacktestExecutionToUI(data));
      setRunState(RUN_LIFECYCLE.COMPLETED);

      // Task 17.2: the response's `backtest_id` names a row the endpoint has already created
      // and completed, so the run appears in "Saved Backtest History" by re-reading the list
      // — not by a second save request from here (Requirement 10.1). `refetch` settles its own
      // failure, so a history read that breaks cannot reach the catch below and be reported as
      // a failed run.
      await refetchSaved();
    } catch (err) {
      // The endpoint's own refusal is what the trader is told — the unavailable version, the
      // window that holds too little data, the market the version does not name — rather than
      // an empty panel.
      console.error("Backtest run failed:", err?.message);
      setRunError(runFailureMessage(err));
      setRunFailure(err);
      // Requirement 6.6: the previous run's figures are not this run's, and this run has
      // none. `null` here is what collapses the whole tier-1 row — see `tierOneFigures`.
      setResults(null);
      setRunState(RUN_LIFECYCLE.FAILED);
    }
  }, [blockers, capital, endDate, refetchSaved, startDate, strategy, versionId]);

  /* ── The result region (task 23.2) ──────────────────────────────────────── */

  /**
   * One state for the whole region, and the ONE condition the three tiers exist under.
   * Everything Requirement 6.6 turns on is in `resultRegionState` and `tierOneFigures`.
   */
  const resultState = resultRegionState(runState, results);
  const tierOne = useMemo(() => tierOneFigures(results), [results]);

  /** Both curves, derived once from the one equity series (task 23.3). */
  const curves = useMemo(() => resultCurves(results), [results]);
  const curveState = curves.readable > 0 ? PANEL_STATES.READY : PANEL_STATES.EMPTY;

  /** The trade list, with its one projected column. */
  const tradeRows = useMemo(() => tradeDetailRows(results?.trades), [results]);
  const tradesReported = Array.isArray(results?.trades);

  /**
   * Tier 3's three tabs (Requirement 6.4).
   *
   * `ds/Tabs` is controlled from here because a new run resets the disclosure to Trades —
   * which is the sense in which tier 3 is "collapsed by default": the tab strip is rendered
   * and ordered, and two of the three panels are closed at rest.
   */
  const detailTabs = useMemo(() => [
    {
      id: DETAIL_TABS.TRADES,
      label: labelOf("tradeList"),
      content: tradesReported && tradeRows.length > 0 ? (
        <DataTable
          caption="Simulated trades, in the order the run reported them"
          columns={TRADE_COLUMNS}
          rows={tradeRows}
          getRowId={(row, index) => row?.trade_id ?? index}
          page={tradesPage}
          pageSize={TRADES_PER_PAGE}
          onPageChange={setTradesPage}
          density="compact"
          stickyHeader
        />
      ) : (
        <EmptyState
          headline={tradesReported
            ? "This run closed no trades"
            : "This run reported no trade list"}
          body={tradesReported
            ? "The strategy never triggered over this window, which is a result rather than "
              + "a failure. The six figures above describe that run."
            : `${reasonOf("tradeList")} The engine returns the list with the run, so there is `
              + "nothing to re-read it from."}
          action={{ label: RETRY_LABEL, onClick: runBacktest }}
        />
      ),
    },
    {
      id: DETAIL_TABS.MONTHLY,
      label: "Monthly returns",
      /*
       * Requirement 19.3, not a placeholder. `BacktestRuntime._calculate_performance_metrics`
       * publishes `monthly_returns` as a bare list of numbers — no months — and resamples it
       * off a positional index rather than the equity curve's own timestamps, so the payload
       * contains nothing that could be dated. A chart with month labels this page invented is
       * the one thing that must not be here, so the state says so instead.
       */
      content: (
        <Panel
          state={PANEL_STATES.UNAVAILABLE}
          unavailable={{
            reason: "The engine reports no dated monthly returns for a backtest: the figures "
              + "it publishes carry no months, so there is no honest way to label a month "
              + "against one. The equity curve above covers the same window point by point.",
          }}
          data-region="monthly-returns"
        />
      ),
    },
    {
      id: DETAIL_TABS.EXTENDED,
      label: "Extended statistics",
      content: (
        <div className="grid min-w-0 grid-cols-3 gap-4">
          {EXTENDED_STATISTICS.map((entry) => (
            <Metric
              key={entry.key}
              tier={3}
              label={entry.field ? labelOf(entry.field) : entry.label}
              value={fromNullable(
                results?.[entry.key],
                entry.field ? reasonOf(entry.field) : entry.reason,
              )}
              format={entry.format}
              precision={entry.precision}
              unit={entry.unit}
              hint={entry.hint}
              data-region={entry.field ?? entry.key}
            />
          ))}
        </div>
      ),
    },
  ], [results, tradeRows, tradesReported, tradesPage, runBacktest]);

  /**
   * The saved rows, with the two figures projected to the text a trader reads.
   *
   * Projected rather than formatted by the table: `12.5` under a "Return %" header is not
   * what a trader expects to read, and an unreadable figure has to reach `ds/DataTable`'s
   * not-available marker rather than an empty cell (Requirement 14.5).
   */
  const savedRunRows = useMemo(
    () => savedRuns.map((run) => ({
      ...run,
      strategy: savedRunName(run),
      returnText: percentText(run?.total_return_pct),
      sharpeText: decimalText(run?.sharpe_ratio, 2),
    })),
    [savedRuns],
  );

  /** The saved table's columns plus its one action, which needs this page's handler. */
  const savedRunColumns = useMemo(
    () => [
      ...SAVED_RUN_COLUMNS,
      {
        key: "inspect",
        header: "Action",
        align: "numeric",
        render: ({ row }) => (
          <CommandButton
            intent="ghost"
            disabled={row?.status !== "completed"}
            disabledReason={row?.status === "completed"
              ? undefined
              : "This run did not complete, so it holds no result to inspect."}
            onClick={() => handleLoadSavedRun(row)}
          >
            Inspect
          </CommandButton>
        ),
      },
    ],
    [handleLoadSavedRun],
  );

  /*
   * The configuration cannot be assembled at all when the listing failed AND nothing else
   * named a strategy. That is the one failure this page renders instead of a region, and it
   * replaces the four panels only — never the results region, whose rows read successfully.
   */
  const configurationFailed = strategy === null && listFailed;

  const versionLabel = currentVersion === null
    ? null
    : String(currentVersion.version ?? currentVersion.id ?? "");

  return (
    <div className="flex min-w-0 flex-col gap-4 overflow-y-auto bg-surface-canvas p-5 text-content-primary">
      <PageHeader
        title="Backtester"
        subtitle="One stored version, one historical window"
        // §4.2's third environment: every figure this page produces is simulated on
        // historical data. The badge is the route's claim, stated once.
        environment="BACKTEST"
        actions={(
          <CommandButton intent="secondary" icon={ArrowLeft} onClick={onBack}>
            Back to strategies
          </CommandButton>
        )}
      />

      <div className="grid min-w-0 grid-cols-3 gap-4">

        {/* ═══ THE CONFIGURATION FLOW — Requirement 6.1's four panels, in order ═══ */}
        {configurationFailed ? (
          <ErrorState
            error={listError}
            context="backtest"
            onRetry={refetchList}
            data-region="configuration-error"
          />
        ) : (
          <div className="flex min-w-0 flex-col gap-4" data-region="backtest-configuration">

            {/* ── 1. Strategy ─────────────────────────────────────────────────── */}
            <Panel
              title="Strategy"
              // A strategy that is already named is a configurable panel whatever the listing
              // did: the empty and failed arms describe the SELECTOR's options, and neither is
              // true of a page that was handed a strategy or deep-linked to one. The listing's
              // own failure is reported inside, where it is about the thing it is about.
              state={strategy !== null ? PANEL_STATES.READY : listState}
              loading={{ kind: "skeleton-metric", rows: 1, columns: 2 }}
              empty={{
                headline: "No strategies to backtest",
                body: "A backtest runs one saved version of one strategy, and this account has "
                  + "none yet.",
                action: { label: "Open the strategy builder", to: "/app/builder" },
              }}
              error={{ error: listError, context: "backtest", onRetry: refetchList }}
              data-region="strategy"
            >
              <div className="flex min-w-0 flex-col gap-3">
                <Field
                  id="bt-strategy"
                  label="Strategy"
                  required
                  placeholder="Choose a strategy…"
                  hint="Owned strategies, as the server lists them."
                  value={selectedId}
                  options={strategyOptions}
                  onChange={(event) => {
                    setSelectedId(event.target.value);
                    // A different strategy is a different run: the previous result describes
                    // the previous version and must not stay on screen beside this one.
                    setResults(null);
                    setRunError(null);
                    setRefusal(null);
                    setRunState(RUN_LIFECYCLE.IDLE);
                  }}
                />

                {/* The version is PINNED, not chosen: the run executes the version's own
                    persisted compiled plan, so what matters is that the trader can read which
                    one that is. */}
                <Metric
                  label="Version this run executes"
                  tier={3}
                  format="raw"
                  value={fromNullable(
                    versionState === PANEL_STATES.READY ? versionLabel : null,
                    selectedId === ""
                      ? "No strategy is selected yet."
                      : versionState === PANEL_STATES.LOADING
                        ? "The version history is still being read."
                        : "This strategy has no version marked current, so there is nothing to "
                          + "backtest. Save a version first.",
                  )}
                  hint={"The immutable version whose stored plan the run executes. Only the "
                    + "version the server marks current is used."}
                  data-region="pinnedVersion"
                />

                {/* The listing failed but this strategy is known, so the panel is usable and
                    the only thing missing is the ability to switch. Said here rather than
                    swallowed: a one-entry selector with no explanation reads as an account
                    with one strategy. */}
                {listFailed ? (
                  <Alert
                    severity="warning"
                    title="The other strategies could not be listed"
                    action={{ label: "Try again", onClick: refetchList }}
                    data-testid="backtester-list-error"
                  >
                    This selector is showing only the strategy already named on this page. Every
                    other one is unknown rather than absent.
                  </Alert>
                ) : null}

                {/* A deep link to a strategy the listing does not carry is the only path that
                    reads one by id, so this is the only place that failure can appear. */}
                {needsDetail && detailState === PANEL_STATES.ERROR ? (
                  <Alert
                    severity="error"
                    title="That strategy could not be read"
                    data-testid="backtester-strategy-error"
                  >
                    {runFailureMessage(detailError)}
                  </Alert>
                ) : null}
              </div>
            </Panel>

            {/* ── 2. Market & data ────────────────────────────────────────────── */}
            <Panel title="Market & data" data-region="market">
              <div className="flex min-w-0 flex-col gap-3">
                <p className="text-small text-content-secondary" data-market-scope="data-check">
                  These two scope the data check below. They are not sent with the run: the
                  execute endpoint accepts no market and no interval, because both are the
                  version&apos;s own DATA block.
                </p>

                <div className="flex min-w-0 flex-col gap-1">
                  <label
                    htmlFor="bt-market"
                    className="text-small font-medium text-content-secondary"
                  >
                    Market
                  </label>
                  <AssetSelector
                    controlProps={{ id: "bt-market", name: "symbol" }}
                    spec={{ label: "Market", example: "BTC/USDT" }}
                    value={market}
                    onChange={setMarket}
                  />
                </div>

                <div className="flex min-w-0 flex-col gap-1">
                  <label
                    htmlFor="bt-interval"
                    className="text-small font-medium text-content-secondary"
                  >
                    Bar interval
                  </label>
                  <TimeframeSelector
                    controlProps={{ id: "bt-interval", name: "timeframe" }}
                    spec={{ label: "Bar interval", example: "15m" }}
                    value={barInterval}
                    onChange={setBarInterval}
                  />
                </div>
              </div>
            </Panel>

            {/* ── 3. Period ───────────────────────────────────────────────────── */}
            <Panel title="Period" data-region="period">
              <div className="flex min-w-0 flex-col gap-3">
                <div className="grid min-w-0 grid-cols-2 gap-4">
                  <Field
                    id="bt-start-date"
                    label="Start date"
                    type="date"
                    required
                    value={startDate}
                    submitted={submitted}
                    invalid={dateErrors.start !== null}
                    error={dateErrors.start ?? undefined}
                    onChange={(event) => setStartDate(event.target.value)}
                  />
                  <Field
                    id="bt-end-date"
                    label="End date"
                    type="date"
                    required
                    value={endDate}
                    submitted={submitted}
                    invalid={dateErrors.end !== null}
                    error={dateErrors.end ?? undefined}
                    onChange={(event) => setEndDate(event.target.value)}
                  />
                </div>

                {/* Nothing at all until a market and an interval have been chosen: the check
                    has not been issued, and there is no honest thing to say about a window
                    nobody has asked about. */}
                <DataAvailabilityNote
                  state={availabilityState}
                  report={availabilityReport}
                  error={availabilityError}
                  onRetry={refetchAvailability}
                  described={describedWindow}
                />
              </div>
            </Panel>

            {/* ── 4. Capital & risk, with the advanced set collapsed (Req 15.6) ── */}
            <Panel title="Capital & risk" data-region="capital">
              <div className="flex min-w-0 flex-col gap-3">
                <div className="grid min-w-0 grid-cols-2 gap-4">
                  {CAPITAL_FORM.filter((entry) => entry.advanced !== true).map((entry) => (
                    <Field
                      key={entry.id}
                      id={entry.id}
                      label={entry.label}
                      type="decimal"
                      unit={entry.unit}
                      hint={entry.hint}
                      required
                      value={capital[entry.id]}
                      submitted={submitted}
                      invalid={capitalErrors[entry.id] !== null}
                      error={capitalErrors[entry.id] ?? undefined}
                      onChange={(event) => setCapital((previous) => ({
                        ...previous,
                        [entry.id]: event.target.value,
                      }))}
                    />
                  ))}
                </div>

                <Accordion
                  title="Advanced"
                  summary="Execution costs the simulator applies to every fill"
                  fields={ADVANCED_FIELD_IDS}
                  id="bt-advanced"
                >
                  {CAPITAL_FORM.filter((entry) => entry.advanced === true).map((entry) => (
                    <Field
                      key={entry.id}
                      id={entry.id}
                      label={entry.label}
                      type="decimal"
                      unit={entry.unit}
                      hint={entry.hint}
                      required
                      value={capital[entry.id]}
                      submitted={submitted}
                      invalid={capitalErrors[entry.id] !== null}
                      error={capitalErrors[entry.id] ?? undefined}
                      onChange={(event) => setCapital((previous) => ({
                        ...previous,
                        [entry.id]: event.target.value,
                      }))}
                    />
                  ))}
                </Accordion>
              </div>
            </Panel>

            {/* ── THE RUN ACTION (Requirements 6.5, 15.3) ─────────────────────── */}
            <div className="flex min-w-0 flex-col gap-2" data-region="run">
              <RunBacktestControl runState={runState} onRun={runBacktest} />

              {/* The action's own refusal. Rendered instead of disabling the trigger, so the
                  trader reads what to change rather than guessing at a grey button. */}
              {refusal === null ? null : (
                <Alert
                  severity="guidance"
                  title="This configuration cannot be run yet"
                  data-testid="backtester-run-refusal"
                >
                  {refusal}
                </Alert>
              )}

              {runError === null ? null : (
                <Alert
                  severity="error"
                  title="The backtest could not be run"
                  data-testid="backtester-run-error"
                >
                  {runError}
                </Alert>
              )}
            </div>
          </div>
        )}

        {/* ═══ THE RESULT REGION — §7.4's THREE TIERS (task 23.2) ══════════════════
            One branch, one predicate. The three tier containers exist ONLY in the `ready`
            arm, and `ready` is reachable only when `tierOneFigures` answered — which is
            Requirement 6.6 made structural rather than remembered: a failed run renders no
            tier-1 row at all, not a row of zeros and not a row of em-dashes.

            Every other state is ONE `ds/Panel` over the whole region: `loading` while the
            run is in flight (§7.4), `ErrorState` on failure, and `EmptyState` both for "no
            run yet" and for a run that answered with nothing renderable. */}
        <div className="col-span-2 flex min-w-0 flex-col gap-3" data-region="backtest-results">
          {resultState === PANEL_STATES.READY && tierOne !== null ? (
            <>
              {/* ── TIER 1 — six figures, one row (Requirement 6.2) ───────────────
                  Walked from `pageHierarchy`'s tier-1 list, so a seventh figure cannot
                  appear without being declared and the order is the declaration's.
                  `Metric tier={1}` exists nowhere else on this page. */}
              <Panel
                title="Result"
                money
                environment="BACKTEST"
                actions={results?.backtest_id ? (
                  <span
                    data-testid="backtester-persisted-run"
                    className="font-mono text-micro tracking-wide text-content-secondary"
                  >
                    {`SAVED · ${String(results.backtest_id).slice(0, 8)}`}
                  </span>
                ) : null}
                data-region="tier-1"
              >
                {/* Six equal-weight slots in ONE row: `flex-1` from a zero basis, not
                    `grid-cols-6` — the built stylesheet carries bare `grid-cols-1…4` plus
                    `lg:grid-cols-6`, so a bare `grid-cols-6` compiles to nothing and renders
                    as though the attribute were absent (`dead-tailwind`, design.md §1.2). */}
                <div
                  {...{ [TIER_PAGE_ATTRIBUTE]: PAGES.BACKTESTER, [TIER_ATTRIBUTE]: 1 }}
                  className="flex min-w-0 items-start gap-4"
                >
                  {TIER_ONE.map(({ key, label }) => (
                    <Metric
                      key={key}
                      tier={1}
                      label={label}
                      value={tierOne[key]}
                      format={TIER_ONE_FIGURE[key]?.format}
                      precision={TIER_ONE_FIGURE[key]?.precision}
                      hint={TIER_ONE_FIGURE[key]?.hint}
                      className="flex-1"
                      data-region={key}
                    />
                  ))}
                </div>
              </Panel>

              {/* ── TIER 2 — the two curves (Requirement 6.3) ─────────────────────
                  Side by side in one container, below tier 1, both drawn from the ONE
                  derived series so they cannot disagree about where the gaps are. */}
              <div
                {...{ [TIER_PAGE_ATTRIBUTE]: PAGES.BACKTESTER, [TIER_ATTRIBUTE]: 2 }}
                className="grid min-w-0 grid-cols-2 gap-4"
              >
                <Panel
                  title={labelOf("equityCurve")}
                  money
                  environment="BACKTEST"
                  level={3}
                  state={curveState}
                  empty={{ ...CURVE_EMPTY, action: { label: RETRY_LABEL, onClick: runBacktest } }}
                  data-region="equityCurve"
                >
                  <Suspense fallback={<LoadingState kind="skeleton-chart" label="Loading the equity curve" />}>
                    <Chart
                      kind="area"
                      data={curves.rows}
                      xAxis={{ key: "point", label: "Point in the run", format: "number" }}
                      yAxis={{ label: "Equity (quote currency)", format: "number" }}
                      series={[{ key: "equity", name: "Equity", token: "brand" }]}
                      emptyMessage="This run produced no equity series"
                    />
                  </Suspense>
                </Panel>

                <Panel
                  title={labelOf("drawdownCurve")}
                  money
                  environment="BACKTEST"
                  level={3}
                  state={curveState}
                  empty={{ ...CURVE_EMPTY, action: { label: RETRY_LABEL, onClick: runBacktest } }}
                  data-region="drawdownCurve"
                >
                  <div className="flex min-w-0 flex-col gap-2">
                    {/* An incomplete curve, labelled incomplete. `lib/drawdownSeries.js`
                        derives `null` — never `0` — for an equity point that could not be
                        read, which `ds/Chart` draws as a gap; this is the same fact in
                        words, because a gap alone does not say the depths after it are
                        measured from a lower peak. */}
                    {curves.unreadable === 0 ? null : (
                      <Alert
                        severity="warning"
                        title={`${curves.unreadable} of ${curves.rows.length} equity points could not be read`}
                        data-testid="backtester-drawdown-incomplete"
                      >
                        {REASON_UNREADABLE_EQUITY}
                      </Alert>
                    )}
                    <Suspense fallback={<LoadingState kind="skeleton-chart" label="Loading the drawdown curve" />}>
                      <Chart
                        kind="area"
                        data={curves.rows}
                        xAxis={{ key: "point", label: "Point in the run", format: "number" }}
                        // Absolute, in the equity curve's own units — the derivation is
                        // `peak − current`, not a percentage. Tier 1's max drawdown is the
                        // engine's `max_drawdown_pct` and stays the only percentage on
                        // screen, so the two cannot contradict each other.
                        yAxis={{ label: "Drawdown from peak (quote currency)", format: "number" }}
                        series={[{ key: "drawdown", name: "Drawdown from peak", token: "loss" }]}
                        emptyMessage="No drawdown curve can be drawn without an equity series"
                      />
                    </Suspense>
                  </div>
                </Panel>
              </div>

              {/* ── TIER 3 — the detail, behind Tabs (Requirement 6.4) ────────────
                  Collapsed is a disclosure state, not an absence: the tab strip is rendered
                  and ordered — which is what Property 4 sees — and two of the three panels
                  are closed at rest. A new run reopens Trades. */}
              <div
                {...{ [TIER_PAGE_ATTRIBUTE]: PAGES.BACKTESTER, [TIER_ATTRIBUTE]: 3 }}
                className="flex min-w-0 flex-col gap-3"
              >
                <Panel
                  title="Detail"
                  money
                  environment="BACKTEST"
                  level={3}
                  data-region="tier-3"
                >
                  <Tabs
                    label="Backtest detail"
                    items={detailTabs}
                    value={detailTab}
                    onChange={setDetailTab}
                    data-testid="backtester-detail-tabs"
                  />
                </Panel>
              </div>
            </>
          ) : (
            <Panel
              title="Result"
              money
              environment="BACKTEST"
              state={resultState}
              // §7.4: the loading state covers the WHOLE region, at the shape of the row it
              // replaces — the six figures — rather than one skeleton per tier.
              loading={{
                kind: "skeleton-metric",
                rows: 2,
                columns: 3,
                label: "Running the backtest",
              }}
              empty={resultEmptyCopy(runState, runBacktest)}
              // Requirement 6.6's `ErrorState`, and the ERROR — never the message string,
              // which `ds/ErrorState` does not accept by design (Requirements 14.3, 14.4).
              // The server's own sentence is beside the trigger, where the fix is.
              error={{ error: runFailure, context: "backtest", onRetry: runBacktest }}
              data-region="result"
            />
          )}

          {/* ── The saved runs the backend holds (task 17.2, Requirement 10.1) ─────
              Not one of the three tiers and not a declared field: these are other runs,
              not this run's result, so they carry no `data-page-tier` and no
              `Metric tier`. Rendered only when there are rows — an empty history is not a
              statement worth a heading. */}
          {savedRunRows.length === 0 ? null : (
            <Panel
              title="Saved Backtest History"
              money
              environment="BACKTEST"
              state={savedState === PANEL_STATES.REFRESHING
                ? PANEL_STATES.REFRESHING
                : PANEL_STATES.READY}
              data-region="saved-runs"
            >
              <DataTable
                caption="Backtest runs this account has saved, most recent first"
                columns={savedRunColumns}
                rows={savedRunRows}
                getRowId={(row, index) => row?.id ?? index}
                pageSize={0}
                density="compact"
              />
            </Panel>
          )}

          {savedState === PANEL_STATES.ERROR ? (
            <ErrorState
              error={savedError}
              context="backtest"
              onRetry={refetchSaved}
              compact
              data-region="saved-runs-error"
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
