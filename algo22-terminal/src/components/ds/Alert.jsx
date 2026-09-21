/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/Alert — a condition worth saying out loud, and no louder than that
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.23. design.md §5.2, §6.5, §7.1, §9.3.
 * Requirements 1.2, 1.4, 1.5, 3.3, 14.5, 16.2, 18.4.
 *
 * ═══ WHO USES IT ═══
 *
 *   * **The shell's disconnected strip** (task 8.7, §6.5). On `disconnected` / `error`
 *     the top bar renders a full-width strip below itself reading *"Not connected to the
 *     trading engine. Live positions, orders and P&L below may be out of date."* with a
 *     retry calling `wsClient.connect()`. That strip is what stops a stale figure from
 *     silently reading as current (Requirement 14.5).
 *   * **The Dashboard's condition strip** (task 13.x, Requirement 3.3). When an exchange
 *     connection, a live strategy or an order submission is in an error or disconnected
 *     state, the page surfaces one summary above the secondary-priority elements —
 *     *"1 exchange disconnected · 1 strategy stopped on error"* with a `[Review]` action.
 *
 * Both are full-width bands, which is why `variant="strip"` exists alongside the default
 * bordered block.
 *
 * ═══ THE SEVERITY → `role` MAPPING IS THE POINT ═══
 *
 * `role="alert"` is an ASSERTIVE live region: a screen reader interrupts whatever it is
 * reading to announce it. `role="status"` is polite and waits for a pause. Choosing
 * between them is not a styling detail — it is the difference between "stop, this matters
 * now" and "when you get a moment".
 *
 * Requirement 16.2 forbids raising a notification for informational or routine events.
 * An assertive role on a routine message is exactly that noise, delivered to the users
 * least able to ignore it: a trader mid-sentence in a position table, cut off to be told
 * something is fine. And the reverse is worse — a polite role on a rejected order means
 * the announcement waits behind whatever else is speaking.
 *
 * So the mapping is declared once, in {@link SEVERITY}, and reachable as
 * {@link alertRole}:
 *
 *   | severity   | token             | border | icon          | role     |
 *   | ---------- | ----------------- | ------ | ------------- | -------- |
 *   | `critical` | `status.error`    | solid  | AlertOctagon  | `alert`  |
 *   | `error`    | `status.error`    | solid  | AlertOctagon  | `alert`  |
 *   | `warning`  | `status.warning`  | solid  | AlertTriangle | `status` |
 *   | `guidance` | `status.guidance` | dashed | Info          | `status` |
 *   | `info`     | `status.neutral`  | solid  | Info          | `status` |
 *
 * The middle three rows are §9.3's table verbatim — the same three surfaces the Strategy
 * Builder needs to keep distinguishable for Requirement 5.5, where the dashed border and
 * the `Info` icon on the guidance row are what make an invalid-connection attempt read as
 * "not yet" rather than "broken". Spelling that table here rather than in the Builder is
 * what stops a fourth copy of it appearing next to the fifth gold banner.
 *
 * No `aria-live` attribute is written. `role="alert"` already implies
 * `aria-live="assertive"` and `role="status"` implies `aria-live="polite"`; adding the
 * attribute alongside the role is a second place for them to disagree.
 *
 * ═══ NO COLOUR PROP, AND `info` HAS NO HUE ═══
 *
 * `severity` selects a state string and the hue comes from `statusToken`
 * (`design/semantic.js`). There is no prop that can change it — the same rule
 * `ds/StatusBadge` enforces, using that module's own {@link COLOUR_PROPS} list so there
 * is one list rather than two. Passing any of them throws in development.
 *
 * `info` resolves through the vocabulary's `idle` key to the NEUTRAL group, so an
 * informational strip carries no hue at all. Requirement 1.5 spends colour only on
 * current state, risk or required action, and a page that paints its informational band
 * amber has spent the warning colour on something that is not a warning.
 *
 * An unrecognised `severity` falls to `warning`, not to `info` and not to `error`.
 * Understating it would announce a rejected order politely; overstating it would
 * interrupt for a routine one. `warning` is visible, polite, and carries the icon that
 * says "look at this" without claiming to know how bad it is.
 *
 * ═══ COLOUR IS NOT THE ONLY CHANNEL ═══
 *
 * Three of the five hues in `tokens.css` are shared (see `design/semantic.js` on the
 * known token collision), so hue alone cannot separate five severities. Each row above
 * differs on the icon as well, and the guidance row differs on border style too — the
 * same multi-axis argument `ds/TradingEnvironmentBadge` makes for Requirement 12.3.
 *
 * @module components/ds/Alert
 */

import { isValidElement } from 'react';
import { AlertOctagon, AlertTriangle, Info, X } from 'lucide-react';

import { statusToken } from '../../design/semantic';

import { ActionControl } from './ActionControl';
import { assertContract, hasText } from './devAssert';
import { COLOUR_PROPS } from './StatusBadge';

/**
 * The five severities, in escalating order.
 *
 * `critical` and `error` are both spelled out rather than collapsed. They resolve to one
 * token and one role, but a caller holding a server value of `CRITICAL` (which
 * `backend_app/backend/risk.py` really does emit) should not have to translate it, and
 * `data-alert-severity` keeps the distinction readable in the DOM.
 */
export const ALERT_SEVERITIES = Object.freeze(['critical', 'error', 'warning', 'guidance', 'info']);

/** The two shapes. `block` sits inside a page region; `strip` spans the column. */
export const ALERT_VARIANTS = Object.freeze(['block', 'strip']);

/**
 * severity → { state, role, border, Icon }. §9.3's table, plus the two ends of it.
 *
 * `state` is the key handed to `statusToken`, so every hue in this file is chosen by
 * `design/semantic.js` and none is chosen here. `info` uses `idle` because that is the
 * vocabulary's neutral entry — going through a declared key rather than relying on
 * `statusToken`'s unknown-value fallback means the neutral treatment is a decision on
 * record, not a coincidence.
 */
const SEVERITY = Object.freeze({
  critical: Object.freeze({ state: 'critical', role: 'alert', border: 'solid', Icon: AlertOctagon }),
  error: Object.freeze({ state: 'error', role: 'alert', border: 'solid', Icon: AlertOctagon }),
  warning: Object.freeze({ state: 'warning', role: 'status', border: 'solid', Icon: AlertTriangle }),
  guidance: Object.freeze({ state: 'guidance', role: 'status', border: 'dashed', Icon: Info }),
  info: Object.freeze({ state: 'idle', role: 'status', border: 'solid', Icon: Info }),
});

/** The severity an unrecognised value renders as. See the module docblock. */
const FALLBACK_SEVERITY = 'warning';

/**
 * `ds/StatusBadge`'s colour-prop list, MINUS `variant`.
 *
 * `variant` is this component's geometry prop — `'block' | 'strip'`, the same spelling
 * `ds/TradingEnvironmentBadge` uses for the same job — so it cannot also be a refused
 * name here. It is subtracted rather than the list being retyped, so the other eight
 * spellings stay in one place and a name added there is refused here too.
 *
 * Nothing is lost by the subtraction: `variant` is destructured, so a caller passing
 * `variant="danger"` gets the `ALERT_VARIANTS` assert instead, which names the two legal
 * values and points at `severity` for anything to do with colour.
 */
const REFUSED_COLOUR_PROPS = Object.freeze(COLOUR_PROPS.filter((name) => name !== 'variant'));

/** Per-variant geometry. Colour, icon and border style never come from here. */
const VARIANT_CLASSES = Object.freeze({
  block: 'w-full rounded-md border px-3 py-2',
  // Flush and full-width, bottom border only — the same geometry
  // `ds/TradingEnvironmentBadge`'s strip uses, so a shell that stacks an environment
  // strip and an alert strip gets two bands of one shape.
  strip: 'w-full border-b px-3 py-2',
});

/**
 * The live-region role a severity earns. `'alert'` (assertive) or `'status'` (polite).
 *
 * Exported so the shell and the Dashboard can assert the mapping without re-deriving it,
 * and so Requirement 16.2's boundary is testable as a function rather than by inspecting
 * rendered markup. Total: anything unrecognised answers for {@link FALLBACK_SEVERITY}.
 *
 * @param {unknown} severity
 * @returns {'alert'|'status'}
 */
export function alertRole(severity) {
  const key = typeof severity === 'string' ? severity.trim().toLowerCase() : '';
  return (SEVERITY[key] ?? SEVERITY[FALLBACK_SEVERITY]).role;
}

/**
 * Render the alert's action: a `{ label, to | href | onClick }` spec, or a ready-made
 * node.
 *
 * Both shapes are accepted because both callers already hold one of them. §6.5's retry is
 * a function (`wsClient.connect()`), so it arrives as `{ label: 'Retry', onClick }` and
 * goes through `ds/ActionControl` — the same rendering `ds/EmptyState` and
 * `ds/ErrorState` use for the same spec shape. The Dashboard's `[Review]` may instead be
 * a `ds/CommandButton` the page has already composed, and re-expressing that as a spec
 * would lose its intent and its `disabledReason`.
 */
function AlertAction({ action }) {
  if (action === null || action === undefined || action === false) return null;
  if (isValidElement(action)) return action;
  if (typeof action !== 'object') return null;
  return <ActionControl action={action} intent="secondary" />;
}

/**
 * A condition strip.
 *
 * @param {Object} props
 * @param {'critical'|'error'|'warning'|'guidance'|'info'} [props.severity] Selects the
 *   token, the icon, the border style and the live-region role. Default `'info'`.
 * @param {string} props.title REQUIRED. The condition, in one line. It is what a screen
 *   reader announces and what a trader reads first, so it states the condition rather
 *   than naming it — "Not connected to the trading engine", not "Connection".
 * @param {React.ReactNode} [props.children] The consequence, if the title does not
 *   already carry it — "Live positions, orders and P&L below may be out of date."
 * @param {React.ReactElement|{label: string, to?: string, href?: string, onClick?: Function}} [props.action]
 *   What the trader can do about it. A spec object, or a composed node.
 * @param {Function} [props.onDismiss] Renders a close button. Omit for a condition the
 *   trader cannot dismiss because it is still true — a disconnected socket is not
 *   dismissible; a completed backtest notice is.
 * @param {string} [props.dismissLabel] Accessible name for that button (Requirement
 *   18.4). Default `'Dismiss'`.
 * @param {'block'|'strip'} [props.variant] `block` inside a page region, `strip`
 *   full-width under the top bar or above a tier.
 * @param {string} [props.className]
 */
export function Alert({
  severity = 'info',
  title,
  children,
  action,
  onDismiss,
  dismissLabel = 'Dismiss',
  variant = 'block',
  className = '',
  ...rest
}) {
  // Requirement 1.4, structurally — the rule `ds/StatusBadge` states at length, using its
  // list so there is one list. A colour prop is refused, not ignored.
  const passedColourProps = REFUSED_COLOUR_PROPS.filter((name) =>
    Object.prototype.hasOwnProperty.call(rest, name),
  );
  assertContract(
    passedColourProps.length === 0,
    `Alert does not accept a colour: received ${passedColourProps.join(', ')}. The hue, the `
      + 'icon, the border style and the live-region role all come from `severity` through '
      + `\`statusToken\` (Requirement 1.4). Pass one of ${ALERT_SEVERITIES.join(' | ')}.`,
  );
  // Stripped whatever the environment: in production the assert logs and returns, and an
  // unrecognised attribute on a <div> is a React warning of its own.
  REFUSED_COLOUR_PROPS.forEach((name) => {
    delete rest[name];
  });

  assertContract(
    hasText(title),
    'Alert: `title` is required. It is the announcement — an alert with no title is a '
      + 'coloured band that a screen reader reports as an empty live region, and Requirement '
      + '3.3 asks the surface to SUMMARISE the condition. State it: "1 exchange disconnected", '
      + 'not "Warning".',
  );

  const key = typeof severity === 'string' ? severity.trim().toLowerCase() : '';
  const known = Object.prototype.hasOwnProperty.call(SEVERITY, key);
  assertContract(
    known,
    `Alert${hasText(title) ? ` "${title}"` : ''}: \`severity\` must be one of `
      + `${ALERT_SEVERITIES.join(' | ')}, received ${JSON.stringify(severity)}. Rendering as `
      + `\`${FALLBACK_SEVERITY}\` — understating it would announce a real failure politely, and `
      + 'overstating it would interrupt a routine message (Requirement 16.2).',
  );
  const resolvedSeverity = known ? key : FALLBACK_SEVERITY;
  const { state, role, border, Icon } = SEVERITY[resolvedSeverity];

  const knownVariant = ALERT_VARIANTS.includes(variant);
  assertContract(
    knownVariant,
    `Alert: \`variant\` must be one of ${ALERT_VARIANTS.join(' | ')}, received `
      + `${JSON.stringify(variant)}.`,
  );
  const resolvedVariant = knownVariant ? variant : 'block';

  const { group, fg, wash } = statusToken(state);

  return (
    <div
      data-ds="alert"
      data-alert-severity={resolvedSeverity}
      data-alert-variant={resolvedVariant}
      data-status-group={group}
      className={`flex items-start gap-2 ${VARIANT_CLASSES[resolvedVariant]} ${className}`.trim()}
      // Inline for the reason `ds/StatusBadge` gives: composing `bg-status-*` from the
      // group would put the hue behind string concatenation. `statusToken` hands over the
      // resolved values and this is where they land.
      style={{ backgroundColor: wash, borderColor: fg, borderStyle: border }}
      {...rest}
      // Written AFTER the spread, so `rest` cannot reach it. A `role` passed at the call
      // site is the one prop that could turn a routine message assertive, which is the
      // noise Requirement 16.2 forbids — the severity decides this and nothing else does.
      role={role}
    >
      {/* The icon is the non-colour axis. `aria-hidden` because the role and the title
          already carry the meaning, and an announced icon would prefix every alert with
          its own name. */}
      <Icon
        size={14}
        strokeWidth={2}
        aria-hidden="true"
        className="mt-0.5 shrink-0"
        style={{ color: fg }}
      />

      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {/* The hue rides on the title as well as the icon. For `info` that resolves to
            the neutral token, so an informational strip reads calm — which is
            Requirement 1.5, not an oversight. */}
        {hasText(title) ? (
          <p className="text-title font-semibold" style={{ color: fg }}>
            {title}
          </p>
        ) : null}
        {children === null || children === undefined || children === false ? null : (
          <div className="text-body text-content-secondary">{children}</div>
        )}
      </div>

      {action ? (
        <div className="shrink-0">
          <AlertAction action={action} />
        </div>
      ) : null}

      {typeof onDismiss === 'function' ? (
        <button
          type="button"
          onClick={onDismiss}
          aria-label={hasText(dismissLabel) ? dismissLabel : 'Dismiss'}
          data-ds="alert-dismiss"
          className="shrink-0 rounded-sm border border-line-default p-1.5 text-content-secondary transition-colors hover:bg-surface-raised hover:text-content-primary"
        >
          <X size={12} strokeWidth={2} aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
}

export default Alert;
