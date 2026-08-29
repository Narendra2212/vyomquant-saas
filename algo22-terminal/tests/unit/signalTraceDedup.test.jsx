/**
 * tests/unit/signalTraceDedup.test.jsx
 *
 * The Signal_Trace_Page's second dedup: the CONTENT key, and the reconnect snapshot it
 * makes safe.
 *
 * Spec: trading-lifecycle-integration task 19.2. Requirements 18.4, 18.5, 23.6.
 *
 * WHAT THIS FILE HOLDS IN PLACE
 * -----------------------------
 * 1. **A state change already applied is not applied again**, keyed on the frame's own
 *    `dedup_key` (`signal_id:order_lifecycle_state`) and INDEPENDENT of `seq` — the case
 *    `websocketClient`'s sequence check cannot catch, because a reconnect restarts both the
 *    server's per-channel counter and the client's `expectedSequence` at 1
 *    (Requirements 18.4, 23.6).
 * 2. **Dedup is per state, not per signal.** A genuinely new lifecycle state for a signal
 *    already on screen is applied; nothing here suppresses progress.
 * 3. **A reconnect snapshot is not overridden by pre-drop pushed state** (Requirements 18.5,
 *    18.6). This is the defect that makes the snapshot worth requesting at all: the overlay
 *    is merged ON TOP of the server's rows, so a stale pushed state would win forever over
 *    a transition that happened while the socket was down and will never be pushed again.
 * 4. **A frame that arrives while the snapshot read is in flight survives it**, because it
 *    may be newer than the read (Requirement 18.5).
 * 5. **The snapshot request sends no parameter the server does not implement.**
 *    `GET /api/signal-trace/signals` declares no `since`, so a `since` in the query string
 *    would be silently ignored — a claim of an incremental read that is not one.
 *
 * WHAT IS REAL HERE AND WHAT IS A DOUBLE
 * --------------------------------------
 * Real: `websocketClient` (its `expectedSequence`/`messageBuffer` gap detection included,
 * untouched by this task), `useSignalTraceRealtime`, `lib/signalTraceRealtime`, `SignalTrace`.
 * Doubles: the `WebSocket` class and the api client, exactly as in
 * `signalTraceRealtime.test.jsx`.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react';
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
import SignalTrace from '../../src/pages/SignalTrace';
import { useSignalTraceRealtime } from '../../src/hooks/useSignalTraceRealtime';
import {
  INITIAL_SIGNAL_REALTIME,
  mergeRealtimeSignals,
  signalRealtimeReducer,
} from '../../src/lib/signalTraceRealtime';
import { OWNED_CHANNEL_EVENTS } from '../../src/constants/wsChannels';

// ---------------------------------------------------------------------------
// Fixtures — the shapes actually on the wire
// ---------------------------------------------------------------------------

const STRATEGY_ID = 'stg_sig_1';
const DEP_A = 'dep_sig_a';
const channelA = `signal.${DEP_A}`;

/** `signal_service.Signal.to_public_dict()`, as both the REST list and the frame carry it. */
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

/** `ws_channels.signal_frame()`'s output — `dedup_key` built the way the server builds it. */
const signalFrame = (event, signal, { seq = 1, deploymentId = signal.deployment_id } = {}) => ({
  type: event,
  channel: `signal.${deploymentId}`,
  deployment_id: deploymentId,
  seq,
  dedup_key: `${signal.id}:${signal.order_lifecycle_state ?? ''}`,
  signal_id: signal.id,
  order_lifecycle_state: signal.order_lifecycle_state ?? null,
  signal,
});

const STATUS_CHANGED = OWNED_CHANNEL_EVENTS.SIGNAL_STATUS_CHANGED;
const GENERATED = OWNED_CHANNEL_EVENTS.SIGNAL_GENERATED;
const SNAPSHOT_FRAME = OWNED_CHANNEL_EVENTS.SIGNAL_SNAPSHOT;

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
}

const lastSocket = () => sockets[sockets.length - 1];

const openSocket = async () => {
  await act(async () => {
    lastSocket().open();
  });
};

/** A reconnect: a new transport under the same client, opened the way `connect` opens one. */
const reconnect = async () => {
  const socket = new FakeWebSocket('ws://x/ws/telemetry');
  wsClient.ws = socket;
  await act(async () => {
    socket.readyState = FakeWebSocket.OPEN;
    wsClient.handleOpen();
  });
  return socket;
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
// 1. The content key, independent of seq (Requirements 18.4, 23.6)
// ═══════════════════════════════════════════════════════════════════════════

const reduceFrames = (frames, state = INITIAL_SIGNAL_REALTIME, at = 1) =>
  frames.reduce((acc, frame) => signalRealtimeReducer(acc, { type: 'frame', frame, at }), state);

describe('content-key dedup', () => {
  it('applies a state change once and records its key', () => {
    const state = reduceFrames([signalFrame(GENERATED, signalRow(), { seq: 1 })]);

    expect(state.applied).toHaveLength(1);
    expect(state.appliedKeys['sig_1:PENDING']).toBe(1);
    expect(state.duplicatesDiscarded).toBe(0);
  });

  it('discards a repeat of an already-applied key whatever seq it carries', () => {
    const first = reduceFrames([signalFrame(STATUS_CHANGED, signalRow(), { seq: 1 })]);

    // Three shapes the same underlying status change arrives in: the identical frame, the
    // same content under a HIGHER seq (a re-publish, which the connection-level sequence
    // check passes because it is in order), and the same content under a LOWER one.
    const after = reduceFrames(
      [
        signalFrame(STATUS_CHANGED, signalRow(), { seq: 1 }),
        signalFrame(STATUS_CHANGED, signalRow(), { seq: 9 }),
        signalFrame(SNAPSHOT_FRAME, signalRow(), { seq: 1 }),
      ],
      first,
      2,
    );

    expect(after.duplicatesDiscarded).toBe(3);
    // Nothing was re-applied, and the identities downstream renderers memoise on held.
    expect(after.applied).toBe(first.applied);
    expect(after.signals).toBe(first.signals);
    // A frame did arrive, though, so liveness is not misreported as silence.
    expect(after.lastFrameAt).toBe(2);
  });

  it('discards a replay whose payload was rewritten under an already-applied state', () => {
    // The key names a signal and a lifecycle state, which is Requirement 18.4's identity
    // ("signal id combined with status/version") and exactly what the server keys on. A
    // second frame under the same state is the same event to this page.
    const first = reduceFrames([signalFrame(STATUS_CHANGED, signalRow({ order_lifecycle_state: 'FILLED', status: 'executed' }), { seq: 1 })]);
    const after = reduceFrames(
      [signalFrame(STATUS_CHANGED, signalRow({ order_lifecycle_state: 'FILLED', status: 'executed', filled: 2 }), { seq: 2 })],
      first,
    );

    expect(after.signals.sig_1.filled).toBeUndefined();
    expect(after.duplicatesDiscarded).toBe(1);
  });

  it('applies a genuinely new state for a signal it already knows', () => {
    // Dedup is per state, not per signal: suppressing this would freeze the page on the
    // first state it ever saw.
    const state = reduceFrames([
      signalFrame(GENERATED, signalRow(), { seq: 1 }),
      signalFrame(STATUS_CHANGED, signalRow({ order_lifecycle_state: 'SUBMITTED', status: 'accepted' }), { seq: 2 }),
      signalFrame(STATUS_CHANGED, signalRow({ order_lifecycle_state: 'FILLED', status: 'executed' }), { seq: 3 }),
    ]);

    expect(state.applied.map((entry) => entry.dedupKey)).toEqual([
      'sig_1:PENDING',
      'sig_1:SUBMITTED',
      'sig_1:FILLED',
    ]);
    expect(state.signals.sig_1.status).toBe('executed');
    expect(state.duplicatesDiscarded).toBe(0);
  });

  it('keeps two signals in the same state apart', () => {
    const state = reduceFrames([
      signalFrame(GENERATED, signalRow({ id: 'sig_1' }), { seq: 1 }),
      signalFrame(GENERATED, signalRow({ id: 'sig_2' }), { seq: 2 }),
    ]);

    expect(Object.keys(state.signals).sort()).toEqual(['sig_1', 'sig_2']);
    expect(state.duplicatesDiscarded).toBe(0);
  });

  it('forgets the ledger when the page changes what it is watching', () => {
    const state = reduceFrames([signalFrame(GENERATED, signalRow(), { seq: 1 })]);
    const reset = signalRealtimeReducer(state, { type: 'reset' });

    expect(reset.appliedKeys).toEqual({});
    expect(reset.duplicatesDiscarded).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. The snapshot settles the overlay (Requirements 18.5, 18.6)
// ═══════════════════════════════════════════════════════════════════════════

describe('the snapshot action', () => {
  it('drops pushed state the read supersedes and keeps what arrived after it', () => {
    const pushedBefore = reduceFrames([signalFrame(STATUS_CHANGED, signalRow({ id: 'old', order_lifecycle_state: 'FILLED' }), { seq: 1 })], INITIAL_SIGNAL_REALTIME, 100);
    const pushedAfter = reduceFrames(
      [signalFrame(STATUS_CHANGED, signalRow({ id: 'new', order_lifecycle_state: 'FILLED' }), { seq: 2 })],
      pushedBefore,
      300,
    );

    const settled = signalRealtimeReducer(pushedAfter, { type: 'snapshot', at: 200 });

    expect(Object.keys(settled.signals)).toEqual(['new']);
    // The ledger survives: the states the snapshot carries are states this client applied,
    // and the server's replay on the new connection must not re-apply them (18.4).
    expect(settled.appliedKeys['old:FILLED']).toBe(100);
    expect(settled.applied).toHaveLength(2);
  });

  it('is a no-op when there is nothing the read supersedes', () => {
    const state = reduceFrames([signalFrame(GENERATED, signalRow(), { seq: 1 })], INITIAL_SIGNAL_REALTIME, 500);
    expect(signalRealtimeReducer(state, { type: 'snapshot', at: 100 })).toBe(state);
    expect(signalRealtimeReducer(INITIAL_SIGNAL_REALTIME, { type: 'snapshot', at: 100 })).toBe(
      INITIAL_SIGNAL_REALTIME,
    );
  });

  it('leaves the server’s rows alone once the overlay is settled', () => {
    const rows = [signalRow({ id: 'sig_1', status: 'closed', order_lifecycle_state: 'CLOSED' })];
    const pushed = reduceFrames([signalFrame(STATUS_CHANGED, signalRow({ status: 'executed', order_lifecycle_state: 'FILLED' }), { seq: 1 })], INITIAL_SIGNAL_REALTIME, 100);

    // Before settling, the stale overlay wins — which is the defect.
    expect(mergeRealtimeSignals(rows, pushed.signals)[0].status).toBe('executed');

    const settled = signalRealtimeReducer(pushed, { type: 'snapshot', at: 200 });
    expect(mergeRealtimeSignals(rows, settled.signals)).toBe(rows);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. Through the socket and the hook — the gap mechanism is not touched
// ═══════════════════════════════════════════════════════════════════════════

describe('useSignalTraceRealtime with duplicate delivery', () => {
  it('discards a re-published state change that the sequence check lets through', async () => {
    const { result } = renderHook(() =>
      useSignalTraceRealtime({ deploymentIds: [DEP_A], fetchDeployments: vi.fn() }),
    );
    await waitFor(() => expect(result.current.channels).toEqual([channelA]));
    await openSocket();

    const filled = signalRow({ status: 'executed', order_lifecycle_state: 'FILLED' });
    await act(async () => {
      lastSocket().deliver(signalFrame(STATUS_CHANGED, filled, { seq: 1 }));
      // seq 2 is IN ORDER, so `websocketClient`'s existing gap/duplicate check passes it —
      // this is precisely the duplicate only the content key can catch.
      lastSocket().deliver(signalFrame(STATUS_CHANGED, filled, { seq: 2 }));
    });

    expect(result.current.realtime.applied).toHaveLength(1);
    expect(result.current.realtime.duplicatesDiscarded).toBe(1);
    expect(result.current.signals.sig_1.order_lifecycle_state).toBe('FILLED');
  });

  it('leaves websocketClient’s own sequence gap detection in place, unduplicated', async () => {
    const { result } = renderHook(() =>
      useSignalTraceRealtime({ deploymentIds: [DEP_A], fetchDeployments: vi.fn() }),
    );
    await waitFor(() => expect(result.current.channels).toEqual([channelA]));
    await openSocket();

    await act(async () => {
      // seq 3 with 1 expected: buffered by the client and a replay asked for, so it never
      // reaches the reducer. The reducer is not a second implementation of this.
      lastSocket().deliver(signalFrame(STATUS_CHANGED, signalRow({ order_lifecycle_state: 'FILLED' }), { seq: 3 }));
    });

    expect(result.current.realtime.applied).toHaveLength(0);
    expect(wsClient.messageBuffer.has(3)).toBe(true);
    expect(lastSocket().sent.some((frame) => frame.action === 'replay')).toBe(true);
  });

  it('settles the overlay only when the snapshot read reports that it landed', async () => {
    let resolveRead;
    const onSnapshot = vi.fn(() => new Promise((resolve) => { resolveRead = resolve; }));

    const { result } = renderHook(() =>
      useSignalTraceRealtime({ deploymentIds: [DEP_A], onSnapshot, fetchDeployments: vi.fn() }),
    );
    await waitFor(() => expect(result.current.channels).toEqual([channelA]));
    await openSocket();
    await act(async () => {
      resolveRead();
    });

    await act(async () => {
      lastSocket().deliver(signalFrame(STATUS_CHANGED, signalRow({ order_lifecycle_state: 'FILLED' }), { seq: 1 }));
    });
    expect(result.current.signals.sig_1).toBeTruthy();

    // A reconnect: the read is issued and is still in flight, so the pushed state stands.
    await reconnect();
    expect(result.current.signals.sig_1).toBeTruthy();

    await act(async () => {
      resolveRead();
    });
    expect(result.current.signals.sig_1).toBeUndefined();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 4. The page — a reconnect learns the transition it slept through
// ═══════════════════════════════════════════════════════════════════════════

const listSignals = (rows) => ({ signals: rows, total: rows.length, limit: 50, offset: 0 });

const renderPage = async () => {
  const view = render(
    <MemoryRouter initialEntries={[`/app/signal-trace?strategy_id=${STRATEGY_ID}`]}>
      <SignalTrace />
    </MemoryRouter>,
  );
  await screen.findByText('BTC/USDT');
  return view;
};

const signalListUrls = () =>
  mockClient.get.mock.calls
    .map(([url]) => url)
    .filter((url) => typeof url === 'string' && url.startsWith('/api/signal-trace/signals?'));

describe('SignalTrace page across a reconnect', () => {
  let served;

  beforeEach(() => {
    served = [signalRow()];
    mockClient.get.mockImplementation(async (url) => {
      if (typeof url === 'string' && url.startsWith('/api/signal-trace/signals?')) {
        return listSignals(served);
      }
      return {};
    });
    mockStrategies.listDeployments.mockResolvedValue({
      strategy_id: STRATEGY_ID,
      deployments: [{ deployment_id: DEP_A, status: 'running' }],
      total: 1,
    });
  });

  it('shows the state persisted while the socket was down, not the last one pushed', async () => {
    await renderPage();
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();

    await act(async () => {
      lastSocket().deliver(
        signalFrame(STATUS_CHANGED, signalRow({ status: 'executed', order_lifecycle_state: 'FILLED' }), { seq: 1 }),
      );
    });
    expect(screen.getByText('executed')).toBeTruthy();

    // The socket drops; the signal closes while it is down. No frame will ever be pushed
    // for that transition on this page's behalf — the snapshot is the only way to learn it.
    served = [signalRow({ status: 'closed', order_lifecycle_state: 'CLOSED' })];
    await reconnect();

    await waitFor(() => expect(screen.getByText('closed')).toBeTruthy());
    expect(screen.queryByText('executed')).toBeNull();
  });

  it('ignores a replay of a state change already on screen after the snapshot', async () => {
    await renderPage();
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();

    const filled = signalRow({ status: 'executed', order_lifecycle_state: 'FILLED' });
    await act(async () => {
      lastSocket().deliver(signalFrame(STATUS_CHANGED, filled, { seq: 1 }));
    });

    served = [filled];
    const reconnected = await reconnect();
    const readsAfterSnapshot = signalListUrls().length;

    // The new connection replays what it has. `expectedSequence` survived the reconnect, so
    // the replay is given a seq the client's sequence check accepts; the content key is what
    // refuses it.
    await act(async () => {
      reconnected.deliver(signalFrame(STATUS_CHANGED, filled, { seq: 2 }));
    });

    expect(screen.getByText('executed')).toBeTruthy();
    // No second read, no second application of one status change.
    expect(signalListUrls()).toHaveLength(readsAfterSnapshot);
  });

  it('sends no cursor the endpoint does not implement', async () => {
    // `signal_trace.list_signals` declares no `since` parameter, and FastAPI drops an
    // undeclared one silently. A `since` in this URL would be a claim of an incremental
    // read that the server never honoured.
    await renderPage();
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();
    await reconnect();

    const urls = signalListUrls();
    expect(urls.length).toBeGreaterThan(1);
    for (const url of urls) expect(url).not.toContain('since=');
    // What DOES scope the snapshot is the page's own filter set, which reached the server.
    expect(urls[urls.length - 1]).toContain(`strategy_id=${STRATEGY_ID}`);
  });
});
