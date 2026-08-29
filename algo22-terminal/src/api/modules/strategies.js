/**
 * Strategies API Module
 * 
 * Endpoints: /api/strategies/*
 */
import { get, post, put, del } from '../../apiClient';

/**
 * @typedef {Object} StrategyNode
 * @property {string} id
 * @property {string} type
 * @property {string} label
 * @property {Object} [params]
 */

/**
 * @typedef {Object} BacktestPayload
 * @property {string[]} strategies - Strategy names (e.g., ["rsi", "macd"])
 * @property {string[]} symbols - Trading pairs (e.g., ["BTCUSDT"])
 * @property {string} timeframe
 * @property {number} initial_capital
 * @property {number} trade_size_pct - Decimal (0.1 = 10%)
 * @property {number} stop_loss_pct - Decimal (0.02 = 2%)
 * @property {number} take_profit_pct - Decimal (0.04 = 4%)
 * @property {number} ml_threshold
 * @property {Object} [params] - Extra params including DAG config
 */

/**
 * @typedef {Object} BacktestResult
 * @property {number} total_return_pct
 * @property {number} final_equity
 * @property {number} total_trades
 * @property {number} win_rate_pct
 * @property {number} total_pnl
 * @property {number} max_drawdown_pct
 * @property {number} profit_factor
 * @property {number} sharpe_ratio
 * @property {number} sortino_ratio
 * @property {number} calmar_ratio
 * @property {Array} equity
 */

/**
 * @typedef {Object} DeployResponse
 * @property {string} status
 * @property {string} message
 */

/**
 * @typedef {Object} BacktestExecuteBody
 * @property {string} [version_id] - Immutable version to execute; omitted means "current".
 * @property {string} start_date - ISO date.
 * @property {string} end_date - ISO date.
 * @property {number} initial_capital
 * @property {number} [commission]
 * @property {number} [slippage]
 * @property {number} [spread]
 * @property {number} [risk_per_trade]
 * @property {number} [max_drawdown]
 * @property {number} [daily_loss_limit]
 */

/**
 * @typedef {Object} BacktestExecuteResponse
 * @property {string} backtest_id - The persisted `strategy_backtests` row.
 * @property {string} status
 * @property {Object} results - Metrics, `charts`, and `trades`, as computed for this run.
 * @property {string} strategy_id
 * @property {string} version_id
 * @property {string} version
 * @property {Object} configuration - The configuration the engine actually applied.
 * @property {string[]} ignored_fields
 */

/**
 * The equity series the UI charts, read off an execute response.
 *
 * `BacktestRuntime` publishes the curve under `results.charts.equity_curve`, whose
 * `values` are either `{timestamp, equity}` records or bare numbers paired positionally
 * with `timestamps`. Both forms are read; neither is invented. An absent curve yields an
 * empty series rather than a fabricated one (Requirement 8.4, 9.4).
 *
 * @param {Object} [metrics] - The `results` object of a {@link BacktestExecuteResponse}.
 * @returns {Array<{timestamp: (string|number), equity: number}>}
 */
export const equitySeriesFromBacktestResults = (metrics) => {
  const curve = metrics?.charts?.equity_curve;
  const values = Array.isArray(curve?.values) ? curve.values : [];
  const timestamps = Array.isArray(curve?.timestamps) ? curve.timestamps : [];

  return values.map((value, index) => {
    if (value !== null && typeof value === 'object') {
      return {
        timestamp: value.timestamp ?? timestamps[index] ?? index,
        equity: Number(value.equity ?? 0),
      };
    }
    return { timestamp: timestamps[index] ?? index, equity: Number(value ?? 0) };
  });
};

/**
 * Adapt one execute response to the shape the Backtester already renders.
 *
 * The extended endpoint answers `{backtest_id, status, results, version, ...}` in a single
 * synchronous response, while the page reads its metrics off one flat object (and the
 * legacy job-queue path used to hand it exactly that). This is the only translation
 * between the two: the metric keys are the engine's own — nothing is renamed, defaulted,
 * or computed here — plus the charted `equity` series and the run's provenance
 * (`backtest_id`, `version`, the applied `configuration`), so the page can state what
 * produced the numbers.
 *
 * @param {BacktestExecuteResponse} [response]
 * @returns {Object|null} `null` when there is nothing to render.
 */
export const mapBacktestExecutionToUI = (response) => {
  if (!response || typeof response !== 'object') return null;
  const metrics = response.results && typeof response.results === 'object' ? response.results : {};

  return {
    ...metrics,
    equity: equitySeriesFromBacktestResults(metrics),
    backtest_id: response.backtest_id ?? null,
    run_status: response.status ?? null,
    strategy_id: response.strategy_id ?? null,
    version_id: response.version_id ?? null,
    version: response.version ?? null,
    configuration: response.configuration ?? null,
    ignored_fields: Array.isArray(response.ignored_fields) ? response.ignored_fields : [],
  };
};

export const strategiesApi = {
  /**
   * Get all strategies
   * @returns {Promise<any[]>}
   */
  list: () => get('/api/strategies'),

  /**
   * Get strategy by ID
   * @param {string} id
   * @returns {Promise<any>}
   */
  getById: (id) => get(`/api/strategies/${id}`),

  /**
   * Save a new strategy
   * @param {Object} payload - Strategy data
   * @returns {Promise<{strategy_id: string, status: string}>}
   */
  create: (payload) => post('/api/strategies', payload),

  /**
   * Update a strategy
   * @param {string} id
   * @param {Object} payload
   * @returns {Promise<any>}
   */
  update: (id, payload) => put(`/api/strategies/${id}`, payload),

  /**
   * Rename a strategy. Touches its display name and nothing else (task 16.2).
   *
   * `PUT /api/strategies/{id}/rename` (`strategies.rename_strategy`, added by task 5.3).
   * The page used to issue this same path and body through a raw `fetch` against a route
   * that did not exist anywhere in the backend, so every rename failed; the path was
   * right, the backend has caught up, and the call now travels the shared client like
   * every other strategy action (Requirement 2.6).
   *
   * The name is measured and stored **after** leading/trailing whitespace is removed:
   * 1-100 characters trimmed is accepted, anything else is a 422 carrying
   * `error: "STRATEGY_NAME_INVALID"` and a `reason` saying which bound was missed. An
   * archived strategy's identifier is a 409 `STRATEGY_ARCHIVED`, and one that is not the
   * caller's is a 404 — the same answer a non-existent id gets (Requirement 20.2).
   *
   * Only `strategies.name` is written, so every version, backtest, deployment and signal
   * record for that strategy is left as it was.
   *
   * @param {string} id
   * @param {string} name - Sent as submitted; the server trims it and stores the trimmed form.
   * @returns {Promise<any>} The updated strategy record.
   */
  rename: (id, name) => put(`/api/strategies/${encodeURIComponent(id)}/rename`, { name }),

  /**
   * Delete a strategy
   * @param {string} id
   * @returns {Promise<{status: string, deleted: string}>}
   */
  delete: (id) => del(`/api/strategies/${id}`),

  /**
   * Deploy/start a strategy
   * @param {string} id
   * @param {Object} [body={}]
   * @param {Object} [options={}] - Optional config including idempotencyKey
   * @returns {Promise<DeployResponse>}
   */
  deploy: (id, body = {}, options = {}) => {
    const headers = {};
    if (options.idempotencyKey) {
      headers['Idempotency-Key'] = options.idempotencyKey;
    }
    return post(`/api/strategies/${id}/deploy`, body, { headers });
  },

  /**
   * Deploy one immutable version through the Deployment_Gate (task 16.1).
   *
   * `POST /api/strategy-operations/strategies/{id}/versions/{version}/deploy`
   * (`strategy_operations.deploy_version`). This is the endpoint Requirement 11.5 names:
   * it runs `evaluate_binding`, the same gate the preflight below reports on, and it
   * records the Deployment_Binding — version, exchange account, risk configuration,
   * execution configuration and mode — as one row (Requirement 13.1). It replaces
   * {@link strategiesApi.deploy}, the legacy `POST /api/strategies/{id}/deploy` path,
   * which binds nothing and does not travel that gate.
   *
   * `body` is the server's `DeploymentBindingRequest`, which declares `extra="forbid"`:
   * a field outside `{mode, exchange_account_id, risk_config_id, execution_config,
   * gap_strategy}` is a 422 rather than a silently dropped setting. Build it with
   * `deployBindingBody()` from `src/lib/deployPreflight.js` so the deploy and the
   * preflight describe the same deployment.
   *
   * `environment` is the legacy column and rides the query string, because the request
   * model has no field for it; `mode` in the body is the constrained one.
   *
   * **No credential travels here.** An exchange account is named by id; its keys are
   * resolved inside the execution process (Requirement 12.5).
   *
   * @param {string} strategyId
   * @param {string} version - The version label, e.g. `"1.2"`.
   * @param {{mode?: string, exchange_account_id?: string, risk_config_id?: string,
   *   execution_config?: Object, gap_strategy?: string}} [body]
   * @param {{environment?: string}} [options]
   * @returns {Promise<any>} `{deployment, binding, success, message}`.
   */
  deployVersion: (strategyId, version, body = {}, options = {}) =>
    post(
      `/api/strategy-operations/strategies/${encodeURIComponent(strategyId)}` +
        `/versions/${encodeURIComponent(version)}/deploy`,
      body,
      options.environment ? { params: { environment: options.environment } } : {},
    ),

  /**
   * Every mandatory deployment condition for one version, each with its own verdict
   * (task 8.2, consumed by tasks 16.1 and 18.1).
   *
   * `GET /api/strategy-operations/strategies/{id}/versions/{version}/deploy/preflight`
   * (`strategy_operations.preflight_deploy_version`) answers `{deployable, conditions:
   * [{name, status, detail?, code?, message?, reason?}]}`, where `status` is `passed`,
   * `failed` or `pending`. It is read-only and side-effect-free by contract — no
   * deployment row, no quota reservation, no exchange call — which is what makes it safe
   * to poll while the deploy modal is open (Requirements 13.4-13.6).
   *
   * `deployable: false` arrives as a **200**. Only a question the endpoint cannot answer
   * is a non-200: 404 for a version that is not the caller's (identical to the answer for
   * one that does not exist), 409 for an archived strategy, 422 for an
   * `execution_config` that is not an object.
   *
   * The query names the deployment being checked, so it must carry the same values the
   * deploy body will: `mode`, `exchange_account_id`, `risk_config_id`, `gap_strategy`,
   * `execution_config` and the legacy `environment`. `execution_config` is a JSON object
   * on the wire — encoded here, so callers hand over the same object they would deploy
   * with and only one place knows the transport form.
   *
   * The response cache is bypassed deliberately: Requirement 13.6 turns on this call
   * reporting a condition that has *just* started failing, which a 60-second cached body
   * could not do.
   *
   * @param {string} strategyId
   * @param {string} version
   * @param {{mode?: string, environment?: string, exchange_account_id?: string,
   *   risk_config_id?: string, gap_strategy?: string,
   *   execution_config?: Object}} [query]
   * @returns {Promise<{deployable: boolean, conditions: Array<Object>}>}
   */
  deployPreflight: (strategyId, version, query = {}) => {
    const params = {};
    for (const [key, value] of Object.entries(query)) {
      if (value === null || value === undefined || value === '') continue;
      params[key] = key === 'execution_config' && typeof value === 'object'
        ? JSON.stringify(value)
        : value;
    }
    return get(
      `/api/strategy-operations/strategies/${encodeURIComponent(strategyId)}` +
        `/versions/${encodeURIComponent(version)}/deploy/preflight`,
      { params, cache: false },
    );
  },

  /**
   * Every deployment one strategy has (task 19.1).
   *
   * `GET /api/strategy-operations/strategies/{id}/deployments`
   * (`strategy_operations.list_deployments`) answers `{strategy_id, deployments:
   * [{deployment_id, status, environment, worker, started_at, health}], total}`, scoped
   * to the caller's own deployments.
   *
   * The Signal_Trace_Page reads this to compose its subscriptions: `SIGNAL_FAMILY` is
   * scoped to `deployment_id`, so a page filtered by strategy holds one
   * `signal.{deployment_id}` channel per deployment that strategy currently has
   * (Requirements 17.4, 18.2).
   *
   * **Known limitation, not compensated for here.** This handler lists
   * `deployment_manager`'s in-process registry, which does not contain a deployment
   * started through `deploy_version` — `strategy_operations.get_deployment` says as much
   * where it reports `runtime_attached: false` for exactly those. So this can answer with
   * fewer deployments than the `strategy_deployments` table holds. Callers that must not
   * miss one union this with the `deployment_id`s their own data already names; see
   * `lib/signalTraceRealtime.deploymentIdsFromSignals`.
   *
   * @param {string} strategyId
   * @returns {Promise<{strategy_id: string, deployments: Array<Object>, total: number}>}
   */
  listDeployments: (strategyId) =>
    get(
      `/api/strategy-operations/strategies/${encodeURIComponent(strategyId)}/deployments`,
    ),

  /**
   * Start a strategy (alias for deploy)
   * @param {string} id
   * @param {Object} [body={}]
   * @param {Object} [options={}] - Optional config including idempotencyKey
   * @returns {Promise<DeployResponse>}
   */
  start: (id, body = {}, options = {}) => strategiesApi.deploy(id, body, options),

  /**
   * Stop a strategy
   * @param {string} id
   * @returns {Promise<{status: string}>}
   */
  stop: (id) => post(`/api/strategies/${id}/stop`),

  /**
   * Pause a strategy
   * @param {string} id
   * @returns {Promise<{status: string}>}
   */
  pause: (id) => post(`/api/strategies/${id}/pause`),

  /**
   * Run backtest
   * @param {BacktestPayload} payload
   * @returns {Promise<BacktestResult>}
   */
  backtest: async (payload) => {
    const res = await post('/api/strategies/backtest', payload);
    if (res && res.job_id && (res.status === 'queued' || res.status === 'running')) {
      const pollInterval = 1000;
      const maxAttempts = 60;
      for (let i = 0; i < maxAttempts; i++) {
        await new Promise((r) => setTimeout(r, pollInterval));
        const statusRes = await get(`/api/strategies/backtest/${res.job_id}`);
        if (statusRes && statusRes.status === 'completed') {
          return statusRes.result;
        }
        if (statusRes && statusRes.status === 'failed') {
          throw new Error(statusRes.error || 'Backtest execution failed');
        }
      }
      throw new Error('Backtest timed out waiting for worker');
    }
    return res;
  },

  /**
   * Every persisted version of one strategy, newest first.
   *
   * `GET /api/strategies/{id}/versions` (`strategy_operations.get_version_history`). Answers
   * an empty list for a strategy that does not exist or is not the caller's — the same
   * answer a strategy with no versions gets (Requirement 20.2).
   *
   * @param {string} strategyId
   * @returns {Promise<{strategy_id: string, versions: any[], total: number}>}
   */
  versions: (strategyId) => get(`/api/strategies/${encodeURIComponent(strategyId)}/versions`),

  /**
   * Backtest one immutable version through the canonical runtime (task 17.1).
   *
   * `POST /api/strategy-operations/strategies/{id}/backtests/execute` runs
   * `Strategy_Version → Strategy_Compiler → DAG_Engine → BacktestEngine (VectorBT) →
   * RiskEngine → BacktestService` — the same pipeline and the same persisted
   * `compiled_plan` the live runtime consumes (Requirements 5.1, 5.2). It replaces
   * {@link strategiesApi.backtest}, the legacy job-queue path, for the Backtester page.
   *
   * Three consequences for callers, all deliberate:
   *
   * * **No graph is sent.** The version's own persisted canonical graph is executed, so a
   *   client-serialized `dag` payload is neither needed nor read (Requirement 5.7).
   * * **No market is sent.** Symbol and timeframe come from the version's DATA block and
   *   the venue is the server's public feed. The request model forbids unknown fields, so
   *   an `exchange`, `symbol` or `api_key` key is a 422 rather than a silent ignore.
   * * **No polling.** The endpoint runs synchronously and returns the persisted result, so
   *   there is no `job_id` to follow.
   *
   * @param {string} strategyId
   * @param {BacktestExecuteBody} body
   * @returns {Promise<BacktestExecuteResponse>}
   */
  executeBacktest: (strategyId, body) =>
    post(
      `/api/strategy-operations/strategies/${encodeURIComponent(strategyId)}/backtests/execute`,
      body,
    ),

  /**
   * The caller's own persisted backtest runs, newest first (task 17.2).
   *
   * `GET /api/backtests` (`strategy_operations.list_backtests`, backed by
   * `BacktestService.list_backtests` — `.eq("user_id", …)` on top of RLS, so another
   * tenant's run is simply absent rather than refused, per Requirement 20.2). Each row is
   * a `strategy_backtests` record: `created_at`, `version`, `dataset`, `status`, the
   * metrics, `equity_curve`, `trades`, and the `blueprint` the run executed.
   *
   * This is what "Saved Backtest History" reads, and after task 17.2 it is the *only*
   * thing that puts a run there: `POST .../backtests/execute` creates and completes the
   * row server-side, so a successful run is already persisted by the time it answers
   * (Requirement 10.1). The page re-reads this list on completion rather than appending a
   * locally-assembled row, so the history shows what the database holds.
   *
   * PATH NOTE — deliberately `/api/backtests`, not `/api/strategy-operations/backtests`.
   * The router is mounted at `prefix="/api"` and this route is declared as `/backtests`
   * with no `strategy-operations` alias (unlike `.../backtests/execute`, which declares
   * both). The raw `fetch` this replaced used the prefixed spelling, which resolves to
   * nothing — it 404'd on every load, and because the handler only checked `res.ok` the
   * history silently stayed empty.
   *
   * @param {{strategyId?: string, limit?: number}} [params]
   * @returns {Promise<{backtests: any[], total: number}>}
   */
  listBacktests: ({ strategyId, limit } = {}) => {
    const query = new URLSearchParams();
    if (strategyId) query.set('strategy_id', strategyId);
    if (limit !== undefined) query.set('limit', String(limit));
    const suffix = query.toString();
    return get(`/api/backtests${suffix ? `?${suffix}` : ''}`);
  },

  /**
   * Validate DAG-based strategy
   * @param {Object} payload - DAG configuration
   * @returns {Promise<any>}
   */
  validate: (payload) => post('/api/strategies/validate', payload),

  /**
   * Compile a canonical StrategyGraph into a Compiled_Plan. Persists nothing.
   *
   * The body is `{blueprint, version, metadata}` where `blueprint` is a canonical
   * `schema_version: 2` graph from `lib/canonicalGraph.js` (`StrategyCompileRequest` in
   * `backend_app/routers/strategy_operations.py`). The response carries `compiled_plan`,
   * `dag_hash`, `warmup_bars` and `execution_graph`.
   *
   * @param {{blueprint: Object, version: string, metadata?: Object}} payload
   * @returns {Promise<any>}
   */
  compile: (payload) => post('/api/strategies/compile', payload),

  /**
   * Preview what one node produces, computed by the executors that run it (task 5.7,
   * Requirements 24.7 and 24.8).
   *
   * `POST /api/strategy-operations/strategies/{strategyId}/nodes/{nodeId}/preview`
   * (`backend_app/routers/strategy_operations.py`) runs the previewed node's upstream closure
   * through the real validator, the real compiler, the real `plan_to_engine_graph` and the real
   * `DAGEngine` over a **server-bounded** historical window, and answers the last N values of
   * every declared output port — plus, for a FEATURE_MATRIX, every produced column name.
   *
   * The body is `{blueprint, bars}` and the request model forbids unknown fields: an
   * `exchange`, `api_key` or account id here is a 422, not a silently ignored key (SB-06,
   * Requirement 12.1). The market comes from the graph's own DATA block; the venue is a
   * deployment fact the server resolves and never accepts or returns.
   *
   * `bars` is a *hint*. The server clamps it and states both the applied figure and the
   * ceiling, so an unbounded preview cannot be requested into existence.
   *
   * @param {string} strategyId
   * @param {string} nodeId
   * @param {{blueprint?: Object, bars?: number}} [body]
   * @returns {Promise<any>}
   */
  previewNode: (strategyId, nodeId, body = {}) =>
    post(
      `/api/strategy-operations/strategies/${encodeURIComponent(strategyId)}` +
        `/nodes/${encodeURIComponent(nodeId)}/preview`,
      body,
    ),

  /**
   * Train ML model
   * @param {string} strategyId - Strategy ID
   * @param {Object} params - Training parameters
   * @returns {Promise<{status: string, message: string}>}
   */
  trainMl: (strategyId, params) => post(`/api/strategies/${strategyId}/train`, params),

  // `getBlocks: () => get('/api/strategies/blocks')` was removed by task 3.6. That endpoint is
  // deprecated server-side and advertises its own replacement: it publishes three of the seven
  // block categories and one generic `window` parameter form for every indicator, which is
  // SB-03 and SB-04 in a single response. Block descriptors now come from
  // `src/lib/registryClient.js` → `GET /api/strategy-operations/registry/blocks`, and nothing
  // in the frontend may reach the alias again.
};

// Legacy compatibility
export const strategyEndpoints = strategiesApi;
export const strategiesEndpoints = strategiesApi;

/**
 * 🔴 STEP 5: Generate unique idempotency key for strategy deployment
 * Prevents duplicate execution when retrying failed requests
 * 
 * @param {string} strategyId - Strategy identifier
 * @returns {string} Unique idempotency key
 */
export const generateIdempotencyKey = (strategyId) => {
  // Use CSPRNG (crypto.randomUUID) — Math.random() is not cryptographically secure
  return `deploy_${strategyId}_${crypto.randomUUID()}`;
};
