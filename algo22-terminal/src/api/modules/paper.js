/**
 * api/modules/paper.js — Paper Trading API Module
 */

import { get, post, del } from '../../apiClient';

/**
 * Query string for `GET /api/paper/sessions`.
 *
 * Both parameters are `Optional[...] = Query(None)` on `list_paper_sessions`, so an omitted
 * one must be absent from the URL rather than sent as an empty value — `?session_state=` is a
 * supplied blank label and would be refused with a 422 naming the permitted states. The names
 * are the handler's own: `session_state` (not `state`, not `status`) and `limit`.
 *
 * @param {{session_state?: string, limit?: number}} [params]
 * @returns {string} The query string including its leading `?`, or `''` when empty.
 */
const sessionListQuery = ({ session_state, limit } = {}) => {
  const query = new URLSearchParams();
  if (session_state !== undefined && session_state !== null && session_state !== '') {
    query.set('session_state', String(session_state));
  }
  if (limit !== undefined && limit !== null) {
    query.set('limit', String(limit));
  }
  const suffix = query.toString();
  return suffix ? `?${suffix}` : '';
};

export const paperApi = {
  /**
   * Get virtual paper trading account details
   */
  getAccount: () => get('/api/paper/account'),

  /**
   * Reset virtual paper trading account balance
   * @param {number} capital - New starting capital (default 100,000)
   */
  resetAccount: (capital = 100000.0) => post('/api/paper/account/reset', { capital }),

  /**
   * Get open paper trading positions
   */
  getPositions: () => get('/api/paper/positions'),

  /**
   * Get list of paper orders
   * @param {string} [status] - Filter by status (OPEN, FILLED, CANCELLED)
   */
  getOrders: (status) => get(`/api/paper/orders${status ? `?status=${status}` : ''}`),

  /**
   * Place a new paper trading order
   * @param {Object} order - { symbol, side, order_type, quantity, price, strategy_id }
   */
  placeOrder: (order) => post('/api/paper/orders', order),

  /**
   * Cancel an open paper limit order
   * @param {string} orderId
   */
  cancelOrder: (orderId) => del(`/api/paper/orders/${orderId}`),

  /**
   * Get trade execution fills history
   * @param {number} [limit=50]
   */
  getTrades: (limit = 50) => get(`/api/paper/trades?limit=${limit}`),

  /**
   * Get comprehensive paper trading performance summary
   */
  getSummary: () => get('/api/paper/summary'),

  /**
   * Paper_Session lifecycle and sub-resource reads.
   *
   * The fourteen additive routes of `backend_app/routers/paper_trading.py`, mounted in
   * `backend_app/main.py` with `prefix="/api/paper"`. Every path, verb, body field and query
   * parameter below is copied from a real `@router.<verb>` decorator and its handler
   * signature in that router — nothing here is assembled from a guess.
   *
   * Seven lifecycle routes (`POST /sessions`, `GET /sessions`, `GET /sessions/{id}`,
   * `POST /sessions/{id}/pause|resume|stop|reset`) and seven sub-resource reads
   * (`GET /sessions/{id}/orders|fills|positions|trades|equity|metrics|events`).
   *
   * The four operations take **no request body**: their handlers accept only the path
   * parameter and the authenticated identity, so the state machine — and not a field the
   * caller sends — decides the transition.
   *
   * @type {Object}
   */
  sessions: {
    /**
     * Start a Paper_Session.
     *
     * `POST /api/paper/sessions` (`start_paper_session`, 201, 10/60s). The body is
     * `extra="forbid"`: exactly one of `listing_id` / `strategy_id`, plus `symbol`,
     * `timeframe`, `initial_capital_minor` and optionally `currency` and
     * `idempotency_key`. There is no `exchange_id` field — the venue is the server's
     * `DEFAULT_EXCHANGE` — and no strategy definition, plan or `version_id` field: the
     * executable artifact is resolved server-side and never travels in either direction.
     * Any other key is a 422 that names the field and echoes no value.
     *
     * `initial_capital_minor` is strict: an exact whole number of Minor_Units. `100000.5`,
     * `"100000"` and `true` are each refused rather than rounded into a balance.
     *
     * @param {Object} body - The start request.
     * @param {string} [body.listing_id] - The Listing to run. Exactly one of this / `strategy_id`.
     * @param {string} [body.strategy_id] - The strategy to run, resolved to its Listing server-side.
     * @param {string} body.symbol - The market, e.g. `BTC/USDT`.
     * @param {string} body.timeframe - The candle timeframe, e.g. `1m`.
     * @param {number} body.initial_capital_minor - Initial simulated capital in Minor_Units, integer.
     * @param {string} [body.currency] - The Paper_Account currency. Server default when omitted.
     * @param {string} [body.idempotency_key] - Caller-supplied key, echoed back on the response.
     * @returns {Promise<{status: string, session_id: string, session: Object, idempotency_key: (string|null)}>}
     */
    create: (body) => post('/api/paper/sessions', body),

    /**
     * The authenticated caller's own Paper_Sessions, newest first.
     *
     * `GET /api/paper/sessions` (`list_paper_sessions`, 120/60s). The caller's id is a
     * predicate on the statement, so another tenant's session is never fetched rather than
     * fetched and dropped. Both query parameters are optional and omitted from the URL when
     * not supplied. An unrecognised `session_state` is a 422 naming the permitted values,
     * never a read that quietly matches nothing.
     *
     * @param {Object} [params] - Optional server-side filters.
     * @param {string} [params.session_state] - `CREATED`, `RUNNING`, `PAUSED` or `STOPPED`.
     * @param {number} [params.limit] - Rows, newest first; server-capped.
     * @returns {Promise<{sessions: Object[]}>}
     */
    list: (params) => get(`/api/paper/sessions${sessionListQuery(params)}`),

    /**
     * One of the caller's own Paper_Sessions, including its feed record.
     *
     * `GET /api/paper/sessions/{session_id}` (`get_paper_session`, 120/60s). Carries
     * `feed_state`, `market_data_source` and `event_sequence`; carries no `config`, no
     * `version_id` and no `source_strategy_id`. Another user's session and an unknown one
     * answer byte-identically with the same 404 `NOT_FOUND`, so the route is no existence
     * oracle.
     *
     * @param {string} sessionId
     * @returns {Promise<{session_id: string, session: Object}>}
     */
    get: (sessionId) => get(`/api/paper/sessions/${encodeURIComponent(sessionId)}`),

    /**
     * `RUNNING -> PAUSED`.
     *
     * `POST /api/paper/sessions/{session_id}/pause` (`pause_paper_session`, 60/60s, no body).
     * The market-data subscription stays open — that is the whole difference between a pause
     * and a stop — and no order, position or balance moves.
     *
     * @param {string} sessionId
     * @returns {Promise<{session_id: string, session_state: string}>}
     */
    pause: (sessionId) => post(`/api/paper/sessions/${encodeURIComponent(sessionId)}/pause`),

    /**
     * `PAUSED -> RUNNING`.
     *
     * `POST /api/paper/sessions/{session_id}/resume` (`resume_paper_session`, 60/60s, no
     * body). Nothing is back-filled across the pause: the candles that closed while the
     * session was paused are candles it never saw.
     *
     * @param {string} sessionId
     * @returns {Promise<{session_id: string, session_state: string}>}
     */
    resume: (sessionId) => post(`/api/paper/sessions/${encodeURIComponent(sessionId)}/resume`),

    /**
     * `RUNNING` or `PAUSED` -> `STOPPED`.
     *
     * `POST /api/paper/sessions/{session_id}/stop` (`stop_paper_session`, 60/60s, no body).
     * The session is `STOPPED` either way — the transition commits before the resource
     * release runs — so `complete: false` with a populated `outstanding` is a 200 reporting
     * a state, not a failure. Read `complete` rather than inferring success from the call
     * returning.
     *
     * @param {string} sessionId
     * @returns {Promise<{session_id: string, session_state: string, complete: boolean, outstanding: string[], finals_committed: boolean, finals_reason: (string|null), stale: (boolean|null), loop_settled: boolean, registrations_closed: (number|null)}>}
     */
    stop: (sessionId) => post(`/api/paper/sessions/${encodeURIComponent(sessionId)}/stop`),

    /**
     * `STOPPED -> CREATED`.
     *
     * `POST /api/paper/sessions/{session_id}/reset` (`reset_paper_session`, 60/60s, no body).
     * Balances return to the recorded `initial_capital_minor`, open orders are `CANCELLED`,
     * open positions close to size zero and a new equity series begins at `previous + 1`.
     * Nothing is deleted: the pre-reset orders, fills, trades, metrics and equity snapshots
     * stay readable. Money in the body is the integer Minor_Units figure only.
     *
     * @param {string} sessionId
     * @returns {Promise<{session_id: string, session_state: string, initial_capital_minor: number, cancelled_orders: string[], orders_not_cancellable: string[], closed_positions: string[], previous_series_index: number, series_index: number}>}
     */
    reset: (sessionId) => post(`/api/paper/sessions/${encodeURIComponent(sessionId)}/reset`),

    /**
     * One session's paper orders, newest first.
     *
     * `GET /api/paper/sessions/{session_id}/orders` (`get_paper_session_orders`, 120/60s).
     * One select with `user_id` and `session_id` both predicates on it. `signal_id` is
     * carried, so an order can be lined up against the signal that produced it.
     *
     * @param {string} sessionId
     * @returns {Promise<{session_id: string, orders: Object[], count: number}>}
     */
    orders: (sessionId) => get(`/api/paper/sessions/${encodeURIComponent(sessionId)}/orders`),

    /**
     * One session's fills, most recently filled first.
     *
     * `GET /api/paper/sessions/{session_id}/fills` (`get_paper_session_fills`, 120/60s).
     * One row per fill — a different set from {@link trades}' closed round-trips, since a
     * partial close moves realized PnL without writing a `paper_trades` row. Both are
     * served rather than one being derived from the other.
     *
     * @param {string} sessionId
     * @returns {Promise<{session_id: string, fills: Object[], count: number}>}
     */
    fills: (sessionId) => get(`/api/paper/sessions/${encodeURIComponent(sessionId)}/fills`),

    /**
     * One session's OPEN positions.
     *
     * `GET /api/paper/sessions/{session_id}/positions` (`get_paper_session_positions`,
     * 120/60s). Open only, as a `closed_at IS NULL` predicate on the statement. A closed
     * position persists at `size = 0` rather than being deleted and is deliberately not
     * served here — its economic content is the `paper_trades` row it produced.
     *
     * @param {string} sessionId
     * @returns {Promise<{session_id: string, positions: Object[], count: number}>}
     */
    positions: (sessionId) =>
      get(`/api/paper/sessions/${encodeURIComponent(sessionId)}/positions`),

    /**
     * One session's closed round-trips, most recently closed first.
     *
     * `GET /api/paper/sessions/{session_id}/trades` (`get_paper_session_trades`, 120/60s).
     * One row per position that reached size zero — the set the win rate is computed over.
     * The individual fills are {@link fills}.
     *
     * @param {string} sessionId
     * @returns {Promise<{session_id: string, trades: Object[], count: number}>}
     */
    trades: (sessionId) => get(`/api/paper/sessions/${encodeURIComponent(sessionId)}/trades`),

    /**
     * One session's persisted equity series, in non-decreasing order.
     *
     * `GET /api/paper/sessions/{session_id}/equity` (`get_paper_session_equity`, 120/60s).
     * Ordered `series_index, taken_at ASC` by the repository and **not** re-sorted: the
     * drawdown of a resorted series is not the drawdown of the series that was read.
     * `series_index` leads because a reset begins a new series while keeping the old
     * snapshots readable, so a client can tell the two apart. This is the series the equity
     * curve is drawn from — persisted snapshots, not live event state.
     *
     * @param {string} sessionId
     * @returns {Promise<{session_id: string, equity: Object[], count: number}>}
     */
    equity: (sessionId) => get(`/api/paper/sessions/${encodeURIComponent(sessionId)}/equity`),

    /**
     * One session's most recently computed metrics, or the honest report that there are none.
     *
     * `GET /api/paper/sessions/{session_id}/metrics` (`get_paper_session_metrics`, 120/60s).
     * `metrics: null` with `computed: false` means the read completed and no metrics row
     * exists yet — a different statement from "the metrics are zero", and not flattened into
     * one. Field by field the same holds: `win_rate` is `null` while the closed-trade count
     * is zero, so a `null` here is always "not computed" and never "zero". Branch on
     * `computed`, which the server states rather than leaving to be inferred.
     *
     * @param {string} sessionId
     * @returns {Promise<{session_id: string, metrics: (Object|null), computed: boolean}>}
     */
    metrics: (sessionId) => get(`/api/paper/sessions/${encodeURIComponent(sessionId)}/metrics`),

    /**
     * The REST equivalent of the Paper_Channel replay.
     *
     * `GET /api/paper/sessions/{session_id}/events?since_sequence=` (`get_paper_session_events`,
     * 120/60s). `since_sequence` is this endpoint's spelling of the socket's `last_sequence`:
     * a position in a stream, never an identity, and it cannot widen what the caller may see
     * because the read underneath carries `user_id` as a predicate. `0` — the server default,
     * and the default here — replays the whole retained log.
     *
     * The frames are the same frames the socket serves, from the same `paper_channel.replay`
     * function, so both transports are applied through one client code path and deduplicated
     * on `event_id`. A negative `since_sequence` names no position and is answered as the
     * unrecoverable-gap case: a 200 with `history_incomplete: true` and a `HISTORY_INCOMPLETE`
     * frame in `error`, exactly as the socket answers it — not a 422.
     *
     * @param {string} sessionId
     * @param {number} [since=0] - Replay every retained event above this per-session sequence number.
     * @returns {Promise<{session_id: string, events: Object[], history_incomplete: boolean, current_sequence: number, error: (Object|null)}>}
     */
    events: (sessionId, since = 0) =>
      get(
        `/api/paper/sessions/${encodeURIComponent(sessionId)}/events?since_sequence=${encodeURIComponent(since)}`,
      ),
  },
};
