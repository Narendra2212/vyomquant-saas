/**
 * tests/unit/lib/singleSocket.test.jsx - production-launch-hardening task 1, CLUSTER C.
 *
 * Requirements 1.23 / 2.23, and the route-existence half of 1.25-1.29 / 2.28.
 * `design.md` §Hypothesized Root Cause (wave 3).
 *
 * WHAT THIS FILE IS
 * -----------------
 * **Bug condition exploration tests.** Every test in sections 1 and 2 is EXPECTED TO FAIL
 * against the current tree (`F`). The failure is the deliverable: it is the counterexample
 * that proves the defect exists, and the same assertion is what validates the fix. Nothing
 * here is a symptom patch and no assertion has been weakened to make a run green.
 *
 * THE DEFECT, IN ONE SENTENCE
 * ---------------------------
 * The application has one socket client and three places that open sockets, so a session that
 * has Billing and the live data pipeline mounted holds three connections against a design that
 * permits exactly one.
 *
 * The three sites:
 *
 * | # | Site | What it opens, as observed on `F` |
 * |---|---|---|
 * | 1 | `src/websocketClient.js:82` (via `connect`/`acquire`) | `wss://d7d88qs4jmch.cloudfront.net/ws/telemetry?token=<JWT>` - the shared client, the one that is supposed to be the only one |
 * | 2 | `src/pages/Billing.jsx:144` | `ws://localhost:3000/ws/user/<id>?token=<JWT>` - its own socket, its own reconnect loop, its own credential read, and its own origin |
 * | 3 | `src/contexts/DataPipelineContext.jsx:372` | `wss://d7d88qs4jmch.cloudfront.net/ws/telemetry?token=<JWT>/ws/market-data` - see below |
 *
 * Sites 1 and 3 take their origin from `CONFIG.wsBaseUrl` (`VITE_WS_URL`, which
 * `algo22-terminal/.env` sets to the live CloudFront distribution); site 2 builds its own from
 * `window.location`, which is why its host differs here and would differ in any deployment
 * where the page origin and `VITE_WS_URL` are not the same host.
 *
 * SITE 3 IS NOT MERELY A THIRD SOCKET. IT IS A BROKEN ONE
 * ------------------------------------------------------
 * `DataPipelineContext.jsx:370` builds its URL as `` `${wsClient.url}/ws/market-data` ``, and
 * `wsClient.url` already ends in `?token=<JWT>` (`websocketClient.js:76`). Concatenating a path
 * onto a URL that already has a query string does not produce a path - it extends the *last
 * query parameter's value*. Parsed:
 *
 *     pathname ................. /ws/telemetry
 *     searchParams.get('token')  <JWT>/ws/market-data
 *
 * So the socket connects to `/ws/telemetry` - a route that does exist - presenting a
 * credential with `/ws/market-data` glued onto the end, which cannot verify. The live data
 * feed has never worked. Three separate defects in one expression: a third socket, a
 * credential in the middle of a path, and a route that is never requested because it was
 * swallowed into a parameter value.
 *
 * And the route it meant to request does not exist either. `backend_app/api_ws/ws_routes.py`
 * registers nine WebSocket routes and `/ws/market-data` is not among them - section 3 asserts
 * that against the file rather than restating it, so the finding cannot go stale. (The string
 * `market-data` does not appear anywhere in `ws_routes.py`; a repo-wide grep of
 * `backend_app/**\/*.py` finds it only in prose and comments, never in a route decorator.)
 *
 * WHY "EXACTLY ONE" IS ASSERTED BY CONSTRUCTION COUNT
 * -------------------------------------------------
 * Requirement 2.23's own words: *"a vitest case asserting the global socket constructor is
 * called once across a mount of Billing plus the data pipeline."* Counting constructions of
 * the stubbed `WebSocket` is the only way "no second socket" is checkable at all -
 * `builderRealtime.test.jsx` makes the same argument for the same reason. A count of *open*
 * sockets would pass on three sockets where two happen to be closed again.
 *
 * THE DOUBLE
 * ----------
 * `RecordingWebSocket` is `FakeWebSocket` from `tests/unit/builderRealtime.test.jsx` and the
 * three `signalTrace*` suites - same class, same recording contract. It is declared inside
 * those test modules and never exported, so it cannot be imported here without executing them.
 *
 * HARNESS
 * -------
 * Real: `websocketClient`, `Billing`, `DataPipelineProvider`, `registryClient`. Doubles: the
 * `WebSocket` class, the axios instance, and the api module - the same two boundaries every
 * other page and builder suite stubs. No connection rule is replaced by a test-only version.
 */

import React, { useEffect } from 'react';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';

// ---------------------------------------------------------------------------
// The two stubbed boundaries. Hoisted, because both are imported at module scope.
// ---------------------------------------------------------------------------

const { mockBilling, mockApi, mockGet, mockPost, mockAxios } = vi.hoisted(() => {
  const mockBilling = {
    getPlans: vi.fn(),
    getEntitlements: vi.fn(),
    getInvoices: vi.fn(),
    getPaymentMethods: vi.fn(),
    createCheckout: vi.fn(),
    setCurrency: vi.fn(),
    cancelSubscription: vi.fn(),
    resumeSubscription: vi.fn(),
    openPortal: vi.fn(),
  };
  return {
    mockBilling,
    mockApi: {
      billing: mockBilling,
      exchange: { getAccounts: vi.fn() },
      user: { getConnectedExchanges: vi.fn() },
      market: { getSymbols: vi.fn() },
    },
    mockGet: vi.fn(),
    mockPost: vi.fn(),
    mockAxios: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn(), patch: vi.fn() },
  };
});

vi.mock('../../../src/api', () => ({
  api: mockApi,
  endpoints: mockApi,
  default: mockApi,
  get: (...args) => mockGet(...args),
  post: (...args) => mockPost(...args),
}));

vi.mock('../../../src/apiClient', () => ({
  default: mockAxios,
  get: (...args) => mockAxios.get(...args),
  post: (...args) => mockAxios.post(...args),
  put: (...args) => mockAxios.put(...args),
  del: (...args) => mockAxios.del(...args),
  patch: (...args) => mockAxios.patch(...args),
}));

import Billing from '../../../src/pages/Billing';
import wsClient from '../../../src/websocketClient';
import { DataPipelineProvider, useDataPipeline } from '../../../src/contexts/DataPipelineContext';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** A structurally-shaped JWT carrying nothing. No real credential is in this file. */
const SYNTHETIC_JWT =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' +
  '.eyJzdWIiOiJzeW50aGV0aWMtbm90LWEtcmVhbC11c2VyIn0' +
  '.c3ludGhldGlj';

const USER_ID = 'usr_single_socket_1';

/** `backend_app/api_ws/ws_routes.py`, read from the repo rather than restated. */
const WS_ROUTES_PY = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../../../backend_app/api_ws/ws_routes.py',
);

/**
 * Every path `@ws_router.websocket(...)` registers, in declaration order.
 *
 * Parsed from the file so the set cannot drift from the backend. `design.md`'s route-contract
 * clauses (2.28) generalise exactly this check; here it answers one question - is
 * `/ws/market-data` a route at all.
 */
const registeredWsRoutes = () => {
  const source = readFileSync(WS_ROUTES_PY, 'utf8');
  const found = [];
  const pattern = /@ws_router\.websocket\(\s*["']([^"']+)["']/g;
  let match = pattern.exec(source);
  while (match !== null) {
    found.push(match[1]);
    match = pattern.exec(source);
  }
  return found;
};

/** `"/ws/candles/{symbol}/{timeframe}"` -> a matcher for a concrete path. */
const routeMatcher = (route) => {
  const escaped = route.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`^${escaped.replace(/\\\{[^}]*\\\}/g, '[^/]+')}$`);
};

/**
 * Every `/ws/...` route named anywhere in `url`, including inside a parameter value.
 *
 * "Including inside a parameter value" is the whole point: `DataPipelineContext.jsx:370` puts
 * its route there, and a check that only read `new URL(url).pathname` would see
 * `/ws/telemetry`, find it registered, and report nothing wrong.
 */
const wsRoutesNamedIn = (url) => {
  const named = [];
  const pattern = /\/ws\/[^?&#]*/g;
  let match = pattern.exec(String(url));
  while (match !== null) {
    named.push(match[0]);
    match = pattern.exec(String(url));
  }
  return named;
};

// ---------------------------------------------------------------------------
// The one double: a WebSocket that goes nowhere and records every construction
// ---------------------------------------------------------------------------

let constructed = [];

class RecordingWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  constructor(url) {
    this.url = url;
    this.readyState = RecordingWebSocket.CONNECTING;
    this.sent = [];
    this.closes = 0;
    constructed.push(this);
  }

  send(text) {
    this.sent.push(text);
  }

  close() {
    this.closes += 1;
    this.readyState = RecordingWebSocket.CLOSED;
    if (this.onclose) this.onclose();
  }

  open() {
    this.readyState = RecordingWebSocket.OPEN;
    if (this.onopen) this.onopen();
  }
}

/** URLs with the credential blanked, so no failure message ever prints one. */
const redact = (url) =>
  String(url)
    .replace(/token=[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, 'token=<JWT>')
    .replace(/token=[^&/]+/g, 'token=<JWT>');

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

/** Reset the shared singleton - it is a module-level object. */
const resetSocketClient = () => {
  wsClient.reconnectEnabled = false;
  if (wsClient.reconnectTimeoutId) {
    clearTimeout(wsClient.reconnectTimeoutId);
    wsClient.reconnectTimeoutId = null;
  }
  wsClient.stopHeartbeat();
  wsClient.ws = null;
  wsClient.url = null;
  wsClient.refcount = 0;
  wsClient.acquiredPath = null;
  wsClient.savedReconnectPolicy = null;
  wsClient.channelSubscriptions.clear();
  wsClient.channelRefusals.clear();
  wsClient.subscriptions.clear();
  wsClient.statusListeners.clear();
  wsClient.openListeners.clear();
  wsClient.messageQueue = [];
  wsClient.reconnectAttempts = 0;
  wsClient.connectionStatus = 'disconnected';
  wsClient.expectedSequence = 1;
  wsClient.setReconnectPolicy({
    maxAttempts: 5,
    baseDelayMs: 1000,
    maxDelayMs: 30000,
    jitterMs: 250,
  });
};

/** Hands the test the live pipeline's own `connectLiveData`, through the real context. */
const pipelineHandle = {};

const PipelineProbe = () => {
  const pipeline = useDataPipeline();
  useEffect(() => {
    pipelineHandle.connectLiveData = pipeline.connectLiveData;
    pipelineHandle.mode = pipeline.mode;
  });
  return null;
};

/**
 * The combined mount Requirement 2.23 names: the shared client, Billing, and the live data
 * pipeline, in the order a real session reaches them.
 *
 * `wsClient.acquire` first, because that is what the shell does before any page renders and
 * because `DataPipelineContext.jsx:370` reads `wsClient.url` - a pipeline mounted with no
 * shared connection would build `undefined/ws/market-data` and the finding in section 2 would
 * be about the test rather than about the code.
 */
const mountTheWholeSession = async () => {
  wsClient.acquire('/ws/telemetry');

  let view;
  await act(async () => {
    view = render(
      <DataPipelineProvider mode="live">
        <PipelineProbe />
        <Billing />
      </DataPipelineProvider>,
    );
  });
  await act(async () => {});

  await act(async () => {
    pipelineHandle.connectLiveData('BTC/USDT', '5m');
  });

  return view;
};

beforeEach(() => {
  constructed = [];
  global.WebSocket = RecordingWebSocket;
  window.WebSocket = RecordingWebSocket;

  window.localStorage.setItem('token', SYNTHETIC_JWT);
  window.localStorage.setItem('userId', USER_ID);
  window.sessionStorage.setItem('token', SYNTHETIC_JWT);

  mockBilling.getPlans.mockResolvedValue({ plans: [], currency: 'USD', currency_symbol: '$' });
  mockBilling.getEntitlements.mockResolvedValue({ plan: 'pro', features: [] });
  mockBilling.getInvoices.mockResolvedValue([]);
  mockBilling.getPaymentMethods.mockResolvedValue([]);
  mockApi.exchange.getAccounts.mockResolvedValue([]);
  mockApi.user.getConnectedExchanges.mockResolvedValue([]);
  mockApi.market.getSymbols.mockResolvedValue([]);
  mockGet.mockResolvedValue([]);
  mockPost.mockResolvedValue({});
  // The registry timeframe fetch. Refused rather than served: this suite counts sockets, and
  // `loadTimeframes` never rejects (`registryClient.js:823`), so a refusal is a settled state
  // rather than an unhandled one.
  mockAxios.get.mockRejectedValue(new Error('registry not served in this suite'));

  resetSocketClient();
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  resetSocketClient();
  vi.clearAllTimers();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.sessionStorage.clear();
  delete pipelineHandle.connectLiveData;
});

// ══════════════════════════════════════════════════════════════════════════
// 1. EXACTLY ONE SOCKET  (Requirements 1.23 / 2.23)
// ══════════════════════════════════════════════════════════════════════════

describe('across a mount of Billing plus the data pipeline', () => {
  it('the global WebSocket constructor is called exactly once', async () => {
    /*
      COUNTEREXAMPLE OBSERVED ON `F` - three constructions, credentials redacted by position:

          1  wss://d7d88qs4jmch.cloudfront.net/ws/telemetry?token=<JWT>
             websocketClient.js:82, via acquire -> connect

          2  ws://localhost:3000/ws/user/usr_single_socket_1?token=<JWT>
             Billing.jsx:144

          3  wss://d7d88qs4jmch.cloudfront.net/ws/telemetry?token=<JWT>/ws/market-data
             DataPipelineContext.jsx:372

      Three connections, three authentications, three reconnect schedules, and a token refresh
      that reauthenticates one of them (`websocketClient.js:740` - `reauthenticate` is a method
      on the shared client and nothing else has one). Billing's carries its own uncancellable
      reconnect timer as well (see `tests/unit/pages/billingSocketLifecycle.test.jsx`), so the
      count is a floor rather than a ceiling.

      The fix wave 3 designs is to multiplex sites 2 and 3 over site 1, which is why the
      preservation section below pins `acquire`/`release` and `subscribeChannel`: they are the
      machinery that makes one socket serve three callers, and they already work.
    */
    await mountTheWholeSession();

    expect(constructed.length, (
      `${constructed.length} sockets were constructed across one session: ` +
      `${constructed.map((socket) => redact(socket.url)).join(' | ')}. ` +
      'The design permits exactly one, multiplexed through src/websocketClient.js.'
    )).toBe(1);
  });

  it('no socket is opened outside the shared client', async () => {
    /*
      The same finding stated as ownership rather than as a count, because the two fail
      differently. A count of one could be reached by deleting the shared client and keeping
      Billing's own socket - which would satisfy 2.23's number and lose the refcount, the
      channel registry, the jittered backoff and the in-place reauthentication.

      COUNTEREXAMPLE OBSERVED ON `F`: two of the three sockets are constructed by callers that
      hold no reference to `wsClient` at all beyond reading its `url` string.
    */
    await mountTheWholeSession();

    // `wsClient.ws` is the socket the shared client owns. Everything else is unowned.
    const unowned = constructed.filter((socket) => socket !== wsClient.ws);
    expect(unowned.map((socket) => redact(socket.url)), (
      'these sockets are owned by no client: they have their own credential read, their own ' +
      'reconnect policy, and no one closes them but the component that opened them'
    )).toEqual([]);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 2. THE URL THE PIPELINE BUILDS  (`DataPipelineContext.jsx:370-372`)
// ══════════════════════════════════════════════════════════════════════════

describe("the live pipeline's socket URL", () => {
  it('names only routes the backend registers', async () => {
    /*
      COUNTEREXAMPLE OBSERVED ON `F`:

          constructed ... wss://d7d88qs4jmch.cloudfront.net/ws/telemetry?token=<JWT>/ws/market-data
          routes named ... ['/ws/telemetry', '/ws/market-data']
          registered ..... /ws/telemetry, /ws/ticker/{symbol}, /ws/orderbook/{symbol},
                           /ws/candles/{symbol}/{timeframe}, /ws/user/{user_id},
                           /ws/pnl/{user_id}, /ws/dashboard, /ws/strategy/{strategy_id},
                           /ws/signal-trace
          unregistered ... ['/ws/market-data']

      Asserted over every route named *anywhere in the string*, not over `new URL(...).pathname`.
      Reading the pathname alone returns `/ws/telemetry`, which is registered, and the check
      would pass while the feed stayed dead - because the route the code meant to request is
      inside a query parameter value, not in the path.

      This assertion is fix-agnostic on purpose. It passes if wave 3 multiplexes market data
      over `/ws/telemetry` (no second route named at all) and it passes if someone implements
      `/ws/market-data` on the backend. It fails only while the frontend names a route that
      does not exist, which is the defect.
    */
    await mountTheWholeSession();

    const routes = registeredWsRoutes();
    const matchers = routes.map(routeMatcher);
    const unregistered = [];
    for (const socket of constructed) {
      for (const named of wsRoutesNamedIn(socket.url)) {
        if (!matchers.some((matcher) => matcher.test(named))) unregistered.push(named);
      }
    }

    expect(unregistered, (
      `the frontend opened a socket naming ${unregistered.join(', ')}, which ` +
      `backend_app/api_ws/ws_routes.py does not register. It registers ${routes.length}: ` +
      `${routes.join(', ')}.`
    )).toEqual([]);
  });

  it('does not put a path after its query string', async () => {
    /*
      The malformedness, asserted on its own because it is a distinct defect from the missing
      route and would survive a fix to it.

      COUNTEREXAMPLE OBSERVED ON `F`, parsed:

          url ......................... wss://d7d88qs4jmch.cloudfront.net/ws/telemetry
                                        ?token=<JWT>/ws/market-data
          pathname .................... /ws/telemetry
          searchParams.get('token') ... <JWT>/ws/market-data

      `/ws/market-data` did not become a path. It became the tail of the credential.
      `` `${wsClient.url}/ws/market-data` `` (`:370`) concatenates onto a string that already
      ends in `?token=...` (`websocketClient.js:76`), so the server receives a connection to
      `/ws/telemetry` presenting a token that cannot verify - and `ws_routes.py:333` fails
      closed. The live tick feed has never delivered a frame.

      Stated as "no query parameter value contains a path segment" so it holds for any
      parameter and any route, rather than pinning today's two names.
    */
    await mountTheWholeSession();

    const offenders = [];
    for (const socket of constructed) {
      const parsed = new URL(String(socket.url));
      for (const [name, value] of parsed.searchParams.entries()) {
        if (value.includes('/')) {
          offenders.push(`${redact(socket.url)} -> ${name} holds a path segment`);
        }
      }
    }

    expect(offenders, (
      'a route was concatenated onto a URL that already carried a query string, so it landed ' +
      `inside a parameter value instead of in the path: ${offenders.join('; ')}`
    )).toEqual([]);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 3. THE ROUTE'S ABSENCE, READ FROM THE BACKEND  (`ws_routes.py`)
// ══════════════════════════════════════════════════════════════════════════

describe('backend_app/api_ws/ws_routes.py', () => {
  it('registers nine WebSocket routes and none of them is /ws/market-data', () => {
    /*
      Recorded rather than asserted-about-the-frontend, so the finding is checkable and cannot
      go stale: if someone implements the route, this test starts failing and says so.

      MEASURED ON `F` - nine routes, at these lines:

          :333   /ws/telemetry
          :368   /ws/ticker/{symbol}
          :508   /ws/orderbook/{symbol}
          :568   /ws/candles/{symbol}/{timeframe}
          :641   /ws/user/{user_id}
          :866   /ws/pnl/{user_id}
          :918   /ws/dashboard
          :1079  /ws/strategy/{strategy_id}
          :1190  /ws/signal-trace

      `/ws/market-data` is not among them, and the substring `market-data` does not appear in
      the file at all. It is also the nine-route set `design.md` calls "an auth change across
      nine WebSocket routes", which is a second, independent confirmation that this parse found
      all of them.
    */
    const routes = registeredWsRoutes();

    expect(routes, 'the parse must have found the declarations for this to mean anything')
      .toHaveLength(9);
    expect(routes).toContain('/ws/telemetry');
    expect(routes).not.toContain('/ws/market-data');
    expect(readFileSync(WS_ROUTES_PY, 'utf8')).not.toContain('market-data');
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 4. PRESERVATION - the machinery the wave-3 fix depends on being sound
// ══════════════════════════════════════════════════════════════════════════

describe('preservation: one connection per session already works when it is used', () => {
  it('test_preserved_acquire_and_release_refcount_under_two_holds', () => {
    /*
      Passes on `F` and must keep passing. This is why wave 3's fix is "route Billing and the
      pipeline through `wsClient`" rather than "write a socket manager": `acquire` / `release`
      (`websocketClient.js:774-818`) already implement one-connection-per-session with a
      refcount, and two holds on it already collapse onto one socket.

      Two holds specifically, because one hold cannot distinguish a refcount from an
      `if (!this.ws)` guard - the second `acquire` is the one that must NOT construct, and the
      first `release` is the one that must NOT close.
    */
    expect(wsClient.holdCount()).toBe(0);

    expect(wsClient.acquire('/ws/telemetry')).toBe(1);
    expect(constructed.length).toBe(1);

    // `connectionStatus` is 'connecting' here, so this does not take the
    // `failed`/`disconnected` re-`connect()` branch at `:783`.
    expect(wsClient.acquire('/ws/telemetry')).toBe(2);
    expect(constructed.length, 'a second hold must reuse the one socket').toBe(1);

    expect(wsClient.release()).toBe(1);
    expect(constructed[0].closes, 'someone is still holding it').toBe(0);

    expect(wsClient.release()).toBe(0);
    expect(constructed[0].closes, 'the last release closes it').toBe(1);
    expect(wsClient.holdCount()).toBe(0);

    // Releasing past zero is a no-op rather than a negative count.
    expect(wsClient.release()).toBe(0);
  });

  it('test_preserved_subscribeChannel_refcounts_per_channel', () => {
    /*
      Passes on `F` and must keep passing. Per-channel refcounting
      (`websocketClient.js:665-722`) is the other half of the multiplexing wave 3 needs: two
      holders of one channel produce ONE `subscribe` frame, and the `unsubscribe` is sent only
      when the last holder goes.

      Both directions are asserted, because each fails differently. An over-eager subscribe
      duplicates server-side work and delivers every frame twice; an over-eager unsubscribe
      silently stops delivering to a holder that is still listening, which is the harder of the
      two to notice and the one that would make a multiplexed Billing quietly stop refreshing.
    */
    wsClient.connect('/ws/telemetry');
    const socket = constructed[0];
    socket.readyState = RecordingWebSocket.OPEN;

    const first = [];
    const second = [];
    const releaseFirst = wsClient.subscribeChannel('strategy.stg_1', (frame) => first.push(frame));
    const releaseSecond = wsClient.subscribeChannel('strategy.stg_1', (frame) =>
      second.push(frame),
    );

    const framesOf = (action) =>
      socket.sent.map((text) => JSON.parse(text)).filter((frame) => frame.action === action);

    expect(framesOf('subscribe'), 'one channel, one subscribe frame').toHaveLength(1);
    expect(framesOf('subscribe')[0]).toEqual({ action: 'subscribe', channel: 'strategy.stg_1' });
    expect(wsClient.subscribedChannels()).toEqual(['strategy.stg_1']);

    // Both holders receive every frame naming the channel.
    wsClient.handleMessage({
      data: JSON.stringify({ type: 'strategy.canvas_state', channel: 'strategy.stg_1' }),
    });
    expect(first).toHaveLength(1);
    expect(second).toHaveLength(1);

    // The first release tells the server nothing: the channel is still held.
    releaseFirst();
    expect(framesOf('unsubscribe'), 'a channel still held is not unsubscribed').toHaveLength(0);
    expect(wsClient.subscribedChannels()).toEqual(['strategy.stg_1']);

    // And the surviving holder is still delivered to.
    wsClient.handleMessage({
      data: JSON.stringify({ type: 'strategy.canvas_state', channel: 'strategy.stg_1' }),
    });
    expect(first, 'the released holder stops receiving').toHaveLength(1);
    expect(second, 'the holder that kept its hold keeps receiving').toHaveLength(2);

    // The last release is the one the server hears about.
    releaseSecond();
    expect(framesOf('unsubscribe')).toHaveLength(1);
    expect(framesOf('unsubscribe')[0]).toEqual({
      action: 'unsubscribe',
      channel: 'strategy.stg_1',
    });
    expect(wsClient.subscribedChannels()).toEqual([]);
  });

  it('test_preserved_the_shared_client_opens_one_socket_on_its_own', () => {
    /*
      Passes on `F`. The control for section 1: the shared client, used alone, already
      satisfies Requirement 2.23. Section 1's three constructions are not a defect in
      `websocketClient` - they are two callers bypassing it.
    */
    wsClient.acquire('/ws/telemetry');
    expect(constructed.length).toBe(1);
    expect(String(constructed[0].url)).toContain('/ws/telemetry');
    expect(constructed[0]).toBe(wsClient.ws);
  });
});
