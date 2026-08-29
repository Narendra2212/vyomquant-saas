/**
 * NodePreview.jsx — the inspector's node preview. Task 5.7, Requirements 24.7 and 24.8.
 *
 * Presentation only. The projection, the state machine and every judgement about what a value
 * means live in `lib/nodePreview.js`; the values themselves are computed by the backend, by the
 * executors that trade. This component decides where things sit on screen and nothing else —
 * it performs no arithmetic, holds no fallback sample and invents no value for a bar the
 * response left empty.
 *
 * The render model arrives as a **prop**, the way `ParameterForm` takes its descriptors, so
 * this file has no client of its own and can be asserted without a network.
 *
 * What it shows, and why each part is there
 * -----------------------------------------
 * * **Every declared output port**, including one that produced nothing. A `macd` node shows
 *   `macd`, `signal` and `histogram` as three series, because that is what it publishes.
 * * **The bounded window**, in words, with the cap named whenever it applied (Requirement
 *   24.7). A silently shortened window is a preview of a question nobody asked.
 * * **For a FEATURE_ENGINEERING node: every produced column name**, plus its per-column warmup
 *   and a sample of values (Requirement 24.8). All the names, a bounded number of value
 *   columns, and the truncation stated when it happened.
 * * **The recorded numeric conditions** for the node — `DIVISION_BY_ZERO at bar 34` rather than
 *   a column of blanks (Requirement 20.4). This is the answer to "why is this bar empty?",
 *   which is the question a preview exists to answer.
 *
 * An empty bar renders as `EMPTY_VALUE_TEXT`, never `0`. A zero is a number an author can
 * compare against a threshold; "no value" is not, and the two must not look alike.
 */

import React from 'react';

import { C } from '../ui-legacy/primitives';
import { Button } from '../ui/Button';
import {
  EMPTY_VALUE_TEXT,
  OUTPUT_KINDS,
  PREVIEW_STATES,
  describeWindow,
} from '../../lib/nodePreview';

const label = (text) => (
  <div
    className="text-caption-sm"
    style={{
      color: C.t3,
      fontFamily: 'monospace',
      letterSpacing: 1,
      textTransform: 'uppercase',
      marginBottom: 4,
    }}
  >
    {text}
  </div>
);

const cellStyle = {
  padding: '2px 6px',
  fontFamily: 'monospace',
  whiteSpace: 'nowrap',
  textAlign: 'right',
};

/** One numeric cell. An empty value is marked as such for a screen reader too, not by glyph. */
const ValueCell = ({ cell, testId }) => (
  <td
    style={{ ...cellStyle, color: cell.empty ? C.t3 : C.t1 }}
    data-testid={testId}
    data-empty={cell.empty ? 'true' : 'false'}
    title={cell.empty ? 'No value on this bar' : undefined}
  >
    {cell.empty ? <span aria-label="no value">{EMPTY_VALUE_TEXT}</span> : cell.text}
  </td>
);

/** The tail of a produced series: newest last, exactly the order the endpoint sent. */
const SeriesTable = ({ output }) => (
  <table
    className="text-caption-sm"
    data-testid={`preview-series-${output.name}`}
    style={{ width: '100%', borderCollapse: 'collapse' }}
  >
    <caption className="sr-only">{`Last ${output.rows.length} values of ${output.name}`}</caption>
    <thead>
      <tr style={{ color: C.t3 }}>
        <th scope="col" style={{ ...cellStyle, textAlign: 'left' }}>Bar</th>
        <th scope="col" style={cellStyle}>{output.name}</th>
      </tr>
    </thead>
    <tbody>
      {output.rows.map((row, position) => (
        <tr key={row.index ?? position}>
          <th scope="row" style={{ ...cellStyle, textAlign: 'left', color: C.t3, fontWeight: 400 }}>
            {row.index ?? '?'}
          </th>
          <ValueCell cell={row} testId={`preview-value-${output.name}-${position}`} />
        </tr>
      ))}
    </tbody>
  </table>
);

/**
 * A FEATURE_MATRIX: the produced column names first, then the value sample.
 *
 * The names come before the numbers on purpose. Requirement 24.8 asks for the names because
 * they are what an author checks — that `lag_1` is the one-bar lag and not the three — and a
 * wide matrix's numbers are truncated while its names never are.
 */
const FeatureMatrixPanel = ({ output }) => (
  <div data-testid={`preview-matrix-${output.name}`}>
    {label(`Produced columns (${output.columnCount})`)}
    <ul
      data-testid={`preview-columns-${output.name}`}
      style={{ listStyle: 'none', margin: '0 0 8px', padding: 0, display: 'flex', flexWrap: 'wrap', gap: 4 }}
    >
      {output.columnWarmup.map(({ column, warmup }) => (
        <li
          key={column}
          data-testid={`preview-column-${column}`}
          data-warmup={warmup === null ? undefined : warmup}
          className="text-caption-sm"
          style={{
            fontFamily: 'monospace',
            border: `1px solid ${C.border}`,
            borderRadius: 3,
            padding: '1px 5px',
            color: C.t2,
          }}
          title={warmup === null ? undefined : `${warmup} warmup bars`}
        >
          {column}
        </li>
      ))}
    </ul>

    {output.sampleTruncated && (
      <p
        className="text-caption-sm"
        data-testid={`preview-truncated-${output.name}`}
        style={{ color: C.gold, margin: '0 0 6px' }}
      >
        {`Values shown for the first ${output.sampledColumns.length} of ${output.columnCount} columns. Every column name is listed above.`}
      </p>
    )}

    <div style={{ overflowX: 'auto' }}>
      <table className="text-caption-sm" style={{ borderCollapse: 'collapse' }}>
        <caption className="sr-only">
          {`Sampled values for ${output.sampledColumns.length} of ${output.columnCount} produced columns`}
        </caption>
        <thead>
          <tr style={{ color: C.t3 }}>
            <th scope="col" style={{ ...cellStyle, textAlign: 'left' }}>Bar</th>
            {output.sampledColumns.map((column) => (
              <th scope="col" key={column} style={cellStyle}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {output.rows.map((row, position) => (
            <tr key={row.index ?? position}>
              <th scope="row" style={{ ...cellStyle, textAlign: 'left', color: C.t3, fontWeight: 400 }}>
                {row.index ?? '?'}
              </th>
              {row.cells.map((cell, column) => (
                <ValueCell
                  key={cell.column ?? column}
                  cell={cell}
                  testId={`preview-cell-${cell.column}-${position}`}
                />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  </div>
);

const OutputPanel = ({ output }) => {
  if (!output.produced) {
    return (
      <div data-testid={`preview-output-${output.name}`} data-produced="false">
        {label(`${output.name} · ${output.portType || 'port'}`)}
        <p className="text-caption-sm" style={{ color: C.t3, margin: 0 }}>
          This port produced no value on this run.
        </p>
      </div>
    );
  }

  return (
    <div data-testid={`preview-output-${output.name}`} data-produced="true" data-kind={output.kind}>
      {label(`${output.name} · ${output.portType || 'port'}`)}
      {output.kind === OUTPUT_KINDS.SERIES && <SeriesTable output={output} />}
      {output.kind === OUTPUT_KINDS.FEATURE_MATRIX && <FeatureMatrixPanel output={output} />}
      {output.kind === OUTPUT_KINDS.SCALAR && (
        <p
          className="text-caption"
          data-testid={`preview-scalar-${output.name}`}
          data-empty={output.value === null ? 'true' : 'false'}
          style={{ fontFamily: 'monospace', color: output.value === null ? C.t3 : C.t1, margin: 0 }}
        >
          {output.text}
        </p>
      )}
      {output.kind === OUTPUT_KINDS.UNRENDERABLE && (
        <p className="text-caption-sm" style={{ color: C.gold, margin: 0 }}>
          {/* The endpoint's own sentence. It knows what it could not sample. */}
          {output.message}
        </p>
      )}
      {output.emptyValueCount > 0 && (
        <p
          className="text-caption-sm"
          data-testid={`preview-empty-count-${output.name}`}
          style={{ color: C.t3, margin: '4px 0 0' }}
        >
          {`${output.emptyValueCount} of the sampled values are empty.`}
        </p>
      )}
    </div>
  );
};

/**
 * @param {object} props
 * @param {string} props.state One of `PREVIEW_STATES`.
 * @param {object|null} props.preview The projected render model, when `state` is `ready`.
 * @param {object|null} props.error The classified failure, when `state` is `error`.
 * @param {{available: boolean, reason: string|null, code: string|null}} props.availability
 * @param {() => void} props.onRequest
 */
export function NodePreview({
  state = PREVIEW_STATES.IDLE,
  preview = null,
  error = null,
  availability = { available: true, reason: null, code: null },
  onRequest = () => {},
}) {
  return (
    <section
      data-testid="node-preview"
      data-state={state}
      style={{ borderTop: `1px solid ${C.border}`, paddingTop: 8, marginTop: 4 }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        {label('Preview')}
        <Button
          variant="ghost"
          size="sm"
          data-testid="preview-run"
          disabled={!availability.available || state === PREVIEW_STATES.LOADING}
          onClick={onRequest}
        >
          {state === PREVIEW_STATES.LOADING ? 'Computing…' : 'Preview'}
        </Button>
      </div>

      {/* Why the button cannot be pressed, in the author's terms rather than by greying out. */}
      {!availability.available && (
        <p
          className="text-caption-sm"
          data-testid="preview-unavailable"
          data-code={availability.code || undefined}
          style={{ color: C.t3, margin: '4px 0 0' }}
        >
          {availability.reason}
        </p>
      )}

      {availability.available && state === PREVIEW_STATES.IDLE && (
        <p className="text-caption-sm" data-testid="preview-idle" style={{ color: C.t3, margin: '4px 0 0' }}>
          {/* The client's own state: nothing has been asked yet, which is not a verdict. */}
          No preview requested yet. The last values this block produces are computed by the
          same executors that run it.
        </p>
      )}

      {state === PREVIEW_STATES.LOADING && (
        <p className="text-caption-sm" data-testid="preview-loading" style={{ color: C.t3, margin: '4px 0 0' }} aria-live="polite">
          Computing this block over a bounded historical window…
        </p>
      )}

      {state === PREVIEW_STATES.ERROR && error !== null && (
        <div data-testid="preview-error" data-code={error.code} data-status={error.status ?? undefined}>
          {/* The backend's own message and hint, verbatim. */}
          <p className="text-caption-sm" style={{ color: C.red, margin: '4px 0 0' }}>{error.message}</p>
          {error.hint && (
            <p className="text-caption-sm" data-testid="preview-error-hint" style={{ color: C.t3, margin: '2px 0 0' }}>
              {error.hint}
            </p>
          )}
          {error.retryable && (
            <Button variant="ghost" size="sm" data-testid="preview-retry" onClick={onRequest} style={{ marginTop: 4 }}>
              Retry
            </Button>
          )}
        </div>
      )}

      {state === PREVIEW_STATES.READY && preview !== null && (
        <div data-testid="preview-body" data-node-id={preview.nodeId} data-category={preview.category || undefined}>
          <p className="text-caption-sm" data-testid="preview-window" style={{ color: C.t3, margin: '4px 0 6px' }}>
            {describeWindow(preview.window)}
          </p>

          {preview.market.symbol !== null && (
            <p className="text-caption-sm" data-testid="preview-market" style={{ color: C.t3, margin: '0 0 8px' }}>
              {/* The DATA block's own parameters. No venue is named, because none is sent. */}
              {`${preview.market.symbol} · ${preview.market.timeframe}`}
            </p>
          )}

          {preview.issues.length > 0 && (
            <ul
              data-testid="preview-issues"
              style={{ listStyle: 'none', margin: '0 0 8px', padding: 0 }}
            >
              {preview.issues.map((issue, position) => (
                <li
                  key={`${issue.code}-${position}`}
                  data-testid={`preview-issue-${issue.code}`}
                  className="text-caption-sm"
                  style={{ color: C.gold, fontFamily: 'monospace' }}
                >
                  {/* The engine's recorded condition, as recorded. This is why a bar is empty. */}
                  {issue.message || issue.code}
                </li>
              ))}
            </ul>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {preview.outputs.map((output) => (
              <OutputPanel key={output.name} output={output} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

export default NodePreview;
