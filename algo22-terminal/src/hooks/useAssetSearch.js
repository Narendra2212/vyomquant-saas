/**
 * useAssetSearch.js — the asset query state machine. Task 7.3, Requirements 11.2, 11.3, 11.7.
 *
 * Owns the four things a paginated, filtered, searched selector has to get right, and nothing
 * else: the projection is `lib/assetUniverse.js`, the transport is `api/modules/assets.js`,
 * and the markup is `components/builder/AssetSelector.jsx`.
 *
 * 1. **Debounce.** A filter change arms a timer (`DEBOUNCE_MS`); each keystroke re-arms it, so
 *    typing "BTC" costs one request rather than three. `pending` is exposed so the control can
 *    say "waiting for you to stop typing" instead of showing a spinner that is not a fetch.
 * 2. **Keyset pagination.** `loadMore()` sends the previous page's `next_cursor` and appends.
 *    Pages are merged by symbol so a universe that refreshed mid-page cannot show one market
 *    twice, and `universeChanged` is surfaced when the server says the universe moved.
 * 3. **A filter change resets the cursor.** The server pins a cursor to its filter set and
 *    answers `422 ASSET_CURSOR_INVALID` if it is continued under different filters, so the
 *    cursor is dropped the moment `filterFingerprint` changes. That is the *primary*
 *    mechanism; the 422 handler below is the backstop, not the design.
 * 4. **A 422 restarts the query once. It never loops.** One restart per fingerprint, guarded
 *    by a ref that is only cleared when the fingerprint changes, so a server that rejected a
 *    cursor for a reason this hook does not model produces one retry and then an error state —
 *    not an unbounded request loop against an authenticated, rate-limited endpoint.
 *
 * Staleness, which is the bug this shape exists to prevent
 * -------------------------------------------------------
 * Two guards, because either alone leaks. Every request carries an `AbortController` whose
 * signal is aborted when it is superseded or the component unmounts, *and* every request
 * carries a sequence number checked against the current one before its result is adopted. The
 * abort covers the network; the sequence number covers the window between an abort and the
 * promise settling, which is where a slow page-one response would otherwise land on top of a
 * fast page-two and show the author markets they had already scrolled past.
 *
 * Fail closed, with one deliberate exception
 * ------------------------------------------
 * A failed **first** page yields zero assets and a stated reason — never a substitute list
 * (Requirement 11.6, and the whole point of task 7.2). A failed **later** page keeps the pages
 * the server already sent and states the failure beside them: those records were real, and
 * discarding them would lose real data to report an error about a page that was never served.
 * `assets.length` and `error` are both readable, so a caller can tell the two cases apart.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { assetsApi, ASSET_LIMIT_DEFAULT } from '../api/modules/assets';
import {
  ASSET_QUERY_STATES,
  canonicalFilters,
  classifyAssetError,
  filterFingerprint,
  mergeAssetPages,
  readAssetPage,
} from '../lib/assetUniverse';

/**
 * How long after the last keystroke a search is issued.
 *
 * 250 ms is the interval already used for the builder's other debounce class (the validator's
 * is 400 ms per Requirement 8.10, and is a heavier request); a search is cheap and interactive,
 * so it is shorter, but it is still long enough that a normal typing cadence produces one
 * request per word rather than one per character.
 */
export const DEBOUNCE_MS = 250;

/** Page size. The server's own default; stated here so the number is visible. */
export const PAGE_LIMIT = ASSET_LIMIT_DEFAULT;

const EMPTY_ASSETS = Object.freeze([]);

const initialState = Object.freeze({
  state: ASSET_QUERY_STATES.IDLE,
  assets: EMPTY_ASSETS,
  total: null,
  nextCursor: null,
  universeChanged: false,
  duplicatesDropped: 0,
  cursorRestarted: false,
  stale: false,
  sourceMeta: null,
  error: null,
  pagesLoaded: 0,
});

/**
 * @param {object}  [options]
 * @param {object}  [options.filters]  Initial filters: `{search, base, quote, marketType, activeOnly}`.
 * @param {number}  [options.limit]    Page size.
 * @param {number}  [options.debounceMs]
 * @param {boolean} [options.enabled]  When false, nothing is requested — used so a control
 *   that is not open does not hold an authenticated, rate-limited endpoint open behind it.
 */
export function useAssetSearch({
  filters: initialFilters = {},
  limit = PAGE_LIMIT,
  debounceMs = DEBOUNCE_MS,
  enabled = true,
} = {}) {
  const [filters, setFilters] = useState(() => canonicalFilters(initialFilters));
  const [result, setResult] = useState(initialState);
  /** Bumped by `retry()` so a retry re-runs the effect without changing the filters. */
  const [attempt, setAttempt] = useState(0);
  /** A debounce is armed and no request is in flight yet. */
  const [pending, setPending] = useState(false);

  const fingerprint = useMemo(
    () => filterFingerprint({ ...filters, limit }),
    [filters, limit],
  );

  const sequenceRef = useRef(0);
  const abortRef = useRef(null);
  /** One cursor restart per fingerprint. Cleared only when the fingerprint changes. */
  const restartedRef = useRef(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (abortRef.current) abortRef.current.abort();
    };
  }, []);

  /**
   * Fetch one page. `cursor === null` is page one and replaces the held list; a cursor
   * appends. Never rejects: the outcome lands in state, so no caller can forget a `catch`.
   */
  const fetchPage = useCallback(
    async (cursor, { appendTo = null, restarted = false } = {}) => {
      const sequence = sequenceRef.current + 1;
      sequenceRef.current = sequence;

      if (abortRef.current) abortRef.current.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const current = () => sequenceRef.current === sequence && mountedRef.current;

      setPending(false);
      setResult((previous) => ({
        ...previous,
        state: ASSET_QUERY_STATES.LOADING,
        // A page-one load clears the list rather than showing the previous query's markets
        // under the new filters, which would be a list that answers a question nobody asked.
        assets: cursor === null ? EMPTY_ASSETS : previous.assets,
        total: cursor === null ? null : previous.total,
        error: null,
        cursorRestarted: restarted,
      }));

      let response;
      try {
        response = await assetsApi.discover(
          { ...filters, limit, cursor },
          { signal: controller.signal },
        );
      } catch (error) {
        if (!current()) return;
        const classified = classifyAssetError(error);

        // The one recovery path. A rejected cursor is restarted from page one, once per
        // filter set — never re-sent, and never retried in a loop.
        if (classified.isCursorInvalid && cursor !== null && restartedRef.current !== fingerprint) {
          restartedRef.current = fingerprint;
          await fetchPage(null, { restarted: true });
          return;
        }

        setResult((previous) => ({
          ...previous,
          state: ASSET_QUERY_STATES.ERROR,
          // Page one fails closed to zero assets; a later page keeps what was really served.
          assets: cursor === null ? EMPTY_ASSETS : previous.assets,
          total: cursor === null ? null : previous.total,
          nextCursor: null,
          error: classified,
        }));
        return;
      }

      if (!current()) return;

      let page;
      try {
        page = readAssetPage(response.data);
      } catch (error) {
        setResult((previous) => ({
          ...previous,
          state: ASSET_QUERY_STATES.ERROR,
          assets: cursor === null ? EMPTY_ASSETS : previous.assets,
          nextCursor: null,
          error: classifyAssetError(error),
        }));
        return;
      }

      const base = cursor === null ? [] : appendTo || [];
      const { assets, duplicates } = mergeAssetPages(base, page.assets);

      setResult((previous) => ({
        state: ASSET_QUERY_STATES.READY,
        assets: Object.freeze(assets),
        total: page.total,
        nextCursor: page.nextCursor,
        // Sticky within a query: once the server has said the universe moved while paging,
        // the list on screen was assembled across a refresh, and that stays true.
        universeChanged: cursor === null ? page.universeChanged : previous.universeChanged || page.universeChanged,
        duplicatesDropped: cursor === null ? duplicates : previous.duplicatesDropped + duplicates,
        cursorRestarted: restarted,
        stale: page.stale,
        sourceMeta: page.sourceMeta,
        error: null,
        pagesLoaded: cursor === null ? 1 : previous.pagesLoaded + 1,
      }));
    },
    [fingerprint, filters, limit],
  );

  // A filter change (or an explicit retry) drops the cursor and restarts from page one,
  // debounced. This is the mechanism that keeps a cursor from ever being sent under filters
  // it was not cut under.
  useEffect(() => {
    if (!enabled) return undefined;
    restartedRef.current = null;
    setPending(true);
    const timer = setTimeout(() => {
      fetchPage(null);
    }, debounceMs);
    return () => {
      clearTimeout(timer);
      setPending(false);
    };
    // `fingerprint` rather than `filters` so two objects with the same filters do not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fingerprint, attempt, enabled, debounceMs]);

  const setFilter = useCallback((key, value) => {
    setFilters((previous) => canonicalFilters({ ...previous, [key]: value }));
  }, []);

  const replaceFilters = useCallback((next) => {
    setFilters(canonicalFilters(next));
  }, []);

  const assetsRef = useRef(result.assets);
  assetsRef.current = result.assets;

  const loadMore = useCallback(() => {
    const cursor = result.nextCursor;
    if (cursor === null || result.state === ASSET_QUERY_STATES.LOADING) return;
    fetchPage(cursor, { appendTo: assetsRef.current });
  }, [fetchPage, result.nextCursor, result.state]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  return {
    filters,
    setFilter,
    replaceFilters,
    state: result.state,
    assets: result.assets,
    total: result.total,
    limit,
    nextCursor: result.nextCursor,
    hasMore: result.nextCursor !== null,
    universeChanged: result.universeChanged,
    duplicatesDropped: result.duplicatesDropped,
    cursorRestarted: result.cursorRestarted,
    stale: result.stale,
    sourceMeta: result.sourceMeta,
    error: result.error,
    pagesLoaded: result.pagesLoaded,
    pending,
    isLoading: result.state === ASSET_QUERY_STATES.LOADING,
    isReady: result.state === ASSET_QUERY_STATES.READY,
    isError: result.state === ASSET_QUERY_STATES.ERROR,
    /** A real answer to a real query: the filters matched nothing (not a missing universe). */
    isEmptyResult: result.state === ASSET_QUERY_STATES.READY && result.assets.length === 0,
    loadMore,
    retry,
  };
}

export default useAssetSearch;
