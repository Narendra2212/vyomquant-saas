/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/lib/deployFlow.js — `Deploy_Confirmation_Flow`, as a transition function
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 10.5. `design.md` §8.1, §8.2, §8.3, §8.4.
 * Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 19.1. Properties P13, P14.
 *
 * WHY THE MACHINE LIVES APART FROM THE COMPONENT
 * ---------------------------------------------
 * The two properties this flow carries are both about *construction*, not appearance:
 *
 *   * **P13** — the backend mutation is unreachable before the confirmation step's
 *     explicit action is satisfied.
 *   * **P14** — the real-funds acknowledgement step is constructed **if and only if** the
 *     resolved environment is Live.
 *
 * Both are statements about a transition function, and both are only honestly assertable
 * if the transition function exists apart from anything that renders it. A component that
 * filtered a fixed four-step list at render time would satisfy neither: the acknowledgement
 * object would have been *built* for a paper deployment and merely not painted, which is
 * precisely the word Requirement 8.4 turns on ("SHALL NOT display" reads as "is not
 * constructed" here, because a hidden node is one CSS mistake away from a shown one).
 *
 * So the step list is derived from the resolved environment — see {@link stepsFor} — and
 * the acknowledgement is constructed inside the `LIVE` branch and nowhere else. Off the
 * Live path there is no object to hide.
 *
 * This follows the separation the codebase already uses for pure decisions:
 * `components/shell/navigation.js`, `ResponsiveGate`'s `routeAccess`,
 * `ConnectionStatusIndicator`'s `connectionPresentation`, `design/errorLine.js`.
 * `components/deploy/DeployConfirmation.jsx` renders what this module decides and adds
 * nothing to it.
 *
 * WHAT THIS MODULE DOES NOT DECIDE (Requirement 19.1)
 * --------------------------------------------------
 * **Whether a deploy is permitted.** That is `lib/deployPreflight.js` +
 * `hooks/useDeployPreflight.js` + `components/DeployPreflightPanel.jsx`, which stay
 * authoritative and are wrapped here rather than reimplemented:
 *
 *   * `deploymentRequest()` builds the POST body and the `environment` query parameter, so
 *     the request this flow issues is byte-for-byte the one `pages/Strategies.jsx` issues
 *     today. This module owns no endpoint string beyond the path template and no field
 *     names at all.
 *   * `executionConfigFrom()` computes the per-order notional, which is also the only
 *     honest source for Requirement 8.1's "estimated exposure" — so the review grid reads
 *     the preflight module's arithmetic instead of repeating it.
 *   * `isDeployable()` / the poll's `deployable` verdict is **not** consulted here and is
 *     not duplicated here. The Deploy control is gated on it by the page, twice already
 *     (button state and handler refusal). This flow adds a *review* in front of that gate;
 *     it does not become a second gate with its own opinion.
 *
 * The blockers this module does raise ({@link deployBlockers}) are structural facts about
 * the request itself, not gate conditions: an environment that resolves to none of the
 * three, a missing strategy id, a missing version label. Without those there is no
 * address to POST to — `Strategies.jsx` already refuses on the last one, in those words.
 *
 * ⚠️ THE ONE CONSEQUENTIAL DECISION: AN UNRESOLVED ENVIRONMENT FAILS CLOSED, BOTH WAYS ⚠️
 * ---------------------------------------------------------------------------------------
 * {@link resolveDeployEnvironment} is total over every input — `null`, `undefined`, a
 * number, an unknown string, `'live'`, `' Live '`, `'LIVE'` — and it resolves to exactly
 * one of `'LIVE'`, `'PAPER'`, `'BACKTEST'` or `null`. `null` is a real state, not an error.
 *
 * What happens on `null` is the single most consequential choice in this task, because
 * both obvious defaults are wrong in opposite directions:
 *
 *   * Defaulting to **PAPER** fails *open*: an unrecognised target string — a typo, a
 *     renamed server enum, a mode this client has not been taught — would take the direct
 *     `Review → Submitting` edge and place real orders with no acknowledgement, which is
 *     exactly the accident Requirement 8 exists to prevent. This is the dangerous one.
 *   * Defaulting to **LIVE** would demand a real-funds acknowledgement for a backtest and
 *     would make P14 false (the ack would be constructed off the Live path), besides
 *     training traders to tick a box that does not always mean what it says.
 *
 * So neither. An unresolved environment yields a flow with **one step** (`Configure`), no
 * acknowledgement, and **no forward edge at all**: `Cancel` is the only transition that
 * moves. P14 holds (no ack is constructed, because the environment is not Live) and P13
 * holds a fortiori (no submission is reachable). The flow says which target values it
 * understands, through `blockers`, and waits.
 *
 * `design/semantic.js` reached the same conclusion for the badge, in the same words:
 * *"Defaulting to LIVE would be alarmist; defaulting to PAPER would be dangerous. Saying
 * 'unconfirmed' is the only honest option."* The environment vocabulary is imported from
 * there — `ENVIRONMENT`/`environmentTreatment` — so no environment string is invented in
 * this file.
 *
 * PURITY
 * ------
 * No React, no `window`, no network, no timers, no colour. Every export is deterministic
 * given its arguments, every returned flow is frozen, and {@link submissionOf} *describes*
 * the request rather than performing it — the component performs it. Nothing here can
 * place an order; the worst a bug in this file can do is refuse to let one be placed.
 *
 * @module lib/deployFlow
 */

import { ENVIRONMENT, environmentTreatment } from '../design/semantic';
import { UNREPORTED_REASON, fromNullable } from '../design/reported';
import { deploymentRequest, executionConfigFrom } from './deployPreflight';

/* ══════════════════════════════════════════════════════════════════════════
 * Vocabulary
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * §8.3's states, spelled as the diagram spells them.
 *
 * The four the flow is *about* — `Configure`, `Review`, `AckLive`, `Submitting` — plus the
 * three the diagram's edges require to exist: `Deployed` (2xx), `Failed` (4xx/5xx) and
 * `Cancelled`, which is the diagram's `[*]` given a name so that `Cancel` is a transition
 * with a destination rather than a hole in the table.
 */
export const DEPLOY_STATE = Object.freeze({
  CONFIGURE: 'Configure',
  REVIEW: 'Review',
  ACK_LIVE: 'AckLive',
  SUBMITTING: 'Submitting',
  DEPLOYED: 'Deployed',
  FAILED: 'Failed',
  CANCELLED: 'Cancelled',
});

/** Every state, for exhaustive iteration in tests and in P13's sequence generator. */
export const DEPLOY_STATES = Object.freeze(Object.values(DEPLOY_STATE));

/**
 * Every event the machine recognises.
 *
 * `ACKNOWLEDGE` is separate from `ADVANCE` on purpose, and that separation *is*
 * Requirement 8.3: §8.3 says "confirm DISABLED until checked" and "explicit confirm
 * activated", which is two distinct trader actions. One combined event would make the
 * acknowledgement a parameter of the confirm rather than a precondition of it.
 */
export const DEPLOY_EVENT = Object.freeze({
  /** Configure → Review, Review → AckLive|Submitting, AckLive → Submitting. */
  ADVANCE: 'ADVANCE',
  /** Review → Configure, AckLive → Review, Failed → Review. */
  BACK: 'BACK',
  /** Any non-terminal state → Cancelled. Issues nothing. */
  CANCEL: 'CANCEL',
  /** Legal **only** while standing on `AckLive`. Ticks the real-funds box. */
  ACKNOWLEDGE: 'ACKNOWLEDGE',
  /** Unticks it. The box is a control, not a latch. */
  WITHDRAW: 'WITHDRAW',
  /** Submitting → Deployed. The 2xx. */
  SUCCEEDED: 'SUCCEEDED',
  /** Submitting → Failed. The 4xx/5xx. */
  REJECTED: 'REJECTED',
});

/** Every event, for P13's sequence generator. */
export const DEPLOY_EVENTS = Object.freeze(Object.values(DEPLOY_EVENT));

/** The steps §8.3 numbers. `Submitting` is a state, not a step — nothing is asked there. */
export const DEPLOY_STEP = Object.freeze({
  CONFIGURE: 'configure',
  REVIEW: 'review',
  ACK_LIVE: 'ackLive',
});

/** Why a transition was refused. Reported rather than thrown — see {@link transition}. */
export const REJECTION = Object.freeze({
  /** The event is not one of {@link DEPLOY_EVENTS}. */
  UNKNOWN_EVENT: 'UNKNOWN_EVENT',
  /** A real event, but the current state has no edge for it. */
  NOT_LEGAL_IN_STATE: 'NOT_LEGAL_IN_STATE',
  /** {@link deployBlockers} is non-empty, so no forward edge is open. */
  BLOCKED: 'BLOCKED',
  /** `AckLive → Submitting` attempted with the box unticked (Requirement 8.3). */
  ACKNOWLEDGEMENT_REQUIRED: 'ACKNOWLEDGEMENT_REQUIRED',
  /** The value passed as a flow was not produced by this module. */
  UNRECOGNISED_FLOW: 'UNRECOGNISED_FLOW',
});

/** Structural reasons the flow cannot move forward. Not gate conditions — see the header. */
export const BLOCKER = Object.freeze({
  ENVIRONMENT_UNCONFIRMED: 'ENVIRONMENT_UNCONFIRMED',
  NO_STRATEGY: 'NO_STRATEGY',
  NO_VERSION: 'NO_VERSION',
});

const BLOCKER_MESSAGE = Object.freeze({
  [BLOCKER.ENVIRONMENT_UNCONFIRMED]:
    'The deployment target is not one of Live, Paper or Backtest, so this flow cannot '
    + 'continue. Choose a target — nothing is assumed, because assuming Paper would place '
    + 'real orders without the real-funds step.',
  [BLOCKER.NO_STRATEGY]:
    'This deployment names no strategy, so there is no deployment address to submit to.',
  // The wording `pages/Strategies.jsx` already refuses with, kept identical so the two
  // surfaces cannot describe the same refusal differently.
  [BLOCKER.NO_VERSION]:
    'This strategy has no current version to deploy. Save a version first — a deployment '
    + 'always binds one immutable version.',
});

/* ══════════════════════════════════════════════════════════════════════════
 * Small readers
 * ══════════════════════════════════════════════════════════════════════════ */

/** A string with visible content, trimmed, or `null`. Numbers count as text for ids. */
function text(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

/** A plain object, or `null`. */
function record(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

/* ══════════════════════════════════════════════════════════════════════════
 * Environment resolution — total, and failing closed in both directions
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The deploy target, resolved to an `EnvironmentId` or to `null`.
 *
 * Delegates the vocabulary entirely to `design/semantic.js`'s `environmentTreatment`,
 * which trims, upper-cases and answers `null` for anything outside `ENVIRONMENT`. That
 * makes `'live'` (the string `pages/Strategies.jsx` holds in `deployConfig.environment`),
 * `'LIVE'`, `' Live '` and `'lIvE'` all resolve to `'LIVE'`, and makes `null`,
 * `undefined`, `42`, `{}`, `''` and `'production'` all resolve to `null`.
 *
 * Deliberately narrower than `semantic.resolveEnvironment`: that function *infers* an
 * environment from server fields (`isSimulated`, a backtest context). A deploy target is
 * **chosen**, not inferred, so there is nothing here to fall back to and no second field
 * that could disagree with the first.
 *
 * See the header for why `null` does not become `PAPER`.
 *
 * @param {unknown} value The trader's chosen target, however it is spelled.
 * @returns {'LIVE'|'PAPER'|'BACKTEST'|null}
 */
export function resolveDeployEnvironment(value) {
  return environmentTreatment(value)?.id ?? null;
}

/** True exactly when the resolved target is Live. The whole of P14's antecedent. */
export function isLiveEnvironment(environmentId) {
  return resolveDeployEnvironment(environmentId) === ENVIRONMENT.LIVE.id;
}

/* ══════════════════════════════════════════════════════════════════════════
 * Requirement 8.5 — title and confirm intent, as data keyed by environment
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Per-environment dialog presentation.
 *
 * `confirmIntent` uses `ds/ConfirmDialog`'s own `CONFIRM_INTENTS` vocabulary
 * (`destructive` | `live` | `neutral`). §8.3 writes the calm case as `intent="primary"`;
 * that is `ConfirmDialog`'s `neutral`, which the component maps to the brand token —
 * "primary" is not a risk level, and the dialog says so in its own comment.
 *
 * §8.3 authors two of the three titles verbatim ("Deploy to live trading", "Start paper
 * session"). The Backtest title follows §8.2's vocabulary for that environment
 * (*"Simulated on historical data"*) rather than being invented from nothing; it is the
 * one string here that the design does not spell out.
 *
 * The badge is not built here. §8.5 is satisfied by handing `environment` to
 * `ConfirmDialog`, which renders `TradingEnvironmentBadge variant="strip"` on every step
 * from `design/semantic.js`'s treatment — so hue, label, icon and border stay in the one
 * module that owns them.
 */
export const DEPLOY_PRESENTATION = Object.freeze({
  [ENVIRONMENT.LIVE.id]: Object.freeze({
    environment: ENVIRONMENT.LIVE.id,
    title: 'Deploy to live trading',
    confirmIntent: 'live',
    confirmLabel: 'Deploy live',
  }),
  [ENVIRONMENT.PAPER.id]: Object.freeze({
    environment: ENVIRONMENT.PAPER.id,
    title: 'Start paper session',
    confirmIntent: 'neutral',
    confirmLabel: 'Start paper session',
  }),
  [ENVIRONMENT.BACKTEST.id]: Object.freeze({
    environment: ENVIRONMENT.BACKTEST.id,
    title: 'Start backtest run',
    confirmIntent: 'neutral',
    confirmLabel: 'Start backtest',
  }),
});

/**
 * The presentation for an unresolved target.
 *
 * `confirmIntent` is `neutral` rather than `live`: the confirm control is unreachable in
 * this state anyway, and painting an unresolved target in the live hue would claim the one
 * thing nobody has established. The title asks the question instead of answering it.
 */
export const UNCONFIRMED_PRESENTATION = Object.freeze({
  environment: null,
  title: 'Choose a deployment target',
  confirmIntent: 'neutral',
  confirmLabel: 'Continue',
});

/**
 * Title and confirm intent for one target. Total: every input has an answer.
 *
 * @param {unknown} environment
 * @returns {{environment: string|null, title: string, confirmIntent: string,
 *   confirmLabel: string}}
 */
export function deployPresentation(environment) {
  const id = resolveDeployEnvironment(environment);
  return id === null ? UNCONFIRMED_PRESENTATION : DEPLOY_PRESENTATION[id];
}

/* ══════════════════════════════════════════════════════════════════════════
 * Requirement 8.1 — the eight review fields, declared as data
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Requirement 8.1's eight fields, in the order the requirement lists them.
 *
 * Declared as data with a stable order so that "Review cannot be left toward Submitting
 * without rendering all eight fields" is structural: {@link buildDeployReview} always
 * returns one entry per row of this table, whatever the configuration carries, so there is
 * no configuration for which a row is *absent*. A row the configuration does not carry is
 * `unavailable` — the `Reported<T>` unavailable arm from `design/reported.js`, which
 * `ds/Metric` and `ds/ConfirmDialog` already render as the not-available marker with the
 * reason. Nothing is defaulted into existence and no value is fabricated (Requirements
 * 14.5, 19.3).
 *
 * `reason` is the sentence shown when the field is missing. It says what is not there and
 * why, in the trader's terms, rather than falling through to `UNREPORTED_REASON` — which
 * is about a *server* that did not answer and is the wrong explanation for a field the
 * trader simply has not filled in.
 */
export const REVIEW_FIELDS = Object.freeze([
  Object.freeze({
    id: 'strategyName',
    label: 'Strategy',
    reason: 'The deployment configuration does not name a strategy.',
  }),
  Object.freeze({
    id: 'version',
    label: 'Version',
    reason: 'No version label is bound to this deployment.',
  }),
  Object.freeze({
    id: 'exchange',
    label: 'Exchange',
    reason: 'No exchange is named. The venue is resolved from the account server-side.',
  }),
  Object.freeze({
    id: 'account',
    label: 'Account',
    reason: 'No exchange account is selected.',
  }),
  Object.freeze({
    id: 'market',
    label: 'Market',
    reason: 'No market is selected.',
  }),
  Object.freeze({
    id: 'sizing',
    label: 'Quantity / sizing',
    reason: 'No allocated capital or per-trade size is configured.',
  }),
  Object.freeze({
    id: 'riskConfig',
    label: 'Risk configuration',
    reason: 'The deployment configuration does not name a risk configuration.',
  }),
  Object.freeze({
    id: 'estimatedExposure',
    label: 'Estimated exposure',
    reason:
      'Exposure cannot be estimated without both the allocated capital and the per-trade '
      + 'size.',
  }),
]);

/** The eight ids, in order. Exported so a test asserts the order without re-listing it. */
export const REVIEW_FIELD_IDS = Object.freeze(REVIEW_FIELDS.map((field) => field.id));

/**
 * The sizing line, assembled from whatever the configuration actually carries.
 *
 * A caller that knows the account currency should pass `sizingLabel` and have it used
 * verbatim; this derivation is the fallback. Each part is emitted only when its own input
 * is present, and no currency symbol is attached to a bare number — claiming a currency
 * nobody stated would be the fabrication Requirement 14.5 forbids. Both parts absent
 * yields `null`, which becomes the not-available marker.
 *
 * @param {Object} config
 * @returns {string|null}
 */
function sizingLine(config) {
  const supplied = text(config.sizingLabel);
  if (supplied !== null) return supplied;

  const parts = [];
  const capital = text(config.capital);
  const perTrade = text(config.tradeSizePct);
  if (capital !== null) parts.push(`${capital} allocated`);
  if (perTrade !== null) parts.push(`${perTrade}% per trade`);
  return parts.length === 0 ? null : parts.join(' · ');
}

/**
 * Requirement 8.1's eight fields for one configuration, each as a `Reported<T>`.
 *
 * Always eight entries, always in {@link REVIEW_FIELDS} order, for every input including
 * `null` and `{}`. A missing field does **not** block the flow — §8.3 is explicit that the
 * flow is not blocked by it, only that it is never silently defaulted.
 *
 * `estimatedExposure` is read from `deployPreflight.executionConfigFrom`'s
 * `max_order_notional` rather than recomputed: that function already expresses "the
 * notional a single order may carry" from capital × per-trade size, and it declines to
 * invent a limit from a blank or nonsensical field. A second derivation here could drift
 * from the number actually sent to the server, which is the one thing this grid must not
 * do.
 *
 * @param {Object} [config] The flow's configuration.
 * @returns {ReadonlyArray<{id: string, label: string,
 *   reported: {available: true, value: *}|{available: false, reason: string}}>}
 */
export function buildDeployReview(config) {
  const source = record(config) ?? {};
  const account = record(source.account);

  const values = {
    strategyName: text(source.strategyName) ?? text(record(source.strategy)?.name),
    version: text(source.version) ?? text(record(source.strategy)?.current_version),
    exchange: text(source.exchange) ?? text(account?.exchange_id),
    account:
      text(source.accountLabel)
      ?? text(account?.label)
      ?? text(account?.name)
      ?? text(account?.account_name)
      ?? text(account?.id)
      ?? text(source.exchangeAccountId),
    market: text(source.market) ?? text(source.symbol),
    sizing: sizingLine(source),
    riskConfig: text(source.riskConfigLabel) ?? text(source.riskConfigId),
    // `{}` when either input is missing or non-positive, which reads as not-available.
    estimatedExposure: executionConfigFrom(source).max_order_notional ?? null,
  };

  return Object.freeze(
    REVIEW_FIELDS.map((field) =>
      Object.freeze({
        id: field.id,
        label: field.label,
        reported: fromNullable(values[field.id], field.reason),
      }),
    ),
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * Requirements 8.2 / 8.4 — the acknowledgement, constructed only on the Live path
 * ══════════════════════════════════════════════════════════════════════════ */

/** §8.3's checkbox label, verbatim. */
export const ACKNOWLEDGEMENT_LABEL =
  'I understand this places real orders with real funds';

/**
 * §8.3's real-funds statement, for one Live deployment.
 *
 * **Called from the `LIVE` branch of {@link stepsFor} and from nowhere else.** For Paper
 * and Backtest this function is never reached, so the sentence is not merely hidden — it
 * does not exist in the returned flow (Requirement 8.4, P14).
 *
 * The venue and account are interpolated when known and omitted when not; the "real
 * orders … real funds" clause is present in all four combinations, because that clause is
 * the requirement and a blank exchange name is no reason to soften it. No placeholder, no
 * `undefined`, and no fabricated account number.
 *
 * @param {Object} config
 * @returns {{statement: string, label: string, control: string}} The shape
 *   `ds/ConfirmDialog`'s `acknowledgement` prop takes, so it passes straight through.
 */
function liveAcknowledgement(config) {
  const source = record(config) ?? {};
  const account = record(source.account);
  const exchange = text(source.exchange) ?? text(account?.exchange_id);
  const accountName =
    text(source.accountLabel)
    ?? text(account?.label)
    ?? text(account?.name)
    ?? text(account?.account_name)
    ?? text(account?.id)
    ?? text(source.exchangeAccountId);

  const venue = exchange === null ? '' : ` on ${exchange}`;
  const held = accountName === null ? '' : ` in account ${accountName}`;

  return Object.freeze({
    statement: `Confirming will place real orders${venue} using real funds${held}.`,
    label: ACKNOWLEDGEMENT_LABEL,
    control: 'checkbox',
  });
}

/* ══════════════════════════════════════════════════════════════════════════
 * The step list — derived from the resolved environment
 * ══════════════════════════════════════════════════════════════════════════ */

function step(id, state, ordinal, title, extra) {
  return Object.freeze({
    id,
    state,
    ordinal,
    title,
    // §8.3's own heading form, e.g. "Step 3 — Real funds".
    heading: `Step ${ordinal} — ${title}`,
    ...extra,
  });
}

/**
 * The steps for one resolved environment.
 *
 * This is the mechanism P14 rests on. The list is **built from** the environment, so there
 * is no path on which an `ackLive` step exists off the Live path: the `push` that creates
 * it sits inside `if (environment === LIVE)`, and {@link liveAcknowledgement} is called
 * from there. Filtering a fixed four-entry list would leave the acknowledgement object
 * constructed and merely unlisted, which is not what Requirement 8.4 asks for.
 *
 *   * `LIVE`      → Target, Review, Real funds
 *   * `PAPER`     → Target, Review          (§8.3's direct `Review → Submitting` edge)
 *   * `BACKTEST`  → Target, Review
 *   * unresolved  → Target                  (nowhere to go; see the header)
 *
 * @param {'LIVE'|'PAPER'|'BACKTEST'|null} environment An already-resolved id.
 * @param {Object} config
 * @returns {ReadonlyArray<Object>}
 */
function stepsFor(environment, config) {
  const steps = [
    step(DEPLOY_STEP.CONFIGURE, DEPLOY_STATE.CONFIGURE, 1, 'Target'),
  ];

  if (environment === null) return Object.freeze(steps);

  steps.push(step(DEPLOY_STEP.REVIEW, DEPLOY_STATE.REVIEW, 2, 'Review'));

  if (environment === ENVIRONMENT.LIVE.id) {
    steps.push(
      step(DEPLOY_STEP.ACK_LIVE, DEPLOY_STATE.ACK_LIVE, 3, 'Real funds', {
        acknowledgement: liveAcknowledgement(config),
      }),
    );
  }

  return Object.freeze(steps);
}

/* ══════════════════════════════════════════════════════════════════════════
 * The flow object
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Every flow this module has produced.
 *
 * A `WeakSet` brand rather than a `kind: 'deployFlow'` field, because a field is forgeable
 * and this one is not: membership is granted only by {@link freezeFlow}, which is
 * module-private and is the sole constructor of a flow. A hand-written
 * `{state: 'Submitting', submission: {...}}` is therefore not a flow — {@link submissionOf}
 * and {@link transition} both refuse it — which is what makes "the POST is issued in the
 * Submitting transition" a statement about the only reachable code path rather than a
 * convention.
 *
 * It holds identities, not values, so every export stays deterministic given its
 * arguments, and it never grows: entries are collected with the flows they brand.
 */
const FLOWS = new WeakSet();

function freezeFlow(flow) {
  const frozen = Object.freeze(flow);
  FLOWS.add(frozen);
  return frozen;
}

/**
 * Whether `candidate` was produced by this module.
 *
 * @param {unknown} candidate
 * @returns {boolean}
 */
export function isDeployFlow(candidate) {
  return typeof candidate === 'object' && candidate !== null && FLOWS.has(candidate);
}

/**
 * The structural facts that hold every forward edge shut. Order is stable.
 *
 * Not a gate (Requirement 19.1): `deployPreflight` decides whether a deployment is
 * permitted, and this list never grows to include one of its conditions. These three are
 * facts about whether a request can be *addressed* and whether the flow can be *shaped*:
 * without a resolved environment there is no step list to derive, and without a strategy
 * and a version there is no path to POST to.
 *
 * @param {unknown} flow A flow, or a raw configuration object.
 * @returns {ReadonlyArray<{code: string, message: string}>}
 */
export function deployBlockers(flow) {
  const source = isDeployFlow(flow) ? flow.config : (record(flow) ?? {});
  const environment = isDeployFlow(flow)
    ? flow.environment
    : resolveDeployEnvironment(source.environment);

  const codes = [];
  if (environment === null) codes.push(BLOCKER.ENVIRONMENT_UNCONFIRMED);
  if (text(source.strategyId) === null) codes.push(BLOCKER.NO_STRATEGY);
  if (text(source.version) === null) codes.push(BLOCKER.NO_VERSION);

  return Object.freeze(
    codes.map((code) => Object.freeze({ code, message: BLOCKER_MESSAGE[code] })),
  );
}

/**
 * The request the `Submitting` transition issues — described, never performed.
 *
 * The route is `POST /api/strategies/{id}/versions/{version}/deploy`, the gated endpoint
 * Requirement 11.5 names. The body and the `environment` query parameter come
 * from `deployPreflight.deploymentRequest`, which owns the server's
 * `DeploymentBindingRequest` shape (`extra="forbid"`) and its `execution_config`
 * allow-list — so this module names no wire field and cannot drift from what the preflight
 * was asked about.
 *
 * `endpoints.strategies.deployVersion(strategyId, version, body, { environment })` is the
 * call this describes. Nothing is imported from `src/api` here: a pure module that held an
 * HTTP client could place an order, and this one must not be able to.
 *
 * `path` below is **descriptive only** — nothing issues a request from it. The
 * authoritative path is `endpoints.strategies.deployVersion`'s, and that is the one the
 * `Submitting` transition's caller (`components/DeployConfirmation.jsx`) travels. This
 * string carried the `/api/strategy-operations/…` spelling that `deployVersion` was just
 * corrected off; it 404s, and it is fixed here so a reader cannot copy a dead path out of
 * a module whose job is to describe the request. If the two ever need to differ, the
 * describing copy is the one that is wrong.
 *
 * @param {Object} config
 * @returns {Object} Frozen.
 */
function submissionFor(config) {
  const request = deploymentRequest(config);
  const strategyId = text(config.strategyId);
  const version = text(config.version);

  return Object.freeze({
    method: 'POST',
    path:
      `/api/strategies/${encodeURIComponent(strategyId)}`
      + `/versions/${encodeURIComponent(version)}/deploy`,
    strategyId,
    version,
    body: request.body,
    // The legacy column rides the query string; the request model has no field for it.
    environment: request.environment,
    query: request.environment === null ? Object.freeze({}) : Object.freeze({ environment: request.environment }),
  });
}

/**
 * Open a deploy confirmation flow. The only constructor.
 *
 * The environment is resolved once, here, and the step list, the acknowledgement, the
 * presentation and the review grid are all derived from that one resolution — so no two
 * parts of the flow can disagree about which environment it is for.
 *
 * @param {Object} [config] The deployment being confirmed.
 * @param {string} [config.strategyId]
 * @param {string} [config.version] The immutable version label, e.g. `"1.2"`.
 * @param {unknown} [config.environment] `'LIVE'|'PAPER'|'BACKTEST'`, in any casing, or the
 *   lowercase mode `pages/Strategies.jsx` holds. Anything else resolves to `null`.
 * @param {Object} [config.account] The selected exchange account row.
 * @param {string} [config.strategyName]
 * @param {string} [config.exchange]
 * @param {string} [config.market]
 * @param {string|number} [config.capital]
 * @param {string|number} [config.tradeSizePct]
 * @param {string} [config.riskConfigId]
 * @param {string} [config.gapStrategy]
 * @returns {Object} A frozen flow in `Configure`.
 */
export function beginDeployFlow(config) {
  const source = Object.freeze({ ...(record(config) ?? {}) });
  const environment = resolveDeployEnvironment(source.environment);
  const steps = stepsFor(environment, source);
  const ackStep = steps.find((s) => s.id === DEPLOY_STEP.ACK_LIVE) ?? null;

  return freezeFlow({
    state: DEPLOY_STATE.CONFIGURE,
    environment,
    config: source,
    steps,
    /**
     * A mirror of the `ackLive` step's own acknowledgement, `null` off the Live path.
     * There is one object, referenced twice — the step list is where it is constructed.
     */
    acknowledgement: ackStep?.acknowledgement ?? null,
    acknowledged: false,
    review: buildDeployReview(source),
    presentation: deployPresentation(environment),
    blockers: deployBlockers({ ...source, environment }),
    /** Non-null **only** in `Submitting`. */
    submission: null,
    /** How many times this flow has entered `Submitting`. P13's "exactly once". */
    submissionCount: 0,
    /** Why the last transition was refused, or `null` if it was taken. */
    rejection: null,
  });
}

/**
 * A flow's successor, built from the previous flow so nothing is recomputed and nothing
 * can drift. `steps`, `review`, `presentation`, `environment` and `config` are carried
 * through by reference — they are properties of the *deployment*, not of the step.
 */
function moveTo(flow, state, patch) {
  return freezeFlow({
    ...flow,
    state,
    rejection: null,
    ...patch,
  });
}

/** The same flow, refused, with the reason recorded. The state does not change. */
function refuse(flow, event, reason) {
  return freezeFlow({
    ...flow,
    rejection: Object.freeze({ from: flow.state, event, reason }),
  });
}

/* ══════════════════════════════════════════════════════════════════════════
 * The transition table — total, and the only source of a next state
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Every legal edge in §8.3, as `state → event → resolver`.
 *
 * An edge that is not in this table does not exist. There is no `default:` branch anywhere
 * below and no code path that computes a next state without consulting this object, which
 * is what makes an invalid transition unexpressible rather than merely discouraged: to add
 * one you would have to add a row here.
 *
 * A resolver answers `{next, reason}` — `next: null` with a reason is a refusal, and every
 * refusal is a *stated* one.
 */
const TRANSITIONS = Object.freeze({
  [DEPLOY_STATE.CONFIGURE]: Object.freeze({
    [DEPLOY_EVENT.ADVANCE]: (flow) => forward(flow, DEPLOY_STATE.REVIEW),
    [DEPLOY_EVENT.CANCEL]: () => ({ next: DEPLOY_STATE.CANCELLED, reason: null }),
  }),

  [DEPLOY_STATE.REVIEW]: Object.freeze({
    // Requirement 8.4: the Live path detours through AckLive; Paper and Backtest do not.
    // The branch reads the environment resolved at construction, so it cannot disagree
    // with the step list that was derived from the same value.
    [DEPLOY_EVENT.ADVANCE]: (flow) =>
      forward(
        flow,
        flow.environment === ENVIRONMENT.LIVE.id
          ? DEPLOY_STATE.ACK_LIVE
          : DEPLOY_STATE.SUBMITTING,
      ),
    [DEPLOY_EVENT.BACK]: () => ({ next: DEPLOY_STATE.CONFIGURE, reason: null }),
    [DEPLOY_EVENT.CANCEL]: () => ({ next: DEPLOY_STATE.CANCELLED, reason: null }),
  }),

  [DEPLOY_STATE.ACK_LIVE]: Object.freeze({
    // Requirement 8.3. The box, then the button — two events, in that order, or nothing.
    [DEPLOY_EVENT.ADVANCE]: (flow) =>
      flow.acknowledged === true
        ? forward(flow, DEPLOY_STATE.SUBMITTING)
        : { next: null, reason: REJECTION.ACKNOWLEDGEMENT_REQUIRED },
    // Legal only here, which is what makes the acknowledgement impossible to satisfy
    // before the step that states what is being acknowledged has been reached.
    [DEPLOY_EVENT.ACKNOWLEDGE]: () => ({ next: DEPLOY_STATE.ACK_LIVE, reason: null }),
    [DEPLOY_EVENT.WITHDRAW]: () => ({ next: DEPLOY_STATE.ACK_LIVE, reason: null }),
    [DEPLOY_EVENT.BACK]: () => ({ next: DEPLOY_STATE.REVIEW, reason: null }),
    [DEPLOY_EVENT.CANCEL]: () => ({ next: DEPLOY_STATE.CANCELLED, reason: null }),
  }),

  // No ADVANCE, no BACK and no CANCEL: the request is in flight. §8.3 gives this state two
  // edges and they are both the server's answer, which is also why re-advancing cannot
  // produce a second submission.
  [DEPLOY_STATE.SUBMITTING]: Object.freeze({
    [DEPLOY_EVENT.SUCCEEDED]: () => ({ next: DEPLOY_STATE.DEPLOYED, reason: null }),
    [DEPLOY_EVENT.REJECTED]: () => ({ next: DEPLOY_STATE.FAILED, reason: null }),
  }),

  [DEPLOY_STATE.FAILED]: Object.freeze({
    [DEPLOY_EVENT.BACK]: () => ({ next: DEPLOY_STATE.REVIEW, reason: null }),
    [DEPLOY_EVENT.CANCEL]: () => ({ next: DEPLOY_STATE.CANCELLED, reason: null }),
  }),

  /** Terminal. §8.3's `Deployed --> [*]`. */
  [DEPLOY_STATE.DEPLOYED]: Object.freeze({}),
  /** Terminal. The diagram's `[*]`. */
  [DEPLOY_STATE.CANCELLED]: Object.freeze({}),
});

/**
 * A forward edge, gated on {@link deployBlockers}.
 *
 * One place, so no forward edge can forget it — including the direct
 * `Review → Submitting` edge, which is the one that reaches the write path without an
 * acknowledgement.
 */
function forward(flow, next) {
  return flow.blockers.length === 0
    ? { next, reason: null }
    : { next: null, reason: REJECTION.BLOCKED };
}

/** The resolver for one `state × event`, or `null` when the state has no such edge. */
function resolverFor(state, event) {
  const edges = TRANSITIONS[state];
  if (!edges) return null;
  return Object.prototype.hasOwnProperty.call(edges, event) ? edges[event] : null;
}

/**
 * What `event` would do to `flow`, without doing it.
 *
 * {@link transition} and {@link canSubmit} both go through this, which is what makes the
 * predicate and the machine incapable of disagreeing: a state the predicate says is
 * unreachable is unreachable, because the predicate is asking the transition table.
 *
 * @param {unknown} flow
 * @param {unknown} event
 * @returns {{next: string|null, reason: string|null}}
 */
function outcomeOf(flow, event) {
  if (!isDeployFlow(flow)) return { next: null, reason: REJECTION.UNRECOGNISED_FLOW };
  if (typeof event !== 'string' || !DEPLOY_EVENTS.includes(event)) {
    return { next: null, reason: REJECTION.UNKNOWN_EVENT };
  }
  const resolve = resolverFor(flow.state, event);
  if (resolve === null) return { next: null, reason: REJECTION.NOT_LEGAL_IN_STATE };
  return resolve(flow);
}

/**
 * Apply one event. **Total**: every `state × event` — and every non-flow, every non-event —
 * has a defined answer, and the answer is always a flow.
 *
 * An unrecognised event does not advance: the returned flow has the same `state`, the same
 * `acknowledged`, the same `submission` and a `rejection` naming what was refused and why.
 * Nothing throws, because a dialog that throws cannot be cancelled, and a live deployment
 * confirmation is the last place to leave a trader with no way out.
 *
 * A value that is not a flow this module produced yields a fresh, blocked `Configure` flow
 * rather than an exception — fail closed, and nothing to submit.
 *
 * @param {Object} flow A flow from {@link beginDeployFlow} or a previous `transition`.
 * @param {string} event One of {@link DEPLOY_EVENTS}.
 * @returns {Object} A frozen flow.
 */
export function transition(flow, event) {
  if (!isDeployFlow(flow)) {
    return refuse(beginDeployFlow(), event, REJECTION.UNRECOGNISED_FLOW);
  }

  const { next, reason } = outcomeOf(flow, event);
  if (next === null) return refuse(flow, event, reason ?? REJECTION.NOT_LEGAL_IN_STATE);

  // ── The acknowledgement's lifetime ────────────────────────────────────────────────────
  // Set only by ACKNOWLEDGE, and only while standing on AckLive. Cleared by WITHDRAW, and
  // cleared by *leaving* AckLive anywhere other than into Submitting: a trader who steps
  // back to change the account or the sizing has acknowledged a different deployment than
  // the one they would then be confirming, and a stale tick carried forward would let the
  // second one through on the first one's consent. Entering Failed clears it too, so a
  // retry after a rejected POST is acknowledged again rather than replayed.
  let acknowledged = flow.acknowledged;
  if (event === DEPLOY_EVENT.ACKNOWLEDGE) acknowledged = true;
  else if (event === DEPLOY_EVENT.WITHDRAW) acknowledged = false;
  else if (next !== DEPLOY_STATE.ACK_LIVE && next !== DEPLOY_STATE.SUBMITTING) {
    acknowledged = false;
  }

  // ── The one place a submission is constructed (Requirement 8.3) ───────────────────────
  // Reached only through the table above, which means only from Review on a non-Live
  // target or from an acknowledged AckLive. Every other state carries `submission: null`,
  // so there is no code path on which a request exists before this transition.
  if (next === DEPLOY_STATE.SUBMITTING) {
    return moveTo(flow, next, {
      acknowledged,
      submission: submissionFor(flow.config),
      submissionCount: flow.submissionCount + 1,
    });
  }

  return moveTo(flow, next, { acknowledged, submission: null });
}

/* ══════════════════════════════════════════════════════════════════════════
 * Queryable predicates — what a component asks before it renders a control
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Whether `ADVANCE` would move the flow at all.
 *
 * @param {unknown} flow
 * @returns {boolean}
 */
export function canAdvance(flow) {
  return outcomeOf(flow, DEPLOY_EVENT.ADVANCE).next !== null;
}

/**
 * **The submit guard.** Whether `ADVANCE` from here enters `Submitting` — that is, whether
 * the next confirm reaches the backend.
 *
 * This is the predicate P13 is asserted against, and it is false at every state before the
 * confirmation step is satisfied:
 *
 *   * `Configure` — false for every environment; the only edge is to `Review`.
 *   * `Review` — false on the **Live** path (the edge is to `AckLive`); true for Paper and
 *     Backtest, which is §8.3's direct edge and Requirement 8.4.
 *   * `AckLive` — false until `ACKNOWLEDGE`, true after it, false again after `WITHDRAW`.
 *   * `Submitting`, `Deployed`, `Failed`, `Cancelled` — false; none has an `ADVANCE` edge.
 *   * anything with a blocker, and anything that is not a flow — false.
 *
 * It reads the same transition table `transition` reads, so it cannot be optimistic about
 * an edge that does not exist.
 *
 * @param {unknown} flow
 * @returns {boolean}
 */
export function canSubmit(flow) {
  return outcomeOf(flow, DEPLOY_EVENT.ADVANCE).next === DEPLOY_STATE.SUBMITTING;
}

/**
 * Why `ADVANCE` is refused, or `null` when it is not.
 *
 * `CommandButton` requires a `disabledReason` for every disabled control (Requirement
 * 19.4), and this is where the deploy confirm's comes from.
 *
 * @param {unknown} flow
 * @returns {string|null} A sentence, or `null`.
 */
export function advanceRefusal(flow) {
  const { next, reason } = outcomeOf(flow, DEPLOY_EVENT.ADVANCE);
  if (next !== null) return null;
  if (reason === REJECTION.BLOCKED && isDeployFlow(flow)) {
    return flow.blockers.map((blocker) => blocker.message).join(' ');
  }
  if (reason === REJECTION.ACKNOWLEDGEMENT_REQUIRED) {
    return `Tick "${ACKNOWLEDGEMENT_LABEL}" to continue.`;
  }
  return UNREPORTED_REASON;
}

/**
 * The real-funds acknowledgement, or `null` when none was constructed.
 *
 * `null` for Paper, for Backtest and for an unresolved target — and `null` because nothing
 * was built, not because something was filtered. Half of P14 reads this; the other half
 * reads {@link hasAckLiveStep}.
 *
 * @param {unknown} flow
 * @returns {{statement: string, label: string, control: string}|null}
 */
export function acknowledgementOf(flow) {
  return isDeployFlow(flow) ? flow.acknowledgement : null;
}

/**
 * Whether the flow's step list contains the real-funds step.
 *
 * Asks the step list rather than the environment, so it would notice a step list that
 * disagreed with the environment it was derived from. This is what P14 asserts is true
 * **iff** the resolved environment is Live.
 *
 * @param {unknown} flow
 * @returns {boolean}
 */
export function hasAckLiveStep(flow) {
  return isDeployFlow(flow)
    ? flow.steps.some((s) => s.id === DEPLOY_STEP.ACK_LIVE)
    : false;
}

/**
 * The request to issue, or `null`.
 *
 * Non-null in exactly one state, `Submitting`, and only for a flow this module produced.
 * A component calls this, sees `null` everywhere else, and therefore has nothing to send
 * before the confirmation step is satisfied — which is P13 stated as a return value.
 *
 * @param {unknown} flow
 * @returns {Object|null}
 */
export function submissionOf(flow) {
  if (!isDeployFlow(flow)) return null;
  return flow.state === DEPLOY_STATE.SUBMITTING ? flow.submission : null;
}

/** The step the flow is standing on, or `null` for the states that are not steps. */
export function currentStep(flow) {
  if (!isDeployFlow(flow)) return null;
  return flow.steps.find((s) => s.state === flow.state) ?? null;
}
