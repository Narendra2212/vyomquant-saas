/**
 * assetUniverse.js — the projection and the failure vocabulary for asset discovery.
 * Task 7.3, Requirements 11.2, 11.3, 11.4, 11.7.
 *
 * Pure. No React, no network, no state. Everything here is a function of one response, so
 * every rule below is assertable without a DOM and without a server:
 *
 *   * `normaliseAsset`  — one `AssetRef` from the wire into the shape the control renders
 *   * `describeAsset`   — the figures the endpoint published, as text, with absent ≠ zero
 *   * `readAssetPage`   — one page, validated all-or-nothing
 *   * `classifyAssetError` — the backend's own error code and message, rendered verbatim
 *   * `canonicalFilters` / `filterFingerprint` — what "the same query" means for a cursor
 *
 * There is no fallback list in this file, and there must never be one
 * -------------------------------------------------------------------
 * SB-06 was a silent `"BTC/USDT"`; `GET /api/market/symbols` used to fall back to ten
 * hardcoded pairs when its exchange call failed, and `contexts/DataPipelineContext.jsx` used
 * to keep a second copy of that same list for when the endpoint failed. Task 7.2 deleted the
 * backend's; this task deletes the client's. So a failure here yields **zero assets and a
 * stated reason**, never a substitute, and the two honest-but-different outcomes are kept
 * distinguishable, because a client that conflates them tells the author the wrong thing:
 *
 *   * `total: 0` with a `200` — "no market matched *your filters*", a real answer to a real
 *     query, and the fix is to change the filters;
 *   * `503 ASSET_UNIVERSE_UNAVAILABLE` — "the platform does not currently know what it can
 *     trade", and the fix is to retry after the interval the server stated.
 *
 * An empty selector rendered for the second case would read as "this platform trades
 * nothing", which is the same untruth as the hardcoded list wearing different clothes.
 *
 * Absent is not zero
 * ------------------
 * `min_notional`, `min_amount`, `price_precision` and `amount_precision` are the exchange's
 * own figures and are `null` where the venue published none (task 7.1). `describeAsset`
 * keeps that distinction in the text: an unpublished minimum reads "not published", never
 * `0`, because a later order-size check that read `0` as "no minimum" would size an order
 * against a limit nobody stated.
 */

/** The observable states of one asset query. Same vocabulary as `REGISTRY_STATES`. */
export const ASSET_QUERY_STATES = Object.freeze({
  IDLE: 'idle',
  LOADING: 'loading',
  READY: 'ready',
  ERROR: 'error',
});

/** The backend's own error codes, named so a caller keys on the code and not on a status. */
export const ASSET_ERROR_CODES = Object.freeze({
  UNIVERSE_UNAVAILABLE: 'ASSET_UNIVERSE_UNAVAILABLE',
  CURSOR_INVALID: 'ASSET_CURSOR_INVALID',
  UNAUTHENTICATED: 'ASSET_UNAUTHENTICATED',
  FORBIDDEN: 'ASSET_FORBIDDEN',
  RATE_LIMITED: 'ASSET_RATE_LIMITED',
  REQUEST_FAILED: 'ASSET_REQUEST_FAILED',
  UNAVAILABLE: 'ASSET_UNAVAILABLE',
  NETWORK_ERROR: 'ASSET_NETWORK_ERROR',
  MALFORMED: 'ASSET_MALFORMED',
});

/** The text shown for a figure the venue did not publish. Never `0`, never blank. */
export const NOT_PUBLISHED_TEXT = 'not published';

/**
 * Every failure an asset query can report.
 *
 * `message` is the **backend's own sentence** wherever the backend sent one, rendered
 * verbatim — the rule `ParameterForm` follows for `fix_hint` and `NodePreview` for
 * `detail.message` (Requirement 8.9). The client authors a sentence only where the backend
 * could not have one: a network error it never received, and a payload that did not validate.
 */
export class AssetQueryError extends Error {
  constructor(
    code,
    message,
    {
      status = null,
      retryable = true,
      authExpired = false,
      retryAfterSeconds = null,
      backendAuthored = false,
      details = {},
    } = {},
  ) {
    super(message);
    this.name = 'AssetQueryError';
    this.code = code;
    this.status = status;
    this.retryable = retryable;
    this.authExpired = authExpired;
    /** The server's stated retry interval, or `null`. Never invented. */
    this.retryAfterSeconds = retryAfterSeconds;
    /** True when `message` came off the wire. Recorded so a test can assert it did. */
    this.backendAuthored = backendAuthored;
    this.details = details;
  }

  /** True for the one failure the pager recovers from by restarting rather than by looping. */
  get isCursorInvalid() {
    return this.code === ASSET_ERROR_CODES.CURSOR_INVALID;
  }

  /** True when the platform cannot say what it trades — the state that must never be empty. */
  get isUniverseUnavailable() {
    return this.code === ASSET_ERROR_CODES.UNIVERSE_UNAVAILABLE;
  }
}

const isPlainObject = (value) =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const nonEmptyString = (value) =>
  typeof value === 'string' && value.trim() !== '' ? value.trim() : null;

const finiteNumber = (value) =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

const malformed = (message, details = {}) =>
  new AssetQueryError(ASSET_ERROR_CODES.MALFORMED, message, {
    status: 200,
    retryable: true,
    details,
  });

/** The normalised error body `create_api_error_response` produces, or the raw detail dict. */
const errorBody = (error) => {
  const data = error?.data ?? error?.response?.data ?? null;
  if (!isPlainObject(data)) return {};
  // `main.py`'s handler puts the raiser's own dict on `detail` and lifts its `error` and
  // `message` to the top level. Both shapes are read, so this works against the normalised
  // body and against an un-normalised `detail` alike.
  const detail = isPlainObject(data.detail) ? data.detail : {};
  return { ...detail, ...data, nested: detail };
};

const codeFrom = (body) =>
  nonEmptyString(body.error) || nonEmptyString(body.error_code) || nonEmptyString(body.nested?.error);

const messageFrom = (body) =>
  nonEmptyString(body.message) ||
  nonEmptyString(body.nested?.message) ||
  (typeof body.detail === 'string' ? nonEmptyString(body.detail) : null);

const retryAfterFrom = (body, header) => {
  const stated =
    finiteNumber(body.retry_after_seconds) ??
    finiteNumber(body.nested?.retry_after_seconds) ??
    finiteNumber(body.details?.retry_after_seconds);
  if (stated !== null) return stated;
  const fromHeader = Number(header);
  return Number.isFinite(fromHeader) && fromHeader >= 0 ? fromHeader : null;
};

/**
 * Classify a rejection from `assetsApi.discover`.
 *
 * The **code takes precedence over the status**, because the backend states it: a 503 is
 * `ASSET_UNIVERSE_UNAVAILABLE` because the body says so, and a 422 is `ASSET_CURSOR_INVALID`
 * because the body says so. A status-only classifier would put a future 422 with a different
 * cause on the cursor-restart path, which is exactly the sort of accidental loop the restart
 * rule exists to avoid.
 *
 * @param {Error} error The `ApiError` from `apiClient`, or an `AssetQueryError` passed through.
 * @param {{retryAfter?: string|null}} [context] `Retry-After` off the response, if read.
 */
export const classifyAssetError = (error, context = {}) => {
  if (error instanceof AssetQueryError) return error;

  const status = error?.status ?? error?.response?.status ?? null;
  const body = errorBody(error);
  const declaredCode = codeFrom(body);
  const backendMessage = messageFrom(body);
  const retryAfterSeconds = retryAfterFrom(body, context.retryAfter);
  const details = { status, code: declaredCode, body };

  const authored = (fallback) => ({
    message: backendMessage || fallback,
    backendAuthored: backendMessage !== null,
  });

  if (declaredCode === ASSET_ERROR_CODES.CURSOR_INVALID || (status === 422 && declaredCode === null)) {
    const { message, backendAuthored } = authored(
      'The continuation cursor is no longer valid for this query.',
    );
    return new AssetQueryError(ASSET_ERROR_CODES.CURSOR_INVALID, message, {
      status,
      retryable: true,
      backendAuthored,
      details,
    });
  }

  if (declaredCode === ASSET_ERROR_CODES.UNIVERSE_UNAVAILABLE) {
    const { message, backendAuthored } = authored(
      'The tradeable market universe is not available yet.',
    );
    return new AssetQueryError(ASSET_ERROR_CODES.UNIVERSE_UNAVAILABLE, message, {
      status,
      retryable: true,
      retryAfterSeconds,
      backendAuthored,
      details,
    });
  }

  if (status === 401) {
    const { message, backendAuthored } = authored(
      'The session is no longer authenticated. Signing in again is the fix, not retrying.',
    );
    return new AssetQueryError(ASSET_ERROR_CODES.UNAUTHENTICATED, message, {
      status,
      retryable: false,
      authExpired: true,
      backendAuthored,
      details,
    });
  }

  if (status === 403) {
    const { message, backendAuthored } = authored('This account is not entitled to asset discovery.');
    return new AssetQueryError(ASSET_ERROR_CODES.FORBIDDEN, message, {
      status,
      retryable: false,
      backendAuthored,
      details,
    });
  }

  if (status === 429) {
    const { message, backendAuthored } = authored(
      'Asset discovery is rate limited. Wait a moment and retry.',
    );
    return new AssetQueryError(ASSET_ERROR_CODES.RATE_LIMITED, message, {
      status,
      retryable: true,
      retryAfterSeconds,
      backendAuthored,
      details,
    });
  }

  if (status !== null && status >= 500) {
    const { message, backendAuthored } = authored(
      `Asset discovery is unavailable (HTTP ${status}). No substitute market list is shown.`,
    );
    return new AssetQueryError(ASSET_ERROR_CODES.UNAVAILABLE, message, {
      status,
      retryable: true,
      retryAfterSeconds,
      backendAuthored,
      details,
    });
  }

  if (status !== null) {
    const { message, backendAuthored } = authored(`The asset discovery request failed (HTTP ${status}).`);
    return new AssetQueryError(ASSET_ERROR_CODES.REQUEST_FAILED, message, {
      status,
      retryable: true,
      backendAuthored,
      details,
    });
  }

  return new AssetQueryError(
    ASSET_ERROR_CODES.NETWORK_ERROR,
    // The one client-authored sentence on this path: no response arrived, so the backend
    // cannot have supplied one.
    `Asset discovery could not be reached: ${error?.message || 'network error'}`,
    { status: null, retryable: true, details },
  );
};

/**
 * One `AssetRef` from the wire.
 *
 * Throws on a record with no `symbol`: a nameless option in a market selector is
 * unselectable and would be an unexplained gap in a list whose length the author can see.
 * Every other field is carried through as sent, `null` included, and nothing is defaulted —
 * `market_type` is not guessed from the symbol, `active` is not assumed true.
 */
export const normaliseAsset = (record) => {
  if (!isPlainObject(record)) {
    throw malformed('An asset record is not an object', { received: typeof record });
  }
  const symbol = nonEmptyString(record.symbol);
  if (symbol === null) {
    throw malformed("An asset record carries no 'symbol'", { received: record });
  }
  const availableOn = Array.isArray(record.available_on)
    ? record.available_on.map((venue) => String(venue))
    : [];
  return Object.freeze({
    symbol,
    base: nonEmptyString(record.base),
    quote: nonEmptyString(record.quote),
    marketType: nonEmptyString(record.market_type),
    // Tri-state on purpose: `null` means the record did not say, which is not `false`.
    active: typeof record.active === 'boolean' ? record.active : null,
    pricePrecision: finiteNumber(record.price_precision),
    amountPrecision: finiteNumber(record.amount_precision),
    minNotional: finiteNumber(record.min_notional),
    minAmount: finiteNumber(record.min_amount),
    availableOn: Object.freeze(availableOn),
    precisionSource: nonEmptyString(record.precision_source),
    listingCount: finiteNumber(record.listing_count) ?? availableOn.length,
  });
};

const figure = (value) => (value === null ? NOT_PUBLISHED_TEXT : String(value));

/**
 * The one-line detail shown under an asset's symbol: what the venue published, in words.
 *
 * `listing_count` is reported as "listed on N venues" and never as a rank or a liquidity
 * figure — task 7.1 chose that field precisely because it is a checkable count and *not* the
 * `liquidity_rank` the design's pseudocode named but no adapter can supply.
 */
export const describeAsset = (asset) => {
  const parts = [];
  if (asset.marketType) parts.push(asset.marketType);
  if (asset.active === false) parts.push('inactive');
  if (asset.active === null) parts.push('active state not published');
  parts.push(`min notional ${figure(asset.minNotional)}`);
  parts.push(`min amount ${figure(asset.minAmount)}`);
  parts.push(
    `precision ${figure(asset.pricePrecision)}/${figure(asset.amountPrecision)}` +
      (asset.precisionSource ? ` from ${asset.precisionSource}` : ''),
  );
  parts.push(
    asset.listingCount === 1
      ? 'listed on 1 venue'
      : `listed on ${asset.listingCount} venues`,
  );
  return parts.join(' · ');
};

/**
 * One discovery page, validated all-or-nothing.
 *
 * A page whose `assets` is not a list, or one of whose records has no symbol, is an error
 * rather than a shorter page: silently dropping a record would make the selector's contents
 * disagree with the `total` beside it, which is the shape of defect SB-03 had.
 */
export const readAssetPage = (payload) => {
  if (!isPlainObject(payload)) {
    throw malformed('The asset discovery response is not an object', {
      received: typeof payload,
    });
  }
  if (!Array.isArray(payload.assets)) {
    throw malformed("The asset discovery response carries no 'assets' list", {
      received: payload.assets,
    });
  }
  const total = finiteNumber(payload.total);
  if (total === null) {
    throw malformed("The asset discovery response carries no numeric 'total'", {
      received: payload.total,
    });
  }

  const sourceMeta = isPlainObject(payload.source_meta) ? payload.source_meta : {};

  return Object.freeze({
    assets: Object.freeze(payload.assets.map(normaliseAsset)),
    /** Everything matching the filters, not this page (Requirement 11.3). */
    total,
    limit: finiteNumber(payload.limit),
    /** Keyset, and pinned to the filter set it was cut under. `null` on the last page. */
    nextCursor: nonEmptyString(payload.next_cursor),
    /** The server stating that the universe moved while this query was being paged. */
    universeChanged: payload.universe_changed === true,
    stale: sourceMeta.stale === true,
    universeHash: nonEmptyString(sourceMeta.universe_hash),
    universeTotal: finiteNumber(sourceMeta.universe_total),
    ageSeconds: finiteNumber(sourceMeta.age_seconds),
    exchanges: Object.freeze(
      Array.isArray(sourceMeta.exchanges) ? sourceMeta.exchanges.map(String) : [],
    ),
    exchangesFailed: Object.freeze(
      Array.isArray(sourceMeta.exchanges_failed) ? sourceMeta.exchanges_failed.map(String) : [],
    ),
    sourceMeta: Object.freeze({ ...sourceMeta }),
  });
};

/** The filter keys a cursor is pinned to, in the order the fingerprint reads them. */
export const ASSET_FILTER_KEYS = Object.freeze([
  'search',
  'base',
  'quote',
  'marketType',
  'activeOnly',
  'limit',
]);

/**
 * The filter set, canonicalised.
 *
 * Text filters are trimmed and upper-cased for `base` / `quote`, which the endpoint matches
 * exactly against exchange currency codes; `search` keeps its case because the endpoint
 * matches it case-insensitively and echoing the author's typing back is friendlier.
 */
export const canonicalFilters = (filters = {}) => {
  const text = (value) => {
    const value_ = typeof value === 'string' ? value.trim() : '';
    return value_ === '' ? null : value_;
  };
  const code = (value) => {
    const value_ = text(value);
    return value_ === null ? null : value_.toUpperCase();
  };
  return Object.freeze({
    search: text(filters.search),
    base: code(filters.base),
    quote: code(filters.quote),
    marketType: text(filters.marketType),
    activeOnly: typeof filters.activeOnly === 'boolean' ? filters.activeOnly : true,
    limit: finiteNumber(filters.limit),
  });
};

/**
 * A stable string identity for a filter set.
 *
 * This is what "the same query" means for a cursor. The server pins each cursor to its
 * filters and answers `422 ASSET_CURSOR_INVALID` when one is continued under different ones,
 * so the client's job is to never send that request: when this fingerprint changes, the
 * cursor is dropped and the query restarts from page one. The 422 handler exists for the
 * case this cannot cover — a cursor that was already in flight, or one the server rejects for
 * a reason the client does not model — and not as the normal path.
 */
export const filterFingerprint = (filters = {}) => {
  const canonical = canonicalFilters(filters);
  return ASSET_FILTER_KEYS.map((key) => `${key}=${canonical[key] === null ? '' : canonical[key]}`).join(
    '\u0000',
  );
};

/**
 * Append `incoming` to `existing`, dropping a symbol already held.
 *
 * Keyset pages over one universe are disjoint, so this normally changes nothing. It matters
 * when the universe refreshed mid-page — the case the server reports as `universe_changed` —
 * where a record can legitimately appear twice. Showing one market twice in a selector is a
 * small thing that makes the whole list look untrustworthy, and the count of duplicates is
 * returned rather than swallowed so a caller can state that the list shifted.
 */
export const mergeAssetPages = (existing = [], incoming = []) => {
  const seen = new Set(existing.map((asset) => asset.symbol));
  const merged = [...existing];
  let duplicates = 0;
  for (const asset of incoming) {
    if (seen.has(asset.symbol)) {
      duplicates += 1;
      continue;
    }
    seen.add(asset.symbol);
    merged.push(asset);
  }
  return { assets: merged, duplicates };
};

/**
 * The `market_type` filter's options, read from the DATA block's own `market_type`
 * `ParamSpec` — never from a list written here.
 *
 * The descriptor publishes `options := ["spot", "swap", "future"]` and the endpoint accepts
 * exactly those three, so the descriptor is the vocabulary. A client-side copy would be the
 * same defect class as a client-side symbol list, one field over: it would keep working right
 * up to the day the platform gained a fourth market type or dropped one.
 */
export const marketTypeOptionsFrom = (params = []) => {
  const spec = (params || []).find((entry) => entry && entry.key === 'market_type');
  if (!spec || !Array.isArray(spec.options)) return Object.freeze([]);
  return Object.freeze(spec.options.filter((option) => typeof option === 'string' && option !== ''));
};
