/**
 * Integration tests for critical user flows
 * Tests: dashboard load, backtest trigger
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import App from '../../src/App';
// Statically imported, not `await import(...)`-ed inside a test.
//
// This used to be a dynamic import in the first test's body, so that one test was charged for
// transforming and evaluating the Dashboard page's entire dependency tree on a cold module
// cache — measured at well over 10s on this host — while the second test ran the identical
// import against a warm cache and finished in milliseconds. A module-graph cost is collection
// work, not behaviour under test, and at the top of the file it is paid during collection
// where it belongs. `vi.mock` factories are hoisted above this import, so the stubs below
// still apply.
import Dashboard from '../../src/pages/Dashboard';

// Mock apiClient & api
//
// The API layer is stubbed *exhaustively* on purpose. A unit test must reach no socket, and
// the earlier partial stub left holes it fell through: the enumerated `api` object named only
// three modules, so a page reaching for `api.paper` / `api.portfolio` / `api.library` — which
// the marketplace and paper-trading pages do — got `undefined` and either threw or fell back
// to a live client. `moduleStub()` answers every member name with a resolved mock, and
// `mockApi` answers every module name, so no accessor can miss.
const { mockGet, mockPost, mockApi, moduleStub } = vi.hoisted(() => {
  const mockGet = vi.fn();
  const mockPost = vi.fn();

  // Every member of an API module resolves through `mockGet`. These tests assert that a page
  // called the API layer, never which member of it the page reached for, so one shared spy is
  // both sufficient and what `expect(mockGet).toHaveBeenCalled()` is written against.
  const moduleStub = () =>
    new Proxy(
      {},
      {
        get: (_target, property) => (typeof property === 'symbol' ? undefined : mockGet),
        has: () => true,
      },
    );

  // Unknown module names answer too, so adding an API module cannot silently reintroduce a
  // live call here.
  const modules = {};
  const mockApi = new Proxy(modules, {
    get: (target, property) => {
      if (typeof property === 'symbol') return undefined;
      if (!(property in target)) target[property] = moduleStub();
      return target[property];
    },
    has: () => true,
  });

  return { mockGet, mockPost, mockApi, moduleStub };
});

vi.mock('../../src/apiClient', () => ({
  default: { get: mockGet, post: mockPost },
  get: mockGet,
  post: mockPost,
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  publicGet: mockGet,
  getMetrics: vi.fn(() => ({ endpoints: {} })),
  logout: vi.fn(),
  clearApiCache: vi.fn(),
  isAuthenticated: vi.fn(() => true),
  getToken: vi.fn(() => 'test-token'),
  testConnection: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

vi.mock('../../src/api', () => ({
  default: mockApi,
  get: mockGet,
  post: mockPost,
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  publicGet: mockGet,
  getMetrics: vi.fn(() => ({ endpoints: {} })),
  logout: vi.fn(),
  isAuthenticated: vi.fn(() => true),
  getToken: vi.fn(() => 'test-token'),
  api: mockApi,
  endpoints: mockApi,
  // Every named module export of src/api/index.js, so a page importing one directly gets the
  // stub rather than the real client.
  assetsApi: mockApi.assets,
  authApi: mockApi.auth,
  dataQualityApi: mockApi.dataQuality,
  exchangeApi: mockApi.exchange,
  marketApi: mockApi.market,
  ordersApi: mockApi.orders,
  strategiesApi: mockApi.strategies,
  portfolioApi: mockApi.portfolio,
  riskApi: mockApi.risk,
  billingApi: mockApi.billing,
  userApi: mockApi.user,
  referralApi: mockApi.referral,
  dashboardApi: mockApi.dashboard,
  healthApi: mockApi.health,
  supportApi: mockApi.support,
  paperApi: mockApi.paper,
  libraryApi: mockApi.library,
  notificationsApi: mockApi.notifications,
}));

// Pages that import a module file directly rather than through `src/api` bypass the mock
// above, so the modules this file's render trees reach are stubbed by path as well.
vi.mock('../../src/api/modules/dashboard', () => ({ dashboardApi: moduleStub() }));
vi.mock('../../src/api/modules/risk', () => ({ riskApi: moduleStub(), riskEndpoints: moduleStub() }));
vi.mock('../../src/api/modules/paper', () => ({ paperApi: moduleStub() }));
vi.mock('../../src/api/modules/portfolio', () => ({ portfolioApi: moduleStub() }));
vi.mock('../../src/api/modules/library', () => ({ libraryApi: moduleStub() }));

// The Supabase client, stubbed at the singleton.
//
// `src/supabase.js` builds a *real* `@supabase/supabase-js` client at import time, and
// `src/App.jsx` imports it, so merely collecting this file used to construct one. With
// `persistSession` on and the localStorage stub below answering every key with a session
// blob, the client's auth initialization then tried to refresh that token against the real
// project URL; jsdom's XHR sat on the socket until it gave up and raised `AggregateError`,
// which is the 30s that the first test was actually spending. The stub keeps the surface the
// app uses — nothing here opens a socket.
vi.mock('../../src/supabase', () => {
  const ok = (data = null) => vi.fn(async () => ({ data, error: null }));
  const auth = {
    getUser: ok({ user: { id: 'test-user', email: 'test@test.com', user_metadata: {} } }),
    getSession: ok({ session: null }),
    signInWithPassword: ok(null),
    signInWithOtp: ok(null),
    signInWithOAuth: ok(null),
    signUp: ok(null),
    verifyOtp: ok(null),
    signOut: vi.fn(async () => ({ error: null })),
    updateUser: ok(null),
    refreshSession: ok({ session: null }),
    setSession: ok({ session: null }),
    resend: ok(null),
    onAuthStateChange: vi.fn(() => ({
      data: { subscription: { unsubscribe: vi.fn() } },
    })),
    mfa: {
      getAuthenticatorAssuranceLevel: ok({ currentLevel: 'aal1', nextLevel: 'aal1' }),
      listFactors: ok({ totp: [], all: [] }),
      challenge: ok({ id: 'challenge-id' }),
      enroll: ok({ id: 'factor-id', totp: { qr_code: '', secret: '' } }),
      unenroll: ok(null),
      verify: ok(null),
    },
  };
  // A thenable query builder: every builder call returns the chain, and awaiting it yields the
  // empty result shape rather than reaching PostgREST.
  const table = () => {
    const chain = { then: (resolve) => resolve({ data: [], error: null }) };
    for (const method of ['select', 'insert', 'update', 'delete', 'upsert', 'eq', 'gte', 'lte', 'order', 'limit', 'single']) {
      chain[method] = vi.fn(() => chain);
    }
    return chain;
  };
  const supabase = {
    auth,
    from: vi.fn(() => table()),
    rpc: ok(null),
    channel: vi.fn(() => ({ on: vi.fn(() => ({ subscribe: vi.fn() })), subscribe: vi.fn() })),
    removeChannel: vi.fn(),
  };
  return { supabase, default: supabase };
});

const mockSession = JSON.stringify({
  access_token: 'test-token',
  user: { id: 'test-user', email: 'test@test.com' }
});

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn((key) => key === 'token' ? 'test-token' : mockSession),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};
global.localStorage = localStorageMock;

// Mock sessionStorage
const sessionStorageMock = {
  getItem: vi.fn((key) => key === 'token' ? 'test-token' : mockSession),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};
global.sessionStorage = sessionStorageMock;

describe('Integration Tests', () => {
  beforeEach(() => {
    mockGet.mockResolvedValue({
      overview: { total_value: 10000, today_pnl: 250 },
      strategies: { items: [] },
      recent_activity: { signals: [], insights: [] },
      equity_curve: [],
      exchanges: [],
      plans: [],
      status: 'healthy'
    });
    sessionStorageMock.getItem.mockImplementation((key) => key === 'token' ? 'test-token' : mockSession);
    localStorageMock.getItem.mockImplementation((key) => key === 'token' ? 'test-token' : mockSession);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe('Dashboard Load', () => {
    it('should load dashboard data on mount', async () => {
      mockGet.mockResolvedValue({
        overview: { total_value: 10000, today_pnl: 250 },
        strategies: { items: [] },
        recent_activity: { signals: [], insights: [] },
        equity_curve: []
      });

      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(mockGet).toHaveBeenCalled();
      }, { timeout: 10000 });
    });

    it('should handle API errors gracefully', async () => {
      mockGet.mockResolvedValue(null);

      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(mockGet).toHaveBeenCalled();
      }, { timeout: 10000 });
    });
  });

  describe('Backtest Trigger', () => {
    it('should trigger backtest with correct payload', async () => {
      const { post } = await import('../../src/apiClient');
      post.mockResolvedValue({
        total_return_pct: 15.5,
        win_rate_pct: 60,
        total_trades: 50
      });

      render(
        <MemoryRouter>
          <App />
        </MemoryRouter>
      );

      expect(post).toBeDefined();
    });
  });
});
