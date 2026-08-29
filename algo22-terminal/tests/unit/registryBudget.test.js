/**
 * "Fetched once per session" — the client half of the registry budget.
 *
 * Spec: strategy-builder task 9.5. Requirement 4.15 and `design.md` -> Performance:
 * "Registry payload | < 250 KB gzipped, cached by `ETag` | **fetched once per session**".
 *
 * The size half of that budget is a server property and is measured from the served bytes in
 * `tests/test_task_9_5_registry_budget.py` (15 863 gzipped bytes for `/registry/blocks`, 6.2%
 * of the 250 KB ceiling). The *frequency* half is a client property: no server header can stop
 * a client from asking twelve times, and a `Cache-Control` of `private, max-age=0,
 * must-revalidate` deliberately does not try to — it asks for revalidation every time and
 * lets the `ETag` make revalidation free.
 *
 * So the assertion belongs here, against the module that actually implements it. Nothing is
 * reimplemented: `src/lib/registryClient.js` has done conditional GET with a
 * single-in-flight rule since task 3.5, task 7.3 extended it to the timeframe set through the
 * shared `conditionalGet`, and task 8.5 is the seam both resources return through. This file
 * asserts what that seam adds up to over a whole session, which
 * `tests/unit/registryClient.test.js` does not: that file proves each mechanism works
 * (a 304 is a success, concurrent callers share one promise, an error drops the cache), one
 * mechanism per test. What it never asserts is the total.
 *
 * What is actually asserted
 * -------------------------
 * * **A session's worth of callers costs one request per resource.** Twelve palette,
 *   inspector, compatibility and timeframe reads in the order a real session makes them, and
 *   the transport is entered exactly twice — once for `/registry/blocks`, once for
 *   `/registry/timeframes`.
 * * **The count is per resource, not shared.** A held block registry does not satisfy a
 *   timeframe read, and the two in-flight promises are independent, because one resource's
 *   outage must not take the other down (which is why there are two caches at all).
 * * **An explicit revalidation transfers no payload.** `refreshRegistry()` sends the held
 *   `ETag` as `If-None-Match`, the `304` carries no body, and the payload the palette renders
 *   is the same frozen object it already had — asserted by identity, not by deep equality.
 * * **`If-None-Match` is only sent when a copy is actually held**, so a cold client cannot
 *   ask a question it has no answer to and get a `304` it cannot render.
 * * **The session has an end.** `resetRegistryClient()` — what logout calls — makes the next
 *   read a real fetch that sends no validator. "Once per session" is a session, not forever.
 * * **One transport for both resources**, so a third resource added later inherits the rule
 *   instead of reimplementing it: both requests carry the same `Accept`, the same
 *   `validateStatus` that treats `304` as success, and go through `client.get`.
 *
 * The shared axios instance is stubbed because it is the network boundary. Everything
 * asserted is the module's own logic; nothing about the count is faked, because the count is
 * exactly what is being measured.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const { mockClient } = vi.hoisted(() => ({ mockClient: { get: vi.fn() } }));

vi.mock('../../src/apiClient', () => ({ default: mockClient }));

import {
  REGISTRY_BLOCKS_PATH,
  REGISTRY_TIMEFRAMES_PATH,
  REGISTRY_STATES,
  getBlocksByCategory,
  getCategories,
  getCompatibilityMatrix,
  getDescriptor,
  getPaletteSections,
  getPortTypes,
  getRegistrySnapshot,
  getRegistryVersion,
  getTimeframeSeconds,
  getTimeframeSnapshot,
  getTimeframes,
  loadRegistry,
  loadTimeframes,
  refreshRegistry,
  refreshTimeframes,
  resetRegistryClient,
} from '../../src/lib/registryClient';

import { PORT_TYPES } from '../../src/lib/canonicalGraph';

// ---------------------------------------------------------------------------
// Fixtures — the real wire shape, minimal but valid
// ---------------------------------------------------------------------------

const CATEGORIES = [
  { id: 'DATA', display_name: 'Market Data', order: 1 },
  { id: 'INDICATOR', display_name: 'Indicators', order: 2 },
  { id: 'MATH', display_name: 'Math', order: 3 },
  { id: 'LOGIC', display_name: 'Logic', order: 4 },
  { id: 'FEATURE_ENGINEERING', display_name: 'Feature Engineering', order: 5 },
  { id: 'ML_DL', display_name: 'ML / DL Models', order: 6 },
  { id: 'ACTION', display_name: 'Actions', order: 7 },
];

const descriptor = (blockId, category) => ({
  block_id: blockId,
  display_name: blockId.toUpperCase(),
  category,
  inputs: [],
  outputs: [],
  params: [],
});

const BLOCKS = [
  descriptor('ohlcv_feed', 'DATA'),
  descriptor('ema', 'INDICATOR'),
  descriptor('add', 'MATH'),
  descriptor('gt', 'LOGIC'),
  descriptor('rolling_zscore', 'FEATURE_ENGINEERING'),
  descriptor('catboost', 'ML_DL'),
  descriptor('action_buy_market', 'ACTION'),
];

const BLOCKS_ETAG = '"r_4f19c2a8-blocks"';
const TIMEFRAMES_ETAG = '"r_4f19c2a8-timeframes"';

const blocksPayload = () => ({
  registry_version: 'r_4f19c2a8',
  registry_schema_version: 1,
  port_types: [...PORT_TYPES],
  categories: CATEGORIES.map((category) => ({ ...category })),
  blocks: BLOCKS.map((block) => ({ ...block })),
  compatibility_matrix: Object.fromEntries(PORT_TYPES.map((type) => [type, [type]])),
});

const timeframesPayload = () => ({
  registry_version: 'r_4f19c2a8',
  registry_schema_version: 1,
  timeframes: [
    { id: '5m', label: '5m', seconds: 300 },
    { id: '1h', label: '1h', seconds: 3600 },
    { id: '1d', label: '1d', seconds: 86400 },
  ],
  sources: ['backend_app.backend.master_executor.TF_SEC'],
  total: 3,
});

const ok = (path) =>
  path === REGISTRY_TIMEFRAMES_PATH
    ? { status: 200, data: timeframesPayload(), headers: { etag: TIMEFRAMES_ETAG } }
    : { status: 200, data: blocksPayload(), headers: { etag: BLOCKS_ETAG } };

/** A 304 carries the validator and **no body**. That is the whole point of the budget. */
const notModified = (path) => ({
  status: 304,
  data: '',
  headers: { etag: path === REGISTRY_TIMEFRAMES_PATH ? TIMEFRAMES_ETAG : BLOCKS_ETAG },
});

/** Answer whichever resource is asked for, so call order never matters to a fixture. */
const serveByPath = () => {
  mockClient.get.mockImplementation((path) => Promise.resolve(ok(path)));
};

const requestsTo = (path) => mockClient.get.mock.calls.filter(([url]) => url === path);

const configFor = (path) => {
  const call = requestsTo(path).at(-1);
  expect(call, `no request was made to ${path}`).toBeTruthy();
  return call[1];
};

beforeEach(() => {
  mockClient.get.mockReset();
  resetRegistryClient();
});

afterEach(() => {
  resetRegistryClient();
});

// ---------------------------------------------------------------------------
// 1. A session's worth of reads costs one request per resource
// ---------------------------------------------------------------------------

describe('registry budget: fetched once per session', () => {
  it('serves a whole session of palette and inspector reads from one fetch', async () => {
    serveByPath();

    // The order a real session makes these in: the canvas mounts and asks for the palette,
    // the inspector resolves a descriptor per selection, a connect attempt consults the
    // compatibility matrix, a category collapses and expands.
    await loadRegistry();
    getPaletteSections();
    getCategories();
    getBlocksByCategory('INDICATOR');
    getDescriptor('ema');
    getCompatibilityMatrix();
    getPortTypes();
    getRegistryVersion();
    await loadRegistry();
    getDescriptor('catboost');
    getBlocksByCategory('FEATURE_ENGINEERING');
    await loadRegistry();

    expect(requestsTo(REGISTRY_BLOCKS_PATH)).toHaveLength(1);
    expect(mockClient.get).toHaveBeenCalledTimes(1);
    expect(getRegistrySnapshot().state).toBe(REGISTRY_STATES.READY);
    expect(getPaletteSections()).toHaveLength(CATEGORIES.length);
  });

  it('costs exactly one request per resource across a session that uses both', async () => {
    serveByPath();

    await loadRegistry();
    await loadTimeframes();
    getDescriptor('ohlcv_feed');
    expect(getTimeframeSeconds('1h')).toBe(3600);
    await loadRegistry();
    await loadTimeframes();
    getTimeframes();
    await Promise.all([loadRegistry(), loadTimeframes(), loadRegistry()]);

    expect(requestsTo(REGISTRY_BLOCKS_PATH)).toHaveLength(1);
    expect(requestsTo(REGISTRY_TIMEFRAMES_PATH)).toHaveLength(1);
    expect(mockClient.get).toHaveBeenCalledTimes(2);
  });

  it('does not let a held block registry satisfy a timeframe read', async () => {
    serveByPath();

    await loadRegistry();
    expect(getTimeframeSnapshot().state).toBe(REGISTRY_STATES.IDLE);
    expect(getTimeframes()).toEqual([]);

    await loadTimeframes();

    expect(requestsTo(REGISTRY_TIMEFRAMES_PATH)).toHaveLength(1);
    expect(getTimeframes()).toHaveLength(3);
  });

  it('collapses many simultaneous callers of each resource into one request each', async () => {
    const resolvers = {};
    mockClient.get.mockImplementation(
      (path) =>
        new Promise((resolve) => {
          resolvers[path] = () => resolve(ok(path));
        }),
    );

    const pending = [
      loadRegistry(),
      loadRegistry(),
      loadTimeframes(),
      loadRegistry(),
      loadTimeframes(),
    ];

    expect(mockClient.get).toHaveBeenCalledTimes(2);
    resolvers[REGISTRY_BLOCKS_PATH]();
    resolvers[REGISTRY_TIMEFRAMES_PATH]();
    const snapshots = await Promise.all(pending);

    expect(mockClient.get).toHaveBeenCalledTimes(2);
    expect(snapshots.every((snapshot) => snapshot.state === REGISTRY_STATES.READY)).toBe(true);
    // The three block callers received one and the same snapshot object.
    expect(new Set([snapshots[0], snapshots[1], snapshots[3]]).size).toBe(1);
    expect(new Set([snapshots[2], snapshots[4]]).size).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// 2. Revalidation transfers no payload
// ---------------------------------------------------------------------------

describe('registry budget: revalidation is free', () => {
  it('sends the held ETag and reuses the very same payload object on a 304', async () => {
    mockClient.get
      .mockImplementationOnce((path) => Promise.resolve(ok(path)))
      .mockImplementationOnce((path) => Promise.resolve(notModified(path)));

    const first = await loadRegistry();
    const held = first.blocks;

    const revalidated = await refreshRegistry();

    expect(requestsTo(REGISTRY_BLOCKS_PATH)).toHaveLength(2);
    expect(configFor(REGISTRY_BLOCKS_PATH).headers['If-None-Match']).toBe(BLOCKS_ETAG);
    expect(revalidated.state).toBe(REGISTRY_STATES.READY);
    expect(revalidated.fromCache).toBe(true);
    // Identity, not deep equality: a 304 must not have rebuilt anything from a body it
    // never received.
    expect(revalidated.blocks).toBe(held);
    expect(revalidated.registryVersion).toBe(first.registryVersion);
  });

  it('reuses the timeframe set on a 304 through the same rule', async () => {
    mockClient.get
      .mockImplementationOnce((path) => Promise.resolve(ok(path)))
      .mockImplementationOnce((path) => Promise.resolve(notModified(path)));

    const first = await loadTimeframes();
    const revalidated = await refreshTimeframes();

    expect(configFor(REGISTRY_TIMEFRAMES_PATH).headers['If-None-Match']).toBe(TIMEFRAMES_ETAG);
    expect(revalidated.fromCache).toBe(true);
    expect(revalidated.timeframes).toBe(first.timeframes);
  });

  it('never sends If-None-Match before a copy is held', async () => {
    serveByPath();

    await loadRegistry();
    await loadTimeframes();

    expect(requestsTo(REGISTRY_BLOCKS_PATH)[0][1].headers['If-None-Match']).toBeUndefined();
    expect(requestsTo(REGISTRY_TIMEFRAMES_PATH)[0][1].headers['If-None-Match']).toBeUndefined();
  });

  it('drops the validator with the payload after a failure, so a retry is a real fetch', async () => {
    mockClient.get
      .mockImplementationOnce((path) => Promise.resolve(ok(path)))
      .mockImplementationOnce(() => {
        const error = new Error('Request failed with status code 500');
        error.status = 500;
        error.response = { status: 500, data: { error: 'boom' } };
        return Promise.reject(error);
      })
      .mockImplementationOnce((path) => Promise.resolve(ok(path)));

    await loadRegistry();
    const failed = await refreshRegistry();
    expect(failed.state).toBe(REGISTRY_STATES.ERROR);
    expect(failed.blocks).toEqual([]);

    const recovered = await loadRegistry();

    expect(recovered.state).toBe(REGISTRY_STATES.READY);
    // A stale validator would have invited a 304 for a payload this client no longer holds.
    expect(configFor(REGISTRY_BLOCKS_PATH).headers['If-None-Match']).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// 3. The session has an end
// ---------------------------------------------------------------------------

describe('registry budget: the session boundary', () => {
  it('re-fetches after the session is reset, with no validator carried over', async () => {
    serveByPath();

    await loadRegistry();
    await loadTimeframes();
    expect(mockClient.get).toHaveBeenCalledTimes(2);

    // What logout calls: one session's registry must not outlive it.
    resetRegistryClient();

    expect(getRegistrySnapshot().state).toBe(REGISTRY_STATES.IDLE);
    expect(getRegistrySnapshot().blocks).toEqual([]);
    expect(getTimeframeSnapshot().timeframes).toEqual([]);

    await loadRegistry();
    await loadTimeframes();

    expect(mockClient.get).toHaveBeenCalledTimes(4);
    expect(configFor(REGISTRY_BLOCKS_PATH).headers['If-None-Match']).toBeUndefined();
    expect(configFor(REGISTRY_TIMEFRAMES_PATH).headers['If-None-Match']).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// 4. One transport, so a third resource inherits the rule
// ---------------------------------------------------------------------------

describe('registry budget: one conditional GET for every resource', () => {
  it('sends both resources through the same request contract', async () => {
    serveByPath();

    await loadRegistry();
    await loadTimeframes();

    for (const path of [REGISTRY_BLOCKS_PATH, REGISTRY_TIMEFRAMES_PATH]) {
      const config = configFor(path);
      expect(config.headers.Accept).toBe('application/json');
      // A 304 is a success. Axios rejects it by default, which would turn every cache hit
      // in the session into a palette error and defeat the whole budget.
      expect(config.validateStatus(200)).toBe(true);
      expect(config.validateStatus(304)).toBe(true);
      expect(config.validateStatus(500)).toBe(false);
      expect(config.validateStatus(401)).toBe(false);
    }
  });

  it('asks the two documented paths and nothing else', async () => {
    serveByPath();

    await loadRegistry();
    await loadTimeframes();
    await refreshRegistry().catch(() => {});

    const paths = new Set(mockClient.get.mock.calls.map(([url]) => url));
    expect([...paths].sort()).toEqual(
      [REGISTRY_BLOCKS_PATH, REGISTRY_TIMEFRAMES_PATH].sort(),
    );
    expect(REGISTRY_BLOCKS_PATH).toBe('/api/strategy-operations/registry/blocks');
    expect(REGISTRY_TIMEFRAMES_PATH).toBe('/api/strategy-operations/registry/timeframes');
  });

  it('reads no token and forwards no credential of its own', async () => {
    serveByPath();

    await loadRegistry();
    await loadTimeframes();

    for (const [, config] of mockClient.get.mock.calls) {
      const headerNames = Object.keys(config.headers).map((name) => name.toLowerCase());
      expect(headerNames).not.toContain('authorization');
      expect(headerNames).not.toContain('apikey');
    }
  });
});
