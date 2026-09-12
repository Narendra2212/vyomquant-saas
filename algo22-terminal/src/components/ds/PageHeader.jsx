/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/PageHeader — the page's title, and the block whose height never moves
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.23. design.md §5.1, §6.2.
 * Requirements 1.2, 2.2. Property P2.
 *
 * Replaces `SectionH` from `ui-legacy/primitives.jsx` (used in `Portfolio`,
 * `TradeHistory` and `Strategies`) and every page's hand-rolled title row.
 *
 * ═══ THE FIXED 64px IS THE POINT ═══
 *
 * This block is exactly {@link PAGE_HEADER_HEIGHT_PX}px tall for every combination of
 * every prop. Not "about 64px", not "64px when it has a subtitle": the root carries
 * `height` and `box-sizing: border-box` as an inline style, so no content it is given
 * and no class a caller passes can change it.
 *
 * Requirement 2.2 says navigating between two in-scope pages must produce no layout
 * shift of shell elements, and Property P2 asserts shell geometry is invariant across
 * route changes. A header that grew by 18px on the pages that happen to have a subtitle
 * would move the entire content region down on every navigation into and out of them —
 * and §6.2's route-level `Suspense` fallback makes it worse, because the fallback
 * renders a `PageHeader` with a title and nothing else, so a page with a subtitle would
 * jump the moment its lazy chunk resolved. Reserving the space unconditionally is what
 * removes the jump.
 *
 * ═══ WHY THE HEIGHT IS STRUCTURAL AND NOT ARITHMETIC ═══
 *
 * A fixed height on the root is only half an invariant: if the content inside it can
 * exceed 64px it merely clips instead of growing, and clipped text is a defect. So the
 * layout is arranged so that no optional prop can add a row:
 *
 *   Row 1 — the breadcrumb, when present.       `--text-micro` → 16px
 *   Row 2 — the `<h1>`, the environment badge and the subtitle, on ONE line.
 *                                                `--text-page` → 32px
 *   Right — `meta` and `actions`, on ONE line, vertically centred.
 *
 * Worst case on the left is 16 + 32 = 48px inside a 64px box. `subtitle` sits beside
 * the title rather than beneath it, and `meta` sits beside `actions` rather than
 * beneath them, precisely so that neither can contribute height — the two props
 * design.md §5.1 names in its "whether or not" clause are the two that were most
 * tempting to stack. Both truncate instead, which loses characters rather than
 * position, and only when the viewport is genuinely too narrow for them.
 *
 * `overflow: hidden` is the backstop for the one thing this cannot control: a caller's
 * own `actions` node. A 60px-tall action cluster clips rather than moving the page.
 *
 * ═══ ONE `<h1>` PER PAGE ═══
 *
 * `title` is required and renders the page's only `<h1>` — it is the accessible name of
 * the whole route, so it is not optional and it is not a `<div>`. It is also not the
 * place for asynchronous data: pass the ROUTE's title (`'Strategy'`), not the entity's
 * name, and put the entity in `breadcrumb`'s last entry, which is allowed to arrive
 * late. §6.2's `Suspense` fallback resolves the same title out of `navigation.js`, so
 * the heading is identical before and after the chunk loads.
 *
 * ═══ `undefined` AND `null` MEAN DIFFERENT THINGS FOR `environment` ═══
 *
 * `ds/Panel`'s distinction, for the same reason. Omitting `environment` means this page
 * is not about a single trading environment and no badge is rendered. Passing `null`
 * means the server did not report one, and renders `ENVIRONMENT UNCONFIRMED` — never a
 * guess. See `ds/TradingEnvironmentBadge`.
 */

import { Link, useInRouterContext } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';

import { assertContract, hasText } from './devAssert';
import { TradingEnvironmentBadge } from './TradingEnvironmentBadge';

/**
 * The reserved height, in pixels (design.md §5.1).
 *
 * Exported as the one place this number is written down: the shell (task 8.5) renders a
 * `PageHeader` in its route `Suspense` fallback and Property P2 asserts the geometry
 * across route changes, and a second copy of `64` in either of them would be a second
 * copy of the contract.
 */
export const PAGE_HEADER_HEIGHT_PX = 64;

/**
 * Geometry that must not be overridable.
 *
 * Inline rather than a `h-16` utility for two reasons: an inline style beats a utility
 * class, so `className="h-auto"` cannot quietly undo Requirement 2.2; and it is
 * readable without a stylesheet, so the unit tests and Property P2 can assert the
 * height in jsdom rather than needing a browser.
 */
const FIXED_BLOCK = Object.freeze({
  height: `${PAGE_HEADER_HEIGHT_PX}px`,
  // Without this, any vertical padding would be added to the 64px.
  boxSizing: 'border-box',
  // The backstop for a caller's own `actions` node. See the docblock.
  overflow: 'hidden',
});

/**
 * The breadcrumb trail. `to` is an in-app route, so it is a `Link` inside a router and a
 * plain anchor outside one — `ds/ActionControl`'s precedent, for its reason: a primitive
 * that hard-fails without a router cannot be rendered in isolation, and both this
 * component's unit tests and the shell's property test render it bare.
 *
 * The LAST entry is the current page. It is never a link, whether or not it carries a
 * `to`, and it carries `aria-current="page"`.
 *
 * An entry with no label is dropped rather than rendered or reported. That is the one
 * silent failure in this file and it is deliberate: the last crumb is usually an
 * entity's name, which arrives from a request, and a crumb that is missing while the
 * name is loading is honest — inventing a placeholder or logging an error on every
 * detail page's first render would both be worse.
 */
function Breadcrumb({ trail }) {
  const inRouter = useInRouterContext();
  const crumbs = trail.filter((entry) => entry && typeof entry === 'object' && hasText(entry.label));
  if (crumbs.length === 0) return null;

  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="flex min-w-0 items-center gap-1 text-micro text-content-secondary">
        {crumbs.map((entry, index) => {
          const isCurrent = index === crumbs.length - 1;
          const linked = !isCurrent && hasText(entry.to);
          return (
            <li key={`${index}-${entry.label}`} className="flex min-w-0 items-center gap-1">
              {index > 0 ? (
                <ChevronRight
                  size={10}
                  strokeWidth={2}
                  aria-hidden="true"
                  className="shrink-0 text-content-muted"
                />
              ) : null}
              {linked ? (
                inRouter ? (
                  <Link to={entry.to} className="truncate hover:text-content-primary">
                    {entry.label}
                  </Link>
                ) : (
                  <a href={entry.to} className="truncate hover:text-content-primary">
                    {entry.label}
                  </a>
                )
              ) : (
                <span
                  className={isCurrent ? 'truncate text-content-primary' : 'truncate'}
                  aria-current={isCurrent ? 'page' : undefined}
                >
                  {entry.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/**
 * The page header.
 *
 * @param {Object} props
 * @param {string} props.title Required. The page's `<h1>`, at `--text-page`. The route's
 *   title, not an entity's name — see the docblock.
 * @param {string} [props.subtitle] One line of qualification, beside the title.
 * @param {'LIVE'|'PAPER'|'BACKTEST'|null} [props.environment] Renders
 *   `ds/TradingEnvironmentBadge` inline. Omit when the page is not about one
 *   environment; pass `null` when the server did not report one.
 * @param {Array<{label: string, to?: string}>} [props.breadcrumb] The trail. The last
 *   entry is the current page and is never a link.
 * @param {React.ReactNode} [props.actions] The right-aligned action cluster —
 *   `ds/CommandButton`s.
 * @param {React.ReactNode} [props.meta] A timestamp or count, beside the actions.
 * @param {string} [props.className] Appended. Cannot change the reserved height.
 */
export function PageHeader({
  title,
  subtitle,
  // Destructured without a default so `undefined` stays distinguishable from `null`.
  environment,
  breadcrumb,
  actions,
  meta,
  className = '',
  style,
  ...rest
}) {
  assertContract(
    hasText(title),
    'PageHeader: `title` is required — it renders the page\'s only `<h1>` and is the '
      + 'accessible name of the route (Requirement 18.4). Pass the ROUTE title, resolved from '
      + '`shell/navigation.js`, rather than a value that arrives from a request; an entity\'s '
      + 'name belongs in `breadcrumb`\'s last entry, which may arrive late.',
  );

  const trail = Array.isArray(breadcrumb) ? breadcrumb : [];
  const showsEnvironment = environment !== undefined;

  return (
    <header
      data-ds="page-header"
      className={`flex w-full min-w-0 items-center justify-between gap-4 ${className}`.trim()}
      {...rest}
      // Spread LAST within the style object, so a caller's own style survives but
      // cannot reopen Requirement 2.2 by setting a height of its own.
      style={{ ...style, ...FIXED_BLOCK }}
    >
      <div className="flex min-w-0 flex-col justify-center">
        {trail.length > 0 ? <Breadcrumb trail={trail} /> : null}
        <div className="flex min-w-0 items-center gap-3">
          {/* Rendered only when there is a title: an empty `<h1>` is a worse accessible
              name than none, and inventing one would be worse still. The block keeps
              its height either way, which is what the route transition depends on. */}
          {hasText(title) ? (
            <h1 className="truncate text-page font-semibold text-content-primary">{title}</h1>
          ) : null}
          {showsEnvironment ? (
            <TradingEnvironmentBadge environment={environment} variant="chip" />
          ) : null}
          {/* Beside the title, never beneath it — see the docblock. */}
          {hasText(subtitle) ? (
            <p className="truncate text-body text-content-secondary">{subtitle}</p>
          ) : null}
        </div>
      </div>

      {/* One row: `meta` cannot add height because it shares the actions' line, and its
          line box is shorter than a control's. */}
      <div className="flex shrink-0 items-center gap-3">
        {meta ? <div className="text-micro text-content-secondary">{meta}</div> : null}
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  );
}

export default PageHeader;
