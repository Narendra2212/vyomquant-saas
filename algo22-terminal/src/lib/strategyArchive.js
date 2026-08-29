/**
 * strategyArchive.js — the Strategies_Page's reading of the archive endpoint
 * (trading-lifecycle-integration task 16.3, Requirements 2.9, 2.10 and 3.1).
 *
 * WHAT THIS MODULE IS FOR
 * =======================
 * `DELETE /api/strategies/{id}` is the same path and method the page has always called,
 * but task 5.1 rewired what it *does*: it no longer deletes a row, it sets
 * `strategies.archived_at`. Two consequences land on the page, and both are string- and
 * shape-handling with no rendering in them, so they live here rather than inline in
 * `Strategies.jsx`:
 *
 * * **The confirmation has to describe archival, not deletion.** Requirement 2.8 wants the
 *   confirmation to name "the selected action and its consequence", and the consequence
 *   changed: the strategy leaves the list, its versions, backtests, deployments and
 *   signals stay (Requirements 3.2, 3.5). A dialog still saying "delete this strategy"
 *   would describe an operation the backend stopped performing.
 * * **A 409 has to be rendered as the specific refusal it is.** Requirement 3.1 makes the
 *   backend identify "each blocking Deployment by its identifier and current state", and
 *   Requirement 2.10 makes the page say that those deployments must be stopped first.
 *   That information is already in the response; the page's job is not to lose it.
 *
 * THE SHAPE READ HERE IS THE ONE ON THE WIRE, NOT AN ASSUMED ONE
 * =============================================================
 * `strategy_archive.archive_strategy` raises
 * `ArchiveRejected("STRATEGY_HAS_ACTIVE_DEPLOYMENTS", …, {strategy_id,
 * blocking_deployments, blocking_states})`. `routers/strategies.py`'s delete handler maps
 * it to `HTTPException(status_code=409, detail=e.to_detail())`, where `to_detail()` is
 * `{error, message, **details}`. `main.py`'s global `HTTPException` handler then normalises
 * every raise into `core/schemas.create_api_error_response`, which lifts `error` and
 * `message` to the top level, echoes the raw dict under `detail`, and collects the
 * remaining keys under `details`. So the body is:
 *
 * ```json
 * {
 *   "error": "STRATEGY_HAS_ACTIVE_DEPLOYMENTS",
 *   "message": "This strategy has 2 active deployment(s), so it cannot be archived. …",
 *   "detail":  { "error": …, "message": …, "strategy_id": …, "blocking_deployments": [ … ] },
 *   "details": { "strategy_id": …, "blocking_deployments": [ … ], "blocking_states": [ … ] },
 *   "status_code": 409, "timestamp": …, "path": …, "solution": null
 * }
 * ```
 *
 * `apiClient`'s response interceptor hands that body to `ApiError` as `.data` and its
 * `message` as `.message`. All three of `data.details`, `data.detail` and the top level are
 * consulted below for `blocking_deployments`, because the same refusal reaches this code
 * through the normaliser today and could reach it as a bare `to_detail()` body from any
 * caller that bypasses the global handler; reading all three is cheaper than depending on
 * which.
 *
 * Each entry is `strategy_lifecycle.binding_report`, whose field names are transcribed
 * verbatim below — `deployment_id`, `binding_state`, `status_raw`, `mode`, `version`,
 * `started_at`, plus the `source` the archive gate attaches. Nothing is renamed and nothing
 * is defaulted into existence: a field the response does not carry reads `null`, and an
 * unreadable state is reported as unreadable rather than guessed.
 *
 * `deployment_id: null` IS NOT A MISSING FIELD
 * ============================================
 * `strategy_archive.load_blocking_deployments` reports one blocking entry that has no
 * `strategy_deployments` row behind it: the legacy deploy path (`routers/strategies.py`'s
 * `deploy_bot`) records a live bot by setting `strategies.status = 'running'` without
 * writing a deployment row, and archiving such a strategy would hide one that is still
 * trading. It marks that entry `deployment_id: None`, `source: "strategies.status"`. It is
 * named in the rendered list as the strategy's own running flag, because telling a user to
 * "stop deployment null" is not actionable.
 */

// ── The codes the archive endpoint answers with ─────────────────────────────────────────

/** Requirement 3.1: a deployment in DEPLOYING, RUNNING or PAUSED blocks archival. 409. */
export const ARCHIVE_BLOCKED_CODE = 'STRATEGY_HAS_ACTIVE_DEPLOYMENTS';

/** Migration `005a_strategy_archive.sql` is not applied, so nothing can be archived. 503. */
export const ARCHIVE_UNAVAILABLE_CODE = 'STRATEGY_ARCHIVE_UNAVAILABLE';

/** The deployments read failed, so "no deployment is live" could not be established. 503. */
export const ARCHIVE_STATE_UNREADABLE_CODE = 'STRATEGY_DEPLOYMENT_STATE_UNREADABLE';

/** `{"status": …}` on a 200. `already_archived` is the idempotent reply (Requirement 3.6). */
export const STATUS_ARCHIVED = 'archived';
export const STATUS_ALREADY_ARCHIVED = 'already_archived';

/**
 * Used only when a 409 carries no readable `message`. The server's own wording is preferred
 * in every other case; this exists so Requirement 2.10's "active deployments must be
 * stopped first" is stated even by a refusal that arrived without prose.
 */
export const ARCHIVE_BLOCKED_FALLBACK_MESSAGE =
  'This strategy still has one or more active deployments, so it was not archived. ' +
  'Stop every deployment listed below first. Nothing was changed.';

/** Used only when a failure carries no readable message at all. */
export const ARCHIVE_FAILED_FALLBACK_MESSAGE =
  'This strategy could not be archived. Nothing was changed.';

/** How an entry with no `strategy_deployments` row behind it is named. See the header. */
export const OWN_STATUS_SOURCE = 'strategies.status';
export const OWN_STATUS_LABEL = "this strategy's own running flag";

// ── Reading the response ────────────────────────────────────────────────────────────────

/** The value as a non-empty trimmed string, or `null`. Only strings count. */
const text = (value) => {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
};

/** A plain record, or `null`. An array is not a record. */
const record = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;

const list = (value) => (Array.isArray(value) ? value : null);

const integer = (value) => (Number.isInteger(value) ? value : null);

/**
 * An identifier as a string. Numeric ids are accepted (the page's own normaliser falls back
 * to an index for a row with no id), `null`/`undefined` are not coerced to `"null"`.
 */
const identifier = (value) => {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return text(value);
};

/** The error body `apiClient` attached, wherever it ended up. */
const bodyOf = (error) => record(error?.data) ?? record(error?.response?.data) ?? {};

/** The HTTP status, from `ApiError.status` or a raw axios error. */
export function archiveFailureStatus(error) {
  const body = bodyOf(error);
  return (
    integer(error?.status) ??
    integer(error?.response?.status) ??
    integer(body.status_code) ??
    null
  );
}

/**
 * One blocking deployment as `strategy_lifecycle.binding_report` reports it, with the
 * fields this page renders read off verbatim.
 *
 * @param {object} report
 * @returns {{deploymentId: string|null, state: string|null, statusRaw: string|null,
 *   mode: string|null, version: string|null, startedAt: string|null, source: string|null,
 *   isOwnStatus: boolean}}
 */
export function normalizeBlockingDeployment(report) {
  const row = record(report) ?? {};
  const source = text(row.source);
  const deploymentId = identifier(row.deployment_id);
  return {
    deploymentId,
    state: text(row.binding_state),
    statusRaw: text(row.status_raw),
    mode: text(row.mode),
    version: identifier(row.version),
    startedAt: text(row.started_at),
    source,
    // The legacy path's pseudo-deployment: no row, so no identifier to stop by.
    isOwnStatus: deploymentId === null || source === OWN_STATUS_SOURCE,
  };
}

/**
 * Every blocking deployment named by a refusal, in the order the server listed them.
 *
 * Empty for any error that is not a blocked archive — including a 409 that somehow carries
 * no list, which is reported as a refusal with no enumerable blockers rather than as an
 * invented one.
 *
 * @param {unknown} error
 * @returns {Array<ReturnType<typeof normalizeBlockingDeployment>>}
 */
export function blockingDeploymentsFrom(error) {
  const body = bodyOf(error);
  const raw =
    list(record(body.details)?.blocking_deployments) ??
    list(record(body.detail)?.blocking_deployments) ??
    list(body.blocking_deployments) ??
    [];
  return raw.map(normalizeBlockingDeployment);
}

/**
 * One blocking deployment as a single line of display text: which deployment, and what
 * state it is in — the two things Requirement 3.1 makes the server identify.
 *
 * @param {object} deployment A {@link normalizeBlockingDeployment} result, or a raw report.
 * @returns {string}
 */
export function describeBlockingDeployment(deployment) {
  const d =
    deployment && Object.prototype.hasOwnProperty.call(deployment, 'deploymentId')
      ? deployment
      : normalizeBlockingDeployment(deployment);

  const who = d.isOwnStatus
    ? OWN_STATUS_LABEL
    : `deployment ${d.deploymentId}`;
  // The canonical state is what the user acts on; the raw column value is the fallback so
  // an unrecognised spelling stays visible instead of being reported as a known state.
  const state = d.state ?? d.statusRaw ?? 'state unreadable';
  const qualifiers = [d.mode, d.version ? `v${d.version}` : null].filter(Boolean);
  const suffix = qualifiers.length ? ` (${qualifiers.join(', ')})` : '';
  return `${who} — ${state}${suffix}`;
}

/**
 * One archive failure, classified for display.
 *
 * @param {unknown} error Whatever `endpoints.strategies.delete` rejected with.
 * @returns {{status: number|null, code: string|null, message: string,
 *   blockingDeployments: Array<object>, blocked: boolean}}
 *   `blocked` is the Requirement 3.1 refusal specifically — the only case with deployments
 *   to name and the only one a user resolves by stopping something.
 */
export function describeArchiveFailure(error) {
  const body = bodyOf(error);
  const detail = record(body.detail) ?? {};
  const status = archiveFailureStatus(error);
  const code = text(body.error) ?? text(detail.error) ?? null;
  const blockingDeployments = blockingDeploymentsFrom(error);
  const blocked =
    code === ARCHIVE_BLOCKED_CODE || (status === 409 && blockingDeployments.length > 0);

  const message =
    text(body.message) ??
    text(detail.message) ??
    text(typeof body.detail === 'string' ? body.detail : null) ??
    text(error?.message) ??
    (blocked ? ARCHIVE_BLOCKED_FALLBACK_MESSAGE : ARCHIVE_FAILED_FALLBACK_MESSAGE);

  return { status, code, message, blockingDeployments, blocked };
}

// ── The confirmation (Requirements 2.8, 2.9, 3.2) ───────────────────────────────────────

/**
 * The confirmation text for the archive action.
 *
 * Names the action ("Archive"), and its consequence in both directions: the strategy leaves
 * the list (Requirement 2.9, 3.3) and its records survive (Requirements 3.2, 3.5). It also
 * states the refusal the user is most likely to meet, so a blocked archive is not a
 * surprise. It deliberately does not say "delete": nothing is deleted.
 *
 * @param {string} [name] The strategy's display name, when the caller has it.
 * @returns {string}
 */
export function archiveConfirmMessage(name) {
  const subject = text(name) ? `"${text(name)}"` : 'this strategy';
  return (
    `Archive ${subject}?\n\n` +
    'It will be removed from your strategy list. Nothing is deleted: its versions, ' +
    'backtests, deployments and signals are all kept, and stay inspectable for history ' +
    'and audit.\n\n' +
    'Archiving is refused while any of its deployments is still deploying, running or ' +
    'paused — stop those first.'
  );
}
