/**
 * ═══════════════════════════════════════════════════════════════════════════
 * components/trading/liveFrame — what a leaf may read off a pushed frame
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 20.2. design.md §13.2(a), §13.2(b). Requirements 7.5, 12.1,
 * 14.5.
 *
 * The three shared panels each subscribe to their own `wsClient` channel at the leaf, and
 * all three have to answer the same two questions about a frame before rendering anything
 * from it. Those two answers live here rather than three times over, because a copy per
 * panel is a copy per panel that can disagree — and the thing they would disagree about is
 * whether a PAPER fill may move a LIVE figure.
 *
 * `pages/Dashboard.jsx` reached the same two tests at task 19.3 (`be66baa`) and states them
 * in its own `isFreshFrame`. This is that function, moved to where more than one file needs
 * it and generalised on one axis only: the panels carry the badge's environment id
 * (`"LIVE"`, `"PAPER"`, `"BACKTEST"` or `null`) while the frame carries the server's ledger
 * word (`"live"`, `"paper"`), so the comparison is case-insensitive here where Dashboard's
 * is exact. Nothing else about the gate changes.
 *
 * WHY A FRAME WITH NO LEDGER PASSES AND A PANEL WITH NO LEDGER REJECTS
 * -------------------------------------------------------------------
 * The two nulls are not symmetric, and treating them as if they were is how a paper figure
 * reaches a live panel:
 *
 *   * A **frame** that names no environment has told us nothing that rules it out, so it
 *     passes — exactly as it does at Dashboard's page root. The server publishes plenty of
 *     frames without the field.
 *   * A **panel** whose environment is `null` is a panel whose ledger the server did not
 *     label (`ds/TradingEnvironmentBadge` renders `ENVIRONMENT UNCONFIRMED` for it). A
 *     frame claiming a ledger cannot be checked against an unknown one, so it is declined.
 *     The alternative is accepting it, which would put a figure from some ledger onto a
 *     surface that cannot say which ledger it is showing.
 *
 * @module components/trading/liveFrame
 */

/**
 * The worker run-state stream, spelled ONCE for every leaf that subscribes to it.
 *
 * `wsClient.subscribeStrategyStatus` spells it in upper case and nothing publishes a
 * lower-case `strategy_status`, so a handler registered on that name can never fire — which
 * is the defect task 19.3 found at the Dashboard page root. Task 20.5 added a second leaf on
 * this channel (`pages/LiveTrading.jsx`'s tier-1 connection slot) beside
 * `./ExecutionsPanel.jsx`'s, and two string literals are two chances to reintroduce that
 * defect in one of them, so the name lives here with the rest of what a frame-reading leaf
 * needs.
 */
export const STRATEGY_STATUS_CHANNEL = "STRATEGY_STATUS";

/**
 * How far behind the read that seeded a leaf a frame's own clock may be and still be read.
 *
 * `pages/Dashboard.jsx` uses this same one-second slack, and for the same reason: the
 * frame's clock is the server's and the read's is this browser's, so a frame published a
 * moment before the read completed must not be discarded for being a moment behind it.
 */
export const FRAME_CLOCK_SLACK_MS = 1000;

/**
 * A reported scalar as the string a trader reads, or `null`.
 *
 * The same coercion `pages/LiveTrading.jsx` applies to a REST field, applied to a pushed
 * one: a finite number becomes its own digits, a blank string is not a reading, and
 * everything else is `null` so that an absent field travels to the marker rather than
 * arriving as `0` or `""` (Requirement 14.5).
 *
 * @param {unknown} value
 * @returns {string|null}
 */
export const frameText = (value) => {
  if (typeof value === "string") return value.trim() === "" ? null : value.trim();
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : null;
  return null;
};

/**
 * A reported scalar as a finite number, or `null`.
 *
 * A numeric string is accepted because the socket's JSON quotes some amounts and not
 * others; `NaN`, `Infinity` and a blank string are not figures and stop here.
 *
 * @param {unknown} value
 * @returns {number|null}
 */
export const frameNumber = (value) => {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const text = frameText(value);
  if (text === null) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
};

/**
 * Whether one pushed frame may be read by a leaf on this ledger, seeded by this read.
 *
 * Two tests, and both are refusals rather than transformations:
 *
 *   * **The ledger.** A frame that names an environment other than the one on screen is
 *     not about the figure it appears to be about. See the module docblock for the two
 *     asymmetric nulls.
 *   * **The clock.** A frame older than the read that seeded this leaf is a replay, and
 *     rendering it would put a superseded figure back on screen (Requirement 14.5).
 *
 * @param {unknown} frame
 * @param {string|null|undefined} environment The badge's environment id, or `null`.
 * @param {number|undefined} syncedAt When the read that seeded this leaf last answered.
 * @returns {boolean}
 */
export const isFreshFrame = (frame, environment, syncedAt) => {
  if (!frame || typeof frame !== "object") return false;

  const claimed = frameText(frame.environment);
  if (claimed !== null) {
    const shown = frameText(environment);
    if (shown === null) return false;
    if (claimed.toLowerCase() !== shown.toLowerCase()) return false;
  }

  if (frame.timestamp && Number.isFinite(syncedAt)) {
    const publishedAt = new Date(frame.timestamp).getTime();
    if (Number.isFinite(publishedAt) && publishedAt < syncedAt - FRAME_CLOCK_SLACK_MS) {
      return false;
    }
  }
  return true;
};
