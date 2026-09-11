/**
 * ═══════════════════════════════════════════════════════════════════════════
 * useLiveChannel — one subscription per channel, one selected slice per consumer
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 5.7. `design.md` §13.1, §13.2(a). Requirements 2.6, 7.5.
 *
 * THE PROBLEM THIS EXISTS FOR
 * ---------------------------
 * There is no global store in this app (`§1.12`). Each page holds its trading data in
 * page-level `useState` and subscribes to `wsClient` at the page root, so **one tick on
 * one channel re-renders the entire page tree** — on `Dashboard.jsx` that is 2012 lines
 * of JSX including two recharts surfaces. The fix in `§13.2` is not a store: it is to
 * move the subscription down to the leaf that renders the value. This hook is what makes
 * that affordable, because moving subscriptions down naively would mean one
 * `wsClient.subscribe` per rendered row.
 *
 * SO IT DOES TWO THINGS
 * ---------------------
 * 1. **Shares one underlying subscription per channel.** A module-level registry keyed on
 *    channel name holds the single `wsClient.subscribe` handler and fans each frame out
 *    to the consumers. Thirty `PnLDisplay` rows watching `pnl` produce one handler on the
 *    client, not thirty.
 * 2. **Returns only `selector(message)`, and re-renders only when that value changed.**
 *    The comparison is `Object.is`, and it happens *before* any state update is enqueued,
 *    so a `pnl` tick for BTC/USDT does not re-render a component watching ETH/USDT — it
 *    does not even schedule a render for it.
 *
 * A SELECTOR RETURNING `undefined` MEANS "NOTHING FOR ME IN THIS FRAME"
 * -------------------------------------------------------------------
 * The natural filtering selector is `(m) => m.symbol === mine ? m.mark_price : undefined`,
 * and it is the shape `§13.2` itself writes. Taken literally against an `Object.is` gate,
 * that selector would **wipe** the value it had: an ETH row displaying 3200.5 would blank
 * the instant a BTC tick arrived, because `undefined` is not `Object.is`-equal to 3200.5.
 * That is the very failure this hook exists to prevent, inverted, so `undefined` is read
 * as *declining the frame* and the previous value stands. It is what makes `§13.2`'s
 * "Object.is equal, SKIPPED" true for a row that already has a price, and not only for one
 * that has never received a tick.
 *
 * The consequence, stated so it is a decision rather than a surprise: a selected value
 * cannot *become* `undefined`. To say "this figure is gone", return `null` — which is also
 * the value the `Metric` not-available marker reads (`§11`). `initial` may still be
 * `undefined`; it simply stays that way until a frame yields something.
 *
 * The previous selected value is also passed as the selector's second argument, for a
 * selector that wants to be explicit (`(m, prev) => …`) or to derive from what it had. A
 * one-argument selector ignores it, which is the documented common case.
 *
 * `selector` MUST BE REFERENTIALLY STABLE
 * --------------------------------------
 * Declare it at module scope, or wrap it in `useCallback`:
 *
 * ```js
 * const pickMark = (m) => (m.symbol === 'BTC/USDT' ? m.mark_price : undefined);   // module scope
 * const mark = useLiveChannel('pnl', pickMark, null);
 *
 * const pickMine = useCallback((m) => (m.symbol === symbol ? m.mark_price : undefined), [symbol]);
 * const mark = useLiveChannel('pnl', pickMine, null);
 * ```
 *
 * An unstable selector is handled rather than punished: the selector is read through a ref
 * that is refreshed on every render, and the subscription is keyed on `channel` alone, so
 * a selector re-created inline every render causes **no** subscribe/unsubscribe churn and
 * never drops a frame. What it cannot do is re-derive retroactively — this hook keeps the
 * *selected value*, not the last raw frame, so a selector whose behaviour changes takes
 * effect on the next frame rather than immediately. For a selector that closes over
 * changing props (a symbol, a strategy id) that distinction matters, which is why
 * `useCallback` with those props in its dependency list is the documented form.
 *
 * WHY NOT `useSyncExternalStore`
 * -----------------------------
 * It wants a `getSnapshot` that is cheap, stable, and returns `Object.is`-equal values
 * across calls — which for a per-consumer selector over a push stream means building
 * exactly the per-consumer value cache below anyway, plus a subscribe signature that
 * cannot express the shared registry as directly. The explicit version is easier to
 * reason about and is what `§13.2(a)` specifies.
 *
 * A NOTE ON `wsClient.subscribe` vs `wsClient.subscribeChannel`
 * ------------------------------------------------------------
 * `subscribe(eventType, cb)` is the client-side routing table: it dispatches on a frame's
 * `type` / `event_type` and tells the server nothing. That is the right one for the
 * page-level tick streams this hook serves (`positions`, `pnl`, `orders`,
 * `STRATEGY_STATUS` — `§7.1`), which arrive on the session socket unconditionally.
 * `subscribeChannel(channel, handler)` is the other thing entirely: a server-side,
 * per-resource subscription that must be asked for and can be refused. This hook is not
 * that, and the word "channel" in its name is `design.md`'s vocabulary for the tick
 * streams, not `websocketClient`'s for the authorised ones.
 */

import { useEffect, useRef, useState } from 'react';

import wsClient from '../websocketClient';

/**
 * channel name → { listeners: Set<Function>, unsubscribe: Function }
 *
 * Module-level, so it is shared by every consumer in the bundle. The `listeners` set IS
 * the reference count: there is no separate integer to get out of step with it, a
 * release that arrives twice removes nothing the second time, and the underlying
 * subscription is torn down at exactly the moment the set empties. An integer counter is
 * how a channel handler ends up outliving every consumer.
 *
 * @type {Map<string, {listeners: Set<Function>, unsubscribe: Function}>}
 */
const registry = new Map();

/**
 * Take a hold on `channel` for `listener`, subscribing on the client if this is the
 * first hold. Returns the release for this hold.
 *
 * @param {string} channel
 * @param {Function} listener called with each frame on the channel
 * @returns {Function} release
 */
function acquire(channel, listener) {
  let entry = registry.get(channel);

  if (!entry) {
    entry = { listeners: new Set(), unsubscribe: null };
    // Registered before subscribing, so the fan-out below can find the entry even if the
    // client were to deliver a frame synchronously from inside `subscribe`.
    registry.set(channel, entry);
    entry.unsubscribe = wsClient.subscribe(channel, (message) => {
      /*
        One handler, N listeners — so a throwing listener must not swallow the frame for
        the listeners after it. With one `wsClient.subscribe` per component this was the
        client's problem and it already guards each callback; collapsing N subscriptions
        into one moves that responsibility here. Without this try/catch, a selector that
        throws on an unexpected payload would silently freeze every *other* consumer of
        the same channel, which is a far worse failure than the one that caused it.
      */
      for (const held of Array.from(entry.listeners)) {
        try {
          held(message);
        } catch (error) {
          console.error(`useLiveChannel: listener failed for channel ${channel}:`, error);
        }
      }
    });
  }

  entry.listeners.add(listener);
  return () => release(channel, listener);
}

/**
 * Drop one hold. The underlying subscription goes when the last hold goes.
 *
 * @param {string} channel
 * @param {Function} listener
 */
function release(channel, listener) {
  const entry = registry.get(channel);
  if (!entry) return;

  // `delete` reports whether anything was removed, which makes a double release a no-op
  // rather than an early teardown of a channel other components are still holding.
  if (!entry.listeners.delete(listener)) return;
  if (entry.listeners.size > 0) return;

  // Removed from the registry *before* unsubscribing: a component remounting during this
  // teardown (React 18 development remounts every effect once, and a channel change
  // unmounts the old hold before mounting the new) must build a fresh entry and take a
  // fresh subscription rather than adopt one that is in the middle of being dropped.
  registry.delete(channel);
  if (typeof entry.unsubscribe === 'function') entry.unsubscribe();
}

/**
 * Subscribe to one live channel and return only the selected slice of its frames.
 *
 * @param {string} channel the tick stream, e.g. `pnl`, `positions`, `orders`,
 *   `STRATEGY_STATUS`. A falsy channel subscribes to nothing and returns `initial`, so a
 *   leaf that has no id to watch yet is not a special case at the call site.
 * @param {Function} selector `(message, previous) => value`. Maps a frame to the value this
 *   component renders. Must be module-scope or `useCallback`-stable — see the note above.
 *   Return `undefined` (or the same value it already had) for a frame this component does
 *   not care about, and no render is scheduled. Return `null` to say a figure is gone.
 * @param {*} [initial] the value before the first frame that changes it
 * @returns {*} the currently selected value
 */
export function useLiveChannel(channel, selector, initial) {
  const [selected, setSelected] = useState(initial);

  // The last value handed to React. The `Object.is` gate reads this rather than `selected`
  // so the comparison sees the value from the frame that arrived a millisecond ago, not
  // the one from the last committed render — two frames inside one React batch would
  // otherwise both pass the gate.
  const lastRef = useRef(initial);

  // Refreshed every render, read only from the frame handler. This is what makes an
  // unstable selector cost nothing: the subscription does not depend on it.
  const selectorRef = useRef(selector);
  selectorRef.current = selector;

  // Kept so a channel change can reset to the ORIGINAL initial value rather than to
  // whatever `initial` happens to be on the render that changed the channel.
  const initialRef = useRef(initial);
  const channelRef = useRef(channel);

  useEffect(() => {
    if (channelRef.current !== channel) {
      channelRef.current = channel;
      /*
        A value selected from `positions` is not a value for `orders`. Requirement 14.5 is
        about exactly this class of mistake — a figure that is real but no longer current
        being rendered as though it were — so the switch clears rather than carries over,
        and the leaf shows its own not-yet-arrived state until the new channel speaks.
      */
      lastRef.current = initialRef.current;
      setSelected(initialRef.current);
    }

    if (!channel || typeof channel !== 'string') return undefined;

    const listener = (message) => {
      let next;
      try {
        next = selectorRef.current(message, lastRef.current);
      } catch (error) {
        // A selector that cannot read this frame yields nothing rather than a wrong
        // value; the previous one stands and the frame is dropped for this consumer only.
        console.error(`useLiveChannel: selector failed for channel ${channel}:`, error);
        return;
      }

      // The frame was declined — see the docblock. Without this line the ordinary
      // symbol-filtering selector would blank its own row on every other symbol's tick.
      if (next === undefined) return;

      // The whole point of the hook. Not `setSelected(next)` with a bail-out inside the
      // updater: an update that is enqueued and then bailed out of has still cost a
      // scheduled render, and on a channel ticking several times a second across dozens
      // of rows that is the cost this was built to remove.
      if (Object.is(lastRef.current, next)) return;
      lastRef.current = next;
      setSelected(next);
    };

    return acquire(channel, listener);
    // Only `channel`. `selector` and `initial` are read through refs rather than closed
    // over, which is both why `exhaustive-deps` is satisfied without a suppression and why
    // an inline selector causes no resubscribe: depending on it would tear the channel
    // down and rebuild it on every render for any caller passing one.
  }, [channel]);

  return selected;
}

/**
 * The channels currently held, in insertion order. For tests and diagnostics.
 * @returns {string[]}
 */
export function activeLiveChannels() {
  return Array.from(registry.keys());
}

/**
 * How many consumers are holding `channel` — i.e. its reference count.
 * @param {string} channel
 * @returns {number}
 */
export function liveChannelSubscriberCount(channel) {
  const entry = registry.get(channel);
  return entry ? entry.listeners.size : 0;
}

/**
 * Drop every hold and unsubscribe every channel.
 *
 * For a test between cases, and for a sign-out that tears the socket down: the registry
 * is module state, so nothing else would clear it.
 */
export function resetLiveChannels() {
  for (const [channel, entry] of Array.from(registry.entries())) {
    registry.delete(channel);
    entry.listeners.clear();
    if (typeof entry.unsubscribe === 'function') {
      try {
        entry.unsubscribe();
      } catch (error) {
        console.error(`useLiveChannel: unsubscribe failed for channel ${channel}:`, error);
      }
    }
  }
}

export default useLiveChannel;
