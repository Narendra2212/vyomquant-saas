/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/Metric — the tier-aware figure, and the one place a zero cannot be invented
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.4. design.md §5.1, §7 (tiers), §18.
 * Requirements 4.1, 8.1, 11.1, 14.5, 19.3. Property P5.
 *
 * Replaces `AnimatedNumber` and the hand-rolled metric card in `Dashboard`,
 * `Portfolio`, `Backtester`, `TradeHistory` and `PaperTrading`.
 *
 * ═══ THE ONE BEHAVIOUR THIS COMPONENT EXISTS FOR ═══
 *
 * `value == null` — or `unavailable` — renders the **not-available marker**. It never
 * renders `0`.
 *
 * That sentence is the entire justification for the component. Requirements 14.5 and
 * 19.3 forbid a fabricated figure standing in for one the backend did not report, and
 * every way of honouring them at the call site has already failed here at least once:
 *
 *   * `pages/Portfolio.jsx` fell back to a hardcoded `100000 / 0` summary on a failed
 *     read, so a trader saw a plausible account value that no server had sent.
 *   * `toNumber(v, fallback = 0)` in `pages/TradeHistory.jsx` turns every absent
 *     figure into a zero on the way in, which is indistinguishable from a real flat
 *     P&L by the time it reaches a cell.
 *   * `pages/Dashboard.jsx` renders `latency ?? 0` as `0 ms`, which reads as a
 *     perfect connection when the truth is that nothing has been measured.
 *
 * Each of those is one `??` away from being correct, and that is exactly why the rule
 * cannot live at the call sites: it has to be broken only once to be false. Here it is
 * a leaf behaviour of the only component allowed to render a figure, so the ~200
 * figures on the in-scope pages get it without any of them having to remember.
 *
 * The three ways it is made hard to bypass:
 *
 *   1. **There is no branch that formats an unreadable value.** `readReported`
 *      (`design/reported.js`) decides availability once, and the formatter is only
 *      reached on its available arm. `0` and `false` are readable and always render;
 *      `null`, `undefined`, `''`, `NaN` and `±Infinity` are not and never do.
 *   2. **`value` is not trusted to be a scalar.** An object, an array or a function
 *      renders the marker and reports a contract violation, rather than crashing the
 *      panel or printing `[object Object]`.
 *   3. **The marker always carries a reason.** `unavailableReason`, or the
 *      `Reported<T>` unavailable arm's own reason, or `UNREPORTED_REASON` — never a
 *      bare dash. Requirement 19.3 asks for an explicit not-available state, and a
 *      dash with no explanation is only marginally better than a blank.
 *
 * ═══ COLOUR IS OPTIONAL, AND USUALLY ABSENT ═══
 *
 * `state` is optional and goes through `statusToken`. Omitting it renders the figure
 * in `content-primary` — no hue at all. Requirement 1.5 asks for emphasis only on
 * elements representing current state, risk or required action, and most figures on a
 * trading screen are none of those: a portfolio value is not a status. A page that
 * colours all nine of its metrics has spent the whole budget and cannot signal
 * anything with colour any more.
 *
 * P&L is the exception, and it has its own component: `ds/PnLDisplay`, whose hue comes
 * from `pnlToken` so that **zero is neutral**.
 *
 * ═══ NEVER ROUNDED UNLESS ASKED ═══
 *
 * With no `precision`, the figure is grouped and otherwise rendered exactly as it
 * arrived — `"1.50"` keeps its trailing zero, and an eight-decimal crypto price keeps
 * all eight. This is `ds/DataTable`'s rule (§11.3's "grouped, and never rounded") and
 * the same figure has to read the same way in a cell and in a card. `precision` is the
 * caller's explicit instruction to round, and `format="currency"` does **not** imply
 * two decimals: defaulting to that would silently turn `0.00000123 BTC` into `0.00`.
 *
 * ═══ WHAT IS EXPORTED, AND WHY IT LIVES HERE ═══
 *
 * `formatFigure`, `tierClasses` and `NotAvailableMarker` are exported for
 * `ds/PnLDisplay`, which is a figure with a sign and a fixed hue and needs the same
 * three. They sit in this file rather than in a fourth module because they are one
 * task's work and a shared `ds/figure.js` would be a third place to look for the
 * formatting rules. `ds/DataTable` keeps its own cell formatter (task 6.9); the two
 * agree on grouping, and converging them is a barrel-level decision for task 6.23,
 * not something to do by reaching across primitives.
 */

import { useId } from 'react';

import { statusToken } from '../../design/semantic';
import { readReported, isReported, UNREPORTED_REASON } from '../../design/reported';

import { assertContract, hasText } from './devAssert';

/** The not-available marker (design.md §3.2, §5.1). Never `0`, never blank. */
export const NOT_AVAILABLE = '—';

/** design.md §7's three priority tiers, as the type scale they select. */
export const METRIC_TIERS = Object.freeze([1, 2, 3]);

/** The `format` vocabulary from design.md §5.1. */
export const METRIC_FORMATS = Object.freeze([
  'currency',
  'percent',
  'number',
  'integer',
  'duration',
  'raw',
]);

/** The formats that are figures rather than text, and so get mono + tabular numerals. */
const NUMERIC_FORMATS = Object.freeze(['currency', 'percent', 'number', 'integer', 'duration']);

/**
 * Tier → type scale, the only mapping design.md §5.1 gives.
 *
 * `--text-figure` / `--text-title` / `--text-body`, reached through the Tailwind
 * utilities `styles/tokens.css` generates for them. Weight rides along with size
 * because a tier is a claim about importance, and a 28px figure at normal weight
 * beside a 13px one at semibold reads as the smaller one being the heading.
 *
 * @param {1|2|3} tier
 * @returns {string}
 */
export function tierClasses(tier) {
  switch (tier) {
    case 1:
      return 'text-figure font-semibold';
    case 3:
      return 'text-body font-medium';
    case 2:
    default:
      return 'text-title font-semibold';
  }
}

/** Label scale per tier. A tier-1 figure earns a slightly larger label than a row of tier-3s. */
function labelClasses(tier) {
  return tier === 1 ? 'text-small' : 'text-micro';
}

/* ══════════════════════════════════════════════════════════════════════════
 * FORMATTING — grouped, and never rounded unless `precision` says so
 * ══════════════════════════════════════════════════════════════════════════ */

/** `'12'`, `'-3.50'`, `'+0.7'` — a decimal string that can be grouped as-is. */
const PLAIN_DECIMAL = /^[+-]?\d+(?:\.\d+)?$/;

/**
 * Thousands separators, added by hand.
 *
 * Not `Intl.NumberFormat`, for the reason `ds/DataTable` gives: its separators are
 * locale-dependent, and a terminal that renders `1.234,5` in one panel and `1,234.5`
 * in another has manufactured a hazard out of a formatting default. Doing it by hand
 * also cannot round, which is the property that matters for money.
 *
 * Exponential notation is returned untouched — grouping it would misstate its
 * magnitude.
 */
function groupDecimal(text) {
  if (/[eE]/.test(text)) return text;
  const negative = text.startsWith('-');
  const unsigned = negative || text.startsWith('+') ? text.slice(1) : text;
  const [whole, fraction] = unsigned.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const sign = negative ? '-' : '';
  return fraction === undefined ? `${sign}${grouped}` : `${sign}${grouped}.${fraction}`;
}

/** A declared precision, or `null`. Negative and non-integer precisions are not instructions. */
function readPrecision(precision) {
  if (!Number.isFinite(precision)) return null;
  const digits = Math.trunc(precision);
  return digits >= 0 && digits <= 20 ? digits : null;
}

/**
 * A number or numeric string → its digits, at the declared precision if there is one.
 *
 * A numeric *string* with no declared precision is grouped as a string so the server's
 * own trailing zeros survive the trip: `"1.50"` is the exchange saying two decimals,
 * and `Number("1.50")` throws that statement away.
 */
function digitsOf(value, digits) {
  if (typeof value === 'bigint') return String(value);

  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!PLAIN_DECIMAL.test(trimmed)) return null;
    if (digits === null) return groupDecimal(trimmed);
    const numeric = Number(trimmed);
    return Number.isFinite(numeric) ? groupDecimal(numeric.toFixed(digits)) : null;
  }

  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return groupDecimal(digits === null ? String(value) : value.toFixed(digits));
}

/**
 * Milliseconds → the coarsest unit that still says something useful.
 *
 * The input unit is **milliseconds**, because every duration the backend reports is
 * one: `latency_ms`, `get_health_status`'s measurement, the trace stages' `latencyMs`.
 * A duration carries its own unit in the output, so `unit` should be omitted alongside
 * `format="duration"`.
 */
function formatDuration(value) {
  const ms = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(ms)) return null;

  const sign = ms < 0 ? '-' : '';
  const abs = Math.abs(ms);

  if (abs < 1000) return `${sign}${Math.round(abs)}ms`;
  if (abs < 60_000) return `${sign}${(abs / 1000).toFixed(1)}s`;

  const totalSeconds = Math.floor(abs / 1000);
  if (abs < 3_600_000) {
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${sign}${minutes}m ${String(seconds).padStart(2, '0')}s`;
  }

  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  return `${sign}${hours}h ${String(minutes).padStart(2, '0')}m`;
}

/**
 * A readable value → the string a trader sees. `null` when it has no honest rendering.
 *
 * `percent` appends `%` and **does not multiply by 100**. Every percentage the backend
 * sends is already in percent units (`current_drawdown_pct`, `today_return_pct`,
 * `win_rate`), and a component that scaled them would render a 3.2% drawdown as 320%.
 * A caller holding a 0..1 fraction converts it before it gets here, where the decision
 * is visible.
 *
 * `integer` is the one format that rounds without being asked, because that is what
 * declaring it means: a count. Declare it only for counts — trade counts, position
 * counts, signal counts — never for money.
 *
 * @param {number|string|bigint|boolean} value A value {@link readReported} called readable.
 * @param {{format?: string, precision?: number}} [options]
 * @returns {string|null}
 */
export function formatFigure(value, options) {
  const { format = 'number', precision } = options || {};
  const digits = readPrecision(precision);

  if (format === 'raw') {
    // Deliberately untouched: no grouping, no rounding, no unit. `raw` is for a value
    // that is already exactly what it should read as — a version string, a symbol, an
    // account label.
    if (typeof value === 'string') return value;
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    return String(value);
  }

  if (format === 'duration') return formatDuration(value);

  if (typeof value === 'boolean') return value ? 'Yes' : 'No';

  const text = digitsOf(value, format === 'integer' ? (digits ?? 0) : digits);
  if (text === null) return null;

  return format === 'percent' ? `${text}%` : text;
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE MARKER
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The not-available marker: an em-dash in `content-muted`, named for a screen reader,
 * with the reason on a tooltip.
 *
 * The accessible name is `"{label}: not available"` (design.md §5.1) and it is carried
 * **twice**, on purpose:
 *
 *   * as an `aria-label` on the wrapper, which is what a test — and Property 5 —
 *     queries, and what a browser exposes for the tooltip;
 *   * as `sr-only` text with the reason appended, because a generic `<span>`'s
 *     `aria-label` is honoured inconsistently across screen readers. The em-dash
 *     itself is `aria-hidden`, so nothing is announced twice whichever wins.
 *
 * `content-muted` is 3.2:1 and `styles/tokens.css` marks it NON-TEXT ONLY. The dash
 * qualifies under that note's own exemption — it is a glyph standing beside a labelled
 * element, not body text — and the full sentence is in the accessible name, so the
 * information does not depend on reading a low-contrast dash.
 *
 * The reason reaches a sighted trader through `title`, which `ds/Tooltip` (§5.2)
 * replaces when it lands; the copy and this component's contract do not change then.
 *
 * @param {Object} props
 * @param {string} [props.label] The figure's label, for the accessible name.
 * @param {string} [props.reason] Why the value is not available (Requirement 19.3).
 * @param {string} [props.className]
 */
export function NotAvailableMarker({ label, reason, className = '' }) {
  const text = hasText(reason) ? reason.trim() : UNREPORTED_REASON;
  const name = hasText(label) ? `${label.trim()}: not available` : 'Not available';

  return (
    <span
      data-metric-marker="not-available"
      aria-label={name}
      title={text}
      className={`text-content-muted ${className}`.trim()}
    >
      <span aria-hidden="true">{NOT_AVAILABLE}</span>
      <span className="sr-only">{`${name}. ${text}`}</span>
    </span>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE COMPONENT
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * A labelled figure.
 *
 * @param {Object} props
 * @param {string} props.label REQUIRED, and always visible. A figure whose label is in
 *   a heading three rows away is a number a trader has to guess at.
 * @param {number|string|null|undefined|{available: boolean}} [props.value] A raw value
 *   or a `Reported<T>` (`design/reported.js`). `null`, `undefined`, `''`, `NaN` and the
 *   unavailable arm all render the marker.
 * @param {string} [props.unit] Rendered after the figure in `content-secondary`, so it
 *   never competes with the digits. Omit for `format="duration"`.
 * @param {1|2|3} [props.tier] design.md §7's priority tier → the type scale.
 * @param {'currency'|'percent'|'number'|'integer'|'duration'|'raw'} [props.format]
 * @param {number} [props.precision] Decimal places. Omitted means **do not round**.
 * @param {string} [props.state] Optional. Goes through `statusToken`. Omit for a calm
 *   figure — which is most of them (Requirement 1.5).
 * @param {boolean} [props.unavailable] Renders the marker whatever `value` holds.
 * @param {string} [props.unavailableReason] Why. Requirement 19.3.
 * @param {string} [props.hint] Explains what the figure measures, on a tooltip.
 * @param {string} [props.className]
 */
export function Metric({
  label,
  value,
  unit,
  tier = 2,
  format = 'number',
  precision,
  state,
  unavailable = false,
  unavailableReason,
  hint,
  className = '',
  ...rest
}) {
  const hintId = useId();

  assertContract(
    hasText(label),
    'Metric: `label` is required and is always rendered. A bare figure is a number '
      + 'nobody can identify, and every metric card this component replaces already had '
      + 'a label — the requirement is that it cannot be dropped by accident.',
  );

  const knownTier = METRIC_TIERS.includes(tier);
  assertContract(
    knownTier,
    `Metric${hasText(label) ? ` "${label}"` : ''}: \`tier\` must be 1, 2 or 3 (design.md §7), `
      + `received ${JSON.stringify(tier)}. It selects --text-figure / --text-title / --text-body.`,
  );
  const resolvedTier = knownTier ? tier : 2;

  const knownFormat = METRIC_FORMATS.includes(format);
  assertContract(
    knownFormat,
    `Metric${hasText(label) ? ` "${label}"` : ''}: \`format\` must be one of `
      + `${METRIC_FORMATS.join(' | ')}, received ${JSON.stringify(format)}.`,
  );
  const resolvedFormat = knownFormat ? format : 'number';

  // A raw value that is neither a scalar nor a `Reported<T>` is a call-site defect, and
  // one that React would otherwise turn into a thrown "Objects are not valid as a React
  // child" — taking the panel with it. It renders the marker instead, and says so.
  const rawObject = typeof value === 'object' && value !== null && !isReported(value);
  assertContract(
    !rawObject && typeof value !== 'function',
    `Metric${hasText(label) ? ` "${label}"` : ''}: \`value\` must be a number, a string, or a `
      + '`Reported<T>` from `design/reported.js` — received '
      + `${Array.isArray(value) ? 'an array' : typeof value}. Pick the field off the object at the `
      + 'call site, or wrap it with `available()` / `unavailable(reason)`.',
  );

  // One decision, made once, for every input. An object, an array, a function, `NaN`
  // and `''` all arrive here as "not readable" — there is no second opinion available
  // further down, because the formatter is only reached on the available arm.
  const report = readReported(value, unavailableReason);
  // `unavailable` is the caller's explicit statement and outranks a present value: a
  // figure the page has declared unreportable must not be shown because it happens to
  // be in memory (Requirement 14.5's cached-as-live case).
  const isAvailable = report.available && unavailable !== true;

  const formatted = isAvailable
    ? formatFigure(report.value, { format: resolvedFormat, precision })
    : null;

  // Belt and braces: an available value the formatter cannot render — a text value in a
  // numeric format — falls to the marker rather than to `null` children.
  const shows = formatted !== null && formatted !== undefined;

  // `report.reason` first: `readReported` has already preferred the `Reported<T>`
  // unavailable arm's own reason, which was written by whoever knew why the field is
  // missing, and has already fallen back to `unavailableReason`. The remaining case is
  // `unavailable` set over a value that is present, where the prop is the only reason
  // there is.
  const reason = report.reason
    ?? (hasText(unavailableReason) ? unavailableReason.trim() : UNREPORTED_REASON);

  // Requirement 1.5: no `state`, no colour. `statusToken` is only consulted when the
  // caller named a state, so there is no path here that assigns a hue by default.
  const hue = hasText(state) ? statusToken(state) : null;

  const numeric = NUMERIC_FORMATS.includes(resolvedFormat);
  const figureClasses = [
    tierClasses(resolvedTier),
    numeric ? 'font-mono tabular-nums' : '',
    hue ? '' : 'text-content-primary',
  ].filter(Boolean).join(' ');

  return (
    <div
      data-metric-tier={resolvedTier}
      data-metric-format={resolvedFormat}
      data-metric-available={shows ? 'true' : 'false'}
      data-metric-state={hue ? hue.group : undefined}
      className={`flex min-w-0 flex-col gap-1 ${className}`.trim()}
      {...rest}
    >
      <span
        className={`${labelClasses(resolvedTier)} uppercase tracking-wide text-content-secondary${hasText(hint) ? ' cursor-help' : ''}`}
        title={hasText(hint) ? hint : undefined}
        aria-describedby={hasText(hint) ? hintId : undefined}
      >
        {label}
      </span>
      {hasText(hint) ? <span id={hintId} className="sr-only">{hint}</span> : null}

      <span className="flex min-w-0 items-baseline gap-1">
        {shows ? (
          <span className={figureClasses} style={hue ? { color: hue.fg } : undefined}>
            {formatted}
          </span>
        ) : (
          <NotAvailableMarker label={label} reason={reason} className={tierClasses(resolvedTier)} />
        )}
        {/* The unit is only rendered beside a real figure. `— USDT` claims the missing
            value is a USDT amount, which is a small fabrication of exactly the kind
            Requirement 14.5 is about. */}
        {shows && hasText(unit) ? (
          <span className="text-small font-normal text-content-secondary">{unit}</span>
        ) : null}
      </span>
    </div>
  );
}

export default Metric;
