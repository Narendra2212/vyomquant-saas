/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/components/Sidebar.jsx — the shell's primary navigation
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 8.6. design.md §6.1, §6.3, §11.6, §1.10.
 * Requirements 2.1, 2.3, 2.4, 17.1.
 *
 * Renders `NAV_GROUPS` from `shell/navigation.js`. It declares no navigation table of
 * its own, which is the whole point of task 8.1: the sidebar, the active state, the
 * route `Suspense` title and the `nav-contract` guard all read one table, so none of
 * them can disagree about what the product's navigation is.
 *
 * ---------------------------------------------------------------------------
 * WHAT THIS REPLACED, AND WHY EACH PIECE HAD TO GO
 * ---------------------------------------------------------------------------
 * 1. A LOCAL 13-ENTRY `NAV` ARRAY. Three routes that existed and worked
 *    (`/app/live-trading`, `/app/portfolio`, `/app/trades`) were unreachable from it,
 *    and five entries §6.4 moves to the account menu were in it. The table now says.
 *
 * 2. `location.pathname === '/app/' + n.id`. An exact comparison against a path derived
 *    from the entry id, which highlighted nothing on `/app/backtester` (a declared alias
 *    of `/app/backtest`) and nothing on `/app/strategies/:strategyId` — so opening a
 *    strategy left the sidebar claiming the trader was nowhere. Replaced by
 *    `activeNavId(pathname)`, which owns the alias and child-route patterns
 *    (Requirement 2.3, §1.10).
 *
 * 3. `handleNavClick`'s `docs` branch — a `window.open(import.meta.env.VITE_DOCS_URL)`
 *    inside the nav click handler, inert when the env var was unset. `docs` is not a
 *    route and is no longer a nav entry (§6.3); §6.4 gives it a home in the account
 *    menu, omitted when the URL is not live. Navigation and external links stop being
 *    the same code path (Requirement 19.4).
 *
 * 4. A `<button onClick={() => navigate(...)}>` per entry. Now a real `<a>`, so
 *    middle-click, Ctrl-click, "copy link address" and the browser's own focus and
 *    hover semantics work (Requirement 18.1, §6.3).
 *
 * 5. THE NOTIFICATION UNREAD READ. `api.notifications.getUnreadCount()` plus a
 *    `wsClient.subscribe('notification')` incrementing a badge on the `notifications`
 *    nav entry — an entry that no longer exists here. The count belongs to the top-bar
 *    bell and the account menu, which task 8.8 gives one shared
 *    `useUnreadNotifications` hook rather than the two independent fetches `Sidebar` and
 *    `TopBar` were each making (§6.4). Nothing in this file polls any more.
 *
 * 6. FIVE COLOUR LITERALS and 21 `C.` references. The brand tile's
 *    `linear-gradient(135deg,#00d4ff,#0055ff)` also went for a second reason:
 *    Requirement 1.5 retires gradient washes. Both guard budgets drop to 0.
 *
 * ---------------------------------------------------------------------------
 * WHY `Link` AND NOT `NavLink`
 * ---------------------------------------------------------------------------
 * §6.3 asks for "a real `<a>` produced by react-router's `NavLink`, not a `<button>`
 * calling `navigate()`". The `<a>` is the requirement and it is what this renders —
 * `Link` is literally the component `NavLink` itself renders, so every semantic §6.3
 * names is present. What is not used is `NavLink`'s own active computation, and that is
 * deliberate rather than incidental:
 *
 *     let isActive = locationPathname === toPathname
 *       || (!end && locationPathname.startsWith(toPathname)
 *              && locationPathname.charAt(toPathname.length) === '/');
 *     let ariaCurrent = isActive ? ariaCurrentProp : undefined;
 *
 * That match cannot see an alias. On `/app/backtester` with `to="/app/backtest"` the
 * `charAt` is `'e'`, not `'/'`, so `isActive` is `false` — the exact bug §1.10 records,
 * reproduced one layer down. And `NavLink` applies `aria-current` from its own answer
 * *after* spreading the caller's props, so a correct `aria-current="page"` passed in
 * cannot survive: it is overwritten with `undefined`. Using `NavLink` here would
 * therefore mean either a second, disagreeing active computation in the shell, or an
 * active entry that a screen reader is never told is the current page. `Link` plus
 * `activeNavId` is one answer, from the table, applied to both the styling and the
 * attribute. The active decision is still real routing state — it is derived from
 * `useLocation()`.
 *
 * ---------------------------------------------------------------------------
 * THE ACTIVE ENTRY IS NOT MARKED BY COLOUR ALONE (Requirement 3, WCAG 1.4.1)
 * ---------------------------------------------------------------------------
 * Four independent signals, three of them not colour:
 *
 *   * a 2px left rule that is ABSENT on inactive entries — a graphical element
 *     appearing at a fixed position, readable in greyscale. The 2px is reserved on
 *     every entry as a transparent border, so its arrival moves nothing;
 *   * a heavier label weight;
 *   * a heavier icon stroke;
 *   * `aria-current="page"`, which is the same fact for assistive technology.
 *
 * The `--color-brand-wash` background and `--color-brand` label are the fourth signal
 * and, on their own, would be the whole of Requirement 3's complaint.
 *
 * ---------------------------------------------------------------------------
 * RAIL MODE KEEPS THE LABEL IN THE ACCESSIBLE NAME (Requirement 2.4)
 * ---------------------------------------------------------------------------
 * Between 768px and 1023px the sidebar is 56px wide and icon-only. `sidebarMode` is
 * READ from `useViewportAccess()`; no breakpoint is re-derived here, because a second
 * copy of 1024 in this file could disagree with the gate about where the rail begins.
 *
 * The label does not disappear, it stops being *visible*: it stays in the anchor as
 * `sr-only` text, so the accessible name is still "Strategy Builder" and is computed
 * from content rather than from an `aria-label` that could drift from the table. It is
 * also the `title`, which is the pointer tooltip. An icon with no accessible name is a
 * nav item a screen-reader user cannot identify, and Requirement 2.4 asks for an icon
 * AND a label on every entry — so at rail width both are still there, one of them
 * visually.
 *
 * `ds/Tooltip` is deliberately not used for that tooltip. Its bubble is centred on its
 * trigger (`left: 50%` with a `translateX(-50%)`) and offers `top` / `bottom` placement
 * only; on a 56px rail flush against the viewport's left edge, a "Strategy Builder"
 * bubble would hang off-screen. `Tooltip` exists because a hover-only hint hides
 * information from keyboard users — here the label is already in the accessible name
 * and announced on focus, so `title` adds a pointer convenience rather than being the
 * only way to reach the text. Group headings have no room at 56px either: they become
 * `sr-only` with a visible separator in their place, so the four groups keep their
 * order and stay announced (§11.6).
 *
 * ---------------------------------------------------------------------------
 * THE FOOTER IS A SLOT, AND TASK 8.8 HAS FILLED IT
 * ---------------------------------------------------------------------------
 * §6.1 pins an `AccountMenu` trigger to the bottom at 56px, and §6.4 anchors that
 * popover to "the sidebar's bottom user card". This file reserves the block and renders
 * whatever it is handed through {@link Sidebar}'s `accountMenu` prop. The block is the
 * same height whether or not anything is in it, so filling it moves nothing above it
 * (Requirement 2.2).
 *
 * Between 8.6 and 8.8 the slot fell back to an inline account card — name, plan tier and
 * sign-out — because sign-out is the only route out of the authenticated app and it
 * exists nowhere else in `src/`, so an empty slot would have left the tree with no way to
 * log out. `shell/AccountMenu.jsx` now carries that card, the profile and tier reads and
 * sign-out itself (moved verbatim), and `App.jsx` passes it in, so the fallback is gone:
 * the only render of this component in `src/` supplies the slot, and keeping a second
 * copy of a sign-out implementation nothing reaches is precisely the dead control
 * Requirement 19.4 is about. An unfilled slot is now an empty reserved 56px, which is
 * what a focused test that does not care about the footer wants.
 *
 * Consequently this file reads NOTHING. No Supabase call, no billing entitlements, no
 * notification count, no timer — it renders a frozen table and one `useLocation()`.
 */

import { useId } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Zap } from 'lucide-react';

import { cssVar } from '../design/tokens';

import { NAV_GROUPS, activeNavId } from './shell/navigation';
import { SIDEBAR_WIDTH_PX, useViewportAccess } from './shell/ResponsiveGate';

/**
 * The brand block's height (§6.1). Written here once; `SIDEBAR_WIDTH_PX` in
 * `shell/ResponsiveGate` owns the widths for the same reason — one number, one home.
 */
export const SIDEBAR_BRAND_HEIGHT_PX = 56;

/** The account slot's height (§6.1). Reserved whether or not the slot is filled. */
export const SIDEBAR_FOOTER_HEIGHT_PX = 56;

/**
 * The root's chrome. `overflow-hidden` is what stops a long label or the account card
 * from painting outside the track; `min-h-0` is what lets the nav between the two fixed
 * blocks scroll rather than pushing the footer off the bottom.
 */
const ROOT_CLASS =
  'flex h-full min-h-0 shrink-0 flex-col overflow-hidden border-r border-line-default '
  + 'bg-surface-panel';

/**
 * Geometry that a caller's `className` must not be able to reopen.
 *
 * The width is the tier's, from the gate. It is inline rather than a utility class
 * because an inline style beats a class, and Requirement 2.2 depends on the sidebar
 * being exactly its track's width at every moment — `min` and `max` as well as `width`
 * so a long label cannot stretch it and a narrow viewport cannot squeeze it.
 */
function fixedWidth(px) {
  return { width: `${px}px`, minWidth: `${px}px`, maxWidth: `${px}px` };
}

/**
 * One nav entry.
 *
 * @param {Object} props
 * @param {(typeof NAV_GROUPS)[number]['entries'][number]} props.entry From the table.
 * @param {boolean} props.active `activeNavId(pathname) === entry.id`. Decided once by
 *   the caller, from the table, so ten entries share one answer.
 * @param {boolean} props.rail Icon-only, 56px.
 */
function NavEntry({ entry, active, rail }) {
  const Icon = entry.icon;

  return (
    <li>
      <Link
        to={entry.path}
        // From the table, not from the router's own match — see the docblock. `undefined`
        // rather than `'false'`: `aria-current="false"` is a value some screen readers
        // announce, and only one entry may claim to be the current page.
        aria-current={active ? 'page' : undefined}
        // The pointer tooltip at rail width. Omitted when the label is on screen, where
        // it would only repeat what is already legible.
        title={rail ? entry.label : undefined}
        data-nav-id={entry.id}
        data-active={active ? 'true' : 'false'}
        className={
          rail
            ? 'flex h-10 w-full items-center justify-center border-l-2 transition-colors '
              + 'text-content-secondary hover:bg-surface-raised hover:text-content-primary'
            : 'flex h-8 w-full items-center gap-2 rounded-r-md border-l-2 pl-2 pr-3 '
              + 'text-small transition-colors text-content-secondary '
              + 'hover:bg-surface-raised hover:text-content-primary'
        }
        style={{
          // Reserved on every entry, coloured on one: the rule appears rather than the
          // row moving. `borderLeftWidth` is the class above, so this is colour only.
          borderLeftColor: active ? cssVar('color.brand') : 'transparent',
          // Left `undefined` when inactive so the `hover:` classes above are not beaten
          // by an inline style — an inline `background: transparent` would silently
          // disable hover feedback on nine entries out of ten.
          background: active ? cssVar('color.brand-wash') : undefined,
          color: active ? cssVar('color.brand') : undefined,
          // Not colour. See the docblock's WCAG 1.4.1 note.
          fontWeight: active ? 600 : 500,
        }}
      >
        <Icon
          size={rail ? 17 : 14}
          strokeWidth={active ? 2.25 : 1.75}
          aria-hidden="true"
          className="shrink-0"
        />
        {/* Present at both widths, visible at one. At 56px this is what keeps the
            accessible name equal to the table's label (Requirement 2.4); `sr-only`
            clips rather than removing, so it still counts as the anchor's content.
            216px was chosen so "Strategy Builder" fits at --text-small without
            ellipsis, but `truncate` stays as the backstop: a label growing past the
            track must lose characters, not widen the shell. */}
        <span className={rail ? 'sr-only' : 'truncate'}>{entry.label}</span>
      </Link>
    </li>
  );
}

/**
 * The primary navigation sidebar.
 *
 * Takes no required props, so `<Sidebar />` is a complete mount — which is what a focused
 * test renders and what `App.jsx`'s shell grid renders with the slot filled.
 *
 * @param {Object} props
 * @param {React.ReactNode} [props.accountMenu] Fills the reserved 56px footer slot.
 *   `App.jsx` passes `shell/AccountMenu`'s `<AccountMenu />`, which is the app's only
 *   route to the seven deferred pages and to sign-out (§6.4). Omitting it leaves the
 *   block reserved and empty; the block's height does not depend on it either way, so
 *   supplying it shifts nothing (Requirement 2.2).
 * @param {string} [props.className] Appended. Cannot change the tier's width.
 */
export default function Sidebar({ accountMenu = null, className = '', style, ...rest }) {
  const location = useLocation();
  // The gate's decision, not a breakpoint of our own. Outside a `ResponsiveGate` this
  // is `'expanded'`, which is what a focused unit test and today's un-rewired `App.jsx`
  // both want — the gate is what collapses the rail; its absence is not.
  const { sidebarMode } = useViewportAccess();
  const rail = sidebarMode === 'rail';
  const width = SIDEBAR_WIDTH_PX[rail ? 'rail' : 'expanded'];

  // One lookup for all ten entries, and the only place this component decides what is
  // active. Requirement 2.3's "at most one" is the table's guarantee (Property 3), not
  // something re-established per row.
  const activeId = activeNavId(location.pathname);

  const headingBase = useId();

  return (
    <aside
      data-shell="sidebar"
      data-sidebar-mode={sidebarMode}
      className={ROOT_CLASS + (className === '' ? '' : ` ${className}`)}
      {...rest}
      // Spread last within the object: a caller's style survives, but the tier's width
      // is not negotiable — Requirement 2.2 depends on the track being exactly this wide.
      style={{ ...style, ...fixedWidth(width) }}
    >
      {/* Brand block, 56px. No gradient and no colour literal: the tile was
          `linear-gradient(135deg,#00d4ff,#0055ff)` with a `#000` glyph, and
          Requirement 1.5 retires gradient washes along with glows. */}
      <div
        className="flex shrink-0 items-center gap-2 border-b border-line-default px-3"
        style={{ height: `${SIDEBAR_BRAND_HEIGHT_PX}px` }}
      >
        <span
          aria-hidden="true"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
          style={{ background: cssVar('color.brand') }}
        >
          <Zap size={15} strokeWidth={2.5} style={{ color: cssVar('color.content.inverse') }} />
        </span>
        {rail ? null : (
          <span className="flex min-w-0 flex-col">
            <span className="truncate text-title font-bold tracking-tight text-content-primary">
              VYOM
              <span style={{ color: cssVar('color.brand') }}>QUANT</span>
            </span>
            {/* `content-secondary` (6.2:1), not `content-muted` (3.2:1): tokens.css
                marks muted NON-TEXT ONLY and this is text. */}
            <span className="truncate font-mono text-micro tracking-widest text-content-secondary">
              QUANT INFRASTRUCTURE
            </span>
          </span>
        )}
      </div>

      {/* `aria-label="Main"` rather than "Main navigation": the element is already a
          navigation landmark, and the role is appended by the screen reader. */}
      <nav aria-label="Main" className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden py-2">
        {NAV_GROUPS.map((group, index) => {
          const headingId = `${headingBase}nav-${group.id}`;
          return (
            <div key={group.id} className="mb-2 last:mb-0">
              {/* At 56px the heading has no room, so the grouping is carried by a rule
                  instead. Not on the first group — a rule above the first item would
                  separate it from the brand block's own border. */}
              {rail && index > 0 ? (
                <hr aria-hidden="true" className="mx-3 mb-2 border-t border-line-subtle" />
              ) : null}
              {/* Visually hidden at rail width, never removed: the groups stay
                  announced and stay in §6.3's order at every viewport. */}
              <h2
                id={headingId}
                className={
                  rail
                    ? 'sr-only'
                    : 'px-3 pb-1 text-micro font-semibold uppercase tracking-widest text-content-secondary'
                }
              >
                {group.label}
              </h2>
              <ul aria-labelledby={headingId}>
                {group.entries.map((entry) => (
                  <NavEntry
                    key={entry.id}
                    entry={entry}
                    active={activeId === entry.id}
                    rail={rail}
                  />
                ))}
              </ul>
            </div>
          );
        })}
      </nav>

      {/* The account slot. 56px whether or not it is filled — see the docblock. */}
      <div
        data-shell="sidebar-account"
        className="shrink-0 border-t border-line-default"
        style={{ height: `${SIDEBAR_FOOTER_HEIGHT_PX}px` }}
      >
        {accountMenu}
      </div>
    </aside>
  );
}
