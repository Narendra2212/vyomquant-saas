/**
 * ═══════════════════════════════════════════════════════════════════════════
 * components/trading/ExecutionsPanel — what the venue did, and the 5-second bound
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 20.2. design.md §7.5, §7.8 (4), §13.2. Requirements 7.3, 7.4,
 * 7.5, 12.1, 12.2, 14.5.
 *
 * The execution half of §7.8 (4)'s three shared surfaces: one panel, parameterised by
 * environment, consumed by `pages/LiveTrading.jsx` at task 20.2 and by
 * `pages/PaperTrading.jsx` at task 25.2. The shape's reasoning is in
 * `./PositionsPanel.jsx`'s docblock and is not restated. Two things are particular to this
 * panel.
 *
 * THIS IS THE PANEL REQUIREMENT 7.5's FIVE-SECOND BOUND LIVES IN
 * -------------------------------------------------------------
 * "A deployment that stops, errors or disconnects is reflected within five seconds." A poll
 * satisfies that only by being fast enough, which means tuning an interval against a
 * requirement and re-tuning it whenever the payload grows. A push satisfies it by
 * construction: `STRATEGY_STATUS` is published when the state changes, this panel subscribes
 * to it at the leaf, and there is no interval anywhere in the path — so the bound holds with
 * no polling and nothing to keep in step.
 *
 * A PUSHED STRATEGY STATE IS NOT AN EXECUTION STATUS, AND IT IS NOT RENDERED AS ONE
 * --------------------------------------------------------------------------------
 * The figures this panel is given are readings of RECORDED EXECUTIONS — what the venue did
 * with an order. `STRATEGY_STATUS.status` is a different fact: the run state of the worker.
 * Superseding one with the other would be a category error dressed as freshness, so the push
 * gets its OWN reading, labelled for what it is, above the row — and the recorded execution
 * figures keep saying exactly what the read said. Requirement 14.5 is the same rule in both
 * directions: a fact is rendered as the fact it is, or not at all.
 *
 * Until a frame arrives the panel renders no such line at all. A "no push yet" marker would
 * be a not-available state for a value nobody claimed was available — the socket having said
 * nothing is not a gap in a read.
 *
 * @module components/trading/ExecutionsPanel
 */

import { memo, useCallback } from "react";

import { Metric } from "../ds/Metric";
import { Panel } from "../ds/Panel";
import { TradingEnvironmentBadge } from "../ds/TradingEnvironmentBadge";
import { TIER_ATTRIBUTE, TIER_PAGE_ATTRIBUTE } from "../../design/pageHierarchy";
import { useLiveChannel } from "../../hooks/useLiveChannel";
import { frameText, isFreshFrame } from "./liveFrame";

/**
 * `STRATEGY_STATUS` — spelled as `wsClient.subscribeStrategyStatus` spells it, which is upper
 * case. Nothing publishes a lower-case `strategy_status`; a handler registered on that name
 * can never fire, which is the defect task 19.3 found at the Dashboard page root.
 */
const STRATEGY_STATUS_CHANNEL = "STRATEGY_STATUS";

/**
 * One deployment's pushed run state, filtered to its strategy id.
 *
 * `memo` with primitive props only. Two selectors on one channel rather than one selector
 * returning a pair: `useLiveChannel` gates on `Object.is`, so an object would fail the
 * comparison on every frame and re-render this leaf for every strategy on the socket — the
 * defect a leaf subscription exists to remove. The registry still holds ONE
 * `wsClient.subscribe` for the channel however many selectors ask for it.
 *
 * Both selectors return `undefined` to decline a frame and never `null`: a frame naming this
 * strategy and carrying no `status` must not blank a state a previous frame reported.
 */
const LiveDeploymentState = memo(function LiveDeploymentState({
  strategyId,
  environment,
  syncedAt,
}) {
  const pickStatus = useCallback(
    (frame) => {
      if (!isFreshFrame(frame, environment, syncedAt)) return undefined;
      if (frameText(frame.strategy_id) !== strategyId) return undefined;
      return frameText(frame.status) ?? undefined;
    },
    [environment, syncedAt, strategyId],
  );

  const pickError = useCallback(
    (frame) => {
      if (!isFreshFrame(frame, environment, syncedAt)) return undefined;
      if (frameText(frame.strategy_id) !== strategyId) return undefined;
      return frameText(frame.error) ?? undefined;
    },
    [environment, syncedAt, strategyId],
  );

  const channel = strategyId === null || strategyId === undefined
    ? null
    : STRATEGY_STATUS_CHANNEL;
  const status = useLiveChannel(channel, pickStatus, undefined);
  const reportedError = useLiveChannel(channel, pickError, undefined);

  if (status === undefined) return null;

  return (
    <p className="text-small text-content-secondary" data-live-channel={STRATEGY_STATUS_CHANNEL}>
      {"The strategy's own stream reports this deployment as "}
      <span className="font-mono text-micro font-bold uppercase tracking-wide text-content-primary">
        {status}
      </span>
      {". This is a pushed state, not a figure from the read below."}
      {reportedError === undefined ? null : ` ${reportedError}`}
    </p>
  );
});

/**
 * The execution panel, badged by environment.
 *
 * `money` is declared: a fill is one of `ds/Panel`'s `MONEY_CONTENT` entries, so `ds/Panel`
 * refuses in development to render this panel without an environment decision (Requirements
 * 7.4, 12.2). `null` is an accepted decision and renders `ENVIRONMENT UNCONFIRMED`.
 *
 * @param {Object} props
 * @param {'LIVE'|'PAPER'|'BACKTEST'|null} props.environment The ledger this panel shows.
 * @param {string} props.title
 * @param {string} [props.page] `pageHierarchy` page id → the container's `data-page`.
 * @param {number} [props.tier] The declared tier → `data-page-tier`.
 * @param {Array<{key: string, label: string, value: unknown, format?: string,
 *   hint?: string}>} props.slots The caller's projected figures, in render order.
 * @param {string|null} [props.strategyId] The deployment's strategy id, which is what the
 *   leaf filters `STRATEGY_STATUS` on. `null` subscribes to nothing.
 * @param {number} [props.syncedAt] When the read that seeded these slots last answered.
 * @param {string} [props.state] A `PANEL_STATES` value.
 * @param {Object} [props.loading] `ds/Panel`'s loading descriptor.
 * @param {Object} [props.error] `ds/Panel`'s error descriptor.
 * @param {import('react').ReactNode} [props.actions] Controls for the header, already built
 *   by the caller. This panel calls no mutation (Property 13).
 * @param {string} [props.region] `data-region` for the panel root.
 * @param {string} [props.className]
 */
export function ExecutionsPanel({
  environment,
  title,
  page,
  tier,
  slots = [],
  strategyId = null,
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
          {/* §8.2's chip. A live execution must be unmistakable from a simulated one, and
              the four axes of difference are the badge's. It does not announce — the page's
              own strip is the announcing instance. */}
          <TradingEnvironmentBadge variant="chip" environment={environment} />
          {actions}
        </div>
      )}
      className={className}
      data-trading-panel="executions"
      data-region={region}
    >
      <div className="flex min-w-0 flex-col gap-4">
        {/* The push, as its own labelled fact, above the recorded figures it does not
            supersede. Renders nothing until a frame for this strategy arrives. */}
        <LiveDeploymentState
          strategyId={strategyId}
          environment={environment}
          syncedAt={syncedAt}
        />

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
          {slots.map((slot) => (
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
          ))}
        </div>
      </div>
    </Panel>
  );
}
