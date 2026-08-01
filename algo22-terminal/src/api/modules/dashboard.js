/**
 * Dashboard API Module
 * 
 * Endpoints: /api/dashboard/*
 * 
 * PHASE 4: Dashboard Aggregation API
 * Single source of truth for all Dashboard data.
 * Replaces multiple frontend API calls with one optimized endpoint.
 */
import { get } from '../../apiClient';

/**
 * @typedef {Object} DashboardOverview
 * @property {number} total_value
 * @property {number} today_pnl
 * @property {number} today_return_pct
 * @property {number} unrealized_pnl
 * @property {number} available_balance
 */

/**
 * @typedef {Object} DashboardSubscription
 * @property {string} tier
 * @property {string} billing_status
 * @property {string} subscription_end
 * @property {boolean} is_trial
 */

/**
 * @typedef {Object} DashboardUsage
 * @property {number} strategies
 * @property {number} strategies_limit
 * @property {number} deployments
 * @property {number} deployments_limit
 * @property {number} ml_training_used
 * @property {number} ml_training_limit
 */

/**
 * @typedef {Object} DashboardDeployments
 * @property {number} running
 * @property {number} stopped
 * @property {number} total
 * @property {Array} items
 */

/**
 * @typedef {Object} DashboardStrategies
 * @property {number} total
 * @property {number} active
 * @property {number} paused
 * @property {Array} items
 */

/**
 * @typedef {Object} DashboardMarketplace
 * @property {number} available_count
 * @property {number} user_publications
 * @property {number} total_subscribers
 * @property {Array} featured
 */

/**
 * @typedef {Object} DashboardRisk
 * @property {string} risk_level
 * @property {number} current_drawdown_pct
 * @property {number} max_daily_loss
 * @property {boolean} circuit_breaker_armed
 * @property {number} circuit_breaker_breaches
 */

/**
 * @typedef {Object} DashboardNotifications
 * @property {number} unread_count
 * @property {number} total_count
 * @property {Array} recent
 * @property {Object} categories
 */

/**
 * @typedef {Object} DashboardReferrals
 * @property {string} referral_code
 * @property {string} referral_link
 * @property {number} total_referrals
 * @property {number} active_referrals
 * @property {number} lifetime_earnings
 */

/**
 * @typedef {Object} DashboardHealth
 * @property {number} exchange_api_latency_ms
 * @property {string} exchange_api_latency_status
 * @property {string} risk_circuit_breaker_status
 * @property {string} order_state_sync_status
 */

/**
 * @typedef {Object} DashboardExchange
 * @property {number} total_exchanges
 * @property {number} connected_exchanges
 * @property {boolean} can_trade
 * @property {Array} exchanges
 */

/**
 * @typedef {Object} DashboardRecentActivity
 * @property {Array} signals
 * @property {Array} insights
 */

/**
 * @typedef {Object} DashboardData
 * @property {DashboardOverview} overview
 * @property {DashboardSubscription} subscription
 * @property {DashboardUsage} usage
 * @property {DashboardDeployments} deployments
 * @property {DashboardStrategies} strategies
 * @property {DashboardMarketplace} marketplace
 * @property {DashboardRisk} risk
 * @property {DashboardNotifications} notifications
 * @property {DashboardReferrals} referrals
 * @property {DashboardHealth} health
 * @property {DashboardExchange} exchange
 * @property {DashboardRecentActivity} recent_activity
 * @property {Array} equity_curve
 * @property {string} generated_at
 */

export const dashboardApi = {
  /**
   * Get complete dashboard data in one optimized call
   * @param {Object} params
   * @param {number} [params.equity_days=30] - Number of days for equity curve data
   * @returns {Promise<DashboardData>}
   */
  getDashboard: (params = {}) => {
    const { equity_days = 30 } = params;
    return get(`/api/dashboard?equity_days=${equity_days}`);
  },

  /**
   * Get dashboard overview data only (portfolio summary)
   * Lightweight endpoint for quick overview updates
   * @returns {Promise<{overview: DashboardOverview}>}
   */
  getOverview: () => get('/api/dashboard/overview'),

  /**
   * Get dashboard strategies data only
   * Lightweight endpoint for strategy updates
   * @returns {Promise<{strategies: DashboardStrategies, insights: Array}>}
   */
  getStrategies: () => get('/api/dashboard/strategies'),
};

// Legacy compatibility
export const dashboardEndpoints = dashboardApi;
export default dashboardApi;
