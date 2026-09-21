/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/StatusBadge — the one status chip, and the only shape a state may take
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.6. design.md §5.1, §5.3. Requirements 1.2, 1.4, 1.5.
 *
 * WHAT IT CONSOLIDATES — THREE IMPLEMENTATIONS, THREE PALETTES
 * -----------------------------------------------------------
 * Three components render a status today and no two of them agree on a colour:
 *
 *   `ui-legacy/primitives.jsx` `Tag2` — nine variants keyed by a `c` prop
 *       (`accent` `profit` `loss` `warning` `purple` `gold` `cyan` `green` `red`),
 *       each with its own hardcoded `rgba()` border wash. Its profit border is
 *       `rgba(0,200,83,0.3)` — the retired #00C853 green — around text painted the
 *       current #26A69A, so a profit tag shows two greens at once.
 *   `ui-legacy/primitives.jsx` `StatusDot` — a nine-entry colour map of its own
 *       (`backtesting` and `active` are brand cyan there, warning amber, error red)
 *       plus a permanent `statusPulse` animation and a `box-shadow` halo.
 *   `ui/Badge.jsx` — eighteen `variants` written as arbitrary-value Tailwind
 *       utilities on a third palette again: #10B981 for every success-ish state and
 *       #EF4444 for every failure-ish one, neither of which is a token. Its dot is
 *       `animate-pulse`, unconditionally.
 *
 * So "running" is #10B981 in a table, #26A69A in a tag and #26A69A-with-a-halo in a
 * dot, and a trader learns three greens for one fact. This component is the single
 * replacement: one shape, one size scale, and colour that comes from
 * `design/semantic.js` and nowhere else.
 *
 * `state`, NOT A COLOUR — WHY THERE IS NO COLOUR PROP
 * --------------------------------------------------
 * Requirement 1.4 asks that every page apply status colour "exclusively through that
 * mapping". A component that accepts `c="profit"` cannot honour that, because the call
 * site is then the thing choosing the hue: `Tag2 c="gold"` and `Badge variant="cyan"`
 * are both in the tree today on states that have nothing to do with money. So this
 * component takes the SERVER'S STATE and looks the colour up itself. There is no prop
 * that can change the colour, which is what makes the rule structural instead of
 * remembered.
 *
 * The migration hazard is muscle memory, so it is caught rather than ignored: passing
 * any of {@link COLOUR_PROPS} throws in development with the state prop named as the
 * replacement, and those props are stripped so they can never reach the DOM.
 *
 * WHAT WAS DROPPED (Requirement 1.5)
 * ---------------------------------
 *   * The pulse. `StatusDot` animated forever for `live`/`active`/`running`, and
 *     `Badge`'s dot pulsed for every variant including `stopped`. A dashboard with
 *     twelve strategies had twelve things blinking, none of which was an event.
 *   * The `box-shadow` halo `StatusDot` drew around the dot, and `Tag2`'s
 *     `boxShadow` + `transform: scale(1.02)` hover growth.
 *   * `Tag2`'s hover palette. A tag is a label, not a control; it had `cursor:
 *     pointer` and a hover colour while doing nothing on click.
 *
 * COLOUR IS NEVER THE ONLY CHANNEL
 * -------------------------------
 * The badge always renders TEXT — `label`, or the humanised `state` when no label is
 * given. There is no code path that renders the dot alone, which is what `StatusDot`
 * did at 8×8 pixels with the meaning carried entirely by hue. The dot is decorative
 * reinforcement and is `aria-hidden`.
 */

import { memo } from 'react';

import { statusToken } from '../../design/semantic';

import { assertContract, hasText } from './devAssert';

/** The two sizes. `sm` is the table/inline chip; `md` is the standalone badge. */
export const STATUS_BADGE_SIZES = Object.freeze(['sm', 'md']);

/**
 * Every prop name the three predecessors used to pass a colour. Rejected by name so a
 * migrated call site fails loudly rather than silently ignoring the argument.
 *
 * `variant` and `c` are `ui/Badge.jsx`'s two; `c` is also `Tag2`'s. The rest are the
 * spellings that get reached for when the first two are refused.
 */
export const COLOUR_PROPS = Object.freeze([
  'c',
  'color',
  'colour',
  'variant',
  'tone',
  'hue',
  'bg',
  'background',
  'fg',
]);

const SIZE_CLASS = Object.freeze({
  sm: 'gap-1 px-1.5 py-0.5 text-micro',
  md: 'gap-1.5 px-2 py-0.5 text-small',
});

/**
 * A server state → a human label: `partially_filled` → `Partially filled`.
 *
 * Sentence case, not shouting: the visible presentation is uppercased by CSS, so the
 * DOM text a screen reader reads stays `Partially filled` rather than `PARTIALLY
 * FILLED`, which some screen readers spell out letter by letter.
 *
 * @param {unknown} state
 * @returns {string} The humanised label, or `''` when there is no readable state.
 */
export function humaniseState(state) {
  if (typeof state !== 'string') return '';
  const words = state.trim().replace(/[_-]+/g, ' ').replace(/\s+/g, ' ');
  if (words === '') return '';
  return words.charAt(0).toUpperCase() + words.slice(1).toLowerCase();
}

/**
 * The status chip.
 *
 * @param {Object} props
 * @param {string} props.state REQUIRED — the server's state, verbatim. Resolved
 *   through `statusToken`, which is total: an unrecognised value renders calmly in the
 *   neutral group rather than as `undefined`.
 * @param {string} [props.label] Overrides the humanised `state`. The text is always
 *   rendered; there is no dot-only form.
 * @param {boolean} [props.dot] Adds the `StatusDot` glyph — static, `aria-hidden`.
 * @param {'sm'|'md'} [props.size]
 * @param {string} [props.className]
 */
export const StatusBadge = memo(function StatusBadge({
  state,
  label,
  dot = false,
  size = 'sm',
  className = '',
  ...rest
}) {
  // Requirement 1.4, structurally. A colour prop is not ignored, it is refused.
  const passedColourProps = COLOUR_PROPS.filter((name) =>
    Object.prototype.hasOwnProperty.call(rest, name),
  );
  assertContract(
    passedColourProps.length === 0,
    `StatusBadge does not accept a colour: received ${passedColourProps.join(', ')}. `
      + 'Requirement 1.4 puts every status colour behind `statusToken`, so the hue comes from '
      + `\`state\` and cannot be overridden. Pass the server's state (\`state="${
        hasText(state) ? state : 'running'
      }"\`) and, if the wording needs changing, \`label\`.`,
  );
  // Stripped whatever the environment: in production the assert logs and returns, and
  // an unrecognised attribute on a <span> is a React warning of its own.
  COLOUR_PROPS.forEach((name) => {
    delete rest[name];
  });

  const known = STATUS_BADGE_SIZES.includes(size);
  assertContract(
    known,
    `StatusBadge: \`size\` must be one of ${STATUS_BADGE_SIZES.join(' | ')}, received ${JSON.stringify(size)}.`,
  );

  // A badge with neither a state nor a label has nothing to say and would render an
  // empty coloured box — which is `StatusDot`'s failure mode, colour with no content.
  assertContract(
    hasText(state) || hasText(label),
    'StatusBadge: `state` is required — it is what selects the colour through `statusToken` '
      + '(Requirement 1.4) and what the label defaults to. Pass the value the server reported.',
  );

  const { group, fg, wash } = statusToken(state);
  const text = hasText(label) ? label : humaniseState(state);

  return (
    <span
      data-status-group={group}
      data-status-state={hasText(state) ? state : undefined}
      className={`inline-flex shrink-0 items-center rounded-sm border font-mono font-semibold uppercase tracking-wide ${
        SIZE_CLASS[known ? size : 'sm']
      } ${className}`.trim()}
      // Inline rather than a `bg-status-*` utility on purpose: the class name would have
      // to be composed from the group, which puts a colour decision back at the call
      // site's mercy and hides the token behind string concatenation. `statusToken` hands
      // over the resolved token values, and this is where they land.
      style={{ color: fg, backgroundColor: wash, borderColor: fg }}
      {...rest}
    >
      {/* Static and `aria-hidden`: the text beside it already carries the meaning, so the
          dot adds no information and must not add an announcement either. */}
      {dot === true ? (
        <span
          aria-hidden="true"
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: fg }}
        />
      ) : null}
      {/* Never empty: `hasText(state) || hasText(label)` is asserted above, and in
          production the fallback is the raw state rather than silence. */}
      {hasText(text) ? text : 'Unknown'}
    </span>
  );
});

/**
 * THE NOT-AVAILABLE MARKER — INTERIM HOME, ONE INSTANCE (Requirements 14.5, 19.3)
 * ------------------------------------------------------------------------------
 * An em-dash in `content-muted` with an accessible name of `"{label}: not available"`
 * and the reason in the title. It never renders `0`, `0 ms`, `0%` or an empty string:
 * a figure nobody measured and a figure that measured zero are different facts, and
 * only the second one is a number.
 *
 * `ds/Metric` (task 6.4) owns this marker — the spec puts it there so that Requirements
 * 14.5 and 19.3 are enforced at one leaf instead of at every call site. `Metric.jsx`
 * does not exist yet, and `StrategyStatus`, `RiskIndicator` and `ExchangeStatus` all
 * need the marker now, so it lives here: this is the module all three already import,
 * so hosting it costs no extra file and there is still exactly ONE marker in the app.
 *
 * >>> WHEN `ds/Metric.jsx` LANDS: delete this function and re-point the three
 * >>> `import { NotAvailable } from './StatusBadge'` lines at `./Metric`. Three
 * >>> one-line edits, no behaviour change. It is deliberately NOT in task 6.23's
 * >>> barrel, so nothing outside `ds/` can start depending on this location.
 *
 * @param {Object} props
 * @param {string} props.label What is unavailable, for the accessible name.
 * @param {string} [props.reason] Why — shown on hover, read from the title.
 * @param {string} [props.className]
 */
export function NotAvailable({ label, reason, className = '' }) {
  const description = hasText(reason) ? reason : undefined;
  const name = `${hasText(label) ? label : 'Value'}: not available`;
  return (
    <span
      // `role="img"` + `aria-label` rather than a bare `aria-label`: an accessible name on
      // a plain <span> is ignored by most assistive technology, and this glyph IS the
      // whole content, so it needs a name that is actually announced.
      role="img"
      aria-label={description ? `${name}. ${description}` : name}
      // `content-muted` is annotated NON-TEXT ONLY in tokens.css (3.2:1). It is valid
      // here because the em-dash is a glyph standing beside a labelled element, and the
      // accessible name above carries the meaning independently of the contrast.
      className={`text-content-muted ${className}`.trim()}
      title={description}
      data-not-available="true"
    >
      {/* An em-dash, not a zero, not "N/A", not an empty cell. */}
      —
    </span>
  );
}

export default StatusBadge;
