/**
 * Deploy Live, re-pointed to the versioned endpoint and gated by the preflight (task 16.1).
 *
 * Requirements 2.2, 11.5, 13.4, 13.5, 13.6.
 *
 * WHAT IS ASSERTED, AND WHY EACH PART MATTERS
 * ===========================================
 * * **The endpoint.** `POST /api/strategy-operations/strategies/{id}/versions/{version}/
 *   deploy`, addressed to the strategy's own current version, with the
 *   `DeploymentBindingRequest` body — and *only* that model's fields. The legacy
 *   `POST /api/strategies/{id}/deploy` binds no account, no risk configuration and no mode
 *   and travels no gate, so a deploy still reaching it would not satisfy Requirement 11.5;
 *   and because the request model declares `extra="forbid"`, the legacy body's
 *   `capital_allocated`/`trade_size_pct`/`account_id` keys would now be a 422.
 * * **The button.** Disabled while the preflight reports anything not yet passed, enabled
 *   automatically once every condition passes, and disabled again by a later poll that
 *   reports a condition failing (Requirements 13.4, 13.5, 13.6). `pending` is treated as
 *   "not established", never as a weaker pass.
 * * **The question asked matches the deployment submitted.** The preflight query carries
 *   the same mode, account and execution config the deploy body will, so a `deployable`
 *   answer cannot be about a different binding.
 *
 * The fixtures are the shapes actually on the wire:
 * `BindingSummary.to_dict()` → `{deployable, conditions:[{name, status, detail?, code?,
 * message?, reason?}]}` with `deployment_binding.CONDITION_*`'s lowercase statuses, and the
 * first condition is `strategy_service.DEPLOY_PREREQUISITE_CONDITION`
 * (`"deploy_prerequisites"`), which the summary prepends.
 *
 * The per-condition panel is task 18.1's subject and is deliberately not asserted here.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, renderHook, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import {
  CONDITION_FAILED,
  CONDITION_PASSED,
  CONDITION_PENDING,
  EXECUTION_CONFIG_FIELDS,
  PREFLIGHT_POLL_INTERVAL_MS,
  deploymentRequest,
  describeCondition,
  describePreflightFailure,
  executionConfigFrom,
  isDeployable,
  normalizeCondition,
  normalizePreflight,
  preflightQueryKey,
} from '../../src/lib/deployPreflight';

// ══════════════════════════════════════════════════════════════════════════════════════
// FIXTURES — the wire shapes
// ══════════════════════════════════════════════════════════════════════════════════════

const CONDITION_NAMES = [
  'deploy_prerequisites',
  'version_ready',
  'plan_readable',
  'market_resolved',
  'deployment_mode',
  'execution_config',
  'gap_policy',
  'binding_storable',
  'models_verified',
  'exchange_account',
  'risk_config',
  'symbol_available',
  'timeframe_supported',
];

const allPassed = () => ({
  deployable: true,
  conditions: CONDITION_NAMES.map((name) => ({ name, status: CONDITION_PASSED })),
});

/** One failed gate and one condition it blocked, exactly as the collect-all summary reports. */
const partiallyFailed = () => ({
  deployable: false,
  conditions: [
    { name: 'deploy_prerequisites', status: CONDITION_PASSED },
    {
      name: 'exchange_account',
      status: CONDITION_FAILED,
      code: 'EXCHANGE_ACCOUNT_NOT_FOUND',
      message: 'No exchange account acct-1 belongs to this user.',
    },
    {
      name: 'symbol_available',
      status: CONDITION_PENDING,
      reason: 'blocked by exchange_account',
    },
  ],
});

// ══════════════════════════════════════════════════════════════════════════════════════
// THE PURE MODULE
// ══════════════════════════════════════════════════════════════════════════════════════

describe('normalizeCondition / normalizePreflight', () => {
  it('reads a condition without defaulting an absent optional key into existence', () => {
    expect(normalizeCondition({ name: 'version_ready', status: 'passed' })).toEqual({
      name: 'version_ready',
      status: 'passed',
      detail: null,
      code: null,
      message: null,
      reason: null,
    });
  });

  it('reports an unreadable status as unreadable rather than as a known one', () => {
    expect(normalizeCondition({ name: 'x', status: 'PASSED' }).status).toBeNull();
    expect(normalizeCondition({ name: 'x', status: 'ok' }).status).toBeNull();
    expect(normalizeCondition(null).name).toBeNull();
  });

  it('splits a partially-failed summary into what failed and what could not be checked', () => {
    const summary = normalizePreflight(partiallyFailed());

    expect(summary.deployable).toBe(false);
    expect(summary.reported).toBe(false);
    expect(summary.conditions).toHaveLength(3);
    expect(summary.failed.map((c) => c.name)).toEqual(['exchange_account']);
    expect(summary.failed[0].code).toBe('EXCHANGE_ACCOUNT_NOT_FOUND');
    expect(summary.pending.map((c) => c.name)).toEqual(['symbol_available']);
    expect(summary.pending[0].reason).toBe('blocked by exchange_account');
  });

  it('reads a fully-passed summary as deployable', () => {
    const summary = normalizePreflight(allPassed());
    expect(summary.deployable).toBe(true);
    expect(summary.failed).toEqual([]);
    expect(summary.pending).toEqual([]);
  });
});

describe('isDeployable', () => {
  it('is false when nothing was checked', () => {
    // "Nothing was checked" must never enable a deployment.
    expect(isDeployable([])).toBe(false);
    expect(isDeployable(normalizePreflight({ deployable: true, conditions: [] }))).toBe(false);
    expect(isDeployable(undefined)).toBe(false);
  });

  it('is false while any condition is failed or pending', () => {
    expect(isDeployable([{ name: 'a', status: CONDITION_PASSED }, { name: 'b', status: CONDITION_FAILED }])).toBe(false);
    expect(isDeployable([{ name: 'a', status: CONDITION_PASSED }, { name: 'b', status: CONDITION_PENDING }])).toBe(false);
  });

  it('does not trust a deployable flag its own conditions contradict', () => {
    // The flag is the server's; the rule is re-derived, so a malformed or truncated body
    // cannot enable the Deploy button.
    const summary = normalizePreflight({
      deployable: true,
      conditions: [{ name: 'version_ready', status: CONDITION_FAILED }],
    });
    expect(summary.reported).toBe(true);
    expect(summary.deployable).toBe(false);
  });

  it('is true only when every condition passed', () => {
    expect(isDeployable(allPassed().conditions)).toBe(true);
  });
});

describe('describeCondition', () => {
  it('states the gate, the verdict and the server’s own reason', () => {
    expect(describeCondition(partiallyFailed().conditions[1])).toBe(
      'exchange_account — failed: No exchange account acct-1 belongs to this user.',
    );
    expect(describeCondition(partiallyFailed().conditions[2])).toBe(
      'symbol_available — pending: blocked by exchange_account',
    );
    expect(describeCondition({ name: 'version_ready', status: 'passed' })).toBe(
      'version_ready — passed',
    );
  });
});

describe('describePreflightFailure', () => {
  it('carries the server’s message and code through', () => {
    const failure = describePreflightFailure(
      Object.assign(new Error('This strategy is archived.'), {
        status: 409,
        data: { error: 'STRATEGY_ARCHIVED', message: 'This strategy is archived.' },
      }),
    );
    expect(failure).toEqual({
      status: 409,
      code: 'STRATEGY_ARCHIVED',
      message: 'This strategy is archived.',
    });
  });

  it('does not turn a 404 into a statement about whose the version is', () => {
    // `preflight_deploy_version` answers VERSION_NOT_FOUND both for a version that does not
    // exist and for another tenant's (Requirement 20.2). Nothing here may add a distinction
    // the backend refused to make.
    const failure = describePreflightFailure({
      status: 404,
      data: { error: 'VERSION_NOT_FOUND', message: 'Version 1.2 not found.' },
    });
    expect(failure.message).toBe('Version 1.2 not found.');
    expect(failure.message).not.toMatch(/another|other user|not yours|permission|forbidden/i);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// THE DEPLOYMENT DESCRIPTION — one shape, two surfaces
// ══════════════════════════════════════════════════════════════════════════════════════

describe('deploymentRequest', () => {
  const sizing = { capital: '10000', tradeSizePct: '10' };

  it('binds the selected account for live, by identifier only', () => {
    const { body, query, mode } = deploymentRequest({
      environment: 'live',
      account: { id: 'acct-1', exchange_id: 'kraken', api_key: 'nope' },
      ...sizing,
    });

    expect(mode).toBe('live');
    expect(body).toEqual({
      mode: 'live',
      exchange_account_id: 'acct-1',
      execution_config: { max_order_notional: 1000 },
    });
    // Requirement 12.5: no credential and no exchange identity travels in the request.
    const serialized = JSON.stringify({ body, query });
    for (const forbidden of ['api_key', 'secret', 'passphrase', 'exchange_id', 'kraken']) {
      expect(serialized).not.toContain(forbidden);
    }
  });

  it('binds no account for paper, which uses none', () => {
    const { body } = deploymentRequest({
      environment: 'paper',
      account: { id: 'acct-1' },
      ...sizing,
    });
    expect(body).toEqual({ mode: 'paper', execution_config: { max_order_notional: 1000 } });
    expect(body).not.toHaveProperty('exchange_account_id');
  });

  it('sends only fields the DeploymentBindingRequest declares', () => {
    const { body } = deploymentRequest({
      environment: 'live',
      account: { id: 'acct-1' },
      capital: '10000',
      tradeSizePct: '10',
      riskConfigId: 'risk-1',
    });
    // `extra="forbid"` server-side: a field outside this set is a 422, not a dropped setting.
    for (const key of Object.keys(body)) {
      expect([
        'mode',
        'exchange_account_id',
        'risk_config_id',
        'execution_config',
        'gap_strategy',
      ]).toContain(key);
    }
    // The legacy body's fields are gone, not renamed.
    for (const legacy of ['exchange_id', 'account_id', 'environment', 'capital_allocated', 'trade_size_pct', 'max_drawdown', 'stop_loss']) {
      expect(body).not.toHaveProperty(legacy);
    }
  });

  it('asks the preflight about the same deployment it would submit, plus the legacy environment', () => {
    const { body, query } = deploymentRequest({
      environment: 'live',
      account: { id: 'acct-1' },
      ...sizing,
    });
    expect(query).toEqual({ ...body, environment: 'live' });
  });

  it('does not rewrite an unrecognised mode to the safe-looking one', () => {
    // `normalise_mode` owns that vocabulary; a client that defaulted it would deploy
    // something the author did not ask for.
    expect(deploymentRequest({ environment: 'LIVE' }).mode).toBe('live');
    expect(deploymentRequest({ environment: 'sandbox' }).body.mode).toBe('sandbox');
    expect(deploymentRequest({}).body).not.toHaveProperty('mode');
  });
});

describe('executionConfigFrom', () => {
  it('expresses the sizing inputs as the one field that limits them', () => {
    expect(executionConfigFrom({ capital: '10000', tradeSizePct: '10' })).toEqual({
      max_order_notional: 1000,
    });
    for (const key of Object.keys(executionConfigFrom({ capital: 1, tradeSizePct: 1 }))) {
      expect(EXECUTION_CONFIG_FIELDS).toContain(key);
    }
  });

  it('invents no limit from a blank or nonsensical input', () => {
    expect(executionConfigFrom({ capital: '', tradeSizePct: '10' })).toEqual({});
    expect(executionConfigFrom({ capital: '10000', tradeSizePct: '0' })).toEqual({});
    expect(executionConfigFrom({ capital: '-5', tradeSizePct: '10' })).toEqual({});
    expect(executionConfigFrom({})).toEqual({});
  });
});

describe('preflightQueryKey', () => {
  it('is stable across key order and changes with the deployment', () => {
    expect(preflightQueryKey({ mode: 'live', environment: 'live' })).toBe(
      preflightQueryKey({ environment: 'live', mode: 'live' }),
    );
    expect(preflightQueryKey({ mode: 'live', exchange_account_id: 'a' })).not.toBe(
      preflightQueryKey({ mode: 'live', exchange_account_id: 'b' }),
    );
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// THE API MODULE — the two requests on the wire
// ══════════════════════════════════════════════════════════════════════════════════════

const { mockClient, mockStrategies, mockExchange } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
  mockStrategies: {
    list: vi.fn(),
    // Asserted to stay uncalled: the legacy deploy travels no Deployment_Gate.
    deploy: vi.fn(),
    deployVersion: vi.fn(),
    deployPreflight: vi.fn(),
  },
  mockExchange: { list: vi.fn() },
}));

vi.mock('../../src/apiClient', () => ({
  default: mockClient,
  get: (...args) => mockClient.get(...args),
  post: (...args) => mockClient.post(...args),
  put: (...args) => mockClient.put(...args),
  del: (...args) => mockClient.del(...args),
}));

// eslint-disable-next-line import/first
import { strategiesApi } from '../../src/api/modules/strategies';

describe('strategiesApi.deployVersion', () => {
  beforeEach(() => vi.clearAllMocks());

  it('posts the binding to the versioned deploy path', async () => {
    mockClient.post.mockResolvedValue({ success: true });
    const body = { mode: 'live', exchange_account_id: 'acct-1' };

    await strategiesApi.deployVersion('s-1', '1.2', body, { environment: 'live' });

    expect(mockClient.post).toHaveBeenCalledTimes(1);
    const [url, sent, config] = mockClient.post.mock.calls[0];
    expect(url).toBe('/api/strategy-operations/strategies/s-1/versions/1.2/deploy');
    expect(sent).toEqual(body);
    // The legacy column rides the query string; the request model has no field for it.
    expect(config).toEqual({ params: { environment: 'live' } });
  });

  it('encodes the identifiers rather than interpolating them raw', async () => {
    mockClient.post.mockResolvedValue({});
    await strategiesApi.deployVersion('a/b', 'v 1', {});
    expect(mockClient.post.mock.calls[0][0]).toBe(
      '/api/strategy-operations/strategies/a%2Fb/versions/v%201/deploy',
    );
  });
});

describe('strategiesApi.deployPreflight', () => {
  beforeEach(() => vi.clearAllMocks());

  it('gets the preflight for one version, carrying the deployment being checked', async () => {
    mockClient.get.mockResolvedValue(allPassed());

    await strategiesApi.deployPreflight('s-1', '1.2', {
      mode: 'live',
      environment: 'live',
      exchange_account_id: 'acct-1',
      execution_config: { max_order_notional: 1000 },
    });

    const [url, config] = mockClient.get.mock.calls[0];
    expect(url).toBe(
      '/api/strategy-operations/strategies/s-1/versions/1.2/deploy/preflight',
    );
    expect(config.params).toEqual({
      mode: 'live',
      environment: 'live',
      exchange_account_id: 'acct-1',
      // A JSON object on the wire, which is what the endpoint parses.
      execution_config: '{"max_order_notional":1000}',
    });
  });

  it('bypasses the response cache, because Requirement 13.6 turns on it not being cached', async () => {
    mockClient.get.mockResolvedValue(allPassed());
    await strategiesApi.deployPreflight('s-1', '1.2', {});
    expect(mockClient.get.mock.calls[0][1].cache).toBe(false);
  });

  it('omits an unstated parameter rather than sending it empty', async () => {
    mockClient.get.mockResolvedValue(allPassed());
    await strategiesApi.deployPreflight('s-1', '1.2', {
      mode: 'paper',
      exchange_account_id: null,
      risk_config_id: '',
      gap_strategy: undefined,
    });
    expect(mockClient.get.mock.calls[0][1].params).toEqual({ mode: 'paper' });
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// THE POLL — Requirements 13.4, 13.5, 13.6
// ══════════════════════════════════════════════════════════════════════════════════════

vi.mock('../../src/api', () => ({
  endpoints: { strategies: mockStrategies, exchange: mockExchange },
}));

// eslint-disable-next-line import/first
import { useDeployPreflight } from '../../src/hooks/useDeployPreflight';

/** A short interval keeps the repeat-poll assertions fast; the page uses the real one. */
const FAST_POLL_MS = 10;

describe('useDeployPreflight', () => {
  beforeEach(() => vi.clearAllMocks());

  it('does not ask anything while the workflow is closed', async () => {
    const fetcher = vi.fn().mockResolvedValue(allPassed());
    const { result } = renderHook(() =>
      useDeployPreflight({ strategyId: 's-1', version: '1.2', enabled: false, fetcher }),
    );

    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.deployable).toBe(false);
    expect(result.current.conditions).toEqual([]);
  });

  it('does not ask about a strategy with no version to deploy', async () => {
    const fetcher = vi.fn().mockResolvedValue(allPassed());
    renderHook(() => useDeployPreflight({ strategyId: 's-1', version: null, fetcher }));
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('reports deployable once every condition has passed (Requirement 13.5)', async () => {
    const fetcher = vi.fn().mockResolvedValue(allPassed());
    const { result } = renderHook(() =>
      useDeployPreflight({
        strategyId: 's-1',
        version: '1.2',
        query: { mode: 'paper', environment: 'paper' },
        intervalMs: 0,
        fetcher,
      }),
    );

    await waitFor(() => expect(result.current.deployable).toBe(true));
    expect(fetcher).toHaveBeenCalledWith('s-1', '1.2', { mode: 'paper', environment: 'paper' });
    expect(result.current.conditions).toHaveLength(CONDITION_NAMES.length);
  });

  it('disables again when a later poll reports a condition failing (Requirement 13.6)', async () => {
    // The gate's answer changes underneath a poll that is already running, which is
    // exactly the situation 13.6 describes (a balance drops, a connection is lost).
    let answer = allPassed();
    const fetcher = vi.fn(async () => answer);

    const { result } = renderHook(() =>
      useDeployPreflight({
        strategyId: 's-1',
        version: '1.2',
        intervalMs: FAST_POLL_MS,
        fetcher,
      }),
    );

    await waitFor(() => expect(result.current.deployable).toBe(true));

    answer = partiallyFailed();
    // No reload, no user action — the next poll is what turns it off again.
    await waitFor(() => expect(result.current.deployable).toBe(false));
    expect(result.current.failed.map((c) => c.name)).toEqual(['exchange_account']);
  });

  it('holds the button closed when the gate cannot be read at all', async () => {
    const fetcher = vi.fn().mockRejectedValue(
      Object.assign(new Error('Version 1.2 not found.'), {
        status: 404,
        data: { error: 'VERSION_NOT_FOUND', message: 'Version 1.2 not found.' },
      }),
    );

    const { result } = renderHook(() =>
      useDeployPreflight({ strategyId: 's-1', version: '1.2', intervalMs: 0, fetcher }),
    );

    await waitFor(() => expect(result.current.error).not.toBeNull());
    // "Could not be checked" is not "checked and satisfied".
    expect(result.current.deployable).toBe(false);
    expect(result.current.error.code).toBe('VERSION_NOT_FOUND');
  });

  it('discards the previous answer when the deployment being checked changes', async () => {
    const fetcher = vi.fn(async (_id, _v, query) =>
      query.exchange_account_id === 'acct-1' ? allPassed() : partiallyFailed(),
    );

    const { result, rerender } = renderHook(
      ({ query }) =>
        useDeployPreflight({
          strategyId: 's-1',
          version: '1.2',
          query,
          intervalMs: 0,
          fetcher,
        }),
      { initialProps: { query: { mode: 'live', exchange_account_id: 'acct-1' } } },
    );

    await waitFor(() => expect(result.current.deployable).toBe(true));

    // A passed summary about one account may not enable a deploy of another.
    rerender({ query: { mode: 'live', exchange_account_id: 'acct-2' } });
    await waitFor(() => expect(result.current.deployable).toBe(false));
    expect(fetcher).toHaveBeenLastCalledWith('s-1', '1.2', {
      mode: 'live',
      exchange_account_id: 'acct-2',
    });
  });

  it('polls at Requirement 13.6’s interval by default', () => {
    expect(PREFLIGHT_POLL_INTERVAL_MS).toBe(2000);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// THE PAGE — the deploy modal, end to end against mocked endpoints
// ══════════════════════════════════════════════════════════════════════════════════════

// eslint-disable-next-line import/first
import Strategies from '../../src/pages/Strategies';

const strategyRow = (overrides = {}) => ({
  id: 's-1',
  name: 'Momentum v2',
  symbol: 'BTCUSDT',
  status: 'stopped',
  timeframe: '1h',
  current_version: '1.2',
  ...overrides,
});

const connectedAccount = (overrides = {}) => ({
  id: 'acct-1',
  exchange_id: 'kraken',
  name: 'Kraken Main',
  // The accounts endpoint spells this uppercase; the modal must not gate the Deploy button
  // on its own reading of that string (see the note in Strategies.jsx).
  status: 'connected',
  health: 'healthy',
  ...overrides,
});

const openDeployModal = async () => {
  render(
    <MemoryRouter>
      <Strategies />
    </MemoryRouter>,
  );
  await screen.findByText('Momentum v2');
  fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));
  await screen.findByText('DEPLOYMENT ORCHESTRATION');
};

const confirmButton = () => screen.getByRole('button', { name: /Confirm & Deploy/ });

describe('Strategies deploy modal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockStrategies.list.mockResolvedValue([strategyRow()]);
    mockExchange.list.mockResolvedValue([connectedAccount()]);
    mockStrategies.deployPreflight.mockResolvedValue(allPassed());
    mockStrategies.deployVersion.mockResolvedValue({
      success: true,
      deployment: { id: 'dep-1' },
      binding: { binding_stored: true },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('polls the preflight for the strategy’s current version while the modal is open', async () => {
    await openDeployModal();

    // The first poll fires as the modal opens and is re-asked once the account selector has
    // resolved, so the assertion is that the gate was asked about *this* deployment.
    await waitFor(() =>
      expect(mockStrategies.deployPreflight).toHaveBeenCalledWith('s-1', '1.2', {
        mode: 'live',
        environment: 'live',
        exchange_account_id: 'acct-1',
        execution_config: { max_order_notional: 1000 },
      }),
    );
    // The modal names the version it would deploy (the card names it too, hence two).
    expect(screen.getAllByText('v1.2').length).toBeGreaterThanOrEqual(2);
  });

  it('deploys through the versioned endpoint with the binding body', async () => {
    await openDeployModal();

    await waitFor(() => expect(confirmButton().disabled).toBe(false));
    fireEvent.click(confirmButton());

    await waitFor(() => expect(mockStrategies.deployVersion).toHaveBeenCalledTimes(1));
    expect(mockStrategies.deployVersion).toHaveBeenCalledWith(
      's-1',
      '1.2',
      {
        mode: 'live',
        exchange_account_id: 'acct-1',
        execution_config: { max_order_notional: 1000 },
      },
      { environment: 'live' },
    );
    // Requirement 11.5: the legacy path, which travels no gate, is not called.
    expect(mockStrategies.deploy).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText('DEPLOYMENT ORCHESTRATION')).toBeNull());
  });

  it('keeps Deploy disabled while a mandatory condition has not passed (Requirement 13.4)', async () => {
    mockStrategies.deployPreflight.mockResolvedValue(partiallyFailed());
    await openDeployModal();

    await waitFor(() => expect(mockStrategies.deployPreflight).toHaveBeenCalled());
    // Give the answer time to be applied, then assert it did not enable anything.
    await waitFor(() => expect(confirmButton().disabled).toBe(true));
    fireEvent.click(confirmButton());
    expect(mockStrategies.deployVersion).not.toHaveBeenCalled();
  });

  it('keeps Deploy disabled when the preflight itself cannot be read', async () => {
    mockStrategies.deployPreflight.mockRejectedValue(
      Object.assign(new Error('Version 1.2 not found.'), {
        status: 404,
        data: { error: 'VERSION_NOT_FOUND', message: 'Version 1.2 not found.' },
      }),
    );
    await openDeployModal();

    const banner = await screen.findByTestId('deploy-error');
    expect(banner.textContent).toContain('Version 1.2 not found.');
    expect(confirmButton().disabled).toBe(true);
    expect(mockStrategies.deployVersion).not.toHaveBeenCalled();
  });

  it('renders the server’s refusal and leaves the list unchanged', async () => {
    mockStrategies.deployVersion.mockRejectedValue(
      Object.assign(new Error('deploy refused'), {
        status: 409,
        data: {
          error: 'VERSION_NOT_DEPLOYABLE',
          message: 'Version 1.2 is DRAFT, so it cannot be deployed yet.',
        },
      }),
    );
    await openDeployModal();

    await waitFor(() => expect(confirmButton().disabled).toBe(false));
    fireEvent.click(confirmButton());

    const banner = await screen.findByTestId('deploy-error');
    expect(banner.textContent).toContain('Version 1.2 is DRAFT');
    // The row is still there (the modal header names it too, hence "all").
    expect(screen.getAllByText('Momentum v2').length).toBeGreaterThan(0);
  });

  it('stops polling when the modal closes', async () => {
    await openDeployModal();
    await waitFor(() => expect(mockStrategies.deployPreflight).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('DEPLOYMENT ORCHESTRATION')).toBeNull());

    const callsWhenClosed = mockStrategies.deployPreflight.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, PREFLIGHT_POLL_INTERVAL_MS + 200));
    expect(mockStrategies.deployPreflight.mock.calls.length).toBe(callsWhenClosed);
  });
});

describe('Strategies.jsx deploy call site', () => {
  const source = readFileSync(
    path.resolve(__dirname, '../../src/pages/Strategies.jsx'),
    'utf-8',
  );

  it('no longer reaches the legacy deploy endpoint', () => {
    // The prose above the handler still names the endpoint it was re-pointed *from*, so
    // this looks for a call, not a mention.
    expect(source).not.toMatch(/await\s+endpoints\.strategies\.deploy\(/);
    expect(source).not.toMatch(/endpoints\.strategies\.start\(/);
    expect(source).toContain('endpoints.strategies.deployVersion(');
  });

  it('takes the binding shape and the poll from the shared modules', () => {
    expect(source).toMatch(/from ["']\.\.\/lib\/deployPreflight["']/);
    expect(source).toMatch(/from ["']\.\.\/hooks\/useDeployPreflight["']/);
    expect(source).toContain('preflight.deployable');
  });

  it('does not send an exchange identifier or a credential', () => {
    // The venue is resolved from the account row server-side (Requirement 12.5).
    expect(source).not.toMatch(/exchange_id:\s*deployConfig/);
    expect(source).not.toMatch(/api_key|secret_key|passphrase/);
  });
});
