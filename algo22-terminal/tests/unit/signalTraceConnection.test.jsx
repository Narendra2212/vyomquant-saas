/**
 * tests/unit/signalTraceConnection.test.jsx
 *
 * The Signal_Trace_Page's connection-status indicator.
 *
 * Spec: trading-lifecycle-integration task 19.3. Requirement 18.9.
 *
 * WHAT THIS FILE HOLDS IN PLACE
 * -----------------------------
 * 1. **The three states Requirement 18.9 names are visibly different** — "connected",
 *    "reconnecting" and "disconnected" each get their own word and their own glyph, not
 *    only their own colour, so the distinction survives a user who cannot tell green from
 *    red (the same rule `DeployPreflightPanel` follows, task 18.1).
 * 2. **`UNAVAILABLE` is not drawn as either of them.** The hook reports four states; the
 *    fourth means this page has no connection to lose (no session, or no active deployment
 *    to subscribe to). Rendering it as `DISCONNECTED` would claim a drop that never
 *    happened, and rendering it as `RECONNECTING` would promise a retry that cannot
 *    succeed.
 * 3. **The page's indicator tracks the real socket** — connected on open, disconnected the
 *    moment it drops, reconnecting once the jittered backoff fires the next attempt. Driven
 *    through the real `websocketClient`, not by poking the component's props.
 * 4. **A refusal and a dropped connection stay separate reports** (Requirements 18.3 and
 *    18.9 are different facts: one channel of a live connection, versus no connection).
 *
 * WHAT IS REAL HERE AND WHAT IS A DOUBLE
 * --------------------------------------
 * Real: `websocketClient` (including its backoff), `useSignalTraceRealtime`,
 * `lib/signalTraceRealtime`, `SignalTrace` and its indicator.
 *
 * Doubles: the `WebSocket` class (jsdom's would open a real connection) and the api client.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const { mockClient, mockStrategies } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn(), patch: vi.fn() },
  mockStrategies: { listDeployments: vi.fn() },
}));

vi.mock('../../src/apiClient', () => ({
  default: mockClient,
  get: (...args) => mockClient.get(...args),
  post: (...args) => mockClient.post(...args),
  put: (...args) => mockClient.put(...args),
  del: (...args) => mockClient.del(...args),
  patch: (...args) => mockClient.patch(...args),
}));

vi.mock('../../src/api', () => ({
  endpoints: { strategies: mockStrategies },
  api: { strategies: mockStrategies },
}));

import wsClient from '../../src/websocketClient';
import { useWsTicketStub } from './helpers/wsTicketStub';

/*
  production-launch-hardening task 8.2. The shared client exchanges this session's JWT for a
  single-use socket ticket over HTTPS before it constructs anything; `useWsTicketStub` is the
  double for that one request. Nothing this file asserts changes — the socket arrives a
  microtask after the `acquire` that asked for it.
*/
useWsTicketStub();

import SignalTrace, {
  SIGNAL_CONNECTION_PRESENTATION,
  SignalConnectionIndicator,
  UNAVAILABLE_REASONS,
  UNREADABLE_CONNECTION_PRESENTATION,
  signalConnectionPresentation,
} from '../../src/pages/SignalTrace';
import { SIGNAL_REALTIME_STATES } from '../../src/lib/signalTraceRealtime';
import { OWNED_CHANNEL_EVENTS } from '../../src/constants/wsChannels';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const STRATEGY_ID = 'stg_sig_1';
const DEP_A = 'dep_sig_a';
const channelA = `signal.${DEP_A}`;

const signalRow = (overrides = {}) => ({
  id: 'sig_1',
  deployment_id: DEP_A,
  strategy_id: STRATEGY_ID,
  strategy_version: '1.2',
  symbol: 'BTC/USDT',
  decision: 'BUY',
  status: 'pending',
  order_lifecycle_state: 'PENDING',
  quantity: 1,
  generated_at: '2024-05-01T10:00:00Z',
  ...overrides,
});

const listing = (rows) => ({ strategy_id: STRATEGY_ID, deployments: rows, total: rows.length });

const refusalFrame = (channel) => ({
  type: 'subscription_refused',
  channel,
  code: 'CHANNEL_FORBIDDEN',
  reason: 'That deployment is not available to this user, so the subscription was refused.',
});

// ---------------------------------------------------------------------------
// The one double: a WebSocket that goes nowhere
// ---------------------------------------------------------------------------

let sockets = [];

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    this.sent = [];
    this.closes = 0;
    sockets.push(this);
  }

  send(text) {
    this.sent.push(JSON.parse(text));
  }

  close() {
    this.closes += 1;
    this.readyState = FakeWebSocket.CLOSED;
    if (this.onclose) this.onclose();
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    if (this.onopen) this.onopen();
  }

  deliver(frame) {
    if (this.onmessage) this.onmessage({ data: JSON.stringify(frame) });
  }

  drop() {
    this.readyState = FakeWebSocket.CLOSED;
    if (this.onclose) this.onclose();
  }
}

const lastSocket = () => sockets[sockets.length - 1];

const openSocket = async () => {
  await act(async () => {
    lastSocket().open();
  });
};

/** Reset the shared singleton between tests — it is a module-level object. */
const resetSocketClient = () => {
  wsClient.reconnectEnabled = false;
  if (wsClient.reconnectTimeoutId) {
    clearTimeout(wsClient.reconnectTimeoutId);
    wsClient.reconnectTimeoutId = null;
  }
  wsClient.stopHeartbeat();
  wsClient.ws = null;
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
  wsClient.messageBuffer.clear();
  wsClient.setReconnectPolicy({ maxAttempts: 5, baseDelayMs: 1000, maxDelayMs: 30000, jitterMs: 250 });
};

beforeEach(() => {
  sockets = [];
  global.WebSocket = FakeWebSocket;
  window.WebSocket = FakeWebSocket;
  window.sessionStorage.setItem('token', 'test-token');
  vi.clearAllMocks();
  resetSocketClient();
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  resetSocketClient();
  window.sessionStorage.clear();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// ═══════════════════════════════════════════════════════════════════════════
// 1. The presentation vocabulary — never colour alone
// ═══════════════════════════════════════════════════════════════════════════

describe('the connection-state presentation vocabulary', () => {
  const states = Object.values(SIGNAL_REALTIME_STATES);

  it('covers every state the hook can report', () => {
    for (const state of states) {
      expect(SIGNAL_CONNECTION_PRESENTATION[state]).toBeTruthy();
    }
  });

  it('pairs a word and a glyph with every colour', () => {
    // Requirement 18.9 asks for a *distinguishable* indicator; a colour swap alone is not
    // distinguishable to a user who cannot see the difference.
    for (const state of states) {
      const presentation = SIGNAL_CONNECTION_PRESENTATION[state];
      expect(presentation.word.trim().length).toBeGreaterThan(0);
      expect(presentation.glyph.trim().length).toBeGreaterThan(0);
      expect(presentation.color).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it('gives each state its own word and its own glyph', () => {
    const words = states.map((s) => SIGNAL_CONNECTION_PRESENTATION[s].word);
    const glyphs = states.map((s) => SIGNAL_CONNECTION_PRESENTATION[s].glyph);
    expect(new Set(words).size).toBe(states.length);
    expect(new Set(glyphs).size).toBe(states.length);
  });

  it('names the three states the requirement names', () => {
    expect(SIGNAL_CONNECTION_PRESENTATION[SIGNAL_REALTIME_STATES.CONNECTED].word).toBe('CONNECTED');
    expect(SIGNAL_CONNECTION_PRESENTATION[SIGNAL_REALTIME_STATES.CONNECTING].word).toBe('RECONNECTING');
    expect(SIGNAL_CONNECTION_PRESENTATION[SIGNAL_REALTIME_STATES.DISCONNECTED].word).toBe('DISCONNECTED');
  });

  it('does not dress "no connection to lose" up as a drop or as a retry', () => {
    const unavailable = SIGNAL_CONNECTION_PRESENTATION[SIGNAL_REALTIME_STATES.UNAVAILABLE];
    expect(unavailable.word).not.toBe(
      SIGNAL_CONNECTION_PRESENTATION[SIGNAL_REALTIME_STATES.DISCONNECTED].word,
    );
    expect(unavailable.word).not.toBe(
      SIGNAL_CONNECTION_PRESENTATION[SIGNAL_REALTIME_STATES.CONNECTING].word,
    );
    // And it does not claim frames are arriving either.
    expect(unavailable.word).not.toBe(
      SIGNAL_CONNECTION_PRESENTATION[SIGNAL_REALTIME_STATES.CONNECTED].word,
    );
  });

  it('reports a state it cannot read as unreadable rather than as connected', () => {
    expect(signalConnectionPresentation('who_knows')).toBe(UNREADABLE_CONNECTION_PRESENTATION);
    expect(signalConnectionPresentation(undefined)).toBe(UNREADABLE_CONNECTION_PRESENTATION);
    expect(UNREADABLE_CONNECTION_PRESENTATION.word).not.toBe('CONNECTED');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. The indicator itself
// ═══════════════════════════════════════════════════════════════════════════

describe('SignalConnectionIndicator', () => {
  const indicator = () => screen.getByTestId('signal-trace-connection');

  it('announces itself to assistive technology, with the glyph hidden from it', () => {
    render(<SignalConnectionIndicator status={SIGNAL_REALTIME_STATES.DISCONNECTED} watching={1} />);

    // The state changes with no action from the user, so it has to be announced.
    expect(indicator().getAttribute('role')).toBe('status');
    expect(indicator().getAttribute('aria-live')).toBe('polite');
    // The word carries the meaning; the glyph would only be read out as noise.
    expect(indicator().querySelector('[aria-hidden="true"]').textContent).toBe('✕');
    expect(indicator().textContent).toContain('DISCONNECTED');
  });

  it.each([
    [SIGNAL_REALTIME_STATES.CONNECTED, 'CONNECTED'],
    [SIGNAL_REALTIME_STATES.CONNECTING, 'RECONNECTING'],
    [SIGNAL_REALTIME_STATES.DISCONNECTED, 'DISCONNECTED'],
  ])('states %s in words and exposes it as data-status', (status, word) => {
    render(<SignalConnectionIndicator status={status} watching={1} />);
    expect(indicator().getAttribute('data-status')).toBe(status);
    expect(indicator().textContent).toContain(word);
  });

  it('says why there is no live connection, rather than implying one was lost', () => {
    const { unmount } = render(
      <SignalConnectionIndicator status={SIGNAL_REALTIME_STATES.UNAVAILABLE} watching={0} />,
    );
    expect(indicator().textContent).toContain(UNAVAILABLE_REASONS.NOTHING_TO_WATCH);
    expect(indicator().textContent).not.toContain('DISCONNECTED');
    unmount();

    // The hook connects only with a token AND a channel, so `UNAVAILABLE` while holding a
    // channel is a missing session — a different thing to tell the user.
    render(<SignalConnectionIndicator status={SIGNAL_REALTIME_STATES.UNAVAILABLE} watching={2} />);
    expect(indicator().textContent).toContain(UNAVAILABLE_REASONS.NOT_AUTHENTICATED);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. The page — the indicator follows the real socket
// ═══════════════════════════════════════════════════════════════════════════

const renderPage = async (url = `/app/signal-trace?strategy_id=${STRATEGY_ID}`) => {
  const view = render(
    <MemoryRouter initialEntries={[url]}>
      <SignalTrace />
    </MemoryRouter>,
  );
  await screen.findByText('BTC/USDT');
  return view;
};

const status = () => screen.getByTestId('signal-trace-connection').getAttribute('data-status');

describe('SignalTrace connection indicator', () => {
  beforeEach(() => {
    mockClient.get.mockImplementation(async (url) => {
      if (typeof url === 'string' && url.startsWith('/api/signal-trace/signals?')) {
        return { signals: [signalRow()], total: 1, limit: 50, offset: 0 };
      }
      return {};
    });
    mockStrategies.listDeployments.mockResolvedValue(listing([{ deployment_id: DEP_A, status: 'running' }]));
  });

  it('reads CONNECTED once the socket is open (Requirement 18.9)', async () => {
    await renderPage();
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();

    await waitFor(() => expect(status()).toBe(SIGNAL_REALTIME_STATES.CONNECTED));
    expect(screen.getByTestId('signal-trace-connection').textContent).toContain('CONNECTED');
  });

  it('distinguishes a dropped connection from the retry that follows it', async () => {
    await renderPage();
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();
    await waitFor(() => expect(status()).toBe(SIGNAL_REALTIME_STATES.CONNECTED));

    // Fake timers from here on, so the backoff `handleClose` schedules is this test's to
    // advance rather than a second of real waiting.
    vi.useFakeTimers();

    // The defect this requirement exists to prevent: the rows stop changing and nothing on
    // screen says so, which looks exactly like a deployment that has gone quiet.
    await act(async () => {
      lastSocket().drop();
    });
    expect(status()).toBe(SIGNAL_REALTIME_STATES.DISCONNECTED);
    expect(screen.getByTestId('signal-trace-connection').textContent).toContain('DISCONNECTED');

    // ...and once `websocketClient`'s bounded, jittered backoff fires the next attempt
    // (Requirement 18.6), that is a different state and reads differently.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    vi.useRealTimers();

    expect(sockets.length).toBeGreaterThan(1);
    expect(status()).toBe(SIGNAL_REALTIME_STATES.CONNECTING);
    expect(screen.getByTestId('signal-trace-connection').textContent).toContain('RECONNECTING');
  });

  it('keeps the refusal banner and the connection indicator as separate reports', async () => {
    await renderPage();
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();

    await act(async () => {
      lastSocket().deliver(refusalFrame(channelA));
    });

    const refusals = await screen.findByTestId('signal-trace-refusals');
    expect(refusals.textContent).toContain(DEP_A);
    // A refusal is one channel of a LIVE connection, so the connection is still connected
    // and the two facts are told apart instead of one masking the other (18.3 vs 18.9).
    expect(status()).toBe(SIGNAL_REALTIME_STATES.CONNECTED);
    expect(refusals.contains(screen.getByTestId('signal-trace-connection'))).toBe(false);
  });

  it('does not report a drop when there was never a connection to lose', async () => {
    window.sessionStorage.removeItem('token');
    await renderPage();

    expect(sockets).toHaveLength(0);
    expect(status()).toBe(SIGNAL_REALTIME_STATES.UNAVAILABLE);
    const text = screen.getByTestId('signal-trace-connection').textContent;
    expect(text).toContain(UNAVAILABLE_REASONS.NOT_AUTHENTICATED);
    expect(text).not.toContain('DISCONNECTED');
    expect(text).not.toContain('RECONNECTING');
  });

  it('says there is nothing to watch when no deployment is subscribable', async () => {
    mockClient.get.mockImplementation(async (url) => {
      if (typeof url === 'string' && url.startsWith('/api/signal-trace/signals?')) {
        return { signals: [signalRow({ deployment_id: null })], total: 1, limit: 50, offset: 0 };
      }
      return {};
    });
    mockStrategies.listDeployments.mockResolvedValue(listing([{ deployment_id: DEP_A, status: 'stopped' }]));

    await renderPage();

    expect(sockets).toHaveLength(0);
    expect(status()).toBe(SIGNAL_REALTIME_STATES.UNAVAILABLE);
    expect(screen.getByTestId('signal-trace-connection').textContent).toContain(
      UNAVAILABLE_REASONS.NOTHING_TO_WATCH,
    );
  });

  it('leaves the pushed-update path it reports on untouched', async () => {
    // The indicator is a report ABOUT the realtime wiring, so the wiring itself must still
    // behave as tasks 19.1/19.2 left it.
    await renderPage();
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();

    await act(async () => {
      lastSocket().deliver({
        type: OWNED_CHANNEL_EVENTS.SIGNAL_STATUS_CHANGED,
        channel: channelA,
        deployment_id: DEP_A,
        seq: 1,
        dedup_key: 'sig_1:FILLED',
        signal_id: 'sig_1',
        order_lifecycle_state: 'FILLED',
        signal: signalRow({ status: 'executed', order_lifecycle_state: 'FILLED' }),
      });
    });

    expect(screen.getByText('executed')).toBeTruthy();
    expect(status()).toBe(SIGNAL_REALTIME_STATES.CONNECTED);
  });
});
