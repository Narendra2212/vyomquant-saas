/**
 * ModelPerformance — finding ML-01's inspector panel for a bound ML_DL model's real
 * train/validation/test metrics and training date.
 *
 * What is under test:
 * * real metrics (the shape `model_versioning.public_model_version()` serves) render as
 *   their actual numbers, for every split that was scored;
 * * a metric the backend did not record for a split renders as an honest "not
 *   available" marker, never as a fabricated `0`;
 * * a split with no metrics at all says "Not scored" rather than an empty row;
 * * no bound model version at all is stated plainly, not silently omitted;
 * * the "past performance" disclosure is always present once any model version
 *   information is shown.
 */

import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

import { ModelPerformance } from '../../src/components/builder/ModelPerformance';

afterEach(cleanup);

const MODEL_VERSION = {
  id: 'mv_1',
  node_id: 'n-ml',
  block_id: 'xgboost',
  created_at: '2026-01-15T10:00:00Z',
  train_metrics: { rows: 500, accuracy: 0.91, f1_macro: 0.9, loss: 0.2 },
  val_metrics: { rows: 100, accuracy: 0.6, f1_macro: 0.55, loss: 0.9 },
  test_metrics: { rows: 100, accuracy: 0.58, f1_macro: 0.52, loss: 0.95 },
};

describe('ModelPerformance', () => {
  it('states plainly that no model version is bound yet', () => {
    render(<ModelPerformance modelVersion={null} />);

    expect(screen.getByTestId('model-performance-unbound').textContent).toMatch(
      /no model version is bound/i,
    );
    expect(screen.queryByTestId('model-performance-metrics-table')).toBeNull();
  });

  it('renders the disclosure even with no model bound', () => {
    render(<ModelPerformance modelVersion={null} />);

    expect(screen.getByTestId('model-performance-disclosure').textContent).toMatch(
      /past performance does not guarantee future results/i,
    );
  });

  it('renders the real metrics for every scored split', () => {
    render(<ModelPerformance modelVersion={MODEL_VERSION} />);

    expect(screen.getByTestId('model-performance-train_metrics-accuracy').textContent).toBe('0.9100');
    expect(screen.getByTestId('model-performance-val_metrics-accuracy').textContent).toBe('0.6000');
    expect(screen.getByTestId('model-performance-test_metrics-accuracy').textContent).toBe('0.5800');
    expect(screen.getByTestId('model-performance-test_metrics-f1_macro').textContent).toBe('0.5200');
  });

  it('renders the training date', () => {
    render(<ModelPerformance modelVersion={MODEL_VERSION} />);

    const el = screen.getByTestId('model-performance-trained-at');
    expect(el.textContent).toMatch(/^Trained /);
    expect(el.textContent).not.toMatch(/not available/i);
  });

  it('shows "not available" for a metric the backend did not record, not a fabricated 0', () => {
    const partial = {
      ...MODEL_VERSION,
      test_metrics: { rows: 100, accuracy: 0.58 }, // f1_macro and loss missing
    };
    render(<ModelPerformance modelVersion={partial} />);

    const cell = screen.getByTestId('model-performance-test_metrics-f1_macro');
    expect(cell.getAttribute('data-available')).toBe('false');
    expect(cell.textContent).toBe('—');
    expect(cell.textContent).not.toBe('0');
    expect(cell.textContent).not.toBe('0.0000');
  });

  it('treats a NaN or non-finite metric as not available, never as 0', () => {
    // `loss` is a real number, so this split IS scored and its row renders per-metric
    // cells; `accuracy` and `f1_macro` are each individually unreadable within it.
    const nanny = {
      ...MODEL_VERSION,
      val_metrics: { rows: 100, accuracy: null, f1_macro: Number.NaN, loss: 0.8 },
    };
    render(<ModelPerformance modelVersion={nanny} />);

    expect(
      screen.getByTestId('model-performance-val_metrics-accuracy').getAttribute('data-available'),
    ).toBe('false');
    expect(
      screen.getByTestId('model-performance-val_metrics-f1_macro').getAttribute('data-available'),
    ).toBe('false');
    expect(
      screen.getByTestId('model-performance-val_metrics-loss').getAttribute('data-available'),
    ).toBe('true');
  });

  it('reports a split with no usable metrics as "Not scored", not an empty row', () => {
    const unscored = { ...MODEL_VERSION, test_metrics: { rows: 0 } };
    render(<ModelPerformance modelVersion={unscored} />);

    expect(screen.getByTestId('model-performance-test_metrics-unscored').textContent).toMatch(
      /not scored/i,
    );
  });

  it('reports an unreadable training date honestly rather than hiding the field', () => {
    const noDate = { ...MODEL_VERSION, created_at: null };
    render(<ModelPerformance modelVersion={noDate} />);

    expect(screen.getByTestId('model-performance-trained-at').textContent).toMatch(
      /training date not available/i,
    );
  });

  it('reports no metrics at all honestly when every split is unscored', () => {
    const nothingScored = {
      ...MODEL_VERSION,
      train_metrics: {},
      val_metrics: {},
      test_metrics: {},
    };
    render(<ModelPerformance modelVersion={nothingScored} />);

    expect(screen.getByTestId('model-performance-no-metrics')).not.toBeNull();
    expect(screen.queryByTestId('model-performance-metrics-table')).toBeNull();
  });
});
