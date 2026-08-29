/**
 * strategyHealth.js — the Strategies_Page health indicator, computed client-side from the
 * only two records that are allowed to determine it (trading-lifecycle-integration task
 * 16.4, Requirements 1.8 and 1.9).
 *
 * WHAT THIS MODULE IS FOR
 * =======================
 * Requirement 1.8 says the health indicator is computed "exclusively from that strategy's
 * most recent Deployment status and/or most recent Backtest_Result outcome, using no other
 * data source and no fabricated or default value". Requirement 1.9 says a strategy with
 * neither record reads `undetermined` — "rather than a default, zero-valued, or fabricated
 * state".
 *
 * That is a purity claim, so it lives in a module of its own rather than inline in
 * `Strategies.jsx`: it takes one strategy row, reads exactly two fields off it, returns a
 * string, touches nothing else, and can be tested without rendering a page.
 *
 * WHAT IT REPLACED, AND WHY THAT WAS A DEFECT
 * ===========================================
 * `Strategies.jsx`'s normaliser carried `health: row.health ?? "healthy"`. `GET
 * /api/strategies` reports no `health` field at all (`routers/strategies.py`'s
 * `_LIST_COLUMNS` is a fixed projection that does not contain one), so that expression
 * resolved to the literal `"healthy"` for **every** strategy — including one whose only
 * deployment had failed. A hardcoded "healthy" for a failed deployment is precisely the
 * fabricated state Requirement 1.8 forbids, and it also silently disabled the card's own
 * `isFailed` branch, which tests `health === "error"`.
 *
 * `undetermined` IS THE HONEST ANSWER TODAY, NOT A BUG IN THIS MODULE
 * ==================================================================
 * `design.md` describes `most_recent_deployment.status` and `most_recent_backtest.outcome`
 * as "fields the list endpoint already returns". They are not returned yet — the list
 * projection named above carries neither — so until the listing is widened, every strategy
 * resolves to `undetermined` here. That is the required behaviour, not a gap being papered
 * over: Requirement 1.9's whole point is that "we have no deployment record and no backtest
 * record for this strategy" must read as *unknown*. Substituting a cheerful default, or
 * reaching for a third source such as `strategies.status` to fill the silence, is the thing
 * both criteria prohibit. When the endpoint starts reporting the two records, this module
 * starts reporting real verdicts with no change to it.
 *
 * THE TWO SOURCES, AND WHICH ONE WINS
 * ===================================
 * The deployment record is consulted first: it describes what the strategy is doing *now*,
 * whereas a backtest outcome is historical evidence about a version. Two of the five binding
 * states are deliberately **inconclusive** rather than mapped to a verdict:
 *
 *   * `DEPLOYING` — bound, not yet confirmed running. Nothing is known to be wrong and
 *     nothing is confirmed working.
 *   * `STOPPED` — a resting state a user asks for on purpose. "Stopped" is not a health
 *     verdict, and the card already displays the binding state itself next to the
 *     indicator, so nothing is lost by declining to restate it as health.
 *
 * When the deployment source is inconclusive or absent, the backtest outcome is consulted;
 * a backtest still `running` is likewise inconclusive, because it has not produced an
 * outcome yet. If both sources come back inconclusive, the answer is `undetermined` —
 * reached by the same route as "no records at all", because in both cases the two permitted
 * sources genuinely do not determine a value.
 *
 * `PAUSED` is the one middle verdict (`degraded`): the deployment exists and is not
 * executing. That is a real, reportable condition without claiming a fault.
 *
 * VOCABULARY SPELLINGS ARE THE BACKEND'S, NOT A NEW SET
 * ====================================================
 * `strategy_deployments.status` is a lowercase legacy string with no `CHECK` constraint, and
 * `strategy_lifecycle.py` already owns the map from every spelling this codebase writes onto
 * the five binding states (`_STATUS_TO_BINDING_STATE`). `DEPLOYMENT_STATUS_TO_BINDING_STATE`
 * below is a transcription of that map, plus the five canonical uppercase names for a caller
 * that has already normalised. It is not a second opinion: if a spelling is added there, it
 * is added here, and anything unrecognised is reported as *unknown* rather than guessed —
 * a status we cannot read is not evidence of health.
 *
 * `strategy_backtests.status` is `running | completed | failed` (migration
 * `001_strategy_architecture.sql`, written by `BacktestService.create_backtest`,
 * `update_backtest_results` and `BacktestRuntime`'s failure path). `outcome` is the name
 * `design.md` uses for that same field on the same record, so both spellings are read off
 * the one record — which is one source, not two.
 */

// ── The reported vocabulary ─────────────────────────────────────────────────────────────

/** Deployment running, or a backtest that completed: nothing reports a problem. */
export const HEALTH_HEALTHY = 'healthy';

/** A deployment exists and is not executing (paused). */
export const HEALTH_DEGRADED = 'degraded';

/** A deployment failed, or a backtest failed. */
export const HEALTH_ERROR = 'error';

/**
 * Neither permitted source determines a value — no records at all (Requirement 1.9), or
 * only inconclusive ones (deploying/stopped deployment, in-flight backtest, unreadable
 * status spelling).
 */
export const HEALTH_UNDETERMINED = 'undetermined';

/** Every value {@link computeStrategyHealth} can return. Nothing else is ever reported. */
export const STRATEGY_HEALTH_INDICATORS = Object.freeze([
  HEALTH_HEALTHY,
  HEALTH_DEGRADED,
  HEALTH_ERROR,
  HEALTH_UNDETERMINED,
]);

/** Which of the two permitted records decided the indicator, for display and for tests. */
export const HEALTH_SOURCE_DEPLOYMENT = 'deployment';
export const HEALTH_SOURCE_BACKTEST = 'backtest';

// ── Source vocabularies (transcribed, not invented) ─────────────────────────────────────

const BINDING_DEPLOYING = 'DEPLOYING';
const BINDING_RUNNING = 'RUNNING';
const BINDING_PAUSED = 'PAUSED';
const BINDING_STOPPED = 'STOPPED';
const BINDING_FAILED = 'FAILED';

/**
 * `strategy_lifecycle.py::_STATUS_TO_BINDING_STATE`, transcribed, plus the five canonical
 * uppercase names. Keys are compared lowercased.
 */
const DEPLOYMENT_STATUS_TO_BINDING_STATE = Object.freeze({
  deploying: BINDING_DEPLOYING,
  deployed: BINDING_DEPLOYING,
  pending: BINDING_DEPLOYING,
  queued: BINDING_DEPLOYING,
  starting: BINDING_DEPLOYING,
  restarting: BINDING_DEPLOYING,
  running: BINDING_RUNNING,
  active: BINDING_RUNNING,
  paused: BINDING_PAUSED,
  stopping: BINDING_STOPPED,
  stopped: BINDING_STOPPED,
  cancelled: BINDING_STOPPED,
  canceled: BINDING_STOPPED,
  completed: BINDING_STOPPED,
  failed: BINDING_FAILED,
  error: BINDING_FAILED,
  crashed: BINDING_FAILED,
});

const BACKTEST_COMPLETED = 'completed';
const BACKTEST_FAILED = 'failed';
const BACKTEST_RUNNING = 'running';

/** `strategy_backtests.status`'s three values, compared lowercased. */
const BACKTEST_OUTCOME_STATES = Object.freeze({
  completed: BACKTEST_COMPLETED,
  failed: BACKTEST_FAILED,
  running: BACKTEST_RUNNING,
});

// ── The two mappings onto the reported vocabulary ───────────────────────────────────────

/** `null` marks a state that is deliberately inconclusive; see the module header. */
const DEPLOYMENT_STATE_HEALTH = Object.freeze({
  [BINDING_FAILED]: HEALTH_ERROR,
  [BINDING_PAUSED]: HEALTH_DEGRADED,
  [BINDING_RUNNING]: HEALTH_HEALTHY,
  [BINDING_DEPLOYING]: null,
  [BINDING_STOPPED]: null,
});

const BACKTEST_OUTCOME_HEALTH = Object.freeze({
  [BACKTEST_FAILED]: HEALTH_ERROR,
  [BACKTEST_COMPLETED]: HEALTH_HEALTHY,
  [BACKTEST_RUNNING]: null,
});

// ── Reading the two fields ──────────────────────────────────────────────────────────────

/** A usable status label, or `null`. Only strings count: a numeric status is not readable. */
const label = (value) => {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
};

/** The record itself, or `null`. An array is not a record. */
const record = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;

/**
 * The most recent deployment's reported status, verbatim, or `null` when there is no
 * deployment record. `state` is accepted as the same field's other spelling.
 */
export function deploymentStatusOf(strategy) {
  const deployment = record(record(strategy)?.most_recent_deployment);
  if (!deployment) return null;
  return label(deployment.status) ?? label(deployment.state);
}

/**
 * The most recent backtest's reported outcome, verbatim, or `null` when there is no
 * backtest record. `status` is the column's name and `outcome` is `design.md`'s name for
 * it; both are read off the one record.
 */
export function backtestOutcomeOf(strategy) {
  const backtest = record(record(strategy)?.most_recent_backtest);
  if (!backtest) return null;
  return label(backtest.outcome) ?? label(backtest.status);
}

/** A deployment status spelling → one of the five binding states, or `null` if unreadable. */
export function deploymentBindingState(status) {
  const text = label(status);
  if (!text) return null;
  return DEPLOYMENT_STATUS_TO_BINDING_STATE[text.toLowerCase()] ?? null;
}

/** A backtest outcome spelling → one of its three states, or `null` if unreadable. */
export function backtestOutcomeState(outcome) {
  const text = label(outcome);
  if (!text) return null;
  return BACKTEST_OUTCOME_STATES[text.toLowerCase()] ?? null;
}

// ── The indicator ───────────────────────────────────────────────────────────────────────

/**
 * The health indicator and the working it was reached by.
 *
 * Pure: it reads `most_recent_deployment` and `most_recent_backtest` off the row, mutates
 * nothing, and consults no module state, no clock and no network.
 *
 * @param {object|null|undefined} strategy A strategy row as the list endpoint reports it.
 * @returns {{indicator: string, source: string|null, deploymentStatus: string|null,
 *   backtestOutcome: string|null, deploymentState: string|null, backtestState: string|null}}
 *   `source` is `null` exactly when the indicator is `undetermined`.
 */
export function explainStrategyHealth(strategy) {
  const deploymentStatus = deploymentStatusOf(strategy);
  const backtestOutcome = backtestOutcomeOf(strategy);
  const deploymentState = deploymentBindingState(deploymentStatus);
  const backtestState = backtestOutcomeState(backtestOutcome);

  const fromDeployment = deploymentState ? DEPLOYMENT_STATE_HEALTH[deploymentState] : null;
  const fromBacktest = backtestState ? BACKTEST_OUTCOME_HEALTH[backtestState] : null;

  // The deployment record decides when it decides anything; the backtest is consulted only
  // where it does not. Neither deciding is `undetermined` with no source — Requirement 1.9.
  const indicator = fromDeployment ?? fromBacktest ?? HEALTH_UNDETERMINED;
  let source = null;
  if (fromDeployment) source = HEALTH_SOURCE_DEPLOYMENT;
  else if (fromBacktest) source = HEALTH_SOURCE_BACKTEST;

  return {
    indicator,
    source,
    deploymentStatus,
    backtestOutcome,
    deploymentState,
    backtestState,
  };
}

/**
 * One strategy's health indicator: one of {@link STRATEGY_HEALTH_INDICATORS}, always a
 * non-empty string, `undetermined` whenever the two permitted sources do not determine one.
 *
 * @param {object|null|undefined} strategy A strategy row as the list endpoint reports it.
 * @returns {string}
 */
export function computeStrategyHealth(strategy) {
  return explainStrategyHealth(strategy).indicator;
}
