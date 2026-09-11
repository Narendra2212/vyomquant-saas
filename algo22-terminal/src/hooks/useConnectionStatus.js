/**
 * ═══════════════════════════════════════════════════════════════════════════
 * useConnectionStatus — the live socket status, from the client's own transitions
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 5.6. `design.md` §6.5. Requirements 2.5, 2.6.
 *
 * WHY THERE IS NO INTERVAL IN HERE
 * --------------------------------
 * Requirement 2.6 gives a 5-second bound: a disconnect must be reflected in the
 * indicator within 5 seconds of the underlying state change. The obvious-looking
 * implementation is `setInterval(() => setStatus(wsClient.getStatus()), 4000)`, and it is
 * the wrong one. A polled bound is only ever *at best* the poll period, it is wrong for
 * the whole of the period after a transition, and it keeps a timer running forever on
 * every page that shows the indicator.
 *
 * `websocketClient._setStatus` already notifies its listeners synchronously on every
 * transition, so subscribing to that push satisfies the bound *by construction* — the
 * elapsed time between the state change and this hook's `setStatus` is one function call,
 * not one poll period. There is deliberately no timer here that could be slower than the
 * bound. Do not add one.
 *
 * WHY IT SEEDS TWICE
 * ------------------
 * `useState`'s initialiser runs during render; the subscription is installed in an
 * effect, which runs after commit. A transition landing in that window would be missed
 * by both — the initialiser ran before it and the listener was installed after it. So the
 * effect re-reads `getStatus()` before subscribing. The second read is not redundant: it
 * is the one that absorbs a transition that raced mount. On the common path it sets the
 * value it already has, and React bails out.
 *
 * WHAT IT DOES NOT DO
 * -------------------
 * It maps nothing. The status word is returned exactly as `websocketClient` reports it
 * (`disconnected`, `connecting`, `connected`, `error`, `failed`, or anything a later
 * change adds). Turning that into a colour and a label is `statusToken` +
 * `ConnectionStatusIndicator`'s job (§4.1, §6.5), and that mapping is total, so an
 * unrecognised word renders neutral with the raw value as its label — never "LIVE". A
 * mapping in here would be a second place for that vocabulary to drift.
 */

import { useEffect, useState } from 'react';

import wsClient from '../websocketClient';

/**
 * The current WebSocket connection status, kept live by push.
 *
 * @returns {string} the client's own status word, unmapped
 */
export function useConnectionStatus() {
  const [status, setStatus] = useState(() => wsClient.getStatus());

  useEffect(() => {
    // Re-seed: a transition may have landed between the render that seeded `status` and
    // this effect. See the note above — this line is the reason a component mounting
    // just after a drop is not briefly, confidently wrong.
    setStatus(wsClient.getStatus());

    // `onStatusChange` returns its own unsubscribe, so the effect cleanup is the
    // unsubscribe. Passing `setStatus` straight in is safe because a status is always a
    // string: React only treats an argument as an updater when it is a function.
    return wsClient.onStatusChange(setStatus);
  }, []);

  return status;
}

export default useConnectionStatus;
