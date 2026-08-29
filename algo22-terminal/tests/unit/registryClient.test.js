/**
 * Unit tests for the backend-authoritative registry client (src/lib/registryClient.js) and
 * for what is left of src/lib/blockRegistry.js.
 *
 * The contract under test is fail-closed behaviour (Requirement 4.12). SB-03 and SB-04 were
 * caused by a hardcoded local block list drifting from the engine, so the rule is: on any
 * registry failure the client reports an error and **zero** blocks, and never a local,
 * bundled or stale-forever substitute.
 *
 * The shared axios instance (`src/apiClient`) is stubbed because it is the network boundary;
 * everything the tests assert — ETag revalidation, 304 reuse, payload validation, state
 * transitions, in-flight de-duplication, error classification — is this module's own real
 * logic, exercised through that boundary. The payload fixture mirrors the shape asserted by
 * `tests/test_registry_endpoints.py` and produced by `BlockRegistry.to_dict()`.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const { mockClient } = vi.hoisted(() => ({ mockClient: { get: vi.fn() } }));

vi.mock('../../src/apiClient', () => ({ default: mockClient }));

import {
  REGISTRY_BLOCKS_PATH,
  REGISTRY_STATES,
  RegistryError,
  getBlocksByCategory,
  getCategories,
  getCompatibilityMatrix,
  getDescriptor,
  getPaletteSections,
  getPortTypes,
  getRegistrySnapshot,
  getRegistryVersion,
  loadRegistry,
  refreshRegistry,
  resetRegistryClient,
  subscribe,
} from '../../src/lib/registryClient';

import * as blockRegistry from '../../src/lib/blockRegistry';
import { PORT_TYPES } from '../../src/lib/canonicalGraph';

// ---------------------------------------------------------------------------
// Fixture: the real wire shape, all seven categories, ordered
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

const descriptor = (blockId, category, extra = {}) => ({
  block_id: blockId,
  display_name: blockId.toUpperCase(),
  category,
  description: `${blockId} in ${category}`,
  inputs: [],
  outputs: [],
  params: [],
  allowed_predecessor_categories: [],
  allowed_successor_categories: [],
  execution_semantics: 'STATELESS',
  runtime_ref: `module:${blockId}`,
  ...extra,
});

const COMPATIBILITY_MATRIX = {
  OHLCV_FRAME: ['OHLCV_FRAME', 'PRICE_SERIES'],
  PRICE_SERIES: ['PRICE_SERIES', 'SCALAR_SERIES'],
  SCALAR_SERIES: ['SCALAR_SERIES'],
  BOOLEAN_SERIES: ['BOOLEAN_SERIES', 'SIGNAL'],
  FEATURE_MATRIX: ['FEATURE_MATRIX'],
  PREDICTION: ['PREDICTION', 'SCALAR_SERIES'],
  SIGNAL: ['SIGNAL', 'TRADE_INTENT'],
  TRADE_INTENT: [],
  SCALAR: ['SCALAR', 'SCALAR_SERIES'],
};

/** Blocks in category order, including the four SB-04 named blocks. */
const BLOCKS = [
  descriptor('ohlcv_feed', 'DATA', {
    outputs: [{ port: 'candles', type: 'OHLCV_FRAME' }],
    params: [{ key: 'symbol', type: 'STRING', required: true, default: null }],
  }),
  descriptor('sma', 'INDICATOR', {
    inputs: [{ port: 'series', type: 'PRICE_SERIES', required: true }],
    outputs: [{ port: 'value', type: 'SCALAR_SERIES' }],
  }),
  descriptor('wma', 'INDICATOR'),
  descriptor('hma', 'INDICATOR'),
  descriptor('add', 'MATH'),
  descriptor('compare', 'LOGIC'),
  descriptor('rolling_zscore', 'FEATURE_ENGINEERING'),
  descriptor('rolling_mean', 'FEATURE_ENGINEERING'),
  descriptor('catboost', 'ML_DL'),
  descriptor('autoencoder', 'ML_DL'),
  descriptor('market_buy', 'ACTION'),
];

const payload = (overrides = {}) => ({
  registry_version: 'r_4f19c2a8',
  registry_schema_version: 1,
  port_types: [...PORT_TYPES],
  categories: CATEGORIES.map((category) => ({ ...category })),
  blocks: BLOCKS.map((block) => ({ ...block })),
  compatibility_matrix: { ...COMPATIBILITY_MATRIX },
  ...overrides,
});

const ETAG = '"r_4f19c2a8-blocks"';

const ok = (body = payload(), etag = ETAG) => ({
  status: 200,
  data: body,
  headers: { etag },
});

const notModified = (etag = ETAG) => ({ status: 304, data: '', headers: { etag } });

/** An axios-style rejection, shaped like the ApiError the shared client raises. */
const httpFailure = (status, data = { error: 'boom' }) => {
  const error = new Error(`Request failed with status code ${status}`);
  error.status = status;
  error.response = { status, data };
  error.data = data;
  return error;
};

const lastRequestConfig = () => mockClient.get.mock.calls.at(-1)[1];

beforeEach(() => {
  mockClient.get.mockReset();
  resetRegistryClient();
});

afterEach(() => {
  resetRegistryClient();
});

// ---------------------------------------------------------------------------
// 1. A successful fetch
// ---------------------------------------------------------------------------

describe('registryClient: a successful fetch', () => {
  it('requests the live registry endpoint and reaches the ready state', async () => {
    mockClient.get.mockResolvedValueOnce(ok());

    const snapshot = await loadRegistry();

    expect(mockClient.get).toHaveBeenCalledTimes(1);
    expect(mockClient.get.mock.calls[0][0]).toBe('/api/strategy-operations/registry/blocks');
    expect(REGISTRY_BLOCKS_PATH).toBe('/api/strategy-operations/registry/blocks');
    expect(snapshot.state).toBe(REGISTRY_STATES.READY);
    expect(snapshot.isReady).toBe(true);
    expect(snapshot.error).toBeNull();
  });

  it('exposes all seven categories in the served order', async () => {
    mockClient.get.mockResolvedValueOnce(ok());
    await loadRegistry();

    expect(getCategories().map((category) => category.id)).toEqual([
      'DATA',
      'INDICATOR',
      'MATH',
      'LOGIC',
      'FEATURE_ENGINEERING',
      'ML_DL',
      'ACTION',
    ]);
    expect(getCategories().map((category) => category.order)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it('orders the palette by categories[].order even when the payload is shuffled', async () => {
    const shuffled = payload({ categories: [...CATEGORIES].reverse().map((c) => ({ ...c })) });
    mockClient.get.mockResolvedValueOnce(ok(shuffled));
    await loadRegistry();

    const sections = getPaletteSections();
    expect(sections.map((section) => section.id)).toEqual([
      'DATA',
      'INDICATOR',
      'MATH',
      'LOGIC',
      'FEATURE_ENGINEERING',
      'ML_DL',
      'ACTION',
    ]);
    // Every section is populated: an empty FEATURE_ENGINEERING panel was SB-03.
    expect(sections.every((section) => section.blocks.length > 0)).toBe(true);
    expect(sections.find((s) => s.id === 'FEATURE_ENGINEERING').blocks).toHaveLength(2);
  });

  it('looks a descriptor up by block_id, including the blocks SB-04 hid', async () => {
    mockClient.get.mockResolvedValueOnce(ok());
    await loadRegistry();

    for (const blockId of ['wma', 'hma', 'catboost', 'autoencoder']) {
      expect(getDescriptor(blockId)).not.toBeNull();
      expect(getDescriptor(blockId).block_id).toBe(blockId);
    }

    const sma = getDescriptor('sma');
    expect(sma.category).toBe('INDICATOR');
    expect(sma.inputs).toEqual([{ port: 'series', type: 'PRICE_SERIES', required: true }]);
    expect(sma.outputs).toEqual([{ port: 'value', type: 'SCALAR_SERIES' }]);
    expect(getDescriptor('no_such_block')).toBeNull();
  });

  it('groups descriptors by category', async () => {
    mockClient.get.mockResolvedValueOnce(ok());
    await loadRegistry();

    expect(getBlocksByCategory('INDICATOR').map((b) => b.block_id)).toEqual(['sma', 'wma', 'hma']);
    expect(getBlocksByCategory('ML_DL').map((b) => b.block_id)).toEqual([
      'catboost',
      'autoencoder',
    ]);
    expect(getBlocksByCategory('NOT_A_CATEGORY')).toEqual([]);
  });

  it('exposes the compatibility matrix, the port vocabulary and the registry version', async () => {
    mockClient.get.mockResolvedValueOnce(ok());
    await loadRegistry();

    expect(getCompatibilityMatrix()).toEqual(COMPATIBILITY_MATRIX);
    expect(getPortTypes()).toEqual([...PORT_TYPES]);
    expect(getRegistryVersion()).toBe('r_4f19c2a8');
    expect(getRegistrySnapshot().registrySchemaVersion).toBe(1);
  });

  it('serves a held registry without a second fetch, and notifies subscribers', async () => {
    mockClient.get.mockResolvedValueOnce(ok());
    const states = [];
    const unsubscribe = subscribe((snapshot) => states.push(snapshot.state));

    await loadRegistry();
    await loadRegistry();
    unsubscribe();

    expect(mockClient.get).toHaveBeenCalledTimes(1);
    expect(states).toEqual([REGISTRY_STATES.LOADING, REGISTRY_STATES.READY]);
  });
});

// ---------------------------------------------------------------------------
// 2. ETag caching
// ---------------------------------------------------------------------------

describe('registryClient: ETag caching', () => {
  it('sends no If-None-Match on the first request', async () => {
    mockClient.get.mockResolvedValueOnce(ok());
    await loadRegistry();

    expect(lastRequestConfig().headers['If-None-Match']).toBeUndefined();
  });

  it('revalidates with the held ETag and treats 304 as success', async () => {
    mockClient.get.mockResolvedValueOnce(ok()).mockResolvedValueOnce(notModified());

    await loadRegistry();
    const revalidated = await refreshRegistry();

    expect(mockClient.get).toHaveBeenCalledTimes(2);
    expect(lastRequestConfig().headers['If-None-Match']).toBe(ETAG);
    // Axios rejects 304 by default; a cache hit must not become a palette error.
    expect(lastRequestConfig().validateStatus(304)).toBe(true);
    expect(lastRequestConfig().validateStatus(500)).toBe(false);
    expect(revalidated.state).toBe(REGISTRY_STATES.READY);
    expect(revalidated.fromCache).toBe(true);
  });

  it('reuses the cached payload on 304 and does not clear it', async () => {
    mockClient.get.mockResolvedValueOnce(ok()).mockResolvedValueOnce(notModified());

    const first = await loadRegistry();
    const after304 = await refreshRegistry();

    expect(after304.blocks).toHaveLength(BLOCKS.length);
    expect(after304.blocks).toEqual(first.blocks);
    expect(after304.registryVersion).toBe('r_4f19c2a8');
    expect(getDescriptor('catboost')).not.toBeNull();
    expect(getPaletteSections().every((section) => section.blocks.length > 0)).toBe(true);
  });

  it('adopts a new payload and its new ETag when the registry changed', async () => {
    const changed = payload({
      registry_version: 'r_99999999',
      blocks: [...BLOCKS, descriptor('kama', 'INDICATOR')].map((b) => ({ ...b })),
    });
    mockClient.get
      .mockResolvedValueOnce(ok())
      .mockResolvedValueOnce(ok(changed, '"r_99999999-blocks"'))
      .mockResolvedValueOnce(notModified('"r_99999999-blocks"'));

    await loadRegistry();
    const updated = await refreshRegistry();
    expect(updated.registryVersion).toBe('r_99999999');
    expect(getDescriptor('kama')).not.toBeNull();

    await refreshRegistry();
    expect(lastRequestConfig().headers['If-None-Match']).toBe('"r_99999999-blocks"');
  });

  it('fails closed when 304 arrives with no cached copy to reuse', async () => {
    mockClient.get.mockResolvedValueOnce(notModified());

    const snapshot = await loadRegistry();

    expect(snapshot.state).toBe(REGISTRY_STATES.ERROR);
    expect(snapshot.error.code).toBe('REGISTRY_NOT_MODIFIED_WITHOUT_CACHE');
    expect(snapshot.blocks).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 3. Fail closed — the anti-fallback rule
// ---------------------------------------------------------------------------

describe('registryClient: fail closed', () => {
  it('yields an error state with ZERO blocks and no fallback list on a 500', async () => {
    mockClient.get.mockRejectedValueOnce(httpFailure(500));

    const snapshot = await loadRegistry();

    expect(snapshot.state).toBe(REGISTRY_STATES.ERROR);
    expect(snapshot.isError).toBe(true);
    expect(snapshot.blocks).toEqual([]);
    expect(snapshot.blocks).toHaveLength(0);
    expect(snapshot.categories).toEqual([]);
    expect(snapshot.portTypes).toEqual([]);
    expect(snapshot.compatibilityMatrix).toEqual({});
    expect(snapshot.registryVersion).toBeNull();
    expect(getPaletteSections()).toEqual([]);
    expect(getBlocksByCategory('FEATURE_ENGINEERING')).toEqual([]);
    expect(getDescriptor('sma')).toBeNull();
    expect(snapshot.error).toBeInstanceOf(RegistryError);
    expect(snapshot.error.code).toBe('REGISTRY_UNAVAILABLE');
    expect(snapshot.error.retryable).toBe(true);
  });

  it('classifies 503 registry assembly failure as unavailable and retryable', async () => {
    mockClient.get.mockRejectedValueOnce(
      httpFailure(503, { error: 'REGISTRY_ASSEMBLY_FAILED', message: 'category empty' }),
    );

    const snapshot = await loadRegistry();

    expect(snapshot.error.code).toBe('REGISTRY_UNAVAILABLE');
    expect(snapshot.error.status).toBe(503);
    expect(snapshot.error.retryable).toBe(true);
    expect(snapshot.blocks).toEqual([]);
  });

  it('reports a network failure as retryable with zero blocks', async () => {
    mockClient.get.mockRejectedValueOnce(new Error('Network Error'));

    const snapshot = await loadRegistry();

    expect(snapshot.error.code).toBe('REGISTRY_NETWORK_ERROR');
    expect(snapshot.error.status).toBeNull();
    expect(snapshot.error.retryable).toBe(true);
    expect(snapshot.blocks).toEqual([]);
  });

  it('drops a previously good payload when a later request fails', async () => {
    mockClient.get.mockResolvedValueOnce(ok()).mockRejectedValueOnce(httpFailure(500));

    const ready = await loadRegistry();
    expect(ready.blocks).toHaveLength(BLOCKS.length);

    const failed = await refreshRegistry();

    // No stale-forever list survives an error, and the stale validator is gone with it.
    expect(failed.state).toBe(REGISTRY_STATES.ERROR);
    expect(failed.blocks).toEqual([]);
    expect(getDescriptor('sma')).toBeNull();

    mockClient.get.mockResolvedValueOnce(ok());
    await loadRegistry();
    expect(lastRequestConfig().headers['If-None-Match']).toBeUndefined();
  });

  it('distinguishes an expired session from a broken registry', async () => {
    mockClient.get.mockRejectedValueOnce(httpFailure(401, { detail: 'Not authenticated' }));

    const snapshot = await loadRegistry();

    expect(snapshot.error.code).toBe('REGISTRY_UNAUTHENTICATED');
    expect(snapshot.error.status).toBe(401);
    expect(snapshot.error.authExpired).toBe(true);
    expect(snapshot.error.retryable).toBe(false);
    expect(snapshot.blocks).toEqual([]);

    // A registry failure is the other case, and must not look the same.
    resetRegistryClient();
    mockClient.get.mockRejectedValueOnce(httpFailure(500));
    const broken = await loadRegistry();
    expect(broken.error.code).toBe('REGISTRY_UNAVAILABLE');
    expect(broken.error.authExpired).toBe(false);
    expect(broken.error.retryable).toBe(true);
    expect(broken.error.code).not.toBe(snapshot.error.code);
  });

  it('separates 403 and 429 from a registry outage', async () => {
    mockClient.get.mockRejectedValueOnce(httpFailure(403));
    expect((await loadRegistry()).error.code).toBe('REGISTRY_FORBIDDEN');

    resetRegistryClient();
    mockClient.get.mockRejectedValueOnce(httpFailure(429));
    const limited = await loadRegistry();
    expect(limited.error.code).toBe('REGISTRY_RATE_LIMITED');
    expect(limited.error.retryable).toBe(true);
  });

  it('retries successfully after an error', async () => {
    mockClient.get.mockRejectedValueOnce(httpFailure(500)).mockResolvedValueOnce(ok());

    const failed = await loadRegistry();
    expect(failed.state).toBe(REGISTRY_STATES.ERROR);

    const recovered = await loadRegistry();

    expect(mockClient.get).toHaveBeenCalledTimes(2);
    expect(recovered.state).toBe(REGISTRY_STATES.READY);
    expect(recovered.blocks).toHaveLength(BLOCKS.length);
    expect(getDescriptor('autoencoder')).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 4. Malformed payloads are errors, never a partial registry
// ---------------------------------------------------------------------------

describe('registryClient: malformed payloads', () => {
  const cases = [
    ['a non-object body', 'not json at all', 'REGISTRY_MALFORMED'],
    ['no registry_version', payload({ registry_version: '' }), 'REGISTRY_MALFORMED'],
    ['no port_types', payload({ port_types: [] }), 'REGISTRY_MALFORMED'],
    ['no categories', payload({ categories: [] }), 'REGISTRY_MALFORMED'],
    ['a category with no id', payload({ categories: [{ display_name: 'X', order: 1 }] }), 'REGISTRY_MALFORMED'],
    ['a category with no order', payload({ categories: [{ id: 'DATA', display_name: 'X' }] }), 'REGISTRY_MALFORMED'],
    ['no blocks list', payload({ blocks: undefined }), 'REGISTRY_MALFORMED'],
    ['zero blocks', payload({ blocks: [] }), 'REGISTRY_EMPTY'],
    ['no compatibility_matrix', payload({ compatibility_matrix: undefined }), 'REGISTRY_MALFORMED'],
  ];

  it.each(cases)('reports %s as an error with zero blocks', async (_label, body, code) => {
    mockClient.get.mockResolvedValueOnce(ok(body));

    const snapshot = await loadRegistry();

    expect(snapshot.state).toBe(REGISTRY_STATES.ERROR);
    expect(snapshot.error.code).toBe(code);
    expect(snapshot.blocks).toEqual([]);
    expect(getPaletteSections()).toEqual([]);
  });

  it('rejects the whole payload when one descriptor is malformed', async () => {
    const broken = payload({
      blocks: [...BLOCKS.map((b) => ({ ...b })), { display_name: 'No id', category: 'MATH' }],
    });
    mockClient.get.mockResolvedValueOnce(ok(broken));

    const snapshot = await loadRegistry();

    // Partially populated is worse than empty: it looks like it worked.
    expect(snapshot.state).toBe(REGISTRY_STATES.ERROR);
    expect(snapshot.error.code).toBe('REGISTRY_MALFORMED');
    expect(snapshot.blocks).toEqual([]);
    expect(getDescriptor('sma')).toBeNull();
  });

  it('rejects a block whose category is not served, and a duplicate block_id', async () => {
    mockClient.get.mockResolvedValueOnce(
      ok(payload({ blocks: [...BLOCKS, descriptor('ghost', 'NOT_SERVED')].map((b) => ({ ...b })) })),
    );
    expect((await loadRegistry()).error.code).toBe('REGISTRY_MALFORMED');

    resetRegistryClient();
    mockClient.get.mockResolvedValueOnce(
      ok(payload({ blocks: [...BLOCKS, descriptor('sma', 'INDICATOR')].map((b) => ({ ...b })) })),
    );
    const duplicated = await loadRegistry();
    expect(duplicated.error.code).toBe('REGISTRY_MALFORMED');
    expect(duplicated.blocks).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 5. Concurrency
// ---------------------------------------------------------------------------

describe('registryClient: concurrent callers', () => {
  it('makes one fetch for many simultaneous callers', async () => {
    let resolveRequest;
    mockClient.get.mockImplementationOnce(
      () => new Promise((resolve) => { resolveRequest = resolve; }),
    );

    const callers = [loadRegistry(), loadRegistry(), loadRegistry(), refreshRegistry()];
    expect(getRegistrySnapshot().state).toBe(REGISTRY_STATES.LOADING);
    expect(getRegistrySnapshot().blocks).toEqual([]);

    resolveRequest(ok());
    const snapshots = await Promise.all(callers);

    expect(mockClient.get).toHaveBeenCalledTimes(1);
    expect(snapshots.every((snapshot) => snapshot.state === REGISTRY_STATES.READY)).toBe(true);
    expect(new Set(snapshots).size).toBe(1);
  });

  it('shares one failed request between concurrent callers, each seeing zero blocks', async () => {
    let rejectRequest;
    mockClient.get.mockImplementationOnce(
      () => new Promise((_resolve, reject) => { rejectRequest = reject; }),
    );

    const callers = [loadRegistry(), loadRegistry()];
    rejectRequest(httpFailure(500));
    const snapshots = await Promise.all(callers);

    expect(mockClient.get).toHaveBeenCalledTimes(1);
    expect(snapshots.every((snapshot) => snapshot.blocks.length === 0)).toBe(true);
    expect(snapshots.every((snapshot) => snapshot.state === REGISTRY_STATES.ERROR)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 6. blockRegistry.js is presentation only
// ---------------------------------------------------------------------------

describe('blockRegistry.js holds no block definitions', () => {
  it('exports zero block definitions and no block lookup at all', () => {
    // Task 3.6 removed the fail-closed shims with their last call sites. A name that answers
    // "no blocks" is still a name a future caller could reach for, so there is no longer one.
    for (const name of ['BlockRegistry', 'getBlockByType', 'getBlocksByCategory', 'getAllBlocks']) {
      expect(blockRegistry[name], `${name} must not be exported any more`).toBeUndefined();
    }

    // Nothing exported may look like a descriptor: block ids, ports, parameter lists and
    // per-block validators are the registry's business (SB-03, SB-04).
    for (const [name, value] of Object.entries(blockRegistry)) {
      if (typeof value !== 'object' || value === null) continue;
      for (const entry of Object.values(value)) {
        if (typeof entry !== 'object' || entry === null) continue;
        expect(
          ['parameters', 'params', 'inputs', 'outputs', 'backendType', 'validation'].some((key) =>
            Object.prototype.hasOwnProperty.call(entry, key),
          ),
          `${name} carries a block definition`,
        ).toBe(false);
      }
    }
  });

  it('exports only presentation, and nothing a strategy could depend on', () => {
    expect(Object.keys(blockRegistry).sort()).toEqual([
      'BlockCategories',
      'CATEGORY_PRESENTATION',
      'StreamTypes',
      'getCategoryColor',
      'getCategoryIcon',
      'getCategoryPresentation',
      'normalizeCategoryId',
    ]);
    expect(Object.isFrozen(blockRegistry.CATEGORY_PRESENTATION)).toBe(true);
  });

  it('generates StreamTypes from the backend port-type vocabulary', () => {
    expect(Object.keys(blockRegistry.StreamTypes)).toEqual([...PORT_TYPES]);
    for (const portType of PORT_TYPES) {
      expect(blockRegistry.StreamTypes[portType]).toBe(portType);
    }
    // The legacy hand-written vocabulary is gone with the blocks that used it.
    expect(blockRegistry.StreamTypes.OHLCV).toBeUndefined();
    expect(blockRegistry.StreamTypes.MARKET_DATA).toBeUndefined();
  });

  it('agrees with the vocabulary the registry actually serves', async () => {
    mockClient.get.mockResolvedValueOnce(ok());
    await loadRegistry();

    expect(getPortTypes()).toEqual(Object.values(blockRegistry.StreamTypes));
  });

  it('keys the category presentation on the seven ids the registry serves', async () => {
    mockClient.get.mockResolvedValueOnce(ok());
    await loadRegistry();

    expect(Object.keys(blockRegistry.CATEGORY_PRESENTATION).sort()).toEqual(
      getCategories()
        .map((category) => category.id)
        .sort(),
    );
    for (const category of getCategories()) {
      expect(blockRegistry.getCategoryIcon(category.id)).toBeTruthy();
      expect(blockRegistry.getCategoryColor(category.id)).toBeTruthy();
      expect(blockRegistry.normalizeCategoryId(category.id)).toBe(category.id);
    }
  });

  it('resolves the served ids and no longer speaks the legacy lowercase vocabulary', () => {
    expect(blockRegistry.normalizeCategoryId('DATA')).toBe('DATA');
    expect(blockRegistry.normalizeCategoryId('feature_engineering')).toBe('FEATURE_ENGINEERING');
    // The eight-key lowercase vocabulary went with the deprecated /api/strategies/blocks call
    // (task 3.6). It is what made the SB-04 category mapping possible.
    expect(blockRegistry.normalizeCategoryId('indicators')).toBeNull();
    expect(blockRegistry.normalizeCategoryId('ml')).toBeNull();
    expect(blockRegistry.normalizeCategoryId('dl')).toBeNull();
    expect(blockRegistry.normalizeCategoryId('nonsense')).toBeNull();
    // An unknown category still draws something rather than hiding a served block.
    expect(blockRegistry.getCategoryIcon('nonsense')).toBeTruthy();
  });
});
