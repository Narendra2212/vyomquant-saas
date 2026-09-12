/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/StrategyStatus — what a strategy is doing, and whether anything is wrong
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.6. design.md §5.1, §5.3, §7.2.
 * Requirements 1.2, 1.4, 1.5 (and 1.8/1.9 of the trading-lifecycle spec, via
 * `lib/strategyHealth.js`).
 *
 * WHAT IT ABSORBS
 * --------------
 *   `LiveStatus` (`ui-legacy/primitives.jsx`) — a boolean `isLive`, with `OFFLINE` in
 *       loss red as the false arm and a permanent 2s opacity `pulse` plus a
 *       `box-shadow` halo on the true arm. A strategy that is paused, draft or failed
 *       is all one thing to it: not live.
 *   `LiveStatusV2` (same file) — five configs (`running` `stopped` `loading` `paused`
 *       `connecting`), a `bgPulse` halo, a `blink` animation, and `stopped` in the same
 *       loss red as a failure. It defaults an unrecognised status to `stopped`.
 *   `_normalizeStatus` (`pages/Strategies.jsx`) — the six-state spelling collapse. It
 *       is transcribed here (see {@link normaliseStrategyStatus}) so the page can drop
 *       its copy at task 19.x.
 *
 * `stopped` AND `failed` ARE NOT THE SAME COLOUR HERE
 * -------------------------------------------------
 * Both predecessors painted `stopped` in the loss hue. A stopped strategy is a resting
 * state somebody asked for; a failed one needs attention. `design/semantic.js` already
 * separates them — `stopped` is in the `neutral` group and `failed` in `error` — so
 * this component gets that right by delegating rather than by deciding.
 *
 * HEALTH IS IMPORTED, NEVER RECOMPUTED, AND NEVER DEFAULTED TO HEALTHY
 * ------------------------------------------------------------------
 * `computeStrategyHealth` comes from `lib/strategyHealth.js`. That module is the whole
 * reason this component can be trusted: `GET /api/strategies` reports no `health`
 * field at all, `Strategies.jsx` used to write `health: row.health ?? "healthy"`, and
 * so every strategy — including one whose only deployment had failed — reported
 * healthy. The fix was to compute the indicator from the two records that are allowed
 * to determine it and to report `undetermined` when neither exists.
 *
 * This component must not undo that, so it does two things:
 *
 *   1. It never computes health itself. Pass a row and `computeStrategyHealth` reads
 *      it; pass a string and it is taken as that function's already-computed output.
 *   2. A string it does not recognise is `undetermined`, NOT healthy. `Dashboard.jsx`
 *      currently writes `health: s.health || "idle"` and, on a start action,
 *      `health: newStatus === "active" ? "healthy" : "idle"` — a health verdict
 *      invented by the client from a status it just set. `idle` is not one of
 *      {@link STRATEGY_HEALTH_INDICATORS}, so it renders as not-available here rather
 *      than being quietly mapped onto something cheerful.
 *
 * `undetermined` RENDERS AS NOT AVAILABLE — an em-dash in the neutral group with an
 * accessible name of "Health: not available" and the reason in the title. Never a green
 * badge, never the word "Healthy", never a hidden default.
 *
 * THE ENVIRONMENT BADGE IS INTERIM (task 6.7)
 * ------------------------------------------
 * `ds/TradingEnvironmentBadge` is task 6.7's and did not exist when this file was
 * written, so importing it would have broken the build. `StrategyEnvironment` below is
 * the same four-axis treatment (hue, label, icon, border style — design.md §4.2,
 * Requirement 12.3) at chip size, resolved through `environmentTreatment` from
 * `design/semantic.js`, which is the same source 6.7 will use.
 *
 * >>> WHEN `ds/TradingEnvironmentBadge.jsx` LANDS: delete `StrategyEnvironment` and
 * >>> render `<TradingEnvironmentBadge environment={environment} variant="chip" />` in
 * >>> its place. Nothing else in this file changes. `Panel.jsx`'s `PanelEnvironment`
 * >>> carries the identical note, and both swaps belong to the same edit.
 */

import { memo } from 'react';
import { FlaskConical, History, Radio } from 'lucide-react';

import {
  HEALTH_UNDETERMINED,
  STRATEGY_HEALTH_INDICATORS,
  computeStrategyHealth,
} from '../../lib/strategyHealth';
import { environmentTreatment, statusToken } from '../../design/semantic';

import { NotAvailable, StatusBadge, humaniseState } from './StatusBadge';
import { assertContract } from './devAssert';

/**
 * `_normalizeStatus`'s six states, transcribed from `pages/Strategies.jsx`.
 *
 * Every spelling on the right is one the page already collapses; nothing new is
 * recognised here, so migrating a call site cannot change what it displays.
 */
const STATUS_SPELLINGS = Object.freeze({
  running: 'running',
  active: 'running',
  live: 'running',
  started: 'running',
  paused: 'paused',
  pause: 'paused',
  backtesting: 'backtesting',
  testing: 'backtesting',
  draft: 'draft',
  failed: 'failed',
  error: 'failed',
  stopped: 'stopped',
  inactive: 'stopped',
  stop: 'stopped',
});

/** The six states this component reports, in `_normalizeStatus`'s order. */
export const STRATEGY_STATUSES = Object.freeze([
  'running',
  'paused',
  'backtesting',
  'draft',
  'failed',
  'stopped',
]);

/** Copy for the one state that is an absence rather than a state. */
const STATUS_UNREPORTED_LABEL = 'Status not reported';
const HEALTH_UNDETERMINED_REASON =
  'No deployment record and no completed backtest for this strategy, so its health is '
  + 'not determined. It is not a report of good health.';

/** Health → the `statusToken` vocabulary. `undetermined` has no entry: it is not a state. */
const HEALTH_STATE = Object.freeze({
  healthy: 'healthy',
  degraded: 'degraded',
  error: 'error',
});

/** lucide components for `semantic.js`'s icon NAMES; see `Panel.jsx` for why it holds names. */
const ENVIRONMENT_ICONS = Object.freeze({ Radio, FlaskConical, History });

/**
 * A server status spelling → one of {@link STRATEGY_STATUSES}, or `null`.
 *
 * DELIBERATE DEVIATION FROM `_normalizeStatus`: that function returns `stopped` for the
 * empty string, for `undefined` and for any spelling it does not know, so a strategy
 * whose status the server never reported was displayed as stopped. "Stopped" is a
 * claim — it says a trader's automation is not running — and it is not one this
 * component is entitled to make on the server's behalf. `null` is returned instead and
 * renders as {@link STATUS_UNREPORTED_LABEL}. Every spelling the page recognised still
 * resolves exactly as it did.
 *
 * @param {unknown} value
 * @returns {string|null}
 */
export function normaliseStrategyStatus(value) {
  if (typeof value !== 'string') return null;
  const key = value.trim().toLowerCase();
  if (key === '') return null;
  return Object.prototype.hasOwnProperty.call(STATUS_SPELLINGS, key)
    ? STATUS_SPELLINGS[key]
    : null;
}

/**
 * The health indicator to display: one of {@link STRATEGY_HEALTH_INDICATORS}.
 *
 * A string is taken as `computeStrategyHealth`'s output and validated against its
 * vocabulary; a row object is handed to `computeStrategyHealth`; anything else — and any
 * string outside the vocabulary — is `undetermined`. There is no path to `healthy` that
 * does not originate in that function.
 *
 * @param {unknown} health A reported indicator, or a strategy row, or nothing.
 * @returns {string}
 */
export function resolveStrategyHealth(health) {
  if (typeof health === 'string') {
    const key = health.trim().toLowerCase();
    return STRATEGY_HEALTH_INDICATORS.includes(key) ? key : HEALTH_UNDETERMINED;
  }
  if (health !== null && typeof health === 'object' && !Array.isArray(health)) {
    return computeStrategyHealth(health);
  }
  return HEALTH_UNDETERMINED;
}

/**
 * INTERIM — replaced by `ds/TradingEnvironmentBadge` at task 6.7. See the module
 * docblock. Four axes: hue, label text, icon and border style, so a trader who cannot
 * distinguish the hues still cannot mistake live for paper (Requirement 12.3).
 */
function StrategyEnvironment({ environment }) {
  const treatment = environmentTreatment(environment);
  // The unconfirmed arm still takes its colour from `semantic.js` — `statusToken(null)`
  // is the neutral group — so nothing here chooses a colour on its own.
  const { fg, wash } = treatment ?? statusToken(null);
  const Icon = treatment ? ENVIRONMENT_ICONS[treatment.icon] : null;

  return (
    <span
      data-strategy-environment={treatment ? treatment.id : 'UNCONFIRMED'}
      title={treatment ? treatment.long : 'The server did not report an execution environment.'}
      className="inline-flex shrink-0 items-center gap-1 rounded-sm border px-1.5 py-0.5 text-micro font-mono tracking-wide"
      style={{
        color: fg,
        backgroundColor: wash,
        borderColor: fg,
        borderStyle: treatment ? treatment.border : 'dotted',
      }}
    >
      {Icon ? <Icon size={10} strokeWidth={2} aria-hidden="true" /> : null}
      <span className="sr-only">Trading environment: </span>
      {treatment ? treatment.label : 'ENVIRONMENT UNCONFIRMED'}
    </span>
  );
}

/**
 * A strategy's status, health and execution environment on one line.
 *
 * @param {Object} props
 * @param {string} [props.status] The server's `status`, verbatim. Absent or unreadable
 *   renders "Status not reported" — never "stopped".
 * @param {boolean} [props.isRunning] The server's `is_running`. Consulted ONLY when
 *   `status` is absent or unreadable, and only its `true` arm: "not running" is not a
 *   status, since it could equally be paused, draft, stopped or failed.
 * @param {string|Object} [props.health] `computeStrategyHealth`'s output, or a strategy
 *   row for it to read. Anything else is `undetermined`, which renders as not available.
 * @param {'LIVE'|'PAPER'|'BACKTEST'|null} [props.environment] Omit entirely to render no
 *   environment chip; pass `null` when the server reported none, which renders
 *   "ENVIRONMENT UNCONFIRMED". Never a guessed environment.
 * @param {boolean} [props.compact] Drops the "Health" caption. The health VALUE is still
 *   rendered as text — no state is ever carried by colour alone (Requirement 1.4).
 * @param {string} [props.className]
 */
export const StrategyStatus = memo(function StrategyStatus({
  status,
  isRunning,
  health,
  // No default, so `undefined` ("no chip") stays distinguishable from `null` ("the
  // server did not say"). This is `Panel`'s convention and it is deliberate.
  environment,
  compact = false,
  className = '',
  ...rest
}) {
  // Both would be legitimate individually; together they mean the component was given
  // nothing at all to report, which is a call-site defect rather than a server silence.
  assertContract(
    status !== undefined || isRunning !== undefined || health !== undefined,
    'StrategyStatus was given no `status`, no `isRunning` and no `health` — there is nothing '
      + 'for it to report. Pass the row\'s `status` (and `health` for the indicator); pass '
      + '`status={null}` explicitly if the server genuinely reported none.',
  );

  const resolvedStatus =
    normaliseStrategyStatus(status) ?? (isRunning === true ? 'running' : null);
  const healthIndicator = resolveStrategyHealth(health);
  const healthState = HEALTH_STATE[healthIndicator];
  const showsEnvironment = environment !== undefined;

  return (
    <span
      data-strategy-status={resolvedStatus ?? 'unreported'}
      data-strategy-health={healthIndicator}
      className={`inline-flex min-w-0 flex-wrap items-center gap-2 ${className}`.trim()}
      {...rest}
    >
      {/* Text always present, from `StatusBadge`. No pulse, no halo, no blink. */}
      <StatusBadge
        state={resolvedStatus ?? 'unknown'}
        label={resolvedStatus ? humaniseState(resolvedStatus) : STATUS_UNREPORTED_LABEL}
        dot
        size={compact ? 'sm' : 'md'}
      />

      <span className="inline-flex min-w-0 items-center gap-1">
        {compact ? null : (
          <span className="text-micro uppercase tracking-wide text-content-secondary">Health</span>
        )}
        {/* `undetermined` is the one indicator with no badge. Rendering it as a chip
            would give an absence the same visual weight as a verdict; the em-dash says
            "we do not know" and its accessible name says why. */}
        {healthState ? (
          <StatusBadge state={healthState} label={humaniseState(healthIndicator)} size="sm" />
        ) : (
          <NotAvailable label="Health" reason={HEALTH_UNDETERMINED_REASON} />
        )}
      </span>

      {showsEnvironment ? <StrategyEnvironment environment={environment} /> : null}
    </span>
  );
});

export default StrategyStatus;
