/**
 * ═══════════════════════════════════════════════════════════════════════════
 * useUnreadNotifications — ONE unread read, shared by every consumer
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 8.8. design.md §6.4. Requirements 14.5, 18.3.
 *
 * ---------------------------------------------------------------------------
 * WHY THIS EXISTS
 * ---------------------------------------------------------------------------
 * `Sidebar.jsx` and `TopBar.jsx` each ran their own `getUnreadCount()` plus their own
 * `wsClient.subscribe('notification')`. Task 8.6 deleted the sidebar's, so today there is
 * exactly one reader — `TopBar`'s `NotificationBell` — and §6.4 is about to add a second:
 * the account menu's `Notifications (3)` row. Two components rendering the same number
 * from two requests can disagree about it, and they disagree in the visible direction (one
 * badge says 3, the row says 4) because the frames that increment them arrive
 * independently of the reads that seed them.
 *
 * So the read moves here, once, and both surfaces subscribe to the result.
 *
 * ---------------------------------------------------------------------------
 * HOW ONE REQUEST IS SHARED — A REF-COUNTED MODULE STORE
 * ---------------------------------------------------------------------------
 * Not a context: the bell and the menu sit in two different subtrees of the shell (the
 * top bar and the sidebar's footer), so a provider would have to wrap the whole shell and
 * every test of either component. Not a module-level cache either — see the next section.
 * What is here is a module store with a **subscriber count**:
 *
 *   * The FIRST consumer to mount starts the store: one `getUnreadCount()` request and one
 *     `wsClient.subscribe('notification')`.
 *   * Every later consumer reads the value that is already there and issues nothing.
 *   * Unmounting a consumer while others remain only drops that consumer's listener. The
 *     request, the socket subscription and the count are untouched, so the bell surviving
 *     the menu's close costs nothing and re-opening the menu costs no request.
 *   * When the LAST consumer unmounts, the socket subscription is dropped and the store is
 *     reset to unknown. An in-flight request is left to settle into a store that is no
 *     longer listening — its result is discarded by the generation check in
 *     {@link startStore}, so a late answer cannot repopulate a torn-down store or a
 *     later one.
 *
 * ---------------------------------------------------------------------------
 * WHY THE STORE RESETS RATHER THAN CACHING ACROSS THE GAP
 * ---------------------------------------------------------------------------
 * A count that outlived its last consumer would be re-rendered on the next mount without
 * being re-read — a figure from an unknown time ago presented as current, which is exactly
 * what Requirement 14.5 rules out. The shell mounts these consumers once per session, so
 * "the last consumer unmounted" means the shell came down; the next mount is a new shell
 * and deserves a new read.
 *
 * ---------------------------------------------------------------------------
 * THE HONESTY SEMANTICS ARE LOAD-BEARING (Requirement 14.5)
 * ---------------------------------------------------------------------------
 * These are `NotificationBell`'s, moved verbatim, and `tests/unit/shell/topBar.test.jsx`
 * pins every one of them:
 *
 *   * `null` means NOT KNOWN — the request has not answered, or it failed, or it answered
 *     with something unreadable. No badge, and no number in the accessible name.
 *   * A server-reported `0` is a FACT and is said out loud ("Notifications, 0 unread"). It
 *     is not the same state as unknown, and the old `useState(0)` said it before anything
 *     had been read.
 *   * A pushed `notification` frame increments a KNOWN count only. `null + 1` would
 *     fabricate the base, so from unknown the count stays unknown — the Notification
 *     Center holds the real list either way.
 *
 * {@link describeUnread} exists so the bell, the menu row and anything added later cannot
 * word that differently. It is the accessible name, not decoration.
 *
 * Nothing here polls. The seed is one request and every later change is a socket frame,
 * which is the same push discipline `useConnectionStatus` states at length.
 */

import { useEffect, useState } from 'react';

import { api } from '../api';
import wsClient from '../websocketClient';

/** The socket frame that means "a new unread row exists". */
const NOTIFICATION_CHANNEL = 'notification';

/**
 * The one store. Module-level on purpose — see the header.
 *
 * `generation` increments on every teardown, so an in-flight request that settles after
 * the last consumer left can tell that it is answering a question nobody is asking any
 * more. Without it, a slow request could publish into a store that had been reset, and the
 * next mount would show a count it never asked for.
 */
const store = {
  /** @type {number|null} `null` is NOT KNOWN. Never coerced to 0. */
  count: null,
  /** @type {Set<(count: number|null) => void>} */
  listeners: new Set(),
  /** @type {(() => void)|null} The socket unsubscribe, while the store is running. */
  unsubscribe: null,
  generation: 0,
};

/** Publish to every consumer. Same value → no publish, so React has nothing to re-render. */
function publish(next) {
  if (store.count === next) return;
  store.count = next;
  for (const listener of Array.from(store.listeners)) listener(next);
}

/**
 * Whether a value read off the wire is a usable count.
 *
 * A negative count, a float, a string and `undefined` are all "not readable", and not
 * readable is `null` rather than `0`: the endpoint failing to say how many is not the same
 * statement as it saying none.
 *
 * @param {unknown} value
 * @returns {boolean}
 */
function isCount(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

/**
 * Start the store: one request, one subscription. Called by the first consumer only.
 *
 * @returns {void}
 */
function startStore() {
  const generation = store.generation;

  (async () => {
    try {
      const response = await api.notifications.getUnreadCount();
      // The store may have been torn down and restarted while this was in flight. Both the
      // generation and the live listener set are checked: the first catches a restart, the
      // second a teardown with no restart after it.
      if (generation !== store.generation || store.listeners.size === 0) return;
      const count = response?.unread_count;
      if (isCount(count)) publish(count);
    } catch {
      // Offline, unauthenticated, or the endpoint failed. The count stays unknown; it does
      // not become zero. This `catch` is the whole of the failure handling on purpose —
      // there is no retry, because a bell that keeps asking is a poll by another name.
    }
  })();

  const unsubscribe = wsClient.subscribe(NOTIFICATION_CHANNEL, () => {
    // A frame IS a new unread row, so +1 on a known count is a real increment rather than
    // an estimate. From unknown it stays unknown — see the header.
    if (typeof store.count === 'number') publish(store.count + 1);
  });
  store.unsubscribe = typeof unsubscribe === 'function' ? unsubscribe : null;
}

/** Stop the store: drop the socket subscription and forget the count. Last consumer only. */
function stopStore() {
  if (store.unsubscribe !== null) {
    store.unsubscribe();
    store.unsubscribe = null;
  }
  // Bumped BEFORE the reset so an in-flight request's generation check has already failed
  // by the time it could publish.
  store.generation += 1;
  store.count = null;
}

/**
 * The accessible name for a control that leads to the Notification Center.
 *
 * One sentence, one place. The bell's `aria-label` and the account menu's Notifications row
 * both read this, so the two surfaces cannot describe one count two ways — and the strings
 * are the ones `tests/unit/shell/topBar.test.jsx` pins.
 *
 * @param {number|null} unread
 * @returns {string} `'Notifications'` when unknown; `'Notifications, N unread'` when known,
 *   including for a real zero.
 */
export function describeUnread(unread) {
  return typeof unread === 'number' ? `Notifications, ${unread} unread` : 'Notifications';
}

/**
 * The count for the badge, capped for display. `null` when there is nothing to show.
 *
 * A cap is a rendering concern, so it belongs beside the rendering rule rather than inside
 * either component: the badge is drawn only for a known, non-zero count, and above 99 it
 * reads `99+` while {@link describeUnread} keeps the real number in the accessible name.
 *
 * @param {number|null} unread
 * @returns {string|null}
 */
export function unreadBadgeText(unread) {
  if (typeof unread !== 'number' || unread <= 0) return null;
  return unread > 99 ? '99+' : String(unread);
}

/**
 * The unread notification count, shared across every consumer in the tree.
 *
 * @returns {{unread: number|null, known: boolean, label: string, badge: string|null}}
 *   `unread` is `null` when the count is not known — never `0` as a stand-in. `label` is
 *   {@link describeUnread}'s sentence and `badge` is {@link unreadBadgeText}'s.
 */
export function useUnreadNotifications() {
  const [unread, setUnread] = useState(store.count);

  useEffect(() => {
    // The first consumer starts the store; every later one joins it. Registering the
    // listener BEFORE starting matters: `startStore`'s own guards read `listeners.size`,
    // and a synchronous socket frame during `subscribe` would otherwise publish to nobody.
    store.listeners.add(setUnread);
    if (store.listeners.size === 1) startStore();

    // Re-seed after subscribing, for the same reason `useConnectionStatus` re-reads
    // `getStatus()`: the store may have been published to between this component's render
    // and this effect. Setting state to the value it already holds is a React no-op.
    setUnread(store.count);

    return () => {
      store.listeners.delete(setUnread);
      if (store.listeners.size === 0) stopStore();
    };
  }, []);

  return {
    unread,
    known: typeof unread === 'number',
    label: describeUnread(unread),
    badge: unreadBadgeText(unread),
  };
}

/**
 * Drop the store unconditionally.
 *
 * For tests, which need a clean store between cases because it is module state — the same
 * reason `ds/overlayRegistry` exports `resetOverlayRegistry`. Application code never calls
 * this: a component tearing down a store other components are reading is the bug the ref
 * count exists to prevent.
 *
 * @returns {void}
 */
export function resetUnreadNotifications() {
  store.listeners.clear();
  stopStore();
}

export default useUnreadNotifications;
