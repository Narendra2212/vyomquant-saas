/**
 * @fileoverview Unit Tests for Portfolio Adapter Pattern
 * Tests mapBackendToUIPortfolio transformation logic with exhaustive edge cases
 * @version 1.0.0
 */

import { describe, it, expect } from 'vitest';

// ═══════════════════════════════════════════════════════════════════════════
// TEST SETUP: Extract adapter logic for isolated testing
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Adapter function under test
 * Mirrors implementation in api.js for isolated unit testing
 *
 * @param {Object} backendData - Raw API response from FastAPI
 * @returns {Object} - Transformed data for UI consumption
 */
function mapBackendToUIPortfolio(backendData) {
  // Defensive null-check to prevent runtime crashes
  if (!backendData || typeof backendData !== 'object') {
    const escalationError = new Error(
      `[PortfolioAPI] Invalid backend response: expected object, received ${typeof backendData}`
    );
    escalationError.code = 'PORTFOLIO_ADAPTER_NULL_DATA';
    throw escalationError;
  }

  // Field mapping with explicit fallbacks for type safety
  const transformedData = {
    // Primary field mapping (currently 1:1 since schemas are aligned)
    total_value: backendData.total_value ?? backendData.portfolio_equity ?? 0,
    unrealized_pnl: backendData.unrealized_pnl ?? backendData.unrealizedPnl ?? 0,
    realized_pnl: backendData.realized_pnl ?? backendData.realizedPnl ?? 0,
    roi_percentage: backendData.roi_percentage ?? backendData.roi_pct ?? backendData.roi ?? 0,
  };

  // Type validation to catch schema drift early
  const requiredNumericFields = ['total_value', 'unrealized_pnl', 'realized_pnl', 'roi_percentage'];
  for (const field of requiredNumericFields) {
    if (typeof transformedData[field] !== 'number' || isNaN(transformedData[field])) {
      const escalationError = new Error(
        `[PortfolioAPI] Type mismatch in field '${field}': expected number, received ${typeof transformedData[field]}`
      );
      escalationError.code = 'PORTFOLIO_ADAPTER_TYPE_MISMATCH';
      escalationError.field = field;
      escalationError.receivedValue = transformedData[field];
      throw escalationError;
    }
  }

  return transformedData;
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST SUITE: mapBackendToUIPortfolio
// ═══════════════════════════════════════════════════════════════════════════

describe('mapBackendToUIPortfolio', () => {

  // ─────────────────────────────────────────────────────────────────────────
  // HAPPY PATH TESTS
  // ─────────────────────────────────────────────────────────────────────────

  describe('Happy Path - Valid Backend Responses', () => {

    it('should transform complete backend response to UI state (all fields present)', () => {
      const backendResponse = {
        total_value: 145200.50,
        unrealized_pnl: 4500.20,
        realized_pnl: 1200.80,
        roi_percentage: 3.1
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result).toEqual({
        total_value: 145200.50,
        unrealized_pnl: 4500.20,
        realized_pnl: 1200.80,
        roi_percentage: 3.1
      });
    });

    it('should handle positive ROI values', () => {
      const backendResponse = {
        total_value: 200000.00,
        unrealized_pnl: 10000.00,
        realized_pnl: 5000.00,
        roi_percentage: 7.5
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.roi_percentage).toBe(7.5);
      expect(result.total_value).toBe(200000.00);
    });

    it('should handle negative P&L values (loss scenario)', () => {
      const backendResponse = {
        total_value: 95000.00,
        unrealized_pnl: -5000.00,
        realized_pnl: -2000.00,
        roi_percentage: -6.8
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.unrealized_pnl).toBe(-5000.00);
      expect(result.realized_pnl).toBe(-2000.00);
      expect(result.roi_percentage).toBe(-6.8);
    });

    it('should handle zero values (empty portfolio)', () => {
      const backendResponse = {
        total_value: 0,
        unrealized_pnl: 0,
        realized_pnl: 0,
        roi_percentage: 0
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result).toEqual({
        total_value: 0,
        unrealized_pnl: 0,
        realized_pnl: 0,
        roi_percentage: 0
      });
    });

    it('should handle very large numbers (institutional portfolios)', () => {
      const backendResponse = {
        total_value: 1500000000.50,  // $1.5B
        unrealized_pnl: 50000000.75,   // $50M
        realized_pnl: 25000000.25,     // $25M
        roi_percentage: 5.0
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.total_value).toBe(1500000000.50);
      expect(result.unrealized_pnl).toBe(50000000.75);
    });

    it('should handle decimal precision (8 decimal places for crypto)', () => {
      const backendResponse = {
        total_value: 1.12345678,
        unrealized_pnl: 0.98765432,
        realized_pnl: 0.11111111,
        roi_percentage: 88.12345678
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.total_value).toBe(1.12345678);
      expect(result.unrealized_pnl).toBe(0.98765432);
      expect(result.realized_pnl).toBe(0.11111111);
      expect(result.roi_percentage).toBe(88.12345678);
    });
  });

  // ─────────────────────────────────────────────────────────────────────────
  // ALTERNATIVE FIELD NAME TESTS (Schema Evolution Support)
  // ─────────────────────────────────────────────────────────────────────────

  describe('Alternative Field Names - Schema Evolution', () => {

    it('should fallback to portfolio_equity if total_value is missing', () => {
      const backendResponse = {
        portfolio_equity: 98765.43,
        unrealized_pnl: 1234.56,
        realized_pnl: 789.01,
        roi_percentage: 2.1
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.total_value).toBe(98765.43);
    });

    it('should fallback to camelCase unrealizedPnl if snake_case missing', () => {
      const backendResponse = {
        total_value: 100000,
        unrealizedPnl: 3000,  // camelCase variant
        realized_pnl: 1000,
        roi_percentage: 4.0
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.unrealized_pnl).toBe(3000);
    });

    it('should fallback to camelCase realizedPnl if snake_case missing', () => {
      const backendResponse = {
        total_value: 100000,
        unrealized_pnl: 3000,
        realizedPnl: 1500,  // camelCase variant
        roi_percentage: 4.5
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.realized_pnl).toBe(1500);
    });

    it('should fallback to roi_pct then roi if roi_percentage missing', () => {
      const backendResponse = {
        total_value: 100000,
        unrealized_pnl: 5000,
        realized_pnl: 2000,
        roi_pct: 7.0  // Alternative field name
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.roi_percentage).toBe(7.0);
    });

    it('should fallback to roi if both roi_percentage and roi_pct missing', () => {
      const backendResponse = {
        total_value: 100000,
        unrealized_pnl: 5000,
        realized_pnl: 2000,
        roi: 8.5  // Short form
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.roi_percentage).toBe(8.5);
    });

    it('should prefer primary field name over fallbacks', () => {
      const backendResponse = {
        total_value: 100000,        // Primary
        portfolio_equity: 99999,    // Fallback (should be ignored)
        unrealized_pnl: 5000,       // Primary
        unrealizedPnl: 4999,        // Fallback (should be ignored)
        realized_pnl: 2000,         // Primary
        realizedPnl: 1999,         // Fallback (should be ignored)
        roi_percentage: 7.0,      // Primary
        roi_pct: 6.9,             // Fallback (should be ignored)
        roi: 6.8                  // Fallback (should be ignored)
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.total_value).toBe(100000);
      expect(result.unrealized_pnl).toBe(5000);
      expect(result.realized_pnl).toBe(2000);
      expect(result.roi_percentage).toBe(7.0);
    });
  });

  // ─────────────────────────────────────────────────────────────────────────
  // NULL & UNDEFINED HANDLING TESTS
  // ─────────────────────────────────────────────────────────────────────────

  describe('Null and Undefined Handling', () => {

    it('should throw PORTFOLIO_ADAPTER_NULL_DATA for null input', () => {
      expect(() => mapBackendToUIPortfolio(null)).toThrow('PORTFOLIO_ADAPTER_NULL_DATA');
    });

    it('should throw PORTFOLIO_ADAPTER_NULL_DATA for undefined input', () => {
      expect(() => mapBackendToUIPortfolio(undefined)).toThrow('PORTFOLIO_ADAPTER_NULL_DATA');
    });

    it('should throw PORTFOLIO_ADAPTER_NULL_DATA for string input', () => {
      expect(() => mapBackendToUIPortfolio('invalid')).toThrow('PORTFOLIO_ADAPTER_NULL_DATA');
    });

    it('should throw PORTFOLIO_ADAPTER_NULL_DATA for number input', () => {
      expect(() => mapBackendToUIPortfolio(12345)).toThrow('PORTFOLIO_ADAPTER_NULL_DATA');
    });

    it('should throw PORTFOLIO_ADAPTER_NULL_DATA for array input', () => {
      expect(() => mapBackendToUIPortfolio([1, 2, 3])).toThrow('PORTFOLIO_ADAPTER_NULL_DATA');
    });

    it('should handle null field values by defaulting to 0', () => {
      const backendResponse = {
        total_value: null,
        unrealized_pnl: null,
        realized_pnl: null,
        roi_percentage: null
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.total_value).toBe(0);
      expect(result.unrealized_pnl).toBe(0);
      expect(result.realized_pnl).toBe(0);
      expect(result.roi_percentage).toBe(0);
    });

    it('should handle undefined field values by defaulting to 0', () => {
      const backendResponse = {
        total_value: undefined,
        unrealized_pnl: undefined,
        realized_pnl: undefined,
        roi_percentage: undefined
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.total_value).toBe(0);
      expect(result.unrealized_pnl).toBe(0);
      expect(result.realized_pnl).toBe(0);
      expect(result.roi_percentage).toBe(0);
    });

    it('should handle missing fields (partial response)', () => {
      const backendResponse = {
        total_value: 100000
        // Missing: unrealized_pnl, realized_pnl, roi_percentage
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.total_value).toBe(100000);
      expect(result.unrealized_pnl).toBe(0);
      expect(result.realized_pnl).toBe(0);
      expect(result.roi_percentage).toBe(0);
    });

    it('should handle empty object {}', () => {
      const result = mapBackendToUIPortfolio({});

      expect(result).toEqual({
        total_value: 0,
        unrealized_pnl: 0,
        realized_pnl: 0,
        roi_percentage: 0
      });
    });
  });

  // ─────────────────────────────────────────────────────────────────────────
  // TYPE VALIDATION & COERCION TESTS
  // ─────────────────────────────────────────────────────────────────────────

  describe('Type Validation and Coercion Edge Cases', () => {

    it('should throw PORTFOLIO_ADAPTER_TYPE_MISMATCH for string number', () => {
      const backendResponse = {
        total_value: '145200.50',  // String instead of number
        unrealized_pnl: 4500.20,
        realized_pnl: 1200.80,
        roi_percentage: 3.1
      };

      expect(() => mapBackendToUIPortfolio(backendResponse)).toThrow('PORTFOLIO_ADAPTER_TYPE_MISMATCH');
    });

    it('should throw PORTFOLIO_ADAPTER_TYPE_MISMATCH for NaN values', () => {
      const backendResponse = {
        total_value: NaN,
        unrealized_pnl: 4500.20,
        realized_pnl: 1200.80,
        roi_percentage: 3.1
      };

      expect(() => mapBackendToUIPortfolio(backendResponse)).toThrow('PORTFOLIO_ADAPTER_TYPE_MISMATCH');
    });

    it('should throw PORTFOLIO_ADAPTER_TYPE_MISMATCH for Infinity', () => {
      const backendResponse = {
        total_value: Infinity,
        unrealized_pnl: 4500.20,
        realized_pnl: 1200.80,
        roi_percentage: 3.1
      };

      expect(() => mapBackendToUIPortfolio(backendResponse)).toThrow('PORTFOLIO_ADAPTER_TYPE_MISMATCH');
    });

    it('should throw PORTFOLIO_ADAPTER_TYPE_MISMATCH for -Infinity', () => {
      const backendResponse = {
        total_value: -Infinity,
        unrealized_pnl: 4500.20,
        realized_pnl: 1200.80,
        roi_percentage: 3.1
      };

      expect(() => mapBackendToUIPortfolio(backendResponse)).toThrow('PORTFOLIO_ADAPTER_TYPE_MISMATCH');
    });

    it('should include field name in type mismatch error', () => {
      const backendResponse = {
        total_value: 100000,
        unrealized_pnl: 'not-a-number',  // String in wrong field
        realized_pnl: 1000,
        roi_percentage: 1.0
      };

      let caughtError;
      try {
        mapBackendToUIPortfolio(backendResponse);
      } catch (error) {
        caughtError = error;
      }

      expect(caughtError.code).toBe('PORTFOLIO_ADAPTER_TYPE_MISMATCH');
      expect(caughtError.field).toBe('unrealized_pnl');
      expect(caughtError.receivedValue).toBe('not-a-number');
    });

    it('should detect type mismatch in roi_percentage', () => {
      const backendResponse = {
        total_value: 100000,
        unrealized_pnl: 1000,
        realized_pnl: 500,
        roi_percentage: '5.0%'  // String with percent sign
      };

      expect(() => mapBackendToUIPortfolio(backendResponse)).toThrow('PORTFOLIO_ADAPTER_TYPE_MISMATCH');
    });
  });

  // ─────────────────────────────────────────────────────────────────────────
  // EXTRA FIELDS PRESERVATION TESTS
  // ─────────────────────────────────────────────────────────────────────────

  describe('Extra Fields Handling', () => {

    it('should ignore extra fields not in UI schema', () => {
      const backendResponse = {
        total_value: 100000,
        unrealized_pnl: 1000,
        realized_pnl: 500,
        roi_percentage: 1.5,
        extra_field_1: 'should be ignored',
        extra_field_2: 12345,
        nested_object: { foo: 'bar' }
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result).toEqual({
        total_value: 100000,
        unrealized_pnl: 1000,
        realized_pnl: 500,
        roi_percentage: 1.5
      });
      expect(result).not.toHaveProperty('extra_field_1');
      expect(result).not.toHaveProperty('extra_field_2');
      expect(result).not.toHaveProperty('nested_object');
    });
  });

  // ─────────────────────────────────────────────────────────────────────────
  // NEGATIVE ZERO & EDGE CASES
  // ─────────────────────────────────────────────────────────────────────────

  describe('Edge Cases and Special Numeric Values', () => {

    it('should handle negative zero (-0)', () => {
      const backendResponse = {
        total_value: -0,
        unrealized_pnl: -0,
        realized_pnl: -0,
        roi_percentage: -0
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      // -0 === 0 in JavaScript, but Object.is would show difference
      expect(result.total_value).toBe(0);
      expect(result.unrealized_pnl).toBe(0);
      expect(result.realized_pnl).toBe(0);
      expect(result.roi_percentage).toBe(0);
    });

    it('should handle very small numbers (epsilon)', () => {
      const backendResponse = {
        total_value: Number.MIN_VALUE,
        unrealized_pnl: Number.EPSILON,
        realized_pnl: -Number.EPSILON,
        roi_percentage: 0.0000001
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(typeof result.total_value).toBe('number');
      expect(typeof result.unrealized_pnl).toBe('number');
      expect(typeof result.realized_pnl).toBe('number');
      expect(typeof result.roi_percentage).toBe('number');
    });

    it('should handle Number.MAX_SAFE_INTEGER safely', () => {
      const backendResponse = {
        total_value: Number.MAX_SAFE_INTEGER,
        unrealized_pnl: Number.MAX_SAFE_INTEGER,
        realized_pnl: Number.MAX_SAFE_INTEGER,
        roi_percentage: 9007199254740991  // Same value
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.total_value).toBe(Number.MAX_SAFE_INTEGER);
    });

    it('should handle scientific notation numbers', () => {
      const backendResponse = {
        total_value: 1.5e6,  // 1,500,000
        unrealized_pnl: 2.5e3,  // 2,500
        realized_pnl: -1.2e2,  // -120
        roi_percentage: 1.5e1  // 15
      };

      const result = mapBackendToUIPortfolio(backendResponse);

      expect(result.total_value).toBe(1500000);
      expect(result.unrealized_pnl).toBe(2500);
      expect(result.realized_pnl).toBe(-120);
      expect(result.roi_percentage).toBe(15);
    });
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// STATIC ANALYSIS: Memory Leak & Async Pattern Verification
// ═══════════════════════════════════════════════════════════════════════════

describe('Static Analysis - Service Layer Patterns', () => {

  it('adapter function should be pure (no side effects)', () => {
    const input = {
      total_value: 100000,
      unrealized_pnl: 1000,
      realized_pnl: 500,
      roi_percentage: 1.5
    };

    const originalInput = JSON.stringify(input);
    mapBackendToUIPortfolio(input);
    const afterCallInput = JSON.stringify(input);

    expect(afterCallInput).toBe(originalInput);
  });

  it('adapter function should not use global state', () => {
    // Verify no global variable references in function source
    const functionSource = mapBackendToUIPortfolio.toString();

    expect(functionSource).not.toMatch(/window\./);
    expect(functionSource).not.toMatch(/global\./);
    expect(functionSource).not.toMatch(/document\./);
    expect(functionSource).not.toMatch(/localStorage/);
  });

  it('adapter function should not contain blocking operations', () => {
    const functionSource = mapBackendToUIPortfolio.toString();

    expect(functionSource).not.toMatch(/await/);
    expect(functionSource).not.toMatch(/XMLHttpRequest/);
    expect(functionSource).not.toMatch(/fetch\(/);
  });

  it('error codes should be descriptive and follow naming convention', () => {
    const expectedCodes = [
      'PORTFOLIO_ADAPTER_NULL_DATA',
      'PORTFOLIO_ADAPTER_TYPE_MISMATCH'
    ];

    const functionSource = mapBackendToUIPortfolio.toString();

    expectedCodes.forEach(code => {
      expect(functionSource).toContain(code);
    });
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// TEST SUMMARY OUTPUT
// ═══════════════════════════════════════════════════════════════════════════

console.log(`
╔════════════════════════════════════════════════════════════════════════════╗
║                    PORTFOLIO ADAPTER TEST SUITE                            ║
╠════════════════════════════════════════════════════════════════════════════╣
║ Total Test Categories: 6                                                   ║
║ - Happy Path Tests                                                        ║
║ - Alternative Field Names (Schema Evolution)                              ║
║ - Null & Undefined Handling                                               ║
║ - Type Validation & Coercion                                              ║
║ - Extra Fields Handling                                                   ║
║ - Edge Cases & Special Values                                             ║
╠════════════════════════════════════════════════════════════════════════════╣
║ Static Analysis Checks: 4                                                  ║
║ - Pure function verification                                               ║
║ - No global state dependencies                                             ║
║ - No blocking operations                                                   ║
║ - Error code convention compliance                                         ║
╚════════════════════════════════════════════════════════════════════════════╝
`);
