/**
 * Deploy Live, re-pointed to the versioned endpoint and gated by the preflight (task 16.1).
 *
 * Requirements 2.2, 11.5, 13.4, 13.5, 13.6.
 *
 * WHAT IS ASSERTED, AND WHY EACH PART MATTERS
 * ===========================================
 * * **The endpoint.** `POST /api/strategies/{id}/versions/{version}/deploy` — the
 *   unprefixed spelling, which is the only one `deploy_version` registers; see the
 *   `deploy_version path` block below, which pins it against the router source. Addressed
 *   to the strategy's own current version, with the
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
 *
 * TASK 17.2c — THE SURFACE MOVED, THE REQUEST DID NOT
 * ===================================================
 * The page-level block at the bottom was written against the hand-rolled modal: an inline
 * `Deploy` button, a `DEPLOYMENT ORCHESTRATION` eyebrow and a `Confirm & Deploy` footer. Task
 * 17.2a partitioned the deployment into the row's overflow menu (Requirement 4.3) and task
 * 17.2c rebuilt the modal on `ds/ConfirmDialog`, so those three handles are gone and the block
 * had been failing on the first of them. It is repaired here rather than deleted, because what
 * it asserts — the endpoint, the body, and the gate that stands in front of both — is exactly
 * what must not have changed, and the four assertions added at the end cover what did: the
 * live-versus-paper treatment (Requirements 4.3, 8.5), §8.4's real-funds acknowledgement
 * sitting ON TOP of the preflight gate rather than beside it, a label on every control
 * (Requirements 15.1, 18.4), and the capital field's own validity.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, renderHook, screen, fireEvent, waitFor, within } from '@testing-library/react';
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
// Task 17.2c: the modal's title, confirm label and real-funds sentence are `deployFlow.js`'s,
// so they are READ here rather than restated. A test that spelled them out again would pass
// while the page and the flow disagreed.
import { ACKNOWLEDGEMENT_LABEL, deployPresentation } from '../../src/lib/deployFlow';

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
    expect(url).toBe('/api/strategies/s-1/versions/1.2/deploy');
    expect(sent).toEqual(body);
    // The legacy column rides the query string; the request model has no field for it.
    expect(config).toEqual({ params: { environment: 'live' } });
  });

  it('encodes the identifiers rather than interpolating them raw', async () => {
    mockClient.post.mockResolvedValue({});
    await strategiesApi.deployVersion('a/b', 'v 1', {});
    expect(mockClient.post.mock.calls[0][0]).toBe(
      '/api/strategies/a%2Fb/versions/v%201/deploy',
    );
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// THE PATH ↔ THE REGISTRATION — the pin
// ══════════════════════════════════════════════════════════════════════════════════════

/**
 * The deploy path, asserted against the backend's own route declarations.
 *
 * A hardcoded expected string (the two cases above) says "this is the path"; it cannot say
 * "and it is the path because that is what the server registers". This block derives the
 * expectation from `backend_app/routers/strategy_operations.py` and the mount prefix in
 * `backend_app/main.py`, which is the same technique `src/api/modules/__tests__/
 * libraryApi.test.js` uses against `library.py`. The repository is a monorepo and CI runs a
 * full `actions/checkout`, so both files are on disk when this runs.
 *
 * Why it is worth deriving: `deploy_version` declares **one** route, the unprefixed
 * `/strategies/{strategy_id}/versions/{version}/deploy`, while its sibling
 * `preflight_deploy_version` registers *both* spellings (`_PREFLIGHT_PATHS`) and
 * `execute_backtest` carries two decorators. That asymmetry is what made the frontend's
 * `strategy-operations`-prefixed POST look right — the preflight GET answered, the gate
 * passed, the button enabled, and only the POST 404'd. If the backend ever adds the alias,
 * this fails and says so rather than quietly permitting either spelling.
 */
describe('strategiesApi.deployVersion path ↔ strategy_operations.deploy_version', () => {
  const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
  const ROUTER = path.join(REPO_ROOT, 'backend_app', 'routers', 'strategy_operations.py');
  const MAIN = path.join(REPO_ROOT, 'backend_app', 'main.py');

  /** Every declared route path ending in `/versions/{version}/deploy` — `/preflight` excluded. */
  const declaredDeployPaths = (source) =>
    [...source.matchAll(/@router\.post\(\s*["']([^"']+)["']/g)]
      .map((m) => m[1])
      .filter((p) => /\/versions\/\{version\}\/deploy$/.test(p));

  beforeEach(() => vi.clearAllMocks());

  it('registers exactly one spelling, and it is the unprefixed one', () => {
    const source = readFileSync(ROUTER, 'utf-8');
    expect(declaredDeployPaths(source)).toEqual([
      '/strategies/{strategy_id}/versions/{version}/deploy',
    ]);
  });

  it('mounts that router at /api', () => {
    const main = readFileSync(MAIN, 'utf-8');
    const mount = main.match(
      /include_router\(\s*strategy_operations\.router\s*,\s*prefix=["']([^"']+)["']/,
    );
    expect(mount, 'strategy_operations.router must be mounted in main.py').not.toBeNull();
    expect(mount[1]).toBe('/api');
  });

  it('posts to the mount prefix + the declared route, not the strategy-operations alias', async () => {
    const source = readFileSync(ROUTER, 'utf-8');
    const main = readFileSync(MAIN, 'utf-8');
    const prefix = main.match(
      /include_router\(\s*strategy_operations\.router\s*,\s*prefix=["']([^"']+)["']/,
    )[1];
    const expected = `${prefix}${declaredDeployPaths(source)[0]}`
      .replace('{strategy_id}', 's-1')
      .replace('{version}', '1.2');

    mockClient.post.mockResolvedValue({ success: true });
    await strategiesApi.deployVersion('s-1', '1.2', { mode: 'paper' });

    expect(mockClient.post.mock.calls[0][0]).toBe(expected);
    // Stated literally too, so the intent survives a change to the derivation above.
    expect(expected).toBe('/api/strategies/s-1/versions/1.2/deploy');
    expect(mockClient.post.mock.calls[0][0]).not.toContain('/strategy-operations/');
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

/** The two presentations the modal can carry, from the module that owns them. */
const LIVE = deployPresentation('live');
const PAPER = deployPresentation('paper');

/**
 * Open the deploy modal, and answer with the dialog element.
 *
 * Two things moved under it since task 16.1 and neither is a change to the request:
 * task 17.2a partitioned the deployment into the row's overflow menu below a divider
 * (Requirement 4.3), so it is no longer an inline `Deploy` button; and task 17.2c rebuilt the
 * modal itself as a `ds/ConfirmDialog`, so the surface is a `role="dialog"` named by
 * `deployPresentation`'s title rather than a `div` with a `DEPLOYMENT ORCHESTRATION` eyebrow.
 */
const openDeployModal = async () => {
  render(
    <MemoryRouter>
      <Strategies />
    </MemoryRouter>,
  );
  await screen.findByText('Momentum v2');
  fireEvent.click(screen.getByRole('button', { name: 'More actions for Momentum v2' }));
  fireEvent.click(screen.getByRole('menuitem', { name: LIVE.confirmLabel }));
  return screen.findByRole('dialog', { name: LIVE.title });
};

/**
 * The dialog's confirm and cancel actions, by the markers `ds/ConfirmDialog` stamps on them
 * rather than by their labels — the labels are `deployPresentation`'s and change with the
 * selected target, and the point of these helpers is that the control is the same one.
 */
const confirmButton = () => document.querySelector('[data-ds="confirm-dialog-confirm"]');
const cancelButton = () => document.querySelector('[data-ds="confirm-dialog-cancel"]');

/** No confirmation surface is mounted. `jest-dom` is not set up here, hence the query. */
const dialogClosed = () =>
  waitFor(() => expect(document.querySelector('[data-ds="confirm-dialog"]')).toBeNull());

/**
 * Tick §8.4's real-funds box.
 *
 * Present only on the Live path — `deployFlow.js` constructs the acknowledgement inside its
 * `LIVE` branch and nowhere else — and it gates the confirm control ON TOP OF
 * `preflight.deployable`, never instead of it, which is what the disabled-while-failing test
 * below asserts by ticking it and finding the control still shut.
 */
const acknowledge = () => fireEvent.click(screen.getByLabelText(ACKNOWLEDGEMENT_LABEL));

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
    // The modal names the version it would deploy, in the summary row and in the sentence
    // that says what is being bound — never a hardcoded label.
    expect(screen.getByText('v1.2')).toBeTruthy();
    expect(screen.getByRole('dialog').textContent).toContain('binds version 1.2');
  });

  it('deploys through the versioned endpoint with the binding body', async () => {
    await openDeployModal();

    acknowledge();
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
    await dialogClosed();
  });

  it('keeps Deploy disabled while a mandatory condition has not passed (Requirement 13.4)', async () => {
    mockStrategies.deployPreflight.mockResolvedValue(partiallyFailed());
    await openDeployModal();

    await waitFor(() => expect(mockStrategies.deployPreflight).toHaveBeenCalled());
    // Give the answer time to be applied, then assert it did not enable anything.
    await waitFor(() => expect(confirmButton().disabled).toBe(true));
    // And satisfying §8.4's acknowledgement does not open it either: the box is a gate ON TOP
    // of the preflight's verdict, not an alternative to it.
    acknowledge();
    expect(confirmButton().disabled).toBe(true);
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

    acknowledge();
    await waitFor(() => expect(confirmButton().disabled).toBe(false));
    fireEvent.click(confirmButton());

    const banner = await screen.findByTestId('deploy-error');
    expect(banner.textContent).toContain('Version 1.2 is DRAFT');
    // The row is still there (the modal names it too, hence "all").
    expect(screen.getAllByText('Momentum v2').length).toBeGreaterThan(0);
  });

  it('stops polling when the modal closes', async () => {
    await openDeployModal();
    await waitFor(() => expect(mockStrategies.deployPreflight).toHaveBeenCalled());

    fireEvent.click(cancelButton());
    await dialogClosed();

    const callsWhenClosed = mockStrategies.deployPreflight.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, PREFLIGHT_POLL_INTERVAL_MS + 200));
    expect(mockStrategies.deployPreflight.mock.calls.length).toBe(callsWhenClosed);
  });

  // ────────────────────────────────────────────────────────────────────────────────────
  // Task 17.2c — the presentation, which is all that changed
  // ────────────────────────────────────────────────────────────────────────────────────

  it('gives a live target the live treatment and a paper target the calm one (Req 4.3, 8.5)', async () => {
    const dialog = await openDeployModal();

    // The title, the confirm label and the intent are `deployPresentation`'s, keyed on the
    // resolved target — not written at the call site, so the two cannot be told apart wrongly.
    expect(dialog.getAttribute('data-ds-intent')).toBe('live');
    expect(confirmButton().textContent).toContain(LIVE.confirmLabel);
    expect(document.querySelector('[data-ds="environment-strip"]').getAttribute('data-environment'))
      .toBe('LIVE');
    // §8.4: a live-funds action, so the acknowledgement exists.
    expect(screen.getByLabelText(ACKNOWLEDGEMENT_LABEL)).toBeTruthy();

    fireEvent.change(within(dialog).getByLabelText('Execution mode'), {
      target: { value: 'paper' },
    });

    const paperDialog = await screen.findByRole('dialog', { name: PAPER.title });
    expect(paperDialog.getAttribute('data-ds-intent')).toBe('neutral');
    expect(document.querySelector('[data-ds="environment-strip"]').getAttribute('data-environment'))
      .toBe('PAPER');
    // Requirement 8.4 / Property 14: off the Live path the real-funds statement is not merely
    // hidden — `deployFlow.js` never constructs it, so there is nothing to find.
    expect(screen.queryByLabelText(ACKNOWLEDGEMENT_LABEL)).toBeNull();
    // A paper binding names no exchange account, so no control for one is rendered.
    expect(screen.queryByLabelText('Connected exchange account')).toBeNull();
  });

  it('associates a visible label with every control in the dialog (Req 15.1, 18.4)', async () => {
    const dialog = await openDeployModal();
    await waitFor(() => expect(within(dialog).getByLabelText('Connected exchange account')).toBeTruthy());

    for (const label of ['Execution mode', 'Connected exchange account', 'Capital ($)', 'Trade size %']) {
      const control = within(dialog).getByLabelText(label);
      expect(control.id, label).toBeTruthy();
      expect(dialog.querySelector(`label[for="${control.id}"]`), label).toBeTruthy();
    }

    // And nothing is left nameless: every control in the dialog, including the
    // acknowledgement checkbox, is bound to a `<label for>`. This is the assertion the three
    // `label-has-associated-control` findings this task cleared were about.
    const controls = [...dialog.querySelectorAll('input, select, textarea')];
    expect(controls.length).toBeGreaterThanOrEqual(5);
    for (const control of controls) {
      expect(dialog.querySelector(`label[for="${control.id}"]`), control.outerHTML).toBeTruthy();
    }
  });

  it('refuses a capital that is not a positive number, and says why', async () => {
    const dialog = await openDeployModal();
    acknowledge();
    await waitFor(() => expect(confirmButton().disabled).toBe(false));

    const capital = within(dialog).getByLabelText('Capital ($)');
    fireEvent.change(capital, { target: { value: '0' } });
    fireEvent.blur(capital);

    expect(confirmButton().disabled).toBe(true);
    expect(within(dialog).getByText(/Capital must be a positive number/)).toBeTruthy();

    // The hole the old `Number(capital) <= 0` spelling would have opened once the control
    // stopped being a native `type="number"`: `Number('abc')` is NaN, and `NaN <= 0` is false.
    fireEvent.change(capital, { target: { value: 'abc' } });
    fireEvent.blur(capital);
    expect(confirmButton().disabled).toBe(true);
    fireEvent.click(confirmButton());
    expect(mockStrategies.deployVersion).not.toHaveBeenCalled();
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
