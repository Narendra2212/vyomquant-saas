/**
 * tests/unit/pages/billingSocketLifecycle.test.jsx - production-launch-hardening task 1,
 * CLUSTER C.
 *
 * Requirements 1.22, 1.23 / 2.22. `design.md` §Hypothesized Root Cause (wave 3).
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
 * `Billing.jsx`'s socket effect schedules its own reconnect from inside `onclose`, and
 * `onclose` is exactly what the effect's cleanup triggers - so tearing the page down is the
 * event that arms the next connection.
 *
 * The cycle, by line, in `src/pages/Billing.jsx`:
 *
 * | Line | What it does |
 * |---|---|
 * | `:135` | `useEffect(..., [loadBilling, loadPlans, currency])` - re-runs on a currency change |
 * | `:144` | `ws = new WebSocket(wsUrl)` - the socket, held only in the effect's closure |
 * | `:164-166` | `ws.onclose = () => { setTimeout(connectWebSocket, 5000); }` - **no id kept** |
 * | `:172` | `return () => { if (ws) ws.close(); }` - the cleanup, which *fires* `:165` |
 *
 * There is no `clearTimeout` anywhere in the effect, and no id to clear it with: the return
 * value of `setTimeout` at `:165` is discarded. So the cleanup at `:172` does not cancel the
 * reconnect - it **causes** one. Five seconds after the page is gone a socket opens, and the
 * only reference to it is the dead closure's `ws` binding, which no surviving cleanup will
 * ever read. It cannot be closed by anything short of a page reload.
 *
 * WHY THE CURRENCY CASE IS ASSERTED TOO (section 2)
 * ------------------------------------------------
 * `currency` is in the dependency array at `:173`, so picking a currency runs the whole
 * teardown/setup cycle *without* unmounting. Each pass leaves one orphan timer behind and
 * opens one fresh socket, so the leak is not a one-off at end of life - it compounds for as
 * long as the page is open. A fix that only cancelled the timer on unmount would still leak
 * on every currency change, which is why the two cases are separate tests rather than one.
 *
 * THE DOUBLE, AND WHY IT IS NOT IMPORTED FROM ELSEWHERE
 * ----------------------------------------------------
 * `tests/` already holds this double: `FakeWebSocket` in `tests/unit/builderRealtime.test.jsx`
 * (and the three `signalTrace*` suites), recording every construction and its URL. It is
 * declared **inside those test modules and never exported**, so importing it would execute
 * those suites as a side effect of this one. `RecordingWebSocket` below is that same class,
 * same shape, same recording contract - not a second design. Extracting it into
 * `tests/unit/helpers/` is the right home for it and is left to the wave-3 fix, which is the
 * change that will have several files reading it.
 *
 * One fidelity note. A real browser fires `onclose` asynchronously, after the close
 * handshake; this double fires it synchronously from `close()`. The difference does not touch
 * what is under test: either way `onclose` runs after the component is gone and schedules the
 * reconnect. Firing it synchronously only makes *when* deterministic.
 *
 * HARNESS
 * -------
 * The `src/api` stub is the shape every other page suite uses
 * (`strategies-table.test.jsx`, `backtestSaveFlow.test.jsx`). `vi.useFakeTimers()` owns the
 * 5 s advance. Nothing in `Billing.jsx` is replaced by a test-only version.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';

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
  /**
   * `GET /api/auth/me` (`routers/auth.py:123`) — where the user id for `/ws/user/{user_id}`
   * comes from since task 8.3. Doubled because it is a network boundary.
   */
  mockAuth: { getMe: vi.fn() },
}));

/**
 * `isAuthenticated` is not doubled: it is `apiClient`'s shipped helper, and it is the whole
 * of task 8.3's point that Billing asks the HTTP client whether a session exists rather than
 * reading a store of its own. Section 3's "no socket without a credential" case is only
 * meaningful against the real one.
 */
vi.mock('../../../src/api', async () => {
  const { isAuthenticated, getToken } = await import('../../../src/apiClient');
  const api = { billing: mockBilling, auth: mockAuth };
  return { api, endpoints: api, default: api, isAuthenticated, getToken };
});

import Billing from '../../../src/pages/Billing';
import wsClient from '../../../src/websocketClient';
import { useWsTicketStub } from '../helpers/wsTicketStub';

/**
 * production-launch-hardening task 8.2: the shared client exchanges the session JWT for a
 * single-use ticket over HTTPS before it opens anything, so every suite whose session has a
 * JWT needs that endpoint doubled. This file's session does, since task 8.3 — the credential
 * lives in `sessionStorage`, which is the store the client reads.
 */
const wsTickets = useWsTicketStub();

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/**
 * A structurally-shaped JWT with no real credential in it.
 *
 * Three base64url segments, so every `token=`-substring assertion below is testing against
 * the shape a real credential has. The payload decodes to
 * `{"sub":"synthetic-not-a-real-user"}` and the signature segment is the literal word
 * `synthetic`. `bugfix.md` §2.20: a secret is referenced by position, never by value, in any
 * artifact this pass produces - so no fixture here carries one.
 */
const SYNTHETIC_JWT =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' +
  '.eyJzdWIiOiJzeW50aGV0aWMtbm90LWEtcmVhbC11c2VyIn0' +
  '.c3ludGhldGlj';

const USER_ID = 'usr_billing_socket_1';

/** `Billing.jsx:165`'s delay. Named so the advance below is visibly that number. */
const RECONNECT_DELAY_MS = 5000;

/** As `supportedCurrencies` (`Billing.jsx:27-45`) spells them, for the two this file uses. */
const CURRENCY_SYMBOLS = Object.freeze({ USD: '$', INR: '\u20b9' });

// ---------------------------------------------------------------------------
// The one double: a WebSocket that goes nowhere and records every construction
// ---------------------------------------------------------------------------

/** Every socket ever constructed in the current test, in construction order. */
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

  // -- driving it from a test -------------------------------------------
  open() {
    this.readyState = RecordingWebSocket.OPEN;
    if (this.onopen) this.onopen();
  }

  deliver(frame) {
    if (this.onmessage) this.onmessage({ data: JSON.stringify(frame) });
  }
}

/** How many sockets have been constructed so far. The whole subject of this file. */
const constructionCount = () => constructed.length;

/** Sockets nobody ever closed. On `F` the orphans of the reconnect timer land here. */
const neverClosed = () => constructed.filter((socket) => socket.closes === 0);

/** URLs with the credential blanked, so a failure message never prints one. */
const redactedUrls = () =>
  constructed.map((socket) => String(socket.url).replace(/token=[^&]*/, 'token=<JWT>'));

// ---------------------------------------------------------------------------
// Mount / interaction helpers
// ---------------------------------------------------------------------------

/**
 * Mount Billing and let its three mount effects settle.
 *
 * The api stub resolves immediately, so one flushed microtask turn is enough to take
 * `isLoadingBilling` false and render the real body (the loading branch at `:335` has no
 * currency control). Vitest's fake timers do not fake `queueMicrotask`, so awaiting inside
 * `act` still drains the promise chain.
 */
const mountBilling = async () => {
  let view;
  await act(async () => {
    view = render(<Billing />);
  });
  /*
    Since task 8.3 the socket is not constructed during the render. The subscription effect
    resolves the user id over HTTP first, and the shared client then mints a ticket, so the
    construction is several microtask turns downstream. `advanceTimersByTimeAsync(0)` crosses
    a real macrotask boundary, which drains that whole chain while the fake clock stands
    still - so nothing here advances toward a reconnect delay that a test has not asked for.
  */
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);
  });
  return view;
};

/** Let an already-mounted page's in-flight subscription requests settle. */
const settle = async () => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);
  });
};

/** Reset the shared singleton between tests - it is a module-level object. */
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
};

/** The currency trigger at `:407`, found by its exact rendered text (`{symbol} {code}`). */
const currencyTrigger = (symbol, code) =>
  screen
    .getAllByRole('button')
    .find((button) => button.textContent.trim() === `${symbol} ${code}`);

/** The dropdown row for `code` at `:452`, whose first span renders `CODE (symbol)`. */
const currencyOption = (code) =>
  screen
    .getAllByRole('button')
    .find((button) => button.textContent.startsWith(`${code} (`));

/** Pick a currency through the real control, which runs `handleCurrencyChange` (`:196`). */
const chooseCurrency = async (fromSymbol, fromCode, toCode) => {
  const trigger = currencyTrigger(fromSymbol, fromCode);
  expect(trigger, `the ${fromCode} currency trigger must be on screen`).toBeTruthy();
  await act(async () => {
    trigger.click();
  });

  const option = currencyOption(toCode);
  expect(option, `the ${toCode} row must be in the open dropdown`).toBeTruthy();
  await act(async () => {
    option.click();
  });
  await act(async () => {});
};

beforeEach(() => {
  constructed = [];
  global.WebSocket = RecordingWebSocket;
  window.WebSocket = RecordingWebSocket;

  /*
    An authenticated session, as this application actually stores one.

    Both of these used to be `localStorage` keys, and both guards they fed were therefore
    unsatisfiable outside a test: every writer in the app writes `sessionStorage` (defect 67,
    P1 - `apiClient.js:595` on `TOKEN_REFRESHED`/`SIGNED_IN`, `:580` and `:606` on sign-out,
    `AuthPage`), and **nothing anywhere in `src/` ever wrote `userId` at all**. So this
    effect had never run once in a real session and the Stripe-webhook-driven updates the
    backend publishes on `/ws/user/{id}` had never reached the page. Section 0 below asserts
    the positive case for exactly that reason: an unmount-safety test alone passes on an
    effect that never runs.

    The credential is `sessionStorage.token`, which is what `isAuthenticated()` reads. The
    identity is the answer to `GET /api/auth/me`, which is a request, not a key.
  */
  window.sessionStorage.setItem('token', SYNTHETIC_JWT);
  // Reset, not just re-stub: `vi.restoreAllMocks()` restores spies, so a `vi.fn()`'s call
  // history would otherwise accumulate across this file and the call-count assertions in
  // section 0 would be reading earlier tests' mounts.
  mockAuth.getMe.mockReset();
  mockAuth.getMe.mockResolvedValue({ id: USER_ID, email: 'billing@example.test', role: 'user' });

  // `GET /api/billing/plans?currency=X` echoes the currency it was asked for
  // (`src/api/modules/billing.js:61` -> `routers/billing.py`), and `loadPlans` feeds that
  // answer straight back into `setCurrency` (`Billing.jsx:68`). The stub echoes too: a stub
  // that answered `USD` to every request would silently revert the currency under test and
  // make section 2 pass without the effect ever having re-run.
  mockBilling.getPlans.mockImplementation((requested) =>
    Promise.resolve({
      plans: [],
      currency: requested || 'USD',
      currency_symbol: CURRENCY_SYMBOLS[requested] || '$',
    }),
  );
  mockBilling.getEntitlements.mockResolvedValue({ plan: 'pro', features: [] });
  mockBilling.getInvoices.mockResolvedValue([]);
  mockBilling.getPaymentMethods.mockResolvedValue([]);
  mockBilling.setCurrency.mockResolvedValue({ ok: true });

  resetSocketClient();
  vi.useFakeTimers();
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
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
// 0. The premise: the effect really did open a socket
// ══════════════════════════════════════════════════════════════════════════

describe('the premise of every assertion below', () => {
  it('opens exactly one socket on mount, at the route it names', async () => {
    await mountBilling();

    expect(constructionCount()).toBe(1);
    // `/ws/user/{user_id}` is a real route - `backend_app/api_ws/ws_routes.py:641`. The route
    // is not the defect here; the lifecycle is.
    expect(constructed[0].url).toContain(`/ws/user/${USER_ID}`);
  });

  it('subscribes for real: an authenticated session with a resolvable id takes a hold', async () => {
    /*
      DEFECT 67, P1 - production-launch-hardening task 8.3.

      This is the assertion the rest of this file cannot make. Every other test here is
      satisfied by an effect that opens *no* socket: "unmounting opens nothing more",
      "nothing is left unclosed", "a currency change leaves one" all pass trivially on a
      subscription that never happens. And on `F` it never happened. Both guards were
      unsatisfiable in a real session:

          localStorage.getItem('token')   always null - every writer writes sessionStorage
          localStorage.getItem('userId')  always null - `setItem('userId'` appears NOWHERE
                                          in src/, so nothing has ever written the key

      So the page has never once subscribed to `/ws/user/{id}`, and the billing updates the
      backend publishes there when a Stripe webhook lands - the entire reason this socket
      exists - have never reached the UI without a manual refresh. Unifying the store alone
      would not have fixed it: the second guard still returned early.

      Asserted as four separate facts, because each fails on its own:
        - the subscription reached the shared client at all (a hold exists);
        - it named the id `GET /api/auth/me` answered, not one from any store;
        - it is a hold on the ONE session socket, not a socket of its own;
        - the page is subscribed to the frames it exists to receive.
    */
    await mountBilling();

    expect(mockAuth.getMe, 'the id must be resolved from the API, not from a store')
      .toHaveBeenCalledTimes(1);
    expect(wsClient.holdCount(), 'the page must hold the session connection').toBe(1);
    expect(wsClient.acquiredPath).toBe(`/ws/user/${USER_ID}`);
    expect(constructionCount()).toBe(1);
    expect(constructed[0], 'the hold must be on the shared client\'s socket, not a new one')
      .toBe(wsClient.ws);
    expect(wsClient.subscribedChannels()).toContain('billing');

    // And a ticket was minted for it over HTTPS, so the subscription is authenticated by the
    // same session the HTTP client is: one store, one session.
    expect(wsTickets.issued).toHaveLength(1);
    expect(wsTickets.requests.every((request) => request.method === 'POST' && request.authorized))
      .toBe(true);
  });

  it('refuses explicitly, with a reason, when the id cannot be resolved', async () => {
    /*
      The other half of defect 67: a session that is authenticated but whose id cannot be
      resolved is a *reported* no-subscription, not a silent one and not a guessed path.
      `/ws/user/{user_id}` authorises the path segment against the token's subject
      (`ws_routes.py:743`), so there is no placeholder that could be correct - a fabricated id
      would be refused by the server and retried by the backoff for the life of the session.
    */
    mockAuth.getMe.mockResolvedValue({ email: 'billing@example.test', role: 'user' });

    await mountBilling();

    expect(constructionCount(), 'no socket without an id the route can authorise').toBe(0);
    expect(wsClient.holdCount()).toBe(0);
    expect(console.error, 'the refusal must say why').toHaveBeenCalledWith(
      expect.stringContaining('no real-time subscription'),
    );
  });

  it('opens nothing at all for a signed-out session', async () => {
    /*
      No credential, no ticket request, no socket, and no `/api/auth/me` call either - the
      page does not ask a signed-out user's question. `isAuthenticated()` here is the real
      helper, so this is the application's own answer about its own store.
    */
    window.sessionStorage.removeItem('token');

    await mountBilling();

    expect(constructionCount()).toBe(0);
    expect(mockAuth.getMe).not.toHaveBeenCalled();
    expect(wsTickets.requests).toHaveLength(0);
    expect(wsClient.holdCount()).toBe(0);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 1. UNMOUNT  (`Billing.jsx:164-172`, Requirements 1.22 / 2.22)
// ══════════════════════════════════════════════════════════════════════════

describe('a socket opened by a page that no longer exists', () => {
  it('is not opened: unmounting cancels the pending reconnect', async () => {
    /*
      COUNTEREXAMPLE OBSERVED ON `F` (src/pages/Billing.jsx:164-172):

          construction count at unmount ......... 1
          advance 5001 ms ....................... constructor called again
          construction count after the advance .. 2
          the second socket's url ............... ws://localhost:3000/ws/user/
                                                  usr_billing_socket_1?token=<JWT>

      The chain is four lines long and entirely within one effect. `:172` calls
      `ws.close()`; `close()` fires `:164`'s `onclose`; `onclose` calls
      `setTimeout(connectWebSocket, 5000)` and keeps no id; five seconds later
      `connectWebSocket` runs `:144` again. The component is gone, so nothing will run a
      cleanup for the new socket - see the orphan assertion in the next test.

      Requirement 2.22's wording is exactly this assertion: "unmounts, advances fake timers
      past the reconnect delay, and asserts the socket constructor was not called again".
    */
    const view = await mountBilling();
    const beforeUnmount = constructionCount();
    expect(beforeUnmount).toBe(1);

    await act(async () => {
      view.unmount();
    });

    await act(async () => {
      vi.advanceTimersByTime(RECONNECT_DELAY_MS + 1);
    });

    expect(constructionCount(), (
      'the page is unmounted, so no socket may be opened for it. `F` constructs ' +
      `${constructionCount() - beforeUnmount} more at ${redactedUrls().slice(beforeUnmount)} - ` +
      'Billing.jsx:172 calls ws.close(), which fires :164, which schedules ' +
      ':165 setTimeout(connectWebSocket, 5000) with no id kept and no clearTimeout anywhere ' +
      'in the effect.'
    )).toBe(beforeUnmount);
  });

  it('leaves nothing open that no cleanup can reach', async () => {
    /*
      COUNTEREXAMPLE OBSERVED ON `F`: one socket with `closes === 0`, constructed 5 s after
      the unmount. This is the half of 1.22 that the construction count alone does not say
      out loud - "nothing will ever close it".

      Why nothing can: `connectWebSocket` assigns to the `ws` binding of the effect run that
      scheduled the timer (`:136`, `let ws = null`). That run's cleanup has already executed.
      React will not call it twice, and no later run shares the binding. The socket is
      reachable from the browser's own connection table and from nowhere in the application.
    */
    const view = await mountBilling();
    await act(async () => {
      view.unmount();
    });
    await act(async () => {
      vi.advanceTimersByTime(RECONNECT_DELAY_MS + 1);
    });

    const orphans = neverClosed();
    expect(orphans.length, (
      `${orphans.length} socket(s) are open with no reference any cleanup can reach. ` +
      'Billing.jsx:136 scopes `ws` to one effect run; the run that scheduled the reconnect ' +
      'has already been cleaned up, so the socket it opens can never be closed.'
    )).toBe(0);
  });

  it('stays quiet however long the page has been gone', async () => {
    /*
      The reconnect is unbounded, so the leak is not capped at one socket: each orphan's own
      `onclose` would schedule the next if it ever closed. Advancing a minute is the cheapest
      way to show the schedule is never cancelled rather than merely late.

      COUNTEREXAMPLE OBSERVED ON `F`: construction count 2 after 60 s - one orphan, and it
      stays at one only because the double never closes it on its own. In a browser, a server
      restart or a network drop closes it and `:164` fires again, from a dead closure.
    */
    const view = await mountBilling();
    const beforeUnmount = constructionCount();

    await act(async () => {
      view.unmount();
    });
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });

    expect(constructionCount(), (
      'a minute after the page was destroyed the application is still opening sockets for it: ' +
      `${redactedUrls().slice(beforeUnmount).join(', ')}`
    )).toBe(beforeUnmount);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 2. A CURRENCY CHANGE  (`Billing.jsx:173` dependency array, Requirement 1.23)
// ══════════════════════════════════════════════════════════════════════════

describe('a currency change, which re-runs the same effect without unmounting', () => {
  it('opens one socket for the new currency and no more', async () => {
    /*
      COUNTEREXAMPLE OBSERVED ON `F`:

          after mount ................................... 1 socket
          after picking INR ............................. 2 sockets   (expected: 2 - the
                                                          effect legitimately reconnects)
          after advancing 5001 ms ....................... 3 sockets   (expected: 2)

      The third is the orphan. `handleCurrencyChange` (`:196`) sets `currency`, `currency` is
      in the dependency array at `:173`, so React tears the effect down and sets it up again:
      the teardown fires `:164` and arms a timer, the setup opens the socket the page will
      actually use, and five seconds later the timer opens a third one that nothing is
      holding.

      This is why the unmount case is not the whole defect. A page left open while a trader
      tries three currencies is a page with three orphan sockets and three orphan timers, all
      of them authenticated, all of them still receiving `subscription_update` frames whose
      handlers call `loadBilling()` and `loadPlans()` from dead closures.
    */
    await mountBilling();
    expect(constructionCount()).toBe(1);

    await chooseCurrency('$', 'USD', 'INR');
    const afterChange = constructionCount();

    await act(async () => {
      vi.advanceTimersByTime(RECONNECT_DELAY_MS + 1);
    });

    expect(constructionCount(), (
      `a currency change left a reconnect armed: ${constructionCount() - afterChange} extra ` +
      'socket(s) opened 5 s later. The teardown at Billing.jsx:172 fires :164, which schedules ' +
      ':165 - and the fresh effect run has its own socket already, so the timer\'s socket is ' +
      'an orphan. The leak compounds once per currency change.'
    )).toBe(afterChange);
  });

  it('leaves exactly one socket open, the one the page is using', async () => {
    /*
      COUNTEREXAMPLE OBSERVED ON `F`: two sockets with `closes === 0` after one currency
      change and the 5 s advance - the live one for INR and the orphan. Asserted separately
      from the count because a count can be satisfied by a fix that closes the wrong socket.

      Requirement 2.23 - "exactly one WebSocket SHALL be open" - is the standing contract; it
      has to hold across a re-render, not only at first mount.
    */
    await mountBilling();
    await chooseCurrency('$', 'USD', 'INR');
    await act(async () => {
      vi.advanceTimersByTime(RECONNECT_DELAY_MS + 1);
    });

    const open = neverClosed();
    expect(open.length, (
      `${open.length} sockets are open after one currency change: ` +
      `${open.map((socket) => String(socket.url).replace(/token=[^&]*/, 'token=<JWT>')).join(', ')}`
    )).toBe(1);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 3. PRESERVATION - what the wave-3 fix must not break
// ══════════════════════════════════════════════════════════════════════════

describe('preservation', () => {
  it('test_preserved_no_socket_without_a_credential', async () => {
    /*
      Passes on `F` and must keep passing. `:138-141` returned before constructing anything
      when either `token` or `userId` was missing, which is the correct refusal: an
      unauthenticated socket to `/ws/user/{id}` would be closed by `ws_routes.py:641`'s
      fail-closed auth anyway, and retrying it every 5 s is a loop against the ALB.

      It is pinned here because the reconnect fix touches exactly these lines - a rewrite
      that moved the credential read out of `connectWebSocket` could easily start
      constructing first and checking after.

      Same two absences since task 8.3, at the sources that actually hold them: no session
      credential in `sessionStorage`, and an `/api/auth/me` that cannot be reached. Neither
      may produce a socket.
    */
    window.sessionStorage.removeItem('token');
    await mountBilling();
    expect(constructionCount()).toBe(0);

    cleanup();
    resetSocketClient();
    constructed = [];
    window.sessionStorage.setItem('token', SYNTHETIC_JWT);
    mockAuth.getMe.mockRejectedValue(new Error('/api/auth/me unreachable'));
    await mountBilling();
    expect(constructionCount()).toBe(0);
  });

  it('test_preserved_subscription_frames_still_refresh_the_page', async () => {
    /*
      Passes on `F` and must keep passing. The five frame kinds at `:150-154` are the whole
      reason the socket exists: a Stripe webhook lands, the backend publishes, and the page
      re-reads its entitlements without the trader refreshing.

      Asserted by call count on the api stub rather than by anything rendered, because the
      contract is "the page re-reads", and `loadPlans` is called with the current currency
      (`:157`) which is the detail a reconnect rewrite is most likely to drop.
    */
    await mountBilling();
    const socket = constructed[0];

    const plansBefore = mockBilling.getPlans.mock.calls.length;
    const entitlementsBefore = mockBilling.getEntitlements.mock.calls.length;

    await act(async () => {
      socket.open();
      socket.deliver({ type: 'subscription_update' });
    });
    await act(async () => {});

    expect(mockBilling.getEntitlements.mock.calls.length).toBeGreaterThan(entitlementsBefore);
    expect(mockBilling.getPlans.mock.calls.length).toBeGreaterThan(plansBefore);
    expect(mockBilling.getPlans).toHaveBeenLastCalledWith('USD');
  });

  it('test_preserved_a_malformed_frame_is_survived', async () => {
    /*
      Passes on `F` and must keep passing. `:147-161` wraps the parse, so a frame that is not
      JSON is logged and dropped rather than throwing inside a socket callback - where it
      would escape React's error boundary entirely and be unhandled.
    */
    await mountBilling();
    const socket = constructed[0];

    await act(async () => {
      socket.open();
      socket.onmessage({ data: 'not json at all' });
    });

    expect(screen.getByText(/Subscription & Billing/i)).toBeTruthy();
  });

  it('test_preserved_the_effect_closes_the_socket_it_owns', async () => {
    /*
      Passes on `F` and must keep passing - it is the half of `:172` that is correct. The
      cleanup does close the socket that effect run opened. The defect is what `close()`
      sets in motion, not the close itself, and a fix that stopped closing on unmount would
      trade one leak for another.
    */
    const view = await mountBilling();
    const socket = constructed[0];
    expect(socket.closes).toBe(0);

    await act(async () => {
      view.unmount();
    });

    expect(socket.closes).toBe(1);
  });
});
