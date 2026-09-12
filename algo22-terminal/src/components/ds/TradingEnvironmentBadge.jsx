/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/TradingEnvironmentBadge — which environment these figures came from
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.7. design.md §1.13, §5.1, §5.3, §8.1, §8.2.
 * Requirements 7.4, 8.5, 12.2, 12.3. Properties P22, P12.
 *
 * ═══ WHAT THIS DEDUPLICATES ═══
 *
 * `SimulatedIndicator` exists **twice**, byte-for-byte identical, in
 * `pages/Portfolio.jsx` and `pages/TradeHistory.jsx`. design.md §5.3 marks this row
 * "Exists ×2 — deduplicate", and it is the only primitive in §5 whose provenance is a
 * duplicate rather than an absence.
 *
 * The two copies are **behaviourally right**, and that is the reason this component is
 * a consolidation rather than a rewrite. What they already get right, and what is
 * preserved here:
 *
 *   * They render from the **server's own fields** — `execution_environment` and
 *     `is_simulated` — and never from the route, a tab position or a toggle. That is
 *     §8.1's resolution rule, written before §8.1 existed.
 *   * They carry meaning in **text and shape**, not in colour alone: a label plus a
 *     `FlaskConical` glyph, so the indicator survives a trader who cannot distinguish
 *     indigo from red (Requirement 12.3).
 *   * When the response arrives without those fields they say
 *     `SIMULATED · SERVER LABEL UNAVAILABLE` rather than asserting a label nobody
 *     sent. That copy is kept **verbatim** below. It is the honest answer and it was
 *     already the answer here.
 *
 * What the duplication costs, and what this fixes: two copies means two hardcoded
 * palettes (four colour literals each, none from `tokens.css`), one shared amber for
 * "unknown" that collides with the warning token, and — the real risk — two places to
 * edit when the label copy changes, on the one indicator whose whole job is to stop a
 * trader mistaking simulated figures for real ones.
 *
 * The two pages are NOT edited by this task. Tasks 15.1 and 16.x re-point them; this
 * is the shared replacement they will import.
 *
 * ═══ THE FOUR AXES (design.md §8.2, Requirement 12.3) ═══
 *
 * | | LIVE | PAPER | BACKTEST | null |
 * | Hue    | `env.live`  | `env.paper`    | `env.backtest` | `status.neutral` |
 * | Label  | `LIVE`      | `PAPER TRADING`| `BACKTEST`     | see below        |
 * | Icon   | `Radio`     | `FlaskConical` | `History`      | `HelpCircle`     |
 * | Border | solid       | dashed         | dotted         | dashed           |
 *
 * All four come from `design/semantic.js`'s `ENVIRONMENT` — none of them is restated
 * here, so Property 22 (which asserts pairwise distinctness on all four) tests the same
 * table this component renders. Hue is one axis of four on purpose: a badge that
 * differed only in colour would fail Requirement 12.3 for a red/green-blind trader, and
 * `--color-env-live` is deliberately the same red as the loss token, which makes text,
 * icon and border style the things actually carrying the distinction.
 *
 * ═══ TWO NULL CASES, BOTH REAL STATES ═══
 *
 * `environment == null` is not an error and not a missing prop to paper over:
 * `positions[].environment` is genuinely nullable, and a response can arrive without
 * `execution_environment` at all.
 *
 *   `environment == null && isSimulated` → `SIMULATED · SERVER LABEL UNAVAILABLE`
 *   `environment == null && !isSimulated` → `ENVIRONMENT UNCONFIRMED`
 *
 * Neither is a guess, and the distinction between them is information: the first says
 * the server confirmed these figures are simulated but not which simulator; the second
 * says the server said nothing at all. Collapsing them would throw away the one fact
 * that matters most in the first case.
 *
 * **`resolveEnvironment` is deliberately not used here.** Its step 3 maps
 * `isSimulated === true` with no named environment onto `PAPER` — a reasonable default
 * for a caller that needs one value, and the wrong thing for this component, which
 * would then print `PAPER TRADING` on the strength of a boolean. Naming a specific
 * simulator the server did not name is exactly the guess §8.1 rules out. This component
 * reads `environmentTreatment`, which returns `null` when the server did not say, and
 * renders that as the state it is.
 *
 * ═══ ONE CONFLICT IS SURFACED RATHER THAN RESOLVED ═══
 *
 * `environment="LIVE"` together with `isSimulated === true` is the server contradicting
 * itself. There is no safe way to pick a winner: choosing LIVE hides a simulation flag,
 * and choosing PAPER hides a live label. So both fields are rendered — `LIVE ·
 * SIMULATED` — which is precisely what the server said, and is what the duplicated
 * `SimulatedIndicator` already did (`${env} · SIMULATED`). For `PAPER` and `BACKTEST`
 * the flag adds nothing the label does not already carry, so it is not repeated.
 */

import { FlaskConical, HelpCircle, History, Radio } from 'lucide-react';

import { ENVIRONMENT, environmentTreatment, statusToken } from '../../design/semantic';

import { assertContract } from './devAssert';

/** design.md §5.1's three placements. */
export const ENVIRONMENT_BADGE_VARIANTS = Object.freeze(['chip', 'strip', 'inline']);

/**
 * `semantic.js` carries `icon: 'Radio'` as a **string** so it can stay free of React and
 * be imported by guards, property tests and view-model code. This is where the name
 * becomes a component, and the only place in this file that knows lucide exists.
 *
 * `HelpCircle` is the unconfirmed glyph — the same "we do not know" icon
 * `semantic.stageIconFor` returns for an unrecognised block category, so one unknown
 * reads the same way everywhere in the app.
 */
const ENVIRONMENT_ICONS = Object.freeze({ Radio, FlaskConical, History, HelpCircle });

/** `pages/TradeHistory.jsx`'s existing copy, kept verbatim. */
const SIMULATED_UNNAMED_LABEL = 'SIMULATED · SERVER LABEL UNAVAILABLE';
const SIMULATED_UNNAMED_LONG =
  'The server reported these figures as simulated but did not name an execution environment.';

/** design.md §8.2's null column. */
const UNCONFIRMED_LABEL = 'ENVIRONMENT UNCONFIRMED';
const UNCONFIRMED_LONG = 'The server did not report an execution environment';

/** The suffix that surfaces a `LIVE` + `is_simulated` contradiction. */
const CONFLICT_SUFFIX = ' · SIMULATED';
const CONFLICT_LONG =
  'The server labelled this environment live and also flagged the data as simulated. '
  + 'Both are shown because neither can be dismissed.';

/**
 * The resolved treatment for a pair of server fields. Total: every input resolves.
 *
 * Exported because Property 22 (task 6.8) asserts pairwise distinctness across the four
 * axes and Property 12 (task 6.3) asserts a money panel names its environment; both
 * need the resolution without rendering, and a second copy of these rules in a test
 * would be a second copy of the rules.
 *
 * @param {unknown} environment The server's `execution_environment` / `environment`.
 * @param {unknown} isSimulated The server's `is_simulated`. Only `true` counts.
 * @returns {{id: string, label: string, long: string, fg: string, wash: string,
 *   icon: string, border: string, simulated: boolean, named: boolean}}
 */
export function environmentBadge(environment, isSimulated) {
  // `=== true`, as `semantic.resolveEnvironment` does: a truthy non-boolean (`"false"`,
  // `1`, `{}`) is not the server saying simulated.
  const flagged = isSimulated === true;
  const treatment = environmentTreatment(environment);

  if (treatment) {
    const conflicted = flagged && treatment.id === ENVIRONMENT.LIVE.id;
    return Object.freeze({
      id: treatment.id,
      label: conflicted ? `${treatment.label}${CONFLICT_SUFFIX}` : treatment.label,
      long: conflicted ? CONFLICT_LONG : treatment.long,
      fg: treatment.fg,
      wash: treatment.wash,
      icon: treatment.icon,
      border: treatment.border,
      simulated: flagged,
      named: true,
    });
  }

  // The neutral arm still takes its colour from `design/semantic.js` — `statusToken(null)`
  // is the neutral group — so there is no branch in this file that chooses a colour.
  const neutral = statusToken(null);
  return Object.freeze({
    id: 'UNCONFIRMED',
    label: flagged ? SIMULATED_UNNAMED_LABEL : UNCONFIRMED_LABEL,
    long: flagged ? SIMULATED_UNNAMED_LONG : UNCONFIRMED_LONG,
    fg: neutral.fg,
    wash: neutral.wash,
    // `FlaskConical` when the server did confirm simulation: the one fact it gave us is
    // carried in shape as well as in text, which is what Requirement 12.3 asks for.
    // `HelpCircle` when it said nothing, because then there is nothing to depict.
    icon: flagged ? ENVIRONMENT.PAPER.icon : 'HelpCircle',
    // design.md §8.2's null column. Shared with PAPER, which is allowed: only the three
    // named environments have to be pairwise distinct.
    border: 'dashed',
    simulated: flagged,
    named: false,
  });
}

/** Per-variant geometry. Colour, label, icon and border style never come from here. */
const VARIANT_CLASSES = Object.freeze({
  chip: 'inline-flex shrink-0 items-center gap-1.5 rounded-sm border px-2 py-0.5 text-micro',
  strip: 'flex w-full items-center gap-2 border-b px-3 py-2 text-micro',
  // No fill and no box: an inline badge sits beside a figure, and a filled chip there
  // would out-weight the number it qualifies. The border axis survives as an underline,
  // so all four axes are present in every variant.
  inline: 'inline-flex shrink-0 items-center gap-1 border-b text-micro',
});

/**
 * The environment indicator.
 *
 * @param {Object} props
 * @param {'LIVE'|'PAPER'|'BACKTEST'|string|null} [props.environment] The server's field.
 *   `null` — or any value outside the three — renders an unconfirmed badge, never a
 *   guessed environment.
 * @param {boolean} [props.isSimulated] The server's `is_simulated`.
 * @param {'chip'|'strip'|'inline'} [props.variant] `chip` in a panel title row, `strip`
 *   full-width under a `PageHeader`, `inline` beside a figure.
 * @param {boolean} [props.announce] `role="status"`, for the FIRST instance on a page
 *   only. A page badging nine money panels must not announce nine times.
 * @param {string} [props.className]
 */
export function TradingEnvironmentBadge({
  environment = null,
  isSimulated = false,
  variant = 'chip',
  announce = false,
  className = '',
  ...rest
}) {
  const known = ENVIRONMENT_BADGE_VARIANTS.includes(variant);
  assertContract(
    known,
    'TradingEnvironmentBadge: `variant` must be one of '
      + `${ENVIRONMENT_BADGE_VARIANTS.join(' | ')}, received ${JSON.stringify(variant)}.`,
  );
  const resolvedVariant = known ? variant : 'chip';

  const badge = environmentBadge(environment, isSimulated);
  const Icon = ENVIRONMENT_ICONS[badge.icon] ?? HelpCircle;

  return (
    <span
      // The four axes, published for Property 22 and for anything asserting on the
      // rendered output rather than on `environmentBadge`'s return value.
      data-environment={badge.id}
      data-environment-border={badge.border}
      data-environment-icon={badge.icon}
      data-environment-variant={resolvedVariant}
      role={announce === true ? 'status' : undefined}
      title={badge.long}
      className={`${VARIANT_CLASSES[resolvedVariant]} font-mono font-bold uppercase tracking-wide whitespace-nowrap ${className}`.trim()}
      style={{
        color: badge.fg,
        // `inline` sits beside a figure, where a filled chip would out-weight the
        // number it qualifies. `chip` and `strip` both read as bands and take the wash.
        backgroundColor: resolvedVariant === 'inline' ? undefined : badge.wash,
        borderColor: badge.fg,
        borderStyle: badge.border,
      }}
      {...rest}
    >
      <Icon size={11} strokeWidth={2} aria-hidden="true" />
      {/* Panel's precedent: the announcement is "Trading environment: PAPER TRADING"
          rather than a bare label floating beside a heading. Property 12 reads this
          full sentence as the panel's accessible environment text. */}
      <span className="sr-only">Trading environment: </span>
      {/* The label is its own element so that the environment name is queryable on its
          own, without the screen-reader prefix or the long form attached to it. */}
      <span>{badge.label}</span>
      {/* The long form is visible only in the full-width variant, where there is room
          for it. Elsewhere it is the tooltip, and `ds/Tooltip` (§5.2) replaces the
          native one without changing this copy. */}
      {resolvedVariant === 'strip' ? (
        <span className="font-sans font-normal normal-case tracking-normal text-content-secondary">
          {badge.long}
        </span>
      ) : null}
    </span>
  );
}

export default TradingEnvironmentBadge;
