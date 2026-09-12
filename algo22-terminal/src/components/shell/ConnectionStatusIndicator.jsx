/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/components/shell/ConnectionStatusIndicator.jsx — the real connection light
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 8.7. design.md §6.5, §1.3, §4.1.
 * Requirements 2.5, 2.6, 14.5, 16.2.
 *
 * ---------------------------------------------------------------------------
 * WHAT THIS REPLACES
 * ---------------------------------------------------------------------------
 * `TopBar.jsx` rendered `<LiveStatusV2 status="running" />` — a literal string, passed
 * to a component that maps `running` to a green dot with a pulsing halo and the label
 * `LIVE`. It never read `wsClient`. So the indicator claimed the trading engine was live
 * while the backend was down, while the socket was closed, and while the user was signed
 * out. A trader reads that light to decide whether the figures beside it are current,
 * which makes it a Requirement 2.5/2.6 gap and a Requirement 14.5 fabrication at once
 * (§1.3).
 *
 * ---------------------------------------------------------------------------
 * THERE IS NO `status` PROP, AND THAT IS THE FIX
 * ---------------------------------------------------------------------------
 * The defect was not a wrong value. It was that a caller could *supply* one. So this
 * component takes no status: it reads `useConnectionStatus()` itself, and a `status` or
 * `state` prop is REFUSED in development the same way `ds/StatusBadge` refuses a colour
 * (Requirement 1.4's structure, applied to a different kind of lie). There is no code
 * path here through which any call site can make the light say anything.
 *
 * The hook is push-based (`wsClient.onStatusChange`), so Requirement 2.6's five-second
 * bound is met by the transition itself rather than by a poll — see the hook's docblock.
 * Nothing in this file has a timer.
 *
 * ---------------------------------------------------------------------------
 * THE MAPPING IS TOTAL, AND UNKNOWN IS NEVER "LIVE"
 * ---------------------------------------------------------------------------
 * `websocketClient` reports `connecting`, `connected`, `disconnected`, `error` and
 * `failed` today. Colour comes from `statusToken` (`design/semantic.js`), which is the
 * app's only state → colour mapping and is total: a word added to the client tomorrow
 * resolves to the neutral group rather than to `undefined`. The LABEL is mapped here,
 * because "what the socket's state is called on screen" is presentation, not semantics —
 * and an unmapped word renders as ITSELF (§6.5). A status this build has never seen can
 * therefore render calm and unfamiliar, but it can never render as a claim.
 *
 * `connected` is labelled "Connected", not "LIVE". The socket being open says the
 * terminal is receiving the engine's frames; it does not say a strategy is trading, and
 * the old label conflated the two on every page including Paper Trading.
 *
 * Two consequences of that split worth being explicit about, because §6.5's summary
 * ("an unrecognised status renders neutral with the raw value as its label") reads as one
 * rule and is really two:
 *
 *   * The HUE is `statusToken`'s, always — Requirement 1.4 allows no second mapping, and
 *     that function's fallback for a word outside the app's whole vocabulary IS neutral.
 *     A word inside the vocabulary but outside {@link CONNECTION_LABELS} (`closed`,
 *     `stale`) therefore keeps its semantic hue, which is what makes the indicator and
 *     the strip agree about a socket that is down under a name nobody mapped.
 *   * The LABEL is either the mapped word or the client's own word verbatim. There is no
 *     branch in this file that can INTRODUCE a status word. "LIVE" cannot appear unless
 *     `websocketClient` itself starts naming a state that, in which case the bar is
 *     reporting the transport rather than deciding for it.
 *
 * A status that cannot be read at all — the client handing back `undefined`, or a
 * non-string — renders {@link CONNECTION_UNKNOWN_LABEL}, not a guess in either
 * direction, and does NOT raise the strip: an unreadable status is not evidence that
 * data is stale, only that we cannot say it is fresh.
 *
 * ---------------------------------------------------------------------------
 * WHY THE INDICATOR IS NOT A LIVE REGION
 * ---------------------------------------------------------------------------
 * The disconnected strip `TopBar` renders from {@link CONNECTION_DOWN_COPY} is an
 * `ds/Alert severity="error"`, which is `role="alert"` — assertive. If this badge were
 * also a live region, one socket drop would be announced twice, a beat apart, and the
 * second announcement would be the one WITHOUT the consequence or the retry.
 *
 * So the strip announces, and this stays a persistent readable indicator (Requirement
 * 2.5 asks for exactly that). The states with no strip — `connecting`, `reconnecting` —
 * are then not announced at all, which is deliberate: reconnection is expected and
 * self-healing, and interrupting a trader for it is the noise Requirement 16.2 forbids.
 *
 * @module components/shell/ConnectionStatusIndicator
 */

import { statusToken } from '../../design/semantic';
import { useConnectionStatus } from '../../hooks/useConnectionStatus';
// Straight from the modules, not through `ds/index.js`. The shell is in every route's
// import graph, and the barrel re-exports 26 primitives — importing it here would pull
// all 26 into the entry chunk to render one badge. Same argument `shell/ResponsiveGate`
// makes for `ds/Alert` and `ds/index.js` makes for keeping `Chart` out of itself.
import { assertContract } from '../ds/devAssert';
import { StatusBadge } from '../ds/StatusBadge';

/**
 * Status word → the label on screen. Every key is a real `_setStatus()` argument in
 * `websocketClient.js`; `reconnecting` is included because §6.5 names it and
 * `setReconnectPolicy` callers can drive the client into it.
 *
 * These describe the CONNECTION, not the trading mode. See the docblock on `connected`.
 */
export const CONNECTION_LABELS = Object.freeze({
  connected: 'Connected',
  connecting: 'Connecting',
  reconnecting: 'Reconnecting',
  disconnected: 'Disconnected',
  error: 'Connection error',
  failed: 'Connection failed',
});

/** Shown when the client reports nothing readable. Not a default — an admission. */
export const CONNECTION_UNKNOWN_LABEL = 'Unknown';

/**
 * The state key an unreadable status resolves to. `unknown` is a declared entry in
 * `design/semantic.js`'s vocabulary, so the neutral treatment is a decision on record
 * rather than a fall through `statusToken`'s unrecognised-value branch.
 */
const UNKNOWN_STATE = 'unknown';

/**
 * §6.5's copy for the strip, verbatim, plus its retry label.
 *
 * It lives beside the mapping rather than in `TopBar.jsx` because the sentence and the
 * decision to show it are one fact about the connection: whoever changes what counts as
 * "down" is looking at the words that will be shown when it is.
 *
 * `title` states the condition (it is what a screen reader announces) and `detail`
 * states the consequence, which is the half that matters — a trader who reads
 * "disconnected" and not "the figures below may be out of date" has been told the
 * mechanism and not the risk.
 */
export const CONNECTION_DOWN_COPY = Object.freeze({
  title: 'Not connected to the trading engine.',
  detail: 'Live positions, orders and P&L below may be out of date.',
  retryLabel: 'Reconnect',
});

/**
 * Everything the shell needs to know about a status word. Pure, total, no React.
 *
 * `down` is `group === 'error'`, not a list of words. `statusToken`'s vocabulary already
 * puts `disconnected`, `error`, `failed`, `closed` and `stale` in the error group, so a
 * sixth way for the socket to be down arrives here already classified instead of
 * silently missing from a hand-kept array — which is how `failed` (the status
 * `websocketClient` sets after it gives up reconnecting, the most stale state there is)
 * would otherwise have been left without a strip.
 *
 * @param {unknown} status A `wsClient.getStatus()` value.
 * @returns {{state: string, label: string, group: string, down: boolean, known: boolean}}
 */
export function connectionPresentation(status) {
  const raw = typeof status === 'string' ? status.trim() : '';
  if (raw === '') {
    return {
      state: UNKNOWN_STATE,
      label: CONNECTION_UNKNOWN_LABEL,
      group: statusToken(UNKNOWN_STATE).group,
      // An unreadable status is not a claim that data is stale. See the docblock.
      down: false,
      known: false,
    };
  }

  const key = raw.toLowerCase();
  const { group } = statusToken(key);
  const known = Object.prototype.hasOwnProperty.call(CONNECTION_LABELS, key);

  return {
    state: key,
    // The raw value as its own label for anything unmapped (§6.5) — never "LIVE".
    label: known ? CONNECTION_LABELS[key] : raw,
    group,
    down: group === 'error',
    known,
  };
}

/**
 * True when the socket's state means the figures on screen may be stale.
 *
 * The one place the shell asks that question, so the indicator's colour and the strip's
 * presence cannot disagree about it.
 *
 * @param {unknown} status
 * @returns {boolean}
 */
export function isConnectionDown(status) {
  return connectionPresentation(status).down;
}

/**
 * The top bar's connection light.
 *
 * @param {Object} props
 * @param {string} [props.className] Appended to the wrapper.
 */
export function ConnectionStatusIndicator({ className = '', ...rest }) {
  // Requirement 14.5, structurally: the value cannot be supplied. `<LiveStatusV2
  // status="running" />` was one prop; refusing the prop is what stops it coming back.
  const supplied = ['status', 'state', 'connected', 'isConnected', 'label'].filter((name) =>
    Object.prototype.hasOwnProperty.call(rest, name),
  );
  assertContract(
    supplied.length === 0,
    `ConnectionStatusIndicator does not accept ${supplied.join(', ')}. The status is read from `
      + '`useConnectionStatus()` and cannot be passed in — a caller-supplied status is exactly '
      + 'the `<LiveStatusV2 status="running" />` defect this component replaces (§1.3, '
      + 'Requirement 14.5).',
  );
  supplied.forEach((name) => {
    delete rest[name];
  });

  const status = useConnectionStatus();
  const { state, label, down, known } = connectionPresentation(status);

  return (
    <span
      data-shell="connection-indicator"
      data-connection-status={known ? state : 'unknown'}
      data-connection-down={down ? 'true' : 'false'}
      className={`inline-flex shrink-0 items-center ${className}`.trim()}
      {...rest}
    >
      {/* The badge reads "Disconnected", which on its own does not say what is
          disconnected. Visible context costs width the bar does not have at 768px, so
          the subject goes to assistive technology only and the visible badge sits beside
          a bar whose other contents make it obvious. */}
      <span className="sr-only">Trading engine connection: </span>
      {/* `state` selects the hue through `statusToken` — the badge takes no colour prop,
          so there is no route from here to a chosen colour, and it stamps its own
          `data-status-group`. The dot is static: both predecessors animated theirs, and
          Requirement 1.5 retires that. */}
      <StatusBadge state={state} label={label} dot size="sm" />
    </span>
  );
}

export default ConnectionStatusIndicator;
