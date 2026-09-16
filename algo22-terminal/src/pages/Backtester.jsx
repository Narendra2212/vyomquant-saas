/**
 * ═══════════════════════════════════════════════════════════════════════════
 * pages/Backtester — configure one run of one stored version (`/app/backtest`)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 23.1: the CONFIGURATION FLOW and the RUN ACTION. design.md
 * §7.4, §11.1, §11.2. Requirements 6.1, 6.5, 15.1, 15.2, 15.3, 15.6, 19.3.
 *
 * The results region below the configuration column is task 23.2's and is deliberately
 * untouched here — it still reads `mapBacktestExecutionToUI`'s flat object, still renders the
 * saved-history table, and still carries the legacy `C.` treatment 23.2 clears. What changed
 * is everything above and beside it.
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

import { useCallback, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import {
  ResponsiveContainer, AreaChart, Area, CartesianGrid, XAxis, YAxis, Tooltip
} from "recharts";

import { endpoints } from "../api";
import { dataQualityApi } from "../api/modules/dataQuality";
import { mapBacktestExecutionToUI } from "../api/modules/strategies";
import { AssetSelector } from "../components/builder/AssetSelector";
import { TimeframeSelector } from "../components/builder/TimeframeSelector";
import { Accordion, partitionAdvanced } from "../components/ds/Accordion";
import { Alert } from "../components/ds/Alert";
import { CommandButton } from "../components/ds/CommandButton";
import { ErrorState } from "../components/ds/ErrorState";
import { Field } from "../components/ds/Field";
import { Metric } from "../components/ds/Metric";
import { PageHeader } from "../components/ds/PageHeader";
import { Panel } from "../components/ds/Panel";
import { C, PanelTitle, CustomTooltip } from "../components/ui-legacy/primitives";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { fromNullable } from "../design/reported";
import { PANEL_STATES, usePanelState } from "../hooks/usePanelState";

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
  const options = useMemo(
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
  const [refusal, setRefusal] = useState(null);
  const [results, setResults] = useState(null);

  /* ── The results region's own reads (task 23.2 owns what they render) ────── */
  const [tradePage, setTradePage] = useState(0);
  const tradesPerPage = 20;

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
  const savedRuns = Array.isArray(savedPayload?.backtests) ? savedPayload.backtests : [];

  const handleLoadSavedRun = (run) => {
    if (!run || typeof run !== "object") return;
    // A saved row is a run that already happened, so nothing about the run lifecycle moves
    // here — but a previous failure's message describes a different run and must go.
    setRunError(null);
    setRefusal(null);
    if (run.strategy_id) setSelectedId(String(run.strategy_id));
    if (typeof run.dataset === "string" && run.dataset.trim() !== "") setMarket(run.dataset.trim());
    if (run.initial_capital !== null && run.initial_capital !== undefined) {
      setCapital((previous) => ({ ...previous, "bt-initial-capital": String(run.initial_capital) }));
    }
    if (run.status === "completed" && run.equity_curve) {
      setResults({
        equity: run.equity_curve,
        total_return_pct: run.total_return_pct,
        win_rate_pct: run.win_rate,
        max_drawdown_pct: run.max_drawdown,
        sharpe_ratio: run.sharpe_ratio,
        sortino_ratio: run.sortino_ratio,
        profit_factor: run.profit_factor,
        total_trades: run.total_trades,
      });
    }
  };

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
      setResults(null);
      setRunState(RUN_LIFECYCLE.FAILED);
    }
  }, [blockers, capital, endDate, refetchSaved, startDate, strategy, versionId]);

  const runInFlight = isRunInFlight(runState);

  /* ── The results region (task 23.2) ─────────────────────────────────────── */

  // Memoised – prevents recharts reconciliation unless results actually change
  const equityData = useMemo(
    () => (results?.equity || []).map((row, i) => ({
      timestamp: row.timestamp ?? row.time ?? i,
      equity: Number(row.equity ?? row.value ?? 0),
    })),
    [results]
  );

  // Stable reference – statItems never changes between renders
  const statItems = useMemo(() => [
    { key: "total_return_pct", label: "Total Return %" },
    { key: "win_rate_pct", label: "Win Rate %" },
    { key: "max_drawdown_pct", label: "Max Drawdown %" },
    { key: "total_trades", label: "Total Trades" },
    { key: "profit_factor", label: "Profit Factor" },
    { key: "sharpe_ratio", label: "Sharpe Ratio" },
    { key: "sortino_ratio", label: "Sortino Ratio" },
    { key: "calmar_ratio", label: "Calmar Ratio" },
  ], []);

  /*
   * The configuration cannot be assembled at all when the listing failed AND nothing else
   * named a strategy. That is the one failure this page renders instead of a region, and it
   * replaces the four panels only — never the results region, whose rows read successfully.
   */
  const configurationFailed = strategy === null
    && (listState === PANEL_STATES.ERROR
      || listState === PANEL_STATES.UNAUTHORISED
      || listState === PANEL_STATES.UNAVAILABLE);

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
              state={listState === PANEL_STATES.EMPTY && strategy !== null
                ? PANEL_STATES.READY
                : listState}
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
                  options={options}
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

        {/* ═══ THE RESULTS REGION — task 23.2 rebuilds everything below ═══════════
            Left as it stands, deliberately: 23.1 is the configuration flow and the run
            action. It still reads `mapBacktestExecutionToUI`'s flat object and still carries
            the legacy `C.` treatment and the two `rgba(255,255,255,0.05)` row rules that
            23.2's budget entries account for. */}
        <div className="col-span-2 flex min-w-0 flex-col gap-3" data-region="backtest-results">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
            {statItems.map((s) => (
              <Card key={s.key} cls="p-3">
                <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase", marginBottom: 4 }}>{s.label}</div>
                <div style={{ color: C.t1, fontSize: 18, fontWeight: 900, fontFamily: "monospace" }}>
                  {results?.[s.key] === null || results?.[s.key] === undefined ? "-" : String(results[s.key])}
                </div>
              </Card>
            ))}
          </div>
          <Card className="p-4 flex-1 flex flex-col">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <PanelTitle
                title="Equity Curve"
                sub={results ? `Simulated performance, ${startDate} to ${endDate}` : "Awaiting backtest execution..."}
              />
              {results?.backtest_id && (
                <span
                  data-testid="backtester-persisted-run"
                  style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", letterSpacing: 1 }}
                >
                  SAVED · {String(results.backtest_id).slice(0, 8)}
                </span>
              )}
            </div>
            <div style={{ width: "100%", height: 280, minHeight: 280, position: "relative" }}>
              <ResponsiveContainer width="100%" height={280} minWidth={100} minHeight={280}>
                <AreaChart data={equityData}>
                  <defs>
                    <linearGradient id="btEq" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={C.green} stopOpacity={0.22} />
                      <stop offset="95%" stopColor={C.green} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                  <XAxis dataKey="timestamp" hide />
                  <YAxis domain={["auto", "auto"]} hide />
                  <Tooltip content={<CustomTooltip prefix="$" />} />
                  <Area dataKey="equity" stroke={C.green} strokeWidth={2} fill="url(#btEq)" dot={false} />
                </AreaChart>
              </ResponsiveContainer>
              {!results && !runInFlight && (
                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: C.t3, fontFamily: "monospace", fontSize: 12 }}>
                  Configure the run and select Run backtest to visualise a result.
                </div>
              )}
              {runInFlight && (
                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: C.cyan, fontFamily: "monospace", fontSize: 12, background: "rgba(1,6,8,0.5)", backdropFilter: "blur(2px)" }}>
                  VectorBT Engine is crunching historical data...
                </div>
              )}
            </div>
          </Card>

          {/* Saved Backtests History */}
          {savedRuns.length > 0 && (
            <Card className="p-4">
              <PanelTitle
                title="Saved Backtest History"
                sub={savedState === PANEL_STATES.REFRESHING
                  ? "Refreshing from the database..."
                  : "Reproducible backtest simulation runs"}
              />
              <div style={{ overflowX: "auto", marginTop: 8 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, fontFamily: "monospace" }}>
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${C.border}`, color: C.t3, textAlign: "left" }}>
                      <th style={{ padding: "6px 8px" }}>Created At</th>
                      <th style={{ padding: "6px 8px" }}>Strategy</th>
                      <th style={{ padding: "6px 8px" }}>Version</th>
                      <th style={{ padding: "6px 8px" }}>Pair</th>
                      <th style={{ padding: "6px 8px" }}>Status</th>
                      <th style={{ padding: "6px 8px" }}>Return %</th>
                      <th style={{ padding: "6px 8px" }}>Win Rate</th>
                      <th style={{ padding: "6px 8px" }}>Max DD</th>
                      <th style={{ padding: "6px 8px", textAlign: "right" }}>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {savedRuns.map(run => (
                      <tr key={run.id} style={{ borderBottom: `1px solid rgba(255,255,255,0.05)` }} className="hover:bg-white/[0.02]">
                        <td style={{ padding: "6px 8px", color: C.t2 }}>{new Date(run.created_at).toLocaleDateString()}</td>
                        <td style={{ padding: "6px 8px", color: C.t1, fontWeight: 700 }}>
                          {run.blueprint?.name || `Strategy ${run.strategy_id?.slice(0, 8)}`}
                        </td>
                        <td style={{ padding: "6px 8px", color: C.t2 }}>{run.version}</td>
                        <td style={{ padding: "6px 8px", color: C.cyan }}>{run.dataset}</td>
                        <td style={{ padding: "6px 8px", color: run.status === "completed" ? C.green : C.t2 }}>
                          {run.status}
                        </td>
                        <td style={{ padding: "6px 8px", color: Number(run.total_return_pct ?? 0) >= 0 ? C.green : C.red }}>
                          {run.total_return_pct ? `${run.total_return_pct.toFixed(2)}%` : "-"}
                        </td>
                        <td style={{ padding: "6px 8px", color: C.t1 }}>{run.win_rate ? `${(run.win_rate * 100).toFixed(1)}%` : "-"}</td>
                        <td style={{ padding: "6px 8px", color: C.red }}>{run.max_drawdown ? `${(run.max_drawdown * 100).toFixed(2)}%` : "-"}</td>
                        <td style={{ padding: "6px 8px", textAlign: "right" }}>
                          <Button variant="ghost" size="xs" onClick={() => handleLoadSavedRun(run)} disabled={run.status !== "completed"}>
                            Inspect
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          {savedState === PANEL_STATES.ERROR && (
            <Card className="p-4">
              <ErrorState error={savedError} context="backtest" onRetry={refetchSaved} compact />
            </Card>
          )}

          {/* Trade Table */}
          {results && results.total_trades > 0 && (
            <Card className="p-4">
              <PanelTitle title="Trade History" sub={`${results.total_trades} simulated trades`} />
              {results.trades && results.trades.length > 0 ? (
                <>
                  <div style={{ overflowX: "auto", marginTop: 8 }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, fontFamily: "monospace" }}>
                      <thead>
                        <tr style={{ borderBottom: `1px solid ${C.border}`, color: C.t3, textAlign: "left" }}>
                          <th style={{ padding: "6px 8px" }}>Trade #</th>
                          <th style={{ padding: "6px 8px" }}>Entry Time</th>
                          <th style={{ padding: "6px 8px" }}>Entry Price</th>
                          <th style={{ padding: "6px 8px" }}>Exit Time</th>
                          <th style={{ padding: "6px 8px" }}>Exit Price</th>
                          <th style={{ padding: "6px 8px" }}>Side</th>
                          <th style={{ padding: "6px 8px" }}>Quantity</th>
                          <th style={{ padding: "6px 8px" }}>Gross P&L</th>
                          <th style={{ padding: "6px 8px" }}>Fees</th>
                          <th style={{ padding: "6px 8px" }}>Net P&L</th>
                          <th style={{ padding: "6px 8px" }}>Return %</th>
                          <th style={{ padding: "6px 8px" }}>Duration</th>
                        </tr>
                      </thead>
                      <tbody>
                        {results.trades
                          .slice(tradePage * tradesPerPage, (tradePage + 1) * tradesPerPage)
                          .map((trade, idx) => (
                          <tr key={idx} style={{ borderBottom: `1px solid rgba(255,255,255,0.05)` }} className="hover:bg-white/[0.02]">
                            <td style={{ padding: "6px 8px", color: C.t2 }}>#{trade.trade_id}</td>
                            <td style={{ padding: "6px 8px", color: C.t2 }}>
                              {trade.entry_time ? new Date(trade.entry_time).toLocaleString() : "-"}
                            </td>
                            <td style={{ padding: "6px 8px", color: C.t1 }}>
                              ${trade.entry_price?.toFixed(2) || "-"}
                            </td>
                            <td style={{ padding: "6px 8px", color: C.t2 }}>
                              {trade.exit_time ? new Date(trade.exit_time).toLocaleString() : "-"}
                            </td>
                            <td style={{ padding: "6px 8px", color: C.t1 }}>
                              ${trade.exit_price?.toFixed(2) || "-"}
                            </td>
                            <td style={{ padding: "6px 8px", color: trade.side === "BUY" ? C.green : C.red }}>
                              {trade.side || "-"}
                            </td>
                            <td style={{ padding: "6px 8px", color: C.t1 }}>
                              {trade.quantity?.toFixed(4) || "-"}
                            </td>
                            <td style={{ padding: "6px 8px", color: trade.gross_pnl >= 0 ? C.green : C.red }}>
                              ${trade.gross_pnl?.toFixed(2) || "-"}
                            </td>
                            <td style={{ padding: "6px 8px", color: C.red }}>
                              ${trade.fees?.toFixed(2) || "-"}
                            </td>
                            <td style={{ padding: "6px 8px", color: trade.net_pnl >= 0 ? C.green : C.red }}>
                              ${trade.net_pnl?.toFixed(2) || "-"}
                            </td>
                            <td style={{ padding: "6px 8px", color: trade.return_pct >= 0 ? C.green : C.red }}>
                              {trade.return_pct ? `${trade.return_pct.toFixed(2)}%` : "-"}
                            </td>
                            <td style={{ padding: "6px 8px", color: C.t2 }}>
                              {trade.duration ? `${trade.duration}s` : "-"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {/* Pagination */}
                  {results.trades.length > tradesPerPage && (
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 12 }}>
                      <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
                        Showing {tradePage * tradesPerPage + 1} to {Math.min((tradePage + 1) * tradesPerPage, results.trades.length)} of {results.trades.length} trades
                      </span>
                      <div style={{ display: "flex", gap: 8 }}>
                        <Button
                          variant="ghost"
                          size="xs"
                          onClick={() => setTradePage(Math.max(0, tradePage - 1))}
                          disabled={tradePage === 0}
                        >
                          Previous
                        </Button>
                        <span style={{ color: C.t1, fontSize: 10, fontFamily: "monospace", alignSelf: "center" }}>
                          Page {tradePage + 1} of {Math.ceil(results.trades.length / tradesPerPage)}
                        </span>
                        <Button
                          variant="ghost"
                          size="xs"
                          onClick={() => setTradePage(Math.min(Math.ceil(results.trades.length / tradesPerPage) - 1, tradePage + 1))}
                          disabled={tradePage >= Math.ceil(results.trades.length / tradesPerPage) - 1}
                        >
                          Next
                        </Button>
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", marginTop: 8 }}>
                  Trade details not available for this backtest (VectorBT trade extraction may require configuration)
                </div>
              )}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
