/**
 * marketplaceFlow.js — the pure rules behind the gated Marketplace screens (MKT-04).
 *
 * The backend already enforces every one of these; the UI repeats only what a person needs to
 * act BEFORE a request is refused (which runs qualify, which action a state admits) and the
 * words for a refusal the server names by code. Nothing here decides eligibility, a price or a
 * transition — the server does, and its answer always wins.
 */

import { apiErrorMessage } from '../../design/apiErrors';

/** `evidence_validator.THRESHOLDS`, mirrored for the picker's pre-check. */
export const EVIDENCE_THRESHOLDS = Object.freeze({
  minConditions: 3,
  maxConditions: 10,
  minWindowDays: 90,
  minTrades: 20,
  minBars: 50,
});

/**
 * The owner-facing sentence for every criterion code `POST /api/library/submissions` can return
 * in `details.failures`. Copied from `eligibility_gate.MP_PUBLIC_MESSAGES` and the evidence
 * validator's public messages, which carry no threshold digit and no internal identifier.
 */
export const ELIGIBILITY_FAILURE_TEXT = Object.freeze({
  MP_OWNERSHIP: 'You can only publish a strategy that you own.',
  MP_TENANT: 'This strategy belongs to a different workspace and cannot be published here.',
  MP_VERSION_EXISTS:
    'This strategy must have at least one saved version before it can be published. Save the strategy, then try again.',
  MP_VERSION_VALID:
    'The saved version of this strategy did not pass validation. Open it in the builder, resolve the reported problems, and save it again.',
  MP_EXECUTION_OK:
    'Every backtest you reference must be a run that finished successfully. Re-run any test that did not complete before publishing.',
  MP_METRICS_COMPLETE:
    'Every backtest you reference must have recorded its full set of performance results. Re-run any test that is missing them.',
  MP_SUBMISSION_OPEN:
    'This strategy already has a submission in progress or a published listing. Complete or withdraw it before submitting again.',
  EV_COUNT:
    'A submission must reference several different completed backtest runs of this strategy, and each reference must name a different run.',
  EV_OWNERSHIP: 'Every referenced backtest run must be one of your own completed runs of this strategy.',
  EV_COMPLETED: 'Every referenced backtest run must have finished successfully, with no reported error.',
  EV_PARAMS:
    'Every referenced backtest run must have recorded the full set of settings it ran with. Re-run any test that is missing them.',
  EV_DURATION: 'Every referenced backtest run must cover a long enough stretch of market history.',
  EV_TRADES: 'Every referenced backtest run must contain enough trades for its results to be meaningful.',
  EV_BARS:
    'Every referenced backtest run must have executed over enough market data, and must have recorded how much.',
  EV_ONE_VERSION: 'Every referenced backtest run must test the same saved version of this strategy.',
  EV_CHECKSUMS: 'Every referenced backtest run must use a different set of market data.',
  EV_DISTINCT:
    'Referenced backtest runs must test genuinely different conditions. Two runs over largely the same stretch of the same market data count as one test.',
});

export function eligibilityFailureText(code) {
  return ELIGIBILITY_FAILURE_TEXT[code] || `The submission was refused at the ${code} check.`;
}

const DAY_MS = 86_400_000;

/** Calendar days a run covers, or `null` when either date is unreadable. */
export function windowDays(run) {
  const start = Date.parse(run?.start_date);
  const end = Date.parse(run?.end_date);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null;
  return Math.round((end - start) / DAY_MS);
}

/**
 * Why one run would not count as evidence, in plain words — or `null` when it meets every
 * per-run threshold the picker can see. Overlap between runs and same-version checks are left
 * to the server, which has the data to decide them.
 */
export function runShortfall(run) {
  if (!run || String(run.status || '').toLowerCase() !== 'completed') return 'Did not complete';
  const days = windowDays(run);
  if (days === null || days < EVIDENCE_THRESHOLDS.minWindowDays) {
    return `Covers ${days ?? '?'} days; needs ${EVIDENCE_THRESHOLDS.minWindowDays}+`;
  }
  const trades = Number(run.total_trades);
  if (!Number.isFinite(trades) || trades < EVIDENCE_THRESHOLDS.minTrades) {
    return `${Number.isFinite(trades) ? trades : 0} trades; needs ${EVIDENCE_THRESHOLDS.minTrades}+`;
  }
  const bars = Number(run.executed_bar_count);
  if (!Number.isFinite(bars) || bars < EVIDENCE_THRESHOLDS.minBars) {
    return `${Number.isFinite(bars) ? bars : 0} bars; needs ${EVIDENCE_THRESHOLDS.minBars}+`;
  }
  return null;
}

/** Whether a selection of run ids can be submitted at all (the count rule). */
export function selectionProblem(selectedIds) {
  const n = selectedIds?.length ?? 0;
  if (n < EVIDENCE_THRESHOLDS.minConditions) {
    return `Select at least ${EVIDENCE_THRESHOLDS.minConditions} backtests (${n} selected).`;
  }
  if (n > EVIDENCE_THRESHOLDS.maxConditions) {
    return `Select at most ${EVIDENCE_THRESHOLDS.maxConditions} backtests (${n} selected).`;
  }
  return null;
}

/** States a Submission can still move out of; the rest are final for that Submission. */
const OPEN_STATES = new Set(['DRAFT', 'SUBMITTED', 'UNDER_REVIEW', 'APPROVED', 'PUBLISHED', 'SUSPENDED']);

/** The Submission that governs this strategy now: the newest one. */
export function currentSubmission(submissions) {
  if (!Array.isArray(submissions) || submissions.length === 0) return null;
  return [...submissions].sort(
    (a, b) => Date.parse(b.created_at || 0) - Date.parse(a.created_at || 0),
  )[0];
}

/** Whether the owner may start a new Submission for this strategy. */
export function canStartSubmission(submission) {
  return !submission || !OPEN_STATES.has(submission.submission_state);
}

/** One line per state, for the owner. */
export const SUBMISSION_STATE_TEXT = Object.freeze({
  DRAFT: 'Draft — not yet offered for review.',
  SUBMITTED: 'Submitted — waiting for a reviewer.',
  UNDER_REVIEW: 'Under review.',
  APPROVED: 'Approved — waiting to be published.',
  PUBLISHED: 'Published — visible in the Marketplace.',
  REJECTED: 'Rejected. You can address the reason and submit again.',
  SUSPENDED: 'Suspended by a reviewer — hidden from the Marketplace; existing subscribers keep access.',
  UNPUBLISHED: 'Unpublished — withdrawn from the Marketplace.',
});

/**
 * The admin actions each state admits — `submission_state.SUBMISSION_TRANSITIONS`, as verbs.
 * `approve` from SUBMITTED is the server's two-edge SUBMITTED -> UNDER_REVIEW -> APPROVED.
 */
export const ADMIN_ACTIONS_BY_STATE = Object.freeze({
  SUBMITTED: ['approve', 'reject'],
  UNDER_REVIEW: ['approve', 'reject'],
  APPROVED: ['publish'],
  PUBLISHED: ['suspend', 'unpublish'],
  SUSPENDED: ['publish', 'unpublish'],
  DRAFT: [],
  REJECTED: [],
  UNPUBLISHED: [],
});

export function adminActionsFor(state) {
  return ADMIN_ACTIONS_BY_STATE[state] || [];
}

/** `money.MINOR_UNIT_EXPONENT`: the currencies a price may be set in. */
export const PRICE_CURRENCIES = Object.freeze({ USD: 2, INR: 2 });

/**
 * A typed major-unit price as exact Minor_Units, or a reason it is not one. A string is parsed
 * digit by digit — never through a float — so "19.99" is 1999 and "19.999" is refused.
 */
export function priceToMinor(text, currency) {
  const exponent = PRICE_CURRENCIES[currency];
  if (exponent === undefined) return { ok: false, message: `${currency} is not a supported currency.` };
  const trimmed = String(text ?? '').trim();
  const match = /^(\d+)(?:\.(\d*))?$/.exec(trimmed);
  if (!match) return { ok: false, message: 'Enter a price such as 19.99.' };
  const fraction = match[2] || '';
  if (fraction.length > exponent) {
    return { ok: false, message: `At most ${exponent} decimal places.` };
  }
  const minor = Number(match[1] + fraction.padEnd(exponent, '0'));
  if (!Number.isSafeInteger(minor) || minor < 1) return { ok: false, message: 'The price must be above zero.' };
  return { ok: true, minor };
}

/** Minor_Units as a major-unit string, exactly (no float division). */
export function minorToText(minor, currency) {
  const exponent = PRICE_CURRENCIES[currency] ?? 2;
  if (!Number.isSafeInteger(Number(minor))) return '—';
  const digits = String(Math.abs(Number(minor))).padStart(exponent + 1, '0');
  const whole = digits.slice(0, digits.length - exponent);
  const frac = digits.slice(digits.length - exponent);
  return `${Number(minor) < 0 ? '-' : ''}${whole}${exponent ? `.${frac}` : ''} ${currency}`;
}

/** The categories `PublishStrategyRequest.validate_category` accepts. */
export const LISTING_CATEGORIES = Object.freeze([
  ['trend_following', 'Trend following'],
  ['mean_reversion', 'Mean reversion'],
  ['momentum', 'Momentum'],
  ['arbitrage', 'Arbitrage'],
  ['market_making', 'Market making'],
  ['ml_hybrid', 'ML hybrid'],
  ['other', 'Other'],
]);

/**
 * A Marketplace refusal's code, message and details. The marketplace routes answer
 * `{error: {code, message, details}}`; FastAPI's own refusals answer `{detail}`.
 */
export function marketplaceRefusal(err) {
  const body = err?.data ?? err?.response?.data ?? null;
  const envelope =
    body && typeof body === 'object'
      ? (body.error && typeof body.error === 'object' ? body.error : null)
        || (body.detail && typeof body.detail === 'object' ? body.detail : null)
      : null;
  return {
    status: err?.status ?? err?.response?.status ?? null,
    code: typeof envelope?.code === 'string' ? envelope.code : null,
    message:
      (typeof envelope?.message === 'string' && envelope.message)
      || (typeof body?.detail === 'string' ? body.detail : null)
      // The shared reader: ApiError's own message, with leaked internals filtered out.
      || apiErrorMessage(err, null)
      || null,
    details: envelope?.details && typeof envelope.details === 'object' ? envelope.details : {},
  };
}
