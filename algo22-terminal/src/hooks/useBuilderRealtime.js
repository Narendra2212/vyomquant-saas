/**
 * ═══════════════════════════════════════════════════════════════════════════
 * useBuilderRealtime — one connection per session, ref-counted
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * strategy-builder task 8.5. `design.md` § WebSocket / realtime, `use_builder_realtime`.
 * Requirements 23.1-23.6, and 21.6 for the refusal it surfaces.
 *
 * WHAT THIS HOOK OWNS, AND WHAT IT DELIBERATELY DOES NOT
 * -----------------------------------------------------
 * It owns the connection lifecycle: taking and releasing a hold on the ONE shared
 * socket, asking for channels, dropping them on unmount, asking for a snapshot when the
 * connection comes back, and running the 30 s safety poll while it is down.
 *
 * It owns no state machine. The frames are reduced by `lib/builderRealtime.js`, which is
 * pure; the runtime labels are task 8.4's, the deployed lock is task 8.3's, and the
 * refusal is the server's. This hook does not classify anything.
 *
 * It also opens no socket of its own. `src/websocketClient.js` is the session's one
 * socket and this hook is a consumer of it — `design.md`'s "Do not open a second
 * socket", enforced by construction rather than by convention: the ref count lives on
 * that client, so three builder views mounting take three holds on one connection.
 *
 * WHY IT DOES NOT CONNECT WITHOUT A TOKEN
 * --------------------------------------
 * The socket authenticates with `?token=`, and the endpoint closes an unauthenticated
 * connection with 1008 before the first frame. Opening one anyway would produce a
 * connect/refuse/backoff loop that can never succeed and would report `DISCONNECTED` for
 * a connection that was never possible. So a session with no token reports
 * `UNAVAILABLE`, states why, and runs the safety poll — which is the same fallback
 * Requirement 23.6 defines for a closed connection, and is exactly as good here.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react';

import wsClient from '../websocketClient';
import {
  INITIAL_REALTIME,
  REALTIME_STATES,
  builderChannels,
  realtimeReducer,
  realtimeStateFromSocket,
} from '../lib/builderRealtime';

/**
 * Requirement 23.6, literally: "poll status at an interval of 30 seconds" — and only
 * while the connection is closed. The timer is created when the connection drops and
 * destroyed when it comes back, so a healthy session makes no polling requests at all.
 * That is the point of the requirement: the poll is a safety net, not a second data
 * path running beside the socket.
 */
export const SAFETY_POLL_INTERVAL_MS = 30000;

/**
 * The reconnect policy the builder installs on the shared client (Requirement 23.4).
 *
 * `maxAttempts: Infinity` is the one deviation from the client's default of 5, and it is
 * safe only because of the other three fields: the delay doubles, is capped at 30 s and
 * is jittered, so an unbounded retry converges on one attempt per ~30 s per client with
 * the fleet spread out. A builder left open through a backend deploy has to come back on
 * its own; giving up after five tries would leave it silently stale behind a socket the
 * user believes is live.
 */
export const BUILDER_RECONNECT_POLICY = Object.freeze({
  maxAttempts: Infinity,
  baseDelayMs: 500,
  maxDelayMs: 30000,
  jitterMs: 250,
});

/** The WebSocket path the builder multiplexes over. */
export const BUILDER_SOCKET_PATH = '/ws/telemetry';

/** Where `websocketClient` reads its token from, so both agree on "authenticated". */
const readToken = () => {
  try {
    return window.sessionStorage.getItem('token');
  } catch (error) {
    // A browser with storage blocked. Reported as no token, which is the truth.
    return null;
  }
};

/**
 * Subscribe to the builder's realtime channels for one strategy.
 *
 * @param {Object} options
 * @param {string} [options.strategyId] the saved strategy, or '' while unsaved
 * @param {string} [options.deploymentId] the running deployment, if any
 * @param {string[]} [options.trainingJobIds] live training jobs, if any
 * @param {Function} [options.onSnapshot] called as `onSnapshot(reason)` on every
 *   (re)connect and on every safety-poll tick while disconnected. Requirement 23.5's
 *   "request a state snapshot" and 23.6's poll are the SAME read — a status read over
 *   the authenticated REST surface — so the caller supplies one function and this hook
 *   decides when it is needed. Two mechanisms for one job would be two things to keep
 *   in agreement.
 * @param {Object} [options.client] the socket client, injectable for tests
 * @param {boolean} [options.enabled] force the connection off (default: on when there
 *   is a token and at least one channel to subscribe)
 * @returns {{realtime: Object, status: string, connected: boolean, channels: string[],
 *            reason: string, requestSnapshot: Function}}
 */
export function useBuilderRealtime({
  strategyId = '',
  deploymentId = '',
  trainingJobIds = null,
  onSnapshot = null,
  client = wsClient,
  enabled = true,
} = {}) {
  const [realtime, dispatch] = useReducer(realtimeReducer, INITIAL_REALTIME);

  // The channel list is derived, and `useMemo`'d on the ids rather than on the array, so
  // a re-render with the same ids does not unsubscribe and resubscribe everything.
  const jobKey = (trainingJobIds || []).join(',');
  const channels = useMemo(
    () =>
      builderChannels({
        strategyId,
        deploymentId,
        trainingJobIds: jobKey === '' ? [] : jobKey.split(','),
      }),
    [strategyId, deploymentId, jobKey],
  );
  const channelKey = channels.join('|');

  const hasToken = Boolean(readToken());
  const active = Boolean(enabled) && hasToken && channels.length > 0;

  // Held in a ref so a caller that re-creates the callback every render does not tear
  // the connection down. The snapshot request is a side effect of connecting, not a
  // dependency of it.
  const snapshotRef = useRef(onSnapshot);
  snapshotRef.current = onSnapshot;

  const requestSnapshot = useCallback((reason) => {
    const request = snapshotRef.current;
    if (typeof request !== 'function') return;
    try {
      request(reason);
    } catch (error) {
      console.error('Builder realtime snapshot failed:', error);
    }
  }, []);

  // -- the connection ------------------------------------------------------
  useEffect(() => {
    if (!active) {
      dispatch({ type: 'status', status: REALTIME_STATES.UNAVAILABLE });
      return undefined;
    }

    dispatch({ type: 'status', status: realtimeStateFromSocket(client.getStatus()) });

    const stopWatchingStatus = client.onStatusChange((status) => {
      dispatch({ type: 'status', status: realtimeStateFromSocket(status) });
    });

    /*
      Requirement 23.5. `websocketClient` has already resubscribed the channels by the
      time this fires — it does that from its own registry inside `handleOpen`, because
      the server drops a connection's authorised subscriptions when it closes and a
      client that did not resubscribe would sit on an open socket receiving nothing.
      What is left for this hook is the second half: ask for a snapshot, because the
      gap between the drop and the reconnect is a gap in the frames and continuity must
      never be assumed.
    */
    const stopWatchingOpen = client.onOpen(() => requestSnapshot('reconnect'));

    client.acquire(BUILDER_SOCKET_PATH, BUILDER_RECONNECT_POLICY);

    const releases = channels.map((channel) =>
      client.subscribeChannel(channel, (frame) => dispatch({ type: 'frame', frame })),
    );

    // If the socket was already open when this view mounted, no `onOpen` will fire for
    // it, so the snapshot is requested directly. A view that opens mid-session needs the
    // current state just as much as one that reconnects.
    if (client.isConnected()) requestSnapshot('mount');

    return () => {
      // Requirement 23.2: a closing view unsubscribes what it subscribed. Nothing else:
      // another view may still hold the same channel, and `release` closes the socket
      // only when the last hold goes.
      for (const release of releases) release();
      stopWatchingOpen();
      stopWatchingStatus();
      client.release();
    };
  }, [active, channelKey, client, requestSnapshot]); // eslint-disable-line react-hooks/exhaustive-deps

  /*
    -- the safety poll, only while the connection is not carrying frames (23.6) --

    "While the realtime connection is closed" is read as "while it is not connected", which
    includes `CONNECTING`. That is the reading that matters in practice rather than a
    pedantic one: a dropped socket spends most of its time in `CONNECTING`, because each
    backoff attempt sets that state and only a successful open clears it. Treating
    `CONNECTING` as not-closed would stop the poll after the first reconnect attempt and
    leave a session with an unreachable backend reading nothing at all — which is exactly
    the situation the safety poll exists for.
  */
  const notConnected = realtime.status !== REALTIME_STATES.CONNECTED;

  useEffect(() => {
    if (!notConnected) return undefined;
    if (typeof snapshotRef.current !== 'function') return undefined;

    // No immediate read here: the connection either just dropped (in which case the
    // last frames are seconds old) or was never available (in which case the caller's
    // own first read has already happened). Firing one now would double every read on
    // an intermittent connection.
    const timer = setInterval(() => requestSnapshot('poll'), SAFETY_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [notConnected, requestSnapshot]);

  // -- a reset when the strategy changes -----------------------------------
  useEffect(() => {
    dispatch({ type: 'reset' });
  }, [strategyId, deploymentId]);

  const reason = useMemo(() => {
    if (!enabled) return 'Realtime updates are switched off for this view.';
    if (!hasToken) {
      return 'Sign in to receive live updates; status is being read every 30 seconds instead.';
    }
    if (channels.length === 0) {
      return 'Save this strategy to receive live validation, deployment and execution updates.';
    }
    if (realtime.status === REALTIME_STATES.DISCONNECTED) {
      return 'The live connection dropped. Reconnecting, and reading status every 30 seconds meanwhile.';
    }
    return '';
  }, [enabled, hasToken, channels.length, realtime.status]);

  return {
    realtime,
    status: realtime.status,
    connected: realtime.status === REALTIME_STATES.CONNECTED,
    channels,
    reason,
    requestSnapshot,
  };
}

export default useBuilderRealtime;
