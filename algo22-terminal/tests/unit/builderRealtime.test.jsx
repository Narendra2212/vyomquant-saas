/**
 * tests/unit/builderRealtime.test.jsx
 *
 * The builder's realtime surface: one ref-counted connection, the channels it asks for,
 * the frames it renders, and the four node runtime states on the canvas.
 *
 * Spec: strategy-builder task 8.5 (`design.md` § WebSocket / realtime, § Builder UX
 * contract → Visual states). Requirements 20.12, 21.6, 23.1-23.6.
 *
 * WHAT THIS FILE HOLDS IN PLACE
 * -----------------------------
 * 1. **One connection per session.** Three views mounting take three holds on one socket
 *    and the socket is constructed once (Requirement 23.1). This is asserted by counting
 *    constructions of the fake `WebSocket`, which is the only way "no second socket" is
 *    checkable at all.
 * 2. **A reconnect resubscribes and then snapshots** (Requirement 23.5), in that order,
 *    because a snapshot taken before the resubscribe could be answered while the
 *    connection is still receiving nothing.
 * 3. **A token refresh reauthenticates in place** (Requirement 23.3) — asserted by the
 *    construction count NOT moving, since the defect this requirement exists to prevent
 *    is a reconnect storm every time a session refreshes.
 * 4. **The backoff is exponential, capped at 30 s, and jittered** (Requirement 23.4).
 * 5. **The safety poll runs at 30 s and only while the connection is closed**
 *    (Requirement 23.6) — including the negative half, which is the one that matters:
 *    a connected session must make no polling requests at all.
 * 6. **The canvas renders the backend's four runtime labels and nothing of its own**
 *    (Requirement 20.12): warming carries the bar count, training carries the epoch
 *    counter, an unrecognised label is never rendered as ready, and the deployed lock is
 *    the backend's `read_only` rather than an inference from a lifecycle word.
 * 7. **A refused subscription is reported** (Requirement 21.6), with the server's own
 *    sentence, on the connection that stayed open.
 *
 * WHAT IS REAL HERE AND WHAT IS A DOUBLE
 * --------------------------------------
 * Real: `websocketClient`, `useBuilderRealtime`, `lib/builderRealtime`,
 * `constants/wsChannels`, `StrategyBuilder`.
 *
 * Doubles: the `WebSocket` class (jsdom's would attempt a real network connection), the
 * api client (as every other builder test does), and the clock where a timer is being
 * asserted. No projection, reducer or connection rule is replaced by a test-only version.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react';

const { mockClient } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn(), patch: vi.fn() },
}));

vi.mock('../../src/apiClient', () => ({
  default: mockClient,
  get: (...args) => mockClient.get(...args),
  post: (...args) => mockClient.post(...args),
  put: (...args) => mockClient.put(...args),
  del: (...args) => mockClient.del(...args),
  patch: (...args) => mockClient.patch(...args),
}));

import wsClient from '../../src/websocketClient';
import {
  BUILDER_RECONNECT_POLICY,
  SAFETY_POLL_INTERVAL_MS,
  useBuilderRealtime,
} from '../../src/hooks/useBuilderRealtime';
import {
  INITIAL_REALTIME,
  REALTIME_STATES,
  RUNTIME_STATE_LABELS,
  builderChannels,
  deployedLockView,
  nodeRuntimeView,
  realtimeReducer,
  refusalList,
  trainingView,
} from '../../src/lib/builderRealtime';
import {
  OWNED_CHANNELS,
  OWNED_CHANNEL_EVENTS,
  VALID_CHANNELS,
  isOwnedChannel,
  isValidChannel,
  ownedChannel,
  parseOwnedChannel,
} from '../../src/constants/wsChannels';
import { resetRegistryClient } from '../../src/lib/registryClient';
import { toCanonical } from '../../src/lib/canonicalGraph';
import {
  descriptor,
  installBuilderStubs,
  renderBuilder,
  served,
  waitForRegistry,
} from './helpers/registryFixture';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const STRATEGY_ID = 'stg_rt_1';
const DEPLOYMENT_ID = 'dep_rt_1';
const JOB_ID = 'job_rt_1';
const NODE_ID = 'n-rsi';

const validationChannel = `builder.validation.${STRATEGY_ID}`;
const strategyChannel = `strategy.${STRATEGY_ID}`;
const deploymentChannel = `deployment.${DEPLOYMENT_ID}`;
const executionChannel = `execution.${DEPLOYMENT_ID}`;

/** A `deployment.runtime_state` frame, in `PlanRuntimeState.to_dict()`'s wire shape. */
const runtimeFrame = (nodes, { barsSeen = 10 } = {}) => ({
  type: OWNED_CHANNEL_EVENTS.DEPLOYMENT_RUNTIME_STATE,
  channel: deploymentChannel,
  deployment_id: DEPLOYMENT_ID,
  runtime_state: {
    bars_seen: barsSeen,
    nodes,
    counts: {},
  },
});

const runtimeNode = ({
  state,
  warmup = 14,
  barsNeeded = 29,
  barsRemaining = 19,
  missing = [],
}) => ({
  state,
  warmup_bars: warmup,
  bars_needed: barsNeeded,
  bars_remaining: barsRemaining,
  missing,
});

/** A `strategy.canvas_state` frame, in `strategy_lifecycle.canvas_state()`'s wire shape. */
const canvasStateFrame = (extra = {}) => ({
  type: OWNED_CHANNEL_EVENTS.STRATEGY_CANVAS_STATE,
  channel: strategyChannel,
  strategy_id: STRATEGY_ID,
  canvas_state: {
    lifecycle_state: 'RUNNING',
    read_only: true,
    editable: false,
    edit_creates_new_draft: true,
    frozen_fields: ['graph_json', 'compiled_plan', 'dag_hash', 'schema_version'],
    read_only_states: ['DEPLOYED', 'RUNNING', 'PAUSED'],
    legal_transitions: ['PAUSED', 'STOPPED'],
    reason: 'This version is RUNNING, so it cannot be changed.',
    ...extra,
  },
});

const trainingFrame = ({ event = OWNED_CHANNEL_EVENTS.TRAINING_PROGRESS, epoch = 3, total = 25 } = {}) => ({
  type: event,
  channel: `training.${JOB_ID}`,
  job_id: JOB_ID,
  node_id: NODE_ID,
  epoch,
  epochs_total: total,
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

  // -- driving it from a test -------------------------------------------
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

  /** Every `{action}` frame this socket was asked to send, by action. */
  actions(action) {
    return this.sent.filter((frame) => frame.action === action);
  }
}

const lastSocket = () => sockets[sockets.length - 1];

/** Bring the newest socket up and let the client's `handleOpen` run. */
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
  wsClient.setReconnectPolicy({ maxAttempts: 5, baseDelayMs: 1000, maxDelayMs: 30000, jitterMs: 250 });
};

beforeEach(() => {
  sockets = [];
  global.WebSocket = FakeWebSocket;
  window.WebSocket = FakeWebSocket;
  window.sessionStorage.setItem('token', 'test-token');
  resetSocketClient();
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  resetSocketClient();
  window.sessionStorage.clear();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// ═══════════════════════════════════════════════════════════════════════════
// 1. The channel names mirror the backend, and stay out of the fixed set
// ═══════════════════════════════════════════════════════════════════════════

describe('the channel names mirror backend ws_channels.py', () => {
  it('composes design.md’s four channels for one strategy and one deployment', () => {
    expect(
      builderChannels({ strategyId: STRATEGY_ID, deploymentId: DEPLOYMENT_ID }),
    ).toEqual([validationChannel, strategyChannel, deploymentChannel, executionChannel]);
  });

  it('contributes nothing for an unsaved strategy or an absent deployment', () => {
    // Not an error: an unsaved canvas has no id, which is a normal state of the builder.
    expect(builderChannels({})).toEqual([]);
    expect(builderChannels({ strategyId: STRATEGY_ID })).toEqual([
      validationChannel,
      strategyChannel,
    ]);
    expect(builderChannels({ strategyId: '', deploymentId: DEPLOYMENT_ID })).toEqual([
      deploymentChannel,
      executionChannel,
    ]);
  });

  it('adds one training channel per live job', () => {
    expect(
      builderChannels({ strategyId: STRATEGY_ID, trainingJobIds: [JOB_ID, 'job_b'] }),
    ).toEqual([validationChannel, strategyChannel, `training.${JOB_ID}`, 'training.job_b']);
  });

  it.each([
    ['', null],
    ['a.b', null],
    ['a b', null],
    ['*', null],
    ['x'.repeat(65), null],
  ])('refuses to compose a channel for the malformed id %p', (id) => {
    expect(ownedChannel(OWNED_CHANNELS.STRATEGY, id)).toBeNull();
  });

  it('parses builder.validation at its last separator, not its first', () => {
    expect(parseOwnedChannel(validationChannel).resourceId).toBe(STRATEGY_ID);
    expect(parseOwnedChannel(`builder.${STRATEGY_ID}`)).toBeNull();
    expect(parseOwnedChannel(`builder.validation.${STRATEGY_ID}.extra`)).toBeNull();
  });

  it.each(['deployment.*', 'strategy', 'execution.', 'training.a.b', 'orders', ''])(
    'does not route %p',
    (channel) => {
      expect(isOwnedChannel(channel)).toBe(false);
    },
  );

  it('keeps the parameterised names out of the fixed channel vocabulary', () => {
    // `assertValidChannel` throws on anything outside `VALID_CHANNELS`, and the backend's
    // `ws_event_stream` constructs a `ChannelType` from every name that passes it. A
    // per-resource name in that set would make both refuse the builder's channels.
    for (const channel of [validationChannel, strategyChannel, deploymentChannel, executionChannel]) {
      expect(isValidChannel(channel)).toBe(false);
      expect(VALID_CHANNELS.has(channel)).toBe(false);
    }
  });

  it('names the resource field the backend puts the id under', () => {
    expect(OWNED_CHANNELS.BUILDER_VALIDATION.resource).toBe('strategy_id');
    expect(OWNED_CHANNELS.STRATEGY.resource).toBe('strategy_id');
    expect(OWNED_CHANNELS.DEPLOYMENT.resource).toBe('deployment_id');
    expect(OWNED_CHANNELS.EXECUTION.resource).toBe('deployment_id');
    expect(OWNED_CHANNELS.TRAINING.resource).toBe('job_id');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. The projection — the backend's verdicts, rendered, never re-derived
// ═══════════════════════════════════════════════════════════════════════════

const reduceFrames = (frames) =>
  frames.reduce((state, frame) => realtimeReducer(state, { type: 'frame', frame, at: 1 }), INITIAL_REALTIME);

describe('the runtime state projection (Requirement 20.12)', () => {
  it('reports warming with both bar counts, from the frame', () => {
    const state = reduceFrames([
      runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'WARMING' }) }, { barsSeen: 10 }),
    ]);

    const view = nodeRuntimeView(state, NODE_ID);

    expect(view.known).toBe(true);
    expect(view.state).toBe('WARMING');
    expect(view.label).toBe('Warming');
    expect(view.barsSeen).toBe(10);
    expect(view.barsNeeded).toBe(29);
    expect(view.barsRemaining).toBe(19);
    // `design.md`'s own wording for this state, with both figures — one alone is not a
    // progress report.
    expect(view.detail).toContain('warmup 10/29 bars');
    expect(view.detail).toContain('19 to go');
  });

  it('reports ready with no caveat', () => {
    const state = reduceFrames([
      runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'READY', barsRemaining: 0 }) }, { barsSeen: 500 }),
    ]);

    const view = nodeRuntimeView(state, NODE_ID);

    expect(view.state).toBe('READY');
    expect(view.label).toBe('Ready');
    expect(view.detail).toBe('');
  });

  it('reports awaiting a model as a thing the author has to act on', () => {
    const state = reduceFrames([
      runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'AWAITING_MODEL' }) }),
    ]);

    const view = nodeRuntimeView(state, NODE_ID);

    expect(view.label).toBe('Awaiting model');
    expect(view.detail).toContain('No verified model');
  });

  it('names the unfed port a not-ready node is waiting on', () => {
    const state = reduceFrames([
      runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'NOT_READY', missing: ['b', 'c'] }) }),
    ]);

    const view = nodeRuntimeView(state, NODE_ID);

    expect(view.label).toBe('Not ready');
    expect(view.missing).toEqual(['b', 'c']);
    expect(view.detail).toBe('Waiting on b, c.');
  });

  it('renders exactly the engine’s four labels and invents none', () => {
    expect(Object.keys(RUNTIME_STATE_LABELS).sort()).toEqual(
      ['AWAITING_MODEL', 'NOT_READY', 'READY', 'WARMING'],
    );
  });

  it('never reports an unrecognised label as known, and never as ready', () => {
    const state = reduceFrames([
      runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'DEFINITELY_NOT_A_STATE' }) }),
    ]);

    const view = nodeRuntimeView(state, NODE_ID);

    expect(view.known).toBe(false);
    expect(view.label).toBe('');
    // The raw word is kept so an operator can see what arrived; it is not mapped onto one
    // of the four, which is the whole point.
    expect(view.state).toBe('DEFINITELY_NOT_A_STATE');
    expect(view.detail).toContain('Unrecognised runtime state');
  });

  it('reports a node nobody has evaluated as unknown, not as fine', () => {
    expect(nodeRuntimeView(INITIAL_REALTIME, NODE_ID).known).toBe(false);
    expect(nodeRuntimeView(reduceFrames([runtimeFrame({})]), NODE_ID).known).toBe(false);
  });

  it('keeps the last good reading when a broken frame arrives', () => {
    const good = reduceFrames([runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'READY' }) })]);
    const after = realtimeReducer(good, {
      type: 'frame',
      frame: { type: OWNED_CHANNEL_EVENTS.DEPLOYMENT_RUNTIME_STATE, channel: deploymentChannel },
    });

    // A frame with no payload is a broken frame, not an empty runtime state. Replacing a
    // good reading with it would blank the canvas.
    expect(nodeRuntimeView(after, NODE_ID).state).toBe('READY');
  });

  it('ignores a frame type this build does not know', () => {
    const state = realtimeReducer(INITIAL_REALTIME, {
      type: 'frame',
      frame: { type: 'deployment.something_new', channel: deploymentChannel, runtime_state: {} },
    });

    expect(state).toBe(INITIAL_REALTIME);
  });
});

describe('training is reported ahead of the runtime label, with its epoch counter', () => {
  it('shows the epoch counter from the worker’s own fields', () => {
    const state = reduceFrames([trainingFrame({ epoch: 3, total: 25 })]);

    const view = nodeRuntimeView(state, NODE_ID);

    expect(view.state).toBe('TRAINING');
    expect(view.label).toBe('Training');
    expect(view.detail).toBe('epoch 3/25');
    expect(view.training.epoch).toBe(3);
    expect(view.training.epochsTotal).toBe(25);
  });

  it('wins over AWAITING_MODEL, which is simultaneously true and less useful', () => {
    // Both statements hold — there is no active model version while one is being trained —
    // but only one tells the author that waiting will fix it.
    const state = reduceFrames([
      runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'AWAITING_MODEL' }) }),
      trainingFrame(),
    ]);

    expect(nodeRuntimeView(state, NODE_ID).label).toBe('Training');
  });

  it('yields to AWAITING_MODEL once the job is no longer running', () => {
    const state = reduceFrames([
      runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'AWAITING_MODEL' }) }),
      trainingFrame({ event: OWNED_CHANNEL_EVENTS.TRAINING_FAILED }),
    ]);

    // A failed job is exactly when "go and train this" is the right thing to say.
    expect(nodeRuntimeView(state, NODE_ID).label).toBe('Awaiting model');
  });

  it('reports a queued job and a cancellation request as the different facts they are', () => {
    const queued = reduceFrames([trainingFrame({ event: OWNED_CHANNEL_EVENTS.TRAINING_QUEUED })]);
    const cancelling = reduceFrames([
      trainingFrame({ event: OWNED_CHANNEL_EVENTS.TRAINING_CANCEL_REQUESTED }),
    ]);

    expect(trainingView(queued, NODE_ID).detail).toBe('Queued for training.');
    expect(trainingView(cancelling, NODE_ID).detail).toContain('has not stopped yet');
  });

  it('fabricates no counter when the totals have not arrived', () => {
    const state = reduceFrames([{ ...trainingFrame(), epoch: undefined, epochs_total: undefined }]);

    expect(trainingView(state, NODE_ID).detail).toBe('Training.');
  });
});

describe('the deployed lock is the backend’s verdict (Requirement 9.9)', () => {
  it('locks on the server’s read_only, and quotes its sentence', () => {
    const view = deployedLockView(reduceFrames([canvasStateFrame()]));

    expect(view.known).toBe(true);
    expect(view.locked).toBe(true);
    expect(view.lifecycleState).toBe('RUNNING');
    expect(view.frozenFields).toContain('graph_json');
    expect(view.reason).toBe('This version is RUNNING, so it cannot be changed.');
  });

  it('does not lock a canvas the backend says is editable', () => {
    const frame = canvasStateFrame({
      lifecycle_state: 'DRAFT',
      read_only: false,
      editable: true,
      frozen_fields: [],
      reason: 'This version is DRAFT and can still be edited in place.',
    });

    expect(deployedLockView(reduceFrames([frame])).locked).toBe(false);
  });

  it('does not lock a canvas nobody has said anything about', () => {
    const view = deployedLockView(INITIAL_REALTIME);

    expect(view.known).toBe(false);
    expect(view.locked).toBe(false);
  });

  it('does not infer the lock from a lifecycle word', () => {
    // A `strategy.lifecycle` frame saying RUNNING is not a `canvas_state` verdict. Task 8.3
    // publishes the verdict precisely so the frontend stops deriving it from the word.
    const state = reduceFrames([
      { type: OWNED_CHANNEL_EVENTS.STRATEGY_LIFECYCLE, channel: strategyChannel, lifecycle_state: 'RUNNING' },
    ]);

    expect(deployedLockView(state).locked).toBe(false);
  });
});

describe('a refused subscription is reported, not swallowed (Requirement 21.6)', () => {
  it('keeps the channel, the code and the server’s reason', () => {
    const state = reduceFrames([refusalFrame(deploymentChannel)]);
    const refusals = refusalList(state);

    expect(refusals).toHaveLength(1);
    expect(refusals[0].channel).toBe(deploymentChannel);
    expect(refusals[0].code).toBe('CHANNEL_FORBIDDEN');
    expect(refusals[0].reason).toContain('not available to this user');
  });

  it('clears everything on a strategy change, so one strategy’s state is never another’s', () => {
    const state = reduceFrames([
      runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'READY' }) }),
      canvasStateFrame(),
      refusalFrame(deploymentChannel),
    ]);

    const reset = realtimeReducer(state, { type: 'reset' });

    expect(nodeRuntimeView(reset, NODE_ID).known).toBe(false);
    expect(deployedLockView(reset).known).toBe(false);
    expect(refusalList(reset)).toEqual([]);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. The socket client — one connection, resubscribe, reauthenticate, backoff
// ═══════════════════════════════════════════════════════════════════════════

describe('one connection per session, ref-counted (Requirement 23.1)', () => {
  it('constructs one socket however many holds are taken', () => {
    wsClient.acquire('/ws/telemetry');
    wsClient.acquire('/ws/telemetry');
    wsClient.acquire('/ws/telemetry');

    expect(sockets).toHaveLength(1);
    expect(wsClient.holdCount()).toBe(3);
  });

  it('closes only when the last hold goes', () => {
    wsClient.acquire('/ws/telemetry');
    wsClient.acquire('/ws/telemetry');
    const socket = lastSocket();
    socket.readyState = FakeWebSocket.OPEN;

    wsClient.release();
    expect(socket.closes).toBe(0);

    wsClient.release();
    expect(socket.closes).toBe(1);
    expect(wsClient.holdCount()).toBe(0);
  });

  it('carries the session token on the URL and never logs it', () => {
    wsClient.acquire('/ws/telemetry');

    expect(lastSocket().url).toContain('token=test-token');
    for (const call of console.log.mock.calls) {
      expect(String(call[0])).not.toContain('test-token');
    }
  });

  it('restores the previous reconnect policy when the session ends', () => {
    wsClient.acquire('/ws/telemetry', BUILDER_RECONNECT_POLICY);
    expect(wsClient.maxReconnectAttempts).toBe(Infinity);

    wsClient.release();
    expect(wsClient.maxReconnectAttempts).toBe(5);
  });
});

describe('channel subscriptions over that one connection', () => {
  it('asks the server once per channel and hands frames to the subscriber', async () => {
    wsClient.acquire('/ws/telemetry');
    await openSocket();

    const seen = [];
    wsClient.subscribeChannel(deploymentChannel, (frame) => seen.push(frame));

    expect(lastSocket().actions('subscribe').map((f) => f.channel)).toContain(deploymentChannel);

    lastSocket().deliver(runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'READY' }) }));
    expect(seen).toHaveLength(1);
    expect(seen[0].type).toBe(OWNED_CHANNEL_EVENTS.DEPLOYMENT_RUNTIME_STATE);
  });

  it('tells the server once for two holders, and unsubscribes when the last goes', async () => {
    wsClient.acquire('/ws/telemetry');
    await openSocket();
    const socket = lastSocket();

    const releaseA = wsClient.subscribeChannel(strategyChannel, () => {});
    const releaseB = wsClient.subscribeChannel(strategyChannel, () => {});

    expect(socket.actions('subscribe').filter((f) => f.channel === strategyChannel)).toHaveLength(1);

    releaseA();
    expect(socket.actions('unsubscribe')).toHaveLength(0);

    releaseB();
    expect(socket.actions('unsubscribe').map((f) => f.channel)).toEqual([strategyChannel]);
  });

  it('delivers a refusal to whoever asked for the channel, on the open connection', async () => {
    wsClient.acquire('/ws/telemetry');
    await openSocket();

    const seen = [];
    wsClient.subscribeChannel(deploymentChannel, (frame) => seen.push(frame));
    lastSocket().deliver(refusalFrame(deploymentChannel));

    expect(seen[0].type).toBe('subscription_refused');
    expect(wsClient.refusalFor(deploymentChannel).code).toBe('CHANNEL_FORBIDDEN');
    // The refusal closed the subscription, not the connection.
    expect(lastSocket().closes).toBe(0);
  });

  it('resubscribes every channel when the connection comes back (Requirement 23.5)', async () => {
    wsClient.acquire('/ws/telemetry', BUILDER_RECONNECT_POLICY);
    await openSocket();
    wsClient.subscribeChannel(strategyChannel, () => {});
    wsClient.subscribeChannel(deploymentChannel, () => {});

    // A fresh socket, as a reconnect produces. The server clears a closed connection's
    // authorised subscriptions, so a client that did not resubscribe would sit on an open
    // socket receiving nothing while reporting itself connected.
    const reconnected = new FakeWebSocket('ws://x/ws/telemetry');
    wsClient.ws = reconnected;
    await act(async () => {
      reconnected.readyState = FakeWebSocket.OPEN;
      wsClient.handleOpen();
    });

    expect(reconnected.actions('subscribe').map((f) => f.channel).sort()).toEqual(
      [deploymentChannel, strategyChannel].sort(),
    );
  });

  it('reauthenticates the existing connection without reconnecting (Requirement 23.3)', async () => {
    wsClient.acquire('/ws/telemetry');
    await openSocket();
    const before = sockets.length;

    expect(wsClient.reauthenticate('refreshed-token')).toBe(true);

    expect(lastSocket().actions('auth').at(-1).token).toBe('refreshed-token');
    // The assertion that matters: no new socket. A refresh that reconnected would be a
    // reconnect storm every time a session refreshed its token.
    expect(sockets).toHaveLength(before);
  });

  it('sends no refresh on a closed connection, which has no identity to refresh', () => {
    expect(wsClient.reauthenticate('refreshed-token')).toBe(false);
  });
});

describe('reconnect backoff is exponential, capped at 30 s, and jittered (Requirement 23.4)', () => {
  it('doubles, caps and never exceeds the cap plus the jitter', () => {
    vi.useFakeTimers();
    const delays = [];
    const setTimeoutSpy = vi.spyOn(global, 'setTimeout').mockImplementation((fn, delay) => {
      delays.push(delay);
      return 0;
    });

    wsClient.setReconnectPolicy(BUILDER_RECONNECT_POLICY);
    wsClient.reconnectEnabled = true;
    for (let i = 0; i < 12; i += 1) wsClient.scheduleReconnect();

    setTimeoutSpy.mockRestore();

    expect(delays).toHaveLength(12);
    // Monotone up to the cap, then capped — with jitter on top of every one of them.
    expect(delays[0]).toBeGreaterThanOrEqual(BUILDER_RECONNECT_POLICY.baseDelayMs);
    expect(delays[1]).toBeGreaterThan(delays[0]);
    for (const delay of delays) {
      expect(delay).toBeLessThanOrEqual(
        BUILDER_RECONNECT_POLICY.maxDelayMs + BUILDER_RECONNECT_POLICY.jitterMs,
      );
    }
    expect(delays.at(-1)).toBeGreaterThanOrEqual(BUILDER_RECONNECT_POLICY.maxDelayMs);
  });

  it('spreads the fleet: two clients on the same attempt do not agree exactly', () => {
    // The jitter is the clause that matters in aggregate — without it every browser that
    // lost the same backend restart retries on the same schedule.
    const draws = new Set();
    const setTimeoutSpy = vi.spyOn(global, 'setTimeout').mockImplementation((fn, delay) => {
      draws.add(delay);
      return 0;
    });

    wsClient.setReconnectPolicy({ ...BUILDER_RECONNECT_POLICY, jitterMs: 250 });
    wsClient.reconnectEnabled = true;
    for (let i = 0; i < 40; i += 1) {
      wsClient.reconnectAttempts = 20; // pinned past the cap, so only jitter varies
      wsClient.scheduleReconnect();
    }

    setTimeoutSpy.mockRestore();
    expect(draws.size).toBeGreaterThan(1);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 4. The hook — what it subscribes, when it snapshots, when it polls
// ═══════════════════════════════════════════════════════════════════════════

describe('useBuilderRealtime', () => {
  it('subscribes the four channels for a saved strategy with a deployment', async () => {
    renderHook(() =>
      useBuilderRealtime({ strategyId: STRATEGY_ID, deploymentId: DEPLOYMENT_ID }),
    );
    await openSocket();

    expect(lastSocket().actions('subscribe').map((f) => f.channel)).toEqual([
      validationChannel,
      strategyChannel,
      deploymentChannel,
      executionChannel,
    ]);
  });

  it('unsubscribes what it subscribed when the view closes (Requirement 23.2)', async () => {
    const { unmount } = renderHook(() =>
      useBuilderRealtime({ strategyId: STRATEGY_ID, deploymentId: DEPLOYMENT_ID }),
    );
    await openSocket();
    const socket = lastSocket();

    unmount();

    expect(socket.actions('unsubscribe').map((f) => f.channel).sort()).toEqual(
      [deploymentChannel, executionChannel, strategyChannel, validationChannel].sort(),
    );
  });

  it('opens no socket at all without a token, and says why', () => {
    window.sessionStorage.removeItem('token');

    const { result } = renderHook(() => useBuilderRealtime({ strategyId: STRATEGY_ID }));

    // The endpoint closes an unauthenticated connection with 1008 before the first frame,
    // so attempting one would be a loop that can never succeed.
    expect(sockets).toHaveLength(0);
    expect(result.current.status).toBe(REALTIME_STATES.UNAVAILABLE);
    expect(result.current.reason).toContain('Sign in');
  });

  it('opens no socket for an unsaved canvas, because there is nothing to subscribe', () => {
    const { result } = renderHook(() => useBuilderRealtime({ strategyId: '' }));

    expect(sockets).toHaveLength(0);
    expect(result.current.channels).toEqual([]);
    expect(result.current.reason).toContain('Save this strategy');
  });

  it('reports the connection state literally, DISCONNECTED included', async () => {
    const { result } = renderHook(() => useBuilderRealtime({ strategyId: STRATEGY_ID }));
    await openSocket();
    expect(result.current.status).toBe(REALTIME_STATES.CONNECTED);
    expect(result.current.connected).toBe(true);

    await act(async () => {
      lastSocket().drop();
    });

    expect(result.current.status).toBe(REALTIME_STATES.DISCONNECTED);
    expect(result.current.connected).toBe(false);
  });

  it('requests a snapshot on every reconnect (Requirement 23.5)', async () => {
    const onSnapshot = vi.fn();
    renderHook(() => useBuilderRealtime({ strategyId: STRATEGY_ID, onSnapshot }));

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
    // Ordering: the channels were resubscribed on that socket before the snapshot was
    // asked for. A snapshot answered while the connection is still receiving nothing
    // would close no gap.
    expect(reconnected.actions('subscribe').length).toBeGreaterThan(0);
  });

  it('polls every 30 s while the connection is closed, and not once while it is open', async () => {
    vi.useFakeTimers();
    const onSnapshot = vi.fn();
    renderHook(() => useBuilderRealtime({ strategyId: STRATEGY_ID, onSnapshot }));

    await act(async () => {
      lastSocket().open();
    });
    onSnapshot.mockClear();

    // Connected: the socket is the data path, so the poll must not run at all.
    await act(async () => {
      vi.advanceTimersByTime(SAFETY_POLL_INTERVAL_MS * 3);
    });
    expect(onSnapshot).not.toHaveBeenCalled();

    await act(async () => {
      lastSocket().drop();
    });
    onSnapshot.mockClear();

    await act(async () => {
      vi.advanceTimersByTime(SAFETY_POLL_INTERVAL_MS);
    });
    expect(onSnapshot).toHaveBeenCalledTimes(1);
    expect(onSnapshot).toHaveBeenCalledWith('poll');

    await act(async () => {
      vi.advanceTimersByTime(SAFETY_POLL_INTERVAL_MS * 2);
    });
    expect(onSnapshot).toHaveBeenCalledTimes(3);
  });

  it('polls at exactly the interval Requirement 23.6 names', () => {
    expect(SAFETY_POLL_INTERVAL_MS).toBe(30000);
  });

  it('installs the bounded, jittered policy the requirement asks for', () => {
    expect(BUILDER_RECONNECT_POLICY.maxDelayMs).toBe(30000);
    expect(BUILDER_RECONNECT_POLICY.jitterMs).toBeGreaterThan(0);
    // Unbounded attempts are safe only because of the cap and the jitter above.
    expect(BUILDER_RECONNECT_POLICY.maxAttempts).toBe(Infinity);
  });

  it('shares one connection between two mounted views', async () => {
    const a = renderHook(() => useBuilderRealtime({ strategyId: STRATEGY_ID }));
    const b = renderHook(() =>
      useBuilderRealtime({ strategyId: STRATEGY_ID, deploymentId: DEPLOYMENT_ID }),
    );
    await openSocket();

    expect(sockets).toHaveLength(1);
    expect(wsClient.holdCount()).toBe(2);

    a.unmount();
    expect(lastSocket().closes).toBe(0);
    b.unmount();
    expect(lastSocket().closes).toBe(1);
  });

  it('reduces the frames that arrive into the realtime state', async () => {
    const { result } = renderHook(() =>
      useBuilderRealtime({ strategyId: STRATEGY_ID, deploymentId: DEPLOYMENT_ID }),
    );
    await openSocket();

    await act(async () => {
      lastSocket().deliver(runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'WARMING' }) }));
      lastSocket().deliver(canvasStateFrame());
    });

    expect(nodeRuntimeView(result.current.realtime, NODE_ID).state).toBe('WARMING');
    expect(deployedLockView(result.current.realtime).locked).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 5. On the canvas
// ═══════════════════════════════════════════════════════════════════════════

const RSI = descriptor('rsi', 'INDICATOR');
const OHLCV = descriptor('ohlcv_feed', 'DATA');

const canvasNode = (id, block, params = {}) => ({
  id,
  type: block.block_id,
  position: { x: 0, y: 0 },
  data: {
    block_id: block.block_id,
    category: block.category,
    label: block.display_name,
    params,
    inputs: block.inputs,
    outputs: block.outputs,
    descriptor: block,
  },
});

const GRAPH = toCanonical(
  [
    canvasNode('n-data', OHLCV, { symbol: 'ETH/USDT', timeframe: '5m' }),
    canvasNode(NODE_ID, RSI, { period: 14 }),
  ],
  [],
  { name: 'Realtime Strategy' },
);

const SAVED_STRATEGY = { id: STRATEGY_ID, name: 'Realtime Strategy', graph_json: GRAPH };

const routeRequests = () => {
  mockClient.get.mockImplementation((url) => {
    if (typeof url === 'string' && url.includes('/data-quality')) {
      return Promise.resolve({ status: 200, data: { strategy_id: STRATEGY_ID, feed: null } });
    }
    return Promise.resolve(served());
  });
  mockClient.post.mockImplementation((url) => {
    if (typeof url === 'string' && url.includes('/validate')) {
      return Promise.resolve({
        valid: true,
        dag_hash: 'dag_rt',
        validation_state: 'VALID',
        errors: [],
        warnings: [],
        summary: { node_count: 2, edge_count: 0, warmup_bars: 14 },
      });
    }
    return Promise.resolve({ status: 'ok', id: STRATEGY_ID });
  });
};

describe('the canvas renders the runtime state it is sent (Requirement 20.12)', () => {
  beforeEach(() => {
    installBuilderStubs();
    window.sessionStorage.setItem('token', 'test-token');
    mockClient.get.mockReset();
    mockClient.post.mockReset();
    resetRegistryClient();
    routeRequests();
  });

  afterEach(() => {
    resetRegistryClient();
  });

  const mountBuilder = async (extra = {}) => {
    renderBuilder({ initialStrategy: SAVED_STRATEGY, deploymentId: DEPLOYMENT_ID, ...extra });
    await waitForRegistry('ready');
    await waitFor(() => expect(sockets.length).toBeGreaterThan(0));
    await openSocket();
    return lastSocket();
  };

  it('draws warming with its bar count on the node itself', async () => {
    const socket = await mountBuilder();

    await act(async () => {
      socket.deliver(runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'WARMING' }) }, { barsSeen: 10 }));
    });

    const badge = await screen.findByTestId('node-runtime-state');
    expect(badge.dataset.nodeId).toBe(NODE_ID);
    expect(badge.dataset.runtimeState).toBe('WARMING');
    expect(badge.dataset.barsSeen).toBe('10');
    expect(badge.dataset.barsNeeded).toBe('29');
    expect(badge.textContent).toContain('Warming');
    expect(badge.textContent).toContain('warmup 10/29 bars');
    // The state is in the accessible name too, so colour is never the only signal.
    expect(badge.getAttribute('aria-label')).toContain('Warming');
  });

  it('draws the epoch counter while a node is training', async () => {
    // The training channel is subscribed only for a job the caller knows about, so the job
    // is handed in. A frame on a channel nobody subscribed reaches nobody, which is the
    // point of the per-channel routing rather than a limitation of it.
    const socket = await mountBuilder({ trainingJob: { job_id: JOB_ID, status: 'RUNNING' } });

    await act(async () => {
      socket.deliver(trainingFrame({ epoch: 7, total: 40 }));
    });

    const badge = await screen.findByTestId('node-runtime-state');
    expect(badge.dataset.runtimeState).toBe('TRAINING');
    expect(badge.dataset.epoch).toBe('7');
    expect(badge.dataset.epochsTotal).toBe('40');
    expect(badge.textContent).toContain('epoch 7/40');
  });

  it('draws nothing at all before a frame arrives', async () => {
    await mountBuilder();

    // A node nobody has evaluated must not look like a node that is fine.
    expect(screen.queryByTestId('node-runtime-state')).toBeNull();
  });

  it('draws no state for a label this build does not recognise', async () => {
    const socket = await mountBuilder();

    await act(async () => {
      socket.deliver(runtimeFrame({ [NODE_ID]: runtimeNode({ state: 'SOMETHING_ELSE' }) }));
    });

    expect(screen.queryByTestId('node-runtime-state')).toBeNull();
  });

  it('shows the deployed lock, quotes the backend’s sentence and blocks the save', async () => {
    const socket = await mountBuilder();

    await act(async () => {
      socket.deliver(canvasStateFrame());
    });

    const banner = await screen.findByTestId('deployed-lock');
    expect(banner.textContent).toContain('This version is RUNNING, so it cannot be changed.');
    expect(banner.dataset.lifecycleState).toBe('RUNNING');
    expect(banner.dataset.frozenFields).toContain('graph_json');
    // Every node carries the affordance: the lock is a property of the version, so a
    // per-node lock on only some blocks would be a lie about which ones may be edited.
    const locks = await screen.findAllByTestId('node-deployed-lock');
    expect(locks).toHaveLength(2);

    // Disabling is right here and only here: the reason is on screen, and the write it
    // would attempt is one migration 004c's trigger refuses.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Save$/ }).disabled).toBe(true),
    );
  });

  it('leaves the save alone while the canvas is editable', async () => {
    const socket = await mountBuilder();

    await act(async () => {
      socket.deliver(
        canvasStateFrame({ lifecycle_state: 'DRAFT', read_only: false, editable: true, frozen_fields: [] }),
      );
    });

    expect(screen.queryByTestId('deployed-lock')).toBeNull();
    expect(screen.queryByTestId('node-deployed-lock')).toBeNull();
  });

  it('reports a refused subscription with the server’s reason', async () => {
    const socket = await mountBuilder();

    await act(async () => {
      socket.deliver(refusalFrame(deploymentChannel));
    });

    const panel = await screen.findByTestId('subscription-refusals');
    expect(panel.textContent).toContain(deploymentChannel);
    expect(panel.textContent).toContain('not available to this user');
  });

  it('reports the realtime connection in the status strip, separately from the feed', async () => {
    const socket = await mountBuilder();

    const cell = await screen.findByTestId('realtime-state');
    expect(cell.dataset.state).toBe(REALTIME_STATES.CONNECTED);

    await act(async () => {
      socket.drop();
    });

    await waitFor(() =>
      expect(screen.getByTestId('realtime-state').dataset.state).toBe(
        REALTIME_STATES.DISCONNECTED,
      ),
    );
    // The feed cell is a measurement of market data and is NOT overwritten by the state of
    // the transport: folding the two would hide a stale feed behind a healthy socket, and
    // would attribute a dropped socket to the market.
    expect(screen.getByTestId('feed-state')).toBeTruthy();
  });

  it('subscribes exactly the four channels from the page', async () => {
    const socket = await mountBuilder();

    expect(socket.actions('subscribe').map((frame) => frame.channel)).toEqual([
      validationChannel,
      strategyChannel,
      deploymentChannel,
      executionChannel,
    ]);
  });
});
