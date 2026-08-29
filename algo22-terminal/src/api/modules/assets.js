/**
 * Asset Discovery API Module — task 7.3, Requirements 11.2, 11.3, 11.4, 11.7.
 *
 * Endpoint: `GET /api/strategy-operations/assets` (`backend_app/routers/strategy_operations.py`,
 * task 7.1), whose body is `asset_universe.discover_assets()` verbatim:
 *
 *   { assets: [AssetRef], total, limit, next_cursor, universe_changed, source_meta }
 *
 * and whose `AssetRef` is
 *
 *   { symbol, base, quote, market_type, active, price_precision, amount_precision,
 *     min_notional, min_amount, available_on, precision_source, listing_count }
 *
 * This module is the network boundary and nothing else: no state, no cache, no retry policy,
 * no judgement about what a failure means. The query state machine is `lib/assetUniverse.js`
 * and the debounce, cursor and restart rules are `hooks/useAssetSearch.js`, so the transport
 * can be swapped without moving any of that, and none of it needs a network to be asserted.
 *
 * The timeframe set the DATA node's other market control is populated from
 * (`GET /api/strategy-operations/registry/timeframes`, Requirement 11.8) is deliberately
 * **not** here: it is a registry projection served behind the same `ETag` / `304` /
 * `Cache-Control` machinery as `/registry/blocks`, so it is loaded by `lib/registryClient.js`,
 * which already implements exactly that. Two market controls, two sources, one client each.
 *
 * Why this calls the shared axios instance rather than `apiClient`'s `get()` helper
 * -------------------------------------------------------------------------------
 * Same reason `lib/registryClient.js` does. `get()` adds three behaviours that are wrong for
 * a selector:
 *
 * 1. **It retries 5xx three times with exponential backoff.** A `503
 *    ASSET_UNIVERSE_UNAVAILABLE` is a *deliberate, immediate* answer carrying its own
 *    `Retry-After`; retrying it four times would cost about seven seconds before the honest
 *    "asset list unavailable" state could render, and would trip the circuit breaker after
 *    five of them — replacing the backend's stated retry interval with a client-invented
 *    503 that says something else.
 * 2. **It caches responses for 60 s and de-duplicates by URL.** A cursor page is
 *    deterministic, but a *search* result served from a 60 s cache after the universe
 *    refreshed is a page the server would no longer serve, and `universe_changed` could not
 *    report it.
 * 3. **It returns `response.data` only.** `Retry-After` and `X-Asset-Universe-Hash` are
 *    headers, and the first of them is the figure the error state has to state.
 *
 * The shared instance still carries the session bearer token through its request
 * interceptor and still normalises errors into `ApiError` through its response interceptor,
 * so authentication and error shape are unchanged (constraint: both endpoints are
 * authenticated; no unauthenticated fetch is introduced).
 */

import client from '../../apiClient';

/** The canonical, record-bearing, paginated discovery endpoint. */
export const ASSETS_PATH = '/api/strategy-operations/assets';

/**
 * The server's own page-size ceiling (`Query(50, ge=1, le=500)`). Mirrored so a caller is
 * refused here with a named reason instead of by a 422 it has to decode.
 */
export const ASSET_LIMIT_CEILING = 500;

/** The server's default page size, stated so a caller can see what omitting `limit` means. */
export const ASSET_LIMIT_DEFAULT = 50;

const clampLimit = (limit) => {
  if (limit === null || limit === undefined) return undefined;
  const parsed = Number(limit);
  if (!Number.isFinite(parsed)) return undefined;
  return Math.min(ASSET_LIMIT_CEILING, Math.max(1, Math.trunc(parsed)));
};

const trimmed = (value) => {
  if (typeof value !== 'string') return undefined;
  const text = value.trim();
  return text === '' ? undefined : text;
};

/**
 * Build the query string the endpoint declares.
 *
 * An absent filter is **omitted**, never sent empty: `?quote=` would be a 32-character
 * bounded string the server matches exactly, so sending it would ask for markets quoted in
 * the empty string. `active_only` is sent only when the caller states it, so omitting it
 * inherits the server's documented default (`true`) rather than a second default here.
 *
 * Exported for assertion: the params a filter set turns into is the part that decides
 * whether a cursor stays valid, so it is checkable without a request.
 */
export const assetQueryParams = ({
  search,
  base,
  quote,
  marketType,
  activeOnly,
  limit,
  cursor,
} = {}) => {
  const params = {};
  const searchText = trimmed(search);
  if (searchText !== undefined) params.search = searchText;
  const baseText = trimmed(base);
  if (baseText !== undefined) params.base = baseText;
  const quoteText = trimmed(quote);
  if (quoteText !== undefined) params.quote = quoteText;
  const marketTypeText = trimmed(marketType);
  if (marketTypeText !== undefined) params.market_type = marketTypeText;
  if (typeof activeOnly === 'boolean') params.active_only = activeOnly;
  const pageSize = clampLimit(limit);
  if (pageSize !== undefined) params.limit = pageSize;
  const cursorText = trimmed(cursor);
  if (cursorText !== undefined) params.cursor = cursorText;
  return params;
};

export const assetsApi = {
  /**
   * One page of the tradeable market universe.
   *
   * @param {object}  [query]
   * @param {string}  [query.search]     Free-text over symbol, base and quote.
   * @param {string}  [query.base]       Base currency, exact.
   * @param {string}  [query.quote]      Quote currency, exact.
   * @param {string}  [query.marketType] `spot`, `swap` or `future`.
   * @param {boolean} [query.activeOnly] Omit to inherit the server default (`true`).
   * @param {number}  [query.limit]      Page size, clamped to `ASSET_LIMIT_CEILING`.
   * @param {string}  [query.cursor]     A previous page's `next_cursor`. **Keyset**, and
   *   pinned to the filter set it was cut under: continuing it under different filters is a
   *   `422 ASSET_CURSOR_INVALID`, not a silent walk through a different result set.
   * @param {object}  [options]
   * @param {AbortSignal} [options.signal] Aborts a superseded request, so a debounced search
   *   cannot have an older page land after a newer one.
   * @returns {Promise<{status: number, data: object, universeHash: string|null,
   *   retryAfter: string|null}>} Rejects with `apiClient`'s `ApiError`, carrying `status` and
   *   the normalised error body on `data`, which is where the backend's own
   *   `ASSET_UNIVERSE_UNAVAILABLE` / `ASSET_CURSOR_INVALID` code and message live.
   */
  discover: async (query = {}, options = {}) => {
    const response = await client.get(ASSETS_PATH, {
      params: assetQueryParams(query),
      headers: { Accept: 'application/json' },
      signal: options.signal,
    });
    return {
      status: response?.status ?? 200,
      data: response?.data ?? null,
      universeHash: readHeader(response?.headers, 'x-asset-universe-hash'),
      retryAfter: readHeader(response?.headers, 'retry-after'),
    };
  },
};

/** Read one response header across axios' `AxiosHeaders`, a plain object, or a `Headers`. */
function readHeader(headers, name) {
  if (!headers) return null;
  if (typeof headers.get === 'function') {
    const viaGetter = headers.get(name);
    if (viaGetter !== undefined && viaGetter !== null) return viaGetter;
  }
  const lower = name.toLowerCase();
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === lower) return headers[key];
  }
  return null;
}

export default assetsApi;
