/**
 * ═══════════════════════════════════════════════════════════════════════════
 * usePanelState — the one panel state machine (design §11.1, Requirement 14)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 5.5. Requirements 14.1, 14.2, 14.3, 14.5, 19.3.
 *
 * WHAT THIS HOOK OWNS
 * -------------------
 * Turning one read into one of §11.1's eight states, and nothing else. It formats nothing,
 * chooses no colour, renders no copy and decides no layout. `ds/Panel` (task 6.1) is what
 * turns the state into a rendering; `design/errorCopy.js` is what turns `error` into words.
 *
 * THIS GENERALISES `pages/paperTradingFormat.js`, IT DOES NOT REPLACE IT
 * --------------------------------------------------------------------
 * That module already carries an eight-state panel vocabulary and a
 * server-error→state classification, both with a passing suite, because Paper Trading was
 * held to Requirement 20.5 before this redesign existed. Two things are lifted from it
 * rather than rewritten here:
 *
 *   1. {@link classifyReadFailure} — imported and used as-is. It reads `ApiError`'s own
 *      `.data` envelope and `.status`, and the ORDER of its checks is the part that matters:
 *      it reads the server's error code before it reads the HTTP status, which is what keeps
 *      a 403 that means "this subscription has expired" from being reported as "sign in
 *      again". Reimplementing 401/403 detection here would have thrown that ordering away.
 *   2. The frozen-vocabulary discipline — {@link PANEL_STATES} below is the same shape as
 *      that module's, widened from Paper Trading's eight to §11.1's eight so a caller never
 *      spells a state as a bare string.
 *
 * `paperTradingFormat.js` is pure — no React, no `api`, no DOM, no clock — so importing it
 * here costs nothing and introduces no cycle. Its own eight states stay exactly as they are;
 * `PaperTrading.jsx` keeps rendering through them until task 25.1 moves that page over.
 *
 * THE THREE INVERSIONS OF `usePolling` (`components/ui-legacy/primitives.jsx`)
 * -------------------------------------------------------------------------
 * This hook replaces `usePolling` (design §13.2). Three of its behaviours are deliberately
 * inverted, and each inversion is commented at the line that performs it so that a later
 * reader does not "fix" it back:
 *
 *   1. **A failed read discards `data`.** `usePolling` keeps the last payload and merely sets
 *      `error`, so a page renders the previous tick's P&L under an error indicator. That is
 *      precisely the previously-cached-as-live rendering Requirement 14.5 forbids. Here a
 *      failure sets `data` to `null` — and `lastUpdated` to `null` with it, because a
 *      freshness stamp for a payload that is no longer on screen is the same lie in smaller
 *      type.
 *   2. **`refetch` is stable.** `usePolling` lists `data` in its `useCallback` dependency
 *      array, so every successful poll produces a new callback, which re-runs the effect,
 *      which tears down the interval and starts a new one — the interval is re-created on
 *      every tick. Here `refetch` depends on nothing that a read changes, so the interval is
 *      set once (design §13.2).
 *   3. **`loading` and `refreshing` are different states.** `usePolling` has one `loading`
 *      boolean, so a background refresh and a first read are indistinguishable and a page
 *      cannot honour §11.1's rule that previous data may stay on screen for one and not the
 *      other. `refreshing` here is reachable ONLY from `ready` — only after a read that
 *      actually succeeded — and never from `error`.
 *
 * THE STATE MACHINE (§11.1)
 * ------------------------
 *   [*]         → idle          nothing has been asked for (disabled, or no reader yet)
 *   idle        → loading       a read starts
 *   loading     → ready         2xx with items
 *   loading     → empty         2xx with zero items
 *   loading     → error         4xx / 5xx / network
 *   loading     → unauthorised  401 / 403
 *   loading     → unavailable   the server says the thing behind this read does not resolve
 *   ready       → refreshing    background refresh (interval, or `refetch`)
 *   refreshing  → ready | empty | error | unauthorised | unavailable
 *   empty       → loading       retry / filter change
 *   error       → loading       retry
 *   unavailable                 terminal for this mount — no request is ever issued
 *
 * `unavailable` WITHOUT A REQUEST (Requirement 19.3)
 * -------------------------------------------------
 * A non-empty `unavailable` reason string short-circuits to `unavailable` and the reader is
 * never called. This is how a capability the backend does not have is expressed — as an
 * explicit "not available" state carrying a human reason — rather than by issuing a request
 * that 404s, or worse, by rendering an empty panel that reads as "you have none of these"
 * when the truth is "we cannot tell you".
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  PANEL_STATES as PAPER_TRADING_PANEL_STATES,
  classifyReadFailure,
} from '../pages/paperTradingFormat';

/**
 * §11.1's eight states, spelled once.
 *
 * Unlike `paperTradingFormat.js`'s eight — which are Paper Trading's eight, and include that
 * page's entitlement and feed conditions — these are the eight every in-scope panel shares.
 * `idle`, `ready` and `refreshing` are among them here, because this hook produces all three
 * and a caller comparing against a bare `'ready'` string is a typo waiting to happen.
 *
 * @type {Readonly<Record<string, string>>}
 */
export const PANEL_STATES = Object.freeze({
  IDLE: 'idle',
  LOADING: 'loading',
  READY: 'ready',
  REFRESHING: 'refreshing',
  EMPTY: 'empty',
  ERROR: 'error',
  UNAVAILABLE: 'unavailable',
  UNAUTHORISED: 'unauthorised',
});

/** The eight, as a list, so `ds/Panel` can be driven by them rather than re-listing them. */
export const ALL_PANEL_STATES = Object.freeze(Object.values(PANEL_STATES));

/**
 * The states in which a panel must NOT render its children (Requirement 14.5, §11.1).
 *
 * `refreshing` is deliberately absent: it is the one state where previous data stays on
 * screen, and it is reachable only from a read that succeeded.
 */
export const STATES_WITHOUT_CHILDREN = Object.freeze([
  PANEL_STATES.IDLE,
  PANEL_STATES.LOADING,
  PANEL_STATES.EMPTY,
  PANEL_STATES.ERROR,
  PANEL_STATES.UNAVAILABLE,
  PANEL_STATES.UNAUTHORISED,
]);

/**
 * `paperTradingFormat.js`'s failure vocabulary projected onto §11.1's.
 *
 * Only the four states {@link classifyReadFailure} can actually return are listed; anything
 * else falls through to `error`, which is the state that admits it does not know why.
 *
 * The two entitlement cases both land on `unavailable` rather than `unauthorised`, and that is
 * the one judgement in this map. `unauthorised` renders as "your session cannot see this" —
 * sign in again — which is wrong and slightly insulting for a trader who is signed in
 * perfectly well and whose subscription simply lapsed. `unavailable` is the state that says
 * "this panel cannot show you data, and here is the human reason", which is exactly the
 * shape of an expired-subscription or unresolvable-listing refusal. It also preserves the
 * distinction `classifyReadFailure` orders its checks to preserve.
 *
 * @type {Readonly<Record<string, string>>}
 */
export const PAPER_TRADING_STATE_TO_PANEL_STATE = Object.freeze({
  [PAPER_TRADING_PANEL_STATES.ERROR]: PANEL_STATES.ERROR,
  [PAPER_TRADING_PANEL_STATES.UNAUTHORISED]: PANEL_STATES.UNAUTHORISED,
  [PAPER_TRADING_PANEL_STATES.UNAVAILABLE_STRATEGY]: PANEL_STATES.UNAVAILABLE,
  [PAPER_TRADING_PANEL_STATES.EXPIRED_SUBSCRIPTION]: PANEL_STATES.UNAVAILABLE,
});

/**
 * The envelope keys that hold a collection when a payload wraps one.
 *
 * This is the only heuristic in the module, and it is an allowlist rather than a guess: a key
 * outside this set is not treated as a collection, so an object payload with real content is
 * never reported as `empty`.
 */
const COLLECTION_KEYS = Object.freeze(['items', 'results', 'rows', 'records', 'entries', 'data']);

/**
 * Whether a successful payload has zero items — the `loading → empty` edge.
 *
 * Total over any input, because the alternative is a panel that renders nothing and explains
 * nothing. The rules, in order:
 *
 *   - `null` / `undefined` — nothing arrived. Empty.
 *   - An array — empty when it has no elements.
 *   - A string — empty when it is blank.
 *   - A `Map` or `Set` — empty when its `size` is 0.
 *   - A plain object — if it carries any {@link COLLECTION_KEYS} array, empty when every such
 *     array is empty (so `{items: [], total: 0}` is empty and `{items: [], errors: [x]}` is
 *     not); otherwise empty only when it has no own enumerable keys at all.
 *   - Anything else, a number or a boolean included — not empty. `0` is a value, and
 *     Requirement 14.5's whole point is that a zero is never treated as an absence.
 *
 * @param {*} data
 * @returns {boolean}
 */
export function isEmptyPayload(data) {
  if (data === null || data === undefined) return true;
  if (Array.isArray(data)) return data.length === 0;
  if (typeof data === 'string') return data.trim() === '';
  if (data instanceof Map || data instanceof Set) return data.size === 0;
  if (typeof data !== 'object') return false;

  const collections = COLLECTION_KEYS.filter((key) => Array.isArray(data[key]));
  if (collections.length > 0) return collections.every((key) => data[key].length === 0);
  return Object.keys(data).length === 0;
}

/**
 * Which of §11.1's states a failed read is in.
 *
 * The classification itself is `paperTradingFormat.js`'s, which reads `ApiError`'s `.data`
 * envelope and `.status`; this only projects its answer onto the shared vocabulary. Nothing
 * about the error is re-derived here.
 *
 * @param {*} error The rejection a reader produced — an `ApiError`, or anything else.
 * @returns {string} One of `error`, `unauthorised`, `unavailable`.
 */
export function panelStateForFailure(error) {
  const { state } = classifyReadFailure(error);
  return PAPER_TRADING_STATE_TO_PANEL_STATE[state] ?? PANEL_STATES.ERROR;
}

/** Shallow element-wise comparison, so an inline `deps` literal does not restart every read. */
function sameDeps(a, b) {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  return a.every((value, index) => Object.is(value, b[index]));
}

const NO_DEPS = Object.freeze([]);

/**
 * Drive one panel's state from one read.
 *
 * @param {Function} reader `() => Promise<*>`. Called with no arguments. Not called at all
 *   while `unavailable` is set or `enabled` is false, and not called if it is not a function
 *   (a page that has not yet resolved which read to issue is `idle`, not broken).
 * @param {Object} [options]
 * @param {Array} [options.deps] The question being asked — a session id, a filter, a page
 *   number. A change here is a NEW question, so the previous answer is discarded and the
 *   state returns to `loading`: an answer about a different question must never be on screen
 *   as though it were about this one. Compared element-wise, so an inline literal is fine.
 * @param {boolean} [options.enabled] False while there is nothing to ask for. `idle`, no
 *   request.
 * @param {string|null} [options.unavailable] A human reason the capability behind this read
 *   does not exist. Short-circuits to `unavailable` WITHOUT issuing a request (Req 19.3).
 * @param {number} [options.intervalMs] Background refresh interval. `0` (the default) never
 *   repeats. The interval is set once and skips its tick while the document is hidden
 *   (design §13.2) rather than being torn down and re-created.
 * @returns {{state: string, data: *, error: *, refetch: Function, lastUpdated: Date|null}}
 */
export function usePanelState(reader, { deps = NO_DEPS, enabled = true, unavailable = null, intervalMs = 0 } = {}) {
  const reason = typeof unavailable === 'string' && unavailable.trim() !== '' ? unavailable : null;
  // A declared capability gap is checked before `enabled`, because it is the more specific
  // fact: `enabled: false` means "not asking yet", `unavailable` means "there is nothing to
  // ask". Either way no request is issued, but only one of them has something to tell the user.
  const active = reason === null && enabled === true && typeof reader === 'function';

  const [{ state, data, error, lastUpdated }, setPanel] = useState(() => ({
    state: PANEL_STATES.IDLE,
    data: null,
    error: null,
    lastUpdated: null,
  }));

  // True exactly when the last completed read succeeded WITH items — i.e. when the panel is
  // in `ready`. This is what makes `refreshing` reachable only from `ready` without putting
  // `state` or `data` in `refetch`'s dependency array (inversion 2 and 3 in the header). It is
  // cleared by every other outcome: a failure, an empty read, or a new question.
  const hasRenderedDataRef = useRef(false);
  // Monotonic read id. A read whose id is no longer current has been superseded by a newer
  // question or a newer refresh, and its outcome is dropped — an out-of-order resolution must
  // not be able to put an older payload on screen.
  const readIdRef = useRef(0);
  const readerRef = useRef(reader);
  readerRef.current = reader;
  const activeRef = useRef(active);
  activeRef.current = active;

  // `deps` arrives as a fresh array on most renders, so its identity cannot drive the effect;
  // its contents have to. The counter changes only when an element actually changed, which
  // keeps the effect's own dependency array a static literal.
  const depsRef = useRef(deps);
  const depsVersionRef = useRef(0);
  if (!sameDeps(depsRef.current, deps)) {
    depsRef.current = deps;
    depsVersionRef.current += 1;
  }
  const depsVersion = depsVersionRef.current;

  /**
   * Issue one read and reduce its outcome into the state.
   *
   * `background` is true for a refresh over data that is already on screen. It is honoured
   * only when the panel is actually in `ready`; from anywhere else a read is a `loading` read,
   * which is what stops `error → refreshing` from existing at all.
   */
  const run = useCallback(async (background) => {
    if (!activeRef.current) return;

    const readId = readIdRef.current + 1;
    readIdRef.current = readId;
    const refreshing = background === true && hasRenderedDataRef.current === true;

    setPanel((previous) =>
      refreshing
        ? { ...previous, state: PANEL_STATES.REFRESHING, error: null }
        // Not a refresh, so nothing from the previous answer survives the wait. A first read
        // and a retry both start from a clean panel.
        : { state: PANEL_STATES.LOADING, data: null, error: null, lastUpdated: null },
    );
    if (!refreshing) hasRenderedDataRef.current = false;

    try {
      const payload = await readerRef.current();
      if (readIdRef.current !== readId) return;
      const empty = isEmptyPayload(payload);
      hasRenderedDataRef.current = !empty;
      setPanel({
        state: empty ? PANEL_STATES.EMPTY : PANEL_STATES.READY,
        data: empty ? null : payload,
        error: null,
        lastUpdated: new Date(),
      });
    } catch (failure) {
      if (readIdRef.current !== readId) return;
      // ═══ THE REQUIREMENT 14.5 INVERSION — DO NOT "FIX" THIS ═══
      // `usePolling` keeps `data` here and only sets `error`, which is how a page comes to
      // render the previous tick's P&L, balance or position under an error indicator. The
      // stale payload is dropped, and `lastUpdated` with it, because a freshness stamp for a
      // payload that is no longer on screen is the same lie in smaller type. A panel that
      // cannot read is a panel with nothing to show, not a panel showing something old.
      hasRenderedDataRef.current = false;
      setPanel({
        state: panelStateForFailure(failure),
        data: null,
        error: failure,
        lastUpdated: null,
      });
    }
    // Nothing a read changes appears here. `data`, `state` and `error` are all read through
    // refs or through `setPanel`'s updater, so this callback's identity survives every tick
    // and the interval below is set once.
  }, []);

  /**
   * Re-read now. A refresh over `ready` data; a clean `loading` read from anywhere else. A
   * no-op while the panel is `idle` or `unavailable`, because neither has a read to repeat.
   *
   * Whether a retry is OFFERED is not decided here: §11.1 puts that on `ds/ErrorState`, which
   * shows the affordance only when the translation reports the failure retryable — and
   * retryability is `ApiError.isRetryable()`, already written. One gate, one place. This
   * callback re-reads whenever it is called, so a caller that renders its own refresh control
   * does not end up with a dead one (Requirement 19.4).
   */
  const refetch = useCallback(() => run(true), [run]);

  useEffect(() => {
    if (reason !== null) {
      // Requirement 19.3: the reader is never called. There is no request to cancel, no
      // spinner, and no empty render pretending the collection is genuinely empty.
      readIdRef.current += 1;
      hasRenderedDataRef.current = false;
      setPanel({ state: PANEL_STATES.UNAVAILABLE, data: null, error: null, lastUpdated: null });
      return undefined;
    }
    if (!active) {
      readIdRef.current += 1;
      hasRenderedDataRef.current = false;
      setPanel({ state: PANEL_STATES.IDLE, data: null, error: null, lastUpdated: null });
      return undefined;
    }

    // A changed `deps` is a new question, so this is a first read and not a refresh.
    run(false);

    const timer =
      intervalMs > 0
        ? setInterval(() => {
            // §13.2: paused while the tab is hidden. The tick is skipped rather than the
            // interval being cleared and re-created, so this stays "set once".
            if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
            run(true);
          }, intervalMs)
        : null;

    return () => {
      if (timer !== null) clearInterval(timer);
      // Whatever is in flight belongs to a question that is no longer being asked — either the
      // panel unmounted or `deps` changed. Bumping the read id is what stops its outcome from
      // landing on a panel that has moved on.
      readIdRef.current += 1;
    };
  }, [active, reason, depsVersion, intervalMs, run]);

  return { state, data, error, refetch, lastUpdated };
}

export default usePanelState;
