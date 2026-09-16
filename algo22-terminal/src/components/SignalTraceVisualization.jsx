/**
 * ═══════════════════════════════════════════════════════════════════════════
 * SignalTraceVisualization — the LIVE pipeline view (vyomquant-ui-redesign 21.6)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * design.md §10.1, §10.2, §1.1 G5. Requirements 1.1, 1.4, 9.1, 9.2, 14.5, 19.3.
 *
 * WHAT THIS SURFACE IS, AND WHY IT IS NOT `pages/SignalTrace.jsx`
 * ==============================================================
 * `pages/SignalTrace.jsx` reads one signal from `GET /api/signal-trace/signals/{id}` and
 * projects it through `lib/signalTraceStages.js`. THIS component reads a live stream —
 * `WS_CHANNELS.SIGNAL_TRACE` and `WS_CHANNELS.RISK_EVENTS` — and shows the last N frames
 * as they arrive. Two different sources, two different surfaces, deliberately (the page
 * renders this one behind its `Live pipeline` toggle).
 *
 * The frame is `signal_trace_engine.SignalTraceRecord.to_frontend_format()`: `{id,
 * signal_id, strategy, symbol, timestamp, status, final_decision, latency_ms, pipeline,
 * errors}`, where `pipeline` is a list of SIX entries keyed by a lowercase `stage` name —
 * `market_data`, `indicators`, `dag_nodes`, `ml_inference`, `risk_validation`,
 * `execution`. Because the frame shape is the engine's and not the REST body's,
 * `buildSignalTraceStages` cannot be handed a frame: it reads `{signal, trace, timeline}`
 * and would answer "nothing is backed" for every frame, throwing away the live data the
 * frame does carry. So the PROJECTION is shared and the ATTACH is local — see below.
 *
 * ONE STAGE LIST, IMPORTED (task 21.6)
 * ===================================
 * This file used to declare its own `PIPELINE_STAGES` map of SEVEN entries
 * (`MARKET_DATA INDICATORS DAG_NODES ML_INFERENCE RISK_VALIDATION EXECUTION EXCHANGE`)
 * and render a row per entry of the incoming `pipeline` array — so a six-entry frame drew
 * six stages, an unknown `stage` name drew a tenth row, and the seventh entry (`EXCHANGE`)
 * had no producer anywhere in the backend.
 *
 * The nine stages now come from {@link SIGNAL_TRACE_STAGES} in `lib/signalTraceStages.js`,
 * the same frozen literal the page uses, and so do the state names and the two reason
 * sentences that are shared with it. There is no second list here to drift: a canonical
 * id this file has no live source for is a MISSING KEY in {@link LIVE_STAGE_KEY}, which is
 * visible, rather than a row that silently fails to exist.
 *
 * The inversion is the same one the module's header describes: rows are built from the
 * canonical nine and the frame is then attached to them. The frame is never iterated to
 * decide which rows exist, so an unrecognised `stage` name cannot add a row and a missing
 * entry cannot remove one.
 *
 * NOTHING IS INVENTED TO FILL THE NEW ROWS (Requirements 14.5, 19.3)
 * =================================================================
 * Three of the nine have no source on this stream at all, and they say so in one sentence
 * each rather than borrowing a neighbour's data:
 *
 *   * **7 Submission** — the engine's frame folds the venue acknowledgement into its
 *     `execution` entry (`order_id` and `status` live there), so there is no submission
 *     record of its own to read. The per-signal view reads `EXCHANGE_RESPONSE` from the
 *     persisted timeline; this stream carries no timeline.
 *   * **9 Position update** — `POSITION_UPDATED` is a persisted timeline event
 *     (`signal_service.POSITION_UPDATED_EVENT`), not a field of this frame.
 *   * **5 Signal** is the one stage backed by the frame ITSELF rather than by a `pipeline`
 *     entry: `final_decision` / `signal_type` is the emitted decision. That is a real
 *     field of a real frame, which is why it is read rather than left absent.
 *
 * Every other absence renders `ds/Metric`'s not-available marker with a reason naming the
 * field that is empty. No zero, no dash, no synthesised entry. The old renderer wrote
 * `stage.latency ? \`${stage.latency}ms\` : '—'` — a bare dash with no explanation, on
 * five stages that can never report a duration.
 *
 * COLOUR (§1.1 G5, Requirement 1.4)
 * =================================
 * `design/semantic.js`'s `statusToken` and the `tokens.css` utility classes, only. The
 * Material palette this file carried (`#2196F3 #00BCD4 #FFAB00 #9C27B0 #FF5722 #00C853
 * #607D8B`) and its GitHub surfaces (`#0d1117 #30363d #161b22 #8b949e #c9d1d9 …`) are
 * gone — 138 colour literals to 0. Five of the six hues were decorative per-stage
 * accents, which Requirement 1.5 retires outright: a stage's identity is its number, its
 * name and its icon, and colour is spent on STATE.
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  Ban,
  Brain,
  ChevronDown,
  ChevronRight,
  Database,
  GitBranch,
  Layers,
  Minus,
  Radio,
  Send,
  Server,
  Shield,
  TrendingDown,
  TrendingUp,
  WifiOff,
  Zap,
} from 'lucide-react';

import WS_CHANNELS from '../constants/wsChannels';
import { statusToken } from '../design/semantic';
import {
  REASON_NOT_YET_REACHED,
  SIGNAL_TRACE_STAGES,
  STAGE_BLOCKED,
  STAGE_COMPLETE,
  STAGE_EXECUTION,
  STAGE_INDICATORS,
  STAGE_LOGIC,
  STAGE_MARKET_DATA,
  STAGE_MODEL,
  STAGE_NOT_AVAILABLE,
  STAGE_ORDER_DECISION,
  STAGE_PENDING,
  STAGE_POSITION_UPDATE,
  STAGE_SIGNAL,
  STAGE_SUBMISSION,
  reasonLifecycleEnded,
} from '../lib/signalTraceStages';
import { normalizeTelemetryEvent } from '../websocketClient';

import { Alert } from './ds/Alert';
import { NotAvailableMarker } from './ds/Metric';
import { StatusBadge } from './ds/StatusBadge';

/* ══════════════════════════════════════════════════════════════════════════
 * TOTAL READERS — every one of them answers for every input
 * ══════════════════════════════════════════════════════════════════════════ */

/** A plain record, or `null`. An array is not a record and neither is a string. */
const asRecord = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;

/** An array, or `[]`. A section the server could not build is not a crash here. */
const asArray = (value) => (Array.isArray(value) ? value : []);

/** A non-empty trimmed string, or `null`. */
const asText = (value) => {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
};

/** A finite number, or `null`. `NaN`, `Infinity` and numeric strings are not numbers. */
const asNumber = (value) =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

/** `true`/`false` only. Anything else is "not reported", which is a third answer. */
const asFlag = (value) => (typeof value === 'boolean' ? value : null);

/** Sum of the finite `execution_ms` values over some nodes, or `null` if none reported one. */
const totalExecutionMs = (nodes) => {
  const reported = nodes
    .map((node) => asNumber(asRecord(node)?.execution_ms))
    .filter((ms) => ms !== null);
  return reported.length === 0 ? null : reported.reduce((sum, ms) => sum + ms, 0);
};

/** A readable scalar as text, or `null` when there is nothing a trader could read. */
const scalarText = (value) => {
  if (typeof value === 'string') return asText(value);
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : null;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return null;
};

/**
 * A record or array as compact JSON — the server's own structure, unedited. `{}` and `[]`
 * read as "nothing carried" and take the marker, because an empty container is not a value.
 */
const structuredText = (value) => {
  if (Array.isArray(value)) return value.length === 0 ? null : JSON.stringify(value);
  const record = asRecord(value);
  if (record === null) return null;
  return Object.keys(record).length === 0 ? null : JSON.stringify(record);
};

/** Either form, whichever fits. The one reader every detail field goes through. */
const detailText = (value) => scalarText(value) ?? structuredText(value);

/* ══════════════════════════════════════════════════════════════════════════
 * THE NINE ROWS — CANONICAL LIST OUTWARD, FRAME ATTACHED (design.md §10.1)
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Canonical stage id → the `pipeline` entry key that backs it on this stream.
 *
 * Transcribed from `signal_trace_engine.SignalTraceRecord.to_frontend_format`, which is
 * the only producer of the array, so these six spellings are the server's own and not a
 * client-side guess. Stage 4 takes `dag_nodes` because that entry is explicitly the nodes
 * that are NOT market-data, indicator, ML, risk or execution nodes — the evaluation
 * itself, which is what Requirement 9.1 calls Logic.
 *
 * Three canonical ids are deliberately absent; see {@link NO_LIVE_SOURCE_REASON} and
 * {@link resolveSignal}. An id absent from BOTH is a missing key and renders the marker
 * with a generic reason rather than a blank row.
 */
const LIVE_STAGE_KEY = Object.freeze({
  [STAGE_MARKET_DATA]: 'market_data',
  [STAGE_INDICATORS]: 'indicators',
  [STAGE_MODEL]: 'ml_inference',
  [STAGE_LOGIC]: 'dag_nodes',
  [STAGE_ORDER_DECISION]: 'risk_validation',
  [STAGE_EXECUTION]: 'execution',
});

/**
 * The two stages this stream cannot carry, and the one sentence each says instead.
 *
 * `not-available` and not `pending`: `pending` promises the stage is still coming, and
 * neither of these is coming on THIS stream however long the frame is watched
 * (design.md §10.2). Both name where the fact does live, so the sentence tells a reader
 * what to do about it rather than only that something is missing.
 */
const NO_LIVE_SOURCE_REASON = Object.freeze({
  [STAGE_SUBMISSION]:
    'The live trace frame carries no submission record of its own: the engine folds the '
    + 'venue acknowledgement into its execution entry, so `order_id` and the venue status '
    + 'are reported there. The per-signal view reads submission from the persisted '
    + 'EXCHANGE_RESPONSE event, which this stream does not carry.',
  [STAGE_POSITION_UPDATE]:
    'The live trace frame carries no position change: POSITION_UPDATED is a persisted '
    + 'timeline event, not a field of this frame. The per-signal view reads it there.',
});

/** The reason a canonical stage has no row data because this frame omitted its entry. */
const absentEntryReason = (key) =>
  `This frame\u2019s \`pipeline\` carries no \`${key}\` entry, so the engine reported `
  + 'nothing for this stage on this signal.';

/**
 * The engine's status words → a canonical state.
 *
 * `TraceStatus` (`pending running completed failed blocked timeout`) and `NodeStatus`
 * share this vocabulary, and the `execution` entry passes its venue status through
 * verbatim, so the rejection spellings are here too. A word not in this map is NOT
 * guessed at — {@link resolveFromStatus} reports it as unreadable and quotes it.
 */
const LIVE_STATUS_STATE = Object.freeze({
  completed: STAGE_COMPLETE,
  complete: STAGE_COMPLETE,
  success: STAGE_COMPLETE,
  succeeded: STAGE_COMPLETE,
  filled: STAGE_COMPLETE,
  ok: STAGE_COMPLETE,
  failed: STAGE_BLOCKED,
  fail: STAGE_BLOCKED,
  error: STAGE_BLOCKED,
  rejected: STAGE_BLOCKED,
  refused: STAGE_BLOCKED,
  denied: STAGE_BLOCKED,
  blocked: STAGE_BLOCKED,
  timeout: STAGE_BLOCKED,
  pending: STAGE_PENDING,
  running: STAGE_PENDING,
});

/** One resolved stage before the lifecycle pass. */
const resolution = ({
  state,
  backed,
  summary = null,
  reason = null,
  latencyMs = null,
  detail = null,
}) => ({ state, backed, summary, reason, latencyMs, detail });

/** A stage with no backing record. The lifecycle pass below settles its final state. */
const unbacked = (reason) => resolution({ state: STAGE_PENDING, backed: false, reason });

/**
 * The state an entry's `status` word reports, or the unreadable answer.
 *
 * An unrecognised word is `not-available` with the word quoted, not `complete` and not
 * `pending`: the entry exists but this view cannot say what it claims, and inventing
 * either answer would put a green marker or a promise on a state nobody declared.
 */
function resolveFromStatus(entry) {
  const word = asText(entry.status);
  if (word === null) {
    return {
      state: STAGE_NOT_AVAILABLE,
      reason:
        'The engine reported this stage with no `status`, so whether it completed cannot '
        + 'be read from this frame.',
    };
  }
  const state = LIVE_STATUS_STATE[word.toLowerCase()];
  if (state === undefined) {
    return {
      state: STAGE_NOT_AVAILABLE,
      reason:
        `The engine reports status \u201c${word}\u201d for this stage, which is not one of `
        + 'the words this view can read, so its outcome is stated as unavailable rather '
        + 'than guessed.',
    };
  }
  return { state, reason: null };
}

/** Stages 1 and 2: a node count, and the summed node time where the nodes report one. */
const resolveNodeStage = (entry, noun) => {
  const nodes = asArray(entry.nodes);
  const { state, reason } = resolveFromStatus(entry);
  return resolution({
    state,
    backed: true,
    summary:
      nodes.length === 0
        ? null
        : `${nodes.length} ${noun}${nodes.length === 1 ? '' : 's'}`,
    reason,
    latencyMs: totalExecutionMs(nodes),
    detail: entry,
  });
};

/** Stage 3. `model` and `confidence` are the engine's `MLInferenceTrace` fields. */
function resolveModel(entry) {
  const { state, reason } = resolveFromStatus(entry);
  const model = asText(entry.model);
  const confidence = asNumber(entry.confidence);
  const prediction = scalarText(entry.prediction);
  return resolution({
    state,
    backed: true,
    summary:
      [
        model,
        confidence === null ? null : `confidence ${(confidence * 100).toFixed(1)}%`,
        prediction === null ? null : `\u2192 ${prediction}`,
      ]
        .filter((part) => part !== null)
        .join(' \u00b7 ') || null,
    reason,
    latencyMs: asNumber(entry.inference_ms),
    detail: entry,
  });
}

/** Stage 4. The `dag_nodes` entry, whose nodes are the engine's own per-node projection. */
function resolveLogic(entry) {
  const nodes = asArray(entry.nodes).filter((node) => asRecord(node) !== null);
  const failed = nodes.find((node) => LIVE_STATUS_STATE[asText(asRecord(node).status)?.toLowerCase()] === STAGE_BLOCKED);
  const fromStatus = resolveFromStatus(entry);
  return resolution({
    // A failed node is a refusal the entry's own `status` may not carry: the engine sets
    // `dag_nodes.status` from the node COUNT, not from the verdicts.
    state: failed === undefined ? fromStatus.state : STAGE_BLOCKED,
    backed: true,
    summary:
      nodes.length === 0 ? null : `${nodes.length} node${nodes.length === 1 ? '' : 's'} evaluated`,
    reason: failed === undefined ? fromStatus.reason : asText(asRecord(failed).error),
    latencyMs: totalExecutionMs(nodes),
    detail: entry,
  });
}

/**
 * Stage 5, backed by the FRAME rather than by a `pipeline` entry — the emitted decision.
 *
 * `final_decision` is the engine's field and `signal_type` is what
 * `normalizeTelemetryEvent` maps `payload.signal` onto, so either is the server's own
 * word. With neither, the stage is unbacked and the lifecycle pass settles it.
 */
function resolveSignal(frame) {
  const decision = frame.decision;
  if (decision === null) {
    return unbacked(
      'This frame reports no `final_decision` and no `signal_type`, so the emitted signal '
      + 'is not stated.',
    );
  }
  return resolution({
    state: STAGE_COMPLETE,
    backed: true,
    summary: decision,
    detail: { final_decision: decision },
  });
}

/**
 * Stage 6. `blocked`/`passed` are the verdict and they outrank `status`: the engine writes
 * `status: "completed"` for a risk check that ran and REFUSED, which is a completed check
 * and a stopped signal at the same time.
 */
function resolveOrderDecision(entry) {
  const passed = asFlag(entry.passed);
  const blocked = asFlag(entry.blocked);
  const refused = passed === false || blocked === true;
  const exposure = asNumber(entry.exposure_pct);
  const fromStatus = resolveFromStatus(entry);
  return resolution({
    state: refused ? STAGE_BLOCKED : fromStatus.state,
    backed: true,
    summary: refused
      ? 'Refused at risk validation'
      : [
        passed === true ? 'Risk passed' : null,
        exposure === null ? null : `exposure ${exposure}%`,
      ]
        .filter((part) => part !== null)
        .join(' \u00b7 ') || null,
    reason: refused ? asText(entry.block_reason) : fromStatus.reason,
    latencyMs: asNumber(entry.validation_ms),
    detail: entry,
  });
}

/** Stage 8. The fill, from the engine's `ExecutionTrace`. */
function resolveExecution(entry) {
  const { state, reason } = resolveFromStatus(entry);
  const size = scalarText(entry.filled_size);
  const price = scalarText(entry.filled_price);
  const orderId = asText(entry.order_id);
  return resolution({
    state,
    backed: true,
    summary:
      [
        size === null ? null : `filled ${size}`,
        price === null ? null : `@ ${price}`,
        orderId === null ? null : `order ${orderId}`,
      ]
        .filter((part) => part !== null)
        .join(' ') || null,
    reason,
    latencyMs: asNumber(entry.exchange_latency_ms),
    detail: entry,
  });
}

/** Canonical id → the resolver for its `pipeline` entry. Keyed by id, so a hole is visible. */
const ENTRY_RESOLVERS = Object.freeze({
  [STAGE_MARKET_DATA]: (entry) => resolveNodeStage(entry, 'market data node'),
  [STAGE_INDICATORS]: (entry) => resolveNodeStage(entry, 'indicator node'),
  [STAGE_MODEL]: resolveModel,
  [STAGE_LOGIC]: resolveLogic,
  [STAGE_ORDER_DECISION]: resolveOrderDecision,
  [STAGE_EXECUTION]: resolveExecution,
});

/**
 * The nine stages of one live frame.
 *
 * Built from {@link SIGNAL_TRACE_STAGES} outward, so the result always has exactly nine
 * entries in canonical order whatever the frame says. Two passes, the same two the shared
 * projection uses: each stage resolves from its own backing entry alone, then the unbacked
 * ones are settled — everything after a `blocked` stage is `not-available` because the
 * lifecycle ended there, and everything else unbacked is `pending`.
 *
 * @param {{pipeline: unknown, decision: string|null}} frame One entry of `traces`.
 * @returns {ReadonlyArray<object>} Exactly nine rows.
 */
function buildLiveStages(frame) {
  const entries = {};
  for (const item of asArray(frame.pipeline)) {
    const record = asRecord(item);
    if (record === null) continue;
    const key = asText(record.stage)?.toLowerCase();
    // The FIRST entry for a key wins, and an unknown key is dropped rather than added as
    // a tenth row. This is where "the frame cannot change which rows exist" is enforced.
    if (key !== null && !Object.prototype.hasOwnProperty.call(entries, key)) {
      entries[key] = record;
    }
  }

  const resolved = SIGNAL_TRACE_STAGES.map((stage) => {
    if (stage.id === STAGE_SIGNAL) return resolveSignal(frame);

    const fixedReason = NO_LIVE_SOURCE_REASON[stage.id];
    if (fixedReason !== undefined) {
      return resolution({ state: STAGE_NOT_AVAILABLE, backed: false, reason: fixedReason });
    }

    const key = LIVE_STAGE_KEY[stage.id];
    if (key === undefined) {
      return resolution({
        state: STAGE_NOT_AVAILABLE,
        backed: false,
        reason: `This stream declares no source for ${stage.name}.`,
      });
    }

    const entry = entries[key];
    if (entry === undefined) return unbacked(absentEntryReason(key));
    return ENTRY_RESOLVERS[stage.id](entry);
  });

  const blockedAt = resolved.findIndex((entry) => entry.state === STAGE_BLOCKED);
  const blockedName = blockedAt === -1 ? null : SIGNAL_TRACE_STAGES[blockedAt].name;

  return Object.freeze(
    SIGNAL_TRACE_STAGES.map((stage, index) => {
      const entry = resolved[index];
      let { state, reason } = entry;

      if (state === STAGE_PENDING) {
        if (blockedAt !== -1 && index > blockedAt) {
          state = STAGE_NOT_AVAILABLE;
          reason = reasonLifecycleEnded(blockedName);
        } else {
          reason = reason ?? REASON_NOT_YET_REACHED;
        }
      }

      return Object.freeze({
        id: stage.id,
        number: stage.number,
        name: stage.name,
        state,
        backed: entry.backed,
        summary: entry.summary,
        reason,
        latencyMs: entry.latencyMs,
        detail: entry.detail,
      });
    }),
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE FOUR STATES, DRAWN (design.md §10.2)
 * ══════════════════════════════════════════════════════════════════════════
 *
 * `token` names a `design/semantic.js` state, so the hue is that module's decision and
 * this file never picks one. `stroke` is the axis that survives a reader who cannot see
 * the hues, and `word` is the axis a screen reader gets: no state here is signalled by
 * colour alone.
 *
 * `not-applicable` is absent on purpose. §10.2 reserves it for the server's own statement
 * (`ml_inference.applicable === false`), and nothing on this stream makes that statement —
 * the engine's `ml_inference` entry reports `pending` when there was no model, which is a
 * gap and not a declaration. `presentationForState` is total, so were the state ever to
 * arrive it would draw as `not-available` rather than crash.
 */
const STATE_PRESENTATION = Object.freeze({
  [STAGE_COMPLETE]: Object.freeze({
    token: 'live', glyph: '\u25cf', stroke: 'solid', muted: false, word: 'Complete',
  }),
  [STAGE_BLOCKED]: Object.freeze({
    token: 'error', glyph: '\u2715', stroke: 'solid', muted: false, word: 'Blocked',
  }),
  [STAGE_PENDING]: Object.freeze({
    token: null, glyph: '\u25cb', stroke: 'dashed', muted: false, word: 'Not yet reached',
  }),
  [STAGE_NOT_AVAILABLE]: Object.freeze({
    token: null, glyph: '\u2013', stroke: 'dotted', muted: true, word: 'Not available',
  }),
});

/** Total over any state string, so an unrecognised one draws calmly rather than crashing. */
const presentationForState = (state) =>
  STATE_PRESENTATION[state] ?? STATE_PRESENTATION[STAGE_NOT_AVAILABLE];

/**
 * Canonical id → its icon. Identity only: the icon distinguishes stages, and no icon here
 * carries a hue of its own (Requirement 1.5 retires the decorative per-stage accent).
 */
const STAGE_ICON = Object.freeze({
  [STAGE_MARKET_DATA]: Database,
  [STAGE_INDICATORS]: Activity,
  [STAGE_MODEL]: Brain,
  [STAGE_LOGIC]: GitBranch,
  [STAGE_SIGNAL]: Radio,
  [STAGE_ORDER_DECISION]: Shield,
  [STAGE_SUBMISSION]: Send,
  [STAGE_EXECUTION]: Zap,
  [STAGE_POSITION_UPDATE]: Layers,
});

/**
 * The stages that can carry a duration on this stream, and the field each number is.
 *
 * Four entries report one: `execution_ms` over the nodes of `market_data`, `indicators`
 * and `dag_nodes`, `ml_inference.inference_ms`, `risk_validation.validation_ms` and
 * `execution.exchange_latency_ms`. Stages 5, 7 and 9 are absent from this map
 * deliberately, and that absence is what puts the marker in their latency slot: a `0ms`
 * would read as "instant", and an unreported duration is not a measured zero.
 */
const LATENCY_SOURCE = Object.freeze({
  [STAGE_MARKET_DATA]: '`execution_ms` summed over its nodes',
  [STAGE_INDICATORS]: '`execution_ms` summed over its nodes',
  [STAGE_MODEL]: '`ml_inference.inference_ms`',
  [STAGE_LOGIC]: '`execution_ms` summed over its nodes',
  [STAGE_ORDER_DECISION]: '`risk_validation.validation_ms`',
  [STAGE_EXECUTION]: '`execution.exchange_latency_ms`',
});

/** Why a stage has no latency slot to fill. The same sentence for all three. */
const NO_DURATION_REASON =
  'Nothing in this frame reports a duration for this stage. An unreported duration and a '
  + 'zero duration are different facts, so no figure is shown rather than a 0ms that would '
  + 'read as instant.';

/* ══════════════════════════════════════════════════════════════════════════
 * THE TRACE, READ OFF ONE FRAME
 * ══════════════════════════════════════════════════════════════════════════ */

/** The engine's terminal `TraceStatus` words, and which of them mean the signal succeeded. */
const TRACE_SUCCEEDED = Object.freeze(['completed', 'success', 'succeeded', 'filled']);
const TRACE_FAILED = Object.freeze(['failed', 'blocked', 'rejected', 'timeout', 'error']);

/**
 * Whether the frame says the signal succeeded: `true`, `false`, or `null` for "the frame
 * did not say".
 *
 * `null` is a real third answer and is why the ERROR filter and the error count below read
 * `=== false` rather than `!success`. The previous reader was `data.success || false`,
 * which turned every frame the engine never labelled — the engine emits `status`, not
 * `success` — into a failure, so the Errors figure equalled the trace count on a stream
 * where nothing had failed.
 */
function readOutcome(data, status) {
  const declared = asFlag(data.success);
  if (declared !== null) return declared;
  const word = status?.toLowerCase();
  if (word === undefined || word === null) return null;
  if (TRACE_SUCCEEDED.includes(word)) return true;
  if (TRACE_FAILED.includes(word)) return false;
  return null;
}

/**
 * One WebSocket frame as the trace this component renders.
 *
 * Every field is `null` when the frame does not carry it — never `0`, never `false`, never
 * a placeholder — so the renderer can tell "the engine said zero" from "the engine said
 * nothing" and draw the marker for the second.
 */
function readFrame(data) {
  const status = asText(data.status);
  return Object.freeze({
    id: `trace-${crypto.randomUUID()}`, // CSPRNG — Math.random() is not cryptographically secure
    signalId: asText(data.signal_id) ?? asText(data.id),
    timestamp: asText(data.timestamp) ?? asNumber(data.timestamp),
    symbol: asText(data.symbol) ?? asText(data.asset),
    strategy: asText(data.strategy) ?? asText(data.strategy_id) ?? asText(data.strategy_name),
    decision: asText(data.final_decision) ?? asText(data.signal_type),
    status,
    pipeline: asArray(data.pipeline),
    errors: asArray(data.errors),
    latencyMs: asNumber(data.latency_ms) ?? asNumber(data.total_latency),
    succeeded: readOutcome(data, status),
  });
}

/**
 * The error-type presentations. Icon and label only — the surface is `ds/Alert` at
 * `error`, so the hue is `semantic.js`'s and there is nothing to choose here.
 */
const ERROR_TYPES = Object.freeze({
  WEBSOCKET_DISCONNECT: Object.freeze({ icon: WifiOff, label: 'WebSocket disconnect' }),
  EXCHANGE_REJECT: Object.freeze({ icon: Server, label: 'Exchange reject' }),
  INVALID_SIGNAL: Object.freeze({ icon: AlertTriangle, label: 'Invalid signal' }),
  NAN_DETECTED: Object.freeze({ icon: AlertOctagon, label: 'NaN detected' }),
  RISK_BLOCK: Object.freeze({ icon: Ban, label: 'Risk block' }),
});

/** A decision word → its direction icon. Absent for anything the frame did not report. */
const DECISION_ICON = Object.freeze({
  BUY: TrendingUp,
  LONG: TrendingUp,
  SELL: TrendingDown,
  SHORT: TrendingDown,
  HOLD: Minus,
});

/* ══════════════════════════════════════════════════════════════════════════
 * FORMATTING
 * ══════════════════════════════════════════════════════════════════════════ */

/** A frame timestamp as a wall clock, or `null` when it is not a readable instant. */
function formatInstant(value) {
  if (value === null) return null;
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return null;
  return at.toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    fractionalSecondDigits: 3,
  });
}

/** A duration in the unit that reads at its own scale. `0` is a measured figure and shows. */
const formatDuration = (ms) => (ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(2)}s`);

/* ══════════════════════════════════════════════════════════════════════════
 * ONE STAGE ROW
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The latency slot: a figure, or the marker with the reason there is none.
 *
 * @param {{stage: object}} props
 */
function StageLatency({ stage }) {
  const source = LATENCY_SOURCE[stage.id] ?? null;
  const label = `${stage.name} latency`;

  if (source === null) {
    return <NotAvailableMarker label={label} reason={NO_DURATION_REASON} />;
  }
  if (stage.latencyMs === null) {
    return (
      <NotAvailableMarker
        label={label}
        reason={`This frame reports no ${source} for this stage, so no duration is stated.`}
      />
    );
  }
  return <span className="font-mono text-content-primary">{formatDuration(stage.latencyMs)}</span>;
}

/**
 * The expanded body: the entry's own fields, under the server's own key names.
 *
 * The keys are the engine's, not a fixed client-side list, so a field added to
 * `to_frontend_format` tomorrow appears here rather than being silently dropped. `nodes`
 * is lifted out and drawn as a list because it is the one field with structure a trader
 * reads rather than inspects.
 *
 * @param {{stage: object}} props
 */
function StageDetail({ stage }) {
  const detail = asRecord(stage.detail);

  if (detail === null) {
    return (
      <div className="flex min-w-0 flex-col gap-1">
        <span className="text-micro uppercase tracking-wide text-content-secondary">
          {`${stage.name} detail`}
        </span>
        <span className="flex min-w-0 flex-wrap items-baseline gap-2">
          <NotAvailableMarker label={`${stage.name} detail`} reason={stage.reason} />
          <span className="min-w-0 text-content-secondary">{stage.reason}</span>
        </span>
      </div>
    );
  }

  const nodes = asArray(detail.nodes).filter((node) => asRecord(node) !== null);
  const fields = Object.entries(detail).filter(([key]) => key !== 'nodes' && key !== 'stage');

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {fields.length === 0 ? null : (
        <div className="grid grid-cols-1 gap-x-4 gap-y-2 md:grid-cols-2">
          {fields.map(([key, value]) => {
            const shown = detailText(value);
            return (
              <div key={key} className="flex min-w-0 flex-col gap-1">
                <span className="font-mono text-micro text-content-secondary">{key}</span>
                {shown === null ? (
                  <NotAvailableMarker
                    label={key}
                    reason={`The engine carries no \`${key}\` for this stage on this signal.`}
                  />
                ) : (
                  <span className="min-w-0 break-words font-mono text-content-primary">{shown}</span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {nodes.length === 0 ? null : (
        <ul className="flex min-w-0 flex-col gap-2">
          {nodes.map((node, index) => {
            const record = asRecord(node);
            const nodeId = asText(record.node_id) ?? asText(record.id);
            const nodeStatus = asText(record.status);
            const execMs = asNumber(record.execution_ms);
            const inputs = structuredText(record.inputs);
            const outputs = structuredText(record.outputs);
            return (
              <li
                key={nodeId ?? `node-${index}`}
                className="flex min-w-0 flex-col gap-1 rounded-sm border border-line-subtle bg-surface-inset p-2"
              >
                <div className="flex min-w-0 flex-wrap items-center gap-2 text-micro">
                  {nodeId === null ? (
                    <NotAvailableMarker
                      label="Node id"
                      reason="This node carries no `node_id`, so it cannot be named."
                    />
                  ) : (
                    <span className="font-mono font-medium text-content-primary">{nodeId}</span>
                  )}
                  {asText(record.type) === null ? null : (
                    <span className="text-content-secondary">{asText(record.type)}</span>
                  )}
                  {nodeStatus === null ? (
                    <NotAvailableMarker
                      label="Node status"
                      reason="This node carries no `status`, so its verdict is not stated."
                    />
                  ) : (
                    <StatusBadge state={nodeStatus} size="sm" />
                  )}
                  {execMs === null ? (
                    <NotAvailableMarker
                      label="Node duration"
                      reason="This node reports no `execution_ms`, so no duration is stated."
                    />
                  ) : (
                    <span className="font-mono text-content-secondary">{`${execMs}ms`}</span>
                  )}
                </div>
                <div className="grid grid-cols-1 gap-x-4 gap-y-2 md:grid-cols-2">
                  <div className="flex min-w-0 flex-col gap-1 text-micro">
                    <span className="font-mono text-content-secondary">inputs</span>
                    {inputs === null ? (
                      <NotAvailableMarker
                        label="Node inputs"
                        reason="This node carries no `inputs`, so none are shown."
                      />
                    ) : (
                      <span className="min-w-0 break-words font-mono text-content-primary">{inputs}</span>
                    )}
                  </div>
                  <div className="flex min-w-0 flex-col gap-1 text-micro">
                    <span className="font-mono text-content-secondary">outputs</span>
                    {outputs === null ? (
                      <NotAvailableMarker
                        label="Node outputs"
                        reason="This node carries no `outputs`, so none are shown."
                      />
                    ) : (
                      <span className="min-w-0 break-words font-mono text-content-primary">{outputs}</span>
                    )}
                  </div>
                </div>
                {asText(record.error) === null ? null : (
                  <span className="min-w-0 break-words text-micro text-status-error">
                    {asText(record.error)}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * One stage: a `<button aria-expanded>` and the region it controls.
 *
 * The independence is structural. The button controls exactly one region, that region's id
 * comes from this row's own `useId`, and the click flips only this row's key in the
 * parent's record — so there is no state in which expanding one row collapses another. The
 * region is always in the DOM and hidden with the `hidden` attribute, so `aria-controls`
 * always names an element that exists.
 *
 * The old renderer's node rows were `<div onClick>`: reachable with a mouse and with
 * nothing else. Every disclosure here is a real `<button>`.
 *
 * @param {{stage: object, expanded: boolean, onToggle: (id: string) => void}} props
 */
function StageRow({ stage, expanded, onToggle }) {
  const rowId = useId();
  const summaryId = `${rowId}-summary`;
  const regionId = `${rowId}-region`;
  const presentation = presentationForState(stage.state);
  const { fg } = statusToken(presentation.token);
  const Icon = STAGE_ICON[stage.id] ?? Activity;

  // Never blank: what the engine recorded, then why there is nothing recorded, then the
  // state in words.
  const summary = stage.summary ?? stage.reason ?? presentation.word;

  return (
    <div
      data-stage-id={stage.id}
      data-stage-state={stage.state}
      data-stage-number={String(stage.number)}
      className="flex min-w-0 items-stretch gap-3"
    >
      {/* The rail. Decorative: the word in the region carries the state, so a border is
          never the only channel. */}
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

          {/* The state marker. `aria-hidden` because the region below announces the state
              in words and `data-stage-state` carries it for tests. */}
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
          <Icon size={13} strokeWidth={2} aria-hidden="true" className="shrink-0" />
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
            <span className="text-micro uppercase tracking-wide text-content-secondary">State</span>
            <StatusBadge state={stage.state} label={presentation.word} />
          </div>

          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-micro uppercase tracking-wide text-content-secondary">
              Summary
            </span>
            {stage.summary === null ? (
              <NotAvailableMarker label={`${stage.name} summary`} reason={stage.reason} />
            ) : (
              <span className="min-w-0 break-words text-content-primary">{stage.summary}</span>
            )}
          </div>

          {/* The engine's own sentence where it has one, and this file's where the absence
              is structural. Never restated or summarised. */}
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-micro uppercase tracking-wide text-content-secondary">Reason</span>
            {stage.reason === null ? (
              <NotAvailableMarker label={`${stage.name} reason`} />
            ) : (
              <span className="min-w-0 break-words text-content-secondary">{stage.reason}</span>
            )}
          </div>

          <StageDetail stage={stage} />
        </div>
      </div>
    </div>
  );
}

/**
 * The nine rows of one live frame.
 *
 * Rendered from `buildLiveStages` exactly as it hands them over: no filter, no sort, no
 * slice. That is what makes Requirement 9.1's "in order" and 9.2's "rather than omitting
 * it" hold on screen and not merely in the builder.
 *
 * @param {{trace: object}} props
 */
function LivePipeline({ trace }) {
  const [expanded, setExpanded] = useState(() => ({}));
  const stages = useMemo(() => buildLiveStages(trace), [trace]);

  const toggle = useCallback((id) => {
    // Only this id's entry is touched, on its own previous value — so a row's state is the
    // parity of its own activations and nothing else.
    setExpanded((previous) => ({ ...previous, [id]: previous[id] !== true }));
  }, []);

  return (
    <div className="flex min-w-0 flex-col gap-1">
      {stages.map((stage) => (
        <StageRow
          key={stage.id}
          stage={stage}
          expanded={expanded[stage.id] === true}
          onToggle={toggle}
        />
      ))}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * ONE TRACE IN THE LIST
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The frame's own error block, as a condition strip rather than a coloured panel.
 *
 * @param {{errors: ReadonlyArray<unknown>}} props
 */
function TraceErrors({ errors }) {
  const readable = errors.map(asRecord).filter((error) => error !== null);
  if (readable.length === 0) return null;

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {readable.map((error, index) => {
        const type = asText(error.type)?.toUpperCase();
        const config = (type === undefined || type === null ? undefined : ERROR_TYPES[type])
          ?? ERROR_TYPES.INVALID_SIGNAL;
        const Icon = config.icon;
        const message = asText(error.message);
        const recoverable = asFlag(error.recoverable);
        const stage = asText(error.stage);
        return (
          <Alert
            key={`${type ?? 'error'}-${index}`}
            severity="error"
            variant="block"
            title={message ?? config.label}
          >
            <span className="flex min-w-0 flex-wrap items-center gap-2 text-micro">
              <Icon size={12} strokeWidth={2} aria-hidden="true" className="shrink-0" />
              <span className="text-content-secondary">{config.label}</span>
              {recoverable === null ? (
                <NotAvailableMarker
                  label="Recoverable"
                  reason="The engine did not report whether this error is recoverable."
                />
              ) : (
                <span className="text-content-secondary">
                  {recoverable ? 'Recoverable' : 'Non-recoverable'}
                </span>
              )}
              {stage === null ? null : (
                <span className="font-mono text-content-secondary">{`at ${stage}`}</span>
              )}
            </span>
          </Alert>
        );
      })}
    </div>
  );
}

/**
 * One trace: a `<button aria-expanded>` header and the nine stages behind it.
 *
 * @param {{trace: object, expanded: boolean, onToggle: (id: string) => void}} props
 */
function TraceItem({ trace, expanded, onToggle }) {
  const rowId = useId();
  const headerId = `${rowId}-header`;
  const regionId = `${rowId}-region`;
  const { fg } = statusToken(trace.decision);
  const DecisionIcon = trace.decision === null ? null : DECISION_ICON[trace.decision.toUpperCase()];
  const clock = formatInstant(trace.timestamp);

  return (
    <li className="flex min-w-0 flex-col rounded-md border border-line-default bg-surface-panel">
      <button
        type="button"
        id={headerId}
        aria-expanded={expanded}
        aria-controls={regionId}
        onClick={() => onToggle(trace.id)}
        className="flex min-w-0 items-center gap-3 rounded-md px-3 py-2 text-left text-body transition-colors hover:bg-surface-raised"
      >
        {expanded ? (
          <ChevronDown size={14} strokeWidth={2} aria-hidden="true" className="shrink-0" />
        ) : (
          <ChevronRight size={14} strokeWidth={2} aria-hidden="true" className="shrink-0" />
        )}

        {DecisionIcon === null || DecisionIcon === undefined ? null : (
          <DecisionIcon
            size={16}
            strokeWidth={2}
            aria-hidden="true"
            className="shrink-0"
            style={{ color: fg }}
          />
        )}

        <span className="flex min-w-0 flex-1 flex-col">
          <span className="min-w-0 truncate font-semibold text-content-primary">
            {trace.symbol ?? (
              <NotAvailableMarker
                label="Market"
                reason="This frame reports no `symbol`, so the market is not named."
              />
            )}
          </span>
          <span className="flex min-w-0 flex-wrap items-baseline gap-2 text-micro text-content-secondary">
            {trace.strategy ?? (
              <NotAvailableMarker
                label="Strategy"
                reason="This frame reports no `strategy`, so the strategy is not named."
              />
            )}
            {clock === null ? (
              <NotAvailableMarker
                label="Received at"
                reason="This frame reports no readable `timestamp`."
              />
            ) : (
              <span className="font-mono">{clock}</span>
            )}
          </span>
        </span>

        {trace.status === null ? (
          <NotAvailableMarker
            label="Trace status"
            reason="This frame reports no `status`, so its lifecycle state is not stated."
          />
        ) : (
          <StatusBadge state={trace.status} size="sm" />
        )}

        <span className="flex shrink-0 flex-col text-right text-micro">
          <span className="text-content-secondary">Latency</span>
          {trace.latencyMs === null ? (
            <NotAvailableMarker
              label="Total latency"
              reason="This frame reports no `latency_ms`, so no total duration is stated."
            />
          ) : (
            <span className="font-mono font-semibold text-content-primary">
              {formatDuration(trace.latencyMs)}
            </span>
          )}
        </span>
      </button>

      <div
        id={regionId}
        role="region"
        aria-labelledby={headerId}
        hidden={!expanded}
        className="flex min-w-0 flex-col gap-3 border-t border-line-subtle px-3 py-2"
      >
        <span className="text-micro uppercase tracking-wide text-content-secondary">
          Execution pipeline
        </span>
        <LivePipeline trace={trace} />
        <TraceErrors errors={trace.errors} />
        <span className="flex min-w-0 flex-wrap items-baseline gap-2 text-micro text-content-secondary">
          <span className="uppercase tracking-wide">Signal id</span>
          {trace.signalId === null ? (
            <NotAvailableMarker
              label="Signal id"
              reason="This frame reports neither `signal_id` nor `id`."
            />
          ) : (
            <span className="min-w-0 break-words font-mono text-content-primary">
              {trace.signalId}
            </span>
          )}
        </span>
      </div>
    </li>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE PANEL
 * ══════════════════════════════════════════════════════════════════════════ */

/** The three list filters. `ALL` is not a filter so much as the absence of one. */
const FILTERS = Object.freeze(['ALL', 'SUCCESS', 'ERROR']);

/** Whether the stream has ever been able to deliver a frame. See the notice below. */
const STREAM_SUBSCRIBED = 'subscribed';
const STREAM_NO_CLIENT = 'no-client';
const STREAM_NO_SUBSCRIBE = 'no-subscribe';

/**
 * Why the panel is empty, in the caller's terms rather than the trader's fault.
 *
 * The old code `console.warn`ed that `wsClient.subscribe` was missing and carried on,
 * leaving the panel showing "Waiting for WebSocket connection..." forever — a sentence
 * that is false, because nothing was waiting and nothing had been asked for. A dead panel
 * that explains itself is the minimum; this states which of the three situations it is in.
 */
const STREAM_NOTICE = Object.freeze({
  [STREAM_NO_CLIENT]: Object.freeze({
    title: 'No WebSocket client was supplied to this panel',
    body:
      'This view renders frames pushed on the signal_trace and risk_events channels. With '
      + 'no client it has no source, so it is empty by construction rather than waiting.',
  }),
  [STREAM_NO_SUBSCRIBE]: Object.freeze({
    title: 'The WebSocket client exposes no subscribe method',
    body:
      'This view subscribes through `wsClient.subscribe(channel, handler)`. The client it '
      + 'was given does not provide it, so no frame can ever arrive here and the panel '
      + 'will stay empty until the client is replaced.',
  }),
});

/**
 * The live pipeline stream.
 *
 * @param {Object} props
 * @param {{subscribe?: Function}} [props.wsClient] The client this panel subscribes on.
 * @param {string} [props.accountId] The account whose stream is being watched, for the
 *   header. Part of the props contract; the frames themselves are tenant-scoped
 *   server-side, so this is a label and never a client-side filter that would silently
 *   hide traffic.
 * @param {number} [props.maxTraces] The ring-buffer depth.
 */
const SignalTraceVisualization = ({ wsClient, accountId, maxTraces = 50 }) => {
  const [traces, setTraces] = useState([]);
  const [filter, setFilter] = useState('ALL');
  const [isPaused, setIsPaused] = useState(false);
  const [expanded, setExpanded] = useState(() => ({}));
  const [stream, setStream] = useState(STREAM_NO_CLIENT);

  const traceBuffer = useRef([]);

  // No mock data, ever: the buffer starts empty and only a frame puts anything in it.
  useEffect(() => {
    traceBuffer.current = [];
    setTraces([]);
  }, []);

  useEffect(() => {
    if (!wsClient) {
      setStream(STREAM_NO_CLIENT);
      return undefined;
    }
    if (typeof wsClient.subscribe !== 'function') {
      // Kept as well as surfaced: the console line is for whoever wired the call site, the
      // notice is for whoever is looking at the panel.
      console.warn('[SignalTraceVisualization] wsClient.subscribe is not available');
      setStream(STREAM_NO_SUBSCRIBE);
      return undefined;
    }

    setStream(STREAM_SUBSCRIBED);

    const handleFrame = (rawData) => {
      if (isPaused) return;
      const data = asRecord(normalizeTelemetryEvent(rawData));
      if (data === null) return;

      traceBuffer.current = [readFrame(data), ...traceBuffer.current].slice(0, maxTraces);
      setTraces(traceBuffer.current);
    };

    const unsubscribeSignalTrace = wsClient.subscribe(WS_CHANNELS.SIGNAL_TRACE, handleFrame);
    const unsubscribeRiskEvents = wsClient.subscribe(WS_CHANNELS.RISK_EVENTS, handleFrame);

    return () => {
      if (typeof unsubscribeSignalTrace === 'function') unsubscribeSignalTrace();
      if (typeof unsubscribeRiskEvents === 'function') unsubscribeRiskEvents();
    };
  }, [wsClient, isPaused, maxTraces]);

  const toggleTrace = useCallback((id) => {
    setExpanded((previous) => ({ ...previous, [id]: previous[id] !== true }));
  }, []);

  const shown = useMemo(() => {
    if (filter === 'SUCCESS') return traces.filter((trace) => trace.succeeded === true);
    if (filter === 'ERROR') return traces.filter((trace) => trace.succeeded === false);
    return traces;
  }, [traces, filter]);

  // Figures over what the frames actually reported. A rate over nothing is not 0%, and an
  // average of no numbers is not 0ms — both take the marker (Requirement 14.5).
  const stats = useMemo(() => {
    const graded = traces.filter((trace) => trace.succeeded !== null);
    const timed = traces
      .map((trace) => trace.latencyMs)
      .filter((ms) => ms !== null);
    return {
      total: traces.length,
      successRate: graded.length === 0
        ? null
        : (graded.filter((trace) => trace.succeeded === true).length / graded.length) * 100,
      ungraded: traces.length - graded.length,
      averageLatencyMs: timed.length === 0
        ? null
        : timed.reduce((sum, ms) => sum + ms, 0) / timed.length,
      errors: traces.filter((trace) => trace.succeeded === false).length,
    };
  }, [traces]);

  const notice = STREAM_NOTICE[stream] ?? null;

  return (
    <div className="flex min-w-0 flex-col gap-4 bg-surface-canvas p-4 font-mono">
      <div className="flex min-w-0 flex-col gap-1">
        <h2 className="text-title font-semibold text-content-primary">Live signal pipeline</h2>
        <p className="flex min-w-0 flex-wrap items-baseline gap-2 text-micro text-content-secondary">
          <span>{`${traces.length} frame${traces.length === 1 ? '' : 's'} buffered`}</span>
          <span>{`newest ${maxTraces} kept`}</span>
          {accountId === undefined || accountId === null || accountId === '' ? null : (
            <span className="font-mono">{`account ${accountId}`}</span>
          )}
        </p>
      </div>

      {notice === null ? null : (
        <Alert severity="warning" variant="block" title={notice.title}>
          {notice.body}
        </Alert>
      )}

      <div className="flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-md border border-line-default bg-surface-panel p-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {FILTERS.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={filter === option}
              onClick={() => setFilter(option)}
              className={
                filter === option
                  ? 'rounded-sm border border-brand bg-surface-raised px-3 py-1 text-micro font-semibold text-brand transition-colors'
                  : 'rounded-sm border border-line-default bg-transparent px-3 py-1 text-micro font-semibold text-content-secondary transition-colors hover:bg-surface-raised'
              }
            >
              {option}
            </button>
          ))}
        </div>

        <button
          type="button"
          aria-pressed={isPaused}
          onClick={() => setIsPaused((paused) => !paused)}
          className={
            isPaused
              ? 'flex items-center gap-2 rounded-sm border border-line-strong bg-surface-raised px-3 py-1 text-micro font-semibold text-content-primary transition-colors'
              : 'flex items-center gap-2 rounded-sm border border-line-default bg-transparent px-3 py-1 text-micro font-semibold text-content-secondary transition-colors hover:bg-surface-raised'
          }
        >
          {isPaused ? (
            <Activity size={13} strokeWidth={2} aria-hidden="true" />
          ) : (
            <Radio size={13} strokeWidth={2} aria-hidden="true" />
          )}
          {isPaused ? 'Paused' : 'Live'}
        </button>
      </div>

      <dl className="grid min-w-0 grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <div className="flex min-w-0 flex-col gap-1 rounded-md border border-line-default bg-surface-panel p-3">
          <dt className="text-micro uppercase tracking-wide text-content-secondary">
            Frames buffered
          </dt>
          <dd className="font-mono text-title font-semibold text-content-primary">{stats.total}</dd>
        </div>
        <div className="flex min-w-0 flex-col gap-1 rounded-md border border-line-default bg-surface-panel p-3">
          <dt className="text-micro uppercase tracking-wide text-content-secondary">Success rate</dt>
          <dd className="font-mono text-title font-semibold text-content-primary">
            {stats.successRate === null ? (
              <NotAvailableMarker
                label="Success rate"
                reason={
                  stats.total === 0
                    ? 'No frame has arrived, so there is no outcome to rate.'
                    : 'No buffered frame reports an outcome the engine labelled, so a rate '
                      + 'over them would be a rate over nothing.'
                }
              />
            ) : (
              `${stats.successRate.toFixed(1)}%`
            )}
          </dd>
        </div>
        <div className="flex min-w-0 flex-col gap-1 rounded-md border border-line-default bg-surface-panel p-3">
          <dt className="text-micro uppercase tracking-wide text-content-secondary">Mean latency</dt>
          <dd className="font-mono text-title font-semibold text-content-primary">
            {stats.averageLatencyMs === null ? (
              <NotAvailableMarker
                label="Mean latency"
                reason="No buffered frame reports a `latency_ms`, so there is nothing to average."
              />
            ) : (
              formatDuration(Math.round(stats.averageLatencyMs))
            )}
          </dd>
        </div>
        <div className="flex min-w-0 flex-col gap-1 rounded-md border border-line-default bg-surface-panel p-3">
          <dt className="text-micro uppercase tracking-wide text-content-secondary">
            Frames reported failed
          </dt>
          <dd className="font-mono text-title font-semibold text-content-primary">{stats.errors}</dd>
          {stats.ungraded === 0 ? null : (
            <span className="text-micro text-content-secondary">
              {`${stats.ungraded} frame${stats.ungraded === 1 ? '' : 's'} report no outcome and are counted in neither figure`}
            </span>
          )}
        </div>
      </dl>

      {shown.length === 0 ? (
        <div className="flex min-w-0 flex-col items-center gap-2 rounded-md border border-line-default bg-surface-panel p-4 text-center">
          <Activity size={24} strokeWidth={2} aria-hidden="true" className="text-content-muted" />
          <span className="text-body text-content-primary">
            {traces.length === 0 ? 'No frames yet' : `No frames match ${filter}`}
          </span>
          <span className="text-micro text-content-secondary">
            {traces.length === 0
              ? stream === STREAM_SUBSCRIBED
                ? 'Subscribed to the signal_trace and risk_events channels. A frame appears '
                  + 'here when a deployed strategy evaluates market data.'
                : 'This panel is not subscribed to anything — see the notice above.'
              : 'The buffered frames are all in the other outcome, or report no outcome at all.'}
          </span>
        </div>
      ) : (
        <ul className="flex min-w-0 flex-col gap-2">
          {shown.map((trace) => (
            <TraceItem
              key={trace.id}
              trace={trace}
              expanded={expanded[trace.id] === true}
              onToggle={toggleTrace}
            />
          ))}
        </ul>
      )}
    </div>
  );
};

export default SignalTraceVisualization;
