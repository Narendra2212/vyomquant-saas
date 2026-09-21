/**
 * Portfolio API Module
 *
 * Endpoints: /api/portfolio/*
 *
 * ---------------------------------------------------------------------------
 * EIGHT DEAD METHODS REMOVED — vyomquant-ui-redesign tasks 10.10 and 13.1
 * design.md §1.4, §7.6. Requirement 19.4.
 * ---------------------------------------------------------------------------
 * `backend_app/routers/portfolio.py` registers exactly six routes — `/summary`,
 * `/equity-curve`, `/allocation`, `/heatmap`, `/recent-transactions`,
 * `/close-all`. This module now declares exactly the five reads among them that
 * it has a caller for. Eight further methods used to sit here with no route
 * behind any of them, so every one of the eight always 404d. A documented client
 * method that cannot succeed is a non-functional API surface (Requirement 19.4).
 *
 * Task 10.10 removed the six with no call site: `getPosition`, `closePosition`,
 * `getPositionHistory`, `getBalance`, `getPnL`, `getPerformance`.
 * `closePosition` was the one that mattered — it read as a working
 * position-close and was not one. The only `POST /positions/{id}/close` in the
 * tree belongs to `backend_app/backend/portfolio_management.py`, mounted at
 * `/api/internal/portfolio-mgmt` behind `Depends(get_admin_user)`: a different
 * prefix, and unreachable for a trader either way.
 *
 * Task 13.1 removes the last two, `getOpenPositions` and `getPositions`. They
 * had to wait because `pages/Portfolio.jsx` called them —
 * `getOpenPositions().catch(() => getPositions())`, a chain that 404d twice and
 * left the live positions table empty for every trader on every load. Deleting
 * them ahead of that page would have turned a 404 into a `TypeError`, which is
 * the worse failure. That read now points at `GET /api/dashboard`, which returns
 * a real normalised `positions[]` (design.md §7.1, §7.6), so the two methods
 * have no caller and no route and are gone. The internal admin-only
 * `GET /positions` remains the only other positions route in the tree, and it is
 * not this module's to expose.
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

