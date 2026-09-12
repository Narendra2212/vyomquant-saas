/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/RiskIndicator — how close an account is to a limit, and who said so
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.6. design.md §5.1, §5.3. Requirements 1.2, 1.4, 1.5.
 *
 * WHAT IT REPLACES
 * ---------------
 *   `RiskMeter` (`ui-legacy/primitives.jsx`) — `{value, max, label, warningAt: 0.7,
 *       dangerAt: 0.9}`. It computes `SAFE`/`WARNING`/`CRITICAL` **client-side** from
 *       `value / max`, with thresholds that match no server rule, and paints the fill
 *       with a `box-shadow` glow once the ratio passes `warningAt`. It also calls
 *       `value.toLocaleString()` unguarded, so a missing figure is a TypeError.
 *   `ProgressBar`'s risk uses (same file) — `{v, max, color}`. It takes a COLOUR from
 *       the call site, which is precisely what Requirement 1.4 rules out, and divides by
 *       `max` without checking it, so `max = 0` renders `NaN%` as a width.
 *
 * THE SERVER ALREADY DECIDED, SO THE CLIENT MUST NOT
 * -------------------------------------------------
 * `backend_app/routers/risk.py` computes the level and returns it as `status`:
 *
 *     kill switch active                       → BLOCKED
 *     loss or position utilisation ≥ 100 %     → BLOCKED
 *                                    ≥  85 %   → CRITICAL
 *                                    ≥  60 %   → WARNING
 *                                otherwise     → SAFE
 *
 * `RiskMeter`'s 70/90 thresholds disagree with those 60/85 numbers, so the same account
 * could read `SAFE` in a panel and `WARNING` in the risk engine that is about to refuse
 * its order. A client and a server disagreeing about risk is the failure this component
 * exists to prevent: a reported `level` is used verbatim, and {@link deriveRiskLevel} —
 * whose defaults ARE the server's 60/85 — runs only when no level was reported at all.
 * `data-risk-source` publishes which of the two happened.
 *
 * TWO SERVER VOCABULARIES, BOTH REAL (finding, recorded not resolved)
 * -----------------------------------------------------------------
 * design.md names `risk.py`'s four uppercase values, and they are correct for
 * `GET /api/risk/status`. But the field the frontend actually reads on the dashboard is
 * `risk_level` from `dashboard_aggregation_service.get_risk_data`, which is LOWERCASE
 * and has FIVE values — `low` `medium` `high` `critical` `blocked` — on 40/75/100
 * thresholds. A third variant inside the same service grades `low`/`medium`/`high`/
 * `critical` off drawdown ratios, and `Dashboard.jsx` itself writes `risk_level:
 * "blocked"` / `"low"` when a kill-switch websocket event arrives. So both vocabularies
 * reach the browser today. {@link normaliseRiskLevel} accepts both and maps onto the
 * four `risk.py` names, because a component that understood only one of them would
 * silently fall through to deriving a level on the pages that use the other — which is
 * the exact behaviour this file is meant to make impossible. Reconciling the two
 * backends is not this task's to do; reading both honestly is.
 *
 * `high` MAPS TO `CRITICAL`, AND `critical` DOES NOT MAP TO `BLOCKED`
 * -----------------------------------------------------------------
 * The two ladders do not line up: the dashboard's `critical` means ≥ 100 % utilisation,
 * which `risk.py` would call `BLOCKED`. `BLOCKED` says trading is halted, and only the
 * kill switch and `risk.py` itself are entitled to say that, so `critical` maps to
 * `CRITICAL` and nothing but a literal `blocked` (or `halted`) produces `BLOCKED`.
 * Over-claiming a halt is a different error from missing one, and this is the direction
 * that keeps the UI's claim inside what the server said.
 *
 * NO GLOW (Requirement 1.5)
 * ------------------------
 * There is no `box-shadow` in this file, at any level, and no animation. The escalation
 * is carried by the fill colour, the level word and the threshold marks. `RiskMeter`
 * glowed hardest exactly when a trader most needed to read the number underneath it.
 */

import { memo } from 'react';

import { statusToken } from '../../design/semantic';

import { NotAvailable, StatusBadge } from './StatusBadge';
import { assertContract, hasText } from './devAssert';

/** The four levels `risk.py` computes, in escalating order. */
export const RISK_LEVELS = Object.freeze(['SAFE', 'WARNING', 'CRITICAL', 'BLOCKED']);

/** `risk.py`'s own thresholds, as fractions. Overridable, but these are the real ones. */
export const DEFAULT_RISK_THRESHOLDS = Object.freeze({ warn: 0.6, critical: 0.85 });

/**
 * Every spelling either backend produces, mapped onto {@link RISK_LEVELS}.
 * `routers/risk.py` supplies the uppercase four; `dashboard_aggregation_service.py`
 * supplies the lowercase five. See the module docblock for why `critical ≠ BLOCKED`.
 */
const LEVEL_SPELLINGS = Object.freeze({
  safe: 'SAFE',
  low: 'SAFE',
  warning: 'WARNING',
  warn: 'WARNING',
  medium: 'WARNING',
  critical: 'CRITICAL',
  high: 'CRITICAL',
  blocked: 'BLOCKED',
  halted: 'BLOCKED',
});

/** Level → the `statusToken` vocabulary. Colour is chosen there, never here. */
const LEVEL_STATE = Object.freeze({
  SAFE: 'ok',
  WARNING: 'warning',
  CRITICAL: 'critical',
  BLOCKED: 'blocked',
});

/** The visible word. `BLOCKED` says what it means: nothing new will be submitted. */
const LEVEL_LABEL = Object.freeze({
  SAFE: 'Within limits',
  WARNING: 'Approaching limit',
  CRITICAL: 'Near limit',
  BLOCKED: 'Trading blocked',
});

const UTILISATION_UNREPORTED_REASON =
  'The server has not reported a utilisation figure for this limit. It is not zero usage.';
const LEVEL_UNREPORTED_REASON =
  'The server did not report a risk level, and there is no utilisation figure to work one out '
  + 'from. This is not a report that risk is low.';

/**
 * A reported risk level → one of {@link RISK_LEVELS}, or `null` when unreadable.
 *
 * `null` is NOT a licence to derive one. An unreadable level means the server said
 * something this component does not understand, and quietly substituting a computed
 * verdict would be the client overruling a value it failed to parse. The component
 * distinguishes that from an absent level, which is the only case a derivation runs in.
 *
 * @param {unknown} level
 * @returns {string|null}
 */
export function normaliseRiskLevel(level) {
  if (typeof level !== 'string') return null;
  const key = level.trim().toLowerCase();
  if (key === '') return null;
  return Object.prototype.hasOwnProperty.call(LEVEL_SPELLINGS, key)
    ? LEVEL_SPELLINGS[key]
    : null;
}

/**
 * A utilisation percentage → a level, on `risk.py`'s ladder.
 *
 * `utilizationPct` is a PERCENTAGE (0–100), because that is what the server's
 * `utilization_pct` fields are, while `thresholds` are FRACTIONS (0–1), because that is
 * what design.md's sketch and `RiskMeter` both used. The two are not interchangeable and
 * conflating them would put every warning at 0.6 % instead of 60 %, so the comparison
 * scales the thresholds here, once.
 *
 * @param {unknown} utilizationPct 0–100, as the server reports it.
 * @param {{warn?: number, critical?: number}} [thresholds] Fractions.
 * @returns {string|null} `null` when there is no readable figure to derive from.
 */
export function deriveRiskLevel(utilizationPct, thresholds = DEFAULT_RISK_THRESHOLDS) {
  if (typeof utilizationPct !== 'number' || !Number.isFinite(utilizationPct)) return null;
  const warn = Number.isFinite(thresholds?.warn) ? thresholds.warn : DEFAULT_RISK_THRESHOLDS.warn;
  const critical = Number.isFinite(thresholds?.critical)
    ? thresholds.critical
    : DEFAULT_RISK_THRESHOLDS.critical;
  // `risk.py`: ≥ 100 % of a limit is BLOCKED, not merely critical.
  if (utilizationPct >= 100) return 'BLOCKED';
  if (utilizationPct >= critical * 100) return 'CRITICAL';
  if (utilizationPct >= warn * 100) return 'WARNING';
  return 'SAFE';
}

/** 0–100, clamped, for the bar's width only. Never used to decide a level. */
const clampPct = (pct) => Math.min(100, Math.max(0, pct));

/**
 * A limit, how much of it is used, and how the server grades that.
 *
 * @param {Object} props
 * @param {string} [props.level] The server's `risk_level` / `status`, verbatim. Preferred
 *   over anything derivable. Both backend vocabularies are understood.
 * @param {number} [props.utilizationPct] 0–100. `null`/absent renders the not-available
 *   marker — never a 0 % bar, which would read as "no risk used" (Requirement 14.5).
 * @param {string} [props.limitLabel] The limit, already formatted: `"$500.00 daily loss"`.
 * @param {string} [props.currentLabel] The current figure, already formatted: `"$312.40"`.
 * @param {{warn: number, critical: number}} [props.thresholds] Fractions, used ONLY when
 *   no level was reported. Defaults to `risk.py`'s 0.6 / 0.85.
 * @param {string} [props.className]
 */
export const RiskIndicator = memo(function RiskIndicator({
  level,
  utilizationPct,
  limitLabel,
  currentLabel,
  thresholds = DEFAULT_RISK_THRESHOLDS,
  className = '',
  ...rest
}) {
  assertContract(
    level !== undefined || utilizationPct !== undefined,
    'RiskIndicator was given neither `level` nor `utilizationPct` — there is no risk to show. '
      + "Pass the server's `risk_level` (preferred), or a utilisation percentage for it to be "
      + 'derived from, or `utilizationPct={null}` if the figure genuinely was not reported.',
  );

  const reported = normaliseRiskLevel(level);
  const measured =
    typeof utilizationPct === 'number' && Number.isFinite(utilizationPct)
      ? utilizationPct
      : null;

  // The whole point of the component: the server's word first, a derivation only where
  // the server said NOTHING, and a record of which of the two happened.
  //
  // "Said nothing" is not the same as "said something unreadable". A level this
  // component cannot parse (`hasText(level)` with no canonical match) blocks the
  // derivation too, because computing a verdict on top of a value we failed to read is
  // the client overruling the server rather than deferring to it — and it is the same
  // client/server disagreement about risk that the reported-level preference exists to
  // prevent. So an unreadable level reports as unreported, and the measured figure is
  // still shown beside it because that part is a fact.
  const derived = reported === null && !hasText(level) ? deriveRiskLevel(measured, thresholds) : null;
  const resolved = reported ?? derived;
  const source = reported !== null ? 'server' : derived !== null ? 'derived' : 'unreported';

  const warnPct = clampPct(
    (Number.isFinite(thresholds?.warn) ? thresholds.warn : DEFAULT_RISK_THRESHOLDS.warn) * 100,
  );
  const criticalPct = clampPct(
    (Number.isFinite(thresholds?.critical)
      ? thresholds.critical
      : DEFAULT_RISK_THRESHOLDS.critical) * 100,
  );

  // Colour from `semantic.js`, for both the badge and the fill. Nothing in this file
  // holds a colour of its own, and there is no `color` prop that could supply one.
  const fill = statusToken(resolved ? LEVEL_STATE[resolved] : null).fg;
  const warnMark = statusToken('warning').fg;
  const criticalMark = statusToken('critical').fg;

  return (
    <div
      data-risk-level={resolved ?? 'unreported'}
      data-risk-source={source}
      className={`flex min-w-0 flex-col gap-1.5 ${className}`.trim()}
      {...rest}
    >
      <div className="flex min-w-0 items-center justify-between gap-2">
        {/* The level as a WORD, not only as a hue (Requirement 1.4, and the a11y bar
            task 6.27 raises). `unreported` says so rather than showing "Within limits",
            which is what a `SAFE` default would have claimed on the server's behalf. */}
        {resolved ? (
          <StatusBadge state={LEVEL_STATE[resolved]} label={LEVEL_LABEL[resolved]} dot size="sm" />
        ) : (
          <NotAvailable label="Risk level" reason={LEVEL_UNREPORTED_REASON} />
        )}

        {measured === null ? (
          <NotAvailable label="Limit used" reason={UTILISATION_UNREPORTED_REASON} />
        ) : (
          <span className="shrink-0 font-mono text-micro tabular-nums text-content-secondary">
            <span className="sr-only">Limit used: </span>
            {`${clampPct(measured).toFixed(1)}%`}
          </span>
        )}
      </div>

      {/* No bar at all without a measurement. A 0-width track would be indistinguishable
          from a genuine 0 %, and a full-width one from a breach. */}
      {measured === null ? null : (
        <div
          role="progressbar"
          aria-valuenow={Math.round(clampPct(measured))}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={
            hasText(limitLabel) ? `Utilisation of ${limitLabel}` : 'Limit utilisation'
          }
          className="relative h-1.5 w-full overflow-hidden rounded-sm bg-surface-inset"
        >
          {/* The two threshold marks. They are what make the bar readable without a
              colour change — the fill's position relative to them is visible in
              greyscale. Both are `aria-hidden`; the level word carries the meaning. */}
          <span
            aria-hidden="true"
            className="absolute inset-y-0 w-px"
            style={{ left: `${warnPct}%`, backgroundColor: warnMark }}
          />
          <span
            aria-hidden="true"
            className="absolute inset-y-0 w-px"
            style={{ left: `${criticalPct}%`, backgroundColor: criticalMark }}
          />
          {/* No glow, no transition, no animation. */}
          <span
            aria-hidden="true"
            className="absolute inset-y-0 left-0 rounded-sm"
            style={{ width: `${clampPct(measured)}%`, backgroundColor: fill }}
          />
        </div>
      )}

      {/* Both labels are strings the caller has already formatted, so an absent one is
          omitted rather than rendered as an empty row — there is no value here for the
          not-available marker to stand in for. */}
      {hasText(currentLabel) || hasText(limitLabel) ? (
        <div className="flex min-w-0 items-center justify-between gap-2 font-mono text-micro tabular-nums text-content-secondary">
          <span className="truncate">{hasText(currentLabel) ? currentLabel : null}</span>
          <span className="shrink-0 truncate">{hasText(limitLabel) ? limitLabel : null}</span>
        </div>
      ) : null}
    </div>
  );
});

export default RiskIndicator;
