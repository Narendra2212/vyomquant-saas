/**
 * useTimeframeSet.js — React's view of the published timeframe set. Requirement 11.8.
 *
 * A subscription to `lib/registryClient.js`'s timeframe cache and nothing more. The fetch,
 * the `ETag` revalidation, the `304` reuse, the single-in-flight rule and the fail-closed
 * behaviour all live in that module, which already implements them for the block registry;
 * this hook exists so a component can read the snapshot without importing a client.
 *
 * There is no local timeframe list here or anywhere below it. `3m` is deliberately absent from
 * what the endpoint publishes — `market_data_validation.TIMEFRAME_MINUTES` has no figure for
 * it, so a 3m strategy would run with its row-coverage check silently unfailable — and a
 * hand-written list in the client would put it straight back in front of the author.
 */

import { useCallback, useEffect, useState } from 'react';

import {
  getTimeframeSnapshot,
  loadTimeframes,
  refreshTimeframes,
  subscribeTimeframes,
} from '../lib/registryClient';

/**
 * @param {{enabled?: boolean}} [options] `enabled: false` subscribes without requesting, so a
 *   control that is not rendered does not hold an authenticated endpoint open behind it.
 */
export function useTimeframeSet({ enabled = true } = {}) {
  const [snapshot, setSnapshot] = useState(getTimeframeSnapshot);

  useEffect(() => {
    // Subscribe before loading: a `ready` cache resolves synchronously, and a listener
    // attached afterwards would miss the transition it exists to observe.
    const unsubscribe = subscribeTimeframes(setSnapshot);
    setSnapshot(getTimeframeSnapshot());
    if (enabled) loadTimeframes();
    return unsubscribe;
  }, [enabled]);

  const reload = useCallback(() => refreshTimeframes(), []);

  return {
    state: snapshot.state,
    /** Empty unless `isReady`. There is no substitute list to fall back to. */
    timeframes: snapshot.timeframes,
    sources: snapshot.sources,
    registryVersion: snapshot.registryVersion,
    error: snapshot.error,
    isLoading: snapshot.isLoading,
    isReady: snapshot.isReady,
    isError: snapshot.isError,
    reload,
  };
}

export default useTimeframeSet;
