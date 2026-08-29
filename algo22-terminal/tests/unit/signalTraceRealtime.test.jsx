/**
 * tests/unit/signalTraceRealtime.test.jsx
 *
 * The Signal_Trace_Page's realtime surface: one owner-scoped channel per deployment, on
 * the session's one socket, feeding the primary list and detail view.
 *
 * Spec: trading-lifecycle-integration task 19.1. Requirements 17.4, 18.1, 18.2, 18.6,
 * 18.7, 18.8, plus 18.3 for the refusal it surfaces.
 *
 * WHAT THIS FILE HOLDS IN PLACE
 * -----------------------------
 * 1. **The channel name and its scope** (Requirement 18.2). `signal.{deployment_id}`,
 *    composed per deployment — never one channel for a strategy — because
 *    `SIGNAL_FAMILY`'s resource is `deployment_id` and that is what the server's existing
 *    ownership lookup can authorise.
 * 2. **Which deployments a strategy-filtered page watches** (Requirement 17.4). Its active
 *    deployments, unioned with the ones its own signals name; a stopped deployment is not
 *    subscribed to, and a status we cannot read is not treated as active.
 * 3. **A pushed status change reaches the list without a manual refresh**
 *    (Requirement 18.1) — asserted end-to-end through the rendered page, because the whole
 *    defect this task exists to fix is that the realtime wiring was in a panel the page
 *    does not mount by default.
 * 4. **A reconnect asks for a snapshot** rather than assuming nothing was missed
 *    (Requirement 18.6), on the same bounded jittered backoff contract
 *    `useBuilderRealtime` defines.
 * 5. **A token refresh re-authenticates in place** (Requirement 18.7) — asserted by the
 *    socket construction count NOT moving.
 * 6. **Unmounting tears the subscription down** (Requirement 18.8).
 * 7. **A refused subscription is visible** (Requirement 18.3), not swallowed.
 *
 * WHAT IS REAL HERE AND WHAT IS A DOUBLE
 * --------------------------------------
 * Real: `websocketClient`, `useSignalTraceRealtime`, `lib/signalTraceRealtime`,
 * `constants/wsChannels`, `SignalTrace`.
 *
 * Doubles: the `WebSocket` class (jsdom's would attempt a real network connection) and the
 * api client. No projection, reducer or connection rule is replaced by a test-only version.
 *
 * Sequence-gap and content-key dedup (task 19.2) and the connection-status indicator
 * (task 19.3) are deliberately not asserted here.
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
  ACTIVE_BINDING_STATES,
  INITIAL_SIGNAL_REALTIME,
  SIGNAL_REALTIME_STATES,
  activeDeploymentIds,
  deploymentIdOfChannel,
  deploymentIdsFromSignals,
  frameDedupKey,
  isActiveDeploymentStatus,
  mergeDeploymentIds,
  mergeRealtimeSignals,
  signalChannel,
  signalRealtimeReducer,
  signalRealtimeStateFromSocket,
  signalRefusalList,
  signalTraceChannels,
  unlistedSignalIds,
} from '../../src/lib/signalTraceRealtime';
import {
  OWNED_CHANNELS,
  OWNED_CHANNEL_EVENTS,
  isOwnedChannel,
  isValidChannel,
} from '../../src/constants/wsChannels';
import { BUILDER_RECONNECT_POLICY } from '../../src/hooks/useBuilderRealtime';
import { SIGNAL_RECONNECT_POLICY } from '../../src/hooks/useSignalTraceRealtime';

// ---------------------------------------------------------------------------
// Fixtures — the shapes actually on the wire
// ---------------------------------------------------------------------------

const STRATEGY_ID = 'stg_sig_1';
const DEP_A = 'dep_sig_a';
const DEP_B = 'dep_sig_b';

const channelA = `signal.${DEP_A}`;
const channelB = `signal.${DEP_B}`;

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

/** `ws_channels.signal_frame()`'s output. */
const signalFrame = (
  event,
  signal,
  { seq = 1, deploymentId = signal.deployment_id, previousState = null } = {},
) => ({
  type: event,
  channel: `signal.${deploymentId}`,
  deployment_id: deploymentId,
  seq,
  dedup_key: `${signal.id}:${signal.order_lifecycle_state ?? ''}`,
  signal_id: signal.id,
  order_lifecycle_state: signal.order_lifecycle_state ?? null,
  signal,
  ...(previousState ? { previous_state: previousState } : {}),
});

const refusalFrame = (channel) => ({
  type: 'subscription_refused',
  channel,
  code: 'CHANNEL_FORBIDDEN',
  reason: 'That deployment is not available to this user, so the subscription was refused.',
});

// ---------------------------------------------------------------------------
// The one double: a WebSocket that goes nowhere and records everything
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

  actions(action) {
    return this.sent.filter((frame) => frame.action === action);
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
// 1. The channel name mirrors ws_channels.SIGNAL_FAMILY
// ═══════════════════════════════════════════════════════════════════════════

describe('the signal channel name mirrors backend SIGNAL_FAMILY', () => {
  it('is scoped to a deployment, not to a strategy or a user', () => {
    expect(OWNED_CHANNELS.SIGNAL.namespace).toBe('signal');
    expect(OWNED_CHANNELS.SIGNAL.resource).toBe('deployment_id');
    expect(signalChannel(DEP_A)).toBe(channelA);
  });

  it('composes one channel per deployment, in order, without duplicates', () => {
    expect(signalTraceChannels([DEP_A, DEP_B, DEP_A])).toEqual([channelA, channelB]);
  });

  it.each([['', null], [null, null], ['   ', null], ['a b', null], ['x'.repeat(65), null]])(
    'contributes nothing for the malformed deployment id %p',
    (id) => {
      // Not an error: a page that has not learned a deployment yet is a normal state, and a
      // malformed id must not become a name the server would have to refuse.
      expect(signalChannel(id)).toBeNull();
      expect(signalTraceChannels([id])).toEqual([]);
    },
  );

  it('stays out of the fixed ChannelType vocabulary, as the parameterised families do', () => {
    expect(isOwnedChannel(channelA)).toBe(true);
    expect(isValidChannel(channelA)).toBe(false);
    expect(deploymentIdOfChannel(channelA)).toBe(DEP_A);
    // A different family's channel is not read as a signal channel.
    expect(deploymentIdOfChannel(`execution.${DEP_A}`)).toBeNull();
  });

  it('reuses useBuilderRealtime’s reconnect contract rather than restating it', () => {
    // Requirement 18.6 defines this page's backoff by reference to that contract, so there
    // has to be exactly one policy object for it to keep referring to.
    expect(SIGNAL_RECONNECT_POLICY).toBe(BUILDER_RECONNECT_POLICY);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. Which deployments a strategy-filtered page watches (Requirement 17.4)
// ═══════════════════════════════════════════════════════════════════════════

describe('the deployments a strategy currently has', () => {
  it('counts the three states the backend calls active, and no others', () => {
    expect(ACTIVE_BINDING_STATES).toEqual(['DEPLOYING', 'RUNNING', 'PAUSED']);
    for (const status of ['running', 'active', 'paused', 'deploying', 'deployed', 'starting', 'queued']) {
      expect(isActiveDeploymentStatus(status)).toBe(true);
    }
    for (const status of ['stopped', 'stopping', 'failed', 'error', 'crashed', 'completed']) {
      expect(isActiveDeploymentStatus(status)).toBe(false);
    }
  });

  it('does not treat a status it cannot read as active', () => {
    // Subscribing on an unreadable spelling would be asking the server for a channel on
    // the strength of a guess.
    expect(isActiveDeploymentStatus('somehow_new')).toBe(false);
    expect(isActiveDeploymentStatus(null)).toBe(false);
  });

  it('keeps the active deployments of a listing and drops the finished ones', () => {
    expect(
      activeDeploymentIds({
        deployments: [
          { deployment_id: DEP_A, status: 'running' },
          { deployment_id: 'dep_stopped', status: 'stopped' },
          { deployment_id: DEP_B, status: 'paused' },
          { deployment_id: 'dep_failed', status: 'failed' },
          { deployment_id: DEP_A, status: 'running' },
        ],
      }),
    ).toEqual([DEP_A, DEP_B]);
  });

  it('keeps a row whose status is absent rather than silently stopping watching it', () => {
    expect(activeDeploymentIds({ deployments: [{ deployment_id: DEP_A }] })).toEqual([DEP_A]);
    expect(activeDeploymentIds(null)).toEqual([]);
    expect(activeDeploymentIds({ deployments: 'nope' })).toEqual([]);
  });

  it('also reads the deployments the loaded signals name, which the listing can miss', () => {
    // `list_deployments` serves an in-process registry that does not contain a deployment
    // started through `deploy_version`, and an unfiltered page has no strategy to ask about.
    expect(
      deploymentIdsFromSignals([
        signalRow({ id: 's1', deployment_id: DEP_A }),
        signalRow({ id: 's2', deployment_id: DEP_B }),
        signalRow({ id: 's3', deployment_id: DEP_A }),
        signalRow({ id: 's4', deployment_id: null }),
      ]),
    ).toEqual([DEP_A, DEP_B]);
  });

  it('unions the two sources, first occurrence winning', () => {
    expect(mergeDeploymentIds([DEP_A], [DEP_B, DEP_A], null, ['', '  '])).toEqual([DEP_A, DEP_B]);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. The reducer — a projection of the server's own frames
// ═══════════════════════════════════════════════════════════════════════════

const reduceFrames = (frames, state = INITIAL_SIGNAL_REALTIME) =>
  frames.reduce((acc, frame) => signalRealtimeReducer(acc, { type: 'frame', frame, at: 1 }), state);

describe('signalRealtimeReducer', () => {
  it('applies the three frame types the channel carries', () => {
    const generated = signalRow();
    const filled = signalRow({ status: 'executed', order_lifecycle_state: 'FILLED' });

    const state = reduceFrames([
      signalFrame(OWNED_CHANNEL_EVENTS.SIGNAL_GENERATED, generated, { seq: 1 }),
      signalFrame(OWNED_CHANNEL_EVENTS.SIGNAL_STATUS_CHANGED, filled, { seq: 2 }),
    ]);

    expect(state.signals.sig_1.order_lifecycle_state).toBe('FILLED');
    expect(state.applied.map((entry) => entry.dedupKey)).toEqual(['sig_1:PENDING', 'sig_1:FILLED']);
    expect(state.applied.map((entry) => entry.seq)).toEqual([1, 2]);
    expect(state.lastFrameAt).toBe(1);
  });

  it('carries the state the server assigned, deriving none of its own', () => {
    const state = reduceFrames([
      signalFrame(OWNED_CHANNEL_EVENTS.SIGNAL_SNAPSHOT, signalRow({ order_lifecycle_state: 'PARTIALLY_FILLED' })),
    ]);
    expect(state.signals.sig_1.order_lifecycle_state).toBe('PARTIALLY_FILLED');
  });

  it('ignores a frame type this build does not know, rather than storing it', () => {
    const state = reduceFrames([
      { type: 'signal.something_new', channel: channelA, signal: signalRow() },
      { type: OWNED_CHANNEL_EVENTS.SIGNAL_GENERATED, channel: channelA, signal: null },
    ]);
    expect(state).toBe(INITIAL_SIGNAL_REALTIME);
  });

  it('records a refusal against the deployment it is about (Requirement 18.3)', () => {
    const state = reduceFrames([refusalFrame(channelB)]);
    const [refusal] = signalRefusalList(state);

    expect(refusal.channel).toBe(channelB);
    expect(refusal.deploymentId).toBe(DEP_B);
    expect(refusal.code).toBe('CHANNEL_FORBIDDEN');
    expect(refusal.reason).toContain('not available to this user');
  });

  it('reads the server’s dedup key and reconstructs it only when absent', () => {
    // Task 19.2 is what *uses* this; 19.1 records it as the server sent it.
    expect(frameDedupKey({ dedup_key: 'sig_1:FILLED' })).toBe('sig_1:FILLED');
    expect(frameDedupKey({ signal_id: 'sig_1', order_lifecycle_state: 'FILLED' })).toBe('sig_1:FILLED');
    expect(frameDedupKey({})).toBe('');
  });

  it('drops everything on a reset but keeps the shared connection’s state', () => {
    const state = reduceFrames([signalFrame(OWNED_CHANNEL_EVENTS.SIGNAL_GENERATED, signalRow())]);
    const connected = signalRealtimeReducer(state, { type: 'status', status: SIGNAL_REALTIME_STATES.CONNECTED });
    const reset = signalRealtimeReducer(connected, { type: 'reset' });

    expect(reset.signals).toEqual({});
    expect(reset.applied).toEqual([]);
    expect(reset.status).toBe(SIGNAL_REALTIME_STATES.CONNECTED);
  });

  it('maps every socket status onto a reported state, unknown included', () => {
    expect(signalRealtimeStateFromSocket('connected')).toBe(SIGNAL_REALTIME_STATES.CONNECTED);
    expect(signalRealtimeStateFromSocket('connecting')).toBe(SIGNAL_REALTIME_STATES.CONNECTING);
    expect(signalRealtimeStateFromSocket('error')).toBe(SIGNAL_REALTIME_STATES.DISCONNECTED);
    expect(signalRealtimeStateFromSocket('failed')).toBe(SIGNAL_REALTIME_STATES.DISCONNECTED);
    expect(signalRealtimeStateFromSocket('who_knows')).toBe(SIGNAL_REALTIME_STATES.DISCONNECTED);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 4. Projecting pushed state onto the rendered page
// ═══════════════════════════════════════════════════════════════════════════

describe('mergeRealtimeSignals / unlistedSignalIds', () => {
  const rows = [signalRow({ id: 'a' }), signalRow({ id: 'b' })];

  it('updates a row in place without reordering the server’s page', () => {
    const merged = mergeRealtimeSignals(rows, { b: { id: 'b', status: 'executed' } });

    expect(merged.map((row) => row.id)).toEqual(['a', 'b']);
    expect(merged[1].status).toBe('executed');
    // Fields the frame did not mention are the row's own.
    expect(merged[1].symbol).toBe('BTC/USDT');
    expect(merged[0]).toBe(rows[0]);
  });

  it('returns the same array when nothing applied, so no render is forced', () => {
    expect(mergeRealtimeSignals(rows, { zzz: { id: 'zzz' } })).toBe(rows);
    expect(mergeRealtimeSignals(rows, null)).toBe(rows);
  });

  it('reports a pushed signal that is not on this page instead of inserting it', () => {
    // Whether it satisfies this page's filters, sort and offset is the server's question.
    expect(unlistedSignalIds(rows, { b: { id: 'b' }, c: { id: 'c' } })).toEqual(['c']);
    expect(unlistedSignalIds(rows, null)).toEqual([]);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 5. The hook — what it subscribes, and when it lets go
// ═══════════════════════════════════════════════════════════════════════════

const listing = (rows) => ({ strategy_id: STRATEGY_ID, deployments: rows, total: rows.length });

describe('useSignalTraceRealtime', () => {
  it('subscribes one channel per active deployment of the filtered strategy', async () => {
    const fetchDeployments = vi.fn().mockResolvedValue(
      listing([
        { deployment_id: DEP_A, status: 'running' },
        { deployment_id: DEP_B, status: 'paused' },
        { deployment_id: 'dep_dead', status: 'stopped' },
      ]),
    );

    const { result } = renderHook(() =>
      useSignalTraceRealtime({ strategyId: STRATEGY_ID, fetchDeployments }),
    );

    await waitFor(() => expect(result.current.channels).toEqual([channelA, channelB]));
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();

    expect(fetchDeployments).toHaveBeenCalledWith(STRATEGY_ID);
    expect(lastSocket().actions('subscribe').map((f) => f.channel)).toEqual([channelA, channelB]);
    // One socket, whatever the number of channels (Requirement 18.2 is about authorisation
    // scope, not about connections).
    expect(wsClient.holdCount()).toBe(1);
  });

  it('also holds a channel for a deployment only its own signals name', async () => {
    const fetchDeployments = vi.fn().mockResolvedValue(listing([{ deployment_id: DEP_A, status: 'running' }]));

    const { result } = renderHook(() =>
      useSignalTraceRealtime({ strategyId: STRATEGY_ID, deploymentIds: [DEP_B], fetchDeployments }),
    );

    await waitFor(() => expect(result.current.channels).toEqual([channelA, channelB]));
  });

  it('reports an unreadable deployment listing rather than watching nothing quietly', async () => {
    const fetchDeployments = vi.fn().mockRejectedValue(new Error('listing failed'));

    const { result } = renderHook(() =>
      useSignalTraceRealtime({ strategyId: STRATEGY_ID, deploymentIds: [DEP_B], fetchDeployments }),
    );

    await waitFor(() => expect(result.current.deploymentsError).toBeTruthy());
    // The ids its own signals named are a separate source and are still held.
    expect(result.current.channels).toEqual([channelB]);
  });

  it('asks the server nothing for a half-typed strategy filter', async () => {
    const fetchDeployments = vi.fn();
    renderHook(() => useSignalTraceRealtime({ strategyId: 'not a valid id', fetchDeployments }));

    await waitFor(() => expect(fetchDeployments).not.toHaveBeenCalled());
    expect(sockets).toHaveLength(0);
  });

  it('opens no socket without a token, and none with nothing to subscribe', async () => {
    window.sessionStorage.removeItem('token');
    const withoutToken = renderHook(() =>
      useSignalTraceRealtime({ deploymentIds: [DEP_A], fetchDeployments: vi.fn() }),
    );
    await waitFor(() => expect(withoutToken.result.current.status).toBe(SIGNAL_REALTIME_STATES.UNAVAILABLE));
    expect(sockets).toHaveLength(0);
    withoutToken.unmount();

    window.sessionStorage.setItem('token', 'test-token');
    const withoutDeployments = renderHook(() => useSignalTraceRealtime({ fetchDeployments: vi.fn() }));
    await waitFor(() =>
      expect(withoutDeployments.result.current.status).toBe(SIGNAL_REALTIME_STATES.UNAVAILABLE),
    );
    expect(sockets).toHaveLength(0);
  });

  it('tears the subscription down when the page unmounts (Requirement 18.8)', async () => {
    const { unmount, result } = renderHook(() =>
      useSignalTraceRealtime({ deploymentIds: [DEP_A, DEP_B], fetchDeployments: vi.fn() }),
    );
    await waitFor(() => expect(result.current.channels).toHaveLength(2));
    await openSocket();
    const socket = lastSocket();

    unmount();

    expect(socket.actions('unsubscribe').map((f) => f.channel).sort()).toEqual(
      [channelA, channelB].sort(),
    );
    expect(wsClient.holdCount()).toBe(0);
    expect(socket.closes).toBe(1);
  });

  it('requests a snapshot on mount over an open socket and on every reconnect (18.6)', async () => {
    const onSnapshot = vi.fn();
    const { result } = renderHook(() =>
      useSignalTraceRealtime({ deploymentIds: [DEP_A], onSnapshot, fetchDeployments: vi.fn() }),
    );
    await waitFor(() => expect(result.current.channels).toEqual([channelA]));

    await openSocket();
    expect(onSnapshot).toHaveBeenCalledWith('reconnect');

    onSnapshot.mockClear();
    const reconnected = new FakeWebSocket('ws://x/ws/telemetry');
    wsClient.ws = reconnected;
    await act(async () => {
      reconnected.readyState = FakeWebSocket.OPEN;
      wsClient.handleOpen();
    });

    expect(onSnapshot).toHaveBeenCalledWith('reconnect');
    // The channels were resubscribed on the new connection before the snapshot was asked
    // for: the server clears a closed connection's authorised subscriptions.
    expect(reconnected.actions('subscribe').map((f) => f.channel)).toEqual([channelA]);
  });

  it('re-authenticates in place when the session token is refreshed (18.7)', async () => {
    const { result } = renderHook(() =>
      useSignalTraceRealtime({ deploymentIds: [DEP_A], fetchDeployments: vi.fn() }),
    );
    await waitFor(() => expect(result.current.channels).toEqual([channelA]));
    await openSocket();
    const socket = lastSocket();

    await act(async () => {
      window.sessionStorage.setItem('token', 'refreshed-token');
      window.dispatchEvent(new Event('storage'));
    });

    expect(socket.actions('auth').map((f) => f.token)).toContain('refreshed-token');
    // The defect this requirement exists to prevent: a reconnect storm every refresh.
    expect(sockets).toHaveLength(1);
    expect(socket.closes).toBe(0);
  });

  it('hands a refused subscription back to the caller (Requirement 18.3)', async () => {
    const { result } = renderHook(() =>
      useSignalTraceRealtime({ deploymentIds: [DEP_A], fetchDeployments: vi.fn() }),
    );
    await waitFor(() => expect(result.current.channels).toEqual([channelA]));
    await openSocket();

    await act(async () => {
      lastSocket().deliver(refusalFrame(channelA));
    });

    expect(result.current.refusals).toHaveLength(1);
    expect(result.current.refusals[0].deploymentId).toBe(DEP_A);
    // The connection stayed open: a refusal is answered per channel, not per socket.
    expect(result.current.status).toBe(SIGNAL_REALTIME_STATES.CONNECTED);
  });

  it('reduces the frames that arrive on a held channel', async () => {
    const { result } = renderHook(() =>
      useSignalTraceRealtime({ deploymentIds: [DEP_A], fetchDeployments: vi.fn() }),
    );
    await waitFor(() => expect(result.current.channels).toEqual([channelA]));
    await openSocket();

    await act(async () => {
      lastSocket().deliver(
        signalFrame(OWNED_CHANNEL_EVENTS.SIGNAL_STATUS_CHANGED, signalRow({ status: 'executed', order_lifecycle_state: 'FILLED' })),
      );
    });

    expect(result.current.signals.sig_1.order_lifecycle_state).toBe('FILLED');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 6. The page — the realtime wiring is in the list, not only in the panel
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

describe('SignalTrace page', () => {
  beforeEach(() => {
    mockClient.get.mockImplementation(async (url) => {
      if (typeof url === 'string' && url.startsWith('/api/signal-trace/signals?')) {
        return { signals: [signalRow()], total: 1, limit: 50, offset: 0 };
      }
      return {};
    });
    mockStrategies.listDeployments.mockResolvedValue(listing([{ deployment_id: DEP_A, status: 'running' }]));
  });

  it('subscribes the channel of the strategy’s deployment on load (17.4, 18.2)', async () => {
    await renderPage();

    await waitFor(() => expect(mockStrategies.listDeployments).toHaveBeenCalledWith(STRATEGY_ID));
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();

    expect(lastSocket().actions('subscribe').map((f) => f.channel)).toEqual([channelA]);
    // The strategy filter reached the list request too, without any user action.
    expect(mockClient.get.mock.calls.some(([url]) => url.includes(`strategy_id=${STRATEGY_ID}`))).toBe(true);
  });

  it('updates a listed signal from a pushed frame, with no manual refresh (18.1)', async () => {
    await renderPage();
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();
    expect(screen.getByText('pending')).toBeTruthy();

    const callsBefore = mockClient.get.mock.calls.length;
    await act(async () => {
      lastSocket().deliver(
        signalFrame(
          OWNED_CHANNEL_EVENTS.SIGNAL_STATUS_CHANGED,
          signalRow({ status: 'executed', order_lifecycle_state: 'FILLED' }),
          { seq: 1, previousState: 'PENDING' },
        ),
      );
    });

    expect(screen.getByText('executed')).toBeTruthy();
    expect(screen.queryByText('pending')).toBeNull();
    // The row changed because a frame arrived, not because the page re-read the list.
    expect(mockClient.get.mock.calls.length).toBe(callsBefore);
  });

  it('announces a pushed signal the page does not show instead of inventing its position', async () => {
    await renderPage();
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();

    await act(async () => {
      lastSocket().deliver(
        signalFrame(OWNED_CHANNEL_EVENTS.SIGNAL_GENERATED, signalRow({ id: 'sig_new' }), { seq: 1 }),
      );
    });

    expect(screen.getByTestId('signal-trace-new-signals').textContent).toContain('1 new signal');
  });

  it('shows a refused subscription rather than silently displaying nothing (18.3)', async () => {
    await renderPage();
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();

    await act(async () => {
      lastSocket().deliver(refusalFrame(channelA));
    });

    const banner = await screen.findByTestId('signal-trace-refusals');
    expect(banner.textContent).toContain(DEP_A);
    expect(banner.textContent).toContain('CHANNEL_FORBIDDEN');
  });

  it('releases the socket when the page is navigated away from (18.8)', async () => {
    const { unmount } = await renderPage();
    await waitFor(() => expect(sockets).toHaveLength(1));
    await openSocket();
    const socket = lastSocket();

    unmount();

    expect(socket.actions('unsubscribe').map((f) => f.channel)).toEqual([channelA]);
    expect(wsClient.holdCount()).toBe(0);
  });
});
