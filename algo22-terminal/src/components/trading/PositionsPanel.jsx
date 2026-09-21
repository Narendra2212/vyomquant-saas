/**
 * ═══════════════════════════════════════════════════════════════════════════
 * components/trading/PositionsPanel — the position, P&L and exposure surface
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 20.2. design.md §7.5, §7.8 (4), §13.2. Requirements 7.2, 7.4,
 * 7.5, 12.1, 12.2, 14.5.
 *
 * WHY THIS IS A COMPONENT AND NOT A SECTION OF A PAGE
 * --------------------------------------------------
 * Requirement 12.1 asks Paper Trading to use "the same visual language" as Live Trading, and
 * §7.8 (4) reads that structurally rather than stylistically: the position, order and
 * execution surfaces are **the same components**, parameterised by environment, so the two
 * pages cannot drift into two vocabularies for one thing. Task 20.2 moves this one out of
 * `pages/LiveTrading.jsx`; task 25.2 points `pages/PaperTrading.jsx` at it.
 *
 * `environment` is therefore a PROP and never a constant. A hardcoded `"LIVE"` here would
 * make the second consumer impossible, and — worse — would badge a paper surface as live the
 * first time someone reused the file. The prop takes `ds/TradingEnvironmentBadge`'s three
 * ids or `null` for a record the server did not label (§8.2); it is not defaulted, because
 * defaulting to LIVE is alarmist and defaulting to PAPER is dangerous.
 *
 * IT OWNS THE RENDERING AND THE SUBSCRIPTION. IT OWNS NO READ.
 * -----------------------------------------------------------
 * Every figure arrives as a prop: `slots` is the page's already-projected list of declared
 * fields, each carrying its `pageFields` label and a `Reported<T>` from the page's own
 * `usePanelState` read. This panel issues no request, holds no `usePanelState`, and knows no
 * API path. The page still owns what is true; this owns how it is said, and one thing more —
 * the leaf subscription below.
 *
 * SUBSCRIBING AT THE LEAF IS THE WHOLE POINT (§13.2b)
 * --------------------------------------------------
 * A `pnl` tick handled at a page root re-renders the page. Handled here — in
 * {@link LivePositionFigure}, a `memo` with primitive props only — it re-renders one text
 * node, and `useLiveChannel` compares the selected slice with `Object.is` before it enqueues
 * anything, so a tick for another symbol does not even schedule a render. That is what makes
 * Requirement 7.5's bound hold with no polling: the figure is superseded by a push, not by an
 * interval, so there is no interval to tune and nothing to tear down and recreate.
 *
 * A PUSH MAY SUPERSEDE A FIGURE. IT MAY NEVER CREATE ONE.
 * ------------------------------------------------------
 * The read seeds every slot; a frame that passes {@link isFreshFrame} and names this
 * position's symbol supersedes the ONE slot it carries a field for. Two rules keep that from
 * becoming a fabrication (Requirement 14.5):
 *
 *   * A slot whose seeded value is the not-available marker subscribes to nothing. There is
 *     no symbol to filter on when the positions read reported none — and on the degraded arm
 *     (BC-2) there is deliberately no position at all, so a tick must not put a figure where
 *     the server's own failure sentence belongs.
 *   * A selector returns `undefined` to DECLINE a frame and never `null`, so no push can talk
 *     a slot into the marker either. `useLiveChannel` reads `undefined` as "nothing for me in
 *     this frame" and leaves the previous value standing.
 *
 * @module components/trading/PositionsPanel
 */

import { memo, useCallback } from "react";

import { Alert } from "../ds/Alert";
import { Metric } from "../ds/Metric";
import { Panel } from "../ds/Panel";
import { TradingEnvironmentBadge } from "../ds/TradingEnvironmentBadge";
import { TIER_ATTRIBUTE, TIER_PAGE_ATTRIBUTE } from "../../design/pageHierarchy";
import { available, readReported, unavailable } from "../../design/reported";
import { useLiveChannel } from "../../hooks/useLiveChannel";
import { frameNumber, frameText, isFreshFrame } from "./liveFrame";

/**
 * `pnl` — the per-symbol P&L stream (`wsClient.subscribePnL`'s channel).
 *
 * `positions` carries the structural question — WHICH positions exist — and that is a REST
 * read's answer, not a tick's: a frame that could add or remove a slot would be re-deriving
 * the page's projection in a leaf. `pnl` carries the quantities, which is what a slot shows.
 */
const PNL_CHANNEL = "pnl";

/**
 * Which declared slot a `pnl` frame may supersede, and with which of its fields.
 *
 * Declared here rather than passed in, because it is a fact about the CHANNEL — the frame's
 * field names — and not about a page's layout. A slot key absent from this map renders as a
 * plain `ds/Metric` and subscribes to nothing, which is every slot the socket says nothing
 * about: an exposure, a risk word and an account-wide realised P&L are not on this stream.
 */
const LIVE_FIELD_BY_SLOT = Object.freeze({
  unrealisedPnl: "unrealized_pnl",
});

/**
 * ONE slot's figure, live off `pnl` and filtered to this position's symbol.
 *
 * §13.2(b)'s worked example, in the element that renders the figure. Every prop is a
 * primitive so the `memo` actually holds: the panel re-rendering for an unrelated reason
 * re-renders none of these, and a frame re-renders exactly the one whose selector answered.
 * The seeded `Reported<T>` is split into `seeded` + `reason` for that reason — a frozen union
 * object would be a new identity on every projection and defeat the comparison.
 *
 * `seeded === null` IS the not-available arm: `readReported` has already resolved a
 * `{available: true, value: null}` claim to unavailable, so a readable seed can never be
 * `null` and the two states cannot be confused.
 */
const LivePositionFigure = memo(function LivePositionFigure({
  label,
  tier,
  format,
  precision,
  hint,
  region,
  symbol,
  environment,
  syncedAt,
  valueField,
  seeded,
  reason,
}) {
  const pick = useCallback(
    (frame) => {
      if (!isFreshFrame(frame, environment, syncedAt)) return undefined;
      if (frameText(frame.symbol) !== symbol) return undefined;
      // `frameNumber` answers `null` for a frame that carried no such field, and `null` is
      // how the hook is told a figure is GONE. Declining is the right reading of a frame
      // that simply did not carry it.
      return frameNumber(frame[valueField]) ?? undefined;
    },
    [environment, syncedAt, symbol, valueField],
  );

  // No symbol to filter on, or nothing seeded to supersede: no subscription at all.
  // `useLiveChannel` treats a falsy channel as no subscription and returns `initial`.
  const channel = symbol === null || seeded === null ? null : PNL_CHANNEL;
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
      precision={precision}
      hint={hint}
      className="flex-1"
      data-region={region}
    />
  );
});

/**
 * The position / P&L / exposure panel, badged by environment.
 *
 * `money` is declared unconditionally: every slot this panel is given is a position, a P&L or
 * an exposure, which is three entries of `ds/Panel`'s `MONEY_CONTENT`. That declaration is
 * what makes Requirement 7.4 structural rather than remembered — `ds/Panel` throws in
 * development for a `money` panel whose `environment` was OMITTED, so this component cannot
 * be consumed without its caller deciding. `null` is an accepted decision and renders
 * `ENVIRONMENT UNCONFIRMED`.
 *
 * @param {Object} props
 * @param {'LIVE'|'PAPER'|'BACKTEST'|null} props.environment The ledger this panel shows.
 *   Required by `ds/Panel`'s money assertion; `null` means the server did not label it.
 * @param {string} props.title
 * @param {string} [props.page] The `pageHierarchy` page id, for the tier container's
 *   `data-page` attribute.
 * @param {number} [props.tier] The declared tier, for `data-page-tier`. Omitted means this
 *   panel's figures are not part of a page's tier ordering (Property 4).
 * @param {Array<{key: string, label: string, value: unknown, format?: string,
 *   precision?: number, hint?: string}>} props.slots The page's projected figures, in the
 *   order they are to be rendered. `value` is a `Reported<T>` or a raw value.
 * @param {string} [props.state] A `PANEL_STATES` value, passed through to `ds/Panel`.
 * @param {Object} [props.loading] `ds/Panel`'s loading descriptor.
 * @param {Object} [props.error] `ds/Panel`'s error descriptor.
 * @param {string|null} [props.notice] A condition to state ABOVE the figures — the server's
 *   own account of a degraded positions read (BC-2). Rendered verbatim.
 * @param {string} [props.noticeTitle]
 * @param {Function} [props.onRetry] Offered by the notice. Never a read this panel owns.
 * @param {import('react').ReactNode} [props.actions] Extra controls for the panel header.
 *   A destructive action arrives as a callback on a control the CALLER built, so this panel
 *   never calls a mutation (Property 13).
 * @param {string|null} [props.symbol] The market the seeded position is in, which is what the
 *   leaf subscription filters on. `null` subscribes to nothing.
 * @param {number} [props.syncedAt] When the read that seeded these slots last answered.
 * @param {string} [props.region] `data-region` for the panel root.
 * @param {string} [props.className]
 */
export function PositionsPanel({
  environment,
  title,
  page,
  tier,
  slots = [],
  state,
  loading,
  error,
  notice = null,
  noticeTitle,
  onRetry,
  actions = null,
  symbol = null,
  syncedAt,
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
          {/* §8.2's chip on a surface showing position and P&L data (Requirements 7.4,
              12.2). It does not announce: a page states its ledger once, in its own strip,
              and a second announcement per panel would make a screen reader read the
              environment on every panel it entered. */}
          <TradingEnvironmentBadge variant="chip" environment={environment} />
          {actions}
        </div>
      )}
      className={className}
      data-trading-panel="positions"
      data-region={region}
    >
      <div className="flex min-w-0 flex-col gap-4">
        {/* The condition, ABOVE the row it explains, verbatim: the server knows which
            environment failed and why, and a figure's marker carries the same sentence.
            Never a flat position in its place (Requirement 14.5). */}
        {notice === null || notice === undefined ? null : (
          <Alert
            severity="warning"
            title={noticeTitle}
            action={onRetry ? { label: "Try again", onClick: onRetry } : undefined}
            data-positions-read="degraded"
          >
            {notice}
          </Alert>
        )}

        {/* ONE container, one row, `flex-1` from a zero basis so the slots are equally
            weighted — which is what "one row of equally-weighted figures" means. Not
            `grid-cols-*` above 4: Tailwind v4 emits a utility only where the source asks
            for it and this build's stylesheet carries no bare `grid-cols-5` and up, so it
            would compile to nothing (design.md §1.2).

            `page` is what decides whether this panel OWNS the tier container. A page whose
            tier is split across more than one panel supplies the container itself and passes
            `tier` alone, so that Property 4's "exactly one container per tier" claim cannot
            be broken by two panels each declaring the same tier. */}
        <div
          {...(page === undefined
            ? {}
            : { [TIER_PAGE_ATTRIBUTE]: page, [TIER_ATTRIBUTE]: tier })}
          className="flex min-w-0 items-start gap-4"
        >
          {slots.map((slot) => {
            const valueField = LIVE_FIELD_BY_SLOT[slot.key];
            const read = readReported(slot.value);

            return valueField === undefined ? (
              <Metric
                key={slot.key}
                tier={tier}
                label={slot.label}
                value={slot.value}
                format={slot.format}
                precision={slot.precision}
                hint={slot.hint}
                className="flex-1"
                data-region={slot.key}
              />
            ) : (
              <LivePositionFigure
                key={slot.key}
                label={slot.label}
                tier={tier}
                format={slot.format}
                precision={slot.precision}
                hint={slot.hint}
                region={slot.key}
                symbol={symbol}
                environment={environment}
                syncedAt={syncedAt}
                valueField={valueField}
                seeded={read.available ? read.value : null}
                reason={read.reason ?? undefined}
              />
            );
          })}
        </div>
      </div>
    </Panel>
  );
}
