/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/PnLDisplay — a signed figure, in mono, with zero rendered as zero
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.4. design.md §5.1, §5.3. Requirements 1.4, 1.5, 14.5.
 *
 * Replaces `PnLBadge` from `components/ui-legacy/primitives.jsx`, which is wrong in
 * three separate ways that are each worth naming because each one is a decision this
 * component reverses:
 *
 *   1. **`value >= 0` colours zero as profit.** A position that has made nothing has
 *      not made a profit. Rendered in profit green with a `+` in front, a flat account
 *      reads as a winning one, and it does so on every panel that shows P&L. Colour
 *      here comes from `pnlToken`, where **zero is neutral** — that decision lives in
 *      `design/semantic.js` and this component only consumes it.
 *   2. **`textShadow: glow` on every figure.** A permanent halo on the number a trader
 *      looks at most, spending the emphasis budget on something that is not a state, a
 *      risk or a required action (Requirement 1.5).
 *   3. **`animation: pulseGlow 2s infinite`, plus an injected `<style>` element.** Every
 *      P&L figure on screen pulsed forever, and each one wrote its own `@keyframes`
 *      into the document. There is no condition that animation reports — it fires for
 *      any non-zero value, so it is motion carrying no information, on the element
 *      least in need of decoration.
 *
 * None of the three is present here. There is no `<style>` child, no `animation`, no
 * `textShadow`, and no code path that can produce them.
 *
 * WHAT IT DOES CARRY
 * ------------------
 * `--font-mono` with `font-variant-numeric: tabular-nums`, so a column of P&L figures
 * has its decimal points in a line and a changing figure does not shift the ones
 * beside it. An explicit sign, because `1,200` and `-1,200` differ by one glyph that is
 * easy to miss and `+1,200` does not. And the not-available marker from `ds/Metric`
 * when there is nothing to report, so an absent P&L is never a zero (Requirement 14.5).
 *
 * IT TAKES NO LABEL OF ITS OWN
 * ---------------------------
 * design.md §5.1 gives it no `label`: it is a figure that sits inside something already
 * labelled — a `DataTable` cell under a "P&L" header, or beside a `Metric`. The
 * optional `label` prop exists only to name the not-available marker, which needs an
 * accessible name whether or not the surrounding column provides one, and it defaults
 * to `P&L`.
 *
 * `row` and `column` are absorbed and ignored. design.md §11.3's worked example is
 * literally `render: PnLDisplay`, and `ds/DataTable` renders a column's `render` as
 * `<Render value={…} row={…} column={…} />`. Without absorbing them here, the two
 * objects would be forwarded onto the DOM node by the `...rest` spread that §5 asks
 * every primitive for.
 */

import { pnlToken } from '../../design/semantic';
import { readReported, UNREPORTED_REASON } from '../../design/reported';

import { hasText } from './devAssert';
import { formatFigure, tierClasses, NotAvailableMarker } from './Metric';

/** Percent decimals. See the docblock on `percentValue` for why this one rounds. */
const PERCENT_PRECISION = 2;

/**
 * `$`, `₹`, `€` sit in front of the digits; `USDT`, `INR`, `BTC` sit after them.
 *
 * One rule rather than a per-call-site decision, and it is decided by whether the
 * string contains a letter: a symbol is a prefix and a code is a suffix, which is how
 * both are conventionally written. `-$1,200.00` keeps the sign outermost, where a
 * trader looks for it.
 */
function isSymbolCurrency(currency) {
  return !/\p{L}/u.test(currency);
}

/**
 * A signed P&L figure.
 *
 * @param {Object} props
 * @param {number|string|null|undefined|{available: boolean}} [props.value] A raw value
 *   or a `Reported<T>`. Absent, `NaN` and the unavailable arm render the marker.
 * @param {string} [props.currency] `'USDT'` renders after the figure, `'$'` before it.
 * @param {boolean} [props.showSign] Default `true`. Setting it `false` suppresses the
 *   `+` on a gain only — a loss keeps its `-`, which is not decoration.
 * @param {boolean} [props.showPercent] Renders `percentValue` in parentheses after the
 *   amount.
 * @param {number|string|null|undefined|{available: boolean}} [props.percentValue] The
 *   change as a **percentage**, not a fraction: `2.4` renders `+2.40%`. Nothing here
 *   multiplies by 100 — see `ds/Metric`'s `formatFigure`.
 * @param {1|2|3} [props.tier] Type scale. Default 3, the in-table size.
 * @param {number} [props.precision] Decimal places for the amount. Omitted means do
 *   not round, so a server string of `"1.50"` keeps its trailing zero.
 * @param {boolean} [props.unavailable] Renders the marker whatever `value` holds.
 * @param {string} [props.unavailableReason] Why (Requirement 19.3).
 * @param {string} [props.label] Names the marker. Defaults to `P&L`.
 * @param {string} [props.className]
 */
export function PnLDisplay({
  value,
  currency,
  showSign = true,
  showPercent = false,
  percentValue,
  tier = 3,
  precision,
  unavailable = false,
  unavailableReason,
  label = 'P&L',
  className = '',
  // Absorbed from `ds/DataTable`'s `<Render value row column />` call so they cannot
  // reach the DOM node through `...rest`. Unused by design.
  // eslint-disable-next-line no-unused-vars
  row,
  // eslint-disable-next-line no-unused-vars
  column,
  ...rest
}) {
  const report = readReported(value, unavailableReason);
  const isAvailable = report.available && unavailable !== true;

  // The hue is chosen from the NUMBER, not from the raw value: a server that sends
  // `"-12.50"` as a string still describes a loss, and `pnlToken` is correctly strict
  // about only accepting a finite number. Coercing here rather than loosening the
  // mapping keeps the "zero is neutral" decision in one place. A non-numeric or absent
  // value gives `null`, which `pnlToken` reads as neutral — an unreadable figure is not
  // a gain.
  const numeric = isAvailable ? Number(report.value) : Number.NaN;
  const signed = Number.isFinite(numeric) ? numeric : null;
  const { group, fg } = pnlToken(signed);

  const amount = isAvailable ? formatFigure(report.value, { format: 'number', precision }) : null;
  const shows = amount !== null && amount !== undefined;

  // `groupDecimal` has already produced the `-`; only the `+` is this component's to
  // add, and only for a value that is actually above zero. Zero gets no sign, because
  // `+0.00` is a claim about direction that a flat figure does not support.
  const sign = showSign !== false && signed !== null && signed > 0 ? '+' : '';

  const symbol = hasText(currency) && isSymbolCurrency(currency.trim()) ? currency.trim() : null;
  const code = hasText(currency) && !symbol ? currency.trim() : null;

  const percentReport = readReported(percentValue);
  const percent = percentReport.available
    ? formatFigure(percentReport.value, { format: 'percent', precision: PERCENT_PRECISION })
    : null;
  const percentNumeric = Number(percentReport.value);
  const percentSign = percent !== null && Number.isFinite(percentNumeric) && percentNumeric > 0 ? '+' : '';

  return (
    <span
      data-pnl-group={group}
      data-pnl-sign={signed === null ? 'unknown' : Math.sign(signed)}
      data-pnl-available={shows ? 'true' : 'false'}
      className={`inline-flex items-baseline gap-1 font-mono tabular-nums ${tierClasses(tier)} ${className}`.trim()}
      // The only style this component sets. No shadow, no animation, no transition.
      style={{ color: fg }}
      {...rest}
    >
      {shows ? (
        <span>
          {sign}
          {symbol}
          {amount}
        </span>
      ) : (
        <NotAvailableMarker
          label={label}
          reason={report.reason ?? (hasText(unavailableReason) ? unavailableReason.trim() : UNREPORTED_REASON)}
        />
      )}
      {/* The currency code is only rendered beside a real figure: `— USDT` asserts that
          the value we do not have is a USDT amount. */}
      {shows && code ? <span className="text-small font-normal text-content-secondary">{code}</span> : null}
      {shows && showPercent === true ? (
        <span className="text-small font-normal">
          {'('}
          {percent === null ? (
            // A declared percentage that was not reported renders the marker rather
            // than being dropped: the parentheses are already on screen, and closing
            // them over nothing reads as a rendering fault.
            <NotAvailableMarker label={`${label} change`} reason={percentReport.reason} />
          ) : (
            `${percentSign}${percent}`
          )}
          {')'}
        </span>
      ) : null}
    </span>
  );
}

export default PnLDisplay;
