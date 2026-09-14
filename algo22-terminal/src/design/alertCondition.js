/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/design/alertCondition.js — Requirement 3.3's condition, derived
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 19.2 part A. design.md §7.1. Requirements 3.3, 14.5, 19.3.
 *
 * `design/pageFields.js`'s `dashboard/alertCondition` entry is the declaration and this
 * module is its implementation. The entry says, verbatim:
 *
 *   > The disjunction over three real field sets: any `exchange.exchanges[].status` not
 *   > connected, OR any `strategies.items[].status` in an error or stopped-on-error state,
 *   > OR any `executions[].status` failed or rejected. No fourth input, and no
 *   > client-invented severity — the strip names which of the three fired.
 *
 * Nothing here adds a fourth input, and nothing here ranks the three against each other.
 * {@link deriveAlertCondition} answers *which arms fired, on which subjects, reporting
 * which states*. The severity, the hue, the icon and the live-region role are `ds/Alert`'s,
 * chosen from a `severity` the call site passes as a constant — not from this data.
 *
 * WHY A MODULE AND NOT A `useMemo` IN THE PAGE
 * -------------------------------------------
 * Same argument `lib/deployFlow.js`, `lib/rowActions.js` and `lib/strategyHealth.js` make:
 * this is a purity claim, so it is testable apart from the page. No React, no `window`, no
 * network, no colour, no clock. Every return value is frozen, and every function is TOTAL —
 * a non-array, a `null`, a number where a record belongs and a status of the wrong type all
 * produce an answer rather than an exception. A derivation that can throw is a derivation
 * that can take the Dashboard down on a payload shape nobody predicted.
 *
 * "NOTHING WAS REPORTED" IS NOT "ALL CLEAR" (`absence: UNMEASURABLE`)
 * -----------------------------------------------------------------
 * The declaration's `absence` is `UNMEASURABLE`, with the reason *"No exchange, strategy or
 * execution state was reported, so no alert condition could be evaluated."* That is a third
 * outcome, distinct from both "a condition fired" and "the conditions were checked and none
 * held", and it is the one this module exists to keep separable:
 *
 *   * `{evaluated: true,  firing: true }`  — at least one arm read a state and it fired.
 *   * `{evaluated: true,  firing: false}`  — states were read and none of them fired. This
 *     is the ONLY outcome that may be rendered as nothing, and it is still not an
 *     assertion: the caller renders no strip, which is silence, not an all-clear.
 *   * `{evaluated: false, firing: false}`  — NOT ONE readable status reached any of the
 *     three arms. Nothing was checked, so nothing is known. `firing` is `false` here for
 *     the same reason `0` is not `null` — the disjunction over an empty set is false — and
 *     `evaluated` is what stops a caller reading that `false` as a clean bill of health.
 *
 * A boolean alone cannot carry that distinction, which is why this module returns a record
 * and not a `boolean`. It is the same argument `design/reported.js` makes for `Reported<T>`.
 *
 * THE EXCHANGE ARM CANNOT FIRE FROM `GET /api/dashboard`, AND THAT IS NOT HIDDEN
 * -----------------------------------------------------------------------------
 * `pageFields`' note on the entry records it: `exchange.exchanges[].status` is the literal
 * string `"connected"` in `dashboard_aggregation_service.get_exchange_health` — every entry,
 * every account, from no measurement (the same constant `pages/Dashboard.jsx`'s
 * `VENUE_CONSTANT_NOTE` already discloses beside the venue rows, alongside its fixed
 * `latency_ms: 35`). So on today's server the exchange arm reads a value that can never
 * satisfy it.
 *
 * The arm is implemented anyway, exactly as declared. That constant is a defect in the
 * aggregation service, not in this module, and deleting the arm would mean the strip stays
 * silent on a real disconnection the day the service starts reporting one. What must NOT
 * happen is the arm's non-firing being presented as a finding: this module therefore never
 * emits a positive statement about an arm that did not fire — `summary` is composed from the
 * FIRED arms only, and an arm with no firing subjects has `summary: null`. A caller cannot
 * render "all exchanges connected" from this return value because the phrase is not in it.
 *
 * `armReported` is what a caller uses to say something honest about silence: it counts the
 * readable statuses the arm actually saw, so "no exchange reported a connection state" is
 * expressible and "every exchange is connected" is not.
 *
 * THE VOCABULARIES ARE READ, NOT INVENTED
 * --------------------------------------
 * Three sources, and no fourth spelling introduced here:
 *
 *   1. `design/semantic.js`'s §4.1 status vocabulary, through `statusToken(state).group`.
 *      ONLY `.group` is read — no `fg`, no `wash`, so no colour crosses this boundary and
 *      this module still returns none. Going through that function rather than transcribing
 *      its table means a spelling added there is understood here with no change.
 *   2. `lib/strategyHealth.js`'s `deploymentBindingState`, which is that module's
 *      transcription of `strategy_lifecycle.py::_STATUS_TO_BINDING_STATE`. It is what
 *      supplies "stopped-on-error": `crashed` — stopped BECAUSE it errored — maps to
 *      `FAILED` there, while a plain `stopped` / `cancelled` / `completed` maps to `STOPPED`,
 *      which that module's header calls "a resting state a user asks for on purpose". A
 *      strategy a trader stopped is not an alert condition, and this arm does not raise one.
 *   3. `backend_app`'s own enumerations for the execution arm's two members:
 *      `core/models/execution_record.py::ExecutionStatus.FAILED = "failed"` and
 *      `backend/state_service.py::OrderStatus.REJECTED = "rejected"` (which
 *      `backend/exchange_executor.py` writes as `status="rejected"` on a refused
 *      submission). `semantic.js` already groups both as `error`.
 *
 * @module design/alertCondition
 */

import { statusToken } from './semantic';
import { deploymentBindingState } from '../lib/strategyHealth';

/* ── The three arms ─────────────────────────────────────────────────────────────────── */

/** Any `exchange.exchanges[].status` that is not a connected state. */
export const ARM_EXCHANGE = 'exchange';

/** Any `strategies.items[].status` in an error or stopped-on-error state. */
export const ARM_STRATEGY = 'strategy';

/** Any `executions[].status` that is failed or rejected. */
export const ARM_EXECUTION = 'execution';

/**
 * The three arms, in declaration order — the order `pageFields`' `inputs` lists them in, so
 * a summary reads exchange · strategy · execution however many of them fired.
 */
export const ALERT_ARMS = Object.freeze([ARM_EXCHANGE, ARM_STRATEGY, ARM_EXECUTION]);

/**
 * The `semantic.js` group a venue must be in to NOT fire the exchange arm.
 *
 * The declared condition is "any status **not connected**", and connectedness is a group in
 * §4.1's vocabulary rather than one spelling: `connected`, `paired`, `open` and `ok` all
 * resolve to it. Testing the group rather than `status === 'connected'` is what stops a
 * venue reporting `ok` from being announced as disconnected.
 */
const CONNECTED_GROUP = 'connected';

/**
 * The `semantic.js` group that makes a strategy status an error state.
 *
 * §4.1 puts `error`, `failed`, `disconnected`, `rejected`, `closed`, `blocked`, `critical`
 * and `stale` in it. `deploymentBindingState`'s `FAILED` is unioned with it below, because
 * `_STATUS_TO_BINDING_STATE` knows one spelling §4.1's table does not — `crashed` — and that
 * is precisely the stopped-on-error case.
 */
const ERROR_GROUP = 'error';

/** The binding state that means the deployment stopped because it failed. */
const FAILED_BINDING_STATE = 'FAILED';

/**
 * The execution arm's two members, and only those two.
 *
 * Named rather than taken as `semantic.js`'s whole `error` group, because that group also
 * holds `closed`, `blocked`, `stale` and `disconnected` — none of which is a failed or
 * rejected submission, and the declaration says "failed or rejected". Sources are in the
 * module header.
 */
export const EXECUTION_FAILURE_STATES = Object.freeze(['failed', 'rejected']);

/* ── Total readers ─────────────────────────────────────────────────────────────────── */

/** The one empty list, shared, so an unfired arm does not allocate three arrays. */
const EMPTY = Object.freeze([]);

/** The array itself, or the empty one. A string is iterable and is still not a list. */
const list = (value) => (Array.isArray(value) ? value : EMPTY);

/** A record, or `null`. An array is not a record and neither is a number. */
const record = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;

/** A readable status, lowercased and trimmed, or `null`. A numeric status is not readable. */
const statusOf = (item) => {
  const source = record(item);
  if (source === null) return null;
  const raw = source.status;
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  return trimmed === '' ? null : trimmed.toLowerCase();
};

/**
 * What to call the thing that fired, from the keys each arm's items really carry.
 *
 * `exchange_id` for a venue, `name` then `id` for a strategy, `id` then `order_id` for an
 * execution — the spellings `dashboard_aggregation_service` publishes and the ones
 * `pages/Dashboard.jsx`'s projections read. `null` when the item named itself in none of
 * them: an unnamed subject is counted and left unnamed rather than given an invented label.
 */
const subjectOf = (item) => {
  const source = record(item);
  if (source === null) return null;
  const candidates = [
    source.exchange_id,
    source.name,
    source.id,
    source.order_id,
    source.strategy_id,
    source.symbol,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.trim() !== '') return candidate.trim();
  }
  return null;
};

/* ── The three predicates ──────────────────────────────────────────────────────────── */

/**
 * Whether a venue's reported status fires the exchange arm.
 *
 * @param {string} status A readable, lowercased status.
 * @returns {boolean}
 */
const firesExchange = (status) => statusToken(status).group !== CONNECTED_GROUP;

/**
 * Whether a strategy's reported status fires the strategy arm.
 *
 * The union of §4.1's `error` group and `_STATUS_TO_BINDING_STATE`'s `FAILED`; see the
 * module header on why neither source alone is enough.
 *
 * @param {string} status A readable, lowercased status.
 * @returns {boolean}
 */
const firesStrategy = (status) =>
  statusToken(status).group === ERROR_GROUP
  || deploymentBindingState(status) === FAILED_BINDING_STATE;

/**
 * Whether an execution's reported status fires the execution arm.
 *
 * @param {string} status A readable, lowercased status.
 * @returns {boolean}
 */
const firesExecution = (status) => EXECUTION_FAILURE_STATES.includes(status);

/* ── The sentence each fired arm contributes ───────────────────────────────────────── */

/**
 * The one-line phrase for `n` firing subjects on one arm, or `null` for none.
 *
 * §7.1's mock reads `1 exchange disconnected · 1 strategy stopped on error`. These phrases
 * are that shape with one deliberate change: the exchange arm says "not connected" rather
 * than "disconnected", because "not connected" is what the arm actually tested. A venue
 * reporting `connecting` fires the arm, and calling that state disconnected would be a
 * claim about the link that nothing checked. The states themselves ride on
 * {@link AlertArm.states}, so a caller can name them verbatim.
 */
const PHRASE = Object.freeze({
  [ARM_EXCHANGE]: Object.freeze({ one: 'exchange not connected', many: 'exchanges not connected' }),
  [ARM_STRATEGY]: Object.freeze({ one: 'strategy in an error state', many: 'strategies in an error state' }),
  [ARM_EXECUTION]: Object.freeze({ one: 'execution failed or rejected', many: 'executions failed or rejected' }),
});

/** The separator §7.1 draws between the arms of the summary. */
export const SUMMARY_SEPARATOR = ' · ';

/* ── One arm ───────────────────────────────────────────────────────────────────────── */

/**
 * @typedef {Object} AlertArm
 * @property {string} arm One of {@link ALERT_ARMS}.
 * @property {boolean} fired Whether at least one item satisfied this arm's predicate.
 * @property {number} armReported How many items reported a READABLE status. `0` means this
 *   arm evaluated nothing — not that it found nothing wrong.
 * @property {number} count How many of those satisfied the predicate.
 * @property {ReadonlyArray<string>} states The distinct firing statuses, lowercased,
 *   verbatim otherwise, in first-seen order. Empty when the arm did not fire.
 * @property {ReadonlyArray<string>} subjects The names of the firing items, in payload
 *   order, with unnamed items omitted. May be shorter than `count`.
 * @property {string|null} summary `"2 strategies in an error state"`, or `null` when the arm
 *   did not fire. There is NO phrase for an arm that did not fire, on purpose.
 */

/**
 * Evaluate one arm over one list.
 *
 * @param {string} arm
 * @param {unknown} items
 * @param {(status: string) => boolean} fires
 * @returns {AlertArm}
 */
const evaluateArm = (arm, items, fires) => {
  const states = [];
  const subjects = [];
  let armReported = 0;
  let count = 0;

  for (const item of list(items)) {
    const status = statusOf(item);
    if (status === null) continue;
    armReported += 1;
    if (!fires(status)) continue;
    count += 1;
    if (!states.includes(status)) states.push(status);
    const subject = subjectOf(item);
    if (subject !== null && !subjects.includes(subject)) subjects.push(subject);
  }

  const fired = count > 0;
  const phrase = PHRASE[arm];
  return Object.freeze({
    arm,
    fired,
    armReported,
    count,
    states: fired ? Object.freeze(states) : EMPTY,
    subjects: fired ? Object.freeze(subjects) : EMPTY,
    summary: fired ? `${count} ${count === 1 ? phrase.one : phrase.many}` : null,
  });
};

/* ── The derivation ────────────────────────────────────────────────────────────────── */

/**
 * @typedef {Object} AlertCondition
 * @property {boolean} evaluated Whether ANY of the three arms read at least one status. See
 *   the module header: `false` is the `UNMEASURABLE` case and is not an all-clear.
 * @property {boolean} firing The disjunction. Always `false` when `evaluated` is `false`.
 * @property {ReadonlyArray<AlertArm>} arms All three, always, in {@link ALERT_ARMS} order —
 *   so a caller can report what an arm saw whether or not it fired.
 * @property {ReadonlyArray<string>} firedArms The ids of the arms that fired, in the same
 *   order. Empty when nothing fired.
 * @property {number} reported How many statuses were read across all three arms.
 * @property {string|null} summary The fired arms' phrases joined by {@link
 *   SUMMARY_SEPARATOR} — `"1 exchange not connected · 1 strategy in an error state"` — or
 *   `null` when nothing fired. Composed from fired arms ONLY, so no absence of a condition
 *   is ever stated as a finding.
 */

/**
 * Requirement 3.3's condition, from the three declared field sets and nothing else.
 *
 * Total over every input: a missing key, a `null`, a non-array, records of the wrong shape
 * and statuses of the wrong type all reduce to "this item reported no readable status", and
 * an arm that read nothing simply does not fire.
 *
 * @param {Object} [input]
 * @param {unknown} [input.exchanges] `exchange.exchanges` — the venue list.
 * @param {unknown} [input.strategies] `strategies.items` — the strategy list.
 * @param {unknown} [input.executions] `executions` — the top-level execution list.
 * @returns {AlertCondition}
 */
export function deriveAlertCondition(input) {
  const source = record(input) ?? {};

  const arms = Object.freeze([
    evaluateArm(ARM_EXCHANGE, source.exchanges, firesExchange),
    evaluateArm(ARM_STRATEGY, source.strategies, firesStrategy),
    evaluateArm(ARM_EXECUTION, source.executions, firesExecution),
  ]);

  const fired = arms.filter((entry) => entry.fired);
  const reported = arms.reduce((total, entry) => total + entry.armReported, 0);

  return Object.freeze({
    evaluated: reported > 0,
    firing: fired.length > 0,
    arms,
    firedArms: fired.length === 0
      ? EMPTY
      : Object.freeze(fired.map((entry) => entry.arm)),
    reported,
    summary: fired.length === 0
      ? null
      : fired.map((entry) => entry.summary).join(SUMMARY_SEPARATOR),
  });
}

/**
 * One fired arm's subjects and states as a readable clause, or `null` for an arm that did
 * not fire.
 *
 * `"binance, bybit — disconnected"`. The states are the server's own spellings, so a trader
 * reading the strip sees the word the subsystem used rather than a word this client chose.
 * An arm whose items named none of themselves yields just the states.
 *
 * @param {AlertArm} arm
 * @returns {string|null}
 */
export function armDetail(arm) {
  const entry = record(arm);
  if (entry === null || entry.fired !== true) return null;
  const subjects = list(entry.subjects).join(', ');
  const states = list(entry.states).join(', ');
  if (subjects === '') return states === '' ? null : states;
  return states === '' ? subjects : `${subjects} — ${states}`;
}

export default deriveAlertCondition;
