/**
 * ═══════════════════════════════════════════════════════════════════════════
 * useSignalTraceRealtime — one channel per deployment, on the session's one socket
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * trading-lifecycle-integration task 19.1. Requirements 17.4, 18.1, 18.2, 18.6, 18.7,
 * 18.8, and 18.3 for the refusal it hands back.
 *
 * WHAT THIS HOOK OWNS
 * -------------------
 * The connection lifecycle, and nothing else: taking a ref-counted hold on the ONE shared
 * socket, learning which deployments a strategy currently has, asking for one
 * `signal.{deployment_id}` channel per deployment, re-authenticating in place when the
 * session token is refreshed, asking for a snapshot when the connection comes back, and
 * dropping every hold when the page unmounts.
 *
 * The frames are reduced by `lib/signalTraceRealtime.js`, which is pure. This hook
 * classifies nothing, orders nothing, and derives no signal state of its own.
 *
 * WHY ONE CHANNEL PER DEPLOYMENT RATHER THAN ONE PER STRATEGY
 * ----------------------------------------------------------
 * `SIGNAL_FAMILY` is scoped to `deployment_id` (`ws_channels.py`, task 14.1) because a
 * deployment already resolves to exactly one owner, so the server can authorise it with
 * the `deployment_id` ownership lookup it already performs for the `deployment` and
 * `execution` families — no new owner-resolution path (Requirement 18.2). A strategy has
 * at most a handful of concurrent deployments, so a page filtered by strategy
 * (Requirement 17.4's entry path from the Strategies_Page) holds a handful of channels.
 * `websocketClient`'s ref count means those are holds on one socket, not sockets.
 *
 * WHY IT OPENS NO SOCKET OF ITS OWN
 * --------------------------------
 * `src/websocketClient.js` is the session's one socket and this hook is a consumer of it,
 * exactly as `useBuilderRealtime` is — `design.md`'s "Do not open a second socket",
 * enforced by construction: the ref count lives on that client, so the Signal_Trace_Page
 * mounting beside an open Builder takes a second hold on the first connection.
 *
 * WHY IT DOES NOT CONNECT WITHOUT A TOKEN OR WITHOUT A DEPLOYMENT
 * --------------------------------------------------------------
 * The endpoint closes an unauthenticated connection before the first frame, so opening one
 * anyway would produce a connect/refuse/backoff loop that can never succeed while
 * reporting `DISCONNECTED` for a connection that was never possible. And a page with no
 * deployment to subscribe to has nothing to receive: taking a hold would keep a socket
 * open to carry nothing. Both report `UNAVAILABLE`, which is the truth.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';

import wsClient from '../websocketClient';
import { endpoints } from '../api';
import { BUILDER_RECONNECT_POLICY, BUILDER_SOCKET_PATH } from './useBuilderRealtime';
import {
  INITIAL_SIGNAL_REALTIME,
  SIGNAL_REALTIME_STATES,
  activeDeploymentIds,
  mergeDeploymentIds,
  signalRealtimeReducer,
  signalRealtimeStateFromSocket,
  signalRefusalList,
  signalTraceChannels,
} from '../lib/signalTraceRealtime';

/**
 * The reconnect policy, and the socket path, are `useBuilderRealtime`'s own — imported
 * rather than restated. Requirement 18.6 defines this page's reconnect behaviour as "the
 * same bounded, jittered backoff policy defined by the platform's existing
 * `useBuilderRealtime` reconnect contract", and the only way for that to stay true as the
 * policy evolves is for there to be one policy.
 */
export { BUILDER_RECONNECT_POLICY as SIGNAL_RECONNECT_POLICY };
export { BUILDER_SOCKET_PATH as SIGNAL_SOCKET_PATH };

/**
 * How often the session token is re-read, for Requirement 18.7.
 *
 * A token refresh is not an event this page can subscribe to: `apiClient`'s Supabase
 * listener writes the new token to `sessionStorage` and notifies nothing that this hook
 * can hear (its own websocket notification pokes `window.wsClient` directly). Reading one
 * `sessionStorage` key on a slow timer is a cheap, honest way to notice, and the *point*
 * of the requirement is what happens next: `reauthenticate` sends an `auth` frame on the
 * existing connection, so a session that refreshes its token hourly does not drop and
 * rebuild its socket — and its subscriptions — hourly. The cross-tab `storage` event is
 * watched too, which covers the refresh a sibling tab performed.
 */
export const TOKEN_WATCH_INTERVAL_MS = 30000;

/** Only an id of this shape can appear in a channel name, so only one is worth asking about. */
const RESOURCE_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

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
 * Subscribe to the signal channels of one strategy's deployments, or of a given set.
 *
 * @param {Object} options
 * @param {string} [options.strategyId] The strategy the page is filtered to, if any. Its
 *   deployment list is fetched once per id and re-fetched on `refreshDeployments()`.
 * @param {string[]} [options.deploymentIds] Deployment ids the caller already knows —
 *   typically the ones its loaded signals name. Unioned with the fetched list, so a
 *   deployment the registry-backed listing misses is still subscribed to.
 * @param {Function} [options.onSnapshot] Called as `onSnapshot(reason)` on mount over an
 *   already-open socket and on every (re)connect. Requirement 18.6's "request a snapshot
 *   of current state rather than assuming no updates were missed"; the caller supplies the
 *   read, this hook decides when it is needed. Returning the read's promise (task 19.2)
 *   tells this hook when the snapshot is on screen, so the pushed state it supersedes can
 *   be dropped; returning nothing leaves the overlay alone.
 * @param {boolean} [options.enabled] Force the connection off.
 * @param {Object} [options.client] The socket client, injectable for tests.
 * @param {Function} [options.fetchDeployments] `(strategyId) => Promise<body>`; defaults to
 *   `endpoints.strategies.listDeployments`.
 * @returns {{realtime: Object, status: string, connected: boolean, channels: string[],
 *   deploymentIds: string[], signals: Object, refusals: Array<Object>,
 *   deploymentsError: Object|null, requestSnapshot: Function, refreshDeployments: Function}}
 */
export function useSignalTraceRealtime({
  strategyId = '',
  deploymentIds = null,
  onSnapshot = null,
  enabled = true,
  client = wsClient,
  fetchDeployments,
} = {}) {
  const [realtime, dispatch] = useReducer(signalRealtimeReducer, INITIAL_SIGNAL_REALTIME);
  const [fetchedIds, setFetchedIds] = useState([]);
  const [deploymentsError, setDeploymentsError] = useState(null);
  const [nonce, setNonce] = useState(0);

  const askedStrategy =
    typeof strategyId === 'string' && RESOURCE_ID_PATTERN.test(strategyId.trim())
      ? strategyId.trim()
      : '';

  const fetchRef = useRef(fetchDeployments);
  fetchRef.current = fetchDeployments;

  const refreshDeployments = useCallback(() => setNonce((n) => n + 1), []);

  // -- which deployments this strategy currently has ------------------------
  useEffect(() => {
    if (askedStrategy === '') {
      // Not an error state: an unfiltered page subscribes to whatever its own signals
      // name, and a half-typed strategy id is not a question worth asking the server.
      setFetchedIds([]);
      setDeploymentsError(null);
      return undefined;
    }

    let cancelled = false;
    const call = fetchRef.current ?? ((id) => endpoints.strategies.listDeployments(id));

    (async () => {
      try {
        const body = await call(askedStrategy);
        if (cancelled) return;
        setFetchedIds(activeDeploymentIds(body));
        setDeploymentsError(null);
      } catch (error) {
        if (cancelled) return;
        // The list is unreadable, so this page does not know which channels to hold. The
        // ids its own signals name are still held (they are a separate source), and the
        // failure is reported rather than swallowed — silently watching nothing looks
        // exactly like a strategy that has produced no signals.
        setFetchedIds([]);
        setDeploymentsError(error ?? new Error('Deployment list unavailable'));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [askedStrategy, nonce]);

  const givenKey = (Array.isArray(deploymentIds) ? deploymentIds : []).join(',');
  const fetchedKey = fetchedIds.join(',');

  // Memoised on the ids' *content*, not the arrays' identity: the caller rebuilds its
  // list on every render, and using its identity would unsubscribe and resubscribe every
  // channel on every keystroke.
  const subscribedIds = useMemo(
    () =>
      mergeDeploymentIds(
        fetchedKey === '' ? [] : fetchedKey.split(','),
        givenKey === '' ? [] : givenKey.split(','),
      ),
    [fetchedKey, givenKey],
  );

  const channels = useMemo(() => signalTraceChannels(subscribedIds), [subscribedIds]);
  const channelKey = channels.join('|');

  const hasToken = Boolean(readToken());
  const active = Boolean(enabled) && hasToken && channels.length > 0;

  // Held in a ref so a caller that re-creates the callback every render does not tear the
  // connection down. The snapshot request is a side effect of connecting, not a
  // dependency of it.
  const snapshotRef = useRef(onSnapshot);
  snapshotRef.current = onSnapshot;

  /*
    Task 19.2, Requirements 18.5/18.6. The snapshot is the caller's read, but WHEN IT LANDS
    is this hook's business, because the pushed overlay it supersedes lives in this hook's
    reducer.

    `onSnapshot` keeps its one-argument shape (`onSnapshot(reason)`) and gains a meaning for
    its RETURN value: a caller that returns the promise of its read is telling this hook
    when the read is on screen, and the reducer then drops the overlay entries that read
    supersedes (see the reducer's `snapshot` action for why leaving them would let pre-drop
    state permanently override post-reconnect truth). A caller that returns nothing has not
    said when its read landed, so nothing is dropped — dropping on a guess would blank a
    frame the read had not yet replaced.

    The request time is taken BEFORE the read is issued, not after it resolves: a frame that
    arrives while the read is in flight may be newer than the read, and the reducer keeps
    exactly those.
  */
  const requestSnapshot = useCallback((reason) => {
    const request = snapshotRef.current;
    if (typeof request !== 'function') return;
    const at = Date.now();
    try {
      const settled = request(reason);
      if (settled && typeof settled.then === 'function') {
        settled.then(
          () => dispatch({ type: 'snapshot', at }),
          (error) => console.error('Signal trace snapshot failed:', error),
        );
      }
    } catch (error) {
      console.error('Signal trace snapshot failed:', error);
    }
  }, []);

  // -- the connection ------------------------------------------------------
  useEffect(() => {
    if (!active) {
      dispatch({ type: 'status', status: SIGNAL_REALTIME_STATES.UNAVAILABLE });
      return undefined;
    }

    dispatch({ type: 'status', status: signalRealtimeStateFromSocket(client.getStatus()) });

    const stopWatchingStatus = client.onStatusChange((status) => {
      dispatch({ type: 'status', status: signalRealtimeStateFromSocket(status) });
    });

    /*
      Requirement 18.6. `websocketClient` has already resubscribed these channels by the
      time this fires — it does that from its own registry inside `handleOpen`, because the
      server clears a connection's authorised subscriptions when it closes. What is left
      for this hook is the second half: ask for a snapshot, because the gap between the
      drop and the reconnect is a gap in the frames and continuity must never be assumed.
    */
    const stopWatchingOpen = client.onOpen(() => requestSnapshot('reconnect'));

    client.acquire(BUILDER_SOCKET_PATH, BUILDER_RECONNECT_POLICY);

    const releases = channels.map((channel) =>
      client.subscribeChannel(channel, (frame) => dispatch({ type: 'frame', frame })),
    );

    // If the socket was already open when this page mounted, no `onOpen` will fire for it,
    // so the snapshot is requested directly. A page that opens mid-session needs the
    // current state just as much as one that reconnects.
    if (client.isConnected()) requestSnapshot('mount');

    return () => {
      // Requirement 18.8: the subscription is explicitly torn down when the page unmounts
      // or the channel set changes. Only this page's holds — another view may hold the
      // same channel, and `release` closes the socket only when the last hold goes.
      for (const release of releases) release();
      stopWatchingOpen();
      stopWatchingStatus();
      client.release();
    };
  }, [active, channelKey, client, requestSnapshot]); // eslint-disable-line react-hooks/exhaustive-deps

  // -- re-authenticate in place on a token refresh (Requirement 18.7) ------
  const authedTokenRef = useRef(readToken());

  useEffect(() => {
    if (!active) return undefined;

    const check = () => {
      const token = readToken();
      if (!token || token === authedTokenRef.current) return;
      // `reauthenticate` sends an `auth` frame on the OPEN connection and returns false
      // when there is none. A closed connection has no identity to refresh and the next
      // `connect()` reads the current token from storage anyway, so the refreshed token is
      // recorded as authenticated only when it was actually sent.
      if (client.reauthenticate(token)) authedTokenRef.current = token;
    };

    check();
    const timer = setInterval(check, TOKEN_WATCH_INTERVAL_MS);
    window.addEventListener('storage', check);
    return () => {
      clearInterval(timer);
      window.removeEventListener('storage', check);
    };
  }, [active, client]);

  // -- a reset when the page is looking at a different strategy ------------
  useEffect(() => {
    dispatch({ type: 'reset' });
  }, [askedStrategy]);

  // Requirement 18.3: a refused subscription is handed back so the page can say "this is
  // not yours" on screen. A refusal that only reached the console would be
  // indistinguishable from a deployment that is simply quiet.
  const refusals = useMemo(() => signalRefusalList(realtime), [realtime.refusals]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    realtime,
    status: realtime.status,
    connected: realtime.status === SIGNAL_REALTIME_STATES.CONNECTED,
    channels,
    deploymentIds: subscribedIds,
    signals: realtime.signals,
    refusals,
    deploymentsError,
    requestSnapshot,
    refreshDeployments,
  };
}

export default useSignalTraceRealtime;
