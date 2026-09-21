/**
 * ═══════════════════════════════════════════════════════════════════════════
 * components/trading/OrdersPanel — the order surface, and how an order reads
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 20.2. design.md §7.5, §7.8 (4), §13.2. Requirements 7.3, 7.4,
 * 7.5, 12.1, 12.2, 14.5, 19.1.
 *
 * The order half of §7.8 (4)'s three shared surfaces: one panel, parameterised by
 * environment, consumed by `pages/LiveTrading.jsx` at task 20.2 and by
 * `pages/PaperTrading.jsx` at task 25.2. The reasoning for the shape — environment as a prop,
 * data as props, the subscription at the leaf, the chip on the header — is written once in
 * `./PositionsPanel.jsx`'s docblock and is not restated here. What is particular to orders is
 * below.
 *
 * IT OWNS HOW AN ORDER READS, AND THAT IS WHY {@link orderReadingOf} IS EXPORTED
 * -----------------------------------------------------------------------------
 * "buy 0.1 BTC/USDT" is composed from three fields of ONE order row, and two things compose
 * it: the page's REST projection of the venue's open orders, and this panel's leaf when a
 * frame supersedes that projection. Two copies of that composition would be two chances for
 * the order a trader is shown and the order a push reports to read differently — the same
 * argument `pages/LiveTrading.jsx` makes about keeping one filter for the row a cancel
 * addresses. So the composition lives with the component that owns the surface, and the page
 * imports it.
 *
 * A PUSH SUPERSEDES THE READING ONLY WHEN THE FRAME SAYS THE ORDER IS OPEN
 * -----------------------------------------------------------------------
 * The slot is "the newest order the venue reports as still OPEN". The `orders` channel
 * carries the whole order lifecycle — placements, fills, cancellations — so a frame is read
 * only when it states an open order state, and declined otherwise. A filled order rendered
 * under "Latest open order" would be a claim about resting capital that is no longer true,
 * and the declining arm leaves the read's own reading standing rather than blanking it
 * (Requirement 14.5).
 *
 * NO MUTATION IS CALLED FROM HERE (Property 13, Requirement 19.1)
 * --------------------------------------------------------------
 * The cancel controls this panel shows in its header are built by the caller and arrive as
 * `actions`. This file imports no API module, so it cannot cancel an order, cannot place one,
 * and adds no manual-order affordance of any kind — `POST /api/orders/execute` stays behind
 * its algo-only guard. Property 13's structural rule is unaffected by the extraction: the
 * caller's `request…` handler still only sets dialog state, and its `handleConfirm…` is still
 * the sole caller of the mutation.
 *
 * @module components/trading/OrdersPanel
 */

import { memo, useCallback } from "react";

import { Metric } from "../ds/Metric";
import { Panel } from "../ds/Panel";
import { TradingEnvironmentBadge } from "../ds/TradingEnvironmentBadge";
import { TIER_ATTRIBUTE, TIER_PAGE_ATTRIBUTE } from "../../design/pageHierarchy";
import { available, readReported, unavailable } from "../../design/reported";
import { useLiveChannel } from "../../hooks/useLiveChannel";
import { frameText, isFreshFrame } from "./liveFrame";

/** `orders` — the order lifecycle stream (`wsClient.subscribeOrders`'s channel). */
const ORDERS_CHANNEL = "orders";

/** The parts of one order row that make up the reading, in the order they are read. */
const ORDER_PARTS = Object.freeze(["side", "amount", "symbol"]);

/**
 * The order state that means "still resting at the venue".
 *
 * ccxt's own word for an order the exchange still holds, and the only one accepted. A wider
 * set — anything that is not `closed` or `canceled` — would eventually read a state this
 * client has never seen as open, and the cost of that is a filled order shown as resting.
 * A frame whose state is anything else is declined, which leaves the READ's reading standing.
 */
const OPEN_ORDER_STATE = "open";

/** The keys an order frame may report its state under. Three spellings, no guessing. */
const ORDER_STATE_KEYS = Object.freeze(["status", "order_state", "state"]);

/**
 * One order row → `"buy 0.1 BTC/USDT"`, or `null` when it reports none of the three.
 *
 * All three parts come off the SAME row, so nothing here is a pair assembled across rows. A
 * row reporting two of the three renders those two: a partial reading of one order is still a
 * true one, and it is a better answer than the marker.
 *
 * Exported because the page's REST projection and this file's leaf must read an order
 * identically — see the module docblock.
 *
 * @param {unknown} row One ccxt order, or one `orders` frame.
 * @returns {string|null}
 */
export const orderReadingOf = (row) => {
  if (!row || typeof row !== "object") return null;

  const parts = [];
  for (const key of ORDER_PARTS) {
    const part = frameText(row[key]);
    if (part !== null) parts.push(part);
  }
  return parts.length === 0 ? null : parts.join(" ");
};

/** The state a frame reports for its order, or `null`. */
const orderStateOf = (frame) => {
  for (const key of ORDER_STATE_KEYS) {
    const value = frameText(frame[key]);
    if (value !== null) return value;
  }
  return null;
};

/**
 * The order slot, live off `orders`.
 *
 * `memo` with primitive props only, for `./PositionsPanel.jsx`'s reason. The filter is the
 * VENUE rather than a market: open orders are held per venue and the slot is the account's
 * newest at the one venue the caller named, across every market — so filtering by symbol
 * here would silently narrow the slot to one market's orders.
 */
const LiveOrderFigure = memo(function LiveOrderFigure({
  label,
  tier,
  format,
  hint,
  region,
  venue,
  environment,
  syncedAt,
  seeded,
  reason,
}) {
  const pick = useCallback(
    (frame) => {
      if (!isFreshFrame(frame, environment, syncedAt)) return undefined;

      // A frame that names a different venue is another exchange's order book.
      const claimed = frameText(frame.exchange_id);
      if (claimed !== null && venue !== null && claimed.toLowerCase() !== venue.toLowerCase()) {
        return undefined;
      }

      const state = orderStateOf(frame);
      if (state === null || state.toLowerCase() !== OPEN_ORDER_STATE) return undefined;

      // `null` from the composition means the frame described no order at all. Declining is
      // the right reading of that; `null` would blank a figure the read reported.
      return orderReadingOf(frame) ?? undefined;
    },
    [environment, syncedAt, venue],
  );

  // No venue means the caller issued no open-orders read either, so there is nothing for a
  // frame to supersede and no subscription is taken.
  const channel = venue === null ? null : ORDERS_CHANNEL;
  const live = useLiveChannel(channel, pick, undefined);

  const value = live === undefined
    ? (seeded === null ? unavailable(reason) : available(seeded))
    : available(live);

  return (
    <Metric
      tier={tier}
      label={label}
      value={value}
      format={format}
      hint={hint}
      className="flex-1"
      data-region={region}
    />
  );
});

/**
 * The order panel, badged by environment.
 *
 * `money` is declared: an order is one of `ds/Panel`'s `MONEY_CONTENT` entries, so
 * `ds/Panel` refuses in development to render this panel without an environment decision
 * (Requirements 7.4, 12.2). `null` is an accepted decision and renders
 * `ENVIRONMENT UNCONFIRMED`.
 *
 * `liveSlot` names which of the given slots the `orders` channel may supersede. It is a
 * prop rather than a constant because the two consumers project a different field into it —
 * Live Trading's declared `latestOrder`, Paper Trading's own open-order reading — and a slot
 * key hardcoded here would make one of them un-live. Every other slot renders as a plain
 * `ds/Metric` and subscribes to nothing.
 *
 * @param {Object} props
 * @param {'LIVE'|'PAPER'|'BACKTEST'|null} props.environment The ledger this panel shows.
 * @param {string} props.title
 * @param {string} [props.page] `pageHierarchy` page id → the container's `data-page`.
 * @param {number} [props.tier] The declared tier → `data-page-tier`.
 * @param {Array<{key: string, label: string, value: unknown, format?: string,
 *   hint?: string}>} props.slots The caller's projected figures, in render order.
 * @param {string|null} [props.liveSlot] The slot key an `orders` frame may supersede.
 * @param {string|null} [props.venue] The exchange the orders were read from, which is what
 *   the leaf filters on. `null` subscribes to nothing.
 * @param {number} [props.syncedAt] When the read that seeded these slots last answered.
 * @param {string} [props.state] A `PANEL_STATES` value.
 * @param {Object} [props.loading] `ds/Panel`'s loading descriptor.
 * @param {Object} [props.error] `ds/Panel`'s error descriptor.
 * @param {import('react').ReactNode} [props.actions] Controls for the header. A destructive
 *   one arrives already built by the caller; this panel calls no mutation.
 * @param {string} [props.region] `data-region` for the panel root.
 * @param {string} [props.className]
 */
export function OrdersPanel({
  environment,
  title,
  page,
  tier,
  slots = [],
  liveSlot = null,
  venue = null,
  syncedAt,
  state,
  loading,
  error,
  actions = null,
  region,
  className = "",
}) {
  return (
    <Panel
      title={title}
      money
      environment={environment}
      state={state}
      loading={loading}
      error={error}
      actions={(
        <div className="flex min-w-0 items-center gap-2">
          {/* §8.2's chip: a live order must be unmistakable from a paper one, and the four
              axes of difference are the badge's, not this file's. It does not announce —
              the page's own strip is the announcing instance. */}
          <TradingEnvironmentBadge variant="chip" environment={environment} />
          {actions}
        </div>
      )}
      className={className}
      data-trading-panel="orders"
      data-region={region}
    >
      {/* One row, `flex-1` from a zero basis. Not `grid-cols-*` above 4 — this build emits
          no bare utility for those, so it would compile to nothing (design.md §1.2).

          `page` decides whether this panel owns the tier container; a page whose tier spans
          more than one panel supplies the container and passes `tier` alone. See
          `./PositionsPanel.jsx`. */}
      <div
        {...(page === undefined
          ? {}
          : { [TIER_PAGE_ATTRIBUTE]: page, [TIER_ATTRIBUTE]: tier })}
        className="flex min-w-0 items-start gap-4"
      >
        {slots.map((slot) => {
          if (slot.key !== liveSlot) {
            return (
              <Metric
                key={slot.key}
                tier={tier}
                label={slot.label}
                value={slot.value}
                format={slot.format}
                hint={slot.hint}
                className="flex-1"
                data-region={slot.key}
              />
            );
          }

          const read = readReported(slot.value);
          return (
            <LiveOrderFigure
              key={slot.key}
              label={slot.label}
              tier={tier}
              format={slot.format}
              hint={slot.hint}
              region={slot.key}
              venue={venue}
              environment={environment}
              syncedAt={syncedAt}
              seeded={read.available ? read.value : null}
              reason={read.reason ?? undefined}
            />
          );
        })}
      </div>
    </Panel>
  );
}
