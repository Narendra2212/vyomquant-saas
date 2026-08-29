/**
 * ═══════════════════════════════════════════════════════════════════════════
 * DeployPreflightPanel — the pre-deployment summary, as the server reported it
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * trading-lifecycle-integration task 18.1. Requirements 13.3, 13.4, 13.5, 13.6.
 *
 * WHAT THIS RENDERS
 * -----------------
 * One row per condition in the body of
 * `GET /api/strategy-operations/strategies/{id}/versions/{version}/deploy/preflight`,
 * each carrying that condition's own verdict — `passed`, `failed` or `pending` — and, for
 * anything that is not passed, the server's own wording for why (Requirement 13.3). The
 * poll that keeps this current is `useDeployPreflight`'s; the reading of the body is
 * `lib/deployPreflight.js`'s. This component decides nothing: it neither derives
 * `deployable` nor judges a condition, so the panel and the Deploy button can never
 * disagree about the same summary.
 *
 * WHY `pending` IS NOT DRAWN AS A SOFTER `passed`
 * ---------------------------------------------
 * `deployment_binding.CONDITION_PENDING` means the condition *could not be evaluated*,
 * because something it depends on failed. It is not a weaker pass and it is not "probably
 * fine": it keeps the Deploy button disabled exactly as `failed` does (Requirement 13.4).
 * So it gets its own glyph, its own colour and its own word, and where the server did not
 * say what blocked it, the row says plainly that it was not evaluated. A ✓-ish or
 * grey-tick treatment here would tell the author a mandatory check had been satisfied when
 * it had not been run.
 *
 * WHY THE WORDING IS THE SERVER'S
 * -------------------------------
 * `message` and `reason` are printed as they arrived. The gate that refuses the deploy is
 * the gate that wrote them (`DeployRejected.message`), so a failure named here and the
 * same failure named by the write path read identically, and no paraphrase can soften a
 * refusal or invent a corrective action the gate did not prescribe. That also keeps the
 * ownership convention intact: a version that is not the caller's and a version that does
 * not exist answer with the same `VERSION_NOT_FOUND` body, and nothing here adds a
 * distinction the backend deliberately refused to make.
 *
 * WHY THERE IS NO LIST OF CONDITIONS THIS PANEL KNOWS ABOUT
 * --------------------------------------------------------
 * Every condition in the response is rendered, whether or not this file has ever heard of
 * it. {@link PREFLIGHT_CONDITION_ORDER} is consulted for row *order* only, with unknown
 * names kept and shown after the known ones in the order the server sent them. A
 * client-side allow-list would silently hide a newly added mandatory condition — the
 * author would see a complete-looking panel that omitted the very check standing between
 * them and a deployment.
 */

import {
  CONDITION_FAILED,
  CONDITION_PASSED,
  CONDITION_PENDING,
  PREFLIGHT_CONDITION_ORDER,
} from '../lib/deployPreflight';

// ── Presentation vocabulary ─────────────────────────────────────────────────────────────

/**
 * How each wire status is drawn. Every entry carries a `glyph` *and* a `word`: the status
 * is never signalled by colour alone.
 */
export const STATUS_PRESENTATION = Object.freeze({
  [CONDITION_PASSED]: Object.freeze({
    word: 'PASSED',
    glyph: '✓',
    color: '#10b981',
    border: '1px solid rgba(16,185,129,0.35)',
    background: 'rgba(16,185,129,0.08)',
  }),
  [CONDITION_FAILED]: Object.freeze({
    word: 'FAILED',
    glyph: '✕',
    color: '#ef4444',
    border: '1px solid rgba(239,68,68,0.45)',
    background: 'rgba(239,68,68,0.10)',
  }),
  [CONDITION_PENDING]: Object.freeze({
    word: 'PENDING',
    glyph: '◌',
    // Dashed, not solid: "not evaluated" reads as unfinished rather than as a verdict.
    color: '#eab308',
    border: '1px dashed rgba(234,179,8,0.55)',
    background: 'rgba(234,179,8,0.08)',
  }),
});

/**
 * A condition whose `status` this client could not read (`normalizeCondition` reports it as
 * `null` rather than coercing it to a known one). Shown as unreadable, never as passed.
 */
export const UNREADABLE_PRESENTATION = Object.freeze({
  word: 'UNREADABLE',
  glyph: '?',
  color: '#94a3b8',
  border: '1px dashed rgba(148,163,184,0.45)',
  background: 'rgba(148,163,184,0.08)',
});

/** Said only where the server sent no `reason` of its own for a pending condition. */
export const PENDING_FALLBACK_NOTE =
  'Not evaluated, because a condition it depends on has not passed.';

export const UNREADABLE_NOTE =
  'This check reported a status this page cannot read, so it does not count as passed.';

/** Shown while the summary is unavailable — "not checked" is never "checked and fine". */
export const UNAVAILABLE_NOTE =
  'The deployment checks could not be read, so nothing has been verified and deployment ' +
  'stays blocked.';

// ── Small readers ───────────────────────────────────────────────────────────────────────

export function presentationFor(status) {
  return STATUS_PRESENTATION[status] ?? UNREADABLE_PRESENTATION;
}

/**
 * A condition's name as a line of prose: `exchange_account` → `Exchange account`.
 *
 * A pure transformation of whatever the server sent, with no lookup table, so a condition
 * name this file has never seen still gets a readable label instead of being dropped. The
 * raw name travels alongside it in `data-condition` and in the row's `title`, so what the
 * gate calls the check stays greppable.
 *
 * @param {unknown} name
 * @returns {string}
 */
export function conditionLabel(name) {
  const raw = typeof name === 'string' ? name.trim() : '';
  if (raw === '') return 'Unnamed check';
  const spaced = raw.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * The conditions in display order: the server's evaluation order for the names that order
 * is known for, then everything else in the order it arrived.
 *
 * @param {Array<Object>} [conditions]
 * @returns {Array<Object>}
 */
export function orderConditions(conditions = []) {
  const rows = Array.isArray(conditions) ? conditions : [];
  const rank = (row) => {
    const index = PREFLIGHT_CONDITION_ORDER.indexOf(row?.name);
    return index === -1 ? PREFLIGHT_CONDITION_ORDER.length : index;
  };
  return rows
    .map((row, arrival) => ({ row, arrival }))
    .sort((a, b) => rank(a.row) - rank(b.row) || a.arrival - b.arrival)
    .map(({ row }) => row);
}

/**
 * A condition's `detail` document as displayable pairs.
 *
 * Only scalar entries are shown, and only the ones the server actually sent — nothing is
 * defaulted, and a nested structure is left out rather than stringified into noise. This is
 * where Requirement 13.3's contextual facts (the exchange, the balance, the permission that
 * was checked) surface, in the server's own values.
 *
 * @param {unknown} detail
 * @returns {Array<[string, string]>}
 */
export function detailEntries(detail) {
  if (detail === null || typeof detail !== 'object' || Array.isArray(detail)) return [];
  return Object.entries(detail)
    .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value))
    .map(([key, value]) => [conditionLabel(key), String(value)]);
}

/**
 * The one-line count for the panel header.
 *
 * @param {Array<Object>} conditions
 * @returns {{total: number, passed: number, failed: number, unresolved: number}}
 */
export function countConditions(conditions = []) {
  const rows = Array.isArray(conditions) ? conditions : [];
  const passed = rows.filter((c) => c?.status === CONDITION_PASSED).length;
  const failed = rows.filter((c) => c?.status === CONDITION_FAILED).length;
  return {
    total: rows.length,
    passed,
    failed,
    // Pending and unreadable together: both mean "not established".
    unresolved: rows.length - passed - failed,
  };
}

// ── Styles ──────────────────────────────────────────────────────────────────────────────

const mono = { fontFamily: 'monospace' };

const panelStyle = {
  background: '#080a0e',
  border: '1px solid #1e293b',
  borderRadius: 8,
  padding: '10px 12px',
  marginBottom: 16,
  fontSize: 10,
  ...mono,
  color: '#94a3b8',
};

const rowStyle = (presentation) => ({
  display: 'flex',
  alignItems: 'flex-start',
  gap: 8,
  padding: '5px 7px',
  marginBottom: 4,
  borderRadius: 6,
  border: presentation.border,
  background: presentation.background,
});

// ── The panel ───────────────────────────────────────────────────────────────────────────

/**
 * @param {Object} props
 * @param {Array<Object>} [props.conditions] `normalizePreflight().conditions`.
 * @param {boolean} [props.deployable] The re-derived verdict the Deploy button uses.
 * @param {boolean|null} [props.reported] The server's own `deployable` flag, for comparison.
 * @param {{message: string}|null} [props.error] A poll that could not be answered.
 * @param {boolean} [props.isLoading] True while a poll is in flight.
 * @param {number|null} [props.checkedAt] When the last answer arrived.
 * @param {Function} [props.refresh] Re-ask now, without waiting for the next poll.
 * @param {Array<[string, string]>} [props.summary] The deployment being checked, as
 *   label/value pairs (Requirement 13.3's contextual list). Rendered as stated facts, with
 *   no pass/fail colouring of its own — the verdicts are the server's rows below it.
 */
export default function DeployPreflightPanel({
  conditions = [],
  deployable = false,
  reported = null,
  error = null,
  isLoading = false,
  checkedAt = null,
  refresh,
  summary = [],
}) {
  const rows = orderConditions(conditions);
  const counts = countConditions(rows);
  const hasRows = rows.length > 0;

  return (
    <div style={panelStyle} data-testid="preflight-panel" data-deployable={String(deployable)}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          marginBottom: 8,
        }}
      >
        <div style={{ color: '#00d4ff', fontWeight: 900 }}>PRE-FLIGHT VALIDATION:</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {/* Requirement 13.6: the panel is re-read while the modal is open, so saying when
              it was last answered is the difference between a live verdict and a stale one. */}
          <span style={{ color: '#5a6578', fontSize: 9 }} data-testid="preflight-checked-at">
            {isLoading && !hasRows
              ? 'checking...'
              : checkedAt
                ? `checked ${new Date(checkedAt).toLocaleTimeString()}`
                : 'not checked yet'}
          </span>
          {typeof refresh === 'function' && (
            <button
              type="button"
              onClick={refresh}
              data-testid="preflight-refresh"
              style={{
                background: 'none',
                border: '1px solid #1e293b',
                borderRadius: 6,
                color: '#00d4ff',
                cursor: 'pointer',
                fontSize: 9,
                padding: '2px 6px',
                ...mono,
              }}
            >
              Re-check
            </button>
          )}
        </div>
      </div>

      {/* Requirement 13.3's contextual half: what deployment these verdicts are about. */}
      {summary.length > 0 && (
        <dl
          data-testid="preflight-summary"
          style={{
            display: 'grid',
            gridTemplateColumns: 'auto 1fr',
            columnGap: 8,
            rowGap: 2,
            margin: '0 0 8px',
            paddingBottom: 8,
            borderBottom: '1px solid #151821',
          }}
        >
          {summary.map(([label, value]) => (
            <div key={label} style={{ display: 'contents' }}>
              <dt style={{ color: '#5a6578' }}>{label}</dt>
              <dd style={{ margin: 0, color: '#cbd5e1' }}>{value}</dd>
            </div>
          ))}
        </dl>
      )}

      {/* aria-live: Requirements 13.5/13.6 turn on this list changing under the user
          without any action of theirs, so a change has to be announced, not just painted. */}
      <div aria-live="polite" data-testid="preflight-conditions">
        {hasRows ? (
          rows.map((condition, index) => {
            const presentation = presentationFor(condition?.status);
            const name = condition?.name ?? null;
            const message = condition?.message ?? null;
            const reason = condition?.reason ?? null;
            const details = detailEntries(condition?.detail);
            const note =
              condition?.status === CONDITION_PENDING && !message && !reason
                ? PENDING_FALLBACK_NOTE
                : condition?.status === undefined || condition?.status === null
                  ? UNREADABLE_NOTE
                  : null;

            return (
              <div
                // The server may report an unnamed condition; the arrival index keeps such a
                // row addressable rather than dropping it.
                key={name ?? `condition-${index}`}
                data-testid="preflight-condition"
                data-condition={name ?? ''}
                data-status={condition?.status ?? 'unreadable'}
                title={name ?? undefined}
                style={rowStyle(presentation)}
              >
                <span aria-hidden="true" style={{ color: presentation.color, lineHeight: '14px' }}>
                  {presentation.glyph}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'baseline' }}>
                    <span style={{ color: '#e2e8f0', fontWeight: 700 }}>
                      {conditionLabel(name)}
                    </span>
                    <span
                      style={{ color: presentation.color, fontSize: 9, letterSpacing: 1, fontWeight: 900 }}
                    >
                      {presentation.word}
                    </span>
                    {condition?.code && (
                      <span style={{ color: '#5a6578', fontSize: 9 }}>{condition.code}</span>
                    )}
                  </div>
                  {/* The gate's own wording, printed as it arrived. */}
                  {message && (
                    <div style={{ color: presentation.color, marginTop: 2 }}>{message}</div>
                  )}
                  {reason && (
                    <div style={{ color: presentation.color, marginTop: 2 }}>{reason}</div>
                  )}
                  {note && <div style={{ color: presentation.color, marginTop: 2 }}>{note}</div>}
                  {details.length > 0 && (
                    <div style={{ color: '#5a6578', marginTop: 2 }}>
                      {details.map(([label, value]) => `${label}: ${value}`).join(' · ')}
                    </div>
                  )}
                </div>
              </div>
            );
          })
        ) : (
          <div data-testid="preflight-unavailable" style={{ color: error ? '#ef4444' : '#5a6578' }}>
            {isLoading && !error ? 'Running the deployment checks...' : UNAVAILABLE_NOTE}
          </div>
        )}
      </div>

      <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        <span
          data-testid="preflight-verdict"
          style={{ color: deployable ? '#10b981' : '#eab308', fontWeight: 900 }}
        >
          {deployable
            ? `All ${counts.total} mandatory checks passed — deployment is unblocked.`
            : hasRows
              ? `${counts.passed} of ${counts.total} passed · ${counts.failed} failed · ` +
                `${counts.unresolved} not evaluated — deployment stays blocked.`
              : 'Nothing has been verified — deployment stays blocked.'}
        </span>
        {/* The server's flag and its own condition list disagreeing is not a display
            detail: the button follows the conditions, and the author is told why. */}
        {reported === true && !deployable && hasRows && (
          <span data-testid="preflight-disagreement" style={{ color: '#eab308' }}>
            The summary reported itself deployable, but its own conditions do not agree, so
            deployment stays blocked.
          </span>
        )}
      </div>
    </div>
  );
}
