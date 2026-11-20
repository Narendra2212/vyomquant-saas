/**
 * @fileoverview Signal Trace's list row projection (ST-UI-01).
 *
 * Kept out of `pages/SignalTrace.jsx` so it can be tested without rendering the page.
 */

/**
 * A non-empty trimmed string, or `null`. The one reader Signal Trace uses for a row field,
 * so `''`, `'   '` and a non-string all reach the not-available marker by the same route.
 */
export const rowText = (value) => {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
};

const text = rowText;

/**
 * A version as the row reports it, without the leading `v` the server already carries
 * (`"v1.0"`), so the cell's own ` v` prefix does not print `vv1.0` (ST-UI-01).
 */
export const bareVersion = (value) => {
  const version = text(value);
  return version === null ? null : version.replace(/^v(?=\d)/i, '');
};

/**
 * One row as the five cells §10.3's table reads. Nothing is defaulted.
 *
 * ST-UI-01 (2026-11-15): the outcome read `row.status`, a legacy column the list does not
 * return, so every row said "Outcome: not available" while the response carried
 * `order_lifecycle_state` (`EXECUTED`, `REJECTED`, ...). The canonical state is read
 * first; the legacy column only if a row has no canonical state.
 */
export const projectSignal = (row, strategyNames) => {
  const strategy = text(row?.strategy_id);
  return {
    id: row?.id,
    time: text(row?.generated_at),
    strategy,
    strategyName: strategy === null ? null : text(strategyNames?.[strategy]),
    version: bareVersion(row?.strategy_version),
    market: text(row?.symbol),
    decision: text(row?.decision),
    outcome: text(row?.order_lifecycle_state) ?? text(row?.status),
  };
};

