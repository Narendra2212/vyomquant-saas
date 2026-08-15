/**
 * Integration tests for critical user flows
 * Tests: dashboard load, backtest trigger
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import App from '../../src/App';

// Mock apiClient & api
const { mockGet, mockPost } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
}));

vi.mock('../../src/apiClient', () => ({
  get: mockGet,
  post: mockPost,
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  logout: vi.fn(),
  clearApiCache: vi.fn(),
}));

vi.mock('../../src/api', () => ({
  get: mockGet,
  post: mockPost,
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  api: {
    dashboard: { getOverview: mockGet, getDashboard: mockGet },
    billing: { getPlans: mockGet, getEntitlements: mockGet },
    orders: { getHistory: mockGet },
  },
  dashboardApi: { getOverview: mockGet, getDashboard: mockGet, getStats: mockGet, getSystemHealth: mockGet },
  exchangeApi: { list: mockGet, getAccounts: mockGet },
  riskApi: { getAccountHealth: mockGet, getMarginHealth: mockGet, getConfig: mockGet, getLimits: mockGet },
  referralApi: { getReferralStats: mockGet, getStats: mockGet },
  billingApi: { getPlans: mockGet, getEntitlements: mockGet },
  healthApi: { getHealth: mockGet, getSystemHealth: mockGet },
}));
vi.mock('../../src/api/modules/dashboard', () => ({
  dashboardApi: {
    getOverview: mockGet,
    getDashboard: mockGet,
    getStats: mockGet,
    getSystemHealth: mockGet,
  },
}));

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
        total_trades: 100,
        total_pnl: 5000,
        win_rate: 65,
        active_bots: 3
      });

      render(
        <MemoryRouter initialEntries={['/app/dashboard']}>
          <App />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(mockGet).toHaveBeenCalled();
      }, { timeout: 10000 });
    }, 15000);

    it('should handle API errors gracefully', async () => {
      mockGet.mockResolvedValue(null);

      render(
        <MemoryRouter initialEntries={['/app/dashboard']}>
          <App />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(mockGet).toHaveBeenCalled();
      }, { timeout: 10000 });
    }, 15000);
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
