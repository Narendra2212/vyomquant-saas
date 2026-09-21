/**
 * ═══════════════════════════════════════════════════════════════════════════
 * useNotificationStream — the ONE toast transport for backend events
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 10.1. design.md §11.5. Requirements 16.1, 16.2.
 *
 * ---------------------------------------------------------------------------
 * WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
 * ---------------------------------------------------------------------------
 * This module is TRANSPORT. It subscribes to the socket, hands every frame it receives to
 * `design/notificationPolicy.notificationFor`, and calls `window.showToast` only when the
 * answer is non-null. It makes no decision about *whether* an event deserves a toast, what
 * it should say, or how loudly — all three live in `notificationPolicy.js`, as data, and a
 * second copy of any of them here would be the thing Requirement 16.2 is trying to stop.
 *
 * Requirement 16.2 is satisfied by TWO structural facts together, and neither is sufficient
 * alone:
 *
 *   1. the allowlist is **default-closed** — `notificationFor` returns `null` for anything
 *      it does not recognise, so an event type the backend adds tomorrow raises nothing
 *      until somebody declares it in `NOTIFIABLE`; and
 *   2. the transport is called from **exactly one place** — this file. A page that toasted
 *      a backend event itself would route around (1) entirely.
 *
 * Pages may still call `window.showToast` for their OWN action outcomes ("Saved as version
 * 7", "Session paused"). Those are not backend events and are not what 16.2 is about.
 *
 * ---------------------------------------------------------------------------
 * WHY EVENT-TYPE SUBSCRIPTIONS AND NOT `subscribeChannel`
 * ---------------------------------------------------------------------------
 * `websocketClient` has two routing tables over one socket:
 *
 *   * `subscribe(eventType, cb)` — client-side only. Routes on `message.type ||
 *     message.event_type`, tells the server nothing, cannot be refused.
 *   * `subscribeChannel(channel, cb)` — routes on `message.channel` AND sends
 *     `{action: 'subscribe', channel}`, which the server may answer with a
 *     `subscription_refused` frame.
 *
 * This hook uses the FIRST, for three reasons:
 *
 *   * **It cannot double a toast.** `processMessage` routes one frame to the event-type
 *     bucket for its `type` and, separately, to the handlers of the channel it names. A
 *     frame carries one `type`, so one frame reaches this handler at most once. Holding
 *     both an event-type subscription for `order_rejected` and a channel subscription for
 *     `execution_events` would deliver the same frame twice and toast it twice — a
 *     user-visible defect, for no gain.
 *   * **It asks the socket for nothing.** A notification transport that could provoke a
 *     refusal frame, or that changed which channels the session holds, would be a
 *     side effect on every other subscriber of the socket. This one is a read.
 *   * **It is not a second opinion about authorisation.** Which channels a session may
 *     hold is `core/websocket_auth`'s answer and the pages' business. This hook classifies
 *     whatever arrives.
 *
 * ---------------------------------------------------------------------------
 * WHICH EVENT TYPES, AND WHERE THE LIST COMES FROM
 * ---------------------------------------------------------------------------
 * From the policy's own tables, not from a list maintained here — that is the difference
 * between "transport" and "a second policy":
 *
 *   * every key of `EVENT_TYPE_KEY` — `deploy_success`, `strategy_deployed`,
 *     `deploy_failed`, `exchange_disconnected`, `order_rejected`, `strategy_stopped`,
 *     `bot_stopped`, `backtest_complete`, `subscription_expired`. These are the frame
 *     types `ws_channels.EventType` puts on the `deployment_events` and
 *     `execution_events` channels, plus the `dispatch_user_notification` event names, plus
 *     the Backtester poller's own transition event. Reading the table means a type added
 *     to the allowlist is subscribed the same commit it is declared, with nothing to
 *     remember.
 *   * `notification` and `subscription_update` — the two ENVELOPE names. A `notification`
 *     frame carries the row on `data` and its discriminating type INSIDE that row, so the
 *     outer type is all `subscribe` can route on; `subscription_update` carries its
 *     discriminator on `event`. `notificationPolicy.js` knows both shapes (its
 *     `ENVELOPE_TYPES`) but does not export them, so these two are the only names written
 *     down here. If a third envelope is ever added to the policy, this list has to be
 *     told — recorded as the one coupling that is not derived.
 *
 * `notificationFor` unwraps all of it. Nothing here reads a payload field.
 *
 * ---------------------------------------------------------------------------
 * WHY A REF-COUNTED MODULE STORE
 * ---------------------------------------------------------------------------
 * The same shape as `useUnreadNotifications`, for a stronger reason. That hook shares one
 * read; this one enforces that a frame produces ONE toast:
 *
 *   * The FIRST consumer to mount installs the subscriptions. Every later consumer is
 *     counted and installs nothing, so a page that mounted a second copy — by accident, or
 *     because it was moved — cannot double every toast in the product. The subscriptions
 *     exist once per session no matter how many callers hold them.
 *   * The LAST consumer to unmount releases them, using the unsubscribe functions
 *     `wsClient.subscribe` returned. A hook that left them behind would keep toasting
 *     after the shell came down.
 *
 * It is mounted in `ShellGrid` (`App.jsx`), once, which is the only place it needs to be.
 * The ref count is what makes "once" a property of this module rather than a convention
 * about where the call site is.
 *
 * ---------------------------------------------------------------------------
 * `window.showToast` IS READ AT CALL TIME, NOT CAPTURED
 * ---------------------------------------------------------------------------
 * Load-bearing, and not a style preference. `AppShell` installs `window.showToast` in an
 * effect, and `ShellGrid` is its CHILD — React runs child effects before parent effects, so
 * at the moment this hook subscribes the transport does not exist yet. Capturing it here
 * would capture `undefined` and silently drop every toast for the life of the session.
 *
 * Reading it per event also means the absence of a host is handled honestly: mounted
 * outside `AppShell` (a test, a page rendered standalone) there is nothing to call, and a
 * notification is dropped rather than crashing the socket handler. `NotificationCenter`
 * holds the full history either way — this transport governs *interruption*, not the
 * record.
 */

import { useEffect } from 'react';

import { EVENT_TYPE_KEY, notificationFor } from '../design/notificationPolicy';
import wsClient from '../websocketClient';

/**
 * The two envelope frame types. See the header: the policy understands both shapes but
 * does not export their names, so these are the one thing this module states rather than
 * derives.
 */
export const ENVELOPE_EVENT_TYPES = Object.freeze(['notification', 'subscription_update']);

/**
 * Every frame type this hook subscribes to: the two envelopes plus every event type the
 * allowlist can resolve.
 *
 * Exported so a test — and task 10.2's property test — can assert the subscription set
 * covers the policy without reaching into the module's internals.
 */
export const NOTIFICATION_EVENT_TYPES = Object.freeze([
  ...ENVELOPE_EVENT_TYPES,
  ...Object.keys(EVENT_TYPE_KEY),
]);

/**
 * The one store. Module-level on purpose — see the header.
 *
 * @type {{mounts: number, releases: Array<() => void>}}
 */
const store = {
  mounts: 0,
  releases: [],
};

/**
 * Hand one frame to the policy, and to the toast host only if the policy said so.
 *
 * `notificationFor` is total: `null`, a non-object, an unknown type and a row carrying a
 * category the allowlist does not pair with its type all come back `null`. So there is no
 * validation in front of it here — a second opinion about what a well-formed event looks
 * like is exactly the duplicated decision this module must not hold.
 *
 * @param {unknown} event A socket frame, so genuinely unknown.
 * @returns {void}
 */
function handleEvent(event) {
  const notification = notificationFor(event);
  // `null` means: not one of Requirement 16.1's seven. Nothing is raised, nothing is
  // logged — an unclassified event is the normal case, not an error.
  if (notification === null) return;

  if (typeof window === 'undefined' || typeof window.showToast !== 'function') return;
  // `(type, message)` — `AppShell`'s installed signature, unchanged. `severity` is already
  // the toast vocabulary; the policy declares it per key precisely so the client decides
  // how loudly to speak rather than echoing the backend's own severity word.
  window.showToast(notification.severity, notification.message);
}

/** Install the subscriptions. First consumer only. */
function startStream() {
  store.releases = NOTIFICATION_EVENT_TYPES.map((eventType) =>
    wsClient.subscribe(eventType, handleEvent),
  ).filter((release) => typeof release === 'function');
}

/** Release them. Last consumer only. */
function stopStream() {
  const releases = store.releases;
  store.releases = [];
  for (const release of releases) release();
}

/**
 * Subscribe the product's one toast transport to the socket.
 *
 * Mounted once, in the shell. Renders nothing, returns nothing, and holds no state: a
 * frame arriving must not re-render the shell, because the toast host is where a
 * notification becomes visible and it already has its own state.
 *
 * @returns {void}
 */
export function useNotificationStream() {
  useEffect(() => {
    store.mounts += 1;
    if (store.mounts === 1) startStream();

    return () => {
      store.mounts -= 1;
      if (store.mounts === 0) stopStream();
    };
  }, []);
}

/**
 * Drop the subscriptions unconditionally.
 *
 * For tests, which need a clean store between cases because it is module state — the same
 * reason `useUnreadNotifications` exports `resetUnreadNotifications`. Application code
 * never calls this.
 *
 * @returns {void}
 */
export function resetNotificationStream() {
  store.mounts = 0;
  stopStream();
}

export default useNotificationStream;
