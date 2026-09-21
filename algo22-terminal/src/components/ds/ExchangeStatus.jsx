/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/ExchangeStatus — one venue: connected, how fast, and may it trade
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.6. design.md §5.1, §5.3, §3 (Dashboard tier 2).
 * Requirements 1.2, 1.4, 1.5, 14.5.
 *
 * WHAT IT ABSORBS
 * --------------
 * `pages/Dashboard.jsx`'s zone-6 blocks, which are inline and duplicated:
 *
 *   * The per-venue row in "Exchange Venues": a 7px dot coloured
 *     `ex.status === "connected" ? "#10b981" : "#ef4444"`, the venue id, the latency,
 *     and a chip repeating `ex.status`. Both the dot and the chip are off-palette
 *     literals — #10b981 and #ef4444 are Tailwind defaults with no token behind them —
 *     and the dot carries the connection state in hue alone.
 *   * The "Exchange API Latency" row in the system-health panel, which reads
 *     `systemHealth.exchange_api_latency_ms` and shows
 *     `"{n} ms ({status || "optimal"})"`. That `|| "optimal"` is a fabricated grade: it
 *     fires exactly when the server sent no grade.
 *
 * The fields are the ones the server actually sends.
 * `dashboard_aggregation_service.get_exchange_health` returns per venue
 * `{exchange_id, status, latency_ms, last_sync}` and, at the top level of the same
 * payload, one `can_trade` for the account. Nothing else exists to read, which is why
 * this component's API is five props and not fifteen.
 *
 * `latencyMs == null` IS NOT `0 ms` (Requirement 14.5)
 * --------------------------------------------------
 * `dashboard_aggregation_service.get_health_status` looks for a measured round trip in
 * Redis and returns `"exchange_api_latency_ms": None` with
 * `"exchange_api_latency_status": "unavailable"` when it finds none. `null` there means
 * NOBODY HAS MEASURED. Rendering that as `0 ms` would report the fastest possible link
 * on the strength of no evidence, which is the substitution Requirement 14.5 forbids —
 * and it is a substitution a trader would act on, because latency is how they decide
 * whether a venue is safe to send an order to right now.
 *
 * So a missing, non-numeric, non-finite or negative latency renders the not-available
 * marker with the reason attached. A server-reported `0` is a different thing: it is a
 * measurement (`get_health_status` does `int(measured)`, so a sub-millisecond round trip
 * legitimately arrives as `0`), and it renders as `0 ms`. Suppressing a real reading
 * would be its own fabrication, in the other direction.
 *
 * RECORDED, NOT FIXED — the per-venue `latency_ms` is a constant. `get_exchange_health`
 * writes `"latency_ms": 35` for every connected venue, from no measurement at all. This
 * component cannot tell a fabricated 35 from a measured one; the honest reading of that
 * field belongs to whoever owns the aggregation service, and it is worth fixing there,
 * because a hardcoded latency is exactly what Requirement 14.5 rules out at source.
 *
 * `connectionState == null` IS NOT "DISCONNECTED"
 * ---------------------------------------------
 * Claiming disconnected is alarmist and claiming connected is dangerous, so an absent
 * state says "Connection not reported" in the neutral group — the same argument
 * `design/semantic.js` makes for refusing to guess a trading environment.
 *
 * `canTrade` — `undefined` AND `null` MEAN DIFFERENT THINGS
 * -------------------------------------------------------
 * `Panel`'s convention, for the same reason. `undefined` (prop omitted) renders no
 * capability chip, because the caller is not showing that dimension. `null` renders
 * "Trade permission not reported", because the caller IS showing it and the server did
 * not answer. `false` renders "Cannot trade", which is the one arm a trader must never
 * miss, and it is `=== false` rather than falsy so a `0` or a `""` cannot produce it.
 */

import { memo } from 'react';

import { NotAvailable, StatusBadge, humaniseState } from './StatusBadge';
import { assertContract, hasText } from './devAssert';

const CONNECTION_UNREPORTED_LABEL = 'Connection not reported';
const LATENCY_UNREPORTED_REASON =
  'No round-trip latency has been measured for this venue, so none is reported. It is not a '
  + 'measurement of zero.';
const TRADE_UNREPORTED_LABEL = 'Trade permission not reported';

/**
 * Whether a reported latency is a reading at all.
 *
 * `0` passes: see the module docblock. A negative value does not — a round trip cannot
 * take less than no time, so a negative figure is a broken clock rather than a fast link.
 *
 * @param {unknown} latencyMs
 * @returns {boolean}
 */
export function isMeasuredLatency(latencyMs) {
  return typeof latencyMs === 'number' && Number.isFinite(latencyMs) && latencyMs >= 0;
}

/**
 * One exchange venue's health.
 *
 * @param {Object} props
 * @param {string} props.exchange REQUIRED — the venue, as the server names it
 *   (`exchange_id`). A row that does not say which venue it describes is not reportable.
 * @param {string} [props.connectionState] The server's `status`, verbatim. Absent renders
 *   "Connection not reported" — never "disconnected".
 * @param {number} [props.latencyMs] Milliseconds, as measured. `null` renders the
 *   not-available marker, never `0 ms` (Requirement 14.5).
 * @param {boolean} [props.canTrade] The server's `can_trade`. Omit for no chip; `null`
 *   for "not reported"; `false` for "Cannot trade".
 * @param {string} [props.accountLabel] Which account, already masked by the caller
 *   (`"…4821"`). Never a full account identifier.
 * @param {string} [props.className]
 */
export const ExchangeStatus = memo(function ExchangeStatus({
  exchange,
  connectionState,
  latencyMs,
  // No default: `undefined` and `null` are different answers here. See the docblock.
  canTrade,
  accountLabel,
  className = '',
  ...rest
}) {
  assertContract(
    hasText(exchange),
    'ExchangeStatus: `exchange` is required — it is which venue this row is about. Pass the '
      + "server's `exchange_id`. A health row that does not name its venue tells a trader "
      + 'nothing they can act on.',
  );

  const measured = isMeasuredLatency(latencyMs);
  const showsCanTrade = canTrade !== undefined;

  return (
    <div
      data-exchange={hasText(exchange) ? exchange : undefined}
      data-connection-state={hasText(connectionState) ? connectionState : 'unreported'}
      data-latency-reported={measured ? 'true' : 'false'}
      className={`flex min-w-0 flex-wrap items-center justify-between gap-2 ${className}`.trim()}
      {...rest}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate font-mono text-body font-semibold uppercase text-content-primary">
          <span className="sr-only">Exchange: </span>
          {hasText(exchange) ? exchange : 'Unnamed venue'}
        </span>
        {hasText(accountLabel) ? (
          <span className="truncate font-mono text-micro text-content-secondary">
            <span className="sr-only">Account: </span>
            {accountLabel}
          </span>
        ) : null}
      </div>

      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {/* Latency first: it is the figure that changes, and it is the one the legacy
            blocks got wrong. Text either way — a number with its unit, or an em-dash
            whose accessible name says it was never measured. */}
        <span className="inline-flex shrink-0 items-center gap-1 font-mono text-micro tabular-nums text-content-secondary">
          <span className="sr-only">Latency: </span>
          {measured ? `${latencyMs} ms` : (
            <NotAvailable label="Latency" reason={LATENCY_UNREPORTED_REASON} />
          )}
        </span>

        {/* The dot lives on the badge, beside the state's own word, instead of standing
            alone as a 7px hue the way Dashboard's inline row does. */}
        <StatusBadge
          state={hasText(connectionState) ? connectionState : 'unknown'}
          label={hasText(connectionState) ? humaniseState(connectionState) : CONNECTION_UNREPORTED_LABEL}
          dot
          size="sm"
        />

        {showsCanTrade ? (
          <StatusBadge
            state={canTrade === true ? 'ok' : canTrade === false ? 'blocked' : 'unknown'}
            label={
              canTrade === true
                ? 'Can trade'
                : canTrade === false
                  ? 'Cannot trade'
                  : TRADE_UNREPORTED_LABEL
            }
            size="sm"
          />
        ) : null}
      </div>
    </div>
  );
});

export default ExchangeStatus;
