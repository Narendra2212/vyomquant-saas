/**
 * useAutoRefresh — keep an ad-hoc loader's data current without a manual page refresh.
 *
 * Pages that read through `usePanelState` get this behaviour from its `intervalMs`
 * option. This hook is for pages that still load with their own `useEffect` + `async`
 * function. It calls `refresh(true)`:
 *
 *   - every `intervalMs` while the tab is visible (a tick is skipped while it is hidden),
 *   - straight away when the trader comes back to the tab (`visibilitychange` → visible,
 *     or window `focus`), throttled so the two events together cause one read.
 *
 * The `true` argument means "background": the caller should keep what is on screen and
 * not flash a full-page loader. The first load is still the caller's own effect.
 *
 * `refresh` is read through a ref, so an inline arrow does not restart the timer.
 */

import { useEffect, useRef } from 'react';

/** Two focus-type events within this window produce one refresh. */
export const FOCUS_REFRESH_THROTTLE_MS = 2000;

/**
 * Call `refresh` on a visible-tab interval and when the tab regains focus.
 *
 * @param {(background: boolean) => any} refresh
 * @param {number} intervalMs `0` disables the interval; focus refresh still runs.
 * @param {{enabled?: boolean}} [options]
 */
export function useAutoRefresh(refresh, intervalMs, { enabled = true } = {}) {
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useEffect(() => {
    if (!enabled) return undefined;
    return subscribeAutoRefresh(() => refreshRef.current?.(true), intervalMs);
  }, [enabled, intervalMs]);
}

/**
 * The non-React part, shared with `usePanelState`: start the interval and the focus
 * listeners, and return a function that stops them.
 *
 * @param {() => any} tick
 * @param {number} intervalMs
 * @returns {() => void}
 */
export function subscribeAutoRefresh(tick, intervalMs) {
  const hasDocument = typeof document !== 'undefined';
  const hasWindow = typeof window !== 'undefined';
  const isHidden = () => hasDocument && document.visibilityState === 'hidden';

  let lastFocusRefresh = 0;
  const onReturn = () => {
    if (isHidden()) return;
    const now = Date.now();
    if (now - lastFocusRefresh < FOCUS_REFRESH_THROTTLE_MS) return;
    lastFocusRefresh = now;
    tick();
  };

  const timer =
    intervalMs > 0
      ? setInterval(() => {
          if (isHidden()) return;
          tick();
        }, intervalMs)
      : null;

  if (hasDocument) document.addEventListener('visibilitychange', onReturn);
  if (hasWindow) window.addEventListener('focus', onReturn);

  return () => {
    if (timer !== null) clearInterval(timer);
    if (hasDocument) document.removeEventListener('visibilitychange', onReturn);
    if (hasWindow) window.removeEventListener('focus', onReturn);
  };
}

/** Shared cadences, so every page polls at one of a few known rates. */
export const REFRESH = Object.freeze({
  /** Live trading state: deployments, orders, positions. */
  LIVE: 10000,
  /** Account-level views: dashboard, history, signals, connections. */
  ACCOUNT: 15000,
  /** Slow-moving reads: billing, entitlements. */
  SLOW: 60000,
});

export default useAutoRefresh;
