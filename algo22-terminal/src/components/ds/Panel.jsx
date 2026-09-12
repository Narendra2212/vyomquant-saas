/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/Panel — the only thing that decides what a panel shows
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.1. design.md §5.2, §11.1.
 * Requirements 3.5, 3.6, 6.6, 10.4, 11.5, 12.2, 14.1–14.5, 19.3.
 * Properties P26 (panel state contract) and P12 (money-panel environment).
 *
 * ONE STATE MACHINE, ONE COMPONENT
 * --------------------------------
 * The state vocabulary and the transitions are `hooks/usePanelState.js`'s; the
 * rendering is this file's. Nothing else in the application may decide what a panel
 * shows. A page writes `state={positions.state}` and hands over four configuration
 * objects, and every decision after that — whether children render, which skeleton,
 * which copy, whether a retry is offered — is made here, once, for all ten pages.
 *
 * THE VOCABULARY IS IMPORTED, NEVER RESTATED
 * -----------------------------------------
 * `PANEL_STATES`, `ALL_PANEL_STATES` and `STATES_WITHOUT_CHILDREN` all come from the
 * hook. In particular the list of states that must not render children is the hook's
 * `STATES_WITHOUT_CHILDREN` rather than a local array, so the two cannot drift: if a
 * ninth state is ever added, it is added in one place and this component's behaviour
 * follows without an edit here.
 *
 * REQUIREMENT 14.5 — CHILDREN ARE NOT RENDERED IN SIX OF THE EIGHT STATES
 * ---------------------------------------------------------------------
 * `idle`, `loading`, `empty`, `error`, `unavailable` and `unauthorised` all render
 * their own body and NOTHING of the children. There is no code path in this file that
 * renders a `data` array beneath an error banner, because the children are not
 * evaluated into the tree at all in those states — the check is on `state`, before the
 * body is chosen, not a conditional inside the child region.
 *
 * `refreshing` is the sole exception and the reason the exception is safe is a
 * property of the hook, not of this component: `refreshing` is reachable only from
 * `ready`, i.e. only after a read that actually succeeded. A failure moves the panel
 * to `error` and drops `data`, so there is no state in which stale figures and a
 * failure notice can be on screen together. This component cannot verify the history
 * that led to `refreshing`; what it can do — and does — is report a `refreshing`
 * panel with no children, since that combination means the panel got here from
 * somewhere other than a successful read. See {@link warnContract} for why that is
 * reported rather than thrown.
 *
 * REQUIREMENTS 7.4 AND 12.2 — HOW THE ENVIRONMENT ASSERTION IS STRUCTURAL
 * ---------------------------------------------------------------------
 * A panel declares that it shows money with `money`. `money` and an ABSENT
 * `environment` is a development-time failure, which is what turns "remember to badge
 * the panels that show money" into a rule the code enforces.
 *
 * The mechanism rests on `undefined` and `null` meaning different things, and the
 * distinction is the whole design:
 *
 *   `environment` omitted (`undefined`) — the DEVELOPER has not decided. There is no
 *       safe rendering for this: naming an environment would be a guess, and omitting
 *       the indicator would leave a trader unable to tell real orders from simulated
 *       ones. So it throws in development, and in production renders the unconfirmed
 *       indicator, which is the only honest fallback.
 *   `environment={null}` — the SERVER did not report one. That is a real, expected
 *       condition (`positions[].environment` is genuinely nullable) and it renders
 *       `ENVIRONMENT UNCONFIRMED` in the neutral treatment. It is a decision, so it
 *       passes.
 *   `environment="LIVE" | "PAPER" | "BACKTEST"` — named, and rendered as such.
 *
 * A money panel therefore cannot be written without making a decision about its
 * environment. It can be written with the decision "the server did not say", and that
 * is exactly the case Property 12's second half checks: the panel states
 * "unconfirmed" rather than naming one.
 *
 * An unrecognised environment STRING resolves to unconfirmed as well.
 * `environmentTreatment` returns `null` for anything outside the three, and a value
 * we cannot name is a value we must not name — see `design/semantic.js`, where
 * defaulting to `LIVE` is called alarmist and defaulting to `PAPER` dangerous.
 *
 * THE INDICATOR IS INTERIM
 * -----------------------
 * `ds/TradingEnvironmentBadge` (task 6.7) is the real badge and absorbs the two
 * duplicate `SimulatedIndicator` definitions. `PanelEnvironment` below is the same
 * contract at a smaller size, so that the `money` → environment rule and its property
 * test can land with this task instead of waiting on 6.7. It already differs on
 * design.md §4.2's four independent axes — hue, label text, icon and border style —
 * so a trader who cannot distinguish the hues still cannot mistake live for paper
 * (Requirement 12.3). When 6.7 lands, this function's body becomes a
 * `TradingEnvironmentBadge` call and nothing else about this file changes.
 */

import { useId } from 'react';
import { Ban, FlaskConical, HelpCircle, History, Radio } from 'lucide-react';

import {
  ALL_PANEL_STATES,
  PANEL_STATES,
  STATES_WITHOUT_CHILDREN,
} from '../../hooks/usePanelState';
import { environmentTreatment, statusToken } from '../../design/semantic';

import { assertContract, hasText, warnContract } from './devAssert';
import { EmptyState } from './EmptyState';
import { ErrorState } from './ErrorState';
import { LOADING_KINDS, LoadingState } from './LoadingState';

/**
 * WHAT COUNTS AS MONEY CONTENT — the vocabulary behind the `money` prop.
 *
 * Requirement 12.2 covers a panel showing positions, orders or P&L; Requirement 7.4
 * covers the live-trading surfaces. Rather than leave "does this panel show money?" to
 * a judgement at each call site, the answer is written down: a panel whose content
 * includes ANY of these is a money panel and passes `money`.
 *
 * Exported because Property 12's generator (task 6.3) builds panel sets from payloads
 * and needs the same list to decide which of them it expects an indicator on. A list
 * in the test and a different list in the reviewers' heads is the drift this prevents.
 */
export const MONEY_CONTENT = Object.freeze([
  'position',
  'order',
  'fill',
  'trade',
  'balance',
  'equity',
  'pnl',
  'exposure',
  'transaction',
]);

/** The unconfirmed indicator's copy. Never a guessed environment (design.md §8.2). */
const UNCONFIRMED_LABEL = 'ENVIRONMENT UNCONFIRMED';
const UNCONFIRMED_DESCRIPTION = 'The server did not report an execution environment for this panel.';

/**
 * The lucide components for `semantic.js`'s icon NAMES.
 *
 * `design/semantic.js` stays free of React so guards and non-component code can import
 * it, so it carries `icon: 'Radio'` as a string. This is the resolution, and
 * `HelpCircle` is the unconfirmed glyph — deliberately the same "we do not know" icon
 * `stageIconFor` uses, so one unknown reads the same way everywhere in the app.
 */
const ENVIRONMENT_ICONS = Object.freeze({ Radio, FlaskConical, History });

/** Production fallback reason for `unavailable` with no reason. Never a code or a status. */
const FALLBACK_UNAVAILABLE_REASON =
  'This panel cannot show data, and the reason was not reported.';

/**
 * The environment indicator. Text, icon, hue and border all differ per environment.
 *
 * The accessible name is the visible label with a screen-reader-only prefix, so the
 * announcement is "Trading environment: LIVE" rather than a bare "LIVE" floating
 * beside a heading. That full sentence is what Property 12 reads.
 */
function PanelEnvironment({ environment }) {
  const treatment = environmentTreatment(environment);
  // The unconfirmed arm still takes its colour from `semantic.js` — `statusToken(null)`
  // is the neutral group — so there is no branch here that chooses a colour itself.
  const { fg, wash } = treatment ?? statusToken(null);
  const Icon = treatment ? ENVIRONMENT_ICONS[treatment.icon] ?? HelpCircle : HelpCircle;

  return (
    <span
      data-panel-environment={treatment ? treatment.id : 'UNCONFIRMED'}
      title={treatment ? treatment.long : UNCONFIRMED_DESCRIPTION}
      className="inline-flex shrink-0 items-center gap-1 rounded-sm border px-1.5 py-0.5 text-micro font-mono tracking-wide"
      style={{
        color: fg,
        backgroundColor: wash,
        borderColor: fg,
        // The fourth axis. `solid` for live, `dashed` for paper, `dotted` for
        // backtest and for unconfirmed (design.md §4.2, Requirement 12.3).
        borderStyle: treatment ? treatment.border : 'dotted',
      }}
    >
      <Icon size={10} strokeWidth={2} aria-hidden="true" />
      <span className="sr-only">Trading environment: </span>
      {treatment ? treatment.label : UNCONFIRMED_LABEL}
    </span>
  );
}

/**
 * `unavailable` — a first-class state with a required human reason (Requirement 19.3).
 *
 * Not an error and not an empty: nothing failed and nothing is missing, the capability
 * simply does not exist for this account, exchange or environment. `usePanelState`
 * reaches it without issuing a request at all, which is the point — the alternative is
 * a request that 404s, or an empty panel that reads as "you have none of these" when
 * the truth is "we cannot tell you".
 *
 * Not announced, for the same reason `EmptyState` is not: it is a resting condition,
 * not an event.
 */
function UnavailableState({ reason }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
      <Ban size={24} strokeWidth={1.5} aria-hidden="true" className="text-content-muted" />
      <p className="text-title font-semibold text-content-primary">Not available</p>
      <p className="max-w-md text-body text-content-secondary">{reason}</p>
    </div>
  );
}

/**
 * The card shell and the state dispatcher.
 *
 * @param {Object} props
 * @param {string} [props.title] Renders the panel's heading and names its region.
 * @param {'LIVE'|'PAPER'|'BACKTEST'|null} [props.environment] REQUIRED when `money`.
 *   Pass `null` when the server reported none — see the module docblock.
 * @param {boolean} [props.money] Declares that this panel's content includes any of
 *   {@link MONEY_CONTENT}. Requirements 7.4, 12.2.
 * @param {string} [props.state] One of `ALL_PANEL_STATES`. Defaults to `ready`.
 * @param {{kind: string, rows?: number, columns?: number, label?: string}} [props.loading]
 *   `kind` is required in state `loading` (Requirement 14.2).
 * @param {Object} [props.empty] `EmptyState` props. `headline`, `body` and `action` all
 *   required in state `empty` (Requirement 14.1).
 * @param {{error?: *, context?: string, onRetry?: Function, compact?: boolean}} [props.error]
 *   `ErrorState` props. Never a message string (Requirements 14.3, 14.4).
 * @param {{reason: string}} [props.unavailable] Required human reason (Requirement 19.3).
 * @param {{error?: *, context?: string}} [props.unauthorised] Optional; the state has
 *   authored copy of its own without it.
 * @param {React.ReactNode} [props.actions] Right-aligned header cluster.
 * @param {2|3} [props.level] Heading level for `title`.
 * @param {string} [props.className]
 * @param {React.ReactNode} [props.children] Rendered ONLY in `ready` and `refreshing`.
 */
export function Panel({
  title,
  // Destructured without a default so `undefined` stays distinguishable from `null`,
  // and so neither reaches `rest` and lands on the DOM node as an attribute.
  environment,
  money = false,
  state = PANEL_STATES.READY,
  loading,
  empty,
  error,
  unavailable,
  unauthorised,
  actions,
  level = 2,
  className = '',
  children,
  ...rest
}) {
  const headingId = useId();

  // ── Requirements 7.4 / 12.2 ───────────────────────────────────────────────
  // `undefined` is "nobody decided"; `null` is "the server did not say". Only the
  // first is a defect. See the module docblock for why the two are not collapsed.
  assertContract(
    money !== true || environment !== undefined,
    `Panel${title ? ` "${title}"` : ''} declares money content (\`money\`) but omits \`environment\`. `
      + 'Requirement 12.2: a panel showing positions, orders or P&L must carry an environment '
      + "indicator. Pass 'LIVE' | 'PAPER' | 'BACKTEST', or `null` if the server reported none — "
      + '`null` renders "unconfirmed", which is honest. Omitting the prop is not.',
  );

  const known = ALL_PANEL_STATES.includes(state);
  assertContract(
    known,
    `Panel${title ? ` "${title}"` : ''}: \`state\` must be one of ${ALL_PANEL_STATES.join(' | ')}, `
      + `received ${JSON.stringify(state)}. Drive it from \`usePanelState\` rather than a literal.`,
  );
  // An unrecognised state falls to `error`, NOT to `ready`. Rendering children under a
  // state nobody understands is how a stale or partial payload reaches a trader, which
  // is the one outcome Requirement 14.5 rules out. `ErrorState` with no error object
  // renders the context default copy — "something went wrong on our side" — which is
  // both true and free of any internal detail.
  const resolved = known ? state : PANEL_STATES.ERROR;

  const isRefreshing = resolved === PANEL_STATES.REFRESHING;
  const showsChildren = !STATES_WITHOUT_CHILDREN.includes(resolved);

  warnContract(
    !isRefreshing || Boolean(children),
    `Panel${title ? ` "${title}"` : ''} is \`refreshing\` with no children. §11.1 makes `
      + '`refreshing` reachable only from `ready`, so previous data should still be on screen; '
      + 'a refresh over nothing is either the wrong state or a lost payload.',
  );

  // ── State → body. The one dispatch (design.md §11.1) ──────────────────────
  let body = null;
  switch (resolved) {
    case PANEL_STATES.IDLE:
      // Nothing has been asked for yet — no request issued, no failure, no absence.
      // There is nothing true to say, so nothing is said, and `body` keeps its initial
      // `null`. An "empty" message here would assert a fact about data nobody has read.
      break;

    case PANEL_STATES.LOADING: {
      const kind = loading?.kind;
      assertContract(
        LOADING_KINDS.includes(kind),
        `Panel${title ? ` "${title}"` : ''} is \`loading\` but \`loading.kind\` is `
          + `${JSON.stringify(kind)}. Requirement 14.2 wants the skeleton to match the content it `
          + `replaces — one of ${LOADING_KINDS.join(' | ')} — sized from the same row/column config.`,
      );
      body = (
        <LoadingState
          kind={kind}
          rows={loading?.rows}
          columns={loading?.columns}
          // A page with nine panels should announce nine distinguishable loads.
          label={loading?.label ?? (title ? `Loading ${title}` : undefined)}
        />
      );
      break;
    }

    case PANEL_STATES.EMPTY:
      assertContract(
        Boolean(empty) && typeof empty === 'object',
        `Panel${title ? ` "${title}"` : ''} is \`empty\` but has no \`empty\` configuration. `
          + 'Requirement 14.1 wants what is missing, why it matters and the next action; pass '
          + '`empty={{ headline, body, action }}`.',
      );
      // `EmptyState` asserts the three fields individually, so a missing one is named.
      body = <EmptyState {...(empty ?? null)} />;
      break;

    case PANEL_STATES.ERROR:
      assertContract(
        // `known === false` reached this branch as the fallback above, and has no
        // configuration by definition; that path is not a call-site defect.
        !known || (Boolean(error) && typeof error === 'object'),
        `Panel${title ? ` "${title}"` : ''} is \`error\` but has no \`error\` configuration. `
          + 'Pass `error={{ error, context, onRetry }}` — `context` selects the copy family and '
          + '`onRetry` is what makes the retry affordance live (Requirements 14.3, 19.4).',
      );
      body = (
        <ErrorState
          error={error?.error}
          context={error?.context}
          onRetry={error?.onRetry}
          compact={error?.compact}
        />
      );
      break;

    case PANEL_STATES.UNAUTHORISED:
      // Routed through the same single translation rather than given copy of its own:
      // `AUTH_ERROR` in `errorCopy.js` already carries "Your session has expired / Sign
      // in again" and a `/signin` action, and `retryable: false` so no retry is offered.
      // The synthetic `{ category }` is what `resolveCategory` reads when the caller has
      // no error object — a state can be reported without one.
      body = (
        <ErrorState
          error={unauthorised?.error ?? { category: 'AUTH_ERROR' }}
          context={unauthorised?.context}
        />
      );
      break;

    case PANEL_STATES.UNAVAILABLE: {
      const reason = unavailable?.reason;
      assertContract(
        hasText(reason),
        `Panel${title ? ` "${title}"` : ''} is \`unavailable\` but carries no reason. `
          + 'Requirement 19.3 makes `unavailable` a first-class state WITH a human reason — '
          + 'without one it is indistinguishable from an empty panel, which claims the trader '
          + 'has none of these rather than admitting we cannot tell them.',
      );
      body = <UnavailableState reason={hasText(reason) ? reason : FALLBACK_UNAVAILABLE_REASON} />;
      break;
    }

    case PANEL_STATES.READY:
    case PANEL_STATES.REFRESHING:
    default:
      // `showsChildren` rather than an unconditional `children`, so the hook's
      // `STATES_WITHOUT_CHILDREN` is what governs even on the fall-through: a ninth
      // state added to that list is handled correctly here without an edit, which is
      // the only arrangement in which the list and this component cannot drift.
      body = showsChildren ? children : null;
      break;
  }

  const showsEnvironment = environment !== undefined || money === true;
  const showsHeader = hasText(title) || showsEnvironment || Boolean(actions) || isRefreshing;

  const Heading = level === 3 ? 'h3' : 'h2';

  return (
    <section
      aria-labelledby={hasText(title) ? headingId : undefined}
      data-panel-state={resolved}
      data-panel-money={money === true ? 'true' : 'false'}
      className={`flex min-w-0 flex-col rounded-lg border border-line-default bg-surface-panel shadow-panel ${className}`.trim()}
      {...rest}
    >
      {showsHeader ? (
        <header className="flex items-center justify-between gap-3 border-b border-line-subtle px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            {hasText(title) ? (
              <Heading id={headingId} className="truncate text-title font-semibold text-content-primary">
                {title}
              </Heading>
            ) : null}
            {/* Rendered whenever an environment was declared, and — because the
                production fallback must never be silence — whenever `money` is set
                even if the prop was omitted. */}
            {showsEnvironment ? <PanelEnvironment environment={environment} /> : null}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {/* §11.1: `refreshing` keeps the data on screen and gets a subtle inline
                progress affordance, never a skeleton over the top of live figures. */}
            {isRefreshing ? (
              <LoadingState kind="inline" label={title ? `Refreshing ${title}` : 'Refreshing'} />
            ) : null}
            {actions}
          </div>
        </header>
      ) : null}

      {/* The children are absent from the tree in six of the eight states, not hidden
          inside it: `body` is only ever `children` when `showsChildren` is true, so
          there is no markup below that could render a stale payload (Requirement 14.5). */}
      {body === null || body === undefined ? null : <div className="min-w-0 p-4">{body}</div>}
    </section>
  );
}

export default Panel;
