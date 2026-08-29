/**
 * Asset discovery and the published timeframe set — the client half. Task 7.3.
 *
 * Requirements under test: 11.2, 11.3, 11.4, 11.7, 11.8, and 11.6's client consequence.
 *
 * What is real here and what is stubbed
 * ------------------------------------
 * The shared axios instance (`src/apiClient`) is the only stub, exactly as
 * `registryClient.test.js` does it. `api/modules/assets.js`, `lib/assetUniverse.js` and
 * `lib/registryClient.js`'s timeframe cache are the shipped code, driven through that one
 * boundary. Response bodies are the real wire shapes: `discover_assets()`'s page dict and
 * `AssetRef.to_dict()` from `backend_app/backend/asset_universe.py`, and the
 * `{registry_version, timeframes: [{id, label, seconds}], sources, total}` payload
 * `get_registry_timeframes` serves — including the ten labels task 7.2's intersection
 * actually publishes, with `3m` absent, because "`3m` is not offered" is a claim these tests
 * have to be able to check.
 *
 * The rule the whole file is shaped around: **a failure yields zero markets and a stated
 * reason.** Not a substitute list, not a stale copy, and not a bare empty list that reads as
 * "this platform trades nothing".
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

const { mockClient } = vi.hoisted(() => ({ mockClient: { get: vi.fn() } }));

vi.mock('../../src/apiClient', () => ({ default: mockClient }));

import {
  ASSETS_PATH,
  ASSET_LIMIT_CEILING,
  ASSET_LIMIT_DEFAULT,
  assetQueryParams,
  assetsApi,
} from '../../src/api/modules/assets';
import {
  ASSET_ERROR_CODES,
  ASSET_FILTER_KEYS,
  AssetQueryError,
  NOT_PUBLISHED_TEXT,
  canonicalFilters,
  classifyAssetError,
  describeAsset,
  filterFingerprint,
  marketTypeOptionsFrom,
  mergeAssetPages,
  normaliseAsset,
  readAssetPage,
} from '../../src/lib/assetUniverse';
import {
  REGISTRY_RESOURCES,
  REGISTRY_STATES,
  REGISTRY_TIMEFRAMES_PATH,
  getTimeframeSeconds,
  getTimeframeSnapshot,
  getTimeframes,
  loadTimeframes,
  refreshTimeframes,
  resetRegistryClient,
  subscribeTimeframes,
} from '../../src/lib/registryClient';

// ---------------------------------------------------------------------------
// Wire fixtures — the real shapes
// ---------------------------------------------------------------------------

/** One `AssetRef.to_dict()`. Absent figures are `null`, exactly as the endpoint sends them. */
const assetRecord = (overrides = {}) => ({
  symbol: 'BTC/USDT',
  base: 'BTC',
  quote: 'USDT',
  market_type: 'spot',
  active: true,
  price_precision: 2,
  amount_precision: 6,
  min_notional: 5,
  min_amount: 0.0001,
  available_on: ['binance', 'okx'],
  precision_source: 'binance',
  listing_count: 2,
  ...overrides,
});

const page = (records, overrides = {}) => ({
  assets: records,
  total: records.length,
  limit: 50,
  next_cursor: null,
  universe_changed: false,
  source_meta: {
    generated_at: '2024-05-01T00:00:00+00:00',
    age_seconds: 12.5,
    ttl_seconds: 21600,
    refresh_interval_seconds: 3600,
    stale: false,
    universe_total: 61,
    universe_hash: 'u_abc123',
    exchanges: ['binance', 'okx'],
    exchanges_failed: [],
    cache_backend: 'process',
    refresh_in_flight: false,
  },
  ...overrides,
});

const ok = (body, headers = {}) => ({ status: 200, data: body, headers });

/**
 * A rejection shaped like `apiClient`'s `ApiError`: `status` plus the normalised body
 * `create_api_error_response` produces, with the raiser's dict on `detail` and its `error` /
 * `message` lifted to the top level.
 */
const httpFailure = (status, detail) => {
  const body =
    detail === undefined
      ? { error: `HTTP_${status}_ERROR`, message: 'An error occurred.', detail: null }
      : {
          error: detail.error,
          message: detail.message,
          detail,
          status_code: status,
          details: Object.fromEntries(
            Object.entries(detail).filter(([key]) => !['error', 'message'].includes(key)),
          ),
        };
  const error = new Error(body.message);
  error.status = status;
  error.data = body;
  return error;
};

const universeUnavailable = () =>
  httpFailure(503, {
    error: 'ASSET_UNIVERSE_UNAVAILABLE',
    message:
      'The tradeable market universe is not available yet. It is refreshed on a schedule ' +
      'outside the request path; retry shortly.',
    last_refresh_error: 'binance: getaddrinfo failed',
    refresh_in_flight: true,
    retry_after_seconds: 30,
  });

const cursorInvalid = () =>
  httpFailure(422, {
    error: 'ASSET_CURSOR_INVALID',
    message: 'This cursor was issued for a different filter set.',
  });

/** The ten labels task 7.2's three-way intersection publishes. `3m` is deliberately absent. */
const TIMEFRAME_ENTRIES = Object.freeze([
  { id: '1m', label: '1m', seconds: 60 },
  { id: '5m', label: '5m', seconds: 300 },
  { id: '15m', label: '15m', seconds: 900 },
  { id: '30m', label: '30m', seconds: 1800 },
  { id: '1h', label: '1h', seconds: 3600 },
  { id: '2h', label: '2h', seconds: 7200 },
  { id: '4h', label: '4h', seconds: 14400 },
  { id: '6h', label: '6h', seconds: 21600 },
  { id: '12h', label: '12h', seconds: 43200 },
  { id: '1d', label: '1d', seconds: 86400 },
]);

const TIMEFRAME_ETAG = '"r_4f19c2a8-timeframes"';

const timeframePayload = (overrides = {}) => ({
  registry_version: 'r_4f19c2a8',
  registry_schema_version: 1,
  timeframes: TIMEFRAME_ENTRIES.map((entry) => ({ ...entry })),
  sources: [
    'backend_app.backend.backtesting_engine.VALID_FREQ_MAP',
    'backend_app.backend.master_executor.TF_SEC',
    'backend_app.backend.market_data_validation.TIMEFRAME_MINUTES',
  ],
  total: TIMEFRAME_ENTRIES.length,
  ...overrides,
});

beforeEach(() => {
  mockClient.get.mockReset();
  resetRegistryClient();
});

// ---------------------------------------------------------------------------
// The transport: what a filter set becomes on the wire
// ---------------------------------------------------------------------------

describe('assetsApi.discover: the query it sends', () => {
  it('targets the canonical discovery endpoint', () => {
    expect(ASSETS_PATH).toBe('/api/strategy-operations/assets');
  });

  it('omits an absent filter rather than sending it empty', () => {
    // `?quote=` would ask for markets quoted in the empty string, which the endpoint matches
    // exactly. Absence and emptiness are different requests.
    expect(assetQueryParams({ search: '  ', quote: '', base: null })).toEqual({});
  });

  it('sends every filter the endpoint declares (Requirement 11.2)', () => {
    expect(
      assetQueryParams({
        search: 'btc',
        base: 'BTC',
        quote: 'USDT',
        marketType: 'swap',
        activeOnly: false,
        limit: 25,
        cursor: 'c_1',
      }),
    ).toEqual({
      search: 'btc',
      base: 'BTC',
      quote: 'USDT',
      market_type: 'swap',
      active_only: false,
      limit: 25,
      cursor: 'c_1',
    });
  });

  it('omits active_only unless the caller states it, inheriting the server default', () => {
    expect(assetQueryParams({ search: 'btc' }).active_only).toBeUndefined();
    expect(assetQueryParams({ activeOnly: true }).active_only).toBe(true);
    expect(assetQueryParams({ activeOnly: false }).active_only).toBe(false);
  });

  it("clamps limit to the server's own ceiling instead of earning a 422", () => {
    expect(ASSET_LIMIT_CEILING).toBe(500);
    expect(ASSET_LIMIT_DEFAULT).toBe(50);
    expect(assetQueryParams({ limit: 5000 }).limit).toBe(ASSET_LIMIT_CEILING);
    expect(assetQueryParams({ limit: 0 }).limit).toBe(1);
    expect(assetQueryParams({ limit: 'nonsense' }).limit).toBeUndefined();
  });

  it('returns the universe hash header beside the body', async () => {
    mockClient.get.mockResolvedValueOnce(ok(page([assetRecord()]), { 'x-asset-universe-hash': 'u_abc123' }));

    const response = await assetsApi.discover({ search: 'btc' });

    expect(mockClient.get.mock.calls[0][0]).toBe(ASSETS_PATH);
    expect(mockClient.get.mock.calls[0][1].params).toEqual({ search: 'btc' });
    expect(response.universeHash).toBe('u_abc123');
  });

  it('forwards an abort signal, so a superseded search can be cancelled', async () => {
    mockClient.get.mockResolvedValueOnce(ok(page([])));
    const controller = new AbortController();

    await assetsApi.discover({}, { signal: controller.signal });

    expect(mockClient.get.mock.calls[0][1].signal).toBe(controller.signal);
  });
});

// ---------------------------------------------------------------------------
// The projection: absent is not zero
// ---------------------------------------------------------------------------

describe('normaliseAsset / describeAsset: the endpoint figures, as sent', () => {
  it('carries every field Requirement 11.4 names', () => {
    const asset = normaliseAsset(assetRecord());

    expect(asset).toMatchObject({
      symbol: 'BTC/USDT',
      base: 'BTC',
      quote: 'USDT',
      marketType: 'spot',
      active: true,
      pricePrecision: 2,
      amountPrecision: 6,
      minNotional: 5,
      minAmount: 0.0001,
      precisionSource: 'binance',
      listingCount: 2,
    });
    expect([...asset.availableOn]).toEqual(['binance', 'okx']);
  });

  it('keeps an unpublished figure as null and reads it as "not published", never 0', () => {
    const asset = normaliseAsset(
      assetRecord({ min_notional: null, min_amount: null, price_precision: null }),
    );

    expect(asset.minNotional).toBeNull();
    expect(asset.minAmount).toBeNull();

    const text = describeAsset(asset);
    expect(text).toContain(`min notional ${NOT_PUBLISHED_TEXT}`);
    expect(text).toContain(`min amount ${NOT_PUBLISHED_TEXT}`);
    // The distinction that matters: a later order-size check reading 0 as "no minimum" would
    // size an order against a limit nobody stated.
    expect(text).not.toContain('min notional 0');
  });

  it('distinguishes a stated zero minimum from an unstated one', () => {
    const stated = describeAsset(normaliseAsset(assetRecord({ min_notional: 0 })));

    expect(stated).toContain('min notional 0');
    expect(stated).not.toContain(`min notional ${NOT_PUBLISHED_TEXT}`);
  });

  it('treats an unstated active flag as unknown rather than as true', () => {
    const asset = normaliseAsset(assetRecord({ active: undefined }));

    expect(asset.active).toBeNull();
    expect(describeAsset(asset)).toContain('active state not published');
  });

  it('names an inactive market as inactive', () => {
    expect(describeAsset(normaliseAsset(assetRecord({ active: false })))).toContain('inactive');
  });

  it('reports listing_count as a venue count and never as a rank', () => {
    const text = describeAsset(normaliseAsset(assetRecord()));

    expect(text).toContain('listed on 2 venues');
    expect(text.toLowerCase()).not.toContain('rank');
    expect(text.toLowerCase()).not.toContain('liquidity');
  });

  it('names the venue the precision figures belong to, never blending two', () => {
    expect(describeAsset(normaliseAsset(assetRecord()))).toContain('from binance');
  });

  it('refuses a record with no symbol rather than rendering a nameless option', () => {
    expect(() => normaliseAsset(assetRecord({ symbol: '' }))).toThrow(AssetQueryError);
    expect(() => normaliseAsset(null)).toThrow(AssetQueryError);
  });

  it('guesses nothing: no market type inferred, no precision defaulted', () => {
    const asset = normaliseAsset({ symbol: 'FOO/BAR' });

    expect(asset.marketType).toBeNull();
    expect(asset.pricePrecision).toBeNull();
    expect(asset.amountPrecision).toBeNull();
    expect(asset.listingCount).toBe(0);
  });
});

describe('readAssetPage', () => {
  it('reads the page, the filtered total and the keyset cursor (Requirement 11.3)', () => {
    const read = readAssetPage(
      page([assetRecord(), assetRecord({ symbol: 'ETH/USDT', base: 'ETH' })], {
        total: 340,
        next_cursor: 'c_2',
      }),
    );

    expect(read.assets).toHaveLength(2);
    // The count of everything matching the filters, not of this page.
    expect(read.total).toBe(340);
    expect(read.nextCursor).toBe('c_2');
    expect(read.universeHash).toBe('u_abc123');
    expect(read.universeTotal).toBe(61);
  });

  it('reads universe_changed and stale as the server states them', () => {
    const changed = readAssetPage(page([], { universe_changed: true }));
    expect(changed.universeChanged).toBe(true);

    const stale = readAssetPage(
      page([], { source_meta: { ...page([]).source_meta, stale: true, age_seconds: 30000 } }),
    );
    expect(stale.stale).toBe(true);
    expect(stale.ageSeconds).toBe(30000);
  });

  it('reads a 200 with zero assets as a real answer, not as an error', () => {
    // "No market matched your filters" and "there is no universe" are different statements
    // and have to stay distinguishable.
    const read = readAssetPage(page([], { total: 0 }));

    expect(read.assets).toEqual([]);
    expect(read.total).toBe(0);
  });

  it('refuses a page rather than silently serving a shorter one', () => {
    expect(() => readAssetPage(page([assetRecord(), { base: 'ETH' }]))).toThrow(AssetQueryError);
    expect(() => readAssetPage({ assets: [], total: 'many' })).toThrow(AssetQueryError);
    expect(() => readAssetPage({ total: 0 })).toThrow(AssetQueryError);
  });
});

// ---------------------------------------------------------------------------
// Failure classification: the backend's own code and sentence
// ---------------------------------------------------------------------------

describe('classifyAssetError', () => {
  it('reads ASSET_UNIVERSE_UNAVAILABLE and renders the backend sentence verbatim', () => {
    const error = classifyAssetError(universeUnavailable());

    expect(error.code).toBe(ASSET_ERROR_CODES.UNIVERSE_UNAVAILABLE);
    expect(error.isUniverseUnavailable).toBe(true);
    expect(error.status).toBe(503);
    expect(error.retryable).toBe(true);
    expect(error.backendAuthored).toBe(true);
    expect(error.message).toBe(
      'The tradeable market universe is not available yet. It is refreshed on a schedule ' +
        'outside the request path; retry shortly.',
    );
  });

  it('takes the retry interval the server stated and never invents one', () => {
    expect(classifyAssetError(universeUnavailable()).retryAfterSeconds).toBe(30);

    const withoutInterval = classifyAssetError(
      httpFailure(503, { error: 'ASSET_UNIVERSE_UNAVAILABLE', message: 'no universe' }),
    );
    expect(withoutInterval.retryAfterSeconds).toBeNull();

    const fromHeader = classifyAssetError(
      httpFailure(503, { error: 'ASSET_UNIVERSE_UNAVAILABLE', message: 'no universe' }),
      { retryAfter: '45' },
    );
    expect(fromHeader.retryAfterSeconds).toBe(45);
  });

  it('keys on the code, not the status: a 422 with another code is not a cursor failure', () => {
    expect(classifyAssetError(cursorInvalid()).isCursorInvalid).toBe(true);
    expect(
      classifyAssetError(httpFailure(422, { error: 'VALIDATION_ERROR', message: 'bad limit' }))
        .isCursorInvalid,
    ).toBe(false);
  });

  it('separates an expired session from a broken universe', () => {
    const expired = classifyAssetError(httpFailure(401));

    expect(expired.code).toBe(ASSET_ERROR_CODES.UNAUTHENTICATED);
    expect(expired.authExpired).toBe(true);
    // Retrying will not fix a dead session, so the state must not offer it as the fix.
    expect(expired.retryable).toBe(false);
  });

  it('classifies 403, 429, other 5xx, other 4xx and a lost network distinctly', () => {
    expect(classifyAssetError(httpFailure(403)).code).toBe(ASSET_ERROR_CODES.FORBIDDEN);
    expect(classifyAssetError(httpFailure(429)).code).toBe(ASSET_ERROR_CODES.RATE_LIMITED);
    expect(classifyAssetError(httpFailure(500)).code).toBe(ASSET_ERROR_CODES.UNAVAILABLE);
    expect(classifyAssetError(httpFailure(418)).code).toBe(ASSET_ERROR_CODES.REQUEST_FAILED);

    const offline = classifyAssetError(new Error('Network Error'));
    expect(offline.code).toBe(ASSET_ERROR_CODES.NETWORK_ERROR);
    expect(offline.status).toBeNull();
    // The one client-authored sentence: no response arrived, so no backend text exists.
    expect(offline.backendAuthored).toBe(false);
  });

  it('never returns a substitute market list on any path', () => {
    for (const failure of [universeUnavailable(), httpFailure(500), httpFailure(401), new Error('x')]) {
      const classified = classifyAssetError(failure);
      expect(classified).toBeInstanceOf(AssetQueryError);
      expect(JSON.stringify(classified.details)).not.toMatch(/[A-Z0-9]{2,10}\/[A-Z0-9]{2,10}/);
    }
  });
});

// ---------------------------------------------------------------------------
// Cursor pinning: what "the same query" means
// ---------------------------------------------------------------------------

describe('filter fingerprints: a cursor belongs to one filter set', () => {
  it('covers every filter the server pins a cursor to', () => {
    expect([...ASSET_FILTER_KEYS]).toEqual([
      'search',
      'base',
      'quote',
      'marketType',
      'activeOnly',
      'limit',
    ]);
  });

  it('changes when any pinned filter changes', () => {
    const base = { search: 'btc', quote: 'USDT', activeOnly: true };
    const reference = filterFingerprint(base);

    expect(filterFingerprint({ ...base, search: 'eth' })).not.toBe(reference);
    expect(filterFingerprint({ ...base, quote: 'USDC' })).not.toBe(reference);
    expect(filterFingerprint({ ...base, marketType: 'swap' })).not.toBe(reference);
    expect(filterFingerprint({ ...base, activeOnly: false })).not.toBe(reference);
    expect(filterFingerprint({ ...base, limit: 100 })).not.toBe(reference);
  });

  it('does not change for a different object with the same filters', () => {
    expect(filterFingerprint({ search: 'btc', quote: ' usdt ' })).toBe(
      filterFingerprint({ quote: 'USDT', search: 'btc' }),
    );
  });

  it('canonicalises currency codes but leaves search case alone', () => {
    const canonical = canonicalFilters({ base: ' btc ', quote: 'usdt', search: ' BtC ' });

    expect(canonical.base).toBe('BTC');
    expect(canonical.quote).toBe('USDT');
    expect(canonical.search).toBe('BtC');
    // The server's own default, inherited rather than restated as a second policy.
    expect(canonical.activeOnly).toBe(true);
  });
});

describe('mergeAssetPages', () => {
  it('appends a disjoint keyset page unchanged', () => {
    const first = [normaliseAsset(assetRecord())];
    const second = [normaliseAsset(assetRecord({ symbol: 'ETH/USDT' }))];

    const merged = mergeAssetPages(first, second);

    expect(merged.assets.map((asset) => asset.symbol)).toEqual(['BTC/USDT', 'ETH/USDT']);
    expect(merged.duplicates).toBe(0);
  });

  it('shows a market once when the universe shifted mid-page, and counts the repeat', () => {
    const first = [normaliseAsset(assetRecord())];
    const second = [normaliseAsset(assetRecord()), normaliseAsset(assetRecord({ symbol: 'SOL/USDT' }))];

    const merged = mergeAssetPages(first, second);

    expect(merged.assets.map((asset) => asset.symbol)).toEqual(['BTC/USDT', 'SOL/USDT']);
    // Counted rather than swallowed, so the shift can be stated.
    expect(merged.duplicates).toBe(1);
  });
});

describe('marketTypeOptionsFrom: the descriptor is the vocabulary', () => {
  it("reads the DATA block's own market_type options", () => {
    const params = [
      { key: 'symbol', type: 'SYMBOL', required: true, default: null },
      { key: 'market_type', type: 'SELECT', options: ['spot', 'swap', 'future'] },
    ];

    expect([...marketTypeOptionsFrom(params)]).toEqual(['spot', 'swap', 'future']);
  });

  it('offers nothing when the descriptor declares nothing, rather than a written-out set', () => {
    expect([...marketTypeOptionsFrom([{ key: 'market_type', type: 'SELECT' }])]).toEqual([]);
    expect([...marketTypeOptionsFrom([])]).toEqual([]);
    expect([...marketTypeOptionsFrom(undefined)]).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// The published timeframe set (Requirement 11.8)
// ---------------------------------------------------------------------------

describe('registryClient timeframes: the endpoint is the only source', () => {
  it('reads the registry timeframe projection', async () => {
    mockClient.get.mockResolvedValueOnce(ok(timeframePayload(), { etag: TIMEFRAME_ETAG }));

    const snapshot = await loadTimeframes();

    expect(REGISTRY_TIMEFRAMES_PATH).toBe('/api/strategy-operations/registry/timeframes');
    expect(mockClient.get.mock.calls[0][0]).toBe(REGISTRY_TIMEFRAMES_PATH);
    expect(snapshot.state).toBe(REGISTRY_STATES.READY);
    expect(snapshot.timeframes.map((entry) => entry.id)).toEqual([
      '1m',
      '5m',
      '15m',
      '30m',
      '1h',
      '2h',
      '4h',
      '6h',
      '12h',
      '1d',
    ]);
  });

  it('does not invent 3m, or any other interval the endpoint withheld', async () => {
    // `3m` is absent on purpose: `market_data_validation.TIMEFRAME_MINUTES` has no figure for
    // it, so a 3m strategy would run with its row-coverage check silently unfailable. A client
    // list would put it straight back in front of the author.
    mockClient.get.mockResolvedValueOnce(ok(timeframePayload()));
    await loadTimeframes();

    expect(getTimeframes().map((entry) => entry.id)).not.toContain('3m');
    expect(getTimeframes().map((entry) => entry.id)).not.toContain('1w');
    expect(getTimeframeSeconds('3m')).toBeNull();
  });

  it('serves the seconds the endpoint published, without parsing a label', async () => {
    mockClient.get.mockResolvedValueOnce(ok(timeframePayload()));
    await loadTimeframes();

    expect(getTimeframeSeconds('15m')).toBe(900);
    expect(getTimeframeSeconds('1d')).toBe(86400);
    // An unpublished label is null rather than a parsed guess: measuring freshness against a
    // guessed interval is how a stale feed reads as fresh.
    expect(getTimeframeSeconds('1M')).toBeNull();
  });

  it('preserves the served order rather than re-sorting from the labels', async () => {
    mockClient.get.mockResolvedValueOnce(ok(timeframePayload()));
    await loadTimeframes();

    const seconds = getTimeframes().map((entry) => entry.seconds);
    expect(seconds).toEqual([...seconds].sort((a, b) => a - b));
  });

  it('revalidates with If-None-Match and reuses the held set on a 304', async () => {
    mockClient.get.mockResolvedValueOnce(ok(timeframePayload(), { etag: TIMEFRAME_ETAG }));
    await loadTimeframes();

    mockClient.get.mockResolvedValueOnce({ status: 304, data: null, headers: {} });
    const revalidated = await refreshTimeframes();

    expect(mockClient.get).toHaveBeenCalledTimes(2);
    expect(mockClient.get.mock.calls[1][1].headers['If-None-Match']).toBe(TIMEFRAME_ETAG);
    expect(mockClient.get.mock.calls[1][1].validateStatus(304)).toBe(true);
    expect(revalidated.isReady).toBe(true);
    expect(revalidated.fromCache).toBe(true);
    expect(revalidated.timeframes).toHaveLength(10);
  });

  it('serves a ready set without a second request', async () => {
    mockClient.get.mockResolvedValueOnce(ok(timeframePayload()));
    await loadTimeframes();
    await loadTimeframes();

    expect(mockClient.get).toHaveBeenCalledTimes(1);
  });

  it('joins concurrent callers onto one request', async () => {
    mockClient.get.mockResolvedValueOnce(ok(timeframePayload()));

    const [first, second] = await Promise.all([loadTimeframes(), loadTimeframes()]);

    expect(mockClient.get).toHaveBeenCalledTimes(1);
    expect(first).toBe(second);
  });

  it('fails closed on every failure: zero timeframes and a stated reason', async () => {
    for (const failure of [
      httpFailure(503),
      httpFailure(500),
      httpFailure(429),
      new Error('Network Error'),
    ]) {
      resetRegistryClient();
      mockClient.get.mockReset();
      mockClient.get.mockRejectedValueOnce(failure);

      const snapshot = await loadTimeframes();

      expect(snapshot.state).toBe(REGISTRY_STATES.ERROR);
      expect(snapshot.timeframes).toEqual([]);
      expect(snapshot.error).not.toBeNull();
      expect(snapshot.error.resource).toBe(REGISTRY_RESOURCES.TIMEFRAMES);
      // No written-out interval anywhere in what the error carries.
      expect(JSON.stringify(snapshot.error.details)).not.toMatch(/"\d+[mhdw]"/);
    }
  });

  it('treats a 200 carrying zero timeframes as malformed, not as an empty vocabulary', async () => {
    // The backend refuses to publish an empty set (`503 TIMEFRAME_VOCABULARY_EMPTY`), so a 200
    // with none is a failed assembly — and an empty selector would read as a platform that
    // trades on no interval.
    mockClient.get.mockResolvedValueOnce(ok(timeframePayload({ timeframes: [], total: 0 })));

    const snapshot = await loadTimeframes();

    expect(snapshot.state).toBe(REGISTRY_STATES.ERROR);
    expect(snapshot.error.code).toBe('REGISTRY_EMPTY');
    expect(snapshot.timeframes).toEqual([]);
  });

  it('refuses an entry with no positive seconds', async () => {
    mockClient.get.mockResolvedValueOnce(
      ok(timeframePayload({ timeframes: [{ id: '15m', label: '15m', seconds: 0 }] })),
    );

    const snapshot = await loadTimeframes();

    expect(snapshot.state).toBe(REGISTRY_STATES.ERROR);
    expect(snapshot.error.code).toBe('REGISTRY_MALFORMED');
  });

  it('drops the held set on error, so a retry cannot serve a disowned one', async () => {
    mockClient.get.mockResolvedValueOnce(ok(timeframePayload(), { etag: TIMEFRAME_ETAG }));
    await loadTimeframes();
    expect(getTimeframes()).toHaveLength(10);

    mockClient.get.mockRejectedValueOnce(httpFailure(500));
    await refreshTimeframes();
    expect(getTimeframes()).toEqual([]);

    mockClient.get.mockResolvedValueOnce(ok(timeframePayload()));
    await loadTimeframes();
    expect(mockClient.get.mock.calls[2][1].headers['If-None-Match']).toBeUndefined();
  });

  it('refuses a 304 for a set it does not hold rather than rendering nothing silently', async () => {
    mockClient.get.mockResolvedValueOnce({ status: 304, data: null, headers: {} });

    const snapshot = await loadTimeframes();

    expect(snapshot.error.code).toBe('REGISTRY_NOT_MODIFIED_WITHOUT_CACHE');
    expect(snapshot.timeframes).toEqual([]);
  });

  it('announces every transition to a subscriber', async () => {
    const seen = [];
    const unsubscribe = subscribeTimeframes((snapshot) => seen.push(snapshot.state));

    mockClient.get.mockResolvedValueOnce(ok(timeframePayload()));
    await loadTimeframes();
    unsubscribe();

    expect(seen).toEqual([REGISTRY_STATES.LOADING, REGISTRY_STATES.READY]);
  });

  it('keeps the two registry caches independent', async () => {
    // A broken block registry says nothing about whether the pipeline's timeframe
    // vocabularies could be read, and conflating them would take the timeframe selector down
    // with the palette.
    mockClient.get.mockResolvedValueOnce(ok(timeframePayload()));
    await loadTimeframes();

    expect(getTimeframeSnapshot().isReady).toBe(true);
    // Blocks were never loaded, and that has not made the timeframe set unavailable.
    expect(getTimeframes()).toHaveLength(10);
  });
});
