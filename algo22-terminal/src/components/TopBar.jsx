/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/components/TopBar.jsx — the shell's status bar
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 8.7. design.md §6.1, §6.5, §6.6, §1.3.
 * Requirements 2.5, 2.6, 14.5, 16.2, 18.1, 19.4.
 *
 * §6.1 gives this bar four regions and no others: page context on the left; connection
 * indicator, notification bell and UTC clock on the right.
 *
 * ---------------------------------------------------------------------------
 * 1. THE HARDCODED STATUS LIGHT IS GONE (Requirement 14.5, §1.3)
 * ---------------------------------------------------------------------------
 * This file rendered `<LiveStatusV2 status="running" />`. A literal. It never read
 * `wsClient`, so the bar claimed the trading engine was live whether or not a socket
 * existed — including while the backend was down and while the user was signed out. A
 * trader reads that light to decide whether the figures on screen are current, which is
 * why it was the shell's worst data-honesty defect and not a cosmetic one.
 *
 * `shell/ConnectionStatusIndicator` replaces it and takes NO status prop: it reads
 * `useConnectionStatus()` itself and refuses a supplied status in development. The
 * defect was that a caller could pass one, so the fix removes the parameter rather than
 * correcting the argument.
 *
 * `LiveStatusV2` itself is left in `ui-legacy/primitives.jsx`. This was its only call
 * site in `src/`, so it is now orphaned, and deleting orphaned legacy components is task
 * 27.3's job — not this file's.
 *
 * ---------------------------------------------------------------------------
 * 2. THE DISCONNECTED STRIP IS INLINE AND PERSISTENT, NOT A TOAST (§6.5)
 * ---------------------------------------------------------------------------
 * On any status in the error group — `disconnected`, `error`, `failed` — the bar renders
 * one full-width `ds/Alert` strip below itself with §6.5's copy and a retry that calls
 * `wsClient.connect()`. On `connecting` / `reconnecting` there is no strip: the
 * indicator alone carries a self-healing state, and a band for it would be the noise
 * Requirement 16.2 forbids.
 *
 * It is inline rather than a toast because the condition STANDS. `design/notificationPolicy.js`
 * is the allowlist for interrupting a trader and it is default-closed over BACKEND
 * events; a local socket drop is not one of the seven categories in it, and a toast for
 * a standing condition disappears while the condition persists — leaving stale figures
 * on screen with nothing beside them saying so. `EXCHANGE_DISCONNECTED` in that
 * allowlist is a different fact (the venue dropped the exchange connector) and stays a
 * toast; this is our own transport.
 *
 * The strip is not dismissable. `ds/Alert` renders a close button only when handed an
 * `onDismiss`, and it is not handed one: the trader cannot make the statement untrue,
 * and dismissing it would leave the fabrication this whole task removes.
 *
 * ---------------------------------------------------------------------------
 * 3. WHY THE STRIP CANNOT COLLIDE WITH `ResponsiveGate`'s
 * ---------------------------------------------------------------------------
 * `shell/ResponsiveGate` renders its own `Alert variant="strip"` for a route restricted
 * by viewport width (`/app/builder` below 1024px). The two never contend for one slot:
 *
 *   * The gate's strip is OUTSIDE the shell grid — it is the first child of the gate's
 *     flex column, above the sidebar and above this bar, and it is the reason that
 *     component's docblock tells task 8.5 to size the shell `height: 100%` rather than
 *     `100dvh`.
 *   * This strip is INSIDE this `<header>`, below the bar row, so it spans the shell's
 *     bar track and shifts only the content column beneath it.
 *
 * They also say different things — one is about the window, the other about the socket —
 * so both being on screen at once is two true statements in two places, not the
 * duplicate-band problem `ResponsiveGate`'s docblock warns task 24.7 about. Each is
 * rendered by exactly one component, and neither is rendered by a page.
 *
 * The one geometry consequence is stated for task 8.5 in {@link TOPBAR_HEIGHT_PX}: the
 * bar ROW is a fixed height and the strip flows below it inside this element, so the
 * shell's bar track must be `auto` / `min-content`, not a fixed `48px` that would clip
 * the strip. The bar row itself is exactly `TOPBAR_HEIGHT_PX` on every route at every
 * status, which is what Property 2 (task 8.9) measures.
 *
 * ---------------------------------------------------------------------------
 * 4. WHAT WAS REMOVED, AND WHY EACH ONE WENT (Requirement 19.4)
 * ---------------------------------------------------------------------------
 *   * `<LiveStatusV2 status="running" />` — see (1).
 *   * THE PROFILE BUTTON, its avatar circle and the divider beside it. It worked, so it
 *     is not dead in the Requirement 19.4 sense; it is a DUPLICATE. §6.1 gives this bar
 *     four regions and a profile control is not one of them, and §6.4 puts Profile in
 *     the `AccountMenu` anchored to the sidebar's bottom card (task 8.8) — which
 *     `Sidebar.jsx` already reserves and already fills with an account card carrying
 *     sign-out. Keeping it here also meant a SECOND `supabase.auth.getUser()` on every
 *     shell mount, for one letter, beside the sidebar's read of the same user.
 *   * `userInitial`'s `"Q"` DEFAULT. It rendered before any user had been read and
 *     survived a failed read, so an avatar could show an initial belonging to nobody.
 *     Nothing in this file substitutes a value it has not read (Requirement 14.5).
 *   * `unreadCount`'s `0` DEFAULT in the accessible name. "View notifications (0
 *     unread)" claimed a count the request may never have returned. Unknown is now
 *     `null`: no badge, and a name that does not mention a number.
 *   * THE `onMouseEnter` / `onMouseLeave` INLINE COLOUR MUTATION on both buttons —
 *     replaced by `hover:` utilities, which also restores hover feedback for the
 *     keyboard-focus case those handlers could not reach.
 *   * `focus:ring-cyan-400`, a Tailwind-default hue with no token behind it, replaced by
 *     the `focus-visible:outline-brand` treatment the `ds/` primitives use.
 *   * ALL 14 `C.*` REFERENCES AND THE ONE COLOUR LITERAL (`#000` on the badge). Both
 *     guard budgets for this file drop to 0.
 *
 * KEPT, because each is wired to something real: the UTC clock (the browser's own
 * clock), and the notification bell (`api.notifications.getUnreadCount()` for the count,
 * the `notification` socket frame for increments, `/app/notifications` for the
 * destination). The bell is now an `<a>` rather than a `<button>` calling `navigate()`,
 * for the reason §6.3 gives for the sidebar: middle-click, Ctrl-click and "copy link
 * address" work on a link and cannot be made to work on a button.
 *
 * ---------------------------------------------------------------------------
 * 5. THE UNREAD COUNT IS READ ONCE IN THE SHELL — FOR NOW
 * ---------------------------------------------------------------------------
 * `Sidebar.jsx` and this file each fetched `getUnreadCount()` independently. Task 8.6
 * removed the sidebar's (its `notifications` nav entry is gone), so the shell makes
 * exactly one such request today: this one. Task 8.8 adds `hooks/useUnreadNotifications`
 * so the bell and the account menu's `Notifications (3)` row share a single read —
 * {@link NotificationBell}'s effect is the whole of what moves into that hook, and
 * nothing outside it reads the count.
 *
 * ---------------------------------------------------------------------------
 * 6. THE PAGE CONTEXT COMES FROM THE TABLE (Requirement 2.5's sibling, §6.3)
 * ---------------------------------------------------------------------------
 * The left region is a `ds/Breadcrumb` over `shell/navigation.js`: the active entry's
 * group label, then its page title, both from `NAV_GROUPS`. No map is maintained here.
 * A route the table does not know renders nothing rather than a guessed heading —
 * `App.jsx`'s `/app/*` catch-all redirects those to the dashboard, and the bar's height
 * does not depend on the trail, so nothing moves either way. (`readTrail` is not needed
 * for that: the trail is inline in a fixed-height row, not a row of its own to reserve.)
 *
 * Its landmark is named "Page context", not "Breadcrumb" — `ds/PageHeader` names its own
 * trail "Breadcrumb", and two navigation landmarks with one name is a screen-reader
 * landmark list with two indistinguishable entries.
 *
 * The route TITLE inside the page body belongs to `ds/PageHeader`, which task 8.5 renders
 * in the route `Suspense` fallback from the same `routeTitle()`. This bar carries the
 * trail; that block carries the heading. One table, two surfaces.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Bell } from 'lucide-react';

import { api } from '../api';
import { useConnectionStatus } from '../hooks/useConnectionStatus';
import wsClient from '../websocketClient';

// Straight from the modules rather than through `ds/index.js`: the shell is in every
// route's import graph and the barrel re-exports 26 primitives, so importing it here
// would pull all 26 into the entry chunk. `shell/ResponsiveGate` imports `ds/Alert` the
// same way, for the same reason.
import { Alert } from './ds/Alert';
import { Breadcrumb } from './ds/Breadcrumb';
import {
  CONNECTION_DOWN_COPY,
  ConnectionStatusIndicator,
  isConnectionDown,
} from './shell/ConnectionStatusIndicator';
import { NAV_GROUPS, activeNavEntry, routeTitle } from './shell/navigation';

/**
 * The bar row's height, in pixels.
 *
 * ═══ NOTE FOR TASK 8.5 ═══
 *
 * 56, not design.md §6.2's `48px` grid row. `Sidebar.jsx` reserves a 56px brand block
 * (`SIDEBAR_BRAND_HEIGHT_PX`) and the sidebar spans BOTH grid rows (§6.1), so a 48px bar
 * puts the brand block's bottom border 8px below this bar's — a visible mis-seam across
 * the full width of the shell, on every route. The two numbers have to agree and 56 is
 * the one that is already load-bearing for the sidebar. `tests/unit/shell/topBar.test.jsx`
 * asserts the equality so the seam cannot drift apart later.
 *
 * The shell's bar TRACK must therefore be `auto` (or `min-content`) rather than a fixed
 * height: the disconnected strip flows below the bar row inside this component's
 * `<header>`, and a fixed track would clip it. That costs Property 2 nothing — the bar
 * row is exactly this tall on every route, and the strip's presence depends on the
 * socket, never on the route.
 */
export const TOPBAR_HEIGHT_PX = 56;

/**
 * The bar row's geometry, held against anything a caller passes.
 *
 * `boxSizing: 'border-box'` so the bottom border is inside the 56px rather than added to
 * it, and `min`/`max` as well as `height` so no content can stretch the row — the same
 * discipline `ds/PageHeader` applies to its reserved 64px block, and what Requirement
 * 2.2 needs from a shell element.
 */
const BAR_GEOMETRY = Object.freeze({
  height: `${TOPBAR_HEIGHT_PX}px`,
  minHeight: `${TOPBAR_HEIGHT_PX}px`,
  maxHeight: `${TOPBAR_HEIGHT_PX}px`,
  boxSizing: 'border-box',
});

/** Declared in `shell/navigation.js`'s `SECONDARY_ROUTE_TITLES`, which titles this route. */
const NOTIFICATIONS_PATH = '/app/notifications';

/**
 * The UTC clock.
 *
 * Its own component so the one-second tick re-renders eight characters rather than the
 * whole bar — with the clock inlined in `TopBar`, every second would re-render the
 * connection indicator and the bell too.
 *
 * `toISOString()` is UTC by definition, so the displayed time and the `dateTime`
 * attribute come from one call and cannot disagree. This is the BROWSER's clock, which
 * is what the label claims; it is not the exchange's server time, and nothing here
 * implies it is.
 */
function UtcClock() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const iso = now.toISOString();

  return (
    <time
      dateTime={iso}
      data-shell="topbar-clock"
      // Mono with `tabular-nums` so the digits are the same width from second to second
      // and the bar's right edge does not jitter (§6.6).
      className="shrink-0 font-mono text-micro tabular-nums text-content-secondary"
    >
      {`${iso.slice(11, 19)} UTC`}
    </time>
  );
}

/**
 * The notification bell.
 *
 * `unread === null` means NOT KNOWN — the request has not answered, or it failed. In that
 * state there is no badge and the accessible name mentions no number at all. A zero that
 * the server really reported is a fact and is said out loud ("Notifications, 0 unread");
 * the old code's `useState(0)` said it before anything had been read and kept saying it
 * after a failed read, which is the same sentence as a fabrication (Requirement 14.5).
 */
function NotificationBell() {
  const [unread, setUnread] = useState(null);

  useEffect(() => {
    let mounted = true;

    (async () => {
      try {
        const res = await api.notifications.getUnreadCount();
        const count = res?.unread_count;
        if (mounted && typeof count === 'number' && Number.isFinite(count) && count >= 0) {
          setUnread(count);
        }
      } catch {
        // Offline, unauthenticated, or the endpoint failed. The count stays unknown; it
        // does not become zero.
      }
    })();

    const unsub = wsClient.subscribe('notification', () => {
      // A `notification` frame IS a new unread row, so +1 on a known count is a real
      // increment, not an estimate. From an unknown count it stays unknown: `null + 1`
      // would invent the base, and the Notification Center holds the real list either way.
      setUnread((current) => (typeof current === 'number' ? current + 1 : current));
    });

    return () => {
      mounted = false;
      if (typeof unsub === 'function') unsub();
    };
  }, []);

  const known = typeof unread === 'number';
  const label = known
    ? `Notifications, ${unread} unread`
    : 'Notifications';

  return (
    <Link
      to={NOTIFICATIONS_PATH}
      aria-label={label}
      title={label}
      data-shell="topbar-notifications"
      data-unread={known ? String(unread) : 'unknown'}
      className={
        'relative flex h-7 w-7 shrink-0 items-center justify-center rounded-md '
        + 'text-content-secondary transition-colors hover:bg-surface-raised '
        + 'hover:text-content-primary focus-visible:outline-2 focus-visible:outline-offset-2 '
        + 'focus-visible:outline-brand'
      }
    >
      <Bell size={15} strokeWidth={1.75} aria-hidden="true" />
      {/* Only when a real count came back and it is not zero. A count is a figure, so it
          is mono; `aria-hidden` because the link's accessible name already carries it and
          announcing both would read the number twice. */}
      {known && unread > 0 ? (
        <span
          aria-hidden="true"
          className={
            'absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center '
            + 'rounded-full border border-line-default bg-surface-raised px-1 font-mono '
            + 'text-micro font-semibold tabular-nums text-brand'
          }
        >
          {unread > 99 ? '99+' : unread}
        </span>
      ) : null}
    </Link>
  );
}

/**
 * The shell's top bar.
 *
 * ═══ THE PROP SIGNATURE FOR TASK 8.5 ═══
 *
 *     <TopBar />
 *
 * No required props, and none that can change what it reports. Route context comes from
 * `useLocation()` and the connection state from `useConnectionStatus()`, so the shell has
 * nothing to thread through and no way to make the bar claim anything. `className` and
 * `style` are accepted for grid placement (`style={{ gridArea: 'bar' }}`) and cannot
 * reopen the bar row's height.
 *
 * @param {Object} props
 * @param {string} [props.className] Appended to the `<header>`.
 * @param {Object} [props.style] Merged onto the `<header>`, not the bar row.
 */
export default function TopBar({ className = '', style, ...rest }) {
  const location = useLocation();
  const status = useConnectionStatus();
  const down = isConnectionDown(status);

  // The trail, from `shell/navigation.js` and nowhere else: the group that owns the
  // active entry, then the entry. Neither crumb carries a `to` — a group has no route,
  // and `ds/Breadcrumb` never links its last crumb because a link to the current page is
  // a control that does nothing (Requirement 19.4).
  const trail = useMemo(() => {
    const pathname = location.pathname;
    const entry = activeNavEntry(pathname);
    if (entry !== null) {
      const group = NAV_GROUPS.find((candidate) => candidate.entries.includes(entry));
      const crumbs = group === undefined ? [] : [{ label: group.label }];
      return [...crumbs, { label: entry.label }];
    }
    // A secondary route (`/app/billing`, `/app/profile`, …) has a title and no group.
    const title = routeTitle(pathname);
    return title === null ? [] : [{ label: title }];
  }, [location.pathname]);

  const retry = useCallback(() => {
    // `acquiredPath` is the path a `wsClient.acquire()` holder connected on; reusing it
    // means a retry reconnects the socket the session actually wants rather than the
    // default telemetry endpoint. `|| undefined` so `connect()`'s own default applies
    // when nothing holds the connection.
    wsClient.connect(wsClient.acquiredPath || undefined);
  }, []);

  return (
    <header
      data-shell="topbar"
      data-connection-down={down ? 'true' : 'false'}
      className={`flex w-full min-w-0 shrink-0 flex-col ${className}`.trim()}
      style={style}
      {...rest}
    >
      <div
        data-shell="topbar-bar"
        className="flex w-full min-w-0 items-center gap-3 border-b border-line-default bg-surface-panel px-4"
        style={BAR_GEOMETRY}
      >
        {/* Left: page context. `min-w-0` on both this and the crumbs is what makes a long
            trail truncate instead of pushing the right-hand controls off the bar. */}
        <div className="flex min-w-0 flex-1 items-center">
          <Breadcrumb items={trail} label="Page context" />
        </div>

        {/* Right, in §6.1's order. */}
        <div className="flex shrink-0 items-center gap-3">
          <ConnectionStatusIndicator />
          <NotificationBell />
          <UtcClock />
        </div>
      </div>

      {/* §6.5's strip. `severity="error"` earns `role="alert"` from `ds/Alert`, which is
          assertive — correct here and only here in this file: the figures a trader is
          reading may no longer be current, which is worth interrupting for. No
          `onDismiss`, so there is no close button (see the docblock). */}
      {down ? (
        <Alert
          severity="error"
          variant="strip"
          data-shell="connection-strip"
          title={CONNECTION_DOWN_COPY.title}
          action={{ label: CONNECTION_DOWN_COPY.retryLabel, onClick: retry }}
        >
          {CONNECTION_DOWN_COPY.detail}
        </Alert>
      ) : null}
    </header>
  );
}
