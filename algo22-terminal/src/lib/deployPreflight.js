/**
 * deployPreflight.js — the deployment configuration workflow's reading of the
 * Deployment_Gate (trading-lifecycle-integration tasks 16.1 and 18.1).
 *
 * Requirements 2.2, 11.5, 13.3, 13.4, 13.5, 13.6.
 *
 * WHAT THIS MODULE IS FOR
 * =======================
 * Two surfaces have to describe the same deployment, and they must not be able to
 * disagree about it:
 *
 * * `POST /api/strategy-operations/strategies/{id}/versions/{version}/deploy` — the
 *   write path. It runs `evaluate_binding` (fail-fast) and records the
 *   Deployment_Binding: version, exchange account, risk configuration, execution
 *   configuration, mode (Requirement 13.1). This is the endpoint Requirement 11.5 names,
 *   replacing the legacy `POST /api/strategies/{id}/deploy`, which travels no gate and
 *   binds nothing.
 * * `GET .../versions/{version}/deploy/preflight` — the read-only half. It runs the same
 *   gates through `evaluate_binding_summary` (collect-all) and reports every condition
 *   with its own verdict, so the workflow can show the author all of the failures at once
 *   instead of the first one (Requirement 13.2/13.3).
 *
 * Both are built here, from one description of the deployment, which is why
 * {@link deploymentRequest} returns the POST body and the GET query together. A preflight
 * that checked a different account, mode or execution config than the deploy submits
 * would report `deployable` about a deployment that is not the one about to happen — the
 * one failure mode this surface must not have.
 *
 * THE BODY SHAPE IS THE SERVER'S, AND IT FORBIDS EXTRA FIELDS
 * ==========================================================
 * `strategy_operations.DeploymentBindingRequest` declares `model_config =
 * ConfigDict(extra="forbid")` and exactly five fields: `mode`, `exchange_account_id`,
 * `risk_config_id`, `execution_config`, `gap_strategy`. `execution_config` is narrowed
 * again by `deployment_binding.normalise_execution_config` to
 * {@link EXECUTION_CONFIG_FIELDS} — an unknown key there is refused rather than dropped,
 * because a limit that is quietly discarded is one the author believes is in force. So
 * this module sends those fields and nothing else; `environment` rides the query string
 * (the request model has no field for it, and `mode` is the constrained one).
 *
 * **No credential and no exchange identity is assembled here.** An account is named by
 * id; its keys are resolved inside the execution process (Requirement 12.5), and the
 * venue is read off the account row server-side.
 *
 * THE STATUS VOCABULARY IS THE RESPONSE'S, LOWERCASE
 * =================================================
 * `deployment_binding.CONDITION_PASSED/FAILED/PENDING` are `"passed"`, `"failed"` and
 * `"pending"`, and `BindingCondition.to_dict()` omits absent optional keys rather than
 * sending nulls. `pending` is not a weaker `passed`: it means the condition could not be
 * evaluated because a condition it depends on failed, so it keeps the Deploy button
 * disabled exactly as `failed` does (Requirement 13.4).
 *
 * WHY `deployable` IS RE-DERIVED HERE
 * ===================================
 * The server computes it as "there is at least one condition and every one passed"
 * (`BindingSummary.deployable`). {@link isDeployable} recomputes the same rule from the
 * conditions rather than trusting the flag alone, so a body that arrives malformed,
 * truncated, or with a `deployable: true` that its own condition list contradicts cannot
 * enable the Deploy button. "Nothing was checked" is never deployable.
 */

// ── The wire vocabulary ─────────────────────────────────────────────────────────────────

/** `deployment_binding.CONDITION_PASSED`. */
export const CONDITION_PASSED = 'passed';
/** `deployment_binding.CONDITION_FAILED`. */
export const CONDITION_FAILED = 'failed';
/** `deployment_binding.CONDITION_PENDING` — could not be checked, not "checked and fine". */
export const CONDITION_PENDING = 'pending';

/** The three statuses Requirement 13.3 names, and the only ones a condition may report. */
export const CONDITION_STATUSES = Object.freeze([
  CONDITION_PASSED,
  CONDITION_FAILED,
  CONDITION_PENDING,
]);

/**
 * The condition names the preflight reports, in the order the server evaluates them:
 * `strategy_service.DEPLOY_PREREQUISITE_CONDITION` first, then
 * `deployment_binding.BINDING_CONDITIONS`.
 *
 * Carried for display ordering and for nothing else — the response's own order is used as
 * given, and a name absent from this list is still rendered. The server owns which
 * conditions exist; a client-side allow-list would silently hide a new mandatory
 * condition, which is the opposite of what Requirement 13.3 asks for.
 */
export const PREFLIGHT_CONDITION_ORDER = Object.freeze([
  'deploy_prerequisites',
  'version_ready',
  'plan_readable',
  'market_resolved',
  'deployment_mode',
  'execution_config',
  'gap_policy',
  'binding_storable',
  'models_verified',
  'exchange_account',
  'risk_config',
  'symbol_available',
  'timeframe_supported',
]);

/** `deployment_binding.BINDING_MODES` — `chk_sd_mode`'s two values. */
export const MODE_PAPER = 'paper';
export const MODE_LIVE = 'live';

/**
 * `deployment_binding.EXECUTION_CONFIG_FIELDS`, verbatim. A key outside this set is
 * refused by the server rather than stored, so nothing else may be assembled into an
 * `execution_config`.
 */
export const EXECUTION_CONFIG_FIELDS = Object.freeze([
  'max_order_notional',
  'max_open_positions',
  'slippage_tolerance_bps',
  'order_timeout_seconds',
  'retry_policy',
]);

/**
 * Requirement 13.6: the summary is re-read while the modal is open so a condition that
 * has just started failing disables the Deploy button again without a reload. Two seconds
 * is `design.md`'s figure for this panel, and the endpoint's own 200/minute limit is sized
 * for it (30 requests a minute).
 */
export const PREFLIGHT_POLL_INTERVAL_MS = 2000;

// ── Small readers ───────────────────────────────────────────────────────────────────────

const text = (value) => {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
};

const record = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;

const identifier = (value) => {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return text(value);
};

const positiveNumber = (value) => {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
};

// ── Reading the preflight response ──────────────────────────────────────────────────────

/**
 * One condition as `BindingCondition.to_dict()` emits it.
 *
 * Every optional key reads `null` when absent — nothing is defaulted into existence, and
 * an unrecognised `status` is reported as `null` rather than coerced to a known one, so a
 * condition this client cannot read can never count as passed.
 *
 * @param {unknown} raw
 * @returns {{name: string|null, status: string|null, detail: Object|null,
 *   code: string|null, message: string|null, reason: string|null}}
 */
export function normalizeCondition(raw) {
  const row = record(raw) ?? {};
  const status = text(row.status);
  return {
    name: text(row.name),
    status: status !== null && CONDITION_STATUSES.includes(status) ? status : null,
    detail: record(row.detail),
    code: text(row.code),
    message: text(row.message),
    reason: text(row.reason),
  };
}

/**
 * One preflight response, read into the shape the workflow renders and gates on.
 *
 * @param {unknown} response The body of `GET .../deploy/preflight`.
 * @returns {{deployable: boolean, reported: boolean|null, conditions: Array<Object>,
 *   failed: Array<Object>, pending: Array<Object>}}
 *   `reported` is the server's own flag, kept for comparison; `deployable` is the
 *   re-derived verdict the button uses.
 */
export function normalizePreflight(response) {
  const body = record(response) ?? {};
  const conditions = (Array.isArray(body.conditions) ? body.conditions : []).map(
    normalizeCondition,
  );
  return {
    deployable: isDeployable(conditions),
    reported: typeof body.deployable === 'boolean' ? body.deployable : null,
    conditions,
    failed: conditions.filter((c) => c.status === CONDITION_FAILED),
    // Everything that is neither passed nor failed: `pending` from the server, and any
    // status this client could not read. Both mean "not established", which is why they
    // are counted together and why neither enables the Deploy button.
    pending: conditions.filter(
      (c) => c.status !== CONDITION_FAILED && c.status !== CONDITION_PASSED,
    ),
  };
}

/**
 * `BindingSummary.deployable`, recomputed: at least one condition, and every one passed.
 *
 * Requirements 13.4 and 13.5 in one expression — the Deploy button is enabled exactly
 * when this is true. An empty list is false: "nothing was checked" must never enable a
 * deployment.
 *
 * @param {Array<Object>|Object} conditionsOrSummary Conditions, or a normalized summary.
 * @returns {boolean}
 */
export function isDeployable(conditionsOrSummary) {
  const conditions = Array.isArray(conditionsOrSummary)
    ? conditionsOrSummary
    : record(conditionsOrSummary)?.conditions;
  if (!Array.isArray(conditions) || conditions.length === 0) return false;
  return conditions.every((c) => normalizeCondition(c).status === CONDITION_PASSED);
}

/**
 * A condition as one line of display text: what was checked, and what came of it.
 *
 * The failure wording is the server's own (`DeployRejected.message`), so the workflow and
 * the write path name a refusal identically; a `pending` condition says which condition
 * blocked it. Nothing is paraphrased into a friendlier claim than the gate made.
 *
 * @param {Object} condition A {@link normalizeCondition} result, or a raw condition.
 * @returns {string}
 */
export function describeCondition(condition) {
  const c = normalizeCondition(condition);
  const name = c.name ?? 'unnamed condition';
  const verdict = c.status ?? 'unreadable';
  const why = c.message ?? c.reason ?? null;
  return why ? `${name} — ${verdict}: ${why}` : `${name} — ${verdict}`;
}

/** Used only when a failure arrived carrying no readable message at all. */
export const PREFLIGHT_UNAVAILABLE_MESSAGE =
  'The deployment checks could not be read, so this version cannot be deployed from here ' +
  'yet. Nothing was deployed.';

/**
 * A preflight that could not be answered at all, classified for display.
 *
 * A 404 is deliberately reported with a message that says the version could not be
 * loaded, because that is the whole of what the server disclosed: `preflight_deploy_version`
 * answers `VERSION_NOT_FOUND` both for a version that does not exist and for one that
 * belongs to another user (Requirement 20.2). Nothing here may add a distinction the
 * backend refused to make.
 *
 * @param {unknown} error Whatever `endpoints.strategies.deployPreflight` rejected with.
 * @returns {{status: number|null, code: string|null, message: string}}
 */
export function describePreflightFailure(error) {
  const body = record(error?.data) ?? record(error?.response?.data) ?? {};
  const detail = record(body.detail) ?? {};
  const status =
    (Number.isInteger(error?.status) ? error.status : null) ??
    (Number.isInteger(error?.response?.status) ? error.response.status : null) ??
    (Number.isInteger(body.status_code) ? body.status_code : null);
  const code = text(body.error) ?? text(detail.error) ?? null;
  const message =
    text(body.message) ??
    text(detail.message) ??
    text(typeof body.detail === 'string' ? body.detail : null) ??
    text(error?.message) ??
    PREFLIGHT_UNAVAILABLE_MESSAGE;

  return { status, code, message };
}

// ── Describing the deployment both surfaces are about ───────────────────────────────────

/**
 * The mode `chk_sd_mode` constrains, read off the workflow's own execution-mode selector.
 *
 * Anything other than the two known values is passed through unchanged rather than
 * defaulted to `paper`: `deployment_binding.normalise_mode` owns that vocabulary, and a
 * client that silently rewrote an unrecognised mode to the safe-looking one would deploy
 * something the author did not ask for. An unrecognised mode is refused there, and the
 * refusal is reported as the `deployment_mode` condition.
 *
 * @param {string} [environment] `'paper'` or `'live'`.
 * @returns {string|null}
 */
export function deploymentModeOf(environment) {
  return text(environment)?.toLowerCase() ?? null;
}

/**
 * The `execution_config` document, assembled from the sizing the workflow collects.
 *
 * Only `max_order_notional` is derivable from what this modal asks for: the capital
 * allocated and the per-trade size percentage bound the notional a single order may
 * carry, which is exactly what that field limits. It is sent rather than dropped because
 * dropping it would leave the author's sizing configuration (Requirement 12.4) in force
 * nowhere, and included only when both inputs are positive numbers — a limit is not
 * invented from a blank or nonsensical field.
 *
 * The workflow's `maxDrawdown` and `stopLoss` inputs have no home in a
 * Deployment_Binding: those are risk limits, and the binding references them by
 * `risk_config_id` (a `risk_settings` row) rather than carrying inline values. They are
 * deliberately not smuggled into `execution_config`, which would be refused
 * (`EXECUTION_CONFIG_UNKNOWN_FIELD`) and rightly so.
 *
 * @param {{capital?: (string|number), tradeSizePct?: (string|number)}} [sizing]
 * @returns {Object} Possibly empty; never carries a field outside
 *   {@link EXECUTION_CONFIG_FIELDS}.
 */
export function executionConfigFrom(sizing = {}) {
  const capital = positiveNumber(sizing.capital);
  const tradeSizePct = positiveNumber(sizing.tradeSizePct);
  if (capital === null || tradeSizePct === null) return {};
  return { max_order_notional: (capital * tradeSizePct) / 100 };
}

/**
 * One deployment, described once, as both surfaces need it.
 *
 * @param {Object} [config] The workflow's own state.
 * @param {string} [config.environment] The execution-mode selection (`paper`/`live`).
 * @param {Object} [config.account] The selected exchange account row, or null for paper.
 * @param {string} [config.riskConfigId] A `risk_settings` id, when the workflow selects one.
 * @param {string|number} [config.capital]
 * @param {string|number} [config.tradeSizePct]
 * @param {string} [config.gapStrategy]
 * @returns {{mode: string|null, environment: string|null, body: Object, query: Object}}
 *   `body` is the `DeploymentBindingRequest` for the POST; `query` is the same deployment
 *   as the preflight GET's parameters. Absent values are omitted from both rather than
 *   sent as nulls, so the server's own defaults apply.
 */
export function deploymentRequest(config = {}) {
  const mode = deploymentModeOf(config.environment);
  // Paper binds no account: `assert_account_required_for_live` requires one only for live,
  // and sending one for paper would bind a deployment to an account it does not use.
  const exchangeAccountId =
    mode === MODE_LIVE ? identifier(record(config.account)?.id ?? config.exchangeAccountId) : null;
  const riskConfigId = identifier(config.riskConfigId);
  const gapStrategy = text(config.gapStrategy);
  const executionConfig = executionConfigFrom(config);

  const body = {};
  if (mode !== null) body.mode = mode;
  if (exchangeAccountId !== null) body.exchange_account_id = exchangeAccountId;
  if (riskConfigId !== null) body.risk_config_id = riskConfigId;
  if (gapStrategy !== null) body.gap_strategy = gapStrategy;
  if (Object.keys(executionConfig).length > 0) body.execution_config = executionConfig;

  return {
    mode,
    environment: mode,
    body,
    // The legacy `environment` column travels alongside `mode` (the POST takes it as a
    // query parameter; the preflight reads it as the fallback for an unstated mode), so
    // the row's legacy column cannot say `paper` about a live binding.
    query: mode !== null ? { ...body, environment: mode } : { ...body },
  };
}

/**
 * A stable string for one preflight query, for use as a change key.
 *
 * The poll has to restart when the deployment being checked changes — a different
 * account, a different mode, a different notional is a different question, and the
 * previous answer must not survive it (Requirement 13.6 in reverse: a passed summary about
 * account A may not enable a Deploy of account B).
 *
 * @param {Object} [query]
 * @returns {string}
 */
export function preflightQueryKey(query = {}) {
  const source = record(query) ?? {};
  const keys = Object.keys(source).sort();
  return JSON.stringify(keys.map((key) => [key, source[key]]));
}
