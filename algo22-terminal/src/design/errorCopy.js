/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ERROR TRANSLATION — the only place an error becomes user-facing words
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `design.md` → "Error Handling" (§12). Requirements 14.3 and 14.4.
 *
 * Requirement 14.4 forbids raw HTTP status codes, exception class names and stack traces as
 * user-facing content. `extractErrorMessage` in `components/ui-legacy/primitives.jsx` falls back
 * to `JSON.stringify(detail)` and then `err.message`, which is exactly how an axios message or a
 * backend traceback reaches the screen today. This module replaces it: a caller hands over the
 * error object it caught and gets back four authored fields plus a correlation id. It never reads
 * `error.message`.
 *
 * WHAT THIS MODULE DOES NOT DO
 * ----------------------------
 * It does not classify errors. `apiClient.js`'s `ApiError` already carries `status`, `data`,
 * `requestId`, `category` (`SERVER_ERROR` / `AUTH_ERROR` / `CLIENT_ERROR` / `NETWORK_ERROR` /
 * `UNKNOWN_ERROR`) and `isRetryable()`, and all of that is read here rather than recomputed
 * (`§1.13`). The one refinement this module adds is `RATE_LIMIT`: `ApiError` files a 429 under
 * `CLIENT_ERROR` but already treats it as retryable in `isRetryable()`, and "too many requests"
 * needs different words from "check what you entered".
 *
 * RESOLUTION ORDER — exactly five steps, first match wins
 * ------------------------------------------------------
 *   1. Backend error code           — the server named the condition; use its copy
 *   2. `detail.reasons[]`           — a safety guard blocked and named the checks
 *   3. WebSocket refusal code       — a `subscription_refused` frame
 *   4. HTTP category                — the server did not name a condition we know
 *   5. Context default              — not an `ApiError` at all (a thrown `Error`, a string)
 *
 * Step 5 is what a raw `throw new Error(...)` lands on, and it is why a stack never reaches the
 * screen: there is no branch that reads a message off the error.
 *
 * @module design/errorCopy
 */

/**
 * The two shapes a backend error body arrives in, both read, neither invented.
 *
 * `{"error": {"code", "message", "details"}, "request_id"}` is what the single exception handler
 * over `backend_app/backend/marketplace/errors.py` serialises. FastAPI's own `HTTPException` path
 * puts a plain `{"error": "CODE", "message": ...}` object under `detail` instead, which is what
 * every router under `backend_app/routers/` raises. `apiClient` parks the parsed body on
 * `ApiError.data` either way.
 *
 * @param {*} error
 * @returns {{code: (string|null), details: Object, reasons: Array<string>}}
 */
function readEnvelope(error) {
  const body = error && typeof error === 'object' ? error.data : null;
  if (!body || typeof body !== 'object') return { code: null, details: {}, reasons: [] };

  const nested =
    (body.error && typeof body.error === 'object' ? body.error : null) ||
    (body.detail && typeof body.detail === 'object' ? body.detail : null) ||
    body;

  // `code` is the marketplace/paper spelling; `error` is the routers' spelling. A string `error`
  // at the top of the body (`publicGet`'s shape) counts too.
  const candidates = [nested.code, nested.error, body.code, body.error];
  const code = candidates.find((value) => typeof value === 'string' && value.length > 0) ?? null;

  const details =
    nested.details && typeof nested.details === 'object' && nested.details !== null
      ? nested.details
      : {};

  const rawReasons = Array.isArray(nested.reasons)
    ? nested.reasons
    : Array.isArray(body.reasons)
      ? body.reasons
      : [];
  const reasons = rawReasons.filter((entry) => typeof entry === 'string' && entry.length > 0);

  return { code, details, reasons };
}

/**
 * Look a server-supplied string up in one of this module's tables, own properties only.
 *
 * Every table below is a plain object literal, so `CODE_COPY['constructor']`,
 * `PAPER_START_REFUSAL_COPY['__proto__']` and `CONTEXT_COPY['toString']` all resolve through
 * `Object.prototype` and hand back something truthy that is not copy at all. The resolution
 * steps read those hits as matches, and the caller gets `headline: undefined` and a `detail`
 * that is a function or an object — a blank error panel, since `ErrorState` and every catch
 * site destructure `{headline, detail, …}` unconditionally.
 *
 * `Object.freeze` does not help: it stops the table being written to, not the prototype chain
 * being read through. This is the same guard `design/semantic.js` puts on `VOCABULARY`,
 * `ENVIRONMENT` and `BLOCK_CATEGORY_STAGE`, for the same reason — the key comes from the wire.
 *
 * Returns `null` rather than `undefined` so every call site can keep using `??`/`!entry`.
 *
 * @param {Object} table
 * @param {unknown} key
 * @returns {*} the entry, or `null` when the table does not own that key
 */
function tableEntry(table, key) {
  return typeof key === 'string' && Object.prototype.hasOwnProperty.call(table, key)
    ? table[key]
    : null;
}

/* ══════════════════════════════════════════════════════════════════════════
 * 1. BACKEND ERROR CODES → HUMAN COPY
 *
 * Codes are the backend's own; the copy is ours. Every entry is authored to name what the
 * trader can do about it, and no entry carries a digit, a table name, a file name or an
 * internal identifier — `PUBLIC_MESSAGE_FOR_CODE` in `marketplace/errors.py` holds the backend
 * to the same rule, and this table is the client half of it.
 *
 * Scope: the codes the in-scope pages' endpoints actually raise. Ops-only and admin-only codes
 * (`RESEARCH_*`, `RECOVERY_LOG_FAILED`, `STATE_VERIFICATION_FAILED`, the moderation half of
 * `MARKETPLACE_*`) are deliberately absent — no in-scope page calls those routes, and an entry
 * for a code that cannot arrive is copy nobody maintains. They fall to the category step, which
 * is honest rather than wrong.
 * ══════════════════════════════════════════════════════════════════════════ */

export const CODE_COPY = Object.freeze({
  // ---- Dashboard (`/api/dashboard/*`) ------------------------------------
  DASHBOARD_FETCH_FAILED: { headline: 'Could not load your dashboard', detail: 'The trading engine did not answer in time.', retryable: true },
  STRATEGIES_FETCH_FAILED: { headline: 'Could not load your strategies', detail: 'The strategy list is temporarily unreadable.', retryable: true },

  // ---- Portfolio (`/api/portfolio/*`) -----------------------------------
  PORTFOLIO_FETCH_FAILED: { headline: 'Could not load your portfolio', detail: 'Live balance and position data is temporarily unreadable.', retryable: true },
  PORTFOLIO_CACHE_STALE: { headline: 'Portfolio figures are out of date', detail: 'The last reading is too old to act on, so it is not shown. Refresh to read again.', retryable: true },
  EQUITY_CURVE_FETCH_FAILED: { headline: 'Could not load the equity curve', detail: 'Historical equity data is temporarily unreadable.', retryable: true },
  TRANSACTIONS_FETCH_FAILED: { headline: 'Could not load your transactions', detail: 'The transaction history is temporarily unreadable.', retryable: true },

  // ---- Live trading and order safety (`/api/orders/*`) ------------------
  EXECUTION_GUARD_BLOCKED: { headline: 'Blocked by a safety check', detail: 'Nothing was sent to the exchange. A risk or safety check refused it.', retryable: false },
  EXECUTION_GUARD_ERROR: { headline: 'Safety checks could not run', detail: 'Nothing was sent to the exchange, because we will not trade without checking first.', retryable: true },
  DIRECT_EXECUTION_BLOCKED: { headline: 'This order cannot be placed here', detail: 'Orders are placed by a deployed strategy, not by hand, on this account.', retryable: false },
  MANUAL_EXECUTION_BLOCKED: { headline: 'Manual trading is blocked', detail: 'This account is set up for strategy execution only.', retryable: false },
  MANUAL_EXECUTION_DISABLED: { headline: 'Manual trading is turned off', detail: 'Turn it on in your risk settings before placing an order by hand.', retryable: false },
  MANUAL_STOP_LOSS_BLOCKED: { headline: 'Stop loss cannot be set by hand', detail: 'The deployed strategy owns this position, including its stop.', retryable: false },
  MANUAL_TAKE_PROFIT_BLOCKED: { headline: 'Take profit cannot be set by hand', detail: 'The deployed strategy owns this position, including its target.', retryable: false },
  SYSTEM_FREEZE: { headline: 'Trading is frozen', detail: 'A platform-wide freeze is in effect, so no order can be placed or cancelled right now.', retryable: false },
  BLOCKED_STRATEGY_ID: { headline: 'This strategy cannot trade', detail: 'It is blocked from placing orders. Check its status on the strategy page.', retryable: false },
  CANCEL_IN_PROGRESS: { headline: 'This cancel is already running', detail: 'Wait for it to finish rather than sending it again.', retryable: false },
  STRATEGY_ID_REQUIRED: { headline: 'Choose a strategy first', detail: 'An order needs the strategy it belongs to.', retryable: false },
  INVALID_SYMBOL: { headline: 'That market is not valid', detail: 'Pick a market from the list.', retryable: false },
  INVALID_SYMBOL_FORMAT: { headline: 'That market is not valid', detail: 'Pick a market from the list rather than typing one.', retryable: false },
  INVALID_SIDE: { headline: 'Choose buy or sell', detail: 'The order side is missing.', retryable: false },
  INVALID_SIDE_VALUE: { headline: 'Choose buy or sell', detail: 'Only those two sides can be placed.', retryable: false },
  INVALID_QUANTITY: { headline: 'Enter a valid quantity', detail: 'The quantity must be a positive number.', retryable: false },
  INVALID_QUANTITY_TYPE: { headline: 'Enter a valid quantity', detail: 'The quantity must be a number.', retryable: false },
  QUANTITY_TOO_SMALL: { headline: 'Quantity is below the minimum', detail: 'Raise it to at least the market minimum shown on the order form.', retryable: false },
  QUANTITY_TOO_LARGE: { headline: 'Quantity is above the maximum', detail: 'Lower it to within the limit shown on the order form.', retryable: false },
  INVALID_PRICE: { headline: 'Enter a valid price', detail: 'The price must be a positive number.', retryable: false },
  INVALID_PRICE_TYPE: { headline: 'Enter a valid price', detail: 'The price must be a number.', retryable: false },
  PRICE_REQUIRED: { headline: 'This order type needs a price', detail: 'Enter a limit price, or switch to a market order.', retryable: false },
  PRICE_TOO_LARGE: { headline: 'Price is above the maximum', detail: 'Lower it to within the limit shown on the order form.', retryable: false },

  // ---- Strategies and strategy detail (`/api/strategies/*`) -------------
  STRATEGIES_LIST_FAILED: { headline: 'Could not load your strategies', detail: 'The strategy list is temporarily unreadable.', retryable: true },
  STRATEGY_GET_FAILED: { headline: 'Could not load this strategy', detail: 'The strategy record is temporarily unreadable.', retryable: true },
  STRATEGY_NOT_FOUND: { headline: 'Strategy not found', detail: 'It does not exist, or it is not on your account.', retryable: false },
  STRATEGY_CREATE_FAILED: { headline: 'Could not create the strategy', detail: 'Nothing was saved. Try again.', retryable: true },
  STRATEGY_CREATE_ERROR: { headline: 'Could not create the strategy', detail: 'Nothing was saved. Try again.', retryable: true },
  STRATEGY_UPDATE_FAILED: { headline: 'Could not save your changes', detail: 'The strategy is unchanged, so nothing was lost. Try again.', retryable: true },
  STRATEGY_NAME_INVALID: { headline: 'That name cannot be used', detail: 'Choose a name with visible text, within the permitted length.', retryable: false },
  STRATEGY_DELETE_FAILED: { headline: 'Could not delete the strategy', detail: 'It is unchanged. Try again.', retryable: true },
  STRATEGY_CLONE_FAILED: { headline: 'Could not clone the strategy', detail: 'No copy was created. Try again.', retryable: true },
  STRATEGY_DEPLOY_FAILED: { headline: 'Deployment did not start', detail: 'The strategy is not live, and nothing is trading. Try again.', retryable: true },
  STRATEGY_PAUSE_FAILED: { headline: 'Could not pause the strategy', detail: 'It is still running. Try again, and check its status before assuming it stopped.', retryable: true },
  STRATEGY_RESUME_FAILED: { headline: 'Could not resume the strategy', detail: 'It is still paused. Try again.', retryable: true },
  STRATEGY_ARCHIVED: { headline: 'This strategy is archived', detail: 'Restore it before making changes.', retryable: false },
  STRATEGY_VALIDATION_FAILED: { headline: 'This strategy is not valid yet', detail: 'The listed issues have to be resolved before it can run.', retryable: false },
  STRATEGY_COMPILATION_FAILED: { headline: 'This strategy could not be compiled', detail: 'The graph is complete but does not translate into a runnable plan.', retryable: false },
  COMPILE_FAILED: { headline: 'Compilation did not finish', detail: 'The strategy is unchanged. Try again.', retryable: true },
  STRATEGY_GRAPH_INVALID: { headline: 'This strategy graph is not valid', detail: 'The listed issues have to be resolved before it can be saved or run.', retryable: false },
  STRATEGY_GRAPH_UNREADABLE: { headline: 'Could not read the strategy graph', detail: 'The saved graph could not be loaded, so the canvas is not shown.', retryable: true },
  DIRECT_STATUS_UPDATE_FORBIDDEN: { headline: 'Status cannot be set directly', detail: 'Use deploy, pause, resume or stop so the change is recorded.', retryable: false },

  // ---- Deployments and versions ----------------------------------------
  DEPLOYMENTS_LIST_FAILED: { headline: 'Could not load deployments', detail: 'The deployment list is temporarily unreadable.', retryable: true },
  DEPLOYMENT_NOT_FOUND: { headline: 'Deployment not found', detail: 'It does not exist, or it is not on your account.', retryable: false },
  DEPLOYMENT_LOOKUP_UNAVAILABLE: { headline: 'Could not check the deployment', detail: 'Its current state is unreadable, so it is not reported rather than guessed.', retryable: true },
  DEPLOY_DISPATCH_FAILED: { headline: 'Deployment did not start', detail: 'Nothing is trading. Try again.', retryable: true },
  DEPLOY_PREREQUISITE_NOT_MET: { headline: 'Not ready to deploy', detail: 'The listed prerequisites have to be met first.', retryable: false },
  DEPLOYMENT_GATE_FAILED: { headline: 'Deployment was refused at the gate', detail: 'A pre-deployment check did not pass, so nothing was started.', retryable: false },
  STOP_DISPATCH_FAILED: { headline: 'Could not stop the strategy', detail: 'It may still be running. Check its status before assuming it stopped.', retryable: true },
  VERSION_NOT_FOUND: { headline: 'Version not found', detail: 'It is not a saved version of this strategy.', retryable: false },
  VERSION_DEPLOY_FAILED: { headline: 'This version did not deploy', detail: 'Nothing is trading. Try again.', retryable: true },
  VERSION_DEPLOY_PREFLIGHT_FAILED: { headline: 'Pre-deployment checks did not run', detail: 'Nothing was deployed, because we will not deploy without checking first.', retryable: true },
  PREFLIGHT_REQUEST_INVALID: { headline: 'Check the deployment settings', detail: 'The market or environment named in the request could not be used.', retryable: false },
  MISSING_STRATEGY_ID: { headline: 'Choose a strategy first', detail: 'This action needs the strategy it applies to.', retryable: false },
  MISSING_DEPLOYMENT_ID: { headline: 'Choose a deployment first', detail: 'This action needs the deployment it applies to.', retryable: false },

  // ---- Backtester -------------------------------------------------------
  BACKTESTS_LIST_FAILED: { headline: 'Could not load backtests', detail: 'The backtest history is temporarily unreadable.', retryable: true },
  BACKTEST_NOT_FOUND: { headline: 'Backtest not found', detail: 'It does not exist, or it is not on your account.', retryable: false },
  BACKTEST_CREATE_FAILED: { headline: 'Could not start the backtest', detail: 'Nothing was queued. Try again.', retryable: true },
  BACKTEST_EXECUTE_FAILED: { headline: 'The backtest did not finish', detail: 'No results were produced, so none are shown. Try again.', retryable: true },
  BACKTEST_VERSION_UNAVAILABLE: { headline: 'No version to backtest', detail: 'Save a version of this strategy first, then run the backtest against it.', retryable: false },
  BACKTEST_NOT_RUNNABLE: { headline: 'This backtest cannot run', detail: 'The version declares no market, or the window holds too little data to simulate.', retryable: false },
  BACKTEST_FEED_UNCONFIGURED: { headline: 'No market data source is configured', detail: 'A backtest needs a measured data source, and this environment has none.', retryable: false },
  DATA_VALIDATION_FAILED: { headline: 'The market data did not pass validation', detail: 'The listed data problems would make the results misleading, so nothing was run.', retryable: false },

  // ---- Strategy Builder: registry, preview, data quality, training ------
  REGISTRY_UNAVAILABLE: { headline: 'Block palette is unavailable', detail: 'The block registry could not be loaded, so no blocks are shown.', retryable: true },
  TIMEFRAME_VOCABULARY_UNAVAILABLE: { headline: 'Timeframes are unavailable', detail: 'The timeframe list could not be loaded, so none are offered rather than guessed.', retryable: true },
  TIMEFRAME_VOCABULARY_EMPTY: { headline: 'No timeframes are available', detail: 'This environment offers none, so a timeframe cannot be chosen here.', retryable: false },
  PREVIEW_GRAPH_UNAVAILABLE: { headline: 'Nothing to preview yet', detail: 'Save the strategy graph first, then preview a block.', retryable: false },
  PREVIEW_NODE_NOT_FOUND: { headline: 'That block is not in the saved graph', detail: 'Save your changes, then preview it.', retryable: false },
  PREVIEW_BLOCK_UNPUBLISHED: { headline: 'This block cannot be previewed', detail: 'It is not a published block, so no preview is produced for it.', retryable: false },
  PREVIEW_MARKET_UNRESOLVED: { headline: 'Choose a market to preview against', detail: 'The graph names none, so there is nothing to compute over.', retryable: false },
  PREVIEW_MULTIPLE_MARKETS: { headline: 'Too many markets to preview', detail: 'This graph reads more than one market. Preview needs a single one.', retryable: false },
  PREVIEW_DATA_UNAVAILABLE: { headline: 'No data for this preview', detail: 'The chosen market and timeframe have no measured data in this window.', retryable: false },
  PREVIEW_FEED_UNCONFIGURED: { headline: 'No market data source is configured', detail: 'A preview needs a measured data source, and this environment has none.', retryable: false },
  PREVIEW_WARMUP_EXCEEDS_WINDOW: { headline: 'The preview window is too short', detail: 'This block needs more history to warm up than the window holds. Widen it.', retryable: false },
  PREVIEW_EXECUTION_FAILED: { headline: 'The preview did not finish', detail: 'No values were produced, so none are shown.', retryable: true },
  DATA_QUALITY_GRAPH_UNAVAILABLE: { headline: 'Data availability cannot be checked yet', detail: 'Save the strategy graph first.', retryable: false },
  DATA_QUALITY_MARKET_UNRESOLVED: { headline: 'Choose a market first', detail: 'Data availability is reported per market, and the graph names none.', retryable: false },
  DATA_QUALITY_MULTIPLE_MARKETS: { headline: 'Too many markets to check', detail: 'This graph reads more than one market. Availability is reported for a single one.', retryable: false },
  ML_MODEL_MISSING: { headline: 'This model is not available', detail: 'Train or select a model before running this strategy.', retryable: false },
  ML_NODE_MISSING_MODEL_ID: { headline: 'A model block has no model selected', detail: 'Open the block and choose a trained model.', retryable: false },
  TRAINING_BLOCKED: { headline: 'Training cannot start', detail: 'The listed conditions have to be met before this model can be trained.', retryable: false },
  TRAINING_DATA_SOURCE_MISSING: { headline: 'Choose a data source for training', detail: 'Training needs a market and timeframe to learn from.', retryable: false },
  TRAINING_DATA_SOURCE_INVALID: { headline: 'That training data source cannot be used', detail: 'Pick a market and timeframe that this environment measures.', retryable: false },
  TRAINING_JOB_NOT_FOUND: { headline: 'Training job not found', detail: 'It does not exist, or it is not on your account.', retryable: false },
  TRAINING_JOB_NOT_CANCELLABLE: { headline: 'This job cannot be cancelled', detail: 'It has already finished or stopped.', retryable: false },
  TRAINING_TARGET_NOT_FOUND: { headline: 'The training target is missing', detail: 'The block this job was to train no longer exists in the graph.', retryable: false },

  // ---- Signal Trace (`/api/signal-trace/*`) ----------------------------
  SIGNALS_LIST_FAILED: { headline: 'Could not load signals', detail: 'The signal list is temporarily unreadable.', retryable: true },
  SIGNAL_GET_FAILED: { headline: 'Could not load this signal', detail: 'The signal record is temporarily unreadable.', retryable: true },
  SIGNAL_NOT_FOUND: { headline: 'Signal not found', detail: 'This trace does not exist, or it is not on your account.', retryable: false },
  TIMELINE_GET_FAILED: { headline: 'Could not load the trace timeline', detail: 'The signal record was read, but its timeline was not.', retryable: true },
  SIGNALS_EXPORT_FAILED: { headline: 'Could not export the signals', detail: 'No file was produced. Try again.', retryable: true },

  // ---- Paper Trading ----------------------------------------------------
  PAPER_PERSISTENCE_UNAVAILABLE: { headline: 'Paper trading is not available', detail: 'The paper-trading store is not ready on this environment.', retryable: false },
  PAPER_READ_FAILED: { headline: 'Could not read your paper account', detail: 'The paper-trading store did not answer.', retryable: true },
  // `detail` is filled from `details.validation` / `details.reason` — see PAPER_START_REFUSAL_COPY.
  PAPER_START_REFUSED: { headline: 'This session cannot start', detail: null, retryable: false },
  PAPER_MARKET_DATA_UNAVAILABLE: { headline: 'No usable market data', detail: 'A paper session needs a measured price source, and this one is not usable.', retryable: false },
  PAPER_SIMULATOR_MISCONFIGURED: { headline: 'Paper trading is not available', detail: 'The simulator on this environment is not one we will fill orders against.', retryable: false },
  PAPER_SESSION_LIMIT_REACHED: { headline: 'Too many paper sessions running', detail: 'Stop one of your running sessions before starting another.', retryable: false },
  PAPER_SESSION_OPERATION_REJECTED: { headline: 'That action is not available now', detail: 'The session is not in a state that allows it, so nothing was changed.', retryable: false },
  PAPER_ORDER_INVALID: { headline: 'This order was rejected', detail: 'It was recorded as rejected and nothing was filled. Check the values and try again.', retryable: false },
  PAPER_ORDER_FAILED: { headline: 'Could not place the paper order', detail: 'Nothing was recorded. Try again.', retryable: true },
  PAPER_INSUFFICIENT_FUNDS: { headline: 'Not enough simulated balance', detail: 'Nothing was reserved. Lower the quantity, or reset the paper account.', retryable: false },
  PAPER_OVER_FILL: { headline: 'That fill exceeds the order', detail: 'Nothing was filled, because an order cannot fill for more than it asked for.', retryable: false },
  PAPER_IDEMPOTENCY_CONFLICT: { headline: 'This request was already used', detail: 'The same key arrived with different values, so nothing was placed. Start a fresh order.', retryable: false },
  PAPER_CONCURRENCY_CONFLICT: { headline: 'The account was busy', detail: 'Another change landed first, so nothing was applied. Try again.', retryable: true },
  PAPER_INVARIANT_VIOLATION: { headline: 'The paper account did not balance', detail: 'Everything was rolled back rather than left in a state the figures do not support.', retryable: false },
  CANCEL_FAILED: { headline: 'Could not cancel the order', detail: 'It may still be open. Check the order list before assuming it was cancelled.', retryable: false },
  CANCEL_ERROR: { headline: 'Could not cancel the order', detail: 'It may still be open. Check the order list, then try again.', retryable: true },
  FORBIDDEN: { headline: 'This is not yours to change', detail: 'The item belongs to another account.', retryable: false },

  // ---- Marketplace and library (`/api/library/*`) ----------------------
  MARKETPLACE_READ_FAILED: { headline: 'Could not load the marketplace', detail: 'Listings are temporarily unreadable, so none are shown rather than a partial set.', retryable: true },
  MARKETPLACE_STRATEGY_UNAVAILABLE: { headline: 'This strategy cannot run', detail: 'Its published version is no longer available from the creator.', retryable: false },
  MARKETPLACE_NOT_SUBSCRIBED: { headline: 'A subscription is needed', detail: 'No subscription on your account entitles you to run this strategy.', retryable: false },
  MARKETPLACE_SUBSCRIPTION_EXPIRED: { headline: 'This subscription has ended', detail: 'Renew it to run this strategy again.', retryable: false, action: { label: 'View listing', to: null } },
  MARKETPLACE_OPERATION_NOT_PERMITTED: { headline: 'That is not allowed on a subscribed strategy', detail: 'A subscription lets you run the strategy, not change or republish it.', retryable: false },
  MARKETPLACE_LISTING_NOT_PURCHASABLE: { headline: 'This listing cannot be bought', detail: 'It is not on sale, or you already hold an active subscription to it.', retryable: false },
  MARKETPLACE_OWN_LISTING: { headline: 'This listing is yours', detail: 'You already have full access, so there is nothing to buy.', retryable: false },
  MARKETPLACE_CHECKOUT_UNAVAILABLE: { headline: 'Checkout could not be opened', detail: 'You have not been charged. Try again in a moment.', retryable: true },
  MARKETPLACE_PAYMENT_REQUIRED: { headline: 'Payment has not completed', detail: 'The subscription stays inactive until the payment is settled.', retryable: false },
  MARKETPLACE_CLONING_DISABLED: { headline: 'This strategy cannot be copied', detail: 'The creator has not allowed the source to be cloned.', retryable: false },
  MARKETPLACE_RATE_LIMITED: { headline: 'Too many requests', detail: 'Wait a few seconds before trying again.', retryable: true },
  MARKETPLACE_ELIGIBILITY_FAILED: { headline: 'Not ready to publish', detail: 'The listed conditions have to pass before this strategy can be submitted.', retryable: false },
  MARKETPLACE_ELIGIBILITY_UNEVALUABLE: { headline: 'Could not check publishing eligibility', detail: 'Nothing was submitted. Try again shortly.', retryable: true },
  MARKETPLACE_SUBMISSION_ALREADY_OPEN: { headline: 'A submission is already open', detail: 'Complete or withdraw it before starting another.', retryable: false },
  MARKETPLACE_SUBMISSION_NOT_FOUND: { headline: 'Submission not found', detail: 'It does not exist, or it is not on your account.', retryable: false },
  MARKETPLACE_SUBMISSION_TRANSITION_REJECTED: { headline: 'That action is not available now', detail: 'The submission is not in a state that allows it, so nothing was changed.', retryable: false },
  MARKETPLACE_USE_SUBMISSION_ACTIONS: { headline: 'Change this from the submission', detail: 'A listing state is moved by reviewing its submission, so nothing was changed here.', retryable: false },
  MARKETPLACE_REJECTION_REASON_REQUIRED: { headline: 'A reason is required', detail: 'Enter a reason with visible text, within the permitted length.', retryable: false },
  MARKETPLACE_EVIDENCE_IMMUTABLE: { headline: 'This evidence cannot be changed', detail: 'It was recorded when the submission was made and stays as it was.', retryable: false },
  MARKETPLACE_EVIDENCE_PERSIST_FAILED: { headline: 'Could not record the submission evidence', detail: 'Nothing was submitted, because a submission without its evidence is not a record.', retryable: true },
  MARKETPLACE_PRICE_OUT_OF_RANGE: { headline: 'That price is outside the allowed range', detail: 'Choose a price within the range shown for this submission.', retryable: false },
  MARKETPLACE_PRICE_EVIDENCE_MISSING: { headline: 'Pricing information is incomplete', detail: 'The listed pricing inputs are needed before a price can be set.', retryable: false },
  MARKETPLACE_COVER_REFERENCE_REJECTED: { headline: 'That cover image cannot be used', detail: 'Upload the image through this page rather than linking to one elsewhere.', retryable: false },
  MARKETPLACE_ACTION_NOT_RECORDED: { headline: 'The change was rolled back', detail: 'It could not be recorded in the audit trail, so it was undone rather than left unlogged.', retryable: true },
  BILLING_ENTITLEMENTS_FAILED: { headline: 'Could not check your plan', detail: 'Your entitlements are unreadable, so none are assumed. Refresh to read again.', retryable: true },

  // ---- Shared across domains -------------------------------------------
  NOT_FOUND: { headline: 'Not found', detail: 'It does not exist, or it is not on your account.', retryable: false },
  EXECUTION_ENVIRONMENT_MISMATCH: { headline: 'Wrong trading environment', detail: 'A simulated strategy cannot be run on the live path. Nothing was sent.', retryable: false },
  EXECUTION_ENVIRONMENT_UNRESOLVED: { headline: 'Trading environment is not set', detail: 'Nothing was sent, because we will never assume an order is live or simulated.', retryable: false },

  // ---- WebSocket subscription refusals ---------------------------------
  // `core/websocket_auth.py`'s four `CHANNEL_REFUSED_*` codes. `websocketClient` delivers the
  // `subscription_refused` frame to the requesting channel's own handlers, so the panel that
  // asked for the channel is the one that renders this (Requirement 21.6).
  CHANNEL_FORBIDDEN: { headline: 'Live updates are not available here', detail: 'This is not on your account, so there is nothing to stream.', retryable: false },
  CHANNEL_UNKNOWN: { headline: 'Live updates are not available here', detail: 'This channel is not one the server streams.', retryable: false },
  CHANNEL_UNAUTHENTICATED: { headline: 'Sign in to see live updates', detail: 'Live updates need a signed-in session.', retryable: false, action: { label: 'Sign in', to: '/signin' } },
  CHANNEL_OWNER_UNRESOLVED: { headline: 'Live updates are paused', detail: 'Ownership could not be confirmed, so nothing is streamed rather than the wrong data.', retryable: true },
});

/**
 * `PAPER_START_REFUSED`'s `details.validation` in words.
 *
 * `paper_session_service` refuses a start with a stable label rather than a sentence, and the
 * label is the whole point: nine different refusals, nine different fixes. The server's own
 * message is never quoted, because `PaperError` builds some of them from `str(e)`.
 */
export const PAPER_START_REFUSAL_COPY = Object.freeze({
  ENTITLEMENT: 'Your subscription does not entitle you to run this strategy.',
  STRATEGY_NOT_EXECUTABLE: 'No runnable version of this strategy resolves, so there is nothing to run.',
  CAPITAL_NOT_POSITIVE: 'Starting capital has to be more than nothing.',
  CAPITAL_EXCEEDS_MAXIMUM: 'Starting capital is above the maximum for a paper session. Lower it.',
  CAPITAL_PRECISION_EXCEEDED: 'Starting capital has more decimal places than the currency allows.',
  CURRENCY_NOT_SUPPORTED: 'That currency is not offered for paper sessions.',
  TIMEFRAME_NOT_SUPPORTED: 'That timeframe is not offered for paper sessions.',
  STRATEGY_SYMBOL_TIMEFRAME_INCOMPATIBLE: 'This strategy does not run on the chosen market and timeframe.',
  SESSION_CONFIG_NOT_BUILDABLE: 'The session settings do not combine into something runnable.',
});

/**
 * `entitlement_resolver.EntitlementReason` → the `MARKETPLACE_*` code it corresponds to, as it
 * arrives in `PAPER_START_REFUSED`'s `details.reason`. Mirrors
 * `pages/paperTradingFormat.js`'s `ENTITLEMENT_REASON_TO_WIRE_CODE` so both surfaces read the
 * server's vocabulary rather than two that drift.
 */
const ENTITLEMENT_REASON_TO_CODE = Object.freeze({
  NOT_SUBSCRIBED: 'MARKETPLACE_NOT_SUBSCRIBED',
  EXPIRED: 'MARKETPLACE_SUBSCRIPTION_EXPIRED',
  LISTING_UNAVAILABLE: 'MARKETPLACE_STRATEGY_UNAVAILABLE',
  SUBSCRIPTION_SUSPENDED: 'MARKETPLACE_OPERATION_NOT_PERMITTED',
});

/* ══════════════════════════════════════════════════════════════════════════
 * 2. `detail.reasons[]` — SAFETY-GUARD BLOCK REASONS
 *
 * `execution_guard` fills `blocked_reasons` with `f"{check_name}: {result.message}"`, and one
 * branch fills it with `f"Exception during validation: {result}"`. The messages are free-form
 * and can carry a Python exception's own text, so ONLY the check name is read here and mapped
 * through a closed table. The message half is dropped, never rendered.
 * ══════════════════════════════════════════════════════════════════════════ */

/** `execution_guard.py`'s `check_name` values in words. */
export const GUARD_CHECK_COPY = Object.freeze({
  balance_validation: 'your available balance',
  circuit_breakers: 'a circuit breaker that is currently open',
  composite_risk_score: 'the overall risk score for this account',
  daily_drawdown: 'today’s drawdown limit',
  duplicate_order: 'a duplicate of an order already in flight',
  exception: 'a safety check that could not complete',
  exposure_limits: 'your exposure limits',
  market_conditions: 'current market conditions',
  market_conditions_detailed: 'current market conditions',
  order_size: 'the permitted order size',
  portfolio_concentration: 'how concentrated the portfolio would become',
  position_exposure_limits: 'the exposure limit for this position',
  risk_engine: 'the risk engine’s own verdict',
  signal_basic: 'the signal’s required fields',
  signal_latency: 'how long ago the signal was produced',
  strategy_conflict: 'a conflict with another running strategy',
  symbol_allowed: 'whether this market is permitted on your account',
  system_health: 'platform health',
});

/** The check name half of a `blocked_reasons` entry, or `null`. */
function checkNameOf(reason) {
  const separator = reason.indexOf(':');
  const name = (separator === -1 ? reason : reason.slice(0, separator)).trim();
  return name.length > 0 ? name : null;
}

/* ══════════════════════════════════════════════════════════════════════════
 * 4. HTTP CATEGORY → COPY, when no code matched
 *
 * Never mentions the status number. `ApiError.category` decides which entry, so this table is a
 * translation of a classification that already exists rather than a second one.
 * ══════════════════════════════════════════════════════════════════════════ */

export const CATEGORY_COPY = Object.freeze({
  NETWORK_ERROR: { headline: 'No connection to VyomQuant', detail: 'Check your internet connection and try again.', retryable: true },
  SERVER_ERROR: { headline: 'Something went wrong on our side', detail: 'This is not caused by anything you did. Please try again in a moment.', retryable: true },
  AUTH_ERROR: { headline: 'Your session has expired', detail: 'Sign in again to continue.', retryable: false, action: { label: 'Sign in', to: '/signin' } },
  CLIENT_ERROR: { headline: 'That request could not be completed', detail: 'Check the values you entered and try again.', retryable: false },
  RATE_LIMIT: { headline: 'Too many requests', detail: 'You are refreshing faster than we can answer. Wait a few seconds.', retryable: true },
});

/* ══════════════════════════════════════════════════════════════════════════
 * 5. CONTEXT DEFAULT — the last resort
 *
 * Reached when the thrown thing is not an `ApiError` at all: a bare `Error` from a render, a
 * string, `null`. There is deliberately no branch that reads a message off it, which is why a
 * stack trace has no path to the screen.
 * ══════════════════════════════════════════════════════════════════════════ */

export const CONTEXT_COPY = Object.freeze({
  dashboard: { headline: 'Could not load your dashboard', detail: 'Nothing is shown rather than a partial reading. Try again.', retryable: true },
  portfolio: { headline: 'Could not load your portfolio', detail: 'Nothing is shown rather than a partial reading. Try again.', retryable: true },
  trades: { headline: 'Could not load your trade history', detail: 'Nothing is shown rather than a partial reading. Try again.', retryable: true },
  strategies: { headline: 'Could not load your strategies', detail: 'Nothing is shown rather than a partial reading. Try again.', retryable: true },
  'strategy-detail': { headline: 'Could not load this strategy', detail: 'Nothing is shown rather than a partial reading. Try again.', retryable: true },
  'live-trading': { headline: 'Could not load live trading', detail: 'Nothing is shown rather than a partial reading. Try again.', retryable: true },
  'signal-trace': { headline: 'Could not load the signal trace', detail: 'Nothing is shown rather than a partial reading. Try again.', retryable: true },
  backtest: { headline: 'Could not load the backtester', detail: 'Nothing is shown rather than a partial reading. Try again.', retryable: true },
  builder: { headline: 'Could not load the strategy builder', detail: 'Nothing is shown rather than a partial reading. Try again.', retryable: true },
  'paper-trading': { headline: 'Could not load paper trading', detail: 'Nothing is shown rather than a partial reading. Try again.', retryable: true },
  marketplace: { headline: 'Could not load the marketplace', detail: 'Nothing is shown rather than a partial reading. Try again.', retryable: true },
  default: { headline: 'Something went wrong', detail: 'This has been reported. Try again, and use the reference below if you contact support.', retryable: true },
});

/* ══════════════════════════════════════════════════════════════════════════
 * THE SCRUBBER
 *
 * Requirement 14.4 — enforced by a pure function, not by discipline.
 * ══════════════════════════════════════════════════════════════════════════ */

export const FORBIDDEN = Object.freeze([
  /\b[1-5]\d{2}\b/, //                       bare HTTP status
  /\b\w*(Error|Exception)\b/, //             AxiosError, ApiError, KeyError, TypeError…
  /\bat\s+\S+\s*\([^)]*:\d+:\d+\)/, //       V8 stack frame
  /\n\s+at\s/, //                            stack frame, minimal form
  /\b(Traceback|File\s+"[^"]+",\s+line)/, // Python traceback
  /https?:\/\/\S+\/api\//, //                internal endpoint URL
]);

/** True when `text` contains anything Requirement 14.4 forbids on screen. */
export function containsForbidden(text) {
  const subject = typeof text === 'string' ? text : '';
  return FORBIDDEN.some((pattern) => pattern.test(subject));
}

/**
 * Read at call time rather than captured at module load, so a test can stub it and so the
 * production bundle keeps the constant-folded `false` Vite substitutes.
 */
function isDevelopment() {
  return import.meta.env.DEV === true;
}

/* ══════════════════════════════════════════════════════════════════════════
 * RESOLUTION
 * ══════════════════════════════════════════════════════════════════════════ */

/** True when `error` is a `subscription_refused` frame rather than an HTTP failure. */
function isRefusalFrame(error) {
  if (!error || typeof error !== 'object') return false;
  if (error.type === 'subscription_refused') return true;
  return typeof error.channel === 'string' && typeof error.code === 'string';
}

/** Step 1 — the backend named the condition. */
function fromCode(error) {
  if (isRefusalFrame(error)) return null;

  const { code, details } = readEnvelope(error);
  // `error.code` covers the thrown-object shapes that carry a code without an HTTP body:
  // `registryClient`'s `RegistryError`, and the builder's own `saveIssue` records.
  const direct = error && typeof error === 'object' && typeof error.code === 'string' ? error.code : null;
  const resolved = code ?? direct;
  if (!resolved) return null;

  // `resolved` is a server-supplied string: own properties only.
  const entry = tableEntry(CODE_COPY, resolved);
  if (!entry) return null;

  if (resolved === 'PAPER_START_REFUSED') {
    return { ...entry, detail: paperStartDetail(details) };
  }
  return entry;
}

/**
 * `PAPER_START_REFUSED`'s detail, from the labels the server sends. An `ENTITLEMENT` refusal
 * carries a second label naming which subscription condition failed, and that is the more useful
 * sentence, so it wins over the generic entitlement one.
 */
function paperStartDetail(details) {
  const validation = typeof details.validation === 'string' ? details.validation : null;
  const reason = typeof details.reason === 'string' ? details.reason : null;

  if (validation === 'ENTITLEMENT' && reason) {
    // `reason` is server-supplied and guarded; `mapped` is then one of this module's own four
    // `MARKETPLACE_*` constants, so the second lookup needs no guard.
    const mapped = tableEntry(ENTITLEMENT_REASON_TO_CODE, reason);
    const entry = mapped ? CODE_COPY[mapped] : null;
    if (entry?.detail) return entry.detail;
  }
  return tableEntry(PAPER_START_REFUSAL_COPY, validation) || 'A start check refused it, and nothing was created.';
}

/** Step 2 — a safety guard blocked and named its checks. */
function fromReasons(error) {
  const { reasons } = readEnvelope(error);
  if (reasons.length === 0) return null;

  const named = [];
  let unnamed = 0;
  for (const reason of reasons) {
    // The check name is the server's own string, so it is guarded too: an unguarded
    // `constructor:` entry would put a function into `named` and then into the detail.
    const copy = tableEntry(GUARD_CHECK_COPY, checkNameOf(reason));
    if (copy && !named.includes(copy)) named.push(copy);
    else if (!copy) unnamed += 1;
  }

  const listed = named.length > 0 ? named.join('; ') : null;
  const detail = listed
    ? `Nothing was sent to the exchange. The checks that blocked it looked at ${listed}.`
    : 'Nothing was sent to the exchange. The safety checks that blocked it are recorded against the reference below.';

  return {
    headline: 'Blocked by a safety check',
    // The count of unrecognised checks is reported, never their text: those strings can carry a
    // Python exception's own words.
    detail: unnamed > 0 && listed ? `${detail} Others were recorded against the reference below.` : detail,
    retryable: false,
  };
}

/** Step 3 — a WebSocket subscription was refused. */
function fromRefusal(error) {
  if (!isRefusalFrame(error)) return null;
  // The frame's `code` comes off the socket: own properties only.
  return tableEntry(CODE_COPY, error.code);
}

/**
 * The `CATEGORY_COPY` key for this error, or `null` when it carries no category.
 *
 * `ApiError` files a 429 under `CLIENT_ERROR` while `isRetryable()` already treats it as
 * retryable; `RATE_LIMIT` is that same distinction given its own words. `UNKNOWN_ERROR` is the
 * one `ApiError` category `CATEGORY_COPY` has no entry for — it is reached only when a status is
 * present but classified nowhere, which reads as a problem on our side, so it borrows that copy.
 */
export function resolveCategory(error) {
  if (!error || typeof error !== 'object') return null;
  if (error.status === 429) return 'RATE_LIMIT';
  const category = error.category;
  if (typeof category !== 'string') return null;
  if (Object.prototype.hasOwnProperty.call(CATEGORY_COPY, category)) return category;
  if (category === 'UNKNOWN_ERROR') return 'SERVER_ERROR';
  return null;
}

/** Step 4 — the HTTP category. */
function fromCategory(error) {
  const category = resolveCategory(error);
  if (!category) return null;
  // Not guarded, and does not need to be: `resolveCategory` already applies the
  // `hasOwnProperty` check to `error.category` and otherwise returns one of two literals here.
  const entry = CATEGORY_COPY[category];
  // `isRetryable()` is `ApiError`'s own answer and is preferred over the table's when present.
  const retryable = typeof error.isRetryable === 'function' ? error.isRetryable() === true : entry.retryable;
  return { ...entry, retryable };
}

/**
 * Step 5 — the context default.
 *
 * `context` is a caller-supplied string rather than a server one, but it is guarded on the same
 * grounds: `CONTEXT_COPY['__proto__']` is truthy, so the `?? CONTEXT_COPY.default` fallback
 * would be skipped and the last resort — the one branch that must always produce copy — would
 * return `Object.prototype`.
 */
function fromContext(context) {
  return tableEntry(CONTEXT_COPY, context) ?? CONTEXT_COPY.default;
}

/**
 * The correlation id a support ticket is opened with.
 *
 * `apiClient` generates one per request and hangs it on `ApiError.requestId`; the marketplace and
 * paper exception handler also returns its own `request_id` in the body. Either is a random
 * identifier — not a status, not a class name, not a path — which is what makes "contact support"
 * actionable without leaking anything about the server.
 */
function readSupportRef(error) {
  if (!error || typeof error !== 'object') return null;
  if (typeof error.requestId === 'string' && error.requestId.length > 0) return error.requestId;
  const body = error.data;
  if (body && typeof body === 'object' && typeof body.request_id === 'string' && body.request_id.length > 0) {
    return body.request_id;
  }
  return null;
}

/**
 * The scrub step, as its own function so it can be exercised directly.
 *
 * Every table above is authored clean, so on today's code this never fires. It is here as the
 * guard that stops a FUTURE `detail: err.message` — or a new table entry that quotes a status,
 * names an exception class or pastes a stack frame — from shipping. In development it throws, so
 * the leak is found by whoever wrote it; in production it degrades to the category copy rather
 * than rendering the offending text.
 *
 * It is exported because a guard that cannot be shown to fire is a guard nobody trusts: with the
 * tables clean there is no input to `translateError` that reaches this branch, so the branch is
 * tested through this entry point instead of through a deliberately broken table.
 *
 * @param {{headline: string, detail: string|null, retryable: boolean,
 *          action: object|null, supportRef: string|null}} candidate
 * @param {*} error - The original error, read only for its category on the degraded path.
 * @param {string} [context] - Named in the development-only failure.
 * @returns {{headline: string, detail: string|null, retryable: boolean,
 *            action: object|null, supportRef: string|null}}
 */
export function enforceNoLeak(candidate, error, context) {
  const surface = `${candidate.headline ?? ''} ${candidate.detail ?? ''} ${candidate.action?.label ?? ''}`;
  if (!containsForbidden(surface)) return candidate;

  if (isDevelopment()) {
    throw new Error(`translateError produced forbidden content for ${context ?? 'an unnamed context'}`);
  }
  const fallback = CATEGORY_COPY[resolveCategory(error) ?? 'SERVER_ERROR'];
  return {
    headline: fallback.headline,
    detail: fallback.detail,
    retryable: fallback.retryable,
    action: fallback.action ?? null,
    supportRef: candidate.supportRef ?? null,
  };
}

/**
 * Translate any thrown thing into copy a trader can act on.
 *
 * Total over its input: an `ApiError`, a `subscription_refused` frame, a bare `Error`, a string,
 * `null` and `undefined` all return the full shape. No branch reads `error.message`.
 *
 * @param {*} error - Whatever was caught. An `ApiError` from `apiClient` gets the most specific
 *   copy; anything else falls through to the context default.
 * @param {string} [context] - A `CONTEXT_COPY` key naming the surface, used for the last-resort
 *   copy and named in the development-only scrubber failure.
 * @returns {{headline: string, detail: string|null, retryable: boolean,
 *            action: object|null, supportRef: string|null}}
 */
export function translateError(error, context) {
  const chosen =
    fromCode(error) ?? fromReasons(error) ?? fromRefusal(error) ?? fromCategory(error) ?? fromContext(context);

  return enforceNoLeak(
    {
      headline: chosen.headline,
      detail: chosen.detail ?? null,
      retryable: chosen.retryable === true,
      action: chosen.action ?? null,
      supportRef: readSupportRef(error),
    },
    error,
    context,
  );
}
