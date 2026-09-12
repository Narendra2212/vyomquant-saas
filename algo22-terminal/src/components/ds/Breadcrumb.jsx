/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/Breadcrumb — where the trader is, as a real navigation landmark
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.23. design.md §5.2, §6.1, §6.2.
 * Requirements 1.2, 2.3, 18.4.
 *
 * ═══ THE SHAPE IS `PageHeader`'s, DELIBERATELY ═══
 *
 * `ds/PageHeader` already accepts a `breadcrumb` array of `{ label, to }` and renders it
 * itself, and §6.1 puts a second trail in the top bar ("Breadcrumb / page context —
 * left", task 8.7). This is the exported primitive for that second site, and it takes the
 * SAME array shape — so a page that has already composed a trail for its `PageHeader` can
 * hand the identical value here, and nothing has to be translated between the two.
 *
 * `PageHeader` keeps its own internal copy for now: it is not exported from that module,
 * so there is no ambiguity about which `Breadcrumb` a caller gets, and re-pointing it at
 * this one is a one-line edit that belongs to whichever task next touches that file
 * rather than to this one. What matters is that the two agree on the contract below, and
 * they do — including the `aria-current` placement and the dropped-label behaviour.
 *
 * ═══ THE FOUR THINGS THAT MAKE IT A BREADCRUMB AND NOT A ROW OF LINKS ═══
 *
 *   1. **A `<nav>` with an accessible name.** A landmark is only useful if a screen
 *      reader can tell it apart from the sidebar's `<nav>`, so `label` is required in
 *      substance and defaults to `'Breadcrumb'` (Requirement 18.4). Two unnamed
 *      navigation landmarks on one page are two entries reading "navigation".
 *   2. **An ordered list.** The trail is a sequence and `<ol>` says so, which is what
 *      lets assistive technology announce "2 of 3". A row of `<a>`s in a `<div>` carries
 *      the same pixels and none of that.
 *   3. **`aria-current="page"` on the last item.** The trail's purpose is to say where
 *      the trader is, and this is the attribute that says it (Requirement 2.3's sibling
 *      for the sidebar).
 *   4. **The last item is TEXT, not a link.** A link to the page you are already on is a
 *      control that does nothing, and Requirement 19.4 forbids non-functional links
 *      outright. It stays text even when the entry carries a `to`, because a page passing
 *      the same descriptor to a nav and to a breadcrumb should not have to strip the
 *      route out for one of them.
 *
 * ═══ A MISSING LABEL IS DROPPED, NOT REPORTED ═══
 *
 * This is the one silent failure in the file and it matches `PageHeader`'s, for its
 * reason: the last crumb is usually an entity's name, which arrives from a request. A
 * trail that is one crumb short while `RSI Reversion` is still loading is honest.
 * Substituting a placeholder would put fabricated text in the one element that claims to
 * say where the trader is, and logging on every detail page's first render would make the
 * console useless.
 *
 * The surviving crumb then becomes the current page and stops being a link, which is the
 * behaviour that falls out of the rule rather than a second rule.
 *
 * ═══ THE ROUTER IS OPTIONAL ═══
 *
 * `to` is an in-app route, so inside a router it is a `Link` — a plain anchor would
 * reload the application and drop the WebSocket connection. Outside one it is an
 * `<a href>` that still navigates. `ds/ActionControl` and `ds/PageHeader` both do this,
 * for the reason `ActionControl` states: a primitive that hard-fails without a router
 * cannot be rendered in isolation, and these components are rendered bare by their own
 * unit tests and by the shell's property test.
 *
 * @module components/ds/Breadcrumb
 */

import { Link, useInRouterContext } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';

import { hasText } from './devAssert';

/**
 * The trail, cleaned: entries that are objects carrying a visible label, in order.
 *
 * Exported because the shell resolves its trail from `shell/navigation.js` (task 8.7) and
 * needs to know whether anything survived before deciding to reserve space for a
 * breadcrumb row. Returning the cleaned array rather than a boolean means the caller
 * counts the same crumbs this component will render.
 *
 * @param {unknown} items
 * @returns {Array<{label: string, to?: string}>}
 */
export function readTrail(items) {
  const list = Array.isArray(items) ? items : [];
  return list.filter(
    (entry) => entry && typeof entry === 'object' && hasText(entry.label),
  );
}

/**
 * The breadcrumb trail.
 *
 * @param {Object} props
 * @param {Array<{label: string, to?: string}>} props.items The trail, outermost first.
 *   The LAST entry is the current page. `ds/PageHeader`'s `breadcrumb` shape exactly.
 * @param {string} [props.label] The landmark's accessible name. Default `'Breadcrumb'`.
 *   Override it when a page carries two trails and they need telling apart.
 * @param {string} [props.className] Appended to the `<nav>`.
 */
export function Breadcrumb({ items, label = 'Breadcrumb', className = '', ...rest }) {
  const inRouter = useInRouterContext();
  const crumbs = readTrail(items);

  // No trail is not an error and not an empty landmark: a page with nothing above it in
  // the hierarchy renders no navigation region at all, so a screen reader's landmark list
  // does not gain an entry containing nothing.
  if (crumbs.length === 0) return null;

  return (
    <nav
      aria-label={hasText(label) ? label : 'Breadcrumb'}
      data-ds="breadcrumb"
      className={`min-w-0 ${className}`.trim()}
      {...rest}
    >
      <ol className="flex min-w-0 items-center gap-1 text-micro text-content-secondary">
        {crumbs.map((entry, index) => {
          const isCurrent = index === crumbs.length - 1;
          // The last crumb is never a link, whatever it carries. See the docblock.
          const linked = !isCurrent && hasText(entry.to);
          return (
            <li
              key={`${index}-${entry.label}`}
              className="flex min-w-0 items-center gap-1"
              data-breadcrumb-current={isCurrent ? 'true' : undefined}
            >
              {/* The separator is presentational — the `<ol>` already carries the
                  sequence, so announcing a chevron between every pair would read the
                  structure twice. */}
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

export default Breadcrumb;
