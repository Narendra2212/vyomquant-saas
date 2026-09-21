/**
 * tests/unit/lib/socketCredential.test.js - production-launch-hardening task 1, CLUSTER C
 * (plus task 2's `fast-check` half, which `tasks.md` places in this file by name).
 *
 * Requirements 1.21 / 2.21. `design.md` §Hypothesized Root Cause (wave 3), correction 3.
 *
 * WHAT THIS FILE IS
 * -----------------
 * **Bug condition exploration tests.** Every test in sections 1-3 is EXPECTED TO FAIL
 * against the current tree (`F`). The failure is the deliverable: it is the counterexample
 * that proves the defect exists, and the same assertion is what validates the fix. Nothing
 * here is a symptom patch and no assertion has been weakened to make a run green.
 *
 * THE DEFECT, IN ONE SENTENCE
 * ---------------------------
 * Both socket builders put the session JWT in the URL query string, where it is written to
 * CloudFront and ALB access logs, to browser history, and to `Referer` on anything the page
 * subsequently loads.
 *
 * The two sites, and the store each reads:
 *
 * | Site | Store | Origin | What it builds |
 * |---|---|---|---|
 * | `src/pages/Billing.jsx:138,142` | `localStorage` | `window.location` | `ws://<page host>/ws/user/<id>?token=<JWT>` |
 * | `src/websocketClient.js:73-76` | `sessionStorage` | `CONFIG.wsBaseUrl` (`VITE_WS_URL`) | `wss://<configured host><path>?token=<JWT>` (`&` when the path already carries a query) |
 *
 * The origin column is a third, smaller finding recorded here rather than asserted: Billing
 * does not read `CONFIG` at all, so it connects to whatever origin served the page while the
 * shared client connects to `VITE_WS_URL`. With `algo22-terminal/.env`'s
 * `VITE_WS_URL=wss://d7d88qs4jmch.cloudfront.net` those are two different hosts, and the
 * counterexamples below show both because that is what `F` produced.
 *
 * `websocketClient.js:77` redacts the credential *from its own console line* - 
 * `.replace(/token=[^&]+/, 'token=[REDACTED]')` - which is the clearest available evidence
 * that the author knew the value was sensitive. The redaction applies to the log statement
 * only. The URL itself is unchanged, and the URL is what reaches the CDN and the load
 * balancer.
 *
 * WHY BOTH SITES ARE ASSERTED, AND WHY THAT IS NOT REDUNDANT
 * ---------------------------------------------------------
 * `design.md` correction 3, and the reason this file exists rather than a single case inside
 * `billingSocketLifecycle.test.jsx`: **asserting only Billing's URL would pass on a fix that
 * merely consolidates the exposure.** Wave 3's shape is "one socket, through the shared
 * client" - so the natural first move is to delete Billing's `new WebSocket` and route it
 * through `wsClient`. That removes one `?token=` and leaves the other, and a Billing-only
 * test would go green on it while every socket in the application still authenticates through
 * the query string. The credential clause and the single-socket clause are independent, and
 * only a both-sites assertion keeps them independent.
 *
 * TWO TOKEN STORES FOR ONE SESSION (section 3)
 * -------------------------------------------
 * The table above records a second finding that is not in `bugfix.md`'s numbered list: the
 * two sites do not read the same store. `Billing.jsx:138` reads `localStorage`;
 * `websocketClient.js:73` and `:118` read `sessionStorage`. One session, two credential
 * stores, and no code that copies between them - so the two sockets can hold different
 * tokens, or one can hold a token while the other has none. It also decides how long the
 * credential outlives the tab: `sessionStorage` is cleared when the tab closes and
 * `localStorage` is not, so the same JWT has two different lifetimes depending on which
 * reader you ask. Section 3 asserts one store, behaviourally - by showing that neither store
 * alone serves both readers.
 *
 * THE PROPERTY (section 4)
 * -----------------------
 * `tasks.md` task 2: *"over generated tokens and paths, **no** constructed socket URL
 * contains the token in any position"*. "Any position" is the load-bearing phrase. A fix that
 * moved the credential out of `?token=` and into the path (`/ws/telemetry/<JWT>`), or into a
 * fragment, or into a differently-named parameter, would satisfy a `token=`-only assertion
 * and log the credential exactly as before - and `DataPipelineContext.jsx:371` shows that
 * credential-in-the-path is not a hypothetical in this codebase. The property is a substring
 * search over the whole URL, in raw and percent-encoded form.
 *
 * CREDENTIALS IN THIS FILE
 * -----------------------
 * `bugfix.md` §2.20: a secret is referenced by key name and by position, never by value.
 * `SYNTHETIC_JWT` below is three base64url segments that decode to nothing of value; the
 * generated tokens are random. No real token is in this file, and every failure message
 * redacts the value it observed.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';
import fc from 'fast-check';
// §5b reads `src/websocketClient.js` as text to pin the removal marker for task 8.2c's
// compatibility shim. Same approach as `tests/unit/guards/no-local-tokens.test.js`: a
// temporary exception is pinned in the source, not just in behaviour, so it cannot be
// forgotten. Goes when the shim goes.
//
// `__dirname`, not `new URL(…, import.meta.url)` — `tests/unit/guards/source-scan.js`
// documents why at length: Vite's `asset-import-meta-url` plugin rewrites that literal
// expression into an asset lookup, the read then raises, and the file reports "0 tests"
// instead of failing. This file did exactly that before switching to `__dirname`.
import { readFileSync } from 'node:fs';
import nodePath from 'node:path';

// ---------------------------------------------------------------------------
// The api stub. Hoisted, because Billing imports `api` at module scope.
// ---------------------------------------------------------------------------

const { mockBilling, mockAuth } = vi.hoisted(() => ({
  mockBilling: {
    getPlans: vi.fn(),
    getEntitlements: vi.fn(),
    getInvoices: vi.fn(),
    getPaymentMethods: vi.fn(),
    createCheckout: vi.fn(),
    setCurrency: vi.fn(),
    cancelSubscription: vi.fn(),
    resumeSubscription: vi.fn(),
    openPortal: vi.fn(),
  },
  // `GET /api/auth/me` (`routers/auth.py:123`), which is where Billing gets the user id for
  // `/ws/user/{user_id}` since task 8.3. A network boundary, so it is doubled.
  mockAuth: { getMe: vi.fn() },
}));

/**
 * `isAuthenticated` and `getToken` are NOT doubled, and that is the point of section 3.
 *
 * They are the shipped helpers, imported from `src/apiClient` — the module that owns the
 * store and whose axios request interceptor reads it on every HTTP call. Section 3 asks
 * which store *serves a reader*; a stub would answer with this file's opinion instead of
 * with the application's, and would keep passing on a Billing that had quietly gone back to
 * reading a store of its own.
 */
vi.mock('../../../src/api', async () => {
  const { isAuthenticated, getToken } = await import('../../../src/apiClient');
  const api = { billing: mockBilling, auth: mockAuth };
  return { api, endpoints: api, default: api, isAuthenticated, getToken };
});

import Billing from '../../../src/pages/Billing';
import wsClient, { WS_TICKET_FALLBACK } from '../../../src/websocketClient';
import { useWsTicketStub } from '../helpers/wsTicketStub';

/**
 * WHAT TASK 8.2 CHANGED, AND WHAT IT DID NOT
 * -----------------------------------------
 * The fix exchanges the JWT for a **single-use, ≤ 30 s opaque ticket** over HTTPS
 * (`POST /api/auth/ws-ticket`) and puts *that* in the query string. Two consequences
 * reach this file, and neither weakens an assertion above.
 *
 * 1. `connect()` is asynchronous now. It cannot be otherwise: the credential has to be
 *    fetched before the socket can be constructed. The calls below are awaited. The
 *    assertions they make about the constructed URL are unchanged, character for
 *    character — `expectNoCredentialInUrl` is the same function it was on `F`.
 *
 * 2. `?token=` became `?ticket=`, so section 3's "which store serves a reader" probe reads
 *    the `ticket` parameter instead. That test still fails, and still for 8.3's reason:
 *    Billing reads `localStorage`, the client reads `sessionStorage`.
 *
 * `useWsTicketStub` is the double for the ticket endpoint. It issues opaque, dot-free,
 * distinct-per-request values, so a URL that carries one cannot satisfy the JWT-shape
 * check by accident and a test can tell one attempt's ticket from the next's.
 *
 * WHAT TASK 8.3 CHANGED, AND WHAT IT DID NOT
 * -----------------------------------------
 * The store table at the top of this file recorded `F` as it was: Billing read
 * `localStorage`, the shared client read `sessionStorage`. Task 8.3 removed the first of
 * those. Billing no longer reads a store at all — it asks `isAuthenticated()`, the HTTP
 * client's own helper, whether a session exists, and asks `GET /api/auth/me` whose it is.
 * `sessionStorage` won because that is what `apiClient`'s request interceptor reads
 * (`apiClient.js:645`) and what every writer in the app writes.
 *
 * Two consequences reach this file. The fixtures now put the credential in
 * `sessionStorage`, because that is the one store a session has. And Billing's mount is
 * asynchronous: the user id is a request and the ticket is another, so the socket is
 * constructed several microtask turns after the render rather than during it. Every
 * assertion about the constructed URL is unchanged.
 *
 * `localStorage.getItem('userId')` is gone too, and it was the other half of defect 67:
 * nothing in `src/` ever wrote `userId`, so that guard was unsatisfiable and this effect had
 * never run once outside a test that populated the key by hand. The positive case — the
 * subscription actually opening for an authenticated user with a resolvable id — is asserted
 * in `tests/unit/pages/billingSocketLifecycle.test.jsx` §0, where the whole lifecycle lives.
 */
const wsTickets = useWsTicketStub();

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** A structurally-shaped JWT carrying nothing. Three base64url segments, no credential. */
const SYNTHETIC_JWT =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' +
  '.eyJzdWIiOiJzeW50aGV0aWMtbm90LWEtcmVhbC11c2VyIn0' +
  '.c3ludGhldGlj';

const USER_ID = 'usr_socket_credential_1';

/**
 * Every WebSocket route `backend_app/api_ws/ws_routes.py` actually registers, with concrete
 * parameters. Read from the file, in declaration order: `:333`, `:368`, `:508`, `:568`,
 * `:641`, `:866`, `:918`, `:1079`, `:1190`.
 *
 * The generator draws from this set rather than from arbitrary strings because the property
 * is about what the client does with a *real* route, and because `/ws/dashboard` is the one
 * that takes `user_id` as a query parameter - which is the `hasQuery` branch at
 * `websocketClient.js:74`, where the separator becomes `&` and a naive URL check stops
 * matching.
 */
const WS_ROUTES = Object.freeze([
  '/ws/telemetry',
  '/ws/ticker/BTCUSDT',
  '/ws/orderbook/BTCUSDT',
  '/ws/candles/BTCUSDT/5m',
  `/ws/user/${USER_ID}`,
  `/ws/pnl/${USER_ID}`,
  `/ws/dashboard?user_id=${USER_ID}`,
  '/ws/strategy/stg_1',
  '/ws/signal-trace',
  // The `hasQuery` branch again, with two parameters already present.
  '/ws/candles/ETHUSDT/1h?replay=1&from=0',
]);

// ---------------------------------------------------------------------------
// The one double: a WebSocket that goes nowhere and records every construction
//
// Same class, same recording contract, as `FakeWebSocket` in
// `tests/unit/builderRealtime.test.jsx` and the three `signalTrace*` suites. It is declared
// inside those test modules and never exported, so it cannot be imported here without
// executing them; this is that double, not a second design of one.
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

  /**
   * The server refuses the handshake: closed with a code, never opened.
   *
   * Added for §5 (task 8.2c). `close()` above fires `onclose()` with no argument, which is
   * what a *local* teardown looks like and is deliberately left alone — the fallback the
   * shim performs is keyed on an explicit close code, so the two must be distinguishable
   * doubles. `4001` is what every route in `api_ws/ws_routes.py` closes with; `1006` is
   * what a browser reports for the same refusal, because those routes close before
   * `accept()`.
   *
   * @param {number} code
   * @param {string} [reason]
   */
  refuse(code, reason = '') {
    this.closes += 1;
    this.readyState = RecordingWebSocket.CLOSED;
    if (this.onclose) this.onclose({ code, reason, wasClean: false });
  }
}

// ---------------------------------------------------------------------------
// Assertions about a URL, stated once
// ---------------------------------------------------------------------------

/** The query parameter names on `url`, whatever the ws/wss scheme. */
const queryParams = (url) => Array.from(new URL(String(url)).searchParams.keys());

/**
 * A JWT-shaped substring of `url`, or `null`.
 *
 * Deliberately independent of `SYNTHETIC_JWT`: it matches the *shape* - three base64url runs
 * separated by dots - so it catches a credential this file never generated, including one
 * sitting in a path segment or a fragment rather than a parameter.
 */
const jwtShapedSubstring = (url) => {
  const match = /[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{6,}/.exec(String(url));
  return match ? match[0] : null;
};

/** `url` with any credential blanked, so no failure message ever prints one. */
const redact = (url) =>
  String(url)
    .replace(/token=[^&]*/g, 'token=<JWT>')
    .replace(/[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{6,}/g, '<JWT>');

/**
 * The single assertion this file makes about a constructed URL, in one place.
 *
 * Three separate claims, because they fail for three different reasons: the parameter name
 * (a fix that renames it), the shape (a fix that relocates it), and the exact value (the
 * belt-and-braces check that this specific credential did not travel).
 */
const expectNoCredentialInUrl = (url, token, where) => {
  const params = queryParams(url);
  expect(params, `${where}: ${redact(url)} carries a token parameter`).not.toContain('token');

  const shaped = jwtShapedSubstring(url);
  expect(shaped, `${where}: ${redact(url)} carries a JWT-shaped substring`).toBeNull();

  expect(
    String(url).includes(token),
    `${where}: the credential appears verbatim in ${redact(url)}`,
  ).toBe(false);
  expect(
    String(url).includes(encodeURIComponent(token)),
    `${where}: the credential appears percent-encoded in ${redact(url)}`,
  ).toBe(false);
};

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

/** Reset the shared singleton - it is a module-level object, as builderRealtime.test.jsx notes. */
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
  // task 8.2c. The degraded credential mode is sticky **for the session**, which is exactly
  // what §5 asserts — and a singleton's session is the whole test file, so it is reset here
  // with everything else. Without this line one §5 case would leave every later test,
  // including §4's property, presenting the legacy credential. Goes with the shim.
  wsClient.credentialMode = 'ticket';
  wsClient.presentedCredential = null;
  wsClient.socketOpened = false;
  wsClient.credentialFallbackLogged = false;
};

/**
 * Mount Billing and let the two requests its subscription depends on settle.
 *
 * Since task 8.3 the socket is not constructed during the render: the effect resolves the
 * user id over HTTP first, and the shared client then mints a ticket, so the construction is
 * several microtask turns downstream of the mount. `advanceTimersByTimeAsync(0)` crosses a
 * real macrotask boundary, which drains the whole chain; it is called twice so a suite that
 * later adds another awaited hop does not start silently asserting on an unmounted socket.
 */
const mountBilling = async () => {
  let view;
  await act(async () => {
    view = render(React.createElement(Billing));
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);
  });
  return view;
};

beforeEach(() => {
  constructed = [];
  global.WebSocket = RecordingWebSocket;
  window.WebSocket = RecordingWebSocket;

  // The shape `routers/auth.py:123` returns. `id` is the JWT `sub`, which is what
  // `/ws/user/{user_id}` authorises the path segment against.
  mockAuth.getMe.mockResolvedValue({ id: USER_ID, email: 'billing@example.test', role: 'user' });

  mockBilling.getPlans.mockResolvedValue({ plans: [], currency: 'USD', currency_symbol: '$' });
  mockBilling.getEntitlements.mockResolvedValue({ plan: 'pro', features: [] });
  mockBilling.getInvoices.mockResolvedValue([]);
  mockBilling.getPaymentMethods.mockResolvedValue([]);
  mockBilling.setCurrency.mockResolvedValue({ ok: true });

  window.localStorage.clear();
  window.sessionStorage.clear();
  resetSocketClient();

  vi.useFakeTimers();
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  resetSocketClient();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

// ══════════════════════════════════════════════════════════════════════════
// 1. BILLING'S SOCKET  (`Billing.jsx:138,142`, Requirements 1.21 / 2.21)
// ══════════════════════════════════════════════════════════════════════════

describe("Billing's socket URL", () => {
  it('carries no credential', async () => {
    /*
      COUNTEREXAMPLE OBSERVED ON `F` (src/pages/Billing.jsx:142), credential redacted by
      position:

          ws://localhost:3000/ws/user/usr_socket_credential_1?token=<JWT>
                                                              ^^^^^^^^^^^
          query parameters: ['token']

      The scheme is `ws:` under jsdom because `window.location.protocol` is `http:`; in
      production the same expression yields `wss://app.vyomquant.in/ws/user/<id>?token=<JWT>`.
      The scheme is the only part that differs, and it is not the part that leaks: a `wss`
      URL's query string is still in the CloudFront access log, the ALB access log and
      `window.history`.

      `:138` is where the value comes from - `localStorage.getItem('token')`, the raw session
      JWT, interpolated at `:142` with no encoding and no ticket exchange.

      The credential is in `sessionStorage` here since task 8.3, because that is now the one
      store a session has. What is asserted about the URL did not change.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await mountBilling();

    expect(constructed.length, 'the effect must have built a URL for this to mean anything')
      .toBe(1);
    expectNoCredentialInUrl(constructed[0].url, SYNTHETIC_JWT, 'Billing.jsx:142');
  });

  it('test_preserved_still_names_the_user_route_it_needs', async () => {
    /*
      Passes on `F` and must keep passing. Moving the credential out of the URL must not move
      the *route* out of it: `/ws/user/{user_id}` is a real endpoint
      (`ws_routes.py:641`) and the page's subscription updates arrive on it. Pinned because
      the obvious fix - routing Billing through `wsClient` - changes the path as well as the
      credential, and the path change has to be deliberate rather than incidental.

      Since task 8.3 the id in that path comes from `GET /api/auth/me` rather than from
      `localStorage.getItem('userId')`, a key nothing in `src/` ever wrote. Same route, a
      source of truth that exists.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await mountBilling();

    expect(constructed.length, 'the effect must have opened a socket').toBe(1);
    expect(String(constructed[0].url)).toContain(`/ws/user/${USER_ID}`);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 2. THE SHARED CLIENT'S SOCKET  (`websocketClient.js:73-76`, Requirements 1.21 / 2.21)
// ══════════════════════════════════════════════════════════════════════════

describe("websocketClient.connect()'s URL", () => {
  it('carries no credential', async () => {
    /*
      COUNTEREXAMPLE OBSERVED ON `F` (src/websocketClient.js:76), credential redacted by
      position:

          wss://d7d88qs4jmch.cloudfront.net/ws/telemetry?token=<JWT>
                                                        ^^^^^^^^^^^
          query parameters: ['token']

      That host is `VITE_WS_URL` from `algo22-terminal/.env`, and it is the live CloudFront
      distribution - so this is not a localhost artefact of the test environment. The
      production build uses `wss://app.vyomquant.in` (`.env.production`), which puts the same
      credential in the ALB access log instead of the CloudFront one.

      This is the site a Billing-only assertion would miss. It is also the one that matters
      more in aggregate: `wsClient` is the shared client, so this URL is built for every
      socket the rest of the application opens, on every reconnect
      (`scheduleReconnect` -> `connect`, `:392-396`), for the life of the session.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await wsClient.connect('/ws/telemetry');

    expect(constructed.length, 'connect() must have built a URL for this to mean anything')
      .toBe(1);
    expectNoCredentialInUrl(constructed[0].url, SYNTHETIC_JWT, 'websocketClient.js:76');
    // Asserted on the client's own record too, because `this.url` is what `scheduleReconnect`
    // reads back (`:394`) and what `DataPipelineContext.jsx:371` concatenates onto.
    expectNoCredentialInUrl(wsClient.url, SYNTHETIC_JWT, 'wsClient.url');
  });

  it('carries no credential on a path that already has a query string', async () => {
    /*
      `:74-75` switches the separator to `&` when the path already carries a query, so the
      credential lands mid-query rather than first. Asserted separately because it is the
      shape that defeats the simplest possible check (`url.endsWith(...)`, or a regex anchored
      on `?token=`) and because `/ws/dashboard` - which takes `user_id` as a query parameter
      (`ws_routes.py:918-920`) - is a route the application really uses.

      COUNTEREXAMPLE OBSERVED ON `F`:

          wss://d7d88qs4jmch.cloudfront.net/ws/dashboard?user_id=usr_socket_credential_1&token=<JWT>
          query parameters: ['user_id', 'token']
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await wsClient.connect(`/ws/dashboard?user_id=${USER_ID}`);

    expect(constructed.length).toBe(1);
    expect(queryParams(constructed[0].url)).toContain('user_id');
    expectNoCredentialInUrl(constructed[0].url, SYNTHETIC_JWT, 'websocketClient.js:76 (&)');
  });

  it('test_preserved_still_connects_when_there_is_no_credential', () => {
    /*
      Passes on `F` and must keep passing - `:75`'s `token ? ... : ''`. A session with no
      token still builds a URL and still attempts the connection, and the server refuses it
      (`ws_routes.py` fails closed on every route). That is the correct division of labour:
      the client does not decide authorisation. A fix that started throwing here would turn a
      server-side refusal into a client-side crash.
    */
    wsClient.connect('/ws/telemetry');

    expect(constructed.length).toBe(1);
    expect(queryParams(constructed[0].url)).toEqual([]);
    expect(String(constructed[0].url)).toContain('/ws/telemetry');
  });

  it('presents a fresh ticket on every reconnect', async () => {
    /*
      The clause that makes the fix work or not work at all.

      `verify_ws_ticket` redeems with `redis_manager.getdel` (`core/websocket_auth.py:152`),
      so a ticket is consumed by the first connection that presents it. A reconnect that
      reused the previous attempt's ticket — or that rebuilt its URL from `this.url`, which
      still carries it — would be refused by the server, and the refusal would look exactly
      like a network fault: the socket would drop, back off, present the same spent ticket,
      and never recover. A session would lose live data permanently on its first blip.

      So: two drops, and the assertion is three *distinct* tickets requested and three
      distinct `?ticket=` values on the wire. Two drops rather than one because a single
      reconnect cannot distinguish "mints a new ticket each time" from "mints a second
      ticket once and then caches it".

      The route survives the round trip too. `scheduleReconnect` used to rebuild the path
      with `new URL(this.url).pathname`, which silently dropped the query string; the path
      is now carried in `connectPath`.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await wsClient.connect('/ws/telemetry');
    expect(constructed.length).toBe(1);

    // Two drops, each answered by the shared client's own backoff.
    for (const attempt of [1, 2]) {
      const live = constructed[constructed.length - 1];
      live.open();
      live.close();
      // Base delay 1000 ms x 2^(attempt-1), plus up to 250 ms of jitter.
      await vi.advanceTimersByTimeAsync(1000 * 2 ** (attempt - 1) + 500);
      expect(constructed.length, `reconnect ${attempt} must have opened a socket`)
        .toBe(attempt + 1);
    }

    expect(wsTickets.issued, 'one ticket request per connection attempt').toHaveLength(3);
    expect(new Set(wsTickets.issued).size, 'a single-use ticket is never reused').toBe(3);
    expect(
      wsTickets.requests.every((request) => request.method === 'POST' && request.authorized),
      'the JWT travels in the Authorization header of a POST, where it is not access-logged',
    ).toBe(true);

    const presented = constructed.map((socket) => new URL(String(socket.url)).searchParams.get('ticket'));
    expect(presented).toEqual(wsTickets.issued);
    for (const socket of constructed) {
      expect(String(socket.url)).toContain('/ws/telemetry');
      expectNoCredentialInUrl(socket.url, SYNTHETIC_JWT, 'a reconnected socket');
    }
  });

  it('treats a ticket it cannot obtain as a handled failure, not a retry loop', async () => {
    /*
      `POST /api/auth/ws-ticket` answers 503 `WS_TICKET_STORE_UNAVAILABLE` when the ticket
      store does not acknowledge the write (`routers/auth.py:309-326`), and it is rate
      limited to 30 a minute. Those two facts together are why this is asserted: a client
      that retried the ticket request on its own schedule, or that reset its backoff on each
      attempt, would spend the whole allowance in two seconds and then be rate-limited on
      top of an outage.

      Asserted as *counted requests over advanced time*, not as an absence of a crash. The
      failure also must not surface as an unhandled rejection — `connect()` resolves here
      rather than rejecting, which is what lets `acquire()` and the top bar's retry ignore
      its return value.

      And it recovers: the moment the endpoint answers, the next scheduled attempt connects.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);
    wsTickets.fail(503);

    await wsClient.connect('/ws/telemetry');

    expect(constructed.length, 'no socket is opened without a credential to present').toBe(0);
    expect(wsClient.getStatus()).not.toBe('connected');
    expect(wsTickets.requests, 'one attempt, not a tight loop').toHaveLength(1);

    // The first scheduled retry: one more request, still no socket.
    await vi.advanceTimersByTimeAsync(1500);
    expect(wsTickets.requests).toHaveLength(2);
    expect(constructed.length).toBe(0);

    // The store comes back. The next scheduled attempt — delayed 2000 ms, because the
    // backoff grew rather than resetting — connects.
    wsTickets.serve();
    await vi.advanceTimersByTimeAsync(2500);

    expect(constructed.length).toBe(1);
    expect(queryParams(constructed[0].url)).toEqual(['ticket']);
    expectNoCredentialInUrl(constructed[0].url, SYNTHETIC_JWT, 'the recovered socket');
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 3. TWO TOKEN STORES FOR ONE SESSION
//    (`Billing.jsx:138` vs `websocketClient.js:73`)
// ══════════════════════════════════════════════════════════════════════════

describe('the store the session credential is read from', () => {
  it('is one store, not two', async () => {
    /*
      COUNTEREXAMPLE OBSERVED ON `F`:

          credential in localStorage only   -> Billing builds a socket; wsClient builds a
                                               URL with no credential at all
          credential in sessionStorage only -> wsClient carries it; Billing builds NO socket
                                               (Billing.jsx:139 returns early)

          stores that serve a reader: ['localStorage', 'sessionStorage']  -- two

      Asserted behaviourally rather than by spying on `getItem`, because the claim is about
      which store *serves* a reader, and a spy would also record reads that find nothing.

      Why this is its own defect and not a detail of 1.21. The two stores have different
      lifetimes: `sessionStorage` is per-tab and cleared when the tab closes,
      `localStorage` is not. So the same session has a credential that expires with the tab
      for one socket and persists on disk for the other, and nothing copies between them - a
      token refresh that writes one store leaves the other holding a stale JWT. Wave 3's fix
      consolidates the sockets; if it does not also consolidate the store, the surviving
      socket inherits whichever lifetime its author happened to pick.

      HOW TASK 8.3 CLOSES IT, AND WHY THIS TEST STILL ASKS THE SAME QUESTION
      ---------------------------------------------------------------------
      Billing no longer reads a store. It calls `isAuthenticated()` — `apiClient`'s own
      helper, the same `sessionStorage.getItem("token")` its axios request interceptor makes
      on every HTTP call (`apiClient.js:616-620`, `:645`) — and gets the user id from
      `GET /api/auth/me`. `isAuthenticated` is deliberately **not** stubbed in this file, so
      the probe below still observes the application's answer to "which store serves a
      reader" rather than this file's.

      `getMe` *is* stubbed: it is a network boundary, not a store. It answers the same id in
      both branches, so it cannot be what makes the two branches differ — which is what
      keeps this a test about the store.
    */
    const serving = new Set();

    // -- localStorage only -------------------------------------------------
    // Call history only: the resolved value the suite's `beforeEach` installed stays in
    // place, because the second branch below needs it to answer.
    mockAuth.getMe.mockClear();
    window.localStorage.setItem('token', SYNTHETIC_JWT);
    const billingView = await mountBilling();
    const billingServedByLocal = constructed.length > 0;
    // Evidence that the refusal happened at the session check rather than downstream: a
    // reader that had found a credential would have gone on to ask whose it is.
    const billingAskedWhoseSessionOnLocal = mockAuth.getMe.mock.calls.length > 0;

    constructed = [];
    resetSocketClient();
    await wsClient.connect('/ws/telemetry');
    // `ticket` since task 8.2: the parameter name changed, the question did not. A client
    // that presented a credential is one that found a session token in the store under
    // test; one that presented nothing did not.
    const clientServedByLocal = queryParams(constructed[0].url).includes('ticket');

    await act(async () => {
      billingView.unmount();
    });
    vi.clearAllTimers();

    // -- sessionStorage only ----------------------------------------------
    window.localStorage.clear();
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);
    constructed = [];
    resetSocketClient();
    await mountBilling();
    const billingServedBySession = constructed.length > 0;

    constructed = [];
    resetSocketClient();
    await wsClient.connect('/ws/telemetry');
    const clientServedBySession = queryParams(constructed[0].url).includes('ticket');

    if (billingServedByLocal || clientServedByLocal) serving.add('localStorage');
    if (billingServedBySession || clientServedBySession) serving.add('sessionStorage');

    // The evidence, recorded so the count below is readable rather than bare.
    expect({
      billingServedByLocal,
      clientServedByLocal,
      billingServedBySession,
      clientServedBySession,
    }).toEqual({
      billingServedByLocal,
      clientServedByLocal,
      billingServedBySession,
      clientServedBySession,
    });

    expect(billingAskedWhoseSessionOnLocal, (
      'a credential in localStorage must not look like a session to Billing at all: it did ' +
      'not stop at the session check, it went on to resolve a user id.'
    )).toBe(false);

    expect(Array.from(serving), (
      `the session credential is read from ${serving.size} stores ` +
      `(${Array.from(serving).join(', ')}). On \`F\`, Billing.jsx:138 read localStorage and ` +
      'websocketClient.js:73 read sessionStorage. One session has one credential, so it has ' +
      'one store.'
    )).toHaveLength(1);

    // And it is the store the HTTP client uses, which is what task 8.3 asks for: a session
    // cannot be authenticated for HTTP and anonymous for the socket.
    expect(Array.from(serving), (
      'the one store must be the one `apiClient`\'s request interceptor reads, or the socket ' +
      'and the HTTP client can still disagree about whether a session exists'
    )).toEqual(['sessionStorage']);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 4. THE PROPERTY  (task 2: "no constructed socket URL contains the token in any position")
// ══════════════════════════════════════════════════════════════════════════

/** base64url, the alphabet a JWT segment is drawn from. */
const BASE64URL = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'.split('');

/** One base64url run of realistic length. */
const segment = fc
  .array(fc.constantFrom(...BASE64URL), { minLength: 12, maxLength: 40 })
  .map((chars) => chars.join(''));

/**
 * A JWT-shaped credential: `header.payload.signature`.
 *
 * Constrained to base64url on purpose. It is the alphabet a real JWT uses, so the generated
 * value is percent-encoding-invariant - which means a URL that contains it raw and a URL that
 * contains it encoded are the same string, and the property cannot pass by accident on an
 * escaping difference. The encoded form is asserted anyway, for the day the generator widens.
 */
const jwtArb = fc.tuple(segment, segment, segment).map((parts) => parts.join('.'));

/** A real route this client connects to. See `WS_ROUTES` for where the set comes from. */
const pathArb = fc.constantFrom(...WS_ROUTES);

/** An opaque user id, for Billing's `/ws/user/{id}`. */
const userIdArb = fc
  .array(fc.constantFrom(...'abcdefghijklmnopqrstuvwxyz0123456789_'.split('')), {
    minLength: 4,
    maxLength: 24,
  })
  .map((chars) => `usr_${chars.join('')}`);

describe('Property: no constructed socket URL contains the credential in any position', () => {
  it('holds for websocketClient.connect() over generated tokens and every real route', async () => {
    /**
     * **Validates: Requirements 1.21, 2.21**
     *
     * `numRuns` is 300: `connect()` is a string build with no I/O, so the run is cheap and
     * the generator space (tokens x ten routes, two of which already carry a query string) is
     * worth covering densely.
     *
     * COUNTEREXAMPLE OBSERVED ON `F` - failed after 1 test, shrunk 38 times to the minimal
     * draw, which is every draw:
     *
     *     Counterexample: ["AAAAAAAAAAAA.AAAAAAAAAAAA.AAAAAAAAAAAA", "/ws/telemetry"]
     *     the credential is at index 53 of
     *     wss://d7d88qs4jmch.cloudfront.net/ws/telemetry?token=<JWT>
     *
     * The shrink is informative in itself: fast-check reduced the token to the shortest
     * base64url triple the generator admits and the path to the first route, and the property
     * still failed - so the exposure does not depend on the token's content or on which route
     * is asked for. It is unconditional.
     *
     * The property is unfalsifiable only in the sense that `F` fails it on run 1; that is
     * the point of an exploration test. It is written as a property rather than a case
     * because "in any position" is a claim about the whole string for all inputs, and the
     * fixes that would defeat a `token=`-shaped assertion - renaming the parameter, moving
     * the value into a path segment, into a fragment, into a subprotocol-shaped suffix - are
     * all still failures, and a substring search is what catches every one of them.
     */
    await fc.assert(
      fc.asyncProperty(jwtArb, pathArb, async (token, path) => {
        constructed = [];
        resetSocketClient();
        window.sessionStorage.setItem('token', token);

        // `asyncProperty` since task 8.2: the ticket is fetched before the socket is
        // constructed, so the predicate has to await the attempt. What it then asserts is
        // unchanged — a substring search over the whole URL, in raw and encoded form.
        await wsClient.connect(path);

        expect(constructed.length).toBe(1);
        for (const socket of constructed) {
          const url = String(socket.url);
          expect(
            url.includes(token),
            `the credential is at index ${url.indexOf(token)} of ${redact(url)}`,
          ).toBe(false);
          expect(url.includes(encodeURIComponent(token))).toBe(false);
          expect(queryParams(url)).not.toContain('token');
        }
      }),
      { numRuns: 300 },
    );
  });

  it("holds for Billing's mount over generated tokens and user ids", async () => {
    /**
     * **Validates: Requirements 1.21, 2.21**
     *
     * `numRuns` is 20, not 300, and the reason is stated rather than tuned: each run renders
     * the whole Billing page, which is the only way to reach `:142` without reimplementing
     * it in the test. Twenty draws is enough to establish that the exposure is a property of
     * the builder and not of one fixture value, and the exhaustive half of the search space
     * is covered by the `connect()` property above, which shares the same token generator.
     *
     * COUNTEREXAMPLE OBSERVED ON `F` - failed after 1 test, shrunk 41 times:
     *
     *     Counterexample: ["AAAAAAAAAAAA.AAAAAAAAAAAA.AAAAAAAAAAAA", "usr_aaaa"]
     *     the credential is at index 43 of
     *     ws://localhost:3000/ws/user/usr_aaaa?token=<JWT>
     *
     * `ws://localhost:3000` here rather than the configured `wss://` host, because
     * `Billing.jsx:142` builds its origin from `window.location` and never reads
     * `VITE_WS_URL` - the asymmetry recorded in the module docblock.
     *
     * `asyncProperty` since task 8.3, for the same reason section 1 awaits its mount: the id
     * is a request and the ticket is another, so the socket is constructed after the render
     * rather than during it. The generated `userId` is now what `GET /api/auth/me` answers
     * rather than what a store holds — which is the one place the id can come from — and the
     * property is unchanged: whatever the id, the credential is in no position of the URL.
     */
    await fc.assert(
      fc.asyncProperty(jwtArb, userIdArb, async (token, userId) => {
        constructed = [];
        resetSocketClient();
        window.sessionStorage.setItem('token', token);
        mockAuth.getMe.mockResolvedValue({ id: userId, email: 'billing@example.test', role: 'user' });

        const view = await mountBilling();

        const urls = constructed.map((socket) => String(socket.url));

        await act(async () => {
          view.unmount();
        });
        vi.clearAllTimers();
        window.sessionStorage.clear();

        expect(urls.length).toBe(1);
        expect(urls[0]).toContain(`/ws/user/${userId}`);
        for (const url of urls) {
          expect(
            url.includes(token),
            `the credential is at index ${url.indexOf(token)} of ${redact(url)}`,
          ).toBe(false);
          expect(url.includes(encodeURIComponent(token))).toBe(false);
          expect(queryParams(url)).not.toContain('token');
        }
      }),
      { numRuns: 20 },
    );
  });
});

// The shared client reads `sessionStorage` and so, through `isAuthenticated()`, does Billing.
// Nothing in `src/` reads a session credential or an identity out of `localStorage` any more;
// the remaining `localStorage` users are the workspace layout, the builder autosave draft, the
// first-trade wizard's progress and supabase's own `sb-<ref>-auth-token`, none of which this
// application reads as a session.

// The shared client's `acquire` / `release` refcounting and its per-channel refcounts are the
// machinery wave 3's fix is built on, and they are pinned in
// `tests/unit/lib/singleSocket.test.jsx` beside the single-socket assertion they serve, rather
// than duplicated here.

// ══════════════════════════════════════════════════════════════════════════
// 5. THE DEPLOY-ORDERING COMPATIBILITY SHIM  (task 8.2c)
//    ┏━━━ TEMPORARY. DELETE THIS WHOLE SECTION WITH THE SHIM. ━━━━━━━━━━━━━┓
// ══════════════════════════════════════════════════════════════════════════
//
// WHAT THIS SECTION IS, AND WHY IT IS NOT A WEAKENING OF §1-§4
// -----------------------------------------------------------
// Sections 1-4 assert that no socket URL carries the session JWT. This section asserts one
// URL that *does*, deliberately, under one condition that is named and bounded. It is an
// explicitly asserted exception, not a relaxation: every assertion above still runs, the
// property in §4 still holds over 300 generated tokens on the default path, and nothing in
// this section touches the default path at all.
//
// THE CONDITION, WHICH IS A DEPLOYMENT FACT AND NOT A HYPOTHETICAL
// ---------------------------------------------------------------
// Two workflows ship the two halves of task 8.2 at different speeds.
// `06-frontend-deploy.yml` fires on a push to `main` touching `algo22-terminal/**` and is an
// S3 sync — seconds. `03-deploy.yml` waits for `02 Build`, then rolls ECS — minutes. So on
// merge the new bundle is live against the old task definition.
//
// What breaks in that window is *not* the mint. `issue_ws_ticket` is already on `main`, so
// `POST /api/auth/ws-ticket` answers 200 with a real ticket. `verify_ws_ticket` is not there:
// nothing can redeem it, the routes read only `token`, and the handshake finds no credential
// and closes 4001. Every socket for every user, until the rollout lands. §2's reconnect case
// deliberately has no `?token=` fallback, so without this shim there is nothing to recover
// with.
//
// So the signal has to be read at the SOCKET, which is the only place the mismatch is
// observable, and the fallback mirrors what task 8.2a already did on the backend by keeping
// `token` accepting for one release.
//
// CREDENTIALS HERE
// ----------------
// Same rule as the rest of the file: `SYNTHETIC_JWT` is three base64url segments that decode
// to nothing, and failure messages go through `redact`. The one place a raw credential is
// compared is the assertion that the fallback URL carries it — asserted by `includes`, and
// reported as `<JWT>`.

describe('the deploy-ordering compatibility shim (task 8.2c)', () => {
  /** The socket the client most recently constructed. */
  const live = () => constructed[constructed.length - 1];

  /** Every `console.warn` line naming the shim. It must be logged once, not per attempt. */
  const degradationWarnings = () =>
    console.warn.mock.calls
      .map((call) => call.join(' '))
      .filter((line) => line.includes(WS_TICKET_FALLBACK.marker));

  it('leaves the default path alone: a ticket, and no token parameter', async () => {
    /*
      The guard on everything else in this section. The shim is reactive — it changes nothing
      until a handshake is actually refused — so the first attempt of a session is the fix as
      §2 asserts it, byte for byte.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await wsClient.connect('/ws/telemetry');

    expect(constructed.length).toBe(1);
    expect(queryParams(constructed[0].url)).toEqual(['ticket']);
    expectNoCredentialInUrl(constructed[0].url, SYNTHETIC_JWT, 'the first attempt of a session');
    expect(wsClient.credentialMode).toBe('ticket');
    expect(degradationWarnings()).toHaveLength(0);
  });

  it('falls back to the legacy token credential after a ticket handshake is refused 4001', async () => {
    /*
      THE SHIM'S DOCUMENTED BEHAVIOUR, AND THE ONE ASSERTION IN THIS FILE THAT EXPECTS A JWT
      IN A URL.

      This is asserted rather than tolerated because a fallback nobody asserts is a fallback
      nobody notices, and because the thing it costs — the JWT in the query string — is
      precisely the exposure Requirements 1.21 / 2.21 remove. Writing it down is what makes
      the trade reviewable: one release of the old exposure, against every socket in the
      product being dead for the length of an ECS rollout.

      The refusal is delivered as a close code on a socket that never opened, which is what a
      refused handshake is. `refuse()` rather than `close()`: the bare `close()` the rest of
      this file uses is a local teardown and must not degrade anything.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await wsClient.connect('/ws/telemetry');
    expect(queryParams(constructed[0].url)).toEqual(['ticket']);

    live().refuse(4001, 'Unauthorized: Missing credential');

    // The next *scheduled* attempt carries the fallback — no immediate retry, so the shim
    // cannot turn a refusing backend into a request loop. Base delay 1000 ms + ≤ 250 ms jitter.
    await vi.advanceTimersByTimeAsync(1500);

    expect(constructed.length, 'the backoff must have made a second attempt').toBe(2);
    expect(queryParams(constructed[1].url)).toEqual(['token']);
    expect(
      String(constructed[1].url).includes(SYNTHETIC_JWT),
      `the fallback URL must carry the session JWT — that is the shim: ${redact(constructed[1].url)}`,
    ).toBe(true);
    expect(String(constructed[1].url)).toContain('/ws/telemetry');

    // No second ticket was minted: the mint succeeds against this backend and is useless,
    // so paying for it again would be a round trip per reconnect for the whole window.
    expect(wsTickets.requests, 'the first attempt minted one ticket and nothing minted again')
      .toHaveLength(1);

    expect(wsClient.credentialMode).toBe('token');
  });

  it('uses `&` when the path already carries a query string', async () => {
    /*
      `/ws/dashboard` takes `user_id` as a query parameter, so the fallback's separator has to
      switch exactly as the ticket path's does. Asserted because a `?` here would produce
      `/ws/dashboard?user_id=…?token=…`, where the route's own parameter is swallowed into the
      previous value and the connection fails for a second, unrelated reason.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await wsClient.connect(`/ws/dashboard?user_id=${USER_ID}`);
    live().refuse(4001);
    await vi.advanceTimersByTimeAsync(1500);

    expect(constructed.length).toBe(2);
    expect(queryParams(constructed[1].url)).toEqual(['user_id', 'token']);
    expect(new URL(String(constructed[1].url)).searchParams.get('user_id')).toBe(USER_ID);
  });

  it('is sticky: a later reconnect goes straight to token and mints no further ticket', async () => {
    /*
      The clause that makes the shim cheap instead of merely working. Once a backend has
      refused a ticket, every reconnect for the rest of the session would otherwise pay a
      doomed mint first — a POST against a 30-a-minute rate limit, per reconnect, for the
      length of the rollout — and then present a credential that cannot be redeemed.

      The second drop here is a *normal* one: the fallback socket opens (the old backend
      accepts `token`) and then drops the way a socket drops. That is the realistic shape, and
      it is also the case that proves the stickiness is state and not a one-shot retry: a
      client that remembered nothing would go back to a ticket on this attempt.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await wsClient.connect('/ws/telemetry');
    live().refuse(4001);
    await vi.advanceTimersByTimeAsync(1500);
    expect(queryParams(constructed[1].url)).toEqual(['token']);

    // The legacy credential works against the old backend: this connection opens.
    live().open();
    expect(wsClient.getStatus()).toBe('connected');
    live().close();

    await vi.advanceTimersByTimeAsync(1500);

    expect(constructed.length, 'the drop must have been reconnected').toBe(3);
    expect(queryParams(constructed[2].url), 'no ticket round trip on a degraded session')
      .toEqual(['token']);
    expect(wsTickets.requests, 'exactly one mint in the whole session').toHaveLength(1);
    expect(wsClient.credentialMode).toBe('token');

    // Logged once, not once per attempt. Three attempts have happened.
    expect(degradationWarnings(), 'the degradation is announced once per session')
      .toHaveLength(1);
  });

  it('never switches credentials twice: a refused token attempt is a real auth failure', async () => {
    /*
      `ticket → token → ticket` is the failure mode this asserts against. A client that
      re-derived its credential from each refusal would alternate forever, present a spent or
      unredeemable credential every time, and never surface a failure — and the loop would be
      invisible because each individual attempt looks like a reasonable retry.

      A refused `token` attempt is not a deploy-window symptom. It is what an expired session
      or a genuinely unauthorised user looks like, and the correct response is the reconnect
      policy the client already has: capped, jittered, bounded, and then `failed`.

      Asserted as a terminating sequence rather than as an absence of a switch, because
      "does not loop" is a claim about where the attempts stop. Default policy is five
      attempts, so: one ticket attempt plus five token attempts, then `failed`.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await wsClient.connect('/ws/telemetry');
    live().refuse(4001);
    await vi.advanceTimersByTimeAsync(1500);
    expect(queryParams(constructed[1].url)).toEqual(['token']);

    // Refuse everything, for longer than the capped delay can ever be (30 s), until the
    // client stops of its own accord. The bound on the loop is the loop's own guard.
    for (let i = 0; i < 10; i += 1) {
      const socket = live();
      if (socket.closes > 0) break;
      socket.refuse(4001);
      await vi.advanceTimersByTimeAsync(31000);
    }

    expect(constructed.length, (
      'one ticket attempt, then maxReconnectAttempts token attempts, then it stops'
    )).toBe(1 + wsClient.maxReconnectAttempts);
    expect(wsClient.getStatus()).toBe('failed');

    // Every attempt after the first presented `token`. None went back to a ticket.
    expect(constructed.slice(1).map((socket) => queryParams(socket.url)))
      .toEqual(constructed.slice(1).map(() => ['token']));
    expect(wsTickets.requests, 'the ticket endpoint is asked once and never again')
      .toHaveLength(1);
    expect(degradationWarnings(), 'one degradation, not one per refusal').toHaveLength(1);
  });

  it('does not degrade on a drop that is not a handshake refusal', async () => {
    /*
      The condition that keeps the JWT out of the URL on an ordinary network blip. A
      connection that reached `open` and then dropped says nothing about whether this backend
      redeems tickets, and a shim that treated every drop as a credential problem would put
      the session JWT in a URL on the first flaky minute of any session — permanently, since
      the mode is sticky.

      Two drops, the shape §2 already uses, and the session is still on tickets afterwards.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await wsClient.connect('/ws/telemetry');

    for (const attempt of [1, 2]) {
      const socket = live();
      socket.open();
      socket.close();
      await vi.advanceTimersByTimeAsync(1000 * 2 ** (attempt - 1) + 500);
      expect(constructed.length).toBe(attempt + 1);
    }

    expect(wsClient.credentialMode).toBe('ticket');
    expect(degradationWarnings()).toHaveLength(0);
    for (const socket of constructed) {
      expect(queryParams(socket.url)).toEqual(['ticket']);
      expectNoCredentialInUrl(socket.url, SYNTHETIC_JWT, 'a socket that dropped after opening');
    }
  });

  it('does not degrade on a close that carries no code at all', async () => {
    /*
      `disconnect()`, `release()`, a `handleError` teardown and every test double in this
      repository fire `onclose` with no event. A browser always supplies a code, so "no code"
      is only ever a local teardown — and reading one as a server refusal would degrade a
      session that was simply being torn down, then keep it degraded.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await wsClient.connect('/ws/telemetry');
    live().close();
    await vi.advanceTimersByTimeAsync(1500);

    expect(wsClient.credentialMode).toBe('ticket');
    expect(queryParams(constructed[1].url)).toEqual(['ticket']);
    expect(degradationWarnings()).toHaveLength(0);
  });

  it('logs the degradation without logging the ticket or the JWT', async () => {
    /*
      §2.20 of `bugfix.md`: a secret is referenced by name and by position, never by value.
      The shim's whole justification is written into a console line, and a console line is
      read by support staff and pasted into tickets, so it is asserted to name the close code
      and the path and nothing else.
    */
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);

    await wsClient.connect('/ws/telemetry');
    const issuedTicket = wsTickets.issued[0];
    live().refuse(4001);
    await vi.advanceTimersByTimeAsync(1500);

    const [warning] = degradationWarnings();
    expect(warning, 'the degradation must be announced').toBeTruthy();

    // Names what it is, why, and when it dies.
    expect(warning).toContain('COMPATIBILITY FALLBACK');
    expect(warning).toContain('verify_ws_ticket');
    expect(warning).toContain('4001');
    expect(warning).toContain(WS_TICKET_FALLBACK.removeWhen);

    // And carries neither credential.
    expect(warning.includes(SYNTHETIC_JWT), 'the JWT is in the log line').toBe(false);
    expect(warning.includes(issuedTicket), 'the ticket is in the log line').toBe(false);
    expect(jwtShapedSubstring(warning), 'a JWT-shaped substring is in the log line').toBeNull();
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 5b. THE REMOVAL MARKER
// ══════════════════════════════════════════════════════════════════════════
//
// This suite exists to FAIL when the shim outlives its purpose, in the style
// `tests/unit/guards/no-local-tokens.test.js` uses for its scheduled exceptions: the thing
// that is allowed to be temporary is pinned by value, so widening it or forgetting it both
// show up in a diff.
//
// The pin cuts both ways on purpose. While the shim is here, these assertions hold and the
// marker is greppable. When ECS is confirmed on the task definition that contains
// `verify_ws_ticket`, deleting the shim breaks this suite — which is the point: the shim
// cannot be removed silently, and it cannot be left behind silently either, because the
// marker names the condition that ends its life and nothing else in `src/` looks like it.

describe('the shim is marked for removal', () => {
  /** `<terminal>/src/websocketClient.js`. This file lives at `tests/unit/lib/`. */
  const SHIM_SOURCE = readFileSync(
    nodePath.resolve(__dirname, '..', '..', '..', 'src', 'websocketClient.js'),
    'utf8',
  );

  it('pins the marker, the kill switch and the removal condition by value', () => {
    // Pinned by value rather than by shape. Changing any of it means changing this
    // assertion, which means saying so in the diff.
    expect(WS_TICKET_FALLBACK).toEqual({
      marker: 'production-launch-hardening-8.2c-deploy-ordering-shim',
      enabled: true,
      removeWhen:
        'ECS is confirmed running the task definition that contains verify_ws_ticket',
      refusalCloseCodes: [4001, 1006],
    });
    // Frozen, so nothing can quietly flip `enabled` at runtime and change which credential
    // a session presents.
    expect(Object.isFrozen(WS_TICKET_FALLBACK)).toBe(true);
    expect(Object.isFrozen(WS_TICKET_FALLBACK.refusalCloseCodes)).toBe(true);
  });

  it('delimits every part of the shim, so all of it can be found and deleted together', () => {
    // Three regions: the constant and its rationale, the `_open` branch that presents the
    // legacy credential, and `_maybeFallBackToLegacyToken`. Pinned as a count so a fourth
    // site cannot be added without this failing, and so deleting one of the three cannot
    // leave the other two behind.
    const opened = SHIM_SOURCE.match(/┏━+ WS_TICKET_FALLBACK/g) || [];
    const closed = SHIM_SOURCE.match(/┗━+ end WS_TICKET_FALLBACK/g) || [];

    expect(opened, (
      'the shim must be delimited at exactly three sites: the WS_TICKET_FALLBACK constant, '
      + 'the WS_CREDENTIAL_MODE_TOKEN branch in `_open`, and `_maybeFallBackToLegacyToken`'
    )).toHaveLength(3);
    expect(closed).toHaveLength(3);
  });

  it('states in the source that it is temporary, and what ends it', () => {
    // A marker with no stated expiry is a marker nobody can act on.
    expect(SHIM_SOURCE).toContain('TEMPORARY. DELETE THIS BLOCK.');
    expect(SHIM_SOURCE).toContain('It exists for exactly one release');
    expect(SHIM_SOURCE).toContain('verify_ws_ticket');
    expect(SHIM_SOURCE).toContain(WS_TICKET_FALLBACK.removeWhen);
    expect(SHIM_SOURCE).toContain(WS_TICKET_FALLBACK.marker);
    // And names the deployment asymmetry that caused it, so the next reader does not have to
    // re-derive it from two workflow files.
    expect(SHIM_SOURCE).toContain('06-frontend-deploy.yml');
    expect(SHIM_SOURCE).toContain('03-deploy.yml');
  });

  it('keeps the fallback in one method, so removing it is one deletion', () => {
    // If the fallback decision spreads out of `_maybeFallBackToLegacyToken`, "delete the
    // block" stops being a true instruction.
    expect(typeof wsClient._maybeFallBackToLegacyToken).toBe('function');

    const assignments = SHIM_SOURCE.match(/this\.credentialMode\s*=(?!=)/g) || [];
    expect(assignments, (
      'credentialMode is assigned in exactly two places: its initialisation in the '
      + 'constructor and the single degradation in `_maybeFallBackToLegacyToken`'
    )).toHaveLength(2);
  });
});
// ┗━━━ end of the task 8.2c section ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
