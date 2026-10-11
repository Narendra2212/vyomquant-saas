/**
 * The trader's own sidebar width preference, persisted.
 *
 * WHY THIS IS NOT A `zustand` STORE, UNLIKE ITS NEIGHBOUR
 * ------------------------------------------------------
 * `store/useWorkspaceStore.js` is built on `zustand` + `zustand/middleware`, and copying
 * that was the obvious move. Measured first, though: **`zustand` is not a declared
 * dependency of this package.** `package.json` does not list it; `npm ls zustand` resolves
 * it at 4.5.7 purely as a transitive dependency of `reactflow@11.11.4`, which bundles it
 * for its own internal state.
 *
 * So importing it here would make the authenticated shell — every route, including the
 * fifteen that never touch the strategy builder — depend on a package that is present only
 * because a charting library happens to use it today. A `reactflow` major bump that drops
 * or renames that dependency would break the sidebar of a trading terminal, and nothing in
 * this repository would have declared the relationship. (`useWorkspaceStore` already
 * carries that risk; it is pre-existing, is recorded rather than fixed here, and is not a
 * reason to add a second instance of it.)
 *
 * The alternative was to add `zustand` to `dependencies`. That is a real option and a
 * defensible one, but it is a dependency decision taken to store ONE BOOLEAN, and this
 * module is 40 lines without it. `useSyncExternalStore` is React 18's own API for exactly
 * this shape — an external mutable source read by components — and it is tear-free, which a
 * hand-rolled `useState` + event listener pair is not.
 *
 * WHAT IT IS NOT
 * --------------
 * It is NOT the sidebar's width, and it is not consulted below laptop width. The viewport
 * floor stays `shell/ResponsiveGate`'s: at tablet width the rail is forced regardless of
 * what is stored here, because 216px of sidebar on a 768px screen leaves no readable
 * content column. `collapsed` is a REQUEST, and `ResponsiveGate.sidebarModeFor` resolves it
 * against the viewport. See that function for the whole decision.
 *
 * `false` is the default, so a trader who has never touched the control gets today's
 * behaviour exactly — labels on screen at laptop width and above.
 *
 * NO CROSS-TAB SYNC, DELIBERATELY. A `storage` listener would make two open tabs agree, and
 * it is left out: a trader who collapses the sidebar in one window has said nothing about
 * the other, and silently reshaping a window they are not looking at is a surprise rather
 * than a convenience. `App.jsx`'s `storage` listener covers the one key where cross-tab
 * agreement IS load-bearing — the auth token — and this is not that.
 */

import { useSyncExternalStore } from 'react';

/**
 * The localStorage key.
 *
 * Distinct from `aerora-workspace-storage`: that key is cleared wholesale by
 * `useWorkspaceStore.resetWorkspace()` to restore the default PANEL LAYOUT, and a trader
 * asking for their grid back has not asked for their sidebar to reopen.
 */
export const SIDEBAR_STORAGE_KEY = 'vyomquant-sidebar-collapsed';

/** `'1'` is collapsed. Any other stored value — including a hand-edited one — reads false. */
const COLLAPSED_VALUE = '1';

const listeners = new Set();

/**
 * Reads the persisted preference.
 *
 * `try`/`catch` because `localStorage` THROWS rather than returning null in two cases this
 * app genuinely meets: Safari private browsing, and a packaged Tauri window whose storage
 * partition has not been granted. A preference that cannot be read is not an error worth
 * failing a boot over — it is just the default.
 */
function readPersisted() {
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === COLLAPSED_VALUE;
  } catch {
    return false;
  }
}

let collapsed = typeof window === 'undefined' ? false : readPersisted();

/** The snapshot. A BOOLEAN, so `useSyncExternalStore` compares it by value and cannot loop. */
export function getSidebarCollapsed() {
  return collapsed;
}

/** @param {() => void} listener @returns {() => void} */
export function subscribeSidebar(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Sets the preference, persists it, and notifies subscribers.
 *
 * Writing is best-effort for the same reason reading is: storage can throw. A failed write
 * means the preference does not survive a reload — the sidebar still collapses now, which
 * is what the trader just asked for.
 *
 * @param {boolean} next
 */
export function setSidebarCollapsed(next) {
  const value = Boolean(next);
  if (value === collapsed) return;
  collapsed = value;
  try {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, value ? COLLAPSED_VALUE : '0');
  } catch {
    /* Not persisted. The in-memory preference below still applies for this session. */
  }
  for (const listener of Array.from(listeners)) listener();
}

/** Flips the preference. */
export function toggleSidebarCollapsed() {
  setSidebarCollapsed(!collapsed);
}

/**
 * Re-reads the persisted preference into module state. **Test seam only** — nothing in
 * `src/` calls it.
 *
 * WHY IT HAS TO EXIST. {@link readPersisted} runs exactly once, at module load, which is
 * what makes a real page reload pick the preference back up. That also makes it the one
 * code path a test cannot reach by rendering: a remount re-runs the component, not the
 * module, so `collapsed` keeps whatever value it already had and the `=== COLLAPSED_VALUE`
 * comparison is never evaluated again.
 *
 * Without this, the parsing half of persistence was untested and a test asserting it was
 * vacuous — `resetSidebarCollapsed()` assigns `false` directly, so "a hand-edited value
 * reads as the default" passed whatever `readPersisted` did, including nothing. Calling
 * this is the closest a jsdom test gets to reloading the page.
 */
export function rehydrateSidebarCollapsed() {
  collapsed = readPersisted();
  for (const listener of Array.from(listeners)) listener();
  return collapsed;
}

/**
 * Resets the module AND the stored key to the default. **Test seam only** — nothing in
 * `src/` calls it.
 *
 * Module state outlives a `cleanup()`, so without this one test collapsing the sidebar
 * would leak into the next file's expectations.
 *
 * NOTE FOR A TEST AUTHOR: this ASSIGNS the default rather than re-reading storage, so it
 * cannot be used to assert anything about what a stored value parses to. Use
 * {@link rehydrateSidebarCollapsed} for that.
 */
export function resetSidebarCollapsed() {
  collapsed = false;
  try {
    window.localStorage.removeItem(SIDEBAR_STORAGE_KEY);
  } catch {
    /* Nothing to remove. */
  }
  for (const listener of Array.from(listeners)) listener();
}

/**
 * The preference, as a hook.
 *
 * The third argument is the server snapshot. There is no SSR here — this is a Vite SPA and
 * a Tauri window — but `useSyncExternalStore` calls it during hydration if one is given, and
 * `false` is the honest answer when there is no `window` to read.
 *
 * @returns {boolean}
 */
export default function useSidebarCollapsed() {
  return useSyncExternalStore(subscribeSidebar, getSidebarCollapsed, () => false);
}
