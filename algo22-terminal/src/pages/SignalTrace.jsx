/**
 * ═══════════════════════════════════════════════════════════════════════════
 * pages/SignalTrace — nine stages, built from the canonical list outward
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 21.4 (part a). design.md §10.1, §10.2, §10.3.
 * Requirements 9.1, 9.2, 9.3, 9.4, 14.5, 19.3.
 *
 * WHAT THIS PAGE DECIDES, AND WHAT IT DOES NOT
 * -------------------------------------------
 * It decides layout, copy and colour. It decides NOTHING about stage semantics.
 * `lib/signalTraceStages.js` is the projection — `buildSignalTraceStages(payload)` returns
 * the nine stages, always all nine, always in canonical order, each already carrying its
 * state, its summary, its reason, its latency and its backing detail. This file maps over
 * that array. There is no branch here that looks at a `timeline` event, at
 * `ml_inference.applicable` or at `dag_nodes.nodes` to work out what a row should say: a
 * second opinion about a stage's state is exactly how the page and the module come to
 * disagree, and the module is the one with 44 tests behind it (Properties 15 and 16).
 *
 * WHAT REPLACED WHAT
 * -----------------
 * The page this replaces rendered a filter grid of eight hand-rolled inputs (three of them
 * labelled by `placeholder` only — Requirement 15.1's exact prohibition), an eleven-column
 * `gridTemplateColumns` string of `<div>`s with a click handler and no keyboard path, and,
 * for the trace itself, `timeline.map()` — a row per event the payload happened to carry,
 * with `JSON.stringify(event.data, null, 2)` in a `<pre>` as the detail. That shape is the
 * one Requirement 9.1 cannot be satisfied by: six events produced six rows, so the nine
 * stages were nine only when the signal had reached all nine, and an unknown event type
 * added a tenth. Everything visual now goes through `ds/*`, and every colour through
 * `design/semantic.js`.
 *
 * THE THREE READS, UNCHANGED
 * -------------------------
 *   * `GET /api/signal-trace/signals?…`            the filtered, paginated list
 *   * `GET /api/signal-trace/signals/{id}`         one signal's trace, for the timeline
 *   * `GET /api/signal-trace/signals/export?…`     the CSV / JSON download
 *
 * The same three the page read before, with the same parameter spellings. Both list and
 * detail now run through `usePanelState` rather than `useState` + `useEffect`, which is
 * what gets Requirement 14.5 for free: a failed read DROPS the previous payload instead of
 * leaving the last successful trace on screen under an error indicator.
 *
 * `usePanelState`, NOT `usePolling`
 * -------------------------------
 * `usePolling` keeps `data` on failure and lists `data` in its fetch dependencies, so its
 * interval is torn down and rebuilt on every tick. Neither is acceptable on a page whose
 * whole subject is what the server actually recorded.
 *
 * WHY THE REALTIME WIRING IS STILL HERE IN FULL
 * --------------------------------------------
 * trading-lifecycle-integration tasks 19.1–19.3 (Requirements 17.4, 18.1–18.9, 23.6) put
 * the live updates in THIS view — the list and its detail — and that work is untouched by
 * this task: one `signal.{deployment_id}` channel per watched deployment, pushed updates
 * applied in place, an arrival the page does not list ANNOUNCED rather than inserted, a
 * refused channel surfaced, and a visible connection state in all four of its states. The
 * only change is the palette: the four presentations below take their colour from
 * `statusToken` instead of the six hex literals they carried.
 */

import { useCallback, useId, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  Activity,
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Download,
  RefreshCw,
  Waypoints,
} from 'lucide-react';

import { get } from '../apiClient';
import { CONFIG } from '../config';
import { Alert } from '../components/ds/Alert';
import { CommandButton } from '../components/ds/CommandButton';
import { DataTable } from '../components/ds/DataTable';
import { EmptyState } from '../components/ds/EmptyState';
import { Field } from '../components/ds/Field';
import { FilterBar } from '../components/ds/FilterBar';
import { NotAvailableMarker } from '../components/ds/Metric';
import { PageHeader } from '../components/ds/PageHeader';
import { Panel } from '../components/ds/Panel';
import { StatusBadge } from '../components/ds/StatusBadge';
import SignalTraceVisualization from '../components/SignalTraceVisualization';
import { PAGES, PAGE_FIELDS_BY_PAGE } from '../design/pageFields';
import { ENVIRONMENT_IDS, statusToken } from '../design/semantic';
import { PANEL_STATES, usePanelState } from '../hooks/usePanelState';
import useSignalTraceRealtime from '../hooks/useSignalTraceRealtime';
import {
  SIGNAL_REALTIME_STATES,
  deploymentIdsFromSignals,
  mergeRealtimeSignals,
  unlistedSignalIds,
} from '../lib/signalTraceRealtime';
import {
  REASON_TRACE_RETENTION,
  STAGE_BLOCKED,
  STAGE_COMPLETE,
  STAGE_EXECUTION,
  STAGE_INDICATORS,
  STAGE_LOGIC,
  STAGE_MARKET_DATA,
  STAGE_MODEL,
  STAGE_NOT_APPLICABLE,
  STAGE_NOT_AVAILABLE,
  STAGE_ORDER_DECISION,
  STAGE_PENDING,
  STAGE_POSITION_UPDATE,
  STAGE_SIGNAL,
  STAGE_SUBMISSION,
  buildSignalTraceStages,
} from '../lib/signalTraceStages';
import wsClient from '../websocketClient';

/* ══════════════════════════════════════════════════════════════════════════
 * THE CONNECTION, AS THIS PAGE REPORTS IT (task 19.3, Requirement 18.9)
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * How each realtime state is drawn.
 *
 * Every entry carries a `glyph` *and* a `word` beside its colour, following
 * `components/DeployPreflightPanel.jsx`'s vocabulary (task 18.1): a user who cannot
 * distinguish green from red must still be able to tell a live page from a stalled one, so
 * the state is never signalled by colour alone.
 *
 * WHY THERE ARE FOUR ENTRIES FOR REQUIREMENT 18.9's THREE STATES
 * -------------------------------------------------------------
 * The hook reports four (`SIGNAL_REALTIME_STATES`), and `UNAVAILABLE` is not a fourth
 * shade of "disconnected": it means this page has no connection to lose — no session
 * token, or no active deployment to subscribe to. Drawing it as `DISCONNECTED` would claim
 * a connection had dropped when none was ever opened, and drawing it as `RECONNECTING`
 * would promise a retry that is not coming and could not succeed. So it gets its own word,
 * its own glyph and its own wording for the reason, which the page supplies because the
 * page is what knows whether there was a deployment to watch.
 *
 * `CONNECTING` is worded as "RECONNECTING" because that is what it is from the user's
 * side: `websocketClient` reports `connecting` both for the first attempt and for every
 * jittered backoff retry (Requirement 18.6), and the thing the user needs to know in both
 * cases is the same — frames are not arriving yet.
 *
 * COLOUR COMES FROM `statusToken`, AND THE STATE NAMES ARE ITS OWN
 * --------------------------------------------------------------
 * Each entry names a `design/semantic.js` state and the token is resolved here, so the
 * four hues are the platform's four rather than this file's. `connected`, `reconnecting`
 * and `disconnected` are literally in `semantic.js`'s vocabulary table; the unavailable
 * and unreadable arms pass `null`, which is that module's own route to the neutral group.
 * The border STYLE carries the second, non-colour axis — dashed for the two states that
 * are not a verdict about a connection that exists.
 */
const CONNECTION_STYLES = Object.freeze([
  Object.freeze({
    state: SIGNAL_REALTIME_STATES.CONNECTED,
    token: 'connected',
    word: 'CONNECTED',
    glyph: '●',
    stroke: 'solid',
    note: 'Live updates are arriving; this list changes on its own.',
  }),
  Object.freeze({
    state: SIGNAL_REALTIME_STATES.CONNECTING,
    token: 'reconnecting',
    word: 'RECONNECTING',
    glyph: '◌',
    // Dashed, as the preflight panel's unfinished state is: an attempt in progress is not
    // a verdict about the connection.
    stroke: 'dashed',
    note: 'Not receiving updates yet. A status change happening now will be read back when the connection returns.',
  }),
  Object.freeze({
    state: SIGNAL_REALTIME_STATES.DISCONNECTED,
    token: 'disconnected',
    word: 'DISCONNECTED',
    glyph: '✕',
    stroke: 'solid',
    note: 'Live updates have stopped. What is shown may be out of date until the connection returns or you refresh.',
  }),
  Object.freeze({
    state: SIGNAL_REALTIME_STATES.UNAVAILABLE,
    token: null,
    word: 'NOT LIVE',
    glyph: '–',
    stroke: 'dashed',
    // Deliberately absent: the reason is the page's to state, see UNAVAILABLE_REASONS.
    note: null,
  }),
]);

/** One `CONNECTION_STYLES` row as the presentation a renderer reads. */
const presentationOf = ({ token, word, glyph, stroke, note }) => {
  const { fg, wash } = statusToken(token);
  return Object.freeze({
    word,
    glyph,
    color: fg,
    border: `1px ${stroke} ${fg}`,
    background: wash,
    note,
  });
};

/** Requirement 18.9's four states, keyed by the state the hook reports. */
export const SIGNAL_CONNECTION_PRESENTATION = Object.freeze(
  Object.fromEntries(CONNECTION_STYLES.map((style) => [style.state, presentationOf(style)])),
);

/**
 * A state this build cannot read. Reported as unreadable rather than as connected, for the
 * same reason the hook maps an unknown socket status to `DISCONNECTED`: a state we cannot
 * read is not evidence that frames are arriving.
 */
export const UNREADABLE_CONNECTION_PRESENTATION = presentationOf({
  token: null,
  word: 'UNKNOWN',
  glyph: '?',
  stroke: 'dashed',
  note: 'This page cannot read the state of its live connection, so treat the list as not updating.',
});

/**
 * The two things `UNAVAILABLE` can mean, told apart by whether there was anything to
 * subscribe to. `useSignalTraceRealtime` connects only with a token AND at least one
 * channel, so a page reporting `UNAVAILABLE` while holding channels is a page with no
 * session, and one holding none has nothing to watch.
 */
export const UNAVAILABLE_REASONS = Object.freeze({
  NOTHING_TO_WATCH:
    'No active deployment to watch, so there are no live updates to receive. This list updates when you refresh it.',
  NOT_AUTHENTICATED:
    'Not signed in for live updates, so this list changes only when you refresh it.',
});

/** @param {string|null|undefined} status @returns {Object} */
export function signalConnectionPresentation(status) {
  return SIGNAL_CONNECTION_PRESENTATION[status] ?? UNREADABLE_CONNECTION_PRESENTATION;
}

/**
 * Requirement 18.9's visible connection-status indicator.
 *
 * Rendered in every state, not only while the connection is down. The requirement names
 * "connected" as one of the states to be distinguished, and an indicator that appears only
 * on failure cannot distinguish a live page from one whose indicator has not been reached
 * yet — the user would have to know the control exists to read its absence.
 *
 * `role="status"` (with `aria-live="polite"`) because the whole point is that this changes
 * with no action from the user, so the change has to be announced and not only painted.
 * The glyph is `aria-hidden`, since the word beside it already carries the state.
 *
 * This is a separate element from the refusal banner on purpose: a refused channel
 * (Requirement 18.3) is a live connection that will not carry one deployment, which is a
 * different fact from the connection itself being down, and collapsing the two would make
 * a partial failure read as a total one.
 *
 * @param {Object} props
 * @param {string} props.status One of `SIGNAL_REALTIME_STATES`.
 * @param {number} [props.watching] How many channels the page holds, i.e. `channels.length`.
 */
export function SignalConnectionIndicator({ status, watching = 0 }) {
  const presentation = signalConnectionPresentation(status);
  const note =
    presentation.note ??
    (watching > 0 ? UNAVAILABLE_REASONS.NOT_AUTHENTICATED : UNAVAILABLE_REASONS.NOTHING_TO_WATCH);

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="signal-trace-connection"
      data-status={status ?? 'unreadable'}
      className="flex flex-wrap items-baseline gap-2 rounded-sm px-3 py-2 font-mono text-micro"
      style={{ border: presentation.border, background: presentation.background }}
    >
      <span aria-hidden="true" style={{ color: presentation.color }}>
        {presentation.glyph}
      </span>
      <span className="font-bold tracking-wide" style={{ color: presentation.color }}>
        {presentation.word}
      </span>
      <span className="text-content-secondary">{note}</span>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE DECLARATION — labels and reasons come from `design/pageFields.js`
 * ══════════════════════════════════════════════════════════════════════════ */

const TRACE_FIELDS = PAGE_FIELDS_BY_PAGE[PAGES.SIGNAL_TRACE] ?? [];

/** One field's declaration, or `null`. */
const fieldEntry = (field) => TRACE_FIELDS.find((entry) => entry.field === field) ?? null;

/** The label §10.1 / §10.3 names for a field. Read, not restated. */
const labelFor = (field) => fieldEntry(field)?.label ?? field;

/**
 * The sentence rendered instead of a value, from the declaration.
 *
 * `undefined` rather than `null` when a field declares none, so `NotAvailableMarker` falls
 * back to its own reason copy instead of being handed an empty string.
 */
const reasonFor = (field) => fieldEntry(field)?.reason ?? undefined;

/**
 * Canonical stage id → the `pageFields` entry that declares it.
 *
 * Keyed by the id rather than by position so a stage with no declaration is a missing key
 * rather than an off-by-one, and so the two lists cannot silently drift apart.
 */
const STAGE_FIELD = Object.freeze({
  [STAGE_MARKET_DATA]: 'stage1MarketData',
  [STAGE_INDICATORS]: 'stage2Indicators',
  [STAGE_MODEL]: 'stage3ModelOutput',
  [STAGE_LOGIC]: 'stage4Logic',
  [STAGE_SIGNAL]: 'stage5Signal',
  [STAGE_ORDER_DECISION]: 'stage6OrderDecision',
  [STAGE_SUBMISSION]: 'stage7Submission',
  [STAGE_EXECUTION]: 'stage8Execution',
  [STAGE_POSITION_UPDATE]: 'stage9PositionUpdate',
});

/* ══════════════════════════════════════════════════════════════════════════
 * THE FIVE STATES, DRAWN (design.md §10.2)
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * §10.2's treatment table, one row per state.
 *
 * `token` is a `design/semantic.js` state name, so the hue is that module's decision.
 * `muted` marks the two states §10.2 gives `content.muted` — the marker is drawn in the
 * muted content colour through a class rather than an inline hue, because "muted" is a
 * content weight and not a status.
 *
 * `stroke` is the rail, and it is the axis that survives a reader who cannot see the
 * hues: solid for a stage that has a record, dashed for one still to come, dotted for one
 * whose record cannot be read. `word` is the third axis, and it is the one a screen reader
 * gets — no state here is signalled by colour alone.
 */
const STATE_PRESENTATION = Object.freeze({
  [STAGE_COMPLETE]: Object.freeze({
    token: 'live', glyph: '●', stroke: 'solid', muted: false, word: 'Complete',
  }),
  [STAGE_BLOCKED]: Object.freeze({
    token: 'error', glyph: '✕', stroke: 'solid', muted: false, word: 'Blocked',
  }),
  [STAGE_PENDING]: Object.freeze({
    // §10.2: a `status.neutral` OUTLINE marker, not the warning hue — a stage that has not
    // happened yet is not a problem, and colouring it amber would make every in-flight
    // signal look like it had one.
    token: null, glyph: '○', stroke: 'dashed', muted: false, word: 'Not yet reached',
  }),
  [STAGE_NOT_APPLICABLE]: Object.freeze({
    token: null, glyph: '·', stroke: 'dotted', muted: true, word: 'Not applicable to this strategy',
  }),
  [STAGE_NOT_AVAILABLE]: Object.freeze({
    token: null, glyph: '–', stroke: 'dotted', muted: true, word: 'Not available',
  }),
});

/** Total over any state string, so an unrecognised one draws calmly rather than crashing. */
const presentationForState = (state) =>
  STATE_PRESENTATION[state] ?? STATE_PRESENTATION[STAGE_NOT_AVAILABLE];

/* ══════════════════════════════════════════════════════════════════════════
 * THE LATENCY SLOT (design.md §10.3, Requirements 14.5, 19.3)
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The only stages that can carry a duration, and the field each number comes from.
 *
 * THREE fields in the whole response report a duration — `dag_nodes[].execution_ms`,
 * `ml_inference.detail.inference_ms` and `execution.outcome.latency_ms` — so these four
 * stages are the only ones where a figure is even possible. §10.3's mock originally drew a
 * latency on almost every row; it was corrected after task 21.1 measured the payload.
 *
 * Stages 1, 5, 6, 7 and 9 are absent from this map deliberately, and that absence is what
 * puts the not-available marker in their latency slot. A `0ms` there would read as
 * "instant", and an unreported duration is not a measured zero (Requirement 14.5).
 *
 * A stage that IS in this map and reports `0` renders `0ms`, because that is a number the
 * server measured. The prohibition is on inventing one, not on showing one.
 */
const LATENCY_SOURCE = Object.freeze({
  [STAGE_INDICATORS]: '`execution_ms` summed over `trace.dag_nodes.nodes`',
  [STAGE_MODEL]: '`trace.ml_inference.detail.inference_ms`',
  [STAGE_LOGIC]: '`execution_ms` summed over this stage\u2019s LOGIC-category nodes',
  [STAGE_EXECUTION]: '`trace.execution.outcome.latency_ms`',
});

/** Why a stage has no latency slot to fill. The same sentence for all five. */
const NO_DURATION_REASON =
  'Nothing in this response reports a duration for this stage. An unreported duration and '
  + 'a zero duration are different facts, so no figure is shown rather than a 0ms that would '
  + 'read as instant.';

/**
 * The latency slot for one stage: a figure, or the marker with the reason there is none.
 *
 * @param {Object} props
 * @param {{id: string, name: string, latencyMs: number|null}} props.stage
 */
function StageLatency({ stage }) {
  const source = LATENCY_SOURCE[stage.id] ?? null;
  const label = `${stage.name} latency`;

  if (source === null) {
    return <NotAvailableMarker label={label} reason={NO_DURATION_REASON} />;
  }
  if (typeof stage.latencyMs !== 'number' || !Number.isFinite(stage.latencyMs)) {
    return (
      <NotAvailableMarker
        label={label}
        reason={`This trace reports no ${source} for this stage, so no duration is stated.`}
      />
    );
  }
  return <span className="font-mono text-content-primary">{`${stage.latencyMs}ms`}</span>;
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE EXPANDED BODY — THE TECHNICAL DETAIL (task 21.4b, design.md §10.3)
 * ══════════════════════════════════════════════════════════════════════════
 *
 * WHERE EVERY VALUE BELOW COMES FROM
 * ---------------------------------
 * `stage.detail`, and nothing else. `lib/signalTraceStages.js` already hands each stage the
 * slice of the payload that backs it — `signal.market_info` for stage 1, `{source, nodes,
 * readings}` for stage 2, the ML `detail` for stage 3, and so on — so no renderer here
 * reaches back into the raw response. That matters for the same reason the state does: a
 * body that re-read `payload.trace.dag_nodes` could show nodes for a stage the module had
 * already called not-available, and the two would disagree on screen.
 *
 * WHAT AN ABSENT FIELD RENDERS
 * ---------------------------
 * The marker, with a reason naming the response path that is empty. Never a `0`, never a
 * dash on its own, never a plausible default (Requirements 14.5, 19.3). And where the whole
 * backing section is absent, the body says THAT — one sentence, the module's own reason —
 * instead of laying out a grid of markers for fields that were never going to be there.
 *
 * WHY THE INNER LABELS ARE THE SERVER'S OWN KEY NAMES
 * -------------------------------------------------
 * `design/pageFields.js` declares one entry per STAGE (plus `resultingPosition`, which is a
 * field in its own right and is read from there). It does not declare the ~70 fields inside
 * the four trace sections, and it should not: `market_info`, `signal.indicators`, a node's
 * `reading` and the row-sourced `ml_info` are open records whose keys are strategy-defined,
 * so any fixed list would silently drop whatever it had not been told about. The named
 * fields below are the ones the backend's own projections always emit — `_dag_node_entry`,
 * `_execution_outcome_of`, `_position_updated_event`, `_node_io` and the three engine
 * dataclasses — and every key beyond them is rendered under "Other reported fields" with
 * the key the server sent. Nothing the response carries is hidden, and nothing it does not
 * carry is invented.
 */

/** A plain record, or `null`. An array is not a record and neither is a string. */
const plainRecord = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;

/** An array, or `[]`. A section the server could not build is not a crash here. */
const plainArray = (value) => (Array.isArray(value) ? value : []);

/**
 * Why a field renders the marker: the response does not carry it.
 *
 * The path is named rather than described, so the sentence says which field of which
 * section is empty and a reader can check it against the payload.
 */
const absentReason = (path) =>
  `The response carries no ${path} for this signal. An unreported value and a zero are `
  + 'different facts, so nothing is stated rather than a default.';

/** A readable scalar as the text to render, or `null` when there is nothing to render. */
function scalarText(value) {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed === '' ? null : trimmed;
  }
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : null;
  if (typeof value === 'bigint') return String(value);
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return null;
}

/**
 * A record or an array as compact JSON — the server's own structure, unedited.
 *
 * The values that reach here are the open ones: a node's evaluated `reading`, a port's
 * `value`, `market_info.reported`, an ML feature vector. They are already bounded
 * server-side (`signal_service._jsonable` caps depth, width and string length), so there is
 * nothing to truncate here, and truncating would be the one edit that turns a real value
 * into a partial one. An empty record or array reads as "nothing carried" and takes the
 * marker, because `{}` is not a value a trader can act on.
 */
function structuredText(value) {
  if (Array.isArray(value)) return value.length === 0 ? null : JSON.stringify(value);
  const record = plainRecord(value);
  if (record === null) return null;
  return Object.keys(record).length === 0 ? null : JSON.stringify(record);
}

/** Any value the response carries → its text, or `null` for "not carried". */
const detailText = (value) => scalarText(value) ?? structuredText(value);

/** A field's value, or the marker with the reason there is none. */
function DetailValue({ label, value, reason }) {
  const shown = detailText(value);
  if (shown === null) return <NotAvailableMarker label={label} reason={reason} />;
  return <span className="min-w-0 break-words font-mono text-content-primary">{shown}</span>;
}

/** One labelled field of a section. */
function DetailField({ label, value, reason, wide = false }) {
  return (
    <div className={`flex min-w-0 flex-col gap-1${wide ? ' col-span-2' : ''}`}>
      <span className="text-micro uppercase tracking-wide text-content-secondary">{label}</span>
      <DetailValue label={label} value={value} reason={reason} />
    </div>
  );
}

/**
 * A whole section the response does not carry, or an empty list inside one.
 *
 * The marker AND the sentence, because this is the case where the sentence is the entire
 * content of the region: a lone dash where a node trace was expected says nothing about why
 * it is not there.
 */
function DetailNote({ label, reason }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-micro uppercase tracking-wide text-content-secondary">{label}</span>
      <span className="flex min-w-0 flex-wrap items-baseline gap-2">
        <NotAvailableMarker label={label} reason={reason} />
        <span className="min-w-0 text-content-secondary">{reason}</span>
      </span>
    </div>
  );
}

/** A titled group inside an expanded body. */
function DetailSection({ title, children }) {
  return (
    <section className="flex min-w-0 flex-col gap-2 border-t border-line-default pt-2">
      <h4 className="text-micro font-semibold uppercase tracking-wide text-content-secondary">
        {title}
      </h4>
      {children}
    </section>
  );
}

/** Two columns of labelled fields. The one layout every section body uses. */
function FieldGrid({ children }) {
  return <div className="grid min-w-0 grid-cols-2 gap-x-4 gap-y-2">{children}</div>;
}

/**
 * A declared field list against one record.
 *
 * @param {Object} props
 * @param {ReadonlyArray<{key: string, label: string, wide?: boolean}>} props.fields
 * @param {unknown} props.record The section, as the projection handed it over.
 * @param {string} props.path The response path of `record`, for the absence reasons.
 */
function DeclaredFields({ fields, record, path }) {
  const source = plainRecord(record);
  return (
    <FieldGrid>
      {fields.map((field) => (
        <DetailField
          key={field.key}
          label={field.label}
          value={source?.[field.key]}
          reason={field.reason ?? absentReason(`${path}.${field.key}`)}
          wide={field.wide === true}
        />
      ))}
    </FieldGrid>
  );
}

/**
 * Every key of a record that the declared list above did not name.
 *
 * This is what keeps the four trace sections honest in the other direction. `market_info`
 * and `ml_info` are open records, the engine dataclasses gain fields without asking this
 * page, and a strategy's node closure is keyed by node id — so a body that rendered only
 * its declared fields would quietly drop real recorded values. Renders nothing at all when
 * there is nothing left over, rather than an empty heading.
 */
function OtherFields({ record, known, path, title = 'Other reported fields' }) {
  const entries = Object.entries(plainRecord(record) ?? {}).filter(
    ([key]) => !known.includes(key),
  );
  if (entries.length === 0) return null;
  return (
    <DetailSection title={title}>
      <FieldGrid>
        {entries.map(([key, value]) => (
          <DetailField
            key={key}
            label={key}
            value={value}
            reason={absentReason(`${path}.${key}`)}
          />
        ))}
      </FieldGrid>
    </DetailSection>
  );
}

/** The keys of a declared field list, for {@link OtherFields}. */
const keysOf = (fields) => fields.map((field) => field.key);

/** An open record rendered key by key — a node closure, an indicator reading set. */
function RecordFields({ record, path, title, emptyReason }) {
  const entries = Object.entries(plainRecord(record) ?? {});
  return (
    <DetailSection title={title}>
      {entries.length === 0 ? (
        <DetailNote label={title} reason={emptyReason} />
      ) : (
        <FieldGrid>
          {entries.map(([key, value]) => (
            <DetailField
              key={key}
              label={key}
              value={value}
              reason={absentReason(`${path}.${key}`)}
            />
          ))}
        </FieldGrid>
      )}
    </DetailSection>
  );
}

/* ── The DAG node trace (stages 2 and 4) ───────────────────────────────── */

/**
 * `_dag_node_entry`'s key set, minus the three the card renders in its own header.
 *
 * One key set covers both sources on purpose — the server's own decision: the engine's
 * record and the row's persisted `node_closure` describe the same node with different
 * amounts of detail, and the absent half comes back `None`/`[]` rather than as a second
 * response shape. So a row-sourced node renders markers for the timing and the per-port
 * I/O, which is exactly what it does not carry, and `source` on the card says why.
 */
const NODE_FIELDS = Object.freeze([
  Object.freeze({ key: 'node_type', label: 'Node type' }),
  Object.freeze({ key: 'node_label', label: 'Node label' }),
  Object.freeze({ key: 'status', label: 'Status' }),
  Object.freeze({ key: 'execution_ms', label: 'Execution ms' }),
  Object.freeze({ key: 'cache_hit', label: 'Cache hit' }),
  Object.freeze({ key: 'retry_count', label: 'Retries' }),
  Object.freeze({ key: 'started_at', label: 'Started at' }),
  Object.freeze({ key: 'ended_at', label: 'Ended at' }),
  Object.freeze({ key: 'reading', label: 'Evaluated reading', wide: true }),
  Object.freeze({ key: 'error', label: 'Error', wide: true }),
]);

/** The node keys rendered somewhere on the card, so `OtherFields` does not repeat them. */
const NODE_KNOWN_KEYS = Object.freeze([
  ...keysOf(NODE_FIELDS),
  'node_id',
  'source',
  'is_source_node',
  'inputs',
  'outputs',
]);

const NODES_PATH = 'trace.dag_nodes.nodes[]';

/**
 * Why a node carries no per-port I/O. `_dag_nodes_from_row` cannot report any: the
 * persisted closure holds the evaluated reading per node and nothing about the ports.
 */
const REASON_NO_NODE_IO =
  'This node record reports no ports. Per-port inputs and outputs are recorded by the live '
  + 'trace engine only, so a node reconstructed from the signal row carries the evaluated '
  + 'reading instead.';

/** One `_node_io` port: its key, its value, and the two shape facts the server sends. */
function NodePort({ port, side, index }) {
  const io = plainRecord(port) ?? {};
  const name = scalarText(io.key);
  const label = name ?? `${side} ${index + 1}`;
  return (
    <span className="flex min-w-0 flex-wrap items-baseline gap-2 font-mono text-micro">
      {name === null ? (
        <NotAvailableMarker label={`${label} name`} reason={absentReason(`${NODES_PATH}.${side}[].key`)} />
      ) : (
        <span className="text-content-secondary">{name}</span>
      )}
      <span aria-hidden="true" className="text-content-muted">=</span>
      <DetailValue
        label={`${label} value`}
        value={io.value}
        reason={absentReason(`${NODES_PATH}.${side}[].value`)}
      />
      <span aria-hidden="true" className="text-content-muted">·</span>
      <DetailValue
        label={`${label} dtype`}
        value={io.dtype}
        reason={absentReason(`${NODES_PATH}.${side}[].dtype`)}
      />
      <span aria-hidden="true" className="text-content-muted">·</span>
      <DetailValue
        label={`${label} shape`}
        value={io.shape}
        reason={absentReason(`${NODES_PATH}.${side}[].shape`)}
      />
    </span>
  );
}

/** One side of a node's I/O. */
function NodePorts({ ports, side, title }) {
  const list = plainArray(ports);
  if (list.length === 0) return <DetailNote label={title} reason={REASON_NO_NODE_IO} />;
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-micro uppercase tracking-wide text-content-secondary">{title}</span>
      {list.map((port, index) => (
        <NodePort
          key={`${side}-${scalarText(plainRecord(port)?.key) ?? String(index)}`}
          port={port}
          side={side}
          index={index}
        />
      ))}
    </div>
  );
}

/** One node of the DAG trace, with its inputs and outputs. */
function NodeCard({ node }) {
  const record = plainRecord(node) ?? {};
  const nodeId = scalarText(record.node_id);
  const source = scalarText(record.source);
  return (
    <div
      data-node-id={nodeId ?? undefined}
      className="flex min-w-0 flex-col gap-2 rounded-sm border border-line-default px-2 py-2"
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-2 text-micro">
        {nodeId === null ? (
          <NotAvailableMarker label="Node id" reason={absentReason(`${NODES_PATH}.node_id`)} />
        ) : (
          <span className="font-mono font-medium text-content-primary">{nodeId}</span>
        )}
        <span className="text-content-secondary">
          {source === null ? 'source not stated' : `read from ${source}`}
        </span>
        {record.is_source_node === true ? (
          <span className="text-content-secondary">emitting node</span>
        ) : null}
      </div>

      <DeclaredFields fields={NODE_FIELDS} record={record} path={NODES_PATH} />

      <NodePorts ports={record.inputs} side="inputs" title="Inputs" />
      <NodePorts ports={record.outputs} side="outputs" title="Outputs" />

      <OtherFields
        record={record}
        known={NODE_KNOWN_KEYS}
        path={NODES_PATH}
        title="Other node fields"
      />
    </div>
  );
}

/** The node trace behind a stage, or the reason there is none to show. */
function NodeList({ nodes, title, emptyReason }) {
  const list = plainArray(nodes);
  return (
    <DetailSection title={title}>
      {list.length === 0 ? (
        <DetailNote label={title} reason={emptyReason} />
      ) : (
        <div className="flex min-w-0 flex-col gap-2">
          {list.map((node, index) => (
            <NodeCard
              key={scalarText(plainRecord(node)?.node_id) ?? `node-${index}`}
              node={node}
            />
          ))}
        </div>
      )}
    </DetailSection>
  );
}

/** The `source` tag every trace section carries, rendered as its own fact. */
function TraceSource({ value, path }) {
  return (
    <DetailField
      label="Trace source"
      value={value}
      reason={absentReason(path)}
    />
  );
}

/* ── Stage 1: market data ──────────────────────────────────────────────── */

/**
 * `signal.market_info`'s named keys: `Signal._market_info()`'s attribution set plus the
 * market facts `_market_facts` reads off the emitting node output. Everything else the
 * record carries — including `reported`, where the emitter volunteered extra named facts —
 * comes through `OtherFields`.
 */
const MARKET_FIELDS = Object.freeze([
  Object.freeze({ key: 'symbol', label: 'Symbol' }),
  Object.freeze({ key: 'timeframe', label: 'Timeframe' }),
  Object.freeze({ key: 'price', label: 'Decision price' }),
  Object.freeze({ key: 'bar_time', label: 'Bar time' }),
  Object.freeze({ key: 'strength', label: 'Strength' }),
  Object.freeze({ key: 'confidence', label: 'Confidence' }),
  Object.freeze({ key: 'signal_type', label: 'Signal type' }),
  Object.freeze({ key: 'side', label: 'Side' }),
  Object.freeze({ key: 'mode', label: 'Mode' }),
  Object.freeze({ key: 'source_node_ids', label: 'Source nodes', wide: true }),
  Object.freeze({ key: 'sizing_intention', label: 'Sizing intention', wide: true }),
]);

const MARKET_PATH = 'signal.market_info';

function MarketDataBody({ stage }) {
  return (
    <>
      <DeclaredFields fields={MARKET_FIELDS} record={stage.detail} path={MARKET_PATH} />
      <OtherFields
        record={stage.detail}
        known={keysOf(MARKET_FIELDS)}
        path={MARKET_PATH}
      />
    </>
  );
}

/* ── Stage 2: indicators ───────────────────────────────────────────────── */

function IndicatorsBody({ stage }) {
  const detail = plainRecord(stage.detail) ?? {};
  return (
    <>
      <FieldGrid>
        <TraceSource value={detail.source} path="trace.dag_nodes.source" />
      </FieldGrid>

      <RecordFields
        record={detail.readings}
        path="signal.indicators"
        title="Indicator readings"
        emptyReason={reasonFor('stage2Indicators') ?? absentReason('signal.indicators')}
      />

      <NodeList
        nodes={detail.nodes}
        title="Node trace"
        emptyReason={REASON_TRACE_RETENTION}
      />
    </>
  );
}

/* ── Stage 3: model ────────────────────────────────────────────────────── */

/**
 * `MLInferenceTrace`'s fields, which is the engine-sourced shape. A row-sourced section
 * carries `ml_info` — an open column — so its keys arrive through `OtherFields`.
 */
const ML_FIELDS = Object.freeze([
  Object.freeze({ key: 'model_id', label: 'Model id' }),
  Object.freeze({ key: 'model_version', label: 'Model version' }),
  Object.freeze({ key: 'prediction', label: 'Prediction' }),
  Object.freeze({ key: 'confidence', label: 'Confidence' }),
  Object.freeze({ key: 'inference_ms', label: 'Inference ms' }),
  Object.freeze({ key: 'status', label: 'Status' }),
  Object.freeze({ key: 'probabilities', label: 'Class probabilities', wide: true }),
  Object.freeze({ key: 'features', label: 'Features', wide: true }),
  Object.freeze({ key: 'feature_vector', label: 'Feature vector', wide: true }),
  Object.freeze({ key: 'error_message', label: 'Error', wide: true }),
]);

const ML_PATH = 'trace.ml_inference.detail';

/**
 * Stage 3's body.
 *
 * The not-applicable arm renders the SECTION rather than a detail grid, because that is what
 * the projection hands over for it: `{source, applicable: false, detail: null}`. There is no
 * inference to lay out, and a grid of ten markers would imply the model's record was lost
 * when the server's statement is that no model was consulted. The state comes from the
 * module; this only reads which one it decided.
 */
function ModelBody({ stage }) {
  if (stage.state === STAGE_NOT_APPLICABLE) {
    const section = plainRecord(stage.detail) ?? {};
    return (
      <FieldGrid>
        <TraceSource value={section.source} path="trace.ml_inference.source" />
        <DetailField
          label="Applies to this strategy"
          value={section.applicable}
          reason={absentReason('trace.ml_inference.applicable')}
        />
      </FieldGrid>
    );
  }

  return (
    <>
      <DeclaredFields fields={ML_FIELDS} record={stage.detail} path={ML_PATH} />
      <OtherFields record={stage.detail} known={keysOf(ML_FIELDS)} path={ML_PATH} />
    </>
  );
}

/* ── Stage 4: logic ────────────────────────────────────────────────────── */

/**
 * Why a `complete` stage 4 can still have no node to show: the projection's third branch,
 * where nodes were retained, none of them is `LOGIC`-category, and the stage stands on
 * `signal.decision` alone. Retention is demonstrably not the explanation there — nodes
 * survived — so the retention sentence would be false.
 */
const REASON_DECISION_ONLY =
  'The retained node trace carries no LOGIC-category node for this signal, so there are no '
  + 'per-node inputs or outputs to show. The decision above is the signal record\u2019s own.';

function LogicBody({ stage }) {
  const detail = plainRecord(stage.detail) ?? {};
  return (
    <>
      <FieldGrid>
        <DetailField
          label={labelFor('listDecision')}
          value={detail.decision}
          reason={absentReason('signal.decision')}
        />
        <TraceSource value={detail.source} path="trace.dag_nodes.source" />
      </FieldGrid>

      <NodeList
        nodes={detail.nodes}
        title="Logic nodes"
        emptyReason={REASON_DECISION_ONLY}
      />
    </>
  );
}

/* ── Stage 5: signal generated ─────────────────────────────────────────── */

/**
 * `SIGNAL_GENERATED.data` is `{decision, indicators, market_info}` — the same two records
 * stages 1 and 2 render in full. Stated rather than dumped a second time: two copies of one
 * record in one timeline read as two records, and a reader comparing them would be looking
 * for a difference that cannot exist (`signal_event_timeline` reads both off the same row).
 */
const REASON_EVENT_REPEATS_SECTIONS =
  'The SIGNAL_GENERATED payload repeats `signal.indicators` and `signal.market_info`, which '
  + 'stages 1 and 2 render in full from the same row. It carries nothing else.';

function SignalBody({ stage }) {
  const detail = plainRecord(stage.detail) ?? {};
  const event = plainRecord(detail.event);
  return (
    <>
      <FieldGrid>
        <DetailField
          label={labelFor('listDecision')}
          value={detail.decision}
          reason={absentReason('signal.decision')}
        />
        <DetailField
          label="Generated at"
          value={event?.timestamp}
          reason={absentReason('timeline[].event=SIGNAL_GENERATED.timestamp')}
        />
      </FieldGrid>

      <DetailSection title="Event payload">
        {event === null ? (
          <DetailNote
            label="SIGNAL_GENERATED"
            reason={absentReason('timeline[].event=SIGNAL_GENERATED')}
          />
        ) : (
          <p className="min-w-0 text-content-secondary">{REASON_EVENT_REPEATS_SECTIONS}</p>
        )}
      </DetailSection>
    </>
  );
}

/* ── Stage 6: order decision (risk validation + order created) ─────────── */

/**
 * The risk verdict's persisted key set — `signal_from_row`'s `risk` dict, which is the row
 * source and the one both sources always have. The engine's `RiskValidationTrace` adds the
 * limit numbers it measured against (`position_limit`, `exposure_pct`, `would_exceed`,
 * `max_drawdown_limit`, `validation_ms`, `blocked`, `block_reason`), and those arrive
 * through `OtherFields` — present on an engine-sourced section, absent on a row-sourced one,
 * with no scaffolding either way.
 */
const RISK_FIELDS = Object.freeze([
  Object.freeze({ key: 'passed', label: 'Risk passed' }),
  Object.freeze({ key: 'position_size', label: 'Position size' }),
  Object.freeze({ key: 'capital', label: 'Capital' }),
  Object.freeze({ key: 'exposure', label: 'Exposure' }),
  Object.freeze({ key: 'expected_loss', label: 'Expected loss' }),
  Object.freeze({ key: 'expected_reward', label: 'Expected reward' }),
  Object.freeze({ key: 'drawdown_check', label: 'Drawdown check' }),
  Object.freeze({ key: 'evaluated_at', label: 'Evaluated at' }),
  Object.freeze({ key: 'reason', label: 'Reason', wide: true }),
]);

const RISK_PATH = 'trace.risk_validation.detail';

/** The risk keys rendered by name or as the check list, so `OtherFields` does not repeat them. */
const RISK_KNOWN_KEYS = Object.freeze([...keysOf(RISK_FIELDS), 'checks']);

/**
 * FINDING, and why each check's verdict is a marker.
 *
 * §10.3 asks for "each risk check with its verdict". `RiskValidationTrace.checks` is a
 * `List[str]` — the NAMES of the checks performed — and the verdict fields (`passed`,
 * `blocked`, `block_reason`) are one verdict over the whole set. So the per-check verdict is
 * not a field the backend produces. Repeating the overall verdict beside each name would
 * claim nine results the server never recorded, so each name renders with the marker and
 * this sentence instead.
 */
const REASON_CHECK_VERDICT_UNREPORTED =
  'The risk section names the checks it performed and reports one verdict over the whole '
  + 'set, so there is no per-check result recorded. The risk verdict above is that verdict.';

function RiskChecks({ checks }) {
  const names = plainArray(checks).map(detailText).filter((name) => name !== null);
  const title = 'Checks performed';
  return (
    <DetailSection title={title}>
      {names.length === 0 ? (
        <DetailNote label={title} reason={absentReason(`${RISK_PATH}.checks`)} />
      ) : (
        <div className="flex min-w-0 flex-col gap-1">
          {names.map((name) => (
            <span
              key={name}
              className="flex min-w-0 flex-wrap items-baseline gap-2 font-mono text-micro"
            >
              <span className="text-content-primary">{name}</span>
              <span aria-hidden="true" className="text-content-muted">·</span>
              <NotAvailableMarker
                label={`${name} verdict`}
                reason={REASON_CHECK_VERDICT_UNREPORTED}
              />
            </span>
          ))}
        </div>
      )}
    </DetailSection>
  );
}

/** `RISK_EVALUATED.data` and `ORDER_CREATED.data`, which are the row's own columns. */
const RISK_EVENT_FIELDS = Object.freeze([
  Object.freeze({ key: 'risk_passed', label: 'Risk passed' }),
  Object.freeze({ key: 'position_size', label: 'Position size' }),
  Object.freeze({ key: 'risk_reason', label: 'Risk reason', wide: true }),
]);

const ORDER_EVENT_FIELDS = Object.freeze([
  Object.freeze({ key: 'order_id', label: 'Order id' }),
  Object.freeze({ key: 'quantity', label: 'Quantity' }),
]);

/** One timeline event's `data`, plus the timestamp the event was recorded at. */
function EventDetail({ event, fields, name, title }) {
  const record = plainRecord(event);
  const path = `timeline[].event=${name}`;
  return (
    <DetailSection title={title}>
      {record === null ? (
        <DetailNote label={name} reason={absentReason(path)} />
      ) : (
        <>
          <FieldGrid>
            <DetailField
              label="Recorded at"
              value={record.timestamp}
              reason={absentReason(`${path}.timestamp`)}
            />
          </FieldGrid>
          <DeclaredFields fields={fields} record={record.data} path={`${path}.data`} />
          <OtherFields
            record={record.data}
            known={keysOf(fields)}
            path={`${path}.data`}
            title={`Other ${name} fields`}
          />
        </>
      )}
    </DetailSection>
  );
}

function OrderDecisionBody({ stage }) {
  const detail = plainRecord(stage.detail) ?? {};
  return (
    <>
      <FieldGrid>
        <TraceSource value={detail.source} path="trace.risk_validation.source" />
      </FieldGrid>

      <DeclaredFields fields={RISK_FIELDS} record={detail.risk} path={RISK_PATH} />
      <RiskChecks checks={plainRecord(detail.risk)?.checks} />
      <OtherFields record={detail.risk} known={RISK_KNOWN_KEYS} path={RISK_PATH} />

      <EventDetail
        event={detail.risk_event}
        fields={RISK_EVENT_FIELDS}
        name="RISK_EVALUATED"
        title="Risk evaluated"
      />
      <EventDetail
        event={detail.order_event}
        fields={ORDER_EVENT_FIELDS}
        name="ORDER_CREATED"
        title="Order created"
      />
    </>
  );
}

/* ── Stages 7 and 8: submission and execution ──────────────────────────── */

/** `EXCHANGE_RESPONSE.data` — the two columns the row records for the venue's answer. */
const SUBMISSION_EVENT_FIELDS = Object.freeze([
  Object.freeze({ key: 'exchange_order_id', label: 'Exchange order id' }),
  Object.freeze({ key: 'order_status', label: 'Order status' }),
]);

/**
 * The submission half of `trace.execution.exchange_response` — the engine's `ExecutionTrace`
 * as it was at submission. Reported ALONGSIDE the outcome and never merged into it, because
 * on a signal whose fills arrived late the two legitimately disagree; the labels say which
 * is which.
 */
const EXCHANGE_SUBMISSION_FIELDS = Object.freeze([
  Object.freeze({ key: 'exchange', label: 'Exchange' }),
  Object.freeze({ key: 'order_id', label: 'Order id' }),
  Object.freeze({ key: 'status', label: 'Status' }),
  Object.freeze({ key: 'order_type', label: 'Order type' }),
  Object.freeze({ key: 'signal_type', label: 'Side' }),
  Object.freeze({ key: 'symbol', label: 'Symbol' }),
  Object.freeze({ key: 'requested_size', label: 'Requested size' }),
  Object.freeze({ key: 'requested_price', label: 'Requested price' }),
  Object.freeze({ key: 'submission_time', label: 'Submitted at' }),
  Object.freeze({ key: 'response_time', label: 'Responded at' }),
  Object.freeze({ key: 'exchange_latency_ms', label: 'Exchange latency ms' }),
  Object.freeze({ key: 'error_code', label: 'Error code' }),
  Object.freeze({ key: 'error_message', label: 'Error', wide: true }),
]);

/** The fill half of the same record, rendered at stage 8 where the fill belongs. */
const EXCHANGE_FILL_FIELDS = Object.freeze([
  Object.freeze({ key: 'filled_size', label: 'Filled size' }),
  Object.freeze({ key: 'filled_price', label: 'Filled price' }),
  Object.freeze({ key: 'fill_percent', label: 'Fill percent' }),
  Object.freeze({ key: 'fees', label: 'Fees' }),
  Object.freeze({ key: 'slippage', label: 'Slippage' }),
  Object.freeze({ key: 'total_latency_ms', label: 'Total latency ms' }),
]);

const EXCHANGE_PATH = 'trace.execution.exchange_response';

/**
 * Why the engine's submission record can be absent on a signal that was submitted.
 * `_execution_section` reports `exchange_response: null` when no engine record survives —
 * the same hour's retention stage 4 depends on — and the row's own timeline event is then
 * the whole record of what the venue said.
 */
const REASON_NO_EXCHANGE_RESPONSE =
  'No engine-observed exchange response is retained for this signal, so the timeline event '
  + 'above is the whole record of the venue\u2019s answer. The trace store keeps the engine\u2019s '
  + 'record for about an hour.';

function SubmissionBody({ stage }) {
  const detail = plainRecord(stage.detail) ?? {};
  const exchange = plainRecord(detail.exchange_response);
  return (
    <>
      <EventDetail
        event={detail.event}
        fields={SUBMISSION_EVENT_FIELDS}
        name="EXCHANGE_RESPONSE"
        title="Exchange response"
      />

      <DetailSection title="Engine record at submission">
        {exchange === null ? (
          <DetailNote label="Exchange response" reason={REASON_NO_EXCHANGE_RESPONSE} />
        ) : (
          <DeclaredFields
            fields={EXCHANGE_SUBMISSION_FIELDS}
            record={exchange}
            path={EXCHANGE_PATH}
          />
        )}
      </DetailSection>
    </>
  );
}

/** `_execution_outcome_of`'s key set — the row's own columns, always all present. */
const OUTCOME_FIELDS = Object.freeze([
  Object.freeze({ key: 'order_id', label: 'Order id' }),
  Object.freeze({ key: 'execution_id', label: 'Execution id' }),
  Object.freeze({ key: 'execution_price', label: 'Execution price' }),
  Object.freeze({ key: 'filled_quantity', label: 'Filled quantity' }),
  Object.freeze({ key: 'remaining_quantity', label: 'Remaining quantity' }),
  Object.freeze({ key: 'fees', label: 'Fees' }),
  Object.freeze({ key: 'slippage', label: 'Slippage' }),
  Object.freeze({ key: 'latency_ms', label: 'Latency ms' }),
  Object.freeze({ key: 'pnl', label: 'P&L' }),
  Object.freeze({ key: 'realized_pnl', label: 'Realised P&L' }),
  Object.freeze({ key: 'order_updated_at', label: 'Order updated at' }),
  Object.freeze({ key: 'executed_at', label: 'Executed at' }),
  Object.freeze({ key: 'failure_reason', label: 'Failure reason', wide: true }),
]);

const OUTCOME_PATH = 'trace.execution.outcome';

/** `EXECUTED.data`. */
const EXECUTED_EVENT_FIELDS = Object.freeze([
  Object.freeze({ key: 'trade_id', label: 'Trade id' }),
  Object.freeze({ key: 'pnl', label: 'P&L' }),
]);

/** Everything either stage renders off the engine record, so neither repeats the other. */
const EXCHANGE_KNOWN_KEYS = Object.freeze([
  ...keysOf(EXCHANGE_SUBMISSION_FIELDS),
  ...keysOf(EXCHANGE_FILL_FIELDS),
]);

function ExecutionBody({ stage }) {
  const detail = plainRecord(stage.detail) ?? {};
  const exchange = plainRecord(detail.exchange_response);
  return (
    <>
      <DetailSection title="Execution outcome">
        <DeclaredFields fields={OUTCOME_FIELDS} record={detail.outcome} path={OUTCOME_PATH} />
      </DetailSection>

      <EventDetail
        event={detail.event}
        fields={EXECUTED_EVENT_FIELDS}
        name="EXECUTED"
        title="Executed"
      />

      <DetailSection title="Engine fill record">
        {exchange === null ? (
          <DetailNote label="Fill detail" reason={REASON_NO_EXCHANGE_RESPONSE} />
        ) : (
          <>
            <DeclaredFields
              fields={EXCHANGE_FILL_FIELDS}
              record={exchange}
              path={EXCHANGE_PATH}
            />
            <OtherFields
              record={exchange}
              known={EXCHANGE_KNOWN_KEYS}
              path={EXCHANGE_PATH}
              title="Other exchange response fields"
            />
          </>
        )}
      </DetailSection>
    </>
  );
}

/* ── Stage 9: position update (BC-6) ───────────────────────────────────── */

const POSITION_PATH = 'timeline[].event=POSITION_UPDATED.data';

/**
 * `_position_updated_event`'s `data` keys. The event reports the position CHANGE, which is
 * why `resulting_position` is in the list and is always `null`: no column in this domain
 * carries the absolute holding a signal left behind.
 */
const POSITION_FIELDS = Object.freeze([
  Object.freeze({ key: 'symbol', label: 'Symbol' }),
  Object.freeze({ key: 'direction', label: 'Direction' }),
  Object.freeze({ key: 'quantity_delta', label: 'Quantity change' }),
  Object.freeze({ key: 'average_price', label: 'Average price' }),
  Object.freeze({ key: 'trade_id', label: 'Trade id' }),
  Object.freeze({ key: 'realized_pnl', label: 'Realised P&L' }),
]);

/** The keys rendered by name or as the server's own absence list. */
const POSITION_KNOWN_KEYS = Object.freeze([
  ...keysOf(POSITION_FIELDS),
  'resulting_position',
  'not_available',
  'not_available_reason',
]);

/**
 * Stage 9's body, and the one place the page renders a SERVER-SUPPLIED absence reason.
 *
 * `data.not_available` names the transition fields the row did not report, plus
 * `resulting_position`, and `data.not_available_reason` says why in one sentence
 * (`signal_service.POSITION_RESULTING_UNREPORTED_REASON`). Both are surfaced VERBATIM: the
 * list is rendered as the server spelled it, and the sentence is passed to every marker for
 * a listed field instead of this page's own `absentReason`. `pageFields`' `resultingPosition`
 * reason is the fallback for a payload that omits the server's, and it says so there — two
 * independent sentences for one fact is how they drift.
 */
function PositionUpdateBody({ stage }) {
  const data = plainRecord(stage.detail) ?? {};
  const unavailable = plainArray(data.not_available)
    .map(scalarText)
    .filter((field) => field !== null);
  const serverReason = scalarText(data.not_available_reason);
  const listed = (key) => (unavailable.includes(key) ? serverReason : null);

  return (
    <>
      <FieldGrid>
        {POSITION_FIELDS.map((field) => (
          <DetailField
            key={field.key}
            label={field.label}
            value={data[field.key]}
            reason={listed(field.key) ?? absentReason(`${POSITION_PATH}.${field.key}`)}
          />
        ))}
        <DetailField
          label={labelFor('resultingPosition')}
          value={data.resulting_position}
          reason={
            listed('resulting_position')
            ?? serverReason
            ?? reasonFor('resultingPosition')
            ?? absentReason(`${POSITION_PATH}.resulting_position`)
          }
          wide
        />
      </FieldGrid>

      <DetailSection title="Reported as not available by the server">
        {unavailable.length === 0 ? (
          <DetailNote
            label="Not available"
            reason={absentReason(`${POSITION_PATH}.not_available`)}
          />
        ) : (
          <div className="flex min-w-0 flex-col gap-1">
            <span className="flex min-w-0 flex-wrap gap-2 font-mono text-micro text-content-primary">
              {unavailable.map((field) => (
                <span key={field}>{field}</span>
              ))}
            </span>
            {serverReason === null ? (
              <NotAvailableMarker
                label="Not available reason"
                reason={absentReason(`${POSITION_PATH}.not_available_reason`)}
              />
            ) : (
              <span className="min-w-0 text-content-secondary">{serverReason}</span>
            )}
          </div>
        )}
      </DetailSection>

      <OtherFields record={data} known={POSITION_KNOWN_KEYS} path={POSITION_PATH} />
    </>
  );
}

/* ── The nine bodies, keyed by canonical stage id ──────────────────────── */

/**
 * Canonical id → the renderer for its expanded region.
 *
 * Keyed by id for the same reason `STAGE_FIELD` is: a stage with no body is a missing key
 * rather than an off-by-one, and the map cannot drift out of step with the projection's
 * order.
 */
const STAGE_BODY = Object.freeze({
  [STAGE_MARKET_DATA]: MarketDataBody,
  [STAGE_INDICATORS]: IndicatorsBody,
  [STAGE_MODEL]: ModelBody,
  [STAGE_LOGIC]: LogicBody,
  [STAGE_SIGNAL]: SignalBody,
  [STAGE_ORDER_DECISION]: OrderDecisionBody,
  [STAGE_SUBMISSION]: SubmissionBody,
  [STAGE_EXECUTION]: ExecutionBody,
  [STAGE_POSITION_UPDATE]: PositionUpdateBody,
});

/**
 * The technical detail for one stage, or the one sentence that says why there is none.
 *
 * A stage the projection resolved with no backing record has `detail === null` — swept by
 * retention, not yet reached, refused upstream, or not applicable to the strategy — and the
 * body then carries the module's own reason and nothing else. It does NOT render the field
 * grid with every cell marked: scaffolding for a record that does not exist reads as a
 * record that arrived empty, which is a different fact and a worse one.
 *
 * @param {Object} props
 * @param {Object} props.stage One entry of `buildSignalTraceStages(...).stages`.
 * @param {string|undefined} props.declaredReason `pageFields`' reason for this stage.
 */
function StageDetail({ stage, declaredReason }) {
  const Body = STAGE_BODY[stage.id] ?? null;
  const label = `${stage.name} detail`;

  if (Body === null || plainRecord(stage.detail) === null) {
    return (
      <DetailNote
        label={label}
        reason={stage.reason ?? declaredReason ?? absentReason(`this trace\u2019s ${stage.name}`)}
      />
    );
  }

  return (
    <div data-stage-detail={stage.id} className="flex min-w-0 flex-col gap-2">
      <Body stage={stage} />
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * ONE STAGE ROW — COLLAPSED, AND INDEPENDENT (Requirement 9.3, §10.3)
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * One stage: a `<button aria-expanded>` and the region it controls.
 *
 * The independence is structural rather than remembered. The button controls exactly one
 * region, that region's id is derived from this row's own `useId`, and the only thing the
 * click does is flip this row's own key in the parent's record. There is no shared "open
 * row" value, so there is no state in which expanding one row can collapse another
 * (Property 17). The region is always in the DOM and is hidden with the `hidden` attribute
 * rather than conditionally rendered, so `aria-controls` always names an element that
 * exists.
 *
 * WHAT THE REGION CARRIES (part (b), task 21.4b)
 * ---------------------------------------------
 * The stage's state, the server's reason, the human summary, and then the technical detail
 * §10.3 asks for: `market_info`'s fields, the raw indicator readings and every `dag_nodes`
 * node with its per-port inputs and outputs, the ML confidence and model id, the LOGIC
 * nodes and the decision, each named risk check, the exchange response fields, the fill, and
 * stage 9's position change with the server's `not_available` list and `not_available_reason`
 * verbatim. All of it off `stage.detail` — see `StageDetail` above.
 *
 * @param {Object} props
 * @param {Object} props.stage One entry of `buildSignalTraceStages(...).stages`.
 * @param {boolean} props.expanded
 * @param {(id: string) => void} props.onToggle
 */
function StageRow({ stage, expanded, onToggle }) {
  const rowId = useId();
  const summaryId = `${rowId}-summary`;
  const regionId = `${rowId}-region`;
  const presentation = presentationForState(stage.state);
  const { fg } = statusToken(presentation.token);

  // The summary line, in order of what is most specific about THIS stage: what the server
  // recorded, then why there is nothing recorded, then the state in words. Never blank.
  const summary = stage.summary ?? stage.reason ?? presentation.word;
  const declaredReason = reasonFor(STAGE_FIELD[stage.id]);

  return (
    <div
      data-stage-id={stage.id}
      data-stage-state={stage.state}
      data-stage-number={String(stage.number)}
      className="flex min-w-0 items-stretch gap-3"
    >
      {/* The rail. Decorative: the word in the region and the marker's own accessible
          text both carry the state, so a border cannot be the only channel. */}
      <span
        aria-hidden="true"
        className="w-0 shrink-0"
        style={{ borderLeftWidth: 2, borderLeftStyle: presentation.stroke, borderLeftColor: fg }}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <button
          type="button"
          id={summaryId}
          aria-expanded={expanded}
          aria-controls={regionId}
          onClick={() => onToggle(stage.id)}
          className="flex min-w-0 items-center gap-3 rounded-sm px-2 py-2 text-left text-body text-content-secondary transition-colors hover:bg-surface-raised"
        >
          {expanded ? (
            <ChevronDown size={13} strokeWidth={2} aria-hidden="true" className="shrink-0" />
          ) : (
            <ChevronRight size={13} strokeWidth={2} aria-hidden="true" className="shrink-0" />
          )}

          {/* The marker. `aria-hidden` because the row already announces its state in
              words through the region below and through `data-stage-state`. */}
          <span
            aria-hidden="true"
            className={presentation.muted ? 'shrink-0 text-content-muted' : 'shrink-0'}
            style={presentation.muted ? undefined : { color: fg }}
          >
            {presentation.glyph}
          </span>

          <span className="w-4 shrink-0 text-right font-mono text-content-secondary">
            {stage.number}
          </span>
          <span className="w-40 shrink-0 truncate font-medium text-content-primary">
            {stage.name}
          </span>
          <span className="min-w-0 flex-1 truncate" title={summary}>
            {summary}
          </span>
          <span className="shrink-0 text-micro">
            <StageLatency stage={stage} />
          </span>
        </button>

        <div
          id={regionId}
          role="region"
          aria-labelledby={summaryId}
          hidden={!expanded}
          className="flex min-w-0 flex-col gap-2 px-2 pb-3 pl-10 text-body"
        >
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-micro text-content-secondary">State</span>
            <StatusBadge state={stage.state} label={presentation.word} />
          </div>

          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-micro text-content-secondary">Summary</span>
            {stage.summary === null ? (
              <NotAvailableMarker
                label={`${stage.name} summary`}
                reason={stage.reason ?? declaredReason}
              />
            ) : (
              <span className="text-content-primary">{stage.summary}</span>
            )}
          </div>

          {/* The server's own sentence, verbatim. `signalTraceStages` supplies it for every
              state that has one — the retention window, the absent LOGIC node, the stage
              that ended the lifecycle, the venue's rejection — and this page neither
              restates nor summarises it. `pageFields`' declared reason is the fallback for
              a state that carries none, so the row is never silent about an absence. */}
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-micro text-content-secondary">Reason</span>
            {stage.reason === null && declaredReason === undefined ? (
              <NotAvailableMarker label={`${stage.name} reason`} />
            ) : (
              <span className="text-content-secondary">{stage.reason ?? declaredReason}</span>
            )}
          </div>

          {/* The technical detail — the whole point of expanding a row. */}
          <StageDetail stage={stage} declaredReason={declaredReason} />
        </div>
      </div>
    </div>
  );
}

/**
 * The nine rows, plus the degraded note above them.
 *
 * Rendered from `stages` exactly as the projection hands it over: no filter, no sort, no
 * `slice`. That is what makes Requirement 9.1's "in order" and 9.2's "rather than omitting
 * it" hold on screen and not merely in the module — a page that filtered this array could
 * still drop a row the module was careful to keep.
 *
 * Expansion lives in ONE record keyed by stage id. A record rather than an "open row" makes
 * the independence Requirement 9.3 asks for a property of the data structure.
 *
 * @param {Object} props
 * @param {ReadonlyArray<Object>} props.stages
 * @param {{message: string, lifecycleStateSource: string|null}|null} props.degraded
 */
function SignalTimeline({ stages, degraded }) {
  const [expanded, setExpanded] = useState(() => ({}));

  const toggle = useCallback((id) => {
    // Only this id's own entry is touched, and it is toggled on its own previous value —
    // so a row's state is the parity of its own activations and nothing else.
    setExpanded((previous) => ({ ...previous, [id]: previous[id] !== true }));
  }, []);

  return (
    <div className="flex min-w-0 flex-col gap-3">
      {/* §10.3: a real server signal. Hiding it would misrepresent how complete the trace
          is, so it sits above the timeline rather than inside one row. `Alert` at
          `warning` is `semantic.js`'s `status.warning` surface. */}
      {degraded === null ? null : (
        <Alert severity="warning" variant="block" title={degraded.message}>
          {degraded.lifecycleStateSource === null
            ? null
            : `Lifecycle state read from ${degraded.lifecycleStateSource}.`}
        </Alert>
      )}

      <div className="flex min-w-0 flex-col">
        {stages.map((stage) => (
          <StageRow
            key={stage.id}
            stage={stage}
            expanded={expanded[stage.id] === true}
            onToggle={toggle}
          />
        ))}
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE LIST
 * ══════════════════════════════════════════════════════════════════════════ */

/** Requirement 17.5's page size, which is also the endpoint's own maximum. */
const PAGE_SIZE = 50;

/** The filters this page sends, with the spellings `signal_trace.list_signals` declares. */
const DEFAULT_FILTERS = Object.freeze({
  symbol: '',
  decision: '',
  status: '',
  date_from: '',
  date_to: '',
});

/** `signal.decision`'s vocabulary, as the server's own `decision` filter accepts it. */
const DECISION_OPTIONS = Object.freeze([
  Object.freeze({ value: '', label: 'Any decision' }),
  Object.freeze({ value: 'BUY', label: 'BUY' }),
  Object.freeze({ value: 'SELL', label: 'SELL' }),
  Object.freeze({ value: 'EXIT', label: 'EXIT' }),
  Object.freeze({ value: 'CLOSE', label: 'CLOSE' }),
  Object.freeze({ value: 'HOLD', label: 'HOLD' }),
]);

/** `public.signals.status`, the vocabulary the `status` filter is documented against. */
const OUTCOME_OPTIONS = Object.freeze([
  Object.freeze({ value: '', label: 'Any outcome' }),
  Object.freeze({ value: 'pending', label: 'Pending' }),
  Object.freeze({ value: 'accepted', label: 'Accepted' }),
  Object.freeze({ value: 'rejected', label: 'Rejected' }),
  Object.freeze({ value: 'executed', label: 'Executed' }),
  Object.freeze({ value: 'failed', label: 'Failed' }),
  Object.freeze({ value: 'cancelled', label: 'Cancelled' }),
  Object.freeze({ value: 'expired', label: 'Expired' }),
]);

/**
 * The `environment` filter's options.
 *
 * The three values are `ENVIRONMENT_IDS` — `semantic.js`'s own list, which is the same
 * three `backend/execution_environment.py` enumerates and the same three the endpoint's
 * `ENVIRONMENT_FILTER_VALUES` is built from. Naming them here would be a fourth copy.
 */
const ENVIRONMENT_OPTIONS = Object.freeze([
  Object.freeze({ value: '', label: 'All environments' }),
  ...ENVIRONMENT_IDS.map((id) => Object.freeze({ value: id, label: id })),
]);

/** What the count region is counting. */
const COUNT_NOUN = 'signals';

/**
 * A non-empty trimmed string, or `null`. The one reader this page uses for a row field, so
 * `''`, `'   '` and a non-string all reach the not-available marker by the same route.
 */
const text = (value) => {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
};

/** One row as the five cells §10.3's table reads. Nothing is defaulted. */
const projectSignal = (row) => ({
  id: row?.id,
  time: text(row?.generated_at),
  strategy: text(row?.strategy_id),
  version: text(row?.strategy_version),
  market: text(row?.symbol),
  decision: text(row?.decision),
  outcome: text(row?.status),
});

/** A cell that renders the declared reason when the row does not carry the field. */
const markerCell = (field) => {
  const label = labelFor(field);
  const reason = reasonFor(field);
  return function MarkerCell() {
    return <NotAvailableMarker label={label} reason={reason} />;
  };
};

/** The strategy id, with the version beside it where the row reports one. */
function StrategyCell({ value, observed }) {
  if (value === null) return markerCell('listStrategy')();
  const version = observed?.version ?? null;
  return (
    <span className="font-mono">
      {value}
      {version === null ? null : (
        <span className="text-content-secondary">{` v${version}`}</span>
      )}
    </span>
  );
}

/**
 * A decision or an outcome, as a chip.
 *
 * `label={value}` keeps the SERVER'S spelling in the DOM — `executed`, not `Executed`. The
 * chip uppercases visually through CSS, so nothing is lost on screen, and the text a
 * screen reader reads and an export carries is the value the server actually recorded.
 */
const chipCell = (field) => {
  const label = labelFor(field);
  const reason = reasonFor(field);
  return function ChipCell({ value }) {
    if (value === null) return <NotAvailableMarker label={label} reason={reason} />;
    return <StatusBadge state={value} label={value} />;
  };
};

/* ══════════════════════════════════════════════════════════════════════════
 * THE PAGE
 * ══════════════════════════════════════════════════════════════════════════ */

export default function SignalTrace() {
  const navigate = useNavigate();
  const { signalId } = useParams();
  const [searchParams] = useSearchParams();

  /*
    Requirement 17.4's entry path: the Strategies page links to
    `/app/signal-trace?strategy_id=…`, and that filter has to be in force on the FIRST read
    rather than applied by an effect afterwards — an effect would issue one unfiltered read
    first, and its result would be on screen under a header claiming a strategy filter.
    Reading `searchParams` in the initialiser is what makes the first read the right one.
  */
  const [strategyId, setStrategyId] = useState(() => searchParams.get('strategy_id') ?? '');
  const [environment, setEnvironment] = useState('');
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState(null);
  const [showPipeline, setShowPipeline] = useState(false);

  const offset = (page - 1) * PAGE_SIZE;

  /*
    The query string, built once. It is BOTH the request and the read's identity: `deps`
    below is this one string, so a read is re-issued exactly when the question changes and
    an answer to an older question can never be on screen as though it answered this one.
  */
  const query = useMemo(() => {
    const params = new URLSearchParams();
    if (strategyId !== '') params.append('strategy_id', strategyId);
    if (environment !== '') params.append('environment', environment);
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== '') params.append(key, value);
    });
    if (search !== '') params.append('search', search);
    params.append('limit', String(PAGE_SIZE));
    params.append('offset', String(offset));
    return params.toString();
  }, [strategyId, environment, filters, search, offset]);

  const listReader = useCallback(() => get(`/api/signal-trace/signals?${query}`), [query]);

  /*
    The detail route (`/app/signal-trace/:signalId`) fixes the signal; on the list route the
    selected row does. One read either way, so the timeline below is written once.
  */
  const detailId = signalId ?? selectedId;

  /*
    Both reads are destructured rather than held as objects, because `refetch` is what
    `onSnapshot` hands to the realtime hook: `usePanelState` guarantees a STABLE `refetch`
    (that is inversion 2 of the three it makes over `usePolling`), and reading it off a
    fresh object every render would throw that guarantee away at the one call site that
    depends on it.
  */
  const {
    state: listState,
    data: listData,
    error: listError,
    refetch: refetchList,
  } = usePanelState(listReader, { deps: [query], enabled: !signalId });

  const detailReader = useCallback(
    () => get(`/api/signal-trace/signals/${detailId}`),
    [detailId],
  );
  const {
    state: detailState,
    data: detailData,
    error: detailError,
    refetch: refetchDetail,
  } = usePanelState(detailReader, {
    deps: [detailId],
    enabled: typeof detailId === 'string' && detailId !== '',
  });

  const served = useMemo(() => {
    const signals = listData?.signals;
    return Array.isArray(signals) ? signals : [];
  }, [listData]);

  const total = Number.isFinite(listData?.total) ? listData.total : served.length;

  /*
    ── Realtime (tasks 19.1, 19.2) ────────────────────────────────────────────────────────

    The deployments watched come from two places, unioned by the hook. The strategy's own
    deployment listing is the primary source and is what makes Requirement 17.4's
    strategy-filtered entry live without further user action; the `deployment_id`s the
    loaded signals name are the second, because that listing is served from an in-process
    registry that does not contain a deployment started through `deploy_version`, and
    because an unfiltered page has no strategy to ask about at all.

    `onSnapshot` is Requirement 18.6's "request a snapshot rather than assuming no updates
    were missed": on a reconnect the whole current page is re-read, since the frames
    between the drop and the reconnect were not delivered to anyone. The read's promise is
    RETURNED, which is what lets the hook drop the pushed state that read supersedes
    (task 19.2).

    ── Why this read is NOT `?deployment_id=…&since=<last_known_seq>` ──
    Two findings, both about the server rather than about this page:

    1. `GET /api/signal-trace/signals` HAS NO `since` PARAMETER, and FastAPI ignores a
       query parameter a handler does not declare — so sending one would narrow nothing
       while making this page's code claim it had.
    2. `seq` IS NOT A CURSOR INTO HISTORY. It is a per-channel in-process counter that is
       deliberately restarted at 1 for a channel's first subscriber, so a pre-drop `seq`
       names no position the server could resolve after a reconnect.

    Narrowing the snapshot by `deployment_id` was considered and rejected: a signal row
    with no `deployment_id`, and any row outside the watched set, would silently vanish
    from a page that had been showing it.
  */
  const signalDeploymentIds = useMemo(() => deploymentIdsFromSignals(served), [served]);

  const requestSnapshot = useCallback(
    () => (signalId ? refetchDetail() : refetchList()),
    [signalId, refetchDetail, refetchList],
  );

  const {
    signals: pushedSignals,
    refusals,
    deploymentsError,
    status: connectionStatus,
    channels: heldChannels,
  } = useSignalTraceRealtime({
    strategyId,
    deploymentIds: signalDeploymentIds,
    onSnapshot: requestSnapshot,
  });

  /*
    The rendered rows are the server's page with every pushed update applied IN PLACE, so
    the descending generation-time order the backend sorted by is never re-derived here.
    A pushed signal that is not on this page is reported rather than inserted — the client
    cannot decide whether it satisfies a thirteen-category server-side query — and the
    affordance offered for it is the same snapshot read.
  */
  const rows = useMemo(() => mergeRealtimeSignals(served, pushedSignals), [served, pushedSignals]);
  const newSignalIds = useMemo(
    () => unlistedSignalIds(served, pushedSignals),
    [served, pushedSignals],
  );

  const displayRows = useMemo(() => rows.map(projectSignal), [rows]);

  /*
    ── The environment badge reads the SERVER, never the select above (§8.1) ──

    `environment_source` says whether migration 010's column answered at all, and
    `count_by_environment` is the page's own aggregate — with `UNLABELLED` for a row the
    column could not label. So the badge states an environment only when the server labelled
    every row in view the same way, and reports `null` — "ENVIRONMENT UNCONFIRMED" — for an
    unlabelled row, for a mixed page, and for a database without 010. Reading it off the
    select would make the badge say whatever the trader had just clicked.
  */
  const pageEnvironment = useMemo(() => {
    if (listData?.environment_source !== 'column') return null;
    const counts = listData?.count_by_environment;
    if (counts === null || typeof counts !== 'object') return null;
    const labelled = Object.entries(counts).filter(([, count]) => count > 0).map(([name]) => name);
    return labelled.length === 1 ? text(labelled[0]) : null;
  }, [listData]);

  /* ── The nine stages (the whole point of the page) ───────────────────── */

  const trace = useMemo(() => buildSignalTraceStages(detailData), [detailData]);

  /* ── Filters ────────────────────────────────────────────────────────── */

  const filterControls = useMemo(() => [
    {
      id: 'symbol',
      label: labelFor('listMarket'),
      kind: 'text',
      placeholder: 'BTC/USDT',
    },
    { id: 'decision', label: labelFor('listDecision'), kind: 'select', options: DECISION_OPTIONS },
    { id: 'status', label: labelFor('listOutcome'), kind: 'select', options: OUTCOME_OPTIONS },
    { id: 'date_from', label: 'From', kind: 'date' },
    { id: 'date_to', label: 'To', kind: 'date' },
  ], []);

  const hasActiveFilters =
    search !== ''
    || environment !== ''
    || Object.values(filters).some((value) => value !== '');

  // Every change that changes the row set returns to page 1: a stale page number would
  // render an empty table over rows that exist.
  const handleFilterChange = useCallback((id, value) => {
    setFilters((previous) => ({ ...previous, [id]: value }));
    setPage(1);
  }, []);

  const handleSearchChange = useCallback((value) => {
    setSearch(value);
    setPage(1);
  }, []);

  const handleStrategyChange = useCallback((event) => {
    setStrategyId(event.target.value);
    setPage(1);
  }, []);

  const handleEnvironmentChange = useCallback((event) => {
    setEnvironment(event.target.value);
    setPage(1);
  }, []);

  const clearFilters = useCallback(() => {
    setFilters(DEFAULT_FILTERS);
    setSearch('');
    setEnvironment('');
    setPage(1);
  }, []);

  /*
    The strategy select offers the strategies this page has actually been shown, plus
    whatever is currently selected. §10.3 draws a strategy dropdown; the honest source for
    its options is the read the page already issues, because nothing on this response names
    a strategy this page has not received a signal for, and inventing a list would mean
    issuing a second read this task does not own. "All strategies" is always present, so
    narrowing to one strategy is never a dead end.
  */
  const strategyOptions = useMemo(() => {
    const seen = new Set(rows.map((row) => text(row?.strategy_id)).filter((id) => id !== null));
    if (strategyId !== '') seen.add(strategyId);
    return [
      { value: '', label: 'All strategies' },
      ...[...seen].sort().map((id) => ({ value: id, label: id })),
    ];
  }, [rows, strategyId]);

  /* ── Export (the third read, unchanged) ─────────────────────────────── */

  const exportSignals = useCallback(async (format) => {
    const params = new URLSearchParams(query);
    params.delete('limit');
    params.delete('offset');
    params.append('format', format);

    const token = sessionStorage.getItem('token');
    const response = await fetch(
      `${CONFIG.apiBaseUrl}/api/signal-trace/signals/export?${params}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} },
    );
    if (!response.ok) throw new Error('Export failed');

    const blob = format === 'csv'
      ? await response.blob()
      : new Blob([JSON.stringify(await response.json(), null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `signal-trace.${format}`;
    anchor.click();
    URL.revokeObjectURL(url);
  }, [query]);

  /* ── Columns (§10.3's five) ─────────────────────────────────────────── */

  const columns = useMemo(() => [
    { key: 'time', header: labelFor('listTime'), format: 'timestamp', priority: 1 },
    { key: 'strategy', header: labelFor('listStrategy'), render: StrategyCell, priority: 2 },
    { key: 'market', header: labelFor('listMarket'), format: 'symbol', priority: 1 },
    { key: 'decision', header: labelFor('listDecision'), render: chipCell('listDecision'), priority: 1 },
    { key: 'outcome', header: labelFor('listOutcome'), render: chipCell('listOutcome'), priority: 1 },
  ], []);

  /* ── Requirement 9.4's empty state ─────────────────────────────────── */

  const noSignals = {
    icon: Waypoints,
    headline: 'No signal traces for this strategy',
    body: 'A trace is produced every time a deployed strategy evaluates market data, so this '
      + 'list fills as soon as one is running. Deploy a strategy, or start a paper session — '
      + 'paper sessions produce traces too.',
    action: { label: 'Review your strategies', to: '/app/strategies' },
  };

  /*
    Which of Requirement 11.5's two empty states applies.

    `ds/FilterBar`'s `emptyVariantFor` compares a filtered count against an unfiltered one,
    and this page never learns the unfiltered total: the filters are applied server-side, so
    `total` is already the filtered figure. The server answers the same question directly —
    `filters_active` is Requirement 17.7's own discriminator — and where it does not, "is
    any control set" is the page's own honest fallback.
  */
  const filtersNarrowed = listData?.filters_active === true || hasActiveFilters;

  const emptyState = filtersNarrowed
    ? {
      ...noSignals,
      variant: 'no-match',
      headline: 'No signals match these filters',
      body: 'The filters and search currently in force exclude every signal this account has '
        + 'recorded. Widen them to see the rows again.',
      clearFiltersAction: { label: 'Clear filters', onClick: clearFilters },
    }
    : noSignals;

  const listBusy = listState === PANEL_STATES.LOADING || listState === PANEL_STATES.REFRESHING;
  const showEmpty = listState === PANEL_STATES.READY && displayRows.length === 0;

  /* ── The header, shared by both routes ─────────────────────────────── */

  const header = (
    <PageHeader
      title="Signal Trace"
      environment={pageEnvironment}
      breadcrumb={signalId ? [
        { label: 'Signal Trace', to: '/app/signal-trace' },
        { label: `Signal ${signalId.slice(0, 8)}` },
      ] : undefined}
      actions={signalId ? (
        <CommandButton
          intent="secondary"
          icon={ArrowLeft}
          onClick={() => navigate('/app/signal-trace')}
        >
          Back to list
        </CommandButton>
      ) : (
        <>
          <Field
            id="signal-trace-strategy"
            label="Strategy"
            type="text"
            options={strategyOptions}
            value={strategyId}
            onChange={handleStrategyChange}
            className="w-44"
          />
          <Field
            id="signal-trace-environment"
            label="Environment"
            type="text"
            options={ENVIRONMENT_OPTIONS}
            value={environment}
            onChange={handleEnvironmentChange}
            className="w-40"
          />
          <CommandButton
            intent="ghost"
            icon={Activity}
            onClick={() => setShowPipeline((shown) => !shown)}
          >
            {showPipeline ? 'Hide pipeline' : 'Live pipeline'}
          </CommandButton>
          <CommandButton
            intent="secondary"
            icon={RefreshCw}
            loading={listBusy}
            loadingLabel="Reading signals"
            onClick={refetchList}
          >
            Refresh
          </CommandButton>
        </>
      )}
    />
  );

  /* ── The timeline panel, shared by both routes ─────────────────────── */

  const timelinePanel = (
    <Panel
      title={detailId === null ? 'Selected signal' : `Signal ${String(detailId).slice(0, 8)}`}
      state={detailState}
      loading={{ kind: 'skeleton-table', rows: 9, columns: 4 }}
      empty={{
        headline: 'This signal has no trace to show',
        body: 'The server answered with no trace body for this signal, so there is nothing to '
          + 'lay out against the nine stages.',
        action: { label: 'Back to the signal list', to: '/app/signal-trace' },
      }}
      error={{ error: detailError, context: 'signals', onRetry: refetchDetail }}
    >
      {/* Keyed by the signal, so switching signals starts every row collapsed again —
          Requirement 9.3 is about first render, and a new signal is a first render. */}
      <SignalTimeline key={detailId} stages={trace.stages} degraded={trace.degraded} />
    </Panel>
  );

  if (signalId) {
    return (
      <div className="flex min-w-0 flex-col gap-4 overflow-y-auto bg-surface-canvas p-5 text-content-primary">
        {header}
        {/* Requirement 18.9 on the detail route too: this view holds the same
            subscriptions, so a connection that drops here stalls this signal's status
            just as silently as it stalls the list. */}
        <SignalConnectionIndicator status={connectionStatus} watching={heldChannels.length} />
        {timelinePanel}
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-4 overflow-y-auto bg-surface-canvas p-5 text-content-primary">
      {header}

      <SignalConnectionIndicator status={connectionStatus} watching={heldChannels.length} />

      {/* Requirement 18.3: a refused subscription is SURFACED. The server keeps the
          connection and answers `subscription_refused` for the channel it would not
          authorise, and a refusal that only reached the console would be indistinguishable
          on screen from a deployment that is simply quiet. A refusal is a DIFFERENT fact
          from the connection being down (one channel of a live connection, versus no
          connection at all), so it stays its own element beside the indicator above. */}
      {refusals.length > 0 ? (
        <Alert
          severity="error"
          variant="block"
          title={`Live updates refused for ${refusals.length} deployment${refusals.length === 1 ? '' : 's'}`}
          data-testid="signal-trace-refusals"
        >
          <span className="flex min-w-0 flex-col gap-1 font-mono text-micro">
            {refusals.map((refusal) => (
              <span key={refusal.channel}>
                {refusal.deploymentId || refusal.channel}
                {refusal.code ? ` — ${refusal.code}` : ''}
                {refusal.reason ? `: ${refusal.reason}` : ''}
              </span>
            ))}
            <span className="text-content-secondary">
              This list is not updating live for those deployments. Refresh to re-read it.
            </span>
          </span>
        </Alert>
      ) : null}

      {/* The deployment list this page composes its subscriptions from could not be read,
          so it does not know every channel to hold. Reported for the same reason as a
          refusal: watching nothing must not look like a strategy with nothing to report. */}
      {deploymentsError ? (
        <Alert
          severity="warning"
          variant="block"
          title="This strategy&rsquo;s deployment list could not be read"
          data-testid="signal-trace-deployments-error"
        >
          Live updates may be incomplete. Signals already listed still update.
        </Alert>
      ) : null}

      {/* The realtime pipeline visualisation, opt-in. It renders its own full-page chrome,
          so it is a collapsible panel rather than part of the layout. Task 21.6 owns its
          palette and its stage list. */}
      {showPipeline ? (
        <div className="max-h-[640px] overflow-hidden overflow-y-auto rounded-md border border-line-default">
          <SignalTraceVisualization wsClient={wsClient} />
        </div>
      ) : null}

      <Panel
        title="Signals"
        state={listState}
        loading={{ kind: 'skeleton-table', rows: 8, columns: columns.length }}
        empty={emptyState}
        error={{ error: listError, context: 'signals', onRetry: refetchList }}
      >
        <div className="flex min-w-0 flex-col gap-3">
          <FilterBar
            filters={filterControls}
            values={filters}
            onChange={handleFilterChange}
            search={search}
            onSearchChange={handleSearchChange}
            searchLabel="Signal id"
            searchPlaceholder="sig_01H…"
            resultCount={displayRows.length}
            totalCount={Math.max(total, displayRows.length)}
            countNoun={COUNT_NOUN}
            actions={(
              <>
                {hasActiveFilters ? (
                  <CommandButton intent="ghost" onClick={clearFilters}>
                    Clear filters
                  </CommandButton>
                ) : null}
                <CommandButton
                  intent="secondary"
                  icon={Download}
                  onClick={() => exportSignals('csv')}
                  disabled={displayRows.length === 0}
                  disabledReason="No signals in view to export."
                >
                  Export CSV
                </CommandButton>
                <CommandButton
                  intent="secondary"
                  icon={Download}
                  onClick={() => exportSignals('json')}
                  disabled={displayRows.length === 0}
                  disabledReason="No signals in view to export."
                >
                  Export JSON
                </CommandButton>
              </>
            )}
          />

          {/* Requirement 18.1: a signal generated on a watched deployment must not be
              invisible until the next manual refresh. It is announced rather than
              inserted, because whether it belongs on this filtered, sorted, paginated page
              is the server's question to answer — and the action offered is that read. */}
          {newSignalIds.length > 0 ? (
            <Alert
              severity="guidance"
              variant="strip"
              title={`${newSignalIds.length} new signal${newSignalIds.length === 1 ? '' : 's'} arrived that this page does not show.`}
              action={{ label: 'Reload the list', onClick: refetchList }}
              data-testid="signal-trace-new-signals"
            />
          ) : null}

          {showEmpty ? (
            <EmptyState {...emptyState} />
          ) : (
            <DataTable
              columns={columns}
              rows={displayRows}
              observe={(row) => ({ version: row.version })}
              totalCount={total}
              page={page}
              pageSize={PAGE_SIZE}
              onPageChange={setPage}
              onRowClick={(row) => setSelectedId(row.id ?? null)}
              stickyHeader
              caption="Signals, newest first"
            />
          )}

          {/* No selection is not an empty panel and not a disabled control: the timeline
              needs one signal, and this says which action produces one. */}
          {detailId === null ? (
            <p className="text-micro text-content-secondary">
              Select a signal to lay its trace out against the nine stages.
            </p>
          ) : null}
        </div>
      </Panel>

      {detailId === null ? null : timelinePanel}
    </div>
  );
}
