/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/ActionControl — the "next action" an EmptyState or ErrorState offers
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.1. Requirements 14.1, 14.3, 11.5.
 *
 * `EmptyState` and `ErrorState` both have to render an action spec of the shape
 * design.md §5.1 declares — `{ label, to }` or `{ label, onClick }` — and
 * `design/errorCopy.js` returns one of exactly that shape in `translateError().action`.
 * This is that rendering, once.
 *
 * It is NOT one of design.md §5's sixteen primitives and task 6.23's barrel must not
 * export it.
 *
 * RECONCILED WITH `ds/CommandButton` — task 6.23
 * ---------------------------------------------
 * An earlier draft of this docblock said this file's body would become a
 * `CommandButton` call once that component landed. It has landed, and it does not: the
 * two components have disjoint contracts and the merge would have cost more than the
 * duplication it removed. Which to use is decided by ONE question — does the call site
 * hold an action SPEC, or is it issuing a command?
 *
 *   `ActionControl` renders a `{ label, to | href | onClick }` OBJECT that came from
 *       somewhere else: `design/errorCopy.js`'s `translateError().action`, or an
 *       `EmptyState`'s `action` / `clearFiltersAction`. It takes no children. Most of
 *       those specs are navigations, so most of what it renders is an anchor, and it
 *       must render one WITHOUT a router (see below). It returns `null` for a spec it
 *       cannot render, because a state panel's action is data and data can be absent.
 *   `ds/CommandButton` renders children as a command: five intents, `loading`,
 *       `disabledReason`, `confirm`, `icon`. It never takes a spec object, never
 *       renders an anchor, and never returns `null` — a command with no label is a
 *       development failure there, not a missing field.
 *
 * So the overlap is "a bordered clickable thing", and the treatments differ on purpose:
 * this one is the compact control that sits inside an empty or error panel, sized to
 * `--text-body`, while a `CommandButton` is a page-level control from `ui/Button`. Two
 * sizes of button is not two components doing one job; rendering the same spec object
 * in two places would have been.
 *
 * The rule for new code: a control that can be disabled, can be busy, needs a
 * confirmation, or carries an icon is a `CommandButton`. This component is reached only
 * through `EmptyState` and `ErrorState`, which are its only two importers.
 *
 * WHY THE ROUTER IS OPTIONAL
 * -------------------------
 * `to` is an in-app route, so it must be a `Link` — a plain anchor would reload the
 * whole application and lose the WebSocket connection. But a primitive that hard-fails
 * outside a router is a primitive that cannot be rendered in isolation, and the panel
 * state contract's property test (task 6.2) renders panels in every state without
 * caring that one of them contains a link. `useInRouterContext` answers the question
 * honestly and cheaply: inside a router it is a `Link`, outside it is an `<a href>`
 * that still navigates. No branch renders nothing.
 */

import { Link, useInRouterContext } from 'react-router-dom';

import { hasText } from './devAssert';

/** Shared shape for both intents; only the colour treatment differs. */
const BASE =
  'inline-flex items-center justify-center rounded-sm border px-3 py-1.5 '
  + 'text-body font-medium transition-colors';

const INTENT = Object.freeze({
  /** The one action the surface wants the trader to take. */
  primary: 'border-brand text-brand hover:bg-brand-wash',
  /** A second, lesser option — `clearFiltersAction`, or an error's own suggestion. */
  secondary: 'border-line-strong text-content-secondary hover:bg-surface-raised hover:text-content-primary',
});

/**
 * Render one action spec.
 *
 * Returns `null` for anything unusable rather than an empty control: a button with no
 * label is a trap for a keyboard user, and a link to nowhere is worse than no link.
 * Whether the action is REQUIRED is not decided here — `EmptyState` asserts that
 * (Requirement 14.1), which is why an absent action can be silent at this level.
 *
 * @param {Object} props
 * @param {{label: string, to?: string, href?: string, onClick?: Function}} [props.action]
 * @param {'primary'|'secondary'} [props.intent]
 * @param {string} [props.className]
 */
export function ActionControl({ action, intent = 'primary', className = '' }) {
  const inRouter = useInRouterContext();

  if (!action || typeof action !== 'object' || !hasText(action.label)) return null;

  const classes = `${BASE} ${INTENT[intent] ?? INTENT.primary} ${className}`.trim();
  const { label, to, href, onClick } = action;

  if (hasText(to)) {
    return inRouter ? (
      <Link to={to} className={classes}>{label}</Link>
    ) : (
      <a href={to} className={classes}>{label}</a>
    );
  }

  if (hasText(href)) {
    // An external destination. `noreferrer` implies `noopener`, but both are named
    // because the pairing is what stops the opened tab reaching back into this one.
    return (
      <a href={href} className={classes} target="_blank" rel="noopener noreferrer">
        {label}
      </a>
    );
  }

  if (typeof onClick === 'function') {
    return (
      <button type="button" onClick={onClick} className={classes}>
        {label}
      </button>
    );
  }

  return null;
}

export default ActionControl;
