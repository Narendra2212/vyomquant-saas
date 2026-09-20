import React, { createContext, useContext, useState, useCallback } from 'react';
// Task 10.8. `extractErrorMessage` is deleted from `ui-legacy/primitives.jsx` — it fell through
// to `JSON.stringify(detail)` and then `err.message`, so a backend traceback reached the screen
// verbatim (Requirement 14.4). The words now come from `design/errorCopy.js`.
import { errorLine } from '../design/errorLine';
import { validateConditions } from '../utils/engineHelpers';

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * LOGIC ENGINE — local condition checks only. No network call path.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `production-launch-hardening` task **9.2**. Requirements 1.26, 2.26. Preservation 3.7.
 *
 * `evaluateLogic` used to `await post('/logic/evaluate', …)`, with the same two independent
 * defects as its two sibling contexts: `post` was never imported, so the expression raised
 * `ReferenceError: post is not defined` before a request existed; and `/logic/evaluate` is
 * declared by no route on this backend. See `IndicatorEngineContext.jsx`'s header for the full
 * argument — it applies unchanged here, including the part about `tasks.md`'s premise. There is
 * no local condition evaluator to fall back on: `validateConditions` checks that a condition
 * names a type, an operator, both operands and a known indicator key, and never compares two
 * values, so no signal was ever produced on this side.
 *
 * The disposition is removal because nothing in `src/` calls `useLogicEngine`.
 *
 * WHAT SURVIVED
 * -------------
 * The empty-condition short circuit — `{signal: 'NONE', confidence: 0, reason: 'No conditions
 * defined'}` — which is the one path in this file that already answered locally and correctly,
 * and the `validateConditions` shape check. Both are pinned by
 * `tests/unit/builder/enginePaths.test.jsx`.
 *
 * Every refusal is still returned rather than thrown, and still carries copy from
 * `design/errorCopy.js`, so a caller's `{signal, confidence}` contract is total over every
 * input.
 *
 * REMOVED WITH THE CALL PATH
 * --------------------------
 * `logicCache` / `clearLogicCache` / `generateLogicCacheKey` and `lastSignal`, all of which were
 * written only from a response; `evaluateLogicBatch`, a fan-out over the removed capability; and
 * `isEvaluating` as a state cell — it is published as the constant `false`, because with no
 * request there is no in-flight period.
 */

export const LogicEngineContext = createContext(null);

export const useLogicEngine = () => {
  const context = useContext(LogicEngineContext);
  if (!context) throw new Error("useLogicEngine must be used within LogicEngineProvider");
  return context;
};

export const LogicEngineProvider = ({ children }) => {
  const [evaluationError, setEvaluationError] = useState(null);

  /**
   * Answer an empty condition list locally; check any other list's shape, then refuse.
   *
   * Never throws. A caller always gets `{signal, confidence}`, and on a refusal an `error`
   * carrying a line `design/errorCopy.js` authors.
   *
   * @param {Array} conditions - Condition records from the builder's logic nodes.
   * @param {Object} indicatorsData - Indicator series by name, for the shape check.
   * @returns {Promise<{signal: string, confidence: number, reason?: string, error?: string}>}
   */
  const evaluateLogic = useCallback(async (conditions, indicatorsData) => {
    // Answered here, with no transport and no engine. The one case that has always been right.
    if (!conditions || conditions.length === 0) {
      return { signal: 'NONE', confidence: 0, reason: 'No conditions defined' };
    }

    // The local shape check survives: it is what tells a malformed condition from an
    // unreachable engine, and the two are different things for a caller to be told.
    const validationErrors = validateConditions(conditions, indicatorsData);
    const cause =
      validationErrors.length > 0
        ? new Error(`Validation failed: ${validationErrors.map((e) => e.message).join(', ')}`)
        : new Error('No logic engine is reachable from the client');

    const message = errorLine(cause, 'builder');
    setEvaluationError({
      message,
      conditions,
      timestamp: new Date().toISOString(),
    });
    return { signal: 'NONE', confidence: 0, error: message };
  }, []);

  // Get available operators for UI
  const getAvailableOperators = useCallback(() => {
    return {
      comparison: [
        { value: '>', label: 'Greater Than', description: 'Value > Threshold' },
        { value: '<', label: 'Less Than', description: 'Value < Threshold' },
        { value: '>=', label: 'Greater or Equal', description: 'Value >= Threshold' },
        { value: '<=', label: 'Less or Equal', description: 'Value <= Threshold' },
        { value: '==', label: 'Equals', description: 'Value == Threshold' }
      ],
      crossover: [
        { value: 'crosses_above', label: 'Crosses Above', description: 'Series A crosses above Series B' },
        { value: 'crosses_below', label: 'Crosses Below', description: 'Series A crosses below Series B' }
      ],
      threshold: [
        { value: 'above_threshold', label: 'Above Threshold', description: 'Value > Constant' },
        { value: 'below_threshold', label: 'Below Threshold', description: 'Value < Constant' }
      ]
    };
  }, []);

  // Get condition template for UI
  const getConditionTemplate = useCallback((type = 'comparison') => {
    const templates = {
      comparison: {
        type: 'comparison',
        left: { source: 'indicator', name: '', key: 'value' },
        operator: '>',
        right: { source: 'constant', value: 0 }
      },
      crossover: {
        type: 'crossover',
        left: { source: 'indicator', name: '', key: 'value' },
        operator: 'crosses_above',
        right: { source: 'indicator', name: '', key: 'value' }
      },
      threshold: {
        type: 'threshold',
        left: { source: 'indicator', name: '', key: 'value' },
        operator: 'above_threshold',
        right: { source: 'constant', value: 70 }
      }
    };
    return templates[type] || templates.comparison;
  }, []);

  const value = {
    evaluateLogic,
    // Constant, not a state cell: see the header. Published so a refusal cannot leave the
    // builder showing a spinner.
    isEvaluating: false,
    evaluationError,
    getAvailableOperators,
    getConditionTemplate
  };

  return (
    <LogicEngineContext.Provider value={value}>
      {children}
    </LogicEngineContext.Provider>
  );
};
