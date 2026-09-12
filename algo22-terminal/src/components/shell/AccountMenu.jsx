/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/components/shell/AccountMenu.jsx — where the seven deferred routes live
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 8.8. design.md §6.4, §6.1, §11.6, §11.7.
 * Requirements 2.1, 18.1, 18.2, 18.3, 18.4, 19.4.
 *
 * ---------------------------------------------------------------------------
 * WHAT THIS FIXES
 * ---------------------------------------------------------------------------
 * Requirement 2.1 caps the sidebar at ten entries and task 8.6 rebuilt it to exactly
 * those ten. Seven routes that were previously reachable from the sidebar were left
 * reachable from nothing at all: `/app/profile`, `/app/security-logs`, `/app/billing`,
 * `/app/exchange`, `/app/risk`, `/app/notifications`, `/app/support`. Every one of them
 * still exists in `App.jsx` and still works. Between 8.6 and this file they were orphaned
 * — a page a trader can reach only by typing its URL is, in practice, a page that is gone.
 *
 * This is the popover §6.4 specifies, anchored to the sidebar's bottom user card, and it
 * is the ONLY entry point to those seven. Nothing in it is decoration.
 *
 * It also carries SIGN OUT, which is not decoration either: it is the only route out of
 * the authenticated app and it exists nowhere else in `src/`. The implementation is moved
 * verbatim from `Sidebar.jsx`'s `SidebarAccount` — same order, same three side effects,
 * same destination. It is auth-adjacent, so it was copied and not reconsidered.
 *
 * ---------------------------------------------------------------------------
 * WHY NOT `role="menu"` / `role="menuitem"` — THE COMMON MISTAKE
 * ---------------------------------------------------------------------------
 * Seven of the nine rows are LINKS to routes, so this is the disclosure pattern: a
 * `<button aria-expanded aria-controls>` revealing a labelled `<nav>` of real anchors.
 * `role="menu"` was considered and rejected for three reasons, each independently
 * sufficient:
 *
 *   1. **`role="menuitem"` on an `<a href>` destroys the link.** A role replaces the
 *      element's implicit one, so a screen reader stops announcing "link", the anchor
 *      leaves the links list, and the affordances that go with being a link go with it.
 *      The whole reason these rows are anchors — the argument §6.3 makes for the sidebar —
 *      is that middle-click, Ctrl-click and "copy link address" work on a link and cannot
 *      be made to work on anything else. Claiming they are menu items would announce that
 *      away while keeping the behaviour, which is the worst of the two.
 *   2. **`menu` means a list of commands**, i.e. an application menu's descendants. This
 *      is site navigation with one action (sign out) at the end of it.
 *   3. **The `menu` keyboard contract contradicts a trapped popover.** In a real menu, Tab
 *      leaves and arrow keys are the only navigation. Here focus is TRAPPED (Requirement
 *      18.3, §11.7), so Tab cycles inside — `role="menu"` would promise a Tab behaviour
 *      this surface deliberately does not have.
 *
 * So: `aria-expanded` on the trigger, `aria-controls` pointing at the panel, and no
 * `aria-haspopup` — `aria-haspopup="true"` is a synonym for `"menu"` and would promise
 * exactly the widget this is not. Arrow-key navigation is still implemented, because a
 * keyboard user meeting a vertical list of controls will try it, and Home/End with it.
 * Being usable by arrow keys is a courtesy; ANNOUNCING a menu one has not implemented is a
 * lie to assistive technology.
 *
 * That handler is on `document` rather than on the panel element, which is the same choice
 * `useFocusTrap` makes and for the same reason: focus can legitimately be sitting on the
 * panel container itself (the trap's zero-focusable case), where a listener bound to the
 * rows would never see the key.
 *
 * ---------------------------------------------------------------------------
 * IT IS A PORTAL, AND WHY IT HAS TO BE
 * ---------------------------------------------------------------------------
 * `Sidebar.jsx`'s root carries `overflow-hidden`, and `App.jsx` wraps the sidebar column
 * in `overflow: hidden` as well. An absolutely-positioned popover inside the 56px footer
 * slot would therefore be clipped to that slot, twice. So the panel is portalled to
 * `document.body` and positioned `fixed` from the trigger's own rect at
 * `var(--z-dropdown)`.
 *
 * It opens UPWARD AND TO THE RIGHT because of where the anchor is: the sidebar is the
 * leftmost column and the slot is its last row, so there is no room below it and none to
 * its left. {@link popoverPosition} is the whole of that decision, kept pure so the
 * geometry is testable in jsdom — which performs no layout, so every
 * `getBoundingClientRect()` there is zeroes and a rendered assertion would prove nothing.
 * It clamps horizontally against the viewport, which is what keeps the panel on screen at
 * the 56px rail width (`sidebarMode === 'rail'`, 768–1023px), where the trigger's left
 * edge IS the viewport's.
 *
 * ---------------------------------------------------------------------------
 * DOCUMENTATION IS ABSENT, NOT DISABLED, WHEN IT IS NOT CONFIGURED (Requirement 19.4)
 * ---------------------------------------------------------------------------
 * The old `docs` nav entry ran `window.open(import.meta.env.VITE_DOCS_URL)` from inside
 * the nav click handler. With the env var unset that is `window.open(undefined)` — a
 * control that looked live, took a click and did nothing. Requirement 19.4 forbids
 * shipping that, so an unset URL means the ROW IS NOT RENDERED. Not greyed out, not
 * present with a tooltip: absent. A disabled row is still an answer to "where are the
 * docs?", and the honest answer is that this build does not have any.
 *
 * {@link readDocsUrl} also refuses anything that is not `http(s)`, which drops the
 * `javascript:` and `data:` cases a mis-set environment variable could otherwise turn into
 * a link in the shell.
 *
 * ---------------------------------------------------------------------------
 * ONE COUNT, ONE REGISTRY, ONE FOCUS TRAP — ALL SHARED
 * ---------------------------------------------------------------------------
 *   * The Notifications row's count is `useUnreadNotifications()`, the same store the top
 *     bar's bell reads. Two requests for one number is what task 8.8 removes; that hook's
 *     docblock states how one request is shared and what unmounting one consumer does.
 *   * Focus containment, Escape and the return of focus to the trigger are
 *     `hooks/useFocusTrap.js` — the hook `ConfirmDialog` and `Drawer` already use, not a
 *     third implementation of the same four awkward cases.
 *   * The open claim goes through `ds/overlayRegistry`, so this popover and a confirmation
 *     cannot be on screen together with two traps fighting over the keyboard (Requirement
 *     17.3). It claims `OVERLAY_KIND.DRAWER` because those are the only two kinds the
 *     registry arbitrates and §6.4 calls this "a `ds/Drawer`-family component"; adding a
 *     third kind would change a contract `tests/unit/ds/overlayRegistry.test.js` pins.
 *
 * ---------------------------------------------------------------------------
 * THE LABELS COME FROM THE ROUTE TABLE
 * ---------------------------------------------------------------------------
 * Every row's label is `SECONDARY_ROUTE_TITLES[path]` from `shell/navigation.js`, which is
 * also what the shell's breadcrumb and the route `Suspense` fallback read. That table's own
 * note — "copy matches §6.4's account-menu labels so the same page is not called two
 * things" — is only true if one of the two reads the other, so this file declares paths and
 * icons and looks the words up. A path the table does not know is dropped with a console
 * error rather than rendered under a guessed name.
 */

import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate } from 'react-router-dom';
import {
  Bell,
  BookOpen,
  ChevronDown,
  ChevronUp,
  CreditCard,
  ExternalLink,
  FileClock,
  LifeBuoy,
  Link2,
  LogOut,
  SlidersHorizontal,
  User,
} from 'lucide-react';

import { api } from '../../api';
import { cssVar } from '../../design/tokens';
import { collectFocusable, useFocusTrap } from '../../hooks/useFocusTrap';
import { useUnreadNotifications } from '../../hooks/useUnreadNotifications';
import { supabase } from '../../supabase';

// Straight from the module rather than through `ds/index.js`: the shell is in every route's
// import graph and the barrel re-exports 26 primitives, so importing it here would pull all
// 26 into the entry chunk. `TopBar.jsx` and `shell/ResponsiveGate.jsx` reach for their
// `ds/` modules the same way, for the same reason.
import { OVERLAY_KIND, releaseOverlay, requestOverlay } from '../ds/overlayRegistry';

import { SECONDARY_ROUTE_TITLES } from './navigation';
import { useViewportAccess } from './ResponsiveGate';

/** The panel's width. Wide enough for "Exchange accounts" at `--text-small` with its icon. */
export const ACCOUNT_MENU_WIDTH_PX = 232;

/** The panel is kept off the viewport's edges by this much, at every width. */
const VIEWPORT_GUTTER_PX = 8;

/** Between the trigger's top edge and the panel's bottom edge. */
const TRIGGER_GAP_PX = 6;

/**
 * With less room than this above the trigger, the panel stops being anchored to it and is
 * pinned to the viewport instead. See {@link popoverPosition}.
 */
const MIN_PANEL_HEIGHT_PX = 160;

/** The trigger's accessible name. It is the whole name at the 56px rail, where it is icon-only. */
const TRIGGER_LABEL = 'Account and settings';

/** The panel's accessible name, carried by the `<nav>` landmark inside it. */
const PANEL_LABEL = 'Account';

/** The Notifications row is the one that carries a count. Matched by path, not by position. */
const NOTIFICATIONS_PATH = '/app/notifications';

/**
 * The rows, in §6.4's order and grouping. `label` is NOT written here — see the docblock.
 *
 * `Documentation` is absent from this table because it is not a route: it is external, it is
 * conditional on configuration, and it is rendered separately below.
 */
const MENU_GROUPS = Object.freeze([
  Object.freeze({
    id: 'account',
    heading: 'Account',
    items: Object.freeze([
      Object.freeze({ path: '/app/profile', icon: User }),
      Object.freeze({ path: '/app/security-logs', icon: FileClock }),
      Object.freeze({ path: '/app/billing', icon: CreditCard }),
    ]),
  }),
  Object.freeze({
    id: 'trading-setup',
    heading: 'Trading setup',
    items: Object.freeze([
      Object.freeze({ path: '/app/exchange', icon: Link2 }),
      Object.freeze({ path: '/app/risk', icon: SlidersHorizontal }),
    ]),
  }),
  Object.freeze({
    // §6.4's third block carries no heading — the rule above it is the grouping. An invented
    // heading ("Help", "Other") would be copy this spec does not have.
    id: 'help',
    heading: null,
    items: Object.freeze([
      Object.freeze({ path: NOTIFICATIONS_PATH, icon: Bell }),
      Object.freeze({ path: '/app/support', icon: LifeBuoy }),
    ]),
  }),
]);

/**
 * The seven internal destinations, flat, in render order.
 *
 * Exported because it is the claim this task exists to make good on: a test can assert that
 * every route §6.4 defers is reachable from here without walking the JSX.
 */
export const ACCOUNT_MENU_PATHS = Object.freeze(
  MENU_GROUPS.flatMap((group) => group.items.map((item) => item.path)),
);

/**
 * `VITE_DOCS_URL`, or `null` when this build has no documentation site.
 *
 * Read at call time rather than captured at module load, so a test can drive both halves
 * with `vi.stubEnv` — the same reason `ds/overlayRegistry` and `design/errorCopy` read
 * `import.meta.env` inside a function.
 *
 * @returns {string|null} An absolute `http`/`https` URL, or `null`.
 */
export function readDocsUrl() {
  const raw = import.meta.env?.VITE_DOCS_URL;
  const trimmed = typeof raw === 'string' ? raw.trim() : '';
  if (trimmed === '') return null;
  try {
    const url = new URL(trimmed);
    // A `javascript:` or `data:` value in an env var must not become a link in the shell.
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return null;
    return url.href;
  } catch {
    // Not an absolute URL. A relative one would resolve against the app's own origin and
    // land on `App.jsx`'s `/app/*` catch-all, which is a broken link with extra steps.
    return null;
  }
}

/**
 * Where the panel goes, given the trigger's rect and the viewport.
 *
 * Pure and total: an unreadable input falls back to the viewport-pinned placement rather
 * than producing `NaN` and an unpositioned panel. jsdom performs no layout, so this is the
 * only honest way to assert the geometry — a rendered `getBoundingClientRect()` there is
 * always zeroes.
 *
 * The panel is placed by `left` and `bottom`, so it GROWS UPWARD from the trigger: the
 * anchor is the last row of the leftmost column, so down and left are both off-screen.
 *
 * @param {{top: number, left: number}|null} rect The trigger's bounding rect.
 * @param {{width: number, height: number}} viewport
 * @returns {{left: number, bottom: number, maxHeight: number}} All in CSS pixels.
 */
export function popoverPosition(rect, viewport) {
  const viewportWidth = Number.isFinite(viewport?.width) ? viewport.width : 0;
  const viewportHeight = Number.isFinite(viewport?.height) ? viewport.height : 0;

  // The rightmost left edge that still leaves a gutter. It is negative when the viewport is
  // narrower than the panel plus its gutters — below the app's 768px floor, so it cannot
  // happen in the shell, but `Math.max` keeps the answer on screen rather than off the left
  // edge if it ever does.
  const rightmost = viewportWidth - ACCOUNT_MENU_WIDTH_PX - VIEWPORT_GUTTER_PX;
  const anchorLeft = Number.isFinite(rect?.left) ? rect.left : 0;
  const left = Math.max(VIEWPORT_GUTTER_PX, Math.min(anchorLeft, rightmost));

  const anchorTop = Number.isFinite(rect?.top) ? rect.top : 0;
  const roomAbove = anchorTop - VIEWPORT_GUTTER_PX - TRIGGER_GAP_PX;

  if (roomAbove < MIN_PANEL_HEIGHT_PX) {
    // Not enough room above the trigger to anchor to it — a short viewport, or a rect we
    // could not measure. Pin to the viewport and let the panel scroll internally. Never a
    // negative `maxHeight`, which would collapse the panel and hide every row in it.
    return {
      left,
      bottom: VIEWPORT_GUTTER_PX,
      maxHeight: Math.max(0, viewportHeight - 2 * VIEWPORT_GUTTER_PX),
    };
  }

  return {
    left,
    bottom: Math.max(VIEWPORT_GUTTER_PX, viewportHeight - anchorTop + TRIGGER_GAP_PX),
    maxHeight: roomAbove,
  };
}

/** The panel's placement, read from the live trigger. */
function measure(trigger) {
  const rect = typeof trigger?.getBoundingClientRect === 'function'
    ? trigger.getBoundingClientRect()
    : null;
  return popoverPosition(rect, {
    width: typeof window === 'undefined' ? 0 : window.innerWidth,
    height: typeof window === 'undefined' ? 0 : window.innerHeight,
  });
}

/** Shared by every row: one hit area, hover feedback, and a real focus ring. */
const ROW_CLASS =
  'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-small '
  + 'text-content-secondary transition-colors hover:bg-surface-raised '
  + 'hover:text-content-primary focus-visible:outline-2 focus-visible:outline-offset-2 '
  + 'focus-visible:outline-brand';

/**
 * Who is signed in, and on what plan. `{name, tier, initial}`, any of which may be unknown.
 *
 * Moved from `Sidebar.jsx`'s `SidebarAccount` unchanged, including the two things it
 * refuses to do: it does not default the name to "Quant Trader" and it does not default an
 * unreadable plan to "FREE TIER" — that guess was wrong for every paying account whose read
 * failed (Requirement 14.5).
 *
 * `[]`, not `[location.pathname]`: the pre-8.6 sidebar re-read the profile and the billing
 * entitlements on EVERY navigation, two requests per route change for a name and a plan word
 * that cannot have changed.
 */
function useAccountIdentity() {
  const [identity, setIdentity] = useState({ name: null, tier: null, initial: '·' });

  useEffect(() => {
    let mounted = true;

    (async () => {
      try {
        const { data } = await supabase.auth.getUser();
        const user = data?.user;
        if (!mounted || !user) return;

        const name = user.user_metadata?.full_name || user.email?.split('@')[0] || null;

        let tier = null;
        try {
          const entitlements = await api.billing.getEntitlements();
          if (entitlements?.plan?.name) tier = `${entitlements.plan.name.toUpperCase()} TIER`;
        } catch {
          // A plan we could not read is left unstated rather than defaulted.
        }

        if (mounted) {
          setIdentity({ name, tier, initial: name ? name.charAt(0).toUpperCase() : '·' });
        }
      } catch {
        // Signed out, or Supabase unconfigured. The trigger renders its unknown state.
      }
    })();

    return () => {
      mounted = false;
    };
  }, []);

  return identity;
}

/**
 * One internal row. A real `<a>`, from react-router's `Link`.
 *
 * @param {Object} props
 * @param {string} props.path
 * @param {string} props.label
 * @param {React.ComponentType} props.icon
 * @param {string} [props.accessibleName] Overrides the visible label as the accessible name.
 *   Used only by the Notifications row, whose name carries the count; the visible text is a
 *   prefix of it, so the label-in-name relationship holds.
 * @param {React.ReactNode} [props.trailing] Rendered at the row's right edge.
 * @param {() => void} props.onNavigate Closes the panel — a row that left it open would put
 *   the popover on top of the page it had just navigated to.
 */
function MenuLink({ path, label, icon: Icon, accessibleName, trailing = null, onNavigate, ...rest }) {
  return (
    <li>
      <Link
        to={path}
        onClick={onNavigate}
        aria-label={accessibleName}
        data-account-menu-item={path}
        className={ROW_CLASS}
        {...rest}
      >
        <Icon size={14} strokeWidth={1.75} aria-hidden="true" className="shrink-0" />
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {trailing}
      </Link>
    </li>
  );
}

/**
 * The account menu: the trigger that fills the sidebar's footer slot, and its popover.
 *
 * Takes no required props, so `<AccountMenu />` is a complete mount — which is what
 * `App.jsx` hands to `Sidebar`'s `accountMenu` slot.
 *
 * @param {Object} props
 * @param {string} [props.className] Appended to the trigger's class list.
 */
export default function AccountMenu({ className = '' }) {
  const instanceId = useId();
  const panelId = `${instanceId}-panel`;
  const navigate = useNavigate();

  // The gate's decision, not a breakpoint of our own — the same read `Sidebar` makes, so the
  // trigger and the track it sits in cannot disagree about where the rail begins.
  const { sidebarMode } = useViewportAccess();
  const rail = sidebarMode === 'rail';

  const identity = useAccountIdentity();
  const { unread, known: unreadKnown, label: unreadLabel, badge } = useUnreadNotifications();

  const triggerRef = useRef(null);
  const panelRef = useRef(null);

  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState(null);

  /*
   * The registry has the final say on whether the panel renders (Requirement 17.3), and the
   * panel renders only once granted — so a refused popover never paints at all rather than
   * flashing and vanishing. Same shape as `ConfirmDialog`'s and `Drawer`'s claim.
   */
  const [granted, setGranted] = useState(false);
  useEffect(() => {
    if (open !== true) {
      setGranted(false);
      return undefined;
    }
    setGranted(requestOverlay({
      id: instanceId,
      kind: OVERLAY_KIND.DRAWER,
      label: PANEL_LABEL,
    }));
    return () => {
      releaseOverlay(instanceId);
      setGranted(false);
    };
  }, [open, instanceId]);

  const active = open === true && granted === true;

  const close = useCallback(() => setOpen(false), []);

  /*
   * Measured on open, and again on resize while open: the trigger is pinned to the bottom of
   * a full-height column, so a window resize moves it. A LAYOUT effect, so the measured
   * placement is in the DOM before the browser paints — with a plain effect the panel's first
   * frame would be the unmeasured fallback and the trader would see it jump. No `scroll`
   * listener: the shell grid does not scroll, only the content column inside it does.
   */
  useLayoutEffect(() => {
    if (!active) {
      setPosition(null);
      return undefined;
    }
    const sync = () => setPosition(measure(triggerRef.current));
    sync();
    window.addEventListener('resize', sync);
    return () => window.removeEventListener('resize', sync);
  }, [active]);

  useFocusTrap({
    active,
    containerRef: panelRef,
    // Escape closes, and focus goes back to the trigger. `returnFocusTo` is explicit rather
    // than relying on the trap's opener capture: a pointer user's click may not have moved
    // focus to the trigger at all, and "back where you were" has to mean the trigger.
    onEscape: close,
    returnFocusTo: triggerRef,
  });

  /*
   * Arrow-key navigation between rows, plus Home/End. See the docblock on why this is not a
   * `role="menu"`, and why the listener is on `document`.
   *
   * The candidate list is recomputed on every keystroke — with `collectFocusable`, the trap's
   * own collector, so the two agree about what is focusable and a row that appears or
   * disappears is in the cycle as soon as it is in the DOM. The scrim is excluded for free:
   * it carries `tabIndex={-1}`, which that collector filters out.
   */
  useEffect(() => {
    if (!active) return undefined;

    const onKeyDown = (event) => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      const panel = panelRef.current;
      if (!panel) return;
      // Only while focus is inside the panel (or has dropped to `<body>`, which is where a
      // browser puts it when the focused element goes away). A key pressed on the page
      // behind is not ours to consume.
      const target = event.target;
      const inside = target === document.body || panel.contains(target);
      if (!inside) return;

      const rows = collectFocusable(panel);
      if (rows.length === 0) return;

      // Consumed, so ArrowDown does not scroll the page behind the popover as well as
      // moving the selection.
      event.preventDefault();

      const index = rows.indexOf(document.activeElement);
      let next;
      if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = rows.length - 1;
      else if (index === -1) next = event.key === 'ArrowUp' ? rows.length - 1 : 0;
      else if (event.key === 'ArrowDown') next = (index + 1) % rows.length;
      else next = (index - 1 + rows.length) % rows.length;

      rows[next].focus({ preventScroll: true });
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [active]);

  /**
   * Sign out. MOVED VERBATIM from `Sidebar.jsx`'s `SidebarAccount` — same order, same three
   * effects, same destination. This is the only way out of the authenticated app; task 8.8
   * changes where it lives and nothing about what it does.
   */
  const signOut = useCallback(async () => {
    try {
      await supabase.auth.signOut();
    } catch {
      // A failed remote sign-out must not strand the session locally.
    }
    sessionStorage.clear();
    localStorage.removeItem(`sb-${import.meta.env.VITE_SUPABASE_URL || ''}-auth-token`);
    navigate('/signin');
  }, [navigate]);

  const docsUrl = readDocsUrl();

  // Resolved from the route table, with the unknown case reported rather than guessed.
  const groups = useMemo(
    () => MENU_GROUPS.map((group) => ({
      ...group,
      items: group.items
        .map((item) => {
          const label = SECONDARY_ROUTE_TITLES[item.path];
          if (typeof label !== 'string' || label.trim() === '') {
            // Reported, not thrown, and dropped rather than rendered under a guessed name.
            // Losing one row is a downgrade; a menu that throws is a trader with no way to
            // sign out. `tests/unit/shell/accountMenu.test.jsx` asserts all seven are
            // present, which is where this would actually be caught.
            console.error(
              `[shell/AccountMenu] no SECONDARY_ROUTE_TITLES entry for "${item.path}", so that `
                + 'row is omitted. Check shell/navigation.js.',
            );
            return null;
          }
          return { ...item, label };
        })
        .filter((item) => item !== null),
    })),
    [],
  );

  const Chevron = active ? ChevronUp : ChevronDown;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        // The disclosure pattern, and no `aria-haspopup` — see the docblock. `aria-controls`
        // is only meaningful while the panel exists, so it is omitted when closed.
        aria-expanded={active}
        aria-controls={active ? panelId : undefined}
        aria-label={rail ? TRIGGER_LABEL : undefined}
        title={rail ? TRIGGER_LABEL : undefined}
        onClick={() => setOpen((current) => !current)}
        data-shell="account-menu-trigger"
        data-open={active ? 'true' : 'false'}
        className={
          (rail
            ? 'flex h-full w-full items-center justify-center '
            : 'flex h-full w-full items-center gap-2 px-3 text-left ')
          + 'transition-colors hover:bg-surface-raised focus-visible:outline-2 '
          + 'focus-visible:outline-offset-2 focus-visible:outline-brand'
          + (className === '' ? '' : ` ${className}`)
        }
      >
        {/* At the rail the trigger's whole content is this avatar, which is decorative, so
            the name comes from `aria-label` above. At full width the name is composed from
            the content — see the `sr-only` span below. */}
        <span
          aria-hidden="true"
          className={
            'relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full border '
            + 'border-line-default bg-surface-inset text-micro font-semibold text-content-secondary'
          }
        >
          {identity.initial}
          {/* The bell's badge without the number: at 56px there is no room for a figure, and
              the count itself is one row inside the panel either way. Hidden from assistive
              technology because this control's name does not claim a count — the
              Notifications row's does. */}
          {badge === null ? null : (
            <span
              data-shell="account-menu-trigger-dot"
              className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full"
              style={{ background: cssVar('color.brand') }}
            />
          )}
        </span>
        {rail ? null : (
          <>
            {/* First in the DOM, so the accessible name reads "Account and settings, <name>"
                rather than trailing the purpose after the identity. It is additional to the
                visible text, not a replacement for it: an `aria-label` here would drop the
                visible name out of the accessible name (WCAG 2.5.3). */}
            <span className="sr-only">{TRIGGER_LABEL}</span>
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-small text-content-primary">
                {/* Not a placeholder name. Whose account this is either came back from the
                    server or did not, and "Quant Trader" — the old default — read as fact. */}
                {identity.name ?? 'Signed in'}
              </span>
              {identity.tier === null ? null : (
                <span className="truncate font-mono text-micro tracking-wider text-content-secondary">
                  {identity.tier}
                </span>
              )}
            </span>
            {/* Up when open, because the panel opens upward: a chevron that pointed away from
                where the surface appears would be a control describing itself wrongly. */}
            <Chevron
              size={12}
              strokeWidth={1.75}
              aria-hidden="true"
              className="shrink-0 text-content-secondary"
            />
          </>
        )}
      </button>

      {active
        ? createPortal(
          <div
            data-shell="account-menu-layer"
            style={{ position: 'fixed', inset: 0, zIndex: cssVar('z.dropdown') }}
          >
            {/*
              The scrim. A real `<button>` rather than a `div onClick`, so a pointer
              dismissal is a control with a name in the accessibility tree — `ds/Drawer`'s
              pattern, copied. `tabIndex={-1}` keeps it out of the tab cycle and out of the
              arrow-key cycle, both of which read `collectFocusable`. It is transparent: this
              is a popover, not a modal surface, and dimming the terminal to show nine rows
              would be the `DesktopOnlyOverlay` instinct in miniature.
            */}
            <button
              type="button"
              tabIndex={-1}
              aria-label="Close account menu"
              onClick={close}
              data-shell="account-menu-scrim"
              style={{
                position: 'absolute',
                inset: 0,
                width: '100%',
                height: '100%',
                padding: 0,
                border: 'none',
                background: 'transparent',
                cursor: 'pointer',
              }}
            />

            <div
              ref={panelRef}
              id={panelId}
              // Holds focus if no row can take it (the trap's zero-focusable case). Negative,
              // so it is not itself a tab stop.
              tabIndex={-1}
              data-shell="account-menu"
              className="flex flex-col overflow-hidden border border-line-default bg-surface-panel"
              style={{
                position: 'fixed',
                left: `${position?.left ?? VIEWPORT_GUTTER_PX}px`,
                bottom: `${position?.bottom ?? VIEWPORT_GUTTER_PX}px`,
                width: `${ACCOUNT_MENU_WIDTH_PX}px`,
                // Unmeasured means unclamped rather than clamped to zero — a panel with no
                // height would hide every row. The layout effect above means this state does
                // not paint.
                maxHeight: position === null ? undefined : `${position.maxHeight}px`,
                borderRadius: cssVar('radius.lg'),
                boxShadow: cssVar('shadow.overlay'),
              }}
            >
              {/* The only scroll region, so a short viewport scrolls the rows rather than
                  taking the panel off screen (Requirement 17.3's discipline, applied to a
                  popover). `min-h-0` is what actually lets a flex child scroll. */}
              <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-1">
                <nav aria-label={PANEL_LABEL} className="flex flex-col">
                  {groups.map((group, index) => {
                    const headingId = `${instanceId}-${group.id}`;
                    return (
                      <div key={group.id} className="flex flex-col">
                        {/* §6.4 rules the blocks apart. Never above the first one, where it
                            would only repeat the panel's own top border. */}
                        {index > 0 ? (
                          <hr aria-hidden="true" className="my-1 border-t border-line-subtle" />
                        ) : null}
                        {group.heading === null ? null : (
                          <p
                            id={headingId}
                            className="px-2 py-1 text-micro font-semibold uppercase tracking-widest text-content-secondary"
                          >
                            {group.heading}
                          </p>
                        )}
                        {/* `aria-labelledby` where there is a heading, so the grouping is
                            announced rather than merely drawn. A `<p>` rather than an `<h2>`:
                            this is a transient popover, and its two labels do not belong in
                            the page's heading outline. */}
                        <ul
                          aria-labelledby={group.heading === null ? undefined : headingId}
                          className="flex flex-col"
                        >
                          {group.items.map((item) => {
                            const isNotifications = item.path === NOTIFICATIONS_PATH;
                            return (
                              <MenuLink
                                key={item.path}
                                path={item.path}
                                label={item.label}
                                icon={item.icon}
                                onNavigate={close}
                                // The bell's sentence, from the hook, so one count is never
                                // worded two ways. Unknown carries no number at all.
                                accessibleName={isNotifications ? unreadLabel : undefined}
                                data-account-menu-unread={
                                  isNotifications
                                    ? (unreadKnown ? String(unread) : 'unknown')
                                    : undefined
                                }
                                trailing={
                                  isNotifications && badge !== null ? (
                                    <span
                                      aria-hidden="true"
                                      className={
                                        'ml-auto shrink-0 rounded-full border border-line-default '
                                        + 'bg-surface-raised px-1.5 font-mono text-micro '
                                        + 'font-semibold tabular-nums text-brand'
                                      }
                                    >
                                      {badge}
                                    </span>
                                  ) : null
                                }
                              />
                            );
                          })}
                          {/* Documentation. External, so a real `<a>` with `target="_blank"`
                              and `rel="noopener noreferrer"` — and ABSENT, not disabled, when
                              the URL is not configured (Requirement 19.4; see the docblock).
                              It sits in this block because §6.4 puts it there, under Support. */}
                          {group.id === 'help' && docsUrl !== null ? (
                            <li>
                              <a
                                href={docsUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                onClick={close}
                                data-account-menu-item="docs"
                                className={ROW_CLASS}
                              >
                                <BookOpen
                                  size={14}
                                  strokeWidth={1.75}
                                  aria-hidden="true"
                                  className="shrink-0"
                                />
                                <span className="min-w-0 flex-1 truncate">Documentation</span>
                                {/* The glyph says "this leaves the app" to a sighted user; the
                                    `sr-only` text says it to everyone else. Without the
                                    second, a new tab opens with no warning. */}
                                <ExternalLink
                                  size={11}
                                  strokeWidth={1.75}
                                  aria-hidden="true"
                                  className="ml-auto shrink-0"
                                />
                                <span className="sr-only">(opens in a new tab)</span>
                              </a>
                            </li>
                          ) : null}
                        </ul>
                      </div>
                    );
                  })}
                </nav>

                <hr aria-hidden="true" className="my-1 border-t border-line-subtle" />

                {/* Outside the `<nav>` on purpose: it is an action, not a destination, and a
                    navigation landmark listing "Sign out" among its links would misdescribe
                    both. A `<button>` for the same reason — there is no URL that means "sign
                    out", so it cannot honestly be a link. */}
                <button
                  type="button"
                  onClick={signOut}
                  data-shell="account-menu-signout"
                  className={ROW_CLASS}
                >
                  <LogOut size={14} strokeWidth={1.75} aria-hidden="true" className="shrink-0" />
                  <span className="min-w-0 flex-1 truncate">Sign out</span>
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )
        : null}
    </>
  );
}
