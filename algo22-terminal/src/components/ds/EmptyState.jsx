/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/EmptyState — what is missing, why it matters, what to do next
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.1. design.md §5.1, §11.1. Requirements 14.1, 11.5.
 *
 * Requirement 14.1 asks an empty region for three things, and the reason this
 * component throws rather than styles is that the third one is the one everybody
 * omits. `<td colSpan={8}>No trades found</td>` — the form this replaces in
 * `TradeHistory`, `Strategies`, `Portfolio` and `PaperTrading` — answers "what is
 * missing" and stops. A trader reading it cannot tell whether they have no trades
 * because nothing has traded, because a filter excludes everything, or because the
 * read failed and this is what failure looks like. So `headline`, `body` and
 * `action` are all required, and a missing one is a development-time failure with
 * the offending copy named.
 *
 * REQUIREMENT 11.5'S TWO CASES
 * ---------------------------
 * "No positions yet" and "No positions match these filters" are different facts
 * with different next actions, and a component that cannot tell them apart will
 * always tell a filtering trader to go and create something they already have.
 * `variant` is that distinction, and `no-match` REQUIRES `clearFiltersAction`
 * because the next action for a filtered-out list is to widen the filter, not to
 * create data. `ds/FilterBar` supplies the counts that let a page choose the
 * variant (`totalCount === 0` vs `rows.length === 0 && totalCount > 0`).
 *
 * WHAT WAS DROPPED FROM THE LEGACY `EmptyState` (`ui-legacy/primitives.jsx`)
 * -----------------------------------------------------------------------
 *   * The `float` animation. It rendered its own `@keyframes float` in a `<style>`
 *     child and bobbed the icon up and down forever. Requirement 1.5 asks for calm
 *     by default; an empty panel is the least urgent thing on a trading screen and
 *     was the only thing moving on it.
 *   * `opacity: 0.5` on the icon. Halving the contrast of the one glyph explaining
 *     an empty region is not restraint, it is a legibility cost.
 *   * Emoji. Call sites passed them as the icon. A screen reader reads an emoji's
 *     CLDR name aloud ("bar chart"), and the glyph itself renders differently on
 *     every platform. `icon` is a lucide component, rendered `aria-hidden` because
 *     the headline beside it already says what it means.
 *
 * IT IS NOT ANNOUNCED, ON PURPOSE
 * ------------------------------
 * No `role="status"`, no `aria-live`. An empty panel is a resting state, not an
 * event: it is what the region looks like, and it is already reachable in reading
 * order under the panel's own heading. `ds/FilterBar` owns the one live region that
 * *should* speak when a filter changes the result count, and `ds/ErrorState` owns
 * the one that speaks when something fails. Adding a third here would mean a page
 * with nine empty panels announcing nine times on load.
 */

import { ActionControl } from './ActionControl';
import { assertContract, hasText, isComponentType } from './devAssert';

/** Requirement 11.5's two cases, spelled once. */
export const EMPTY_VARIANTS = Object.freeze(['no-data', 'no-match']);

/**
 * The three fields Requirement 14.1 names, in the order it names them. Exported so
 * the panel state contract's property test (task 6.2) asserts against this list
 * rather than restating it.
 */
export const REQUIRED_EMPTY_FIELDS = Object.freeze(['headline', 'body', 'action']);

/**
 * An empty region that explains itself.
 *
 * @param {Object} props
 * @param {Function} [props.icon] A lucide component. Static, rendered `aria-hidden`.
 * @param {string} props.headline REQUIRED — what is missing (Requirement 14.1).
 * @param {string} props.body REQUIRED — why it matters (Requirement 14.1).
 * @param {{label: string, to?: string, href?: string, onClick?: Function}} props.action
 *   REQUIRED — the next action (Requirement 14.1).
 * @param {'no-data'|'no-match'} [props.variant] Requirement 11.5's two cases.
 * @param {{label: string, to?: string, href?: string, onClick?: Function}} [props.clearFiltersAction]
 *   REQUIRED when `variant === 'no-match'`.
 * @param {string} [props.className]
 */
export function EmptyState({
  icon: Icon,
  headline,
  body,
  action,
  variant = 'no-data',
  clearFiltersAction,
  className = '',
  ...rest
}) {
  const known = EMPTY_VARIANTS.includes(variant);
  assertContract(
    known,
    `EmptyState: \`variant\` must be one of ${EMPTY_VARIANTS.join(' | ')}, received ${JSON.stringify(variant)}.`,
  );
  const resolvedVariant = known ? variant : 'no-data';

  // The three Requirement 14.1 fields, asserted individually so the message names
  // the one that is missing rather than saying "something is wrong with this panel".
  assertContract(
    hasText(headline),
    'EmptyState: `headline` is required — it is what says WHAT IS MISSING (Requirement 14.1). '
      + 'A region with no headline is the `<td colSpan>No trades found</td>` this component replaces.',
  );
  assertContract(
    hasText(body),
    `EmptyState (${headline || 'untitled'}): \`body\` is required — it is what says WHY IT MATTERS `
      + '(Requirement 14.1). "No strategies yet" does not tell a trader that nothing trades until one is deployed.',
  );
  assertContract(
    Boolean(action) && typeof action === 'object' && hasText(action.label),
    `EmptyState (${headline || 'untitled'}): \`action\` is required and needs a \`label\` — it is `
      + 'THE NEXT ACTION (Requirement 14.1). Pass `{ label, to }` for a route or `{ label, onClick }` for a command.',
  );
  // Requirement 11.5: the filtered case's next action is to widen the filter. Telling a
  // trader to create data they already have is the failure this check exists to stop.
  assertContract(
    resolvedVariant !== 'no-match'
      || (Boolean(clearFiltersAction) && typeof clearFiltersAction === 'object' && hasText(clearFiltersAction.label)),
    `EmptyState (${headline || 'untitled'}): \`variant="no-match"\` requires \`clearFiltersAction\` `
      + '(Requirement 11.5). The rows exist; a filter is hiding them, so the way out is to clear it.',
  );

  return (
    <div
      data-empty-variant={resolvedVariant}
      className={`flex flex-col items-center gap-3 px-4 py-10 text-center ${className}`.trim()}
      {...rest}
    >
      {/* `isComponentType`, not `typeof Icon === 'function'`: lucide's exports are
          `forwardRef` objects at the pinned version, so the obvious test drops every
          icon it is given. See `devAssert.isComponentType`. */}
      {isComponentType(Icon) ? (
        // Static. No animation, no reduced opacity. `aria-hidden` because the
        // headline underneath already carries the meaning.
        <Icon size={28} strokeWidth={1.5} aria-hidden="true" className="text-content-muted" />
      ) : null}

      <p className="text-title font-semibold text-content-primary">{headline}</p>
      <p className="max-w-md text-body text-content-secondary">{body}</p>

      <div className="mt-1 flex flex-wrap items-center justify-center gap-2">
        {/* On `no-match` the filter is the thing in the way, so clearing it leads. */}
        {resolvedVariant === 'no-match' ? (
          <>
            <ActionControl action={clearFiltersAction} intent="primary" />
            <ActionControl action={action} intent="secondary" />
          </>
        ) : (
          <ActionControl action={action} intent="primary" />
        )}
      </div>
    </div>
  );
}

export default EmptyState;
