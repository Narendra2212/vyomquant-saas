/**
 * ═══════════════════════════════════════════════════════════════════════════
 * useDeployPreflight — the Deployment_Gate, re-read while the modal is open
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * trading-lifecycle-integration tasks 16.1 and 18.1. Requirements 13.3, 13.4, 13.5, 13.6.
 *
 * WHAT THIS HOOK OWNS
 * -------------------
 * The poll lifecycle, and nothing else: it asks
 * `GET /api/strategy-operations/strategies/{id}/versions/{version}/deploy/preflight` while
 * the deployment configuration workflow is open, every
 * {@link PREFLIGHT_POLL_INTERVAL_MS} ms, and stops the moment the workflow closes. The
 * response is read by `lib/deployPreflight.js`, which is pure; this hook classifies
 * nothing and derives no verdict of its own.
 *
 * WHY IT POLLS RATHER THAN FETCHING ONCE
 * -------------------------------------
 * Requirement 13.6 is the whole reason: a condition that passed a moment ago — a balance
 * that has since dropped, an exchange connection that has since been lost — must disable
 * the Deploy button again *while the workflow stays open*, with no reload and no user
 * action. Re-asking is what makes that automatic, and it is safe to re-ask because the
 * endpoint is read-only and side-effect-free by contract: no deployment row, no quota
 * reservation, no exchange call. The endpoint's own 200/minute ceiling is sized for this
 * interval.
 *
 * WHY A NEW QUESTION DISCARDS THE PREVIOUS ANSWER
 * ----------------------------------------------
 * Changing the account, the mode or the sizing makes the open summary an answer about a
 * deployment that is no longer the one on screen. The state is cleared when the query
 * changes, so `deployable` reads false until the new question has been answered — the
 * button can never be enabled by a summary about a different binding.
 *
 * WHY A FAILED POLL DISABLES THE BUTTON
 * ------------------------------------
 * "Could not be checked" is not "checked and satisfied". A rejected poll clears the
 * summary and reports the failure, so an unreachable gate leaves the Deploy button
 * disabled rather than open (Requirement 13.4).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { endpoints } from '../api';
import {
  PREFLIGHT_POLL_INTERVAL_MS,
  describePreflightFailure,
  normalizePreflight,
  preflightQueryKey,
} from '../lib/deployPreflight';

/** The summary state before anything has been answered: not deployable, nothing to show. */
const EMPTY = Object.freeze({
  deployable: false,
  reported: null,
  conditions: [],
  failed: [],
  pending: [],
});

/**
 * Poll one version's deployment preflight.
 *
 * @param {Object} options
 * @param {string} [options.strategyId] The strategy whose version would be deployed.
 * @param {string} [options.version] The immutable version label, e.g. `"1.2"`.
 * @param {Object} [options.query] The deployment being checked — `deploymentRequest().query`.
 * @param {boolean} [options.enabled] True while the workflow is open. Nothing is requested
 *   when false, when there is no strategy, or when there is no version.
 * @param {number} [options.intervalMs] Poll interval; defaults to Requirement 13.6's two
 *   seconds. `0` or a negative value fetches once and does not repeat.
 * @param {Function} [options.fetcher] `(strategyId, version, query) => Promise<body>`.
 *   Defaults to the shared `endpoints.strategies.deployPreflight`.
 * @returns {{deployable: boolean, conditions: Array<Object>, failed: Array<Object>,
 *   pending: Array<Object>, reported: boolean|null, error: Object|null,
 *   isLoading: boolean, checkedAt: number|null, refresh: Function}}
 */
export function useDeployPreflight({
  strategyId,
  version,
  query,
  enabled = true,
  intervalMs = PREFLIGHT_POLL_INTERVAL_MS,
  fetcher,
} = {}) {
  const [summary, setSummary] = useState(EMPTY);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [checkedAt, setCheckedAt] = useState(null);

  const active = Boolean(enabled && strategyId && version);
  // The query is rebuilt on every render by the page that owns the form state, so its
  // identity cannot drive the effect; its content has to.
  const queryKey = useMemo(() => preflightQueryKey(query), [query]);
  const queryRef = useRef(query);
  queryRef.current = query;
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  // Bumped by `refresh()` to re-run the effect on demand (the 18.1 panel's retry affordance).
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!active) {
      // Closing the workflow discards the answer as well as the timer: a stale summary
      // must not be what a re-opened modal gates its Deploy button on.
      setSummary(EMPTY);
      setError(null);
      setIsLoading(false);
      setCheckedAt(null);
      return undefined;
    }

    let cancelled = false;
    let timer = null;

    // A new question, so the previous answer is not an answer to it.
    setSummary(EMPTY);
    setError(null);
    setIsLoading(true);

    const ask = async () => {
      const call =
        fetcherRef.current ??
        ((id, v, q) => endpoints.strategies.deployPreflight(id, v, q));
      try {
        const body = await call(strategyId, version, queryRef.current ?? {});
        if (cancelled) return;
        setSummary(normalizePreflight(body));
        setError(null);
      } catch (err) {
        if (cancelled) return;
        // Unreadable is not deployable (Requirement 13.4).
        setSummary(EMPTY);
        setError(describePreflightFailure(err));
      } finally {
        if (!cancelled) {
          setIsLoading(false);
          setCheckedAt(Date.now());
          if (intervalMs > 0) timer = setTimeout(ask, intervalMs);
        }
      }
    };

    ask();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // `queryKey` stands in for `query`'s content, which is what makes a changed account or
    // mode restart the poll instead of leaving it asking the old question.
  }, [active, strategyId, version, queryKey, intervalMs, nonce]);

  return {
    ...summary,
    error,
    isLoading,
    checkedAt,
    refresh,
  };
}

export default useDeployPreflight;
