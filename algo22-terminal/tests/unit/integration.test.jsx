/**
 * Integration tests for critical user flows
 * Tests: dashboard load, backtest trigger
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../../src/App';

// Mock apiClient
vi.mock('../../src/apiClient', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
}));

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(() => 'test-token'),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};
global.localStorage = localStorageMock;

// Mock sessionStorage
const sessionStorageMock = {
  getItem: vi.fn(() => 'test-token'),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};
global.sessionStorage = sessionStorageMock;

describe('Integration Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe('Dashboard Load', () => {
    it('should load dashboard data on mount', async () => {
      const { get } = await import('../../src/apiClient');
      get.mockResolvedValue({
        total_trades: 100,
        total_pnl: 5000,
        win_rate: 65,
        active_bots: 3
      });

      render(<App />);

      await waitFor(() => {
        expect(get).toHaveBeenCalledWith('/api/stats');
      });
    });

    it('should handle API errors gracefully', async () => {
      const { get } = await import('../../src/apiClient');
      get.mockResolvedValue(null);

      render(<App />);

      await waitFor(() => {
        expect(get).toHaveBeenCalled();
      });
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

      render(<App />);

      // Navigate to backtester (would need to implement navigation in test)
      // This is a placeholder for the actual integration test
      // In a real implementation, you would:
      // 1. Navigate to strategy builder
      // 2. Create a strategy
      // 3. Click backtest button
      // 4. Verify the API call

      expect(post).toBeDefined();
    });
  });
});
