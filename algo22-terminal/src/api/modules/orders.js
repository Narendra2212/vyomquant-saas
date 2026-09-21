/**
 * Orders API Module
 * 
 * Endpoints: /api/orders/*
 */
import { get, post } from '../../apiClient';

/*
 * The `OrderRequest` typedef went with `createOrder` below. It described an
 * order-placement body, and nothing in this module places an order any more — leaving it
 * would document a request shape no method here can send.
 */

/**
 * @typedef {Object} OrderResponse
 * @property {string} orderId - Order ID
 * @property {string} symbol - Trading symbol
 * @property {string} side - Order side
 * @property {string} type - Order type
 * @property {string} status - Order status
 * @property {number} quantity - Order quantity
 * @property {number} [price] - Order price
 * @property {number} [filled] - Filled quantity
 * @property {number} [remaining] - Remaining quantity
 * @property {string} timestamp - Order timestamp
 */

/*
 * ⚠️ SIX DEAD METHODS WERE DELETED FROM THIS MODULE ⚠️
 * ----------------------------------------------------
 * `createOrder`, `getOrders`, `getOrder`, `updateOrder`, `getOrderTrades` and
 * `getClosedOrders` all addressed routes `routers/orders.py` does not declare, so every call
 * 404'd. None had a caller anywhere in `src/` — checked before deleting, one at a time — so
 * removing them changes no behaviour. What each was, and why it is gone rather than
 * repointed:
 *
 *   * `createOrder` — `POST /api/orders`. The router declares nothing at its mount root. The
 *     nearest POSTs are `/execute` and `/create`, and **both answer 403
 *     MANUAL_EXECUTION_BLOCKED by design**: this backend routes all execution through a
 *     strategy deployment. So there is no correct address for it and it must not be given
 *     one. `pages/LiveTrading.jsx`'s docblock already records that it adds no manual-order
 *     affordance of any kind for this reason.
 *   * `getOrders` — `GET /api/orders?{filters}`. Same absent root. The supported reads are
 *     `GET /open` (`getOpenOrders`) and `GET /history` (`getHistory`), both below.
 *   * `getOrder` (GET) and `updateOrder` (PUT) — `/api/orders/{order_id}`, which the router
 *     declares under no verb. That absence is what made the old
 *     `DELETE /api/orders/{orderId}` cancel a 404 too (corrected at task 20.3, below). A
 *     single order is read out of `/open` or `/history`; amendment is not offered at all.
 *   * `getOrderTrades` — `GET /api/orders/{order_id}/trades`. Per-order fills are not
 *     exposed.
 *   * `getClosedOrders` — `GET /api/orders/closed`. The router declares `/open` and
 *     `/history` and no `/closed`. **`GET /api/orders/history` is the closed-order read**, so
 *     if something needs closed orders it already has a correct method: `getHistory`.
 */
export const ordersApi = {
  /**
   * Cancel ONE resting order at a venue.
   *
   * ⚠️ PATH, METHOD AND PARAMETER CORRECTION (task 20.3) ⚠️
   * ------------------------------------------------------
   * This function used to be `DELETE /api/orders/{orderId}`. `routers/orders.py` declares no
   * `DELETE` at all and no `/{order_id}` write route — the cancel is
   * `@router.post("/cancel/{order_id}")` — so the old spelling resolved to nothing on every
   * call. That is the same class of defect `api/modules/strategies.js` records three times
   * over, and the fourth was `GET /api/orders/open`'s missing `exchange_id` (task 20.1c).
   *
   * `cancel_order` needs THREE things and refuses without any one of them:
   *
   *   * `order_id` in the **path**.
   *   * `exchange_id` as a **required query parameter** — `Query(...)` with no default. The
   *     handler loads that venue's decrypted keys and builds the execution engine against it
   *     before it asks the venue for anything, so an omitted venue is a 422 and not a default.
   *   * a `CancelOrderRequest` **body** carrying `{order_id, symbol}`. Both fields are
   *     non-optional on the model, and the handler reads `body.symbol` twice — once for the
   *     `ExecutionGuard` signal and once for the engine call — so a cancel cannot be issued
   *     without naming the market the order rests in. The id is therefore sent in both the
   *     path and the body, which is what the route asks for.
   *
   * This is a corrected address for the SAME action. Nothing about what the endpoint does is
   * changed here: its `SafetyMonitor` freeze check, its `ExecutionGuard` validation, its Redis
   * cancel lock and its idempotency key are the server's and are untouched.
   *
   * @param {string} orderId - The venue's order id, as `getOpenOrders` reported it.
   * @param {string} symbol - The market the order rests in, e.g. `BTC/USDT`. Required.
   * @param {string} exchangeId - The venue, e.g. `binance`. Required by the route.
   * @returns {Promise<Object>} The execution engine's own cancel result.
   */
  cancelOrder: async (orderId, symbol, exchangeId) => {
    const params = new URLSearchParams();
    if (exchangeId) params.set('exchange_id', exchangeId);
    return post(`/api/orders/cancel/${encodeURIComponent(orderId)}?${params}`, {
      order_id: orderId,
      symbol,
    });
  },

  /**
   * Cancel EVERY order this account has resting at one venue, optionally in one market.
   *
   * ⚠️ PATH, METHOD AND PARAMETER CORRECTION (task 20.3) ⚠️
   * ------------------------------------------------------
   * This function used to be `DELETE /api/orders?{options}`. The route is
   * `@router.post("/cancel-all")`, so the old spelling resolved to nothing — and the options
   * it serialised into the query string were never the parameters the route reads.
   *
   * What it actually declares:
   *
   *   * `exchange_id` as a **required query parameter**, for the same reason as
   *     {@link ordersApi.cancelOrder}: the venue is what the engine is built against.
   *   * a `CancelAllRequest` **body** whose one field, `symbol`, is genuinely OPTIONAL. The
   *     handler reads `body.symbol` and passes `None` when it is absent, which is the
   *     every-market case. So an omitted market is omitted from the body rather than sent
   *     blank — `{"symbol": ""}` would be a filter on the empty symbol, not the absence of
   *     one, exactly as `?symbol=` was on the open-orders read.
   *
   * Scope, because it is easy to under-read: with no `symbol` this cancels the account's open
   * orders at that venue across every market. Open orders are held per venue and carry no
   * strategy, so this is not scoped to one strategy or one deployment and cannot be.
   *
   * @param {string} exchangeId - The venue, e.g. `binance`. Required by the route.
   * @param {string} [symbol] - Restrict to one market. Omitted means every market at the venue.
   * @returns {Promise<{status: string, cancelled: Object}>}
   */
  cancelAllOrders: async (exchangeId, symbol) => {
    const params = new URLSearchParams();
    if (exchangeId) params.set('exchange_id', exchangeId);
    return post(`/api/orders/cancel-all?${params}`, symbol ? { symbol } : {});
  },

  /**
   * Get order history
   * @param {Object} [filters] - Optional filters
   * @param {string} [filters.symbol] - Filter by symbol
   * @param {string} [filters.startDate] - Start date
   * @param {string} [filters.endDate] - End date
   * @param {number} [filters.limit] - Limit results
   * @returns {Promise<OrderResponse[]>}
   */
  getOrderHistory: async (filters = {}) => {
    const params = new URLSearchParams(filters);
    return get(`/api/orders/history?${params}`);
  },

  getHistory: async (filters = {}) => {
    const params = new URLSearchParams(filters);
    return get(`/api/orders/history?${params}`);
  },

  /**
   * Get open orders. A READ: nothing here places, amends or cancels an order.
   *
   * `GET /api/orders/open` declares `exchange_id` as `Query(...)` with NO default — the route
   * loads that venue's decrypted keys before it asks the venue for anything — so the call
   * answers 422 without one. This function sent only `symbol`, which is why
   * `design/pageFields.js`'s `latestOrder` entry records the endpoint as unreachable as
   * written; the venue is now a parameter it can send.
   *
   * `symbol` stays FIRST and stays optional, so every existing positional call keeps its
   * meaning, and an omitted parameter is omitted from the query string rather than sent
   * blank: `?symbol=` is a filter on the empty symbol, not the absence of a filter.
   *
   * @param {string} [symbol] - Filter by symbol
   * @param {string} [exchangeId] - Venue to query, e.g. `binance`. Required by the route.
   * @returns {Promise<OrderResponse[]>} The venue's own ccxt order array — no envelope.
   */
  getOpenOrders: async (symbol, exchangeId) => {
    const params = new URLSearchParams();
    if (symbol) params.set('symbol', symbol);
    if (exchangeId) params.set('exchange_id', exchangeId);
    return get(`/api/orders/open?${params}`);
  },

};
