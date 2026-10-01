/**
 * ═══════════════════════════════════════════════════════════════════════════
 * useEntitlements — one read of the account's plan, shared by every surface
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * THE PROBLEM THIS SOLVES
 * -----------------------
 * `GET /api/billing/entitlements` was being called independently by `pages/Billing.jsx`,
 * `components/layout/AccountMenu.jsx`, `pages/Profile.jsx` and `pages/Wizard.jsx` — four requests
 * for one fact, each with its own loading state, each able to show a different plan than the others
 * for as long as they disagreed. Adding gate components to a dozen more surfaces would have made
 * that a dozen more requests.
 *
 * WHY A MODULE-LEVEL STORE RATHER THAN A REACT CONTEXT
 * ---------------------------------------------------
 * A context provider is the conventional answer and would be the right one in a codebase that
 * already had one. This one has no auth or user context at all — `src/AppState.jsx` is sixteen
 * lines holding `demoMode` and `uiMode` — so a provider would mean editing the application's root
 * tree, and every consumer would then be coupled to being mounted inside it. A surface rendered
 * outside the provider would fail at runtime, which is a worse failure than the one being fixed.
 *
 * A module-level store gives the same single-flight behaviour with no wiring: the first component
 * to mount triggers the read, every later one receives the same promise, and all of them re-render
 * together when it resolves. `useSyncExternalStore` is React's own primitive for exactly this, so
 * there is no subscription bookkeeping to get wrong and no tearing between consumers.
 *
 * STALENESS, AND WHY IT IS BOUNDED RATHER THAN POLLED
 * -------------------------------------------------
 * The snapshot is reused for {@link STALE_AFTER_MS} and re-read on demand after that. It is NOT
 * polled: entitlements change when billing changes, and billing changes already announce
 * themselves — `pages/Billing.jsx` calls {@link refreshEntitlements} after a verified checkout and
 * on a billing websocket frame. A timer would spend a request a minute to learn nothing on all the
 * days a trader does not change plan.
 *
 * WHAT THIS HOOK IS NOT
 * ---------------------
 * Not an authorization mechanism. It reports what the server said so the UI can show a locked state
 * instead of a button that 403s. Every gated action is refused independently by the backend, so
 * nothing here — not the cache, not the snapshot, not a consumer's props — can grant access.
 */

import { useCallback, useSyncExternalStore } from 'react';

import { api, isAuthenticated } from '../api';
import { entitlementsBody } from '../design/entitlements';

/** How long a snapshot is reused before the next consumer triggers a re-read. */
export const STALE_AFTER_MS = 60_000;

/** The lifecycle of the shared read. Mirrors `usePanelState`'s vocabulary where they overlap. */
export const ENTITLEMENT_STATES = Object.freeze({
  /** Nobody is signed in, so there is nothing to read and no request is issued. */
  ANONYMOUS: 'anonymous',
  LOADING: 'loading',
  READY: 'ready',
  ERROR: 'error',
});

/*
  The store. One object, replaced wholesale on every transition so `useSyncExternalStore` sees a
  new reference and consumers re-render. Never mutated in place — a mutated snapshot is how
  `useSyncExternalStore` ends up serving a stale render it believes is current.
*/
let snapshot = Object.freeze({
  state: ENTITLEMENT_STATES.LOADING,
  body: null,
  error: null,
  fetchedAt: 0,
});

/** In-flight read, so N consumers mounting in one tick produce ONE request. */
let inFlight = null;

const subscribers = new Set();

function publish(next) {
  snapshot = Object.freeze(next);
  // Copied before iterating: a subscriber that unmounts in response to this notification would
  // otherwise mutate the Set mid-iteration.
  for (const notify of Array.from(subscribers)) notify();
}

function subscribe(notify) {
  subscribers.add(notify);
  return () => subscribers.delete(notify);
}

function getSnapshot() {
  return snapshot;
}

/**
 * Read the entitlements, reusing an in-flight or fresh result.
 *
 * @param {boolean} force Bypass the staleness window. Used by {@link refreshEntitlements}.
 * @returns {Promise<object|null>} The entitlements body, or `null` when nobody is signed in.
 */
async function load(force) {
  if (!isAuthenticated()) {
    // Not an error state. An anonymous visitor has no plan, and reporting "could not read your
    // plan" to someone who is not signed in sends them looking for a fault that is not there.
    if (snapshot.state !== ENTITLEMENT_STATES.ANONYMOUS) {
      publish({
        state: ENTITLEMENT_STATES.ANONYMOUS,
        body: null,
        error: null,
        fetchedAt: Date.now(),
      });
    }
    return null;
  }

  if (inFlight) return inFlight;

  const fresh =
    snapshot.state === ENTITLEMENT_STATES.READY
    && Date.now() - snapshot.fetchedAt < STALE_AFTER_MS;
  if (fresh && !force) return snapshot.body;

  // `body` is kept on screen across a refresh — the same distinction `usePanelState` draws between
  // `loading` and `refreshing`. Blanking it would make every gate flash to its locked state for the
  // duration of a background re-read, which reads as a downgrade the trader did not make.
  publish({ ...snapshot, state: ENTITLEMENT_STATES.LOADING, error: null });

  inFlight = (async () => {
    try {
      const response = await api.billing.getEntitlements();
      const body = entitlementsBody(response);
      publish({
        state: ENTITLEMENT_STATES.READY,
        body,
        error: null,
        fetchedAt: Date.now(),
      });
      return body;
    } catch (error) {
      // `body` is dropped. A plan that could not be re-read is not evidence of the plan it used to
      // be, and a gate rendering an unlocked control from a stale snapshot is the failure mode this
      // whole layer exists to avoid. Gates fail closed from here.
      publish({
        state: ENTITLEMENT_STATES.ERROR,
        body: null,
        error,
        fetchedAt: Date.now(),
      });
      return null;
    } finally {
      inFlight = null;
    }
  })();

  return inFlight;
}

/**
 * Force a re-read and notify every consumer.
 *
 * Call this after anything that can change the account's plan or its usage: a verified checkout, a
 * cancellation, a billing websocket frame, or creating/deleting a resource whose count a gate is
 * rendering.
 *
 * @returns {Promise<object|null>}
 */
export function refreshEntitlements() {
  return load(true);
}

/**
 * Drop the shared snapshot. Called on sign-out so the next account does not see the last one's plan.
 */
export function clearEntitlements() {
  inFlight = null;
  publish({
    state: ENTITLEMENT_STATES.LOADING,
    body: null,
    error: null,
    fetchedAt: 0,
  });
}

/**
 * The account's entitlement state.
 *
 * @returns {{
 *   state: string,
 *   body: object|null,
 *   error: unknown,
 *   plan: string|null,
 *   tier: string|null,
 *   displayName: string|null,
 *   isReady: boolean,
 *   refresh: () => Promise<object|null>,
 * }}
 */
export function useEntitlements() {
  const current = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  /*
    The read is triggered from the subscribe callback rather than from an effect. `useSyncExternalStore`
    subscribes during commit, so this runs once per mount at the same point an effect would — but it
    cannot double-fire under StrictMode's deliberate double-invocation, because `load` is
    single-flight and returns the existing promise.
  */
  const subscribeAndLoad = useCallback((notify) => {
    const unsubscribe = subscribe(notify);
    void load(false);
    return unsubscribe;
  }, []);

  // Re-subscribing with the loading side effect attached. The second call is cheap: `subscribe`
  // is a Set insert and `load` short-circuits on a fresh snapshot.
  useSyncExternalStore(subscribeAndLoad, getSnapshot, getSnapshot);

  return {
    state: current.state,
    body: current.body,
    error: current.error,
    plan: current.body?.plan ?? null,
    tier: current.body?.tier ?? null,
    displayName: current.body?.display_name ?? null,
    isReady: current.state === ENTITLEMENT_STATES.READY && current.body !== null,
    refresh: refreshEntitlements,
  };
}

export default useEntitlements;
