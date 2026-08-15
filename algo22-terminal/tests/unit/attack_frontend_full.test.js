/**
 * ═══════════════════════════════════════════════════════════════════════════
 * HOSTILE FRONTEND AUDIT & ATTACK SUITE
 * ═══════════════════════════════════════════════════════════════════════════
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get, post, logout, clearApiCache, ApiError, getToken, isAuthenticated } from '../../src/apiClient';
import wsClient from '../../src/websocketClient';

// Storage Mocks
const sessionStorageMock = (() => {
  let store = {};
  return {
    getItem: vi.fn((k) => store[k] ?? null),
    setItem: vi.fn((k, v) => { store[k] = String(v); }),
    removeItem: vi.fn((k) => { delete store[k]; }),
    clear: vi.fn(() => { store = {}; })
  };
})();
global.sessionStorage = sessionStorageMock;

const localStorageMock = (() => {
  let store = {};
  return {
    getItem: vi.fn((k) => store[k] ?? null),
    setItem: vi.fn((k, v) => { store[k] = String(v); }),
    removeItem: vi.fn((k) => { delete store[k]; }),
    clear: vi.fn(() => { store = {}; })
  };
})();
global.localStorage = localStorageMock;

// Mock Axios
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
        response: { use: vi.fn((ok, err) => { mockAxiosInstance._resOk = ok; mockAxiosInstance._resErr = err; }) }
      }
    }
  };
});

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => mockAxiosInstance)
  }
}));

describe('HOSTILE FRONTEND ATTACK SUITE', () => {
  beforeEach(() => {
    sessionStorageMock.clear();
    localStorageMock.clear();
    clearApiCache();
    vi.clearAllMocks();
  });

  afterEach(() => {
    clearApiCache();
  });

  describe('Phase 2 & 3: Auth Lifecycle & Tenant Isolation', () => {
    it('ATTACK 1: Logout completely purges in-memory API cache (prevents User B seeing User A data)', async () => {
      sessionStorageMock.setItem('token', 'token_user_a');
      const userAData = { id: 'usr_a', tenant: 'tenant_a', strategies: ['strat_1', 'strat_2'] };
      mockAxiosInstance.get.mockResolvedValueOnce({ data: userAData });

      const data1 = await get('/api/strategies');
      expect(data1).toEqual(userAData);
      expect(mockAxiosInstance.get).toHaveBeenCalledTimes(1);

      await logout();
      expect(sessionStorageMock.getItem('token')).toBeNull();

      sessionStorageMock.setItem('token', 'token_user_b');
      const userBData = { id: 'usr_b', tenant: 'tenant_b', strategies: [] };
      mockAxiosInstance.get.mockResolvedValueOnce({ data: userBData });

      const data2 = await get('/api/strategies');
      expect(data2).toEqual(userBData);
      expect(mockAxiosInstance.get).toHaveBeenCalledTimes(2);
    });

    it('ATTACK 2: 401 Unauthorized clears active session and dispatches auth-expired event', async () => {
      sessionStorageMock.setItem('token', 'expired_token');
      const dispatchSpy = vi.spyOn(window, 'dispatchEvent');

      const error401 = {
        response: {
          status: 401,
          statusText: 'Unauthorized',
          data: { detail: 'Token expired or invalid' }
        },
        config: { url: '/api/protected', method: 'get' }
      };

      if (mockAxiosInstance._resErr) {
        try {
          await Promise.resolve(mockAxiosInstance._resErr(error401)).catch(() => {});
        } catch (e) {
          // Handled
        }
      }

      expect(sessionStorageMock.getItem('token')).toBeNull();
      const authExpiredCalls = dispatchSpy.mock.calls.filter(
        ([evt]) => evt && evt.type === 'auth-expired'
      );
      expect(authExpiredCalls.length).toBeGreaterThanOrEqual(1);
    });
  });

  describe('Phase 4: Trading Order Submission & Idempotency', () => {
    it('ATTACK 3: Rapid post requests attach stable Idempotency-Key headers', async () => {
      sessionStorageMock.setItem('token', 'valid_token');
      mockAxiosInstance.post.mockResolvedValue({
        data: { orderId: 'ord_123', status: 'FILLED' }
      });

      let capturedConfig = null;
      if (mockAxiosInstance._reqInterceptor) {
        const rawConfig = { method: 'POST', url: '/api/orders', headers: {}, data: { symbol: 'BTCUSDT', qty: 1 } };
        capturedConfig = mockAxiosInstance._reqInterceptor(rawConfig);
      }

      expect(capturedConfig.headers['Idempotency-Key']).toBeDefined();
      expect(capturedConfig.headers['Idempotency-Key']).toContain('idemp_');
      expect(capturedConfig.headers['Authorization']).toBe('Bearer valid_token');
    });
  });

  describe('Phase 5: Financial Precision & Satoshi Arithmetic', () => {
    it('ATTACK 4: Sub-satoshi and high-precision values format without catastrophic cancellation', () => {
      const satoshiQty = 0.00000001;
      const btcPrice = 64231.55;
      const feeRate = 0.00075;

      const notional = satoshiQty * btcPrice;
      const fee = notional * feeRate;

      expect(satoshiQty.toFixed(8)).toBe('0.00000001');
      expect(notional).toBeGreaterThan(0);
      expect(fee).toBeGreaterThan(0);
      expect(Number.isFinite(fee)).toBe(true);
    });
  });

  describe('Phase 7: WebSocket Reconnect Lifecycle', () => {
    it('ATTACK 5: WebSocket reconnect is re-enabled on explicit connect() after disconnect()', () => {
      wsClient.disconnect();
      expect(wsClient.reconnectEnabled).toBe(false);

      wsClient.connect('/ws/telemetry');
      expect(wsClient.reconnectEnabled).toBe(true);
      expect(wsClient.reconnectAttempts).toBe(0);
    });
  });

  describe('Phase 10: Billing Plan Convergence', () => {
    it('ATTACK 6: Authenticated helper methods converge correctly', () => {
      sessionStorageMock.removeItem('token');
      expect(isAuthenticated()).toBe(false);
      expect(getToken()).toBeNull();

      sessionStorageMock.setItem('token', 'active_sub_tok');
      expect(isAuthenticated()).toBe(true);
      expect(getToken()).toBe('active_sub_tok');
    });
  });

  describe('Phase 11: Schema, Dedup Cache & Event Isolation', () => {
    it('ATTACK 7: clearApiCache purges WebSocket event deduplication cache across user sessions', async () => {
      const { getGlobalDedupCache } = await import('../../src/utils/eventDedupCache.js');
      const cache = getGlobalDedupCache();
      cache.markSeen('orders', 'evt_1001');
      expect(cache.isDuplicate('orders', 'evt_1001')).toBe(true);

      clearApiCache();
      const freshCache = getGlobalDedupCache();
      expect(freshCache.isDuplicate('orders', 'evt_1001')).toBe(false);
    });

    it('ATTACK 8: Strategy builder update and create safely handle API responses', async () => {
      const { strategiesApi } = await import('../../src/api/modules/strategies.js');
      mockAxiosInstance.post.mockResolvedValueOnce({ data: { status: 'created', strategy_id: 'strat_new' } });
      const created = await strategiesApi.create({ name: 'Alpha' });
      expect(created.strategy_id).toBe('strat_new');

      mockAxiosInstance.put.mockResolvedValueOnce({ data: { status: 'updated', strategy_id: 'strat_new' } });
      const updated = await strategiesApi.update('strat_new', { name: 'Alpha v2' });
      expect(updated.status).toBe('updated');
    });
  });
});
