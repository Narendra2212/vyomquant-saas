/**
 * Feed state and data quality API Module — task 7.11, Requirements 19.7, 19.8, 19.9, 19.10.
 *
 * Endpoint: `GET /api/strategy-operations/strategies/{id}/data-quality`
 * (`backend_app/routers/strategy_operations.py`, task 7.4). Its body is
 *
 *   { strategy_id, version_id, version, dag_hash, compiler_version,
 *     market: { symbol, timeframe },
 *     warmup_bars,
 *     feed: FeedStateReport.to_dict(),
 *     data_quality: { available, status, report, ..., window },
 *     computed_at }
 *
 * and its `feed` half is `backend_app/backend/feed_state.py`'s wire form:
 *
 *   { state, reason, display, age_seconds, age_text, last_event_at, timeframe,
 *     expected_interval_seconds, measured, delayed_at_intervals, stale_at_intervals,
 *     delayed_after_seconds, stale_after_seconds, connected, available_bars, warmup_bars,
 *     bars_missing, age_state, observation_source, observed_at, detail }
 *
 * This module is the network boundary and nothing else. It classifies nothing: the five-state
 * vocabulary, the 1.5x / 3x boundaries, the precedence between a stale feed and an unfilled
 * warmup, and the `display` sentence are all the server's, because the classifier that decides
 * is the one that must be quoted. `lib/graphValidation.js` → `feedStateFromReport` projects the
 * body onto the status strip, and `pages/StrategyBuilder.jsx` owns the timer; neither of them
 * needs a network to be asserted.
 *
 * Why this calls the shared axios instance rather than `apiClient`'s `get()` helper
 * -------------------------------------------------------------------------------
 * Same reason `modules/assets.js` and `lib/registryClient.js` do, plus one that is specific to
 * a liveness reading:
 *
 * 1. **`get()` caches GET responses for 60 s and de-duplicates by URL.** This response's whole
 *    subject is *how old* the last candle is, and the endpoint says so on the wire with
 *    `Cache-Control: no-store`. A 60-second-old age served as the current one is exactly the
 *    defect Requirement 19.7 exists to prevent — it would let a feed that stopped read fresh
 *    for a further minute. A client-side cache here would contradict the response header it
 *    was given.
 * 2. **`get()` retries 5xx three times with exponential backoff and can trip a circuit
 *    breaker.** The strip's honest answer to a failure is "unknown" *now*, not seven seconds
 *    later, and an endpoint rate-limited at `60/minute` is not one to multiply requests
 *    against.
 * 3. **`get()` resolves to `null` on some failures.** A `null` body and a body reporting
 *    `DISCONNECTED` are different statements, and the strip must be able to tell them apart.
 *
 * The shared instance still carries the session bearer token through its request interceptor
 * and still normalises errors into `ApiError` through its response interceptor, so this stays
 * an authenticated, read-only call with the platform's error shape. Nothing here writes.
 */

import client from '../../apiClient';

/** The endpoint, as one string, so a caller cannot assemble a near-miss of it. */
export const DATA_QUALITY_PATH = (strategyId) =>
  `/api/strategy-operations/strategies/${encodeURIComponent(strategyId)}/data-quality`;

/**
 * The server's own limit on this route (`@limiter.limit("60/minute")`), stated here so the
 * caller that owns the refresh timer can be read against it rather than guessing.
 */
export const DATA_QUALITY_LIMIT_PER_MINUTE = 60;

/**
 * The historical-window check the Backtester's Period panel reads (task 23.1).
 *
 * `POST /api/backtests/validate-data` — `strategy_operations.validate_historical_data`,
 * backed by `BacktestService.validate_historical_data`. `strategy_operations.router` is
 * mounted at `/api` in `main.py` and the handler carries the single decorator
 * `@router.post("/backtests/validate-data")` with no `strategy-operations` alias, so this
 * is the declared address and the prefixed spelling resolves to nothing.
 */
export const BACKTEST_WINDOW_PATH = '/api/backtests/validate-data';

/** The server's own limit on that route (`@limiter.limit("100/minute")`). */
export const BACKTEST_WINDOW_LIMIT_PER_MINUTE = 100;

export const dataQualityApi = {
  /**
   * Feed state and the data quality report for one saved strategy's configured data source.
   *
   * @param {string} strategyId A **saved** strategy id. An unsaved canvas has no id and so has
   *   nothing to ask about; that is "not applicable", not an error, and the caller is expected
   *   to decide it rather than send a request with `undefined` in the path — hence the throw.
   * @param {object} [options]
   * @param {AbortSignal} [options.signal] Aborts a superseded or unmounted read, so a slow
   *   answer cannot land on a canvas that has moved on.
   * @returns {Promise<{status: number, data: object|null}>} Rejects with `apiClient`'s
   *   `ApiError`, carrying `status` and the backend's own detail body on `data` — which is
   *   where `STRATEGY_NOT_FOUND`, `DATA_QUALITY_GRAPH_UNAVAILABLE` and
   *   `STRATEGY_VALIDATION_FAILED` live.
   */
  forStrategy: async (strategyId, options = {}) => {
    if (typeof strategyId !== 'string' || strategyId.trim() === '') {
      throw new Error('dataQualityApi.forStrategy requires a saved strategy id');
    }
    const response = await client.get(DATA_QUALITY_PATH(strategyId.trim()), {
      headers: { Accept: 'application/json' },
      signal: options.signal,
    });
    return {
      status: response?.status ?? 200,
      data: response?.data ?? null,
    };
  },

  /**
   * What historical data exists for one market, one bar interval and one window.
   *
   * The body is the service's own verdict and nothing is re-derived from it here:
   *
   *   { valid, issues: [{code, message, severity, …}], warnings: [{…}],
   *     data_info: { total_candles, date_range, columns } }
   *
   * `valid` is `false` only for the conditions the service itself calls disqualifying —
   * no data in the window, duplicate timestamps, non-positive OHLCV, out-of-order
   * timestamps, fewer candles than the warmup needs — and each one arrives with the
   * sentence the service wrote for it. The Backtester renders those sentences verbatim
   * rather than summarising them, because the service is the thing that knows.
   *
   * EVERY ARGUMENT IS REQUIRED, AND THE THROW IS THE POINT. The handler reads the body
   * with `body.get("symbol", "BTC/USDT")` and `body.get("timeframe", "1h")`, so a request
   * missing either is answered *for a market and an interval the caller never named* — a
   * report about BTC/USDT hourly candles presented as a report about whatever the trader
   * had selected. There is no honest partial request to this route, so a partial one is
   * refused here instead of being sent.
   *
   * Why the shared instance rather than `apiClient`'s `post()` helper: the helper mints an
   * idempotency key and retries, both of which belong to a WRITE. This POST writes
   * nothing — it is a read whose parameters are too long for a query string — and its
   * honest answer to a failure is "the check could not be completed" now, not three
   * attempts later against a route rate-limited at 100/minute.
   *
   * @param {{symbol: string, timeframe: string, startDate: string, endDate: string}} window
   * @param {object} [options]
   * @param {AbortSignal} [options.signal] Aborts a superseded read, so an answer about an
   *   older window cannot land on a newer one.
   * @returns {Promise<object|null>} The verdict body. Rejects with `apiClient`'s `ApiError`
   *   carrying the backend's own `DATA_VALIDATION_FAILED` detail on `data`.
   */
  forBacktestWindow: async ({ symbol, timeframe, startDate, endDate } = {}, options = {}) => {
    const named = { symbol, timeframe, start_date: startDate, end_date: endDate };
    const missing = Object.entries(named)
      .filter(([, value]) => typeof value !== 'string' || value.trim() === '')
      .map(([key]) => key);
    if (missing.length > 0) {
      throw new Error(
        `dataQualityApi.forBacktestWindow requires ${missing.join(', ')}: the route defaults `
          + 'an absent symbol to BTC/USDT and an absent timeframe to 1h, so a partial request '
          + 'is answered about a market nobody chose.',
      );
    }
    const response = await client.post(
      BACKTEST_WINDOW_PATH,
      Object.fromEntries(Object.entries(named).map(([key, value]) => [key, value.trim()])),
      { headers: { Accept: 'application/json' }, signal: options.signal },
    );
    return response?.data ?? null;
  },
};

export default dataQualityApi;
