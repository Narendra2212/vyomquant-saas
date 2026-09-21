import React, { createContext, useContext, useState, useCallback } from 'react';
// Task 10.8. `extractErrorMessage` is deleted from `ui-legacy/primitives.jsx` — it fell through
// to `JSON.stringify(detail)` and then `err.message`, so a backend traceback reached the screen
// verbatim (Requirement 14.4). The words now come from `design/errorCopy.js`.
import { errorLine } from '../design/errorLine';
import { getMinDataLength } from '../utils/engineHelpers';

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * INDICATOR ENGINE — local input checks only. No network call path.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `production-launch-hardening` task **9.2**. Requirements 1.25, 2.25. Preservation 3.7.
 *
 * WHAT WAS REMOVED, AND WHY REMOVAL RATHER THAN REPAIR
 * ---------------------------------------------------
 * `computeIndicator` used to `await post('/indicator/compute', …)`. That one expression carried
 * two independent defects:
 *
 *   * `post` was never imported — this module takes only `{ endpoints }` from `../api` and used
 *     nothing from it — so the expression raised `ReferenceError: post is not defined` before a
 *     request was built. The site's own `catch` then translated that programming error into
 *     `CONTEXT_COPY.builder`: "could not load the builder, try again". Retrying was the one
 *     action the copy suggested and the one action that could not work.
 *   * `/indicator/compute` is declared by no route. It appears in no Python source under
 *     `backend_app/`, under no router prefix, and `tests/test_route_contract.py` (task 9.1)
 *     resolves every frontend address against the app's own OpenAPI schema and finds nothing
 *     for it.
 *
 * Importing `post` would have turned the `ReferenceError` into a 404. Both halves had to go,
 * and the disposition is removal because **there is no consumer**: nothing in `src/` calls
 * `useIndicatorEngine`, which is measured in
 * `tests/unit/builder/enginePaths.test.jsx` §6 and is why a dead capability was never reported.
 *
 * `tasks.md` gives a different reason — that "the builder already computes indicators locally
 * via `DataPipelineContext` and `utils/engineHelpers`". That premise is false and the same
 * section measures it: `engineHelpers` exports four functions and none of them reads a candle,
 * `DataPipelineContext` publishes nine and every one fetches or validates, and there is no RSI,
 * SMA, EMA or MACD implementation anywhere in `src/`. Indicator values are produced by the
 * backend in this application, everywhere they are produced at all. So removal is right, for a
 * different reason than the task text gives: not "the local path stands" but "there is no
 * consumer and no route".
 *
 * WHAT SURVIVED
 * -------------
 * The length gate. `getMinDataLength(indicator, params)` is a real local computation — a candle
 * count, checked before anything else happens — and it is pinned by
 * `enginePaths.test.jsx`'s preservation section. A 14-period RSI still refuses five candles,
 * and it refuses them in authored words.
 *
 * Every refusal goes through `design/errorCopy.js` via `errorLine`, so Requirement 14.4 holds
 * on this path exactly as it did: no branch here puts a raw message on screen, and the
 * `cause` chain keeps the real reason available to a developer without putting it in the copy.
 *
 * REMOVED WITH THE CALL PATH, BECAUSE NOTHING COULD REACH IT ANY MORE
 * ------------------------------------------------------------------
 *   * `indicatorCache` / `clearIndicatorCache` / `generateCacheKey` — the cache was only ever
 *     written from a successful response, so with no response it could never hold an entry and
 *     the lookup branch was unreachable.
 *   * `lastComputation` — set from the same response.
 *   * the OHLCV standardisation pass — it existed to build the request body. Its only other
 *     use was `.length`, which is `ohlcvData.length`.
 *   * `computeIndicatorsBatch` — a fan-out over the removed capability. It could only ever
 *     return a map of refusals.
 *   * `isComputing` — kept on the context value, because the preservation test reads it, but no
 *     longer a state cell: with no request there is no in-flight period, so it is the constant
 *     `false` rather than a flag that is set and cleared in the same tick.
 */

export const IndicatorEngineContext = createContext(null);

export const useIndicatorEngine = () => {
  const context = useContext(IndicatorEngineContext);
  if (!context) throw new Error("useIndicatorEngine must be used within IndicatorEngineProvider");
  return context;
};

export const IndicatorEngineProvider = ({ children }) => {
  const [computationError, setComputationError] = useState(null);

  /**
   * Check the inputs for an indicator, then refuse — there is nothing here that computes one.
   *
   * Always rejects, and the rejection is always a line `design/errorCopy.js` authors. Two
   * distinct causes, because they are two different things for a caller to have done: input
   * that no indicator could be computed from, and an indicator engine that this client cannot
   * reach. The distinction is carried on `cause`, not in the copy.
   *
   * @param {string} indicator - `rsi`, `macd`, `ema`, …
   * @param {Array} ohlcvData - Candles, either object or positional-array form.
   * @param {Object} [params] - Indicator parameters; `period`, `fast`, `slow`, `signal`.
   * @returns {Promise<never>}
   */
  const computeIndicator = useCallback(async (indicator, ohlcvData, params = {}) => {
    const refuse = (cause) => {
      const message = errorLine(cause, 'builder');
      setComputationError({
        indicator,
        message,
        params,
        timestamp: new Date().toISOString(),
      });
      return new Error(message, { cause });
    };

    if (!ohlcvData || ohlcvData.length === 0) {
      throw refuse(new Error('No OHLCV data provided for indicator computation'));
    }

    // The one local computation on this path, and the one the preservation test pins.
    const minLength = getMinDataLength(indicator, params);
    if (ohlcvData.length < minLength) {
      throw refuse(
        new Error(
          `Not enough data for ${indicator.toUpperCase()} (need ${minLength} candles, got ${ohlcvData.length})`,
        ),
      );
    }

    // The inputs are usable and there is still no engine to compute with. Said plainly here
    // rather than by attempting a request: the route does not exist, and pretending otherwise
    // is what made this a `ReferenceError` dressed as a loading failure.
    throw refuse(new Error(`No indicator engine is reachable from the client for ${indicator}`));
  }, []);

  // Get available outputs for multi-output indicators
  const getAvailableOutputs = useCallback((indicator) => {
    const multiOutputIndicators = {
      macd: ['macd', 'signal', 'histogram'],
      bollinger: ['upper', 'middle', 'lower'],
      bb: ['upper', 'middle', 'lower'],
      stochastic: ['k', 'd'],
      rsi: ['value'],
      ema: ['value'],
      sma: ['value'],
      atr: ['value'],
      obv: ['value'],
      vwap: ['value']
    };
    return multiOutputIndicators[indicator.toLowerCase()] || ['value'];
  }, []);

  // Validate indicator parameters
  const validateIndicatorParams = useCallback((indicator, params) => {
    const errors = [];

    const validations = {
      rsi: { period: { min: 2, max: 100, required: true } },
      macd: {
        fast: { min: 2, max: 50, required: true },
        slow: { min: 5, max: 200, required: true },
        signal: { min: 2, max: 50, required: true }
      },
      ema: { period: { min: 2, max: 200, required: true } },
      sma: { period: { min: 2, max: 200, required: true } },
      bollinger: {
        period: { min: 2, max: 100, required: true },
        stdDev: { min: 0.5, max: 5, required: true }
      },
      atr: { period: { min: 2, max: 100, required: true } }
    };

    const indicatorValidations = validations[indicator.toLowerCase()];
    if (indicatorValidations) {
      Object.entries(indicatorValidations).forEach(([param, rules]) => {
        const value = params[param];
        if (rules.required && (value === undefined || value === null)) {
          errors.push({ param, message: `${param} is required` });
        }
        if (value !== undefined && rules.min !== undefined && value < rules.min) {
          errors.push({ param, message: `${param} must be >= ${rules.min}` });
        }
        if (value !== undefined && rules.max !== undefined && value > rules.max) {
          errors.push({ param, message: `${param} must be <= ${rules.max}` });
        }
      });
    }

    return errors;
  }, []);

  const value = {
    computeIndicator,
    // Nothing is ever in flight on this provider, so this is a constant rather than a state
    // cell. Published because a failure must never leave the builder showing a spinner, which
    // is what the preservation test reads it for.
    isComputing: false,
    computationError,
    getAvailableOutputs,
    validateIndicatorParams
  };

  return (
    <IndicatorEngineContext.Provider value={value}>
      {children}
    </IndicatorEngineContext.Provider>
  );
};
