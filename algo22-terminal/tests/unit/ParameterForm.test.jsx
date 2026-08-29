/**
 * ParameterForm — registry-driven parameter form (task 3.7).
 *
 * Requirements under test: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 8.9.
 *
 * The descriptor payloads in `fixtures/registryDescriptors.json` were captured from the
 * running backend via `BlockRegistry.to_dict()` (`ohlcv_feed`, `action_buy_market`,
 * `action_buy_limit`, `macd`, `feat_lag`, `feat_select`, `feat_time`, `xgboost`, `between`),
 * so the form is proven against the shape the API actually serves rather than a hand-written
 * guess. `xgboost` is included because 19 of the 48 `ml_models` hyperparameters carry
 * `help=""` today — the missing-help degradation is tested against the real gap.
 */

import React, { useState } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  ParameterForm,
  PARAM_TYPES,
  BEHAVIOUR_CHANGING_PARAMS,
  describeField,
  blockingParamKeys,
  isUnset,
  normaliseSeverity,
} from '../../src/components/builder/ParameterForm';
import descriptors from './fixtures/registryDescriptors.json';

afterEach(cleanup);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const spec = (overrides) => ({
  key: 'p',
  label: 'P',
  type: 'TEXT',
  required: true,
  default: null,
  min: null,
  max: null,
  step: null,
  options: null,
  options_source: null,
  unit: null,
  example: null,
  help: '',
  depends_on: [],
  affects_warmup: false,
  ...overrides,
});

const control = (key) => document.querySelector(`[data-param-key="${key}"]`);

const field = (key) => screen.getByTestId(`param-${key}`);

/** Renders the form with real React state, the way the inspector will drive it. */
function Harness({ params, initialValues = {}, issues = [], ...rest }) {
  const [values, setValues] = useState(initialValues);
  return (
    <ParameterForm
      params={params}
      values={values}
      issues={issues}
      onChange={(key, value) => setValues((prev) => ({ ...prev, [key]: value }))}
      {...rest}
    />
  );
}

/** A spec per `ParamType`, plus one type the vocabulary does not contain. */
const oneOfEachType = [
  spec({ key: 'p_number', label: 'Number param', type: 'NUMBER', min: 0, max: 10, step: 0.5 }),
  spec({ key: 'p_integer', label: 'Integer param', type: 'INTEGER', min: 2, max: 500, step: 1 }),
  spec({ key: 'p_text', label: 'Text param', type: 'TEXT' }),
  spec({ key: 'p_select', label: 'Select param', type: 'SELECT', options: ['a', 'b'] }),
  spec({ key: 'p_multiselect', label: 'Multiselect param', type: 'MULTISELECT', options: [1, 2, 3] }),
  spec({ key: 'p_boolean', label: 'Boolean param', type: 'BOOLEAN', default: false }),
  spec({ key: 'p_date', label: 'Date param', type: 'DATE' }),
  spec({ key: 'p_symbol', label: 'Symbol param', type: 'SYMBOL' }),
  spec({ key: 'p_timeframe', label: 'Timeframe param', type: 'TIMEFRAME' }),
];

// ---------------------------------------------------------------------------
// Requirement 5.1 / 5.2 — a control per declared ParamType
// ---------------------------------------------------------------------------

describe('control vocabulary (Requirements 5.1, 5.2)', () => {
  it('covers every ParamType the backend publishes', () => {
    expect(PARAM_TYPES).toEqual([
      'NUMBER',
      'INTEGER',
      'TEXT',
      'SELECT',
      'MULTISELECT',
      'BOOLEAN',
      'DATE',
      'SYMBOL',
      'TIMEFRAME',
    ]);
    expect(oneOfEachType.map((s) => s.type)).toEqual([...PARAM_TYPES]);
  });

  it('renders the right control for each type', () => {
    render(<Harness params={oneOfEachType} />);

    expect(control('p_number').tagName).toBe('INPUT');
    expect(control('p_number')).toHaveProperty('type', 'number');
    expect(control('p_integer')).toHaveProperty('type', 'number');
    expect(control('p_integer').inputMode).toBe('numeric');
    expect(control('p_text')).toHaveProperty('type', 'text');
    expect(control('p_select').tagName).toBe('SELECT');
    expect(control('p_select').multiple).toBe(false);
    expect(control('p_multiselect').tagName).toBe('SELECT');
    expect(control('p_multiselect').multiple).toBe(true);
    expect(control('p_boolean')).toHaveProperty('type', 'checkbox');
    expect(control('p_date')).toHaveProperty('type', 'date');
    expect(control('p_symbol')).toHaveProperty('type', 'text');
    expect(control('p_timeframe')).toHaveProperty('type', 'text');
  });

  it('surfaces an unknown ParamType instead of silently skipping it', () => {
    render(<Harness params={[spec({ key: 'p_alien', label: 'Alien', type: 'QUATERNION' })]} />);

    const notice = screen.getByTestId('unsupported-p_alien');
    expect(notice).toBeTruthy();
    expect(notice.getAttribute('data-unsupported-param-type')).toBe('QUATERNION');
    expect(notice.textContent).toContain('QUATERNION');
    // still editable, so the author is not locked out of a parameter the backend requires
    expect(control('p_alien')).toBeTruthy();
    expect(screen.getByLabelText('Alien')).toBe(control('p_alien'));
  });

  it('applies min, max, step, unit and the declared option set (Requirement 5.6)', () => {
    render(<Harness params={oneOfEachType.concat(spec({ key: 'p_unit', label: 'Windowed', type: 'INTEGER', unit: 'bars', min: 2, max: 500, step: 1, default: 14 }))} />);

    expect(control('p_number').getAttribute('min')).toBe('0');
    expect(control('p_number').getAttribute('max')).toBe('10');
    expect(control('p_number').getAttribute('step')).toBe('0.5');
    expect(control('p_integer').getAttribute('step')).toBe('1');

    const selectOptions = Array.from(control('p_select').options).map((o) => o.value);
    expect(selectOptions).toEqual(['', 'a', 'b']); // blank first, nothing auto-picked
    const multiOptions = Array.from(control('p_multiselect').options).map((o) => o.value);
    expect(multiOptions).toEqual(['1', '2', '3']);

    expect(within(field('p_unit')).getByText(/bars/).textContent).toContain('2–500');
    expect(within(field('p_unit')).getByText(/bars/).textContent).toContain('step 1');
  });

  it('a NUMBER without a declared step accepts decimals', () => {
    render(<Harness params={[spec({ key: 'num', type: 'NUMBER' })]} />);
    expect(control('num').getAttribute('step')).toBe('any');
  });
});

// ---------------------------------------------------------------------------
// Requirement 5.2 — example is a placeholder, help is a tooltip
// ---------------------------------------------------------------------------

describe('example and help (Requirement 5.2)', () => {
  it('shows example as a placeholder and never as a value', () => {
    const onChange = vi.fn();
    render(
      <ParameterForm
        params={[spec({ key: 'symbol', label: 'Symbol', type: 'SYMBOL', example: 'BTC/USDT' })]}
        values={{}}
        onChange={onChange}
      />,
    );

    const input = control('symbol');
    expect(input.getAttribute('placeholder')).toBe('BTC/USDT');
    expect(input.value).toBe('');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('puts the example on a SELECT blank option, not on a selected option', () => {
    render(
      <Harness
        params={[
          spec({
            key: 'quantity_type',
            label: 'Size expressed as',
            type: 'SELECT',
            options: ['base_amount', 'percent_of_equity'],
            example: 'percent_of_equity',
          }),
        ]}
      />,
    );

    const select = control('quantity_type');
    expect(select.value).toBe('');
    expect(select.options[0].value).toBe('');
    expect(select.options[0].textContent).toContain('percent_of_equity');
  });

  it('renders help as a keyboard-reachable tooltip wired to the control', async () => {
    const user = userEvent.setup();
    render(
      <Harness
        params={[spec({ key: 'window', label: 'Window', type: 'INTEGER', default: 14, help: 'Bars in the average.' })]}
      />,
    );

    const help = screen.getByTestId('help-window');
    expect(help.textContent).toBe('Bars in the average.');
    expect(help.getAttribute('role')).toBe('tooltip');
    // described by the help region whether or not the tooltip is visually open
    expect(control('window').getAttribute('aria-describedby')).toContain(help.id);

    const trigger = screen.getByRole('button', { name: 'Help for Window' });
    expect(trigger.getAttribute('aria-controls')).toBe(help.id);
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(help.getAttribute('data-open')).toBe('false');

    await user.tab();
    expect(document.activeElement).toBe(trigger);
    await user.keyboard('{Enter}');
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByTestId('help-window').getAttribute('data-open')).toBe('true');
  });

  it('degrades cleanly when help is missing — no trigger and no empty tooltip', () => {
    render(<Harness params={[spec({ key: 'subsample', label: 'Subsample', type: 'NUMBER', help: '' })]} />);

    expect(screen.queryByTestId('help-subsample')).toBeNull();
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(screen.queryByRole('button', { name: /Help for/ })).toBeNull();
    const describedBy = control('subsample').getAttribute('aria-describedby') || '';
    expect(describedBy).not.toContain('-help');
  });
});

// ---------------------------------------------------------------------------
// Requirement 5.3 — required visible, optional under Advanced
// ---------------------------------------------------------------------------

describe('required and Advanced grouping (Requirement 5.3)', () => {
  const params = [
    spec({ key: 'req_a', label: 'Req A', type: 'TEXT', required: true }),
    spec({ key: 'opt_a', label: 'Opt A', type: 'TEXT', required: false }),
    spec({ key: 'req_b', label: 'Req B', type: 'INTEGER', required: true, default: 5 }),
    spec({ key: 'opt_b', label: 'Opt B', type: 'BOOLEAN', required: false, default: false }),
  ];

  it('shows every required parameter without expanding anything', () => {
    render(<Harness params={params} />);

    const requiredSection = screen.getByTestId('required-params');
    expect(within(requiredSection).getByLabelText('Req A')).toBeTruthy();
    expect(within(requiredSection).getByLabelText('Req B')).toBeTruthy();
    expect(screen.queryByLabelText('Opt A')).toBeNull();
    expect(screen.queryByLabelText('Opt B')).toBeNull();
  });

  it('hides optional parameters until Advanced is expanded', async () => {
    const user = userEvent.setup();
    render(<Harness params={params} />);

    const toggle = screen.getByTestId('advanced-toggle');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.textContent).toContain('Advanced (2)');
    expect(screen.getByTestId('advanced-params').hasAttribute('hidden')).toBe(true);
    expect(toggle.getAttribute('aria-controls')).toBe(screen.getByTestId('advanced-params').id);

    await user.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(within(screen.getByTestId('advanced-params')).getByLabelText('Opt A')).toBeTruthy();
  });

  it('auto-expands Advanced when a hidden optional field carries a backend error', () => {
    render(
      <Harness
        params={params}
        issues={[
          {
            code: 'PARAM_OUT_OF_RANGE',
            severity: 'error',
            node_id: null,
            edge_id: null,
            field: 'opt_a',
            message: 'Opt A is not a legal value.',
            expected: 'text',
            actual: 3,
            fix_hint: 'Set Opt A to a short label.',
          },
        ]}
      />,
    );

    expect(screen.getByTestId('advanced-toggle').getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByTestId('issue-opt_a').textContent).toContain('Set Opt A to a short label.');
  });

  it('no Advanced section at all when every parameter is required', () => {
    render(<Harness params={[spec({ key: 'only', type: 'TEXT', required: true })]} />);
    expect(screen.queryByTestId('advanced-toggle')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Requirements 5.5 / 5.6 — a declared default is shown *as* a default
// ---------------------------------------------------------------------------

describe('declared defaults (Requirements 5.5, 5.6)', () => {
  it('shows the declared default value with an explicit default affordance', () => {
    render(
      <Harness params={[spec({ key: 'window', label: 'Window', type: 'INTEGER', default: 14, min: 2, max: 500, step: 1, unit: 'bars' })]} />,
    );

    expect(control('window').value).toBe('14');
    const badge = screen.getByTestId('default-badge-window');
    expect(badge.textContent).toBe('default');
    expect(badge.getAttribute('title')).toBe('Block default: 14');
    // the default is an affordance, not a blocker
    expect(screen.queryByTestId('parameter-form-blocking')).toBeNull();
  });

  it('marks a value equal to the default as the default, and drops the badge once changed', async () => {
    const user = userEvent.setup();
    render(<Harness params={[spec({ key: 'window', label: 'Window', type: 'INTEGER', default: 14 })]} initialValues={{ window: 14 }} />);

    expect(screen.getByTestId('default-badge-window')).toBeTruthy();
    expect(screen.queryByTestId('reset-default-window')).toBeNull();

    await user.clear(control('window'));
    await user.type(control('window'), '21');
    expect(control('window').value).toBe('21');
    expect(screen.queryByTestId('default-badge-window')).toBeNull();

    const reset = screen.getByTestId('reset-default-window');
    expect(reset.textContent).toContain('Reset to default (14)');
    await user.click(reset);
    expect(control('window').value).toBe('14');
    expect(screen.getByTestId('default-badge-window')).toBeTruthy();
  });

  it('a boolean default of false is shown as the default, not as an unset field', async () => {
    const user = userEvent.setup();
    render(
      <Harness
        params={[spec({ key: 'reduce_only', label: 'Reduce only', type: 'BOOLEAN', required: true, default: false, help: 'Shrink only.' })]}
      />,
    );

    const box = control('reduce_only');
    expect(box.type).toBe('checkbox'); // not the tri-state select: `false` is a real default
    expect(box.checked).toBe(false);
    expect(screen.getByTestId('default-badge-reduce_only').getAttribute('title')).toBe('Block default: false');
    expect(screen.queryByTestId('parameter-form-blocking')).toBeNull();
    expect(box.getAttribute('aria-invalid')).toBe('false');

    await user.click(box);
    expect(control('reduce_only').checked).toBe(true);
    expect(screen.queryByTestId('default-badge-reduce_only')).toBeNull();
    expect(screen.getByTestId('reset-default-reduce_only').textContent).toContain('Reset to default (false)');
  });
});

// ---------------------------------------------------------------------------
// Requirement 5.4 — the financial-safety rule
// ---------------------------------------------------------------------------

describe('behaviour-changing parameters render empty and block (Requirement 5.4)', () => {
  const TYPE_BY_KEY = {
    symbol: 'SYMBOL',
    timeframe: 'TIMEFRAME',
    quantity: 'NUMBER',
    quantity_type: 'SELECT',
    price: 'NUMBER',
    trigger_price: 'NUMBER',
    confidence_threshold: 'NUMBER',
  };

  const REQUIREMENT_KEYS = Object.keys(TYPE_BY_KEY);

  it('the safety list covers every key the requirement names', () => {
    REQUIREMENT_KEYS.forEach((key) => expect(BEHAVIOUR_CHANGING_PARAMS).toContain(key));
  });

  REQUIREMENT_KEYS.forEach((key) => {
    it(`${key} renders empty and blocks until it is set`, async () => {
      const user = userEvent.setup();
      const onBlockingChange = vi.fn();
      const type = TYPE_BY_KEY[key];
      const params = [
        spec({
          key,
          label: key,
          type,
          required: true,
          default: null,
          example: type === 'SELECT' ? 'percent_of_equity' : '0.25',
          options: type === 'SELECT' ? ['base_amount', 'percent_of_equity'] : null,
          help: 'No default: this parameter changes how the strategy trades.',
        }),
      ];

      render(<Harness params={params} onBlockingChange={onBlockingChange} />);

      // empty
      expect(control(key).value).toBe('');
      // blocking, in words rather than by colour
      const banner = screen.getByTestId('parameter-form-blocking');
      expect(banner.getAttribute('data-blocking-keys')).toBe(key);
      expect(banner.textContent).toContain('Saving is blocked');
      expect(field(key).getAttribute('data-blocking')).toBe('true');
      expect(control(key).getAttribute('aria-invalid')).toBe('true');
      expect(within(field(key)).getByText(/Required · not set/)).toBeTruthy();
      expect(onBlockingChange).toHaveBeenLastCalledWith([key]);

      // set it, and the block clears
      if (type === 'SELECT') {
        await user.selectOptions(control(key), 'percent_of_equity');
      } else {
        await user.type(control(key), '0.25');
      }
      expect(screen.queryByTestId('parameter-form-blocking')).toBeNull();
      expect(control(key).getAttribute('aria-invalid')).toBe('false');
      expect(onBlockingChange).toHaveBeenLastCalledWith([]);
    });
  });

  it('refuses to prefill even if a descriptor wrongly carries a default', () => {
    // `ParamSpec.__post_init__` rejects this shape, so it cannot reach a client from the
    // real registry. The form does not rely on that: it withholds the value regardless.
    render(
      <Harness
        params={[
          spec({ key: 'quantity', label: 'Size', type: 'NUMBER', required: true, default: 1 }),
          spec({ key: 'symbol', label: 'Symbol', type: 'SYMBOL', required: true, default: 'BTC/USDT' }),
          spec({
            key: 'quantity_type',
            label: 'Size expressed as',
            type: 'SELECT',
            required: true,
            default: 'percent_of_equity',
            options: ['base_amount', 'percent_of_equity'],
          }),
        ]}
      />,
    );

    expect(control('quantity').value).toBe('');
    expect(control('symbol').value).toBe('');
    expect(control('quantity_type').value).toBe('');
    expect(screen.queryByTestId('default-badge-quantity')).toBeNull();
    expect(screen.getByTestId('parameter-form').getAttribute('data-blocking-count')).toBe('3');
  });

  it('blockingParamKeys reports the same set without a DOM', () => {
    const params = [
      spec({ key: 'symbol', type: 'SYMBOL', required: true }),
      spec({ key: 'timeframe', type: 'TIMEFRAME', required: true }),
      spec({ key: 'window', type: 'INTEGER', required: true, default: 14 }),
      spec({ key: 'client_tag', type: 'TEXT', required: false }),
    ];
    expect(blockingParamKeys(params, {})).toEqual(['symbol', 'timeframe']);
    expect(blockingParamKeys(params, { symbol: 'BTC/USDT', timeframe: '15m' })).toEqual([]);
    expect(blockingParamKeys(params, { symbol: '   ' })).toEqual(['symbol', 'timeframe']);
  });

  it('an unset boolean with no declared default is not silently false', () => {
    render(<Harness params={[spec({ key: 'direction', label: 'Direction', type: 'BOOLEAN', required: true })]} />);
    const el = control('direction');
    expect(el.tagName).toBe('SELECT');
    expect(el.value).toBe('');
    expect(screen.getByTestId('parameter-form-blocking')).toBeTruthy();
  });

  it('an empty multi-selection is unset, not a choice', () => {
    expect(isUnset([])).toBe(true);
    expect(isUnset([1])).toBe(false);
    const columns = spec({ key: 'columns', type: 'MULTISELECT', required: true, options_source: 'upstream_matrix_columns' });
    expect(describeField(columns, { columns: [] }).blocking).toBe(true);
    expect(describeField(columns, { columns: ['ret_1'] }).blocking).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Requirement 8.9 — the backend's fix_hint is the only message
// ---------------------------------------------------------------------------

describe('inline validation uses the backend fix_hint (Requirement 8.9)', () => {
  const issue = (overrides) => ({
    code: 'PARAM_OUT_OF_RANGE',
    severity: 'error',
    node_id: 'n-1',
    edge_id: null,
    field: 'window',
    message: 'RSI period must be between 2 and 500. Got 0.',
    expected: '2..500',
    actual: 0,
    fix_hint: 'Set the RSI period to a value between 2 and 500.',
    ...overrides,
  });

  const params = [
    spec({ key: 'window', label: 'Window', type: 'INTEGER', default: 14, min: 2, max: 500 }),
    spec({ key: 'source', label: 'Source', type: 'SELECT', options: ['close', 'open'], default: 'close' }),
  ];

  it('renders the fix_hint verbatim against the named field only', () => {
    render(<ParameterForm params={params} values={{ window: 0 }} nodeId="n-1" issues={[issue()]} />);

    const target = screen.getByTestId('issue-window');
    expect(target.textContent).toContain('Set the RSI period to a value between 2 and 500.');
    expect(target.textContent).toContain('RSI period must be between 2 and 500. Got 0.');
    expect(screen.queryByTestId('issue-source')).toBeNull();
    expect(control('window').getAttribute('aria-describedby')).toContain(target.id);
    expect(control('window').getAttribute('aria-invalid')).toBe('true');
    expect(control('source').getAttribute('aria-invalid')).toBe('false');
  });

  it('authors no parallel message: a blocking field with a backend issue shows only backend text', () => {
    render(
      <ParameterForm
        params={[spec({ key: 'quantity', label: 'Size', type: 'NUMBER', required: true })]}
        values={{}}
        nodeId="n-9"
        issues={[issue({ field: 'quantity', node_id: 'n-9', fix_hint: 'Enter a position size.', message: 'quantity is required.' })]}
      />,
    );

    expect(screen.getByTestId('issue-quantity').textContent).toContain('Enter a position size.');
    expect(screen.queryByText(/Required · not set/)).toBeNull();
  });

  it('falls back to the backend message when fix_hint is empty, never to client copy', () => {
    render(<ParameterForm params={params} values={{ window: 0 }} nodeId="n-1" issues={[issue({ fix_hint: '' })]} />);
    expect(screen.getByTestId('issue-window').textContent).toContain('RSI period must be between 2 and 500. Got 0.');
  });

  it('normalises both backend severity vocabularies and fails closed', () => {
    // `registry.py` emits "ERROR"; `schema.py` emits "error"; the validator fails closed.
    expect(normaliseSeverity('ERROR')).toBe('error');
    expect(normaliseSeverity('error')).toBe('error');
    expect(normaliseSeverity('WARNING')).toBe('warning');
    expect(normaliseSeverity('warn')).toBe('warning');
    expect(normaliseSeverity(null)).toBe('error');
    expect(normaliseSeverity('nonsense')).toBe('error');

    render(<ParameterForm params={params} values={{ window: 3 }} nodeId="n-1" issues={[issue({ severity: 'WARNING', code: 'WARMUP_EXCEEDS_HISTORY' })]} />);
    const rendered = screen.getByTestId('issue-window').firstChild;
    expect(rendered.getAttribute('data-severity')).toBe('warning');
    expect(rendered.getAttribute('role')).toBe('status');
    // a warning is not blocking, so the control is not marked invalid
    expect(control('window').getAttribute('aria-invalid')).toBe('false');
  });

  it('ignores an issue that names a different node', () => {
    render(<ParameterForm params={params} values={{ window: 0 }} nodeId="n-2" issues={[issue()]} />);
    expect(screen.queryByTestId('issue-window')).toBeNull();
  });

  it('surfaces an issue naming a field this block does not publish', () => {
    render(<ParameterForm params={params} values={{}} nodeId="n-1" issues={[issue({ field: 'ghost' })]} />);
    expect(screen.getByTestId('unmatched-issues').textContent).toContain('ghost');
  });
});

// ---------------------------------------------------------------------------
// Accessibility associations
// ---------------------------------------------------------------------------

describe('accessibility associations', () => {
  it('labels every control and associates help, constraints and errors', () => {
    render(
      <ParameterForm
        params={[
          spec({
            key: 'window',
            label: 'Window',
            type: 'INTEGER',
            required: true,
            default: 14,
            min: 2,
            max: 500,
            step: 1,
            unit: 'bars',
            example: 14,
            help: 'Bars in the average.',
          }),
        ]}
        values={{ window: 0 }}
        nodeId="n-1"
        issues={[
          {
            code: 'PARAM_OUT_OF_RANGE',
            severity: 'error',
            node_id: 'n-1',
            edge_id: null,
            field: 'window',
            message: 'Window must be between 2 and 500. Got 0.',
            expected: '2..500',
            actual: 0,
            fix_hint: 'Use a window between 2 and 500.',
          },
        ]}
        blockId="rsi"
      />,
    );

    const input = screen.getByLabelText('Window');
    const label = document.querySelector(`label[for="${input.id}"]`);
    expect(label).toBeTruthy();
    expect(label.getAttribute('for')).toBe(input.id);

    const describedBy = input.getAttribute('aria-describedby').split(' ');
    expect(describedBy).toContain(screen.getByTestId('help-window').id);
    expect(describedBy).toContain(screen.getByTestId('issue-window').id);
    describedBy.forEach((id) => expect(document.getElementById(id)).toBeTruthy());

    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(input.getAttribute('aria-required')).toBe('true');
    expect(screen.getByRole('group', { name: 'Parameters for rsi' })).toBeTruthy();
    expect(screen.getByTestId('required-window').textContent).toBe('required');
  });

  it('every control has a unique id even when two forms are mounted', () => {
    render(
      <>
        <ParameterForm params={[spec({ key: 'window', label: 'Window', type: 'INTEGER', default: 14 })]} values={{}} nodeId="n-1" />
        <ParameterForm params={[spec({ key: 'window', label: 'Window', type: 'INTEGER', default: 14 })]} values={{}} nodeId="n-2" />
      </>,
    );

    const ids = Array.from(document.querySelectorAll('[data-param-key="window"]')).map((el) => el.id);
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(2);
  });

  it('renders an explicit empty state rather than nothing', () => {
    render(<ParameterForm params={[]} values={{}} />);
    expect(screen.getByTestId('parameter-form-empty')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Real backend descriptor payloads
// ---------------------------------------------------------------------------

describe('real registry descriptors (BlockRegistry.to_dict())', () => {
  it('ohlcv_feed: symbol and timeframe are empty and blocking; benign defaults show as defaults', () => {
    const feed = descriptors.ohlcv_feed;
    const onBlockingChange = vi.fn();
    render(<Harness params={feed.params} blockId={feed.block_id} onBlockingChange={onBlockingChange} />);

    expect(control('symbol').value).toBe('');
    expect(control('symbol').getAttribute('placeholder')).toBe('BTC/USDT');
    expect(control('timeframe').value).toBe('');
    expect(control('timeframe').getAttribute('placeholder')).toBe('15m');
    expect(onBlockingChange).toHaveBeenLastCalledWith(['symbol', 'timeframe']);

    // market_type and mode are required *with* defaults: visible, defaulted, not blocking
    expect(control('market_type').value).toBe('spot');
    expect(screen.getByTestId('default-badge-market_type')).toBeTruthy();
    expect(control('mode').value).toBe('streaming');
    expect(screen.getByTestId('default-badge-mode')).toBeTruthy();

    // warmup_bars / history_start / history_end are optional -> Advanced
    expect(screen.queryByLabelText('Warmup bars')).toBeNull();
    expect(screen.getByTestId('advanced-toggle').textContent).toContain('Advanced (3)');
  });

  it('ohlcv_feed: help text comes from the descriptor verbatim', () => {
    const symbol = descriptors.ohlcv_feed.params.find((p) => p.key === 'symbol');
    render(<Harness params={descriptors.ohlcv_feed.params} />);
    expect(screen.getByTestId('help-symbol').textContent).toBe(symbol.help);
  });

  it('action_buy_market: quantity and quantity_type block; the SELECT starts blank', async () => {
    const user = userEvent.setup();
    const buy = descriptors.action_buy_market;
    render(<Harness params={buy.params} blockId={buy.block_id} />);

    expect(screen.getByTestId('parameter-form').getAttribute('data-blocking-count')).toBe('2');
    expect(control('quantity').value).toBe('');
    expect(control('quantity').getAttribute('min')).toBe('0');
    const qtyType = control('quantity_type');
    expect(qtyType.value).toBe('');
    expect(Array.from(qtyType.options).map((o) => o.value)).toEqual([
      '',
      'base_amount',
      'quote_notional',
      'percent_of_equity',
      'percent_of_free_balance',
      'fixed_notional',
    ]);

    await user.selectOptions(qtyType, 'percent_of_equity');
    await user.type(control('quantity'), '0.25');
    expect(screen.queryByTestId('parameter-form-blocking')).toBeNull();
  });

  it('action_buy_limit: price is required with no default and blocks', () => {
    const limit = descriptors.action_buy_limit;
    const price = limit.params.find((p) => p.key === 'price');
    expect(price.required).toBe(true);
    expect(price.default).toBeNull();

    render(<Harness params={limit.params} blockId={limit.block_id} />);
    expect(control('price').value).toBe('');
    expect(screen.getByTestId('parameter-form-blocking').getAttribute('data-blocking-keys')).toContain('price');
  });

  it('macd: integer ranges and steps from the descriptor reach the controls', () => {
    const macd = descriptors.macd;
    render(<Harness params={macd.params} blockId={macd.block_id} />);

    macd.params.forEach((param) => {
      const el = control(param.key);
      expect(el).toBeTruthy();
      expect(el.getAttribute('min')).toBe(String(param.min));
      expect(el.getAttribute('max')).toBe(String(param.max));
      expect(el.getAttribute('step')).toBe(String(param.step));
      expect(el.value).toBe(String(param.default));
      expect(screen.getByTestId(`default-badge-${param.key}`)).toBeTruthy();
    });
    expect(screen.queryByTestId('parameter-form-blocking')).toBeNull();
  });

  it('feat_lag: a MULTISELECT default is preselected and numeric option types survive', async () => {
    const user = userEvent.setup();
    const lag = descriptors.feat_lag;
    const lags = lag.params.find((p) => p.key === 'lags');
    expect(lags.type).toBe('MULTISELECT');

    const onChange = vi.fn();
    render(<ParameterForm params={lag.params} values={{ lags: lags.default }} onChange={onChange} blockId={lag.block_id} />);

    const select = control('lags');
    expect(Array.from(select.selectedOptions).map((o) => o.value)).toEqual(lags.default.map(String));
    expect(screen.getByTestId('default-badge-lags')).toBeTruthy();

    await user.deselectOptions(select, String(lags.default[0]));
    const emitted = onChange.mock.calls.at(-1)[1];
    expect(emitted.every((v) => typeof v === 'number')).toBe(true);
  });

  it('feat_time: a string MULTISELECT keeps its declared option set', () => {
    const time = descriptors.feat_time;
    const parts = time.params.find((p) => p.key === 'parts');
    render(<Harness params={time.params} initialValues={{ parts: parts.default }} />);
    expect(Array.from(control('parts').options).map((o) => o.value)).toEqual(parts.options.map(String));
  });

  it('feat_select: options_source is stated, resolved from the caller, and blocks until chosen', async () => {
    const user = userEvent.setup();
    const featSelect = descriptors.feat_select;
    const columns = featSelect.params.find((p) => p.key === 'columns');
    expect(columns.options).toBeNull();
    expect(columns.options_source).toBe('upstream_matrix_columns');

    const { unmount } = render(<Harness params={featSelect.params} blockId={featSelect.block_id} />);
    expect(screen.getByText(/upstream_matrix_columns/)).toBeTruthy();
    expect(Array.from(control('columns').options)).toHaveLength(0);
    expect(screen.getByTestId('parameter-form-blocking').getAttribute('data-blocking-keys')).toContain('columns');
    unmount();

    render(
      <Harness
        params={featSelect.params}
        blockId={featSelect.block_id}
        resolveOptions={(s) => (s.options_source === 'upstream_matrix_columns' ? ['ret_1', 'ret_5'] : null)}
      />,
    );
    expect(Array.from(control('columns').options).map((o) => o.value)).toEqual(['ret_1', 'ret_5']);
    await user.selectOptions(control('columns'), 'ret_5');
    expect(screen.queryByTestId('parameter-form-blocking')).toBeNull();
  });

  it('xgboost: the real help="" hyperparameters degrade without an empty tooltip', () => {
    const xgb = descriptors.xgboost;
    const blank = xgb.params.filter((p) => !p.help);
    expect(blank.length).toBeGreaterThan(0);

    render(<Harness params={xgb.params} blockId={xgb.block_id} />);
    blank.forEach((param) => {
      expect(screen.queryByTestId(`help-${param.key}`)).toBeNull();
      expect(screen.queryByRole('button', { name: `Help for ${param.label}` })).toBeNull();
      const el = control(param.key);
      if (el) {
        expect((el.getAttribute('aria-describedby') || '')).not.toContain(`${param.key}-help`);
      }
    });
  });

  it('between: a BOOLEAN with a declared default renders a checkbox in its default state', () => {
    const between = descriptors.between;
    const inclusive = between.params.find((p) => p.key === 'inclusive');
    expect(inclusive.default).toBe(true);

    render(<Harness params={between.params} blockId={between.block_id} />);
    const box = control('inclusive');
    expect(box.type).toBe('checkbox');
    expect(box.checked).toBe(true);
    expect(box.hasAttribute('required')).toBe(false);
    expect(box.getAttribute('aria-required')).toBe('true');
    expect(screen.getByTestId('default-badge-inclusive')).toBeTruthy();
  });

  it('every param of every captured descriptor gets a labelled control, none unsupported', async () => {
    const user = userEvent.setup();

    for (const descriptor of Object.values(descriptors)) {
      render(
        <Harness
          params={descriptor.params}
          blockId={descriptor.block_id}
          resolveOptions={() => ['col_a', 'col_b']}
        />,
      );

      // Required fields are visible without expanding (5.3); expand Advanced for the rest.
      const toggle = screen.queryByTestId('advanced-toggle');
      if (toggle) await user.click(toggle);

      for (const param of descriptor.params) {
        const where = `${descriptor.block_id}.${param.key}`;
        expect(PARAM_TYPES, where).toContain(param.type);

        const el = control(param.key);
        expect(el, where).toBeTruthy();
        expect(document.querySelector(`label[for="${el.id}"]`), `${where} label`).toBeTruthy();
        expect(screen.queryByTestId(`unsupported-${param.key}`), `${where} unsupported`).toBeNull();

        // 5.4 holds across the whole real payload: no behaviour-changing param is prefilled.
        if (BEHAVIOUR_CHANGING_PARAMS.includes(param.key)) {
          expect(el.value, where).toBe('');
          expect(field(param.key).getAttribute('data-blocking'), where).toBe('true');
        }
      }

      cleanup();
    }
  });
});
