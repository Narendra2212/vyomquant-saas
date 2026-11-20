/**
 * ModelPerformance.jsx — finding ML-01: the inspector panel that surfaces a bound
 * ML_DL model's actual train/validation/test metrics and training date.
 *
 * `backend_app/backend/model_versioning.py`'s `public_model_version()` has always
 * included `train_metrics`, `val_metrics`, `test_metrics` and `created_at` in the
 * response `GET /strategy-operations/training/jobs/{job_id}` (and the list endpoint)
 * already serve — ML-01's audit found that nothing in this client ever rendered them.
 * A trader configuring an ML_DL node saw the training progress bar and nothing about
 * how good the model that came out of it actually is. This component closes that gap
 * by rendering the numbers the backend already computed, honestly: a metric the
 * backend did not record is shown as "not available", never as a fabricated `0`, and a
 * split that was never scored says so rather than rendering an empty table row.
 *
 * Presentation only, the same convention `NodePreview.jsx` and `NodeTrace.jsx` use: the
 * caller resolves which model version (if any) is bound to the selected node and hands
 * it over as a prop, already shaped like `model_versioning.public_model_version()`.
 * This component performs no fetch, invents no figure, and holds no state of its own.
 *
 * The persistent "trained model" disclosure that belongs on the canvas, for every
 * ML_DL node at all times, is a separate, smaller badge drawn directly on the node in
 * `StrategyBuilder.jsx`'s `DynamicNode` — this panel is the inspector's deeper detail,
 * shown only once a trader has selected that node.
 */

import React from 'react';

import { token } from '../../design/tokens';

/** Metric keys this component knows how to label, in the order they are shown.
 *
 * Mirrors exactly the two shapes `training_runtime.StatefulEstimator.evaluate()`
 * produces (`backend_app/backend/training_runtime.py`) — classification
 * (`accuracy`, `f1_macro`, `loss`) and regression (`r2`, `mae`, `mse`) — plus `rows`,
 * which both shapes carry. No other key is invented or guessed at.
 */
const METRIC_LABELS = Object.freeze({
  rows: 'Rows scored',
  accuracy: 'Accuracy',
  f1_macro: 'F1 (macro)',
  loss: 'Loss',
  mse: 'MSE',
  mae: 'MAE',
  r2: 'R²',
});

const METRIC_ORDER = Object.freeze(['rows', 'accuracy', 'f1_macro', 'r2', 'mae', 'mse', 'loss']);

/** Every metric key EXCEPT `rows`. A split that only carries `{"rows": N}` (what the
 * backend's trainer records when a split had no estimator to score, including the
 * `{"rows": 0}` empty case) was not actually scored, whatever `rows` says. */
const SCORE_METRIC_KEYS = METRIC_ORDER.filter((key) => key !== 'rows');

const SPLITS = Object.freeze([
  { key: 'train_metrics', label: 'Train' },
  { key: 'val_metrics', label: 'Validation' },
  { key: 'test_metrics', label: 'Test (held out)' },
]);

/** `value` formatted for display, or `null` for anything that is not a finite number. */
function formatMetric(key, value) {
  if (typeof value !== 'number' || Number.isNaN(value) || !Number.isFinite(value)) return null;
  if (key === 'rows') return String(Math.round(value));
  return value.toFixed(4);
}

/** Whether a split was actually SCORED — carries at least one real metric, not just a
 * row count. `{"rows": 0}` and `{"rows": N}` alone are both "not scored": the first is
 * an empty split, the second is a split the trainer could not evaluate at all. */
function hasAnyMetric(metrics) {
  if (!metrics || typeof metrics !== 'object') return false;
  return SCORE_METRIC_KEYS.some((key) => formatMetric(key, metrics[key]) !== null);
}

/** `created_at` as a short, locale-formatted date, or `null` if it cannot be read. */
function formatTrainingDate(createdAt) {
  if (!createdAt) return null;
  const parsed = new Date(createdAt);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleString();
}

const sectionLabelStyle = {
  color: token.content.muted,
  letterSpacing: 1,
  textTransform: 'uppercase',
  marginBottom: 6,
};

const cellStyle = {
  padding: '3px 6px',
  textAlign: 'right',
  fontFamily: 'monospace',
  whiteSpace: 'nowrap',
};

/**
 * One split's row of metric cells, in `METRIC_ORDER`. A metric the backend did not
 * record for this split (missing, non-numeric, NaN, or dropped by the server's own
 * `sanitize_epoch_metrics`) renders as "not available" — never as `0`, which would be
 * indistinguishable from a model that was genuinely measured at zero.
 */
const MetricRow = ({ split, metrics }) => {
  const scored = hasAnyMetric(metrics);
  return (
    <tr data-testid={`model-performance-row-${split.key}`}>
      <th
        scope="row"
        style={{ ...cellStyle, textAlign: 'left', color: token.content.secondary, fontFamily: 'inherit' }}
      >
        {split.label}
      </th>
      {scored ? (
        METRIC_ORDER.map((key) => {
          const formatted = formatMetric(key, metrics ? metrics[key] : undefined);
          return (
            <td
              key={key}
              style={{ ...cellStyle, color: formatted === null ? token.content.muted : token.content.primary }}
              data-testid={`model-performance-${split.key}-${key}`}
              data-available={formatted === null ? 'false' : 'true'}
              title={formatted === null ? `${METRIC_LABELS[key]} was not recorded for this split` : undefined}
            >
              {formatted === null ? <span aria-label="not available">—</span> : formatted}
            </td>
          );
        })
      ) : (
        <td
          colSpan={METRIC_ORDER.length}
          style={{ ...cellStyle, textAlign: 'left', color: token.content.muted }}
          data-testid={`model-performance-${split.key}-unscored`}
        >
          Not scored
        </td>
      )}
    </tr>
  );
};

/**
 * @param {object} props
 * @param {object|null} [props.modelVersion] The bound model version, shaped exactly as
 *   `model_versioning.public_model_version()` returns it (so `train_metrics` /
 *   `val_metrics` / `test_metrics` / `created_at`, among other fields). `null` or
 *   `undefined` means "no model version is known to be bound to this node yet" — a
 *   fact this panel states rather than hides.
 */
export function ModelPerformance({ modelVersion = null }) {
  const trainingDate = modelVersion ? formatTrainingDate(modelVersion.created_at) : null;
  const anyScored = modelVersion
    ? SPLITS.some((split) => hasAnyMetric(modelVersion[split.key]))
    : false;

  return (
    <div data-testid="model-performance-panel" style={{ marginTop: 12 }}>
      <div className="text-micro" style={sectionLabelStyle}>
        Model performance
      </div>

      {/*
        The disclosure a trader needs before trusting any number below it: these are
        historical training-time measurements, not a live guarantee. Worded the way
        `RiskSettings.jsx`'s "Not enforced yet" disclosures are — a plain sentence, not
        a colour alone.
      */}
      <div
        data-testid="model-performance-disclosure"
        className="text-micro font-semibold"
        style={{ color: token.content.secondary, marginBottom: 8, whiteSpace: 'normal' }}
      >
        Trained model — these figures are historical training-time measurements, not a
        live guarantee. Past performance does not guarantee future results.
      </div>

      {!modelVersion && (
        <p
          className="text-micro"
          data-testid="model-performance-unbound"
          style={{ color: token.content.muted, margin: 0 }}
        >
          No model version is bound to this block yet — train it to see real performance
          figures here.
        </p>
      )}

      {modelVersion && (
        <>
          <div
            className="text-micro"
            data-testid="model-performance-trained-at"
            style={{ color: token.content.secondary, marginBottom: 6 }}
          >
            {trainingDate ? `Trained ${trainingDate}` : 'Training date not available'}
          </div>

          {anyScored ? (
            <table
              className="text-micro"
              style={{ width: '100%', borderCollapse: 'collapse' }}
              data-testid="model-performance-metrics-table"
            >
              <caption className="sr-only">
                Train, validation and test metrics for the bound model version
              </caption>
              <thead>
                <tr style={{ color: token.content.muted }}>
                  <th scope="col" style={{ ...cellStyle, textAlign: 'left' }}>Split</th>
                  {METRIC_ORDER.map((key) => (
                    <th key={key} scope="col" style={cellStyle}>{METRIC_LABELS[key]}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {SPLITS.map((split) => (
                  <MetricRow key={split.key} split={split} metrics={modelVersion[split.key]} />
                ))}
              </tbody>
            </table>
          ) : (
            <p
              className="text-micro"
              data-testid="model-performance-no-metrics"
              style={{ color: token.content.muted, margin: 0 }}
            >
              This model version records no train, validation or test metrics.
            </p>
          )}
        </>
      )}
    </div>
  );
}

export default ModelPerformance;
