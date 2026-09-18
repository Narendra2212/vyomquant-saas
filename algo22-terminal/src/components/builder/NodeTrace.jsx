/**
 * NodeTrace.jsx — the inspector's execution trace. Task 9.3, Requirement 24.6.
 *
 * "THE Strategy_Builder SHALL display the recorded execution trace for a selected node,
 * comprising that node's inputs, outputs, duration and recorded failures."
 *
 * Presentation only, the same division `NodePreview` follows: the projection and every
 * judgement about what a recorded fact means live in `lib/nodeTrace.js`, the facts themselves
 * are recorded by `dag_engine.ExecutionTracer` and `signal_trace_engine`, and the render model
 * arrives as a **prop** so this file has no client of its own and can be asserted without a
 * network.
 *
 * There is no arithmetic here and no composed explanation. The duration is the endpoint's sum,
 * the shapes are the tracer's own dicts formatted by `describeShape`, and the sentence at the
 * top is the server's `summary` verbatim — the rule `ParameterForm` follows for `fix_hint` and
 * `NodePreview` for `detail.message`, and the right rule here because every fact in that
 * sentence is a fact about a run only the server observed.
 *
 * What it shows, and why each part is there
 * -----------------------------------------
 * * **The one-line answer to "why did nothing happen?"**, first, because that is the question
 *   the panel exists for.
 * * **The live runtime reading**, when there is one, from task 8.5's
 *   `deployment.runtime_state` frame through `builderRealtime.nodeRuntimeView`. A node that is
 *   `WARMING` or waiting on a port never ran, and that is a different answer from a node that
 *   ran and failed. No new subscription: this is the reading the canvas already stamps onto the
 *   node.
 * * **Every recorded execution** of the node — status, duration, each bound input port with its
 *   recorded type and shape, and the recorded output. A list, because a node started twice in
 *   one run spent time twice and reporting only the last would hide a first attempt that
 *   failed.
 * * **The blocking failure**, called out as upstream when it is upstream. The answer to a
 *   silent strategy is frequently a *different* block, and a panel that only ever reported this
 *   node's own failures would be silent in exactly that case.
 * * **The numeric conditions** the run recorded — `DIVISION_BY_ZERO on 3 bars from bar 34`
 *   rather than a column of blanks (Requirement 20.4).
 * * **Live signal provenance**, when `signal_trace_engine` holds any for this node. A preview
 *   is not a deployment, and the panel says so rather than showing an empty list.
 *
 * A missing figure renders as `UNRECORDED_TEXT`, never `0`: a node nobody timed and a node that
 * took no measurable time are different facts. Status is a word before it is a colour.
 */

import React from 'react';

import { token } from '../../design/tokens';
import {
  TRACE_STATUSES,
  UNRECORDED_TEXT,
  hasTrace,
} from '../../lib/nodeTrace';

const label = (text) => (
  <div
    className="text-micro"
    style={{
      color: token.content.muted,
      fontFamily: 'monospace',
      letterSpacing: 1,
      textTransform: 'uppercase',
      marginBottom: 4,
    }}
  >
    {text}
  </div>
);

const STATUS_COLOUR = {
  [TRACE_STATUSES.SUCCESS]: token.content.primary,
  [TRACE_STATUSES.FAIL]: token.status.loss.fg,
  [TRACE_STATUSES.PENDING]: token.status.warning.fg,
  [TRACE_STATUSES.NOT_EXECUTED]: token.content.muted,
};

const mono = {
  fontFamily: 'monospace',
  margin: 0,
};

/**
 * One bound input, as the run recorded it. Requirement 24.6's "inputs".
 *
 * The port leads, because that is the name on the author's own block. The upstream node is on
 * a `data-` attribute and in the tooltip rather than in the line: a ULID is what makes a trace
 * followable and is not what makes it readable.
 */
const InputRow = ({ input, index }) => (
  <li
    className="text-micro"
    data-testid={`trace-input-${input.label !== UNRECORDED_TEXT ? input.label : index}`}
    data-port={input.port || undefined}
    data-source={input.source || undefined}
    title={input.source ? `from ${input.source}` : undefined}
    style={{ ...mono, color: token.content.secondary, listStyle: 'none' }}
  >
    <span style={{ color: token.content.muted }}>{input.label}</span>
    {' · '}
    <span>{input.type || UNRECORDED_TEXT}</span>
    {' · '}
    <span data-testid={`trace-input-shape-${input.label !== UNRECORDED_TEXT ? input.label : index}`}>
      {input.shapeText}
    </span>
  </li>
);

/** One traced execution of the selected node. */
const ExecutionPanel = ({ execution, position }) => (
  <div
    data-testid={`trace-execution-${position}`}
    data-status={execution.status || undefined}
    style={{ borderLeft: `2px solid ${token.line.default}`, paddingLeft: 8, marginTop: 6 }}
  >
    <p className="text-micro" style={{ ...mono, color: STATUS_COLOUR[execution.status] || token.content.secondary }}>
      <span data-testid={`trace-execution-status-${position}`}>{execution.status || UNRECORDED_TEXT}</span>
      {' · '}
      {/* The tracer's own figure. Requirement 24.6's "duration". */}
      <span data-testid={`trace-execution-duration-${position}`}>{execution.durationText}</span>
      {execution.nodeType && (
        <>
          {' · '}
          <span style={{ color: token.content.muted }}>{execution.nodeType}</span>
        </>
      )}
    </p>

    {execution.errorMessage && (
      /* The runtime's own message, verbatim. */
      <p
        className="text-micro"
        data-testid={`trace-execution-error-${position}`}
        style={{ ...mono, color: token.status.loss.fg, marginTop: 2 }}
      >
        {execution.errorMessage}
      </p>
    )}

    <div style={{ marginTop: 4 }}>
      {label(`Inputs (${execution.inputs.length})`)}
      {execution.inputs.length === 0 ? (
        <p className="text-micro" data-testid={`trace-no-inputs-${position}`} style={{ ...mono, color: token.content.muted }}>
          The run bound no inputs to this block.
        </p>
      ) : (
        <ul style={{ margin: 0, padding: 0 }}>
          {execution.inputs.map((input, index) => (
            <InputRow key={`${input.port || ''}-${input.source || index}`} input={input} index={index} />
          ))}
        </ul>
      )}
    </div>

    <div style={{ marginTop: 4 }}>
      {label('Output')}
      <p
        className="text-micro"
        data-testid={`trace-output-${position}`}
        data-output-type={execution.output.type || undefined}
        style={{ ...mono, color: token.content.secondary }}
      >
        {execution.output.type || UNRECORDED_TEXT}
        {' · '}
        {execution.output.shapeText}
      </p>
    </div>
  </div>
);

/**
 * @param {object} props
 * @param {object|null} props.trace A projected trace from `lib/nodeTrace.js`, or null.
 * @param {object|null} props.runtime Task 8.5's `nodeRuntimeView` reading for this node.
 */
export function NodeTrace({ trace = null, runtime = null }) {
  if (!hasTrace(trace)) {
    return (
      <section
        data-testid="node-trace"
        data-state="absent"
        style={{ borderTop: `1px solid ${token.line.default}`, paddingTop: 8, marginTop: 4 }}
      >
        {label('Execution trace')}
        <p className="text-micro" data-testid="trace-absent" style={{ ...mono, color: token.content.muted }}>
          {/* The client's own state: nothing has been run, which is not a verdict about the
              block. A preview records the trace, so asking for one is what fills this in. */}
          No execution has been recorded for this block yet. Run a preview and the inputs,
          duration and any recorded failure appear here.
        </p>
      </section>
    );
  }

  const live = runtime && runtime.known === true ? runtime : null;

  return (
    <section
      data-testid="node-trace"
      data-state="recorded"
      data-node-id={trace.nodeId}
      data-status={trace.status}
      data-recorded={trace.recorded ? 'true' : 'false'}
      style={{ borderTop: `1px solid ${token.line.default}`, paddingTop: 8, marginTop: 4 }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
        {label('Execution trace')}
        <span
          className="text-micro"
          data-testid="trace-status"
          style={{ ...mono, color: STATUS_COLOUR[trace.status] || token.content.secondary }}
        >
          {/* The status word before any colour. */}
          {trace.statusLabel}
        </span>
      </div>

      {/* The server's sentence, verbatim: the one-line answer to "why did nothing happen?". */}
      {trace.summary !== '' && (
        <p
          className="text-micro"
          data-testid="trace-summary"
          style={{ ...mono, color: token.content.secondary }}
          aria-live="polite"
        >
          {trace.summary}
        </p>
      )}

      <p className="text-micro" data-testid="trace-duration" style={{ ...mono, color: token.content.muted, marginTop: 4 }}>
        {`Recorded duration: ${trace.durationText}`}
      </p>

      {/* Task 8.5's reading, reused rather than re-subscribed. A node that is warming never
          ran, and that is a different answer from a node that ran and failed. */}
      {live !== null && (
        <p
          className="text-micro"
          data-testid="trace-runtime-state"
          data-runtime-state={live.state || undefined}
          style={{ ...mono, color: token.content.muted, marginTop: 2 }}
        >
          {`Live runtime state: ${live.label}${live.detail ? ` — ${live.detail}` : ''}`}
        </p>
      )}

      {/* The earliest failure this node depended on. Called out as upstream when it is, because
          the block to fix is then a different one from the block selected. */}
      {trace.blockingFailure !== null && (
        <div
          data-testid="trace-blocking-failure"
          data-upstream={trace.blockedUpstream ? 'true' : 'false'}
          data-failed-node={trace.blockingFailure.nodeId || undefined}
          style={{ marginTop: 6 }}
        >
          {label(trace.blockedUpstream ? 'Blocked upstream' : 'Failure')}
          <p className="text-micro" style={{ ...mono, color: token.status.loss.fg }}>
            {trace.blockedUpstream
              ? `${trace.blockingFailure.nodeId}: ${trace.blockingFailure.errorMessage || 'no reason recorded'}`
              : trace.blockingFailure.errorMessage || 'no reason recorded'}
          </p>
        </div>
      )}

      {/* Requirement 20.4: the recorded numeric conditions, each with the endpoint's sentence.
          This is why a bar is empty rather than wrong. */}
      {trace.conditions.length > 0 && (
        <div style={{ marginTop: 6 }}>
          {label(`Recorded conditions (${trace.conditions.length})`)}
          <ul data-testid="trace-conditions" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {trace.conditions.map((condition, position) => (
              <li
                key={`${condition.code}-${position}`}
                data-testid={`trace-condition-${condition.code}`}
                className="text-micro"
                style={{ ...mono, color: token.status.warning.fg }}
              >
                {condition.display}
              </li>
            ))}
          </ul>
        </div>
      )}

      {trace.executions.length === 0 ? (
        <p className="text-micro" data-testid="trace-not-executed" style={{ ...mono, color: token.content.muted, marginTop: 6 }}>
          {/* Distinct from "produced nothing": the tracer holds no entry at all for this
              block, so it was never started. */}
          The run recorded no execution of this block.
        </p>
      ) : (
        <div style={{ marginTop: 6 }}>
          {label(`Executions (${trace.executions.length})`)}
          {trace.executions.map((execution, position) => (
            <ExecutionPanel key={position} execution={execution} position={position} />
          ))}
        </div>
      )}

      {/* Requirement 24.6's "recorded failures", for the whole run. Kept even when one of them
          is already shown as the blocking failure: the run's failure list is what shows a
          second, later failure that a single call-out would hide. */}
      {trace.failures.length > 0 && (
        <div style={{ marginTop: 6 }}>
          {label(`Recorded failures this run (${trace.failures.length})`)}
          <ul data-testid="trace-failures" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {trace.failures.map((failure, position) => (
              <li
                key={`${failure.nodeId}-${position}`}
                data-testid={`trace-failure-${position}`}
                data-failed-node={failure.nodeId || undefined}
                className="text-micro"
                style={{ ...mono, color: token.status.loss.fg }}
              >
                {`${failure.nodeId || UNRECORDED_TEXT}: ${failure.errorMessage || 'no reason recorded'}`}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* A different question about the same block: what happened when the strategy was
          actually running. The endpoint's sentence, including when the answer is "nothing". */}
      <div style={{ marginTop: 6 }}>
        {label('Live signal provenance')}
        {trace.signalProvenance.message && (
          <p
            className="text-micro"
            data-testid="trace-provenance-message"
            data-available={trace.signalProvenance.available ? 'true' : 'false'}
            style={{ ...mono, color: token.content.muted }}
          >
            {trace.signalProvenance.message}
          </p>
        )}
        {trace.signalProvenance.records.length > 0 && (
          <ul data-testid="trace-provenance" style={{ listStyle: 'none', margin: '4px 0 0', padding: 0 }}>
            {trace.signalProvenance.records.map((record, position) => (
              <li
                key={record.traceId || position}
                data-testid={`trace-provenance-record-${position}`}
                className="text-micro"
                style={{ ...mono, color: token.content.secondary }}
              >
                {/* The market, never a venue. */}
                {`${record.recordedAt || UNRECORDED_TEXT} · ${record.symbol || UNRECORDED_TEXT} · ${record.status || UNRECORDED_TEXT}`}
                {record.executions.length > 0 && (
                  <span style={{ color: token.content.muted }}>{` · ${record.executions[0].durationText}`}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

export default NodeTrace;
