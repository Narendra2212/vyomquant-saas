/**
 * Portfolio API Module
 *
 * Endpoints: /api/portfolio/*
 *
 * ---------------------------------------------------------------------------
 * SIX DEAD METHODS REMOVED — vyomquant-ui-redesign task 10.10
 * design.md §1.4, §7.6. Requirement 19.4.
 * ---------------------------------------------------------------------------
 * `getPosition`, `closePosition`, `getPositionHistory`, `getBalance`, `getPnL`
 * and `getPerformance` are gone. `backend_app/routers/portfolio.py` registers
 * exactly six routes — `/summary`, `/equity-curve`, `/allocation`, `/heatmap`,
 * `/recent-transactions`, `/close-all` — so every one of those six always 404d,
 * and none had a call site anywhere in `src/`. A documented client method that
 * cannot succeed is a non-functional API surface (Requirement 19.4).
 *
 * `closePosition` was the one that mattered: it read as a working position-close
 * and was not one. The only `POST /positions/{id}/close` in the tree belongs to
 * `backend_app/backend/portfolio_management.py`, mounted at
 * `/api/internal/portfolio-mgmt` behind `Depends(get_admin_user)` — a different
 * prefix, and unreachable for a trader either way.
 *
 * `getPositions` and `getOpenPositions` are equally routeless but stay for now;
 * see the note on `getOpenPositions` below.
 *
 * This module is a read surface. `get` is the only verb it needs.
 */
import { get } from '../../apiClient';

/**
 * @typedef {Object} Position
 * @property {string} positionId - Position ID
 * @property {string} symbol - Trading symbol
 * @property {string} side - Position side: 'long' or 'short'
 * @property {number} size - Position size
 * @property {number} entryPrice - Average entry price
 * @property {number} currentPrice - Current market price
 * @property {number} unrealizedPnl - Unrealized profit/loss
 * @property {number} realizedPnl - Realized profit/loss
 * @property {string} timestamp - Position timestamp
 */

/**
 * @typedef {Object} PortfolioSummary
 * @property {number} totalValue - Total portfolio value
 * @property {number} totalPnl - Total profit/loss
 * @property {number} availableBalance - Available balance
 * @property {number} usedBalance - Used balance
 * @property {number} marginUsed - Margin used
 * @property {number} marginLevel - Margin level
 * @property {Position[]} positions - Open positions
 */

export const portfolioApi = {
  /**
   * Get portfolio summary
   * @returns {Promise<PortfolioSummary>}
   */
  getSummary: async () => {
    return get('/api/portfolio/summary');
  },

  /**
   * Get all positions
   * @param {Object} [filters] - Optional filters
   * @param {string} [filters.symbol] - Filter by symbol
 * @param {string} [filters.side] - Filter by side
   * @returns {Promise<Position[]>}
   */
  getPositions: async (filters = {}) => {
    const params = new URLSearchParams(filters);
    return get(`/api/portfolio/positions?${params}`);
  },

  /**
   * Get open positions only
   *
   * NOTE: no such route exists — `/api/portfolio/positions/open` 404s, as does
   * `getPositions` above. Both are kept only because `pages/Portfolio.jsx` still
   * calls them; task 13.1 removes them in the same change that re-points that
   * read at `/api/dashboard`. Deleting them here would turn a 404 into a
   * `TypeError`, which is the worse failure.
   *
   * @returns {Promise<Position[]>}
   */
  getOpenPositions: async () => {
    return get('/api/portfolio/positions/open');
  },

  /**
   * Get asset allocation
   * @returns {Promise<Array<{asset: string, value_usd: number, pct: number}>>}
   */
  getAllocation: async () => {
    return get('/api/portfolio/allocation');
  },

  /**
   * Get equity curve
   * @param {number} [days=90]
   * @returns {Promise<Array<{timestamp: string, equity: number}>>}
   */
  getEquityCurve: async (days = 90) => {
    return get(`/api/portfolio/equity-curve?days=${days}`);
  },

  /**
   * Get PnL heatmap
   * @param {number} [months=3]
   * @returns {Promise<Array<{date: string, pnl_usd: number}>>}
   */
  getHeatmap: async (months = 3) => {
    return get(`/api/portfolio/heatmap?months=${months}`);
  },

  /**
   * Get recent transactions
   * @param {number} [limit=50]
   * @param {number} [days=30]
   */
  getRecentTransactions: async (limit = 50, days = 30) => {
    return get(`/api/portfolio/recent-transactions?limit=${limit}&days=${days}`);
  },
};

