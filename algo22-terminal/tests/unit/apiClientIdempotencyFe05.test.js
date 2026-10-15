/**
 * FE-05 -- apiClient's request interceptor no longer overwrites a caller-supplied
 * Idempotency-Key, and its own fallback generator no longer derives the key from
 * request content.
 *
 * THE DEFECT
 *   The interceptor called generateIdempotencyKey(url, data), which hashed
 *   `${sessionId}_${url}_${payloadStr}` into the key unconditionally -- even when the
 *   caller (e.g. api/modules/strategies.js::deploy's options.idempotencyKey) had
 *   already set its own Idempotency-Key header, that header was thrown away and
 *   replaced. Worse, the hash was deterministic over (session, url, payload): two
 *   SEPARATE, deliberately-identical submissions (e.g. placing the same order twice
 *   in a row on purpose) collided onto the exact same key, so the backend's own
 *   idempotency check silently treated the second legitimate action as a duplicate
 *   retry of the first.
 *
 * THE FIX
 *   The interceptor now honours an existing `config.headers['Idempotency-Key']` if
 *   the caller set one, and otherwise generates `idemp_${crypto.randomUUID()}` --
 *   independent of request content, so two identical submissions never collide.
 *
 * Mocking convention mirrors tests/unit/attack_frontend_full.test.js (ATTACK 3, the
 * existing idempotency test this file extends): mock axios.create to capture the
 * request interceptor function, then invoke it directly on a hand-built config.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

const sessionStorageMock = (() => {
  let store = {};
  return {
    getItem: vi.fn((k) => store[k] ?? null),
    setItem: vi.fn((k, v) => { store[k] = String(v); }),
    removeItem: vi.fn((k) => { delete store[k]; }),
    clear: vi.fn(() => { store = {}; }),
  };
})();
global.sessionStorage = sessionStorageMock;

const localStorageMock = (() => {
  let store = {};
  return {
    getItem: vi.fn((k) => store[k] ?? null),
    setItem: vi.fn((k, v) => { store[k] = String(v); }),
    removeItem: vi.fn((k) => { delete store[k]; }),
    clear: vi.fn(() => { store = {}; }),
  };
})();
global.localStorage = localStorageMock;

const { mockAxiosInstance } = vi.hoisted(() => {
  return {
    mockAxiosInstance: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
      patch: vi.fn(),
      interceptors: {
        request: { use: vi.fn((fn) => { mockAxiosInstance._reqInterceptor = fn; }) },
        response: { use: vi.fn((ok, err) => { mockAxiosInstance._resOk = ok; mockAxiosInstance._resErr = err; }) },
      },
    },
  };
});

vi.mock('axios', () => ({
  default: { create: vi.fn(() => mockAxiosInstance) },
}));

import { clearApiCache } from '../../src/apiClient';

describe('FE-05: idempotency keys are per-intent, not per-content-hash', () => {
  beforeEach(() => {
    sessionStorageMock.clear();
    localStorageMock.clear();
    clearApiCache();
    vi.clearAllMocks();
  });

  const runInterceptor = (config) => mockAxiosInstance._reqInterceptor(config);

  it('honours a caller-supplied Idempotency-Key rather than overwriting it', () => {
    const callerKey = 'caller-chosen-intent-key-abc123';
    const config = {
      method: 'POST',
      url: '/api/strategies/strat-1/deploy',
      headers: { 'Idempotency-Key': callerKey },
      data: { mode: 'paper' },
    };

    const result = runInterceptor(config);

    expect(result.headers['Idempotency-Key']).toBe(callerKey);
  });

  it('generates its own key when the caller supplied none', () => {
    const config = {
      method: 'POST',
      url: '/api/orders',
      headers: {},
      data: { symbol: 'BTCUSDT', qty: 1 },
    };

    const result = runInterceptor(config);

    expect(result.headers['Idempotency-Key']).toBeDefined();
    expect(result.headers['Idempotency-Key']).toContain('idemp_');
  });

  it('two identical submissions with no caller key get DIFFERENT idempotency keys', () => {
    const makeConfig = () => ({
      method: 'POST',
      url: '/api/orders',
      headers: {},
      data: { symbol: 'BTCUSDT', side: 'buy', qty: 1, price: 65000 },
    });

    const first = runInterceptor(makeConfig());
    const second = runInterceptor(makeConfig());

    expect(first.headers['Idempotency-Key']).not.toBe(second.headers['Idempotency-Key']);
  });

  it('a GET request is never given an Idempotency-Key', () => {
    const config = { method: 'GET', url: '/api/orders', headers: {} };

    const result = runInterceptor(config);

    expect(result.headers['Idempotency-Key']).toBeUndefined();
  });
});
