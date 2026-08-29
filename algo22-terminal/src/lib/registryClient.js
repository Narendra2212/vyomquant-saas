/**
 * registryClient.js — the only source of block descriptors and of the published timeframe
 * set in the frontend.
 *
 * Talks to `GET /api/strategy-operations/registry/blocks`
 * (`backend_app/routers/strategy_operations.py`), whose body is
 * `BlockRegistry.to_dict()` verbatim:
 *
 *   { registry_version, registry_schema_version, port_types,
 *     categories: [{ id, display_name, order }],   // ascending, gapless, all seven
 *     blocks: [BlockDescriptor],                   // served in category order
 *     compatibility_matrix: { PORT_TYPE: [PORT_TYPE, …] } }
 *
 * Fail closed — the whole point of this module
 * -------------------------------------------
 * SB-03 (an always-empty FEATURE_ENGINEERING palette) and SB-04 (`wma`, `hma`, `catboost`
 * and `autoencoder` unselectable) were both caused by a hand-maintained local block list
 * drifting from what the engine can actually run. A local list consulted "just when the
 * fetch fails" is that same defect with better manners: it would put the drifted answer back
 * on screen at exactly the moment nobody can check it.
 *
 * So on **any** failure — network, 401, 403, 429, 4xx, 5xx, or a payload that does not
 * validate — this module moves to `error` and reports **zero blocks**. There is no bundled
 * default, and no stale copy survives an error: the cached payload and its entity tag are
 * dropped, so a retry is a real fetch and cannot serve a registry the backend has since
 * disowned (Requirement 4.12). The only path that reuses a cached payload is a `304`, which
 * is the backend *stating* the copy is current.
 *
 * A `401` is reported as its own code with `authExpired` set. An expired session and a
 * broken registry are different problems with different fixes, and a palette that conflates
 * them tells the user to retry when they need to log in (Requirement 21.1).
 *
 * Caching and concurrency
 * -----------------------
 * * The `ETag` from a `200` is stored and sent back as `If-None-Match`, so an unchanged
 *   registry costs a `304` with no body (Requirement 4.15).
 * * One in-flight request at a time. Every concurrent caller — palette, inspector,
 *   connection checks — joins the same promise, so N components cost one fetch.
 *
 * The timeframe set (task 7.3, Requirement 11.8)
 * ----------------------------------------------
 * `GET /api/strategy-operations/registry/timeframes` is served by the same router, behind
 * the same `_registry_response` — same `ETag`, same `304`, same `Cache-Control` — so it is
 * loaded here rather than by a second module that would reimplement all of it. What it
 * publishes is the **intersection** of the data pipeline's own vocabularies (task 7.2):
 *
 *   { registry_version, registry_schema_version, timeframes: [{ id, label, seconds }],
 *     sources: [String], total }
 *
 * The two caches are independent — separate payload, entity tag, in-flight promise, state
 * and listeners — because a broken block registry is not a reason to withhold a timeframe
 * set, or the reverse. Everything else is shared: one transport, one conditional-GET, one
 * error classifier, one `REGISTRY_*` code vocabulary. `RegistryError.resource` says which
 * resource an error belongs to.
 *
 * The same anti-fallback rule applies, for the same reason. `ParamSpec` publishes the DATA
 * block's `timeframe` with **no** `options` tuple (`required=True, default=None`, SB-06), so
 * this endpoint is the only source of that vocabulary. A client-side timeframe list would be
 * SB-03 in a second location: `3m` is deliberately *not* published, because the market-data
 * coverage gate has no figure for it, and a hand-written list would put it back on screen.
 * So on any failure this module reports zero timeframes and an error — never a written-out
 * list, and never a silently empty selector presented as if the platform supported nothing.
 *
 * Auth
 * ----
 * Requests go through the shared axios instance (`src/apiClient.js`), whose request
 * interceptor attaches the session bearer token. No token is read, stored or forwarded here.
 */

import client from '../apiClient';

/** Registry endpoint root. The router is mounted at `/api` in `backend_app/main.py`. */
export const REGISTRY_BASE_PATH = '/api/strategy-operations/registry';

/** The full registry: all seven categories, in palette order. */
export const REGISTRY_BLOCKS_PATH = `${REGISTRY_BASE_PATH}/blocks`;

/** The bar intervals every stage of the data pipeline can process (Requirement 11.8). */
export const REGISTRY_TIMEFRAMES_PATH = `${REGISTRY_BASE_PATH}/timeframes`;

/** Which registry resource a load, snapshot or error belongs to. */
export const REGISTRY_RESOURCES = Object.freeze({
  BLOCKS: 'blocks',
  TIMEFRAMES: 'timeframes',
});

/** The client's observable states. */
export const REGISTRY_STATES = Object.freeze({
  IDLE: 'idle',
  LOADING: 'loading',
  READY: 'ready',
  ERROR: 'error',
});

/**
 * Every failure this module reports. `code` is machine-readable, `retryable` says whether
 * retrying the same request can plausibly succeed, and `authExpired` separates a dead
 * session from a broken registry.
 */
export class RegistryError extends Error {
  constructor(
    code,
    message,
    {
      status = null,
      retryable = true,
      authExpired = false,
      details = {},
      resource = REGISTRY_RESOURCES.BLOCKS,
    } = {},
  ) {
    super(message);
    this.name = 'RegistryError';
    this.code = code;
    this.status = status;
    this.retryable = retryable;
    this.authExpired = authExpired;
    this.details = details;
    /** `blocks` or `timeframes` — which resource failed, so a caller need not guess. */
    this.resource = resource;
  }
}

const isPlainObject = (value) =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const nonEmptyString = (value) =>
  typeof value === 'string' && value.trim() !== '' ? value : null;

const malformed = (message, details = {}, resource = REGISTRY_RESOURCES.BLOCKS) =>
  new RegistryError('REGISTRY_MALFORMED', message, { status: 200, details, resource });

/** Read one response header across axios' `AxiosHeaders`, a plain object, or a `Headers`. */
const readHeader = (headers, name) => {
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
};

/**
 * Validate the served payload, or throw.
 *
 * Deliberately all-or-nothing. A partially-populated registry is the worst of the available
 * outcomes: the palette would look like it worked while silently missing blocks, which is
 * indistinguishable from SB-03 to the person using it. A payload that does not validate is
 * an error with zero blocks, same as a 500.
 *
 * An empty `blocks` list is treated as malformed rather than as an empty registry. The
 * backend guarantees every category is populated and answers `503` when assembly fails, so
 * a `200` carrying no blocks is a lie no client should render.
 */
const validatePayload = (payload) => {
  if (!isPlainObject(payload)) {
    throw malformed('The registry response is not an object', { received: typeof payload });
  }

  const registryVersion = nonEmptyString(payload.registry_version);
  if (registryVersion === null) {
    throw malformed("The registry response carries no 'registry_version'", {
      received: payload.registry_version,
    });
  }

  if (!Array.isArray(payload.port_types) || payload.port_types.length === 0) {
    throw malformed("The registry response carries no 'port_types' vocabulary", {
      received: payload.port_types,
    });
  }
  const portTypes = payload.port_types.map((portType) => {
    const value = nonEmptyString(portType);
    if (value === null) {
      throw malformed("A 'port_types' entry is not a non-empty string", { received: portType });
    }
    return value;
  });

  if (!Array.isArray(payload.categories) || payload.categories.length === 0) {
    throw malformed("The registry response carries no 'categories'", {
      received: payload.categories,
    });
  }
  const categories = payload.categories.map((category) => {
    if (!isPlainObject(category)) {
      throw malformed("A 'categories' entry is not an object", { received: category });
    }
    const id = nonEmptyString(category.id);
    if (id === null) {
      throw malformed("A 'categories' entry carries no 'id'", { received: category });
    }
    if (typeof category.order !== 'number' || !Number.isFinite(category.order)) {
      throw malformed(`Category '${id}' carries no numeric 'order'`, {
        category: id,
        received: category.order,
      });
    }
    return Object.freeze({
      id,
      display_name: nonEmptyString(category.display_name) || id,
      order: category.order,
    });
  });

  const categoryIds = new Set(categories.map((category) => category.id));
  if (categoryIds.size !== categories.length) {
    throw malformed('The registry response serves a duplicate category id', {
      served: categories.map((category) => category.id),
    });
  }

  if (!Array.isArray(payload.blocks)) {
    throw malformed("The registry response carries no 'blocks' list", {
      received: payload.blocks,
    });
  }
  if (payload.blocks.length === 0) {
    throw new RegistryError(
      'REGISTRY_EMPTY',
      'The registry served zero blocks. Every category is guaranteed to be populated, so ' +
        'an empty catalogue is a failed assembly rather than an empty palette (SB-03).',
      { status: 200, retryable: true },
    );
  }

  const seenBlockIds = new Set();
  const blocks = payload.blocks.map((block) => {
    if (!isPlainObject(block)) {
      throw malformed("A 'blocks' entry is not an object", { received: block });
    }
    const blockId = nonEmptyString(block.block_id);
    if (blockId === null) {
      throw malformed("A block descriptor carries no 'block_id'", { received: block });
    }
    if (seenBlockIds.has(blockId)) {
      throw malformed(`The registry serves block '${blockId}' twice`, { block_id: blockId });
    }
    seenBlockIds.add(blockId);

    const category = nonEmptyString(block.category);
    if (category === null) {
      throw malformed(`Block '${blockId}' carries no 'category'`, { block_id: blockId });
    }
    if (!categoryIds.has(category)) {
      throw malformed(
        `Block '${blockId}' is in category '${category}', which the response does not serve`,
        { block_id: blockId, category, served: [...categoryIds] },
      );
    }

    for (const field of ['inputs', 'outputs', 'params']) {
      if (!Array.isArray(block[field])) {
        throw malformed(`Block '${blockId}' field '${field}' is not a list`, {
          block_id: blockId,
          field,
          received: typeof block[field],
        });
      }
    }
    return block;
  });

  if (!isPlainObject(payload.compatibility_matrix)) {
    throw malformed("The registry response carries no 'compatibility_matrix'", {
      received: payload.compatibility_matrix,
    });
  }
  for (const [sourceType, targets] of Object.entries(payload.compatibility_matrix)) {
    if (!Array.isArray(targets)) {
      throw malformed(`compatibility_matrix['${sourceType}'] is not a list`, {
        port_type: sourceType,
        received: typeof targets,
      });
    }
  }

  return {
    registryVersion,
    registrySchemaVersion:
      payload.registry_schema_version === undefined ? null : payload.registry_schema_version,
    portTypes: Object.freeze(portTypes),
    categories: Object.freeze([...categories].sort((a, b) => a.order - b.order)),
    blocks: Object.freeze(blocks),
    compatibilityMatrix: Object.freeze(payload.compatibility_matrix),
  };
};

/**
 * Validate the served timeframe set, or throw.
 *
 * All-or-nothing, for the same reason `validatePayload` is: a partially-read timeframe set
 * would silently withhold intervals the pipeline supports, which is the SB-03 shape.
 *
 * An empty `timeframes` list is **malformed**, not an empty vocabulary. The backend refuses
 * to serve one — `_pipeline_timeframes` raises `503 TIMEFRAME_VOCABULARY_EMPTY` rather than
 * publish nothing — so a `200` carrying no interval is a lie, and rendering it as an empty
 * selector would read as "this platform trades on no interval".
 *
 * `seconds` is required and must be positive: it is the figure a caller uses as an expected
 * bar interval, and a missing or zero one would make a freshness check unfailable. Nothing
 * is computed from the label here — no parsing of `"15m"` into a duration — because the
 * endpoint already states the number and two derivations of one interval is one too many.
 */
const validateTimeframePayload = (payload) => {
  const resource = REGISTRY_RESOURCES.TIMEFRAMES;
  const bad = (message, details) => malformed(message, details, resource);

  if (!isPlainObject(payload)) {
    throw bad('The timeframe response is not an object', { received: typeof payload });
  }

  const registryVersion = nonEmptyString(payload.registry_version);
  if (registryVersion === null) {
    throw bad("The timeframe response carries no 'registry_version'", {
      received: payload.registry_version,
    });
  }

  if (!Array.isArray(payload.timeframes)) {
    throw bad("The timeframe response carries no 'timeframes' list", {
      received: payload.timeframes,
    });
  }
  if (payload.timeframes.length === 0) {
    throw new RegistryError(
      'REGISTRY_EMPTY',
      'The registry served zero timeframes. The backend refuses to publish an empty ' +
        'interval set, so an empty list is a failed assembly rather than a platform that ' +
        'supports no interval — and a written-out client list is not the answer to it.',
      { status: 200, retryable: true, resource },
    );
  }

  const seen = new Set();
  const timeframes = payload.timeframes.map((entry) => {
    if (!isPlainObject(entry)) {
      throw bad("A 'timeframes' entry is not an object", { received: entry });
    }
    const id = nonEmptyString(entry.id);
    if (id === null) {
      throw bad("A 'timeframes' entry carries no 'id'", { received: entry });
    }
    if (seen.has(id)) {
      throw bad(`The registry serves timeframe '${id}' twice`, { id });
    }
    seen.add(id);
    if (typeof entry.seconds !== 'number' || !Number.isFinite(entry.seconds) || entry.seconds <= 0) {
      throw bad(`Timeframe '${id}' carries no positive 'seconds'`, {
        id,
        received: entry.seconds,
      });
    }
    return Object.freeze({
      id,
      // The label is the endpoint's own; `id` stands in only when it is absent, which is
      // never in the served shape. Nothing here invents a prettier name for an interval.
      label: nonEmptyString(entry.label) || id,
      seconds: entry.seconds,
    });
  });

  if (!Array.isArray(payload.sources)) {
    throw bad("The timeframe response carries no 'sources' list", {
      received: payload.sources,
    });
  }

  return {
    registryVersion,
    registrySchemaVersion:
      payload.registry_schema_version === undefined ? null : payload.registry_schema_version,
    // Served order is bar duration ascending. Preserved, not re-sorted: re-sorting would
    // mean parsing the labels, which is the second derivation this avoids.
    timeframes: Object.freeze(timeframes),
    sources: Object.freeze(payload.sources.map((source) => String(source))),
  };
};

/**
 * Classify a transport failure. Nothing is retried here; the caller decides.
 *
 * One classifier and one `REGISTRY_*` code vocabulary for both resources: a caller that
 * needs to know *which* resource failed reads `error.resource`, and `details.url` records
 * the path, so the codes do not have to be doubled to stay traceable.
 */
const toRegistryError = (
  error,
  { path = REGISTRY_BLOCKS_PATH, resource = REGISTRY_RESOURCES.BLOCKS } = {},
) => {
  if (error instanceof RegistryError) return error;

  const status = error?.status ?? error?.response?.status ?? null;
  const details = { url: path };

  if (status === 401) {
    return new RegistryError(
      'REGISTRY_UNAUTHENTICATED',
      'The session is no longer authenticated. The registry itself may be perfectly healthy; ' +
        'signing in again is the fix, not retrying.',
      { status, retryable: false, authExpired: true, details, resource },
    );
  }
  if (status === 403) {
    return new RegistryError(
      'REGISTRY_FORBIDDEN',
      'This account is not entitled to the block registry.',
      { status, retryable: false, details, resource },
    );
  }
  if (status === 429) {
    return new RegistryError(
      'REGISTRY_RATE_LIMITED',
      'The registry endpoint is rate limited. Wait a moment and retry.',
      { status, retryable: true, details, resource },
    );
  }
  if (status !== null && status >= 500) {
    return new RegistryError(
      'REGISTRY_UNAVAILABLE',
      `The registry is unavailable (HTTP ${status}). ` +
        (resource === REGISTRY_RESOURCES.TIMEFRAMES
          ? 'The timeframe selector offers nothing until it answers; it never offers a ' +
            'local substitute.'
          : 'The palette shows no blocks until it answers; it never shows a local substitute.'),
      {
        status,
        retryable: true,
        details: { ...details, data: error?.data ?? error?.response?.data },
        resource,
      },
    );
  }
  if (status !== null) {
    return new RegistryError(
      'REGISTRY_REQUEST_FAILED',
      `The registry request failed (HTTP ${status}).`,
      {
        status,
        retryable: true,
        details: { ...details, data: error?.data ?? error?.response?.data },
        resource,
      },
    );
  }
  return new RegistryError(
    'REGISTRY_NETWORK_ERROR',
    `The registry could not be reached: ${error?.message || 'network error'}`,
    { status: null, retryable: true, details, resource },
  );
};

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

/** The validated payload, held only while the state is `ready`. Dropped on any error. */
let cached = null;

/** The `ETag` for `cached`. Dropped with it, so `If-None-Match` is never sent stale. */
let entityTag = null;

/** `block_id` → descriptor, rebuilt whenever `cached` changes. */
let descriptorIndex = new Map();

/** The single in-flight request, shared by every concurrent caller. */
let inflight = null;

let snapshot = null;

const listeners = new Set();

const EMPTY_LIST = Object.freeze([]);
const EMPTY_MATRIX = Object.freeze({});

const buildSnapshot = ({ state, error = null, fromCache = false, fetchedAt = null }) => {
  const ready = state === REGISTRY_STATES.READY && cached !== null;
  return Object.freeze({
    state,
    /** Zero blocks unless the state is `ready`. This is the anti-fallback guarantee. */
    blocks: ready ? cached.blocks : EMPTY_LIST,
    categories: ready ? cached.categories : EMPTY_LIST,
    portTypes: ready ? cached.portTypes : EMPTY_LIST,
    compatibilityMatrix: ready ? cached.compatibilityMatrix : EMPTY_MATRIX,
    registryVersion: ready ? cached.registryVersion : null,
    registrySchemaVersion: ready ? cached.registrySchemaVersion : null,
    error,
    fromCache,
    fetchedAt,
    isLoading: state === REGISTRY_STATES.LOADING,
    isReady: ready,
    isError: state === REGISTRY_STATES.ERROR,
  });
};

const setSnapshot = (next) => {
  snapshot = next;
  for (const listener of [...listeners]) {
    try {
      listener(snapshot);
    } catch (error) {
      console.error('[registryClient] A registry listener threw:', error);
    }
  }
  return snapshot;
};

/** Forget everything. Called on error (fail closed) and by `resetRegistryClient`. */
const dropCache = () => {
  cached = null;
  entityTag = null;
  descriptorIndex = new Map();
};

const adoptPayload = (validated) => {
  cached = validated;
  descriptorIndex = new Map(validated.blocks.map((block) => [block.block_id, block]));
};

snapshot = buildSnapshot({ state: REGISTRY_STATES.IDLE });

// ---------------------------------------------------------------------------
// Fetching
// ---------------------------------------------------------------------------

/**
 * One conditional GET, shared by both resources.
 *
 * Extracted rather than copied: the `If-None-Match`-only-when-a-copy-is-held rule and the
 * "a 304 is a success" rule are the two things easiest to get subtly wrong, and having them
 * in one place is what stops the timeframe loader from drifting from the block loader.
 */
const conditionalGet = async (path, { tag, hasCache, resource }) => {
  const headers = { Accept: 'application/json' };
  if (tag !== null && hasCache) headers['If-None-Match'] = tag;

  try {
    return await client.get(path, {
      headers,
      // 304 is a success here: it means the held copy is current. Axios rejects it by
      // default, which would turn a cache hit into a palette error.
      validateStatus: (status) => status === 200 || status === 304,
    });
  } catch (error) {
    throw toRegistryError(error, { path, resource });
  }
};

const requestRegistry = async () => {
  const previousTag = entityTag;
  const response = await conditionalGet(REGISTRY_BLOCKS_PATH, {
    tag: previousTag,
    hasCache: cached !== null,
    resource: REGISTRY_RESOURCES.BLOCKS,
  });

  const status = response?.status ?? 200;

  if (status === 304) {
    if (cached === null) {
      throw new RegistryError(
        'REGISTRY_NOT_MODIFIED_WITHOUT_CACHE',
        'The registry answered 304 for a copy this client does not hold. Nothing can be ' +
          'rendered from that, and no substitute is invented.',
        { status: 304, retryable: true },
      );
    }
    return { validated: cached, tag: previousTag, fromCache: true };
  }

  const validated = validatePayload(response?.data);
  const tag = nonEmptyString(readHeader(response?.headers, 'etag'));
  return { validated, tag, fromCache: false };
};

const runLoad = async () => {
  setSnapshot(buildSnapshot({ state: REGISTRY_STATES.LOADING }));
  try {
    const { validated, tag, fromCache } = await requestRegistry();
    adoptPayload(validated);
    entityTag = tag;
    return setSnapshot(
      buildSnapshot({ state: REGISTRY_STATES.READY, fromCache, fetchedAt: Date.now() }),
    );
  } catch (error) {
    const registryError = toRegistryError(error);
    // Fail closed: no stale payload, no stale validator, no blocks.
    dropCache();
    return setSnapshot(
      buildSnapshot({
        state: REGISTRY_STATES.ERROR,
        error: registryError,
        fetchedAt: Date.now(),
      }),
    );
  } finally {
    inflight = null;
  }
};

/**
 * Load the registry, or return the snapshot already held.
 *
 * Never rejects: the returned snapshot *is* the outcome, so a caller renders `error` the
 * same way it renders `ready` and cannot forget a `catch`.
 *
 * @param {{ revalidate?: boolean }} [options] `revalidate` re-asks the backend even when a
 *   ready copy is held; the held `ETag` travels as `If-None-Match`, so an unchanged registry
 *   costs a `304` and the cached payload is reused.
 * @returns {Promise<object>} The resulting snapshot.
 */
export function loadRegistry({ revalidate = false } = {}) {
  // Concurrent callers join the one request rather than each starting their own.
  if (inflight !== null) return inflight;
  if (!revalidate && snapshot.isReady) return Promise.resolve(snapshot);
  inflight = runLoad();
  return inflight;
}

/** Re-ask the backend, sending `If-None-Match` when a copy is held. */
export function refreshRegistry() {
  return loadRegistry({ revalidate: true });
}

/** The current state, synchronously. Blocks are empty unless `state` is `ready`. */
export function getRegistrySnapshot() {
  return snapshot;
}

/**
 * Observe state changes. Called on every transition, including the move to `loading`.
 * @returns {() => void} Unsubscribe.
 */
export function subscribe(listener) {
  if (typeof listener !== 'function') {
    throw new TypeError('registryClient.subscribe expects a function');
  }
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// ---------------------------------------------------------------------------
// Lookups — all empty unless the state is `ready`
// ---------------------------------------------------------------------------

/** The descriptor for `blockId`, or `null`. Never a guess and never a stub. */
export function getDescriptor(blockId) {
  if (!snapshot.isReady) return null;
  const descriptor = descriptorIndex.get(blockId);
  return descriptor === undefined ? null : descriptor;
}

/** Descriptors in `categoryId`, in served order. */
export function getBlocksByCategory(categoryId) {
  if (!snapshot.isReady) return EMPTY_LIST;
  return snapshot.blocks.filter((block) => block.category === categoryId);
}

/** The served categories, ascending by `order`. */
export function getCategories() {
  return snapshot.categories;
}

/**
 * The palette, ready to render: one entry per served category in `categories[].order`,
 * each carrying its descriptors in served order.
 */
export function getPaletteSections() {
  return snapshot.categories.map((category) =>
    Object.freeze({
      ...category,
      blocks: getBlocksByCategory(category.id),
    }),
  );
}

/** The port-type compatibility rules the backend enforces (Requirement 6.9). */
export function getCompatibilityMatrix() {
  return snapshot.compatibilityMatrix;
}

/** The served port-type vocabulary. */
export function getPortTypes() {
  return snapshot.portTypes;
}

/** The version of the registry currently held, or `null`. */
export function getRegistryVersion() {
  return snapshot.registryVersion;
}

// ---------------------------------------------------------------------------
// The published timeframe set (task 7.3, Requirement 11.8)
// ---------------------------------------------------------------------------
//
// A second, independent cache over the same transport. Independent because the two
// resources fail independently: a 503 from `/registry/blocks` says nothing about whether
// the data pipeline's timeframe vocabularies could be read, and conflating them would take
// the timeframe selector down with the palette (and the reverse).

/** The validated timeframe payload, held only while its state is `ready`. */
let cachedTimeframes = null;

/** The `ETag` for `cachedTimeframes`. Dropped with it. */
let timeframeTag = null;

/** The single in-flight timeframe request. */
let timeframeInflight = null;

let timeframeSnapshot = null;

const timeframeListeners = new Set();

const buildTimeframeSnapshot = ({ state, error = null, fromCache = false, fetchedAt = null }) => {
  const ready = state === REGISTRY_STATES.READY && cachedTimeframes !== null;
  return Object.freeze({
    state,
    /**
     * Zero timeframes unless the state is `ready`. This is the anti-fallback guarantee for
     * Requirement 11.8: there is no bundled interval list to fall back to, so a caller that
     * renders this array renders nothing when the endpoint has not answered — and has to say
     * why, because an empty selector on its own reads as a platform that trades on no
     * interval.
     */
    timeframes: ready ? cachedTimeframes.timeframes : EMPTY_LIST,
    /** Which pipeline vocabularies the served set was intersected from. */
    sources: ready ? cachedTimeframes.sources : EMPTY_LIST,
    registryVersion: ready ? cachedTimeframes.registryVersion : null,
    registrySchemaVersion: ready ? cachedTimeframes.registrySchemaVersion : null,
    error,
    fromCache,
    fetchedAt,
    isLoading: state === REGISTRY_STATES.LOADING,
    isReady: ready,
    isError: state === REGISTRY_STATES.ERROR,
  });
};

const setTimeframeSnapshot = (next) => {
  timeframeSnapshot = next;
  for (const listener of [...timeframeListeners]) {
    try {
      listener(timeframeSnapshot);
    } catch (error) {
      console.error('[registryClient] A timeframe listener threw:', error);
    }
  }
  return timeframeSnapshot;
};

const dropTimeframeCache = () => {
  cachedTimeframes = null;
  timeframeTag = null;
};

timeframeSnapshot = buildTimeframeSnapshot({ state: REGISTRY_STATES.IDLE });

const requestTimeframes = async () => {
  const previousTag = timeframeTag;
  const response = await conditionalGet(REGISTRY_TIMEFRAMES_PATH, {
    tag: previousTag,
    hasCache: cachedTimeframes !== null,
    resource: REGISTRY_RESOURCES.TIMEFRAMES,
  });

  const status = response?.status ?? 200;

  if (status === 304) {
    if (cachedTimeframes === null) {
      throw new RegistryError(
        'REGISTRY_NOT_MODIFIED_WITHOUT_CACHE',
        'The registry answered 304 for a timeframe set this client does not hold. Nothing ' +
          'can be rendered from that, and no substitute is invented.',
        { status: 304, retryable: true, resource: REGISTRY_RESOURCES.TIMEFRAMES },
      );
    }
    return { validated: cachedTimeframes, tag: previousTag, fromCache: true };
  }

  const validated = validateTimeframePayload(response?.data);
  const tag = nonEmptyString(readHeader(response?.headers, 'etag'));
  return { validated, tag, fromCache: false };
};

const runTimeframeLoad = async () => {
  setTimeframeSnapshot(buildTimeframeSnapshot({ state: REGISTRY_STATES.LOADING }));
  try {
    const { validated, tag, fromCache } = await requestTimeframes();
    cachedTimeframes = validated;
    timeframeTag = tag;
    return setTimeframeSnapshot(
      buildTimeframeSnapshot({ state: REGISTRY_STATES.READY, fromCache, fetchedAt: Date.now() }),
    );
  } catch (error) {
    const registryError = toRegistryError(error, {
      path: REGISTRY_TIMEFRAMES_PATH,
      resource: REGISTRY_RESOURCES.TIMEFRAMES,
    });
    // Fail closed: no stale set, no written-out list, no timeframes.
    dropTimeframeCache();
    return setTimeframeSnapshot(
      buildTimeframeSnapshot({
        state: REGISTRY_STATES.ERROR,
        error: registryError,
        fetchedAt: Date.now(),
      }),
    );
  } finally {
    timeframeInflight = null;
  }
};

/**
 * Load the published timeframe set, or return the snapshot already held. Never rejects.
 *
 * @param {{ revalidate?: boolean }} [options]
 * @returns {Promise<object>} The resulting timeframe snapshot.
 */
export function loadTimeframes({ revalidate = false } = {}) {
  if (timeframeInflight !== null) return timeframeInflight;
  if (!revalidate && timeframeSnapshot.isReady) return Promise.resolve(timeframeSnapshot);
  timeframeInflight = runTimeframeLoad();
  return timeframeInflight;
}

/** Re-ask the backend, sending `If-None-Match` when a copy is held. */
export function refreshTimeframes() {
  return loadTimeframes({ revalidate: true });
}

/** The current timeframe state, synchronously. Empty unless `state` is `ready`. */
export function getTimeframeSnapshot() {
  return timeframeSnapshot;
}

/**
 * Observe timeframe state changes.
 * @returns {() => void} Unsubscribe.
 */
export function subscribeTimeframes(listener) {
  if (typeof listener !== 'function') {
    throw new TypeError('registryClient.subscribeTimeframes expects a function');
  }
  timeframeListeners.add(listener);
  return () => timeframeListeners.delete(listener);
}

/** The served timeframe entries, ascending by bar duration. Empty unless `ready`. */
export function getTimeframes() {
  return timeframeSnapshot.timeframes;
}

/**
 * The `seconds` the endpoint published for `id`, or `null`.
 *
 * `null` for an unpublished label rather than a parsed guess: a caller measuring feed
 * freshness against a guessed interval is how a stale feed reads as fresh, and the backend
 * refuses to default an unpublished interval for exactly that reason.
 */
export function getTimeframeSeconds(id) {
  const entry = timeframeSnapshot.timeframes.find((timeframe) => timeframe.id === id);
  return entry === undefined ? null : entry.seconds;
}

/**
 * Drop everything: payload, entity tag, descriptor index, the timeframe set, in-flight
 * requests and listeners' view of the world. Call on logout so one session's registry cannot
 * outlive it; used by tests to isolate cases.
 */
export function resetRegistryClient() {
  dropCache();
  dropTimeframeCache();
  inflight = null;
  timeframeInflight = null;
  setTimeframeSnapshot(buildTimeframeSnapshot({ state: REGISTRY_STATES.IDLE }));
  return setSnapshot(buildSnapshot({ state: REGISTRY_STATES.IDLE }));
}
