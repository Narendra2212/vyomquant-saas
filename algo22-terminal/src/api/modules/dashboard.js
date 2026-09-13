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
 * @property {number} current_drawdown_pct - DEPRECATED (BC-1): carries `today_return_pct`, not a
 *   drawdown, so a profitable day reads as a positive "drawdown". Read
 *   `current_drawdown_pct_v2` instead.
 * @property {number|null} current_drawdown_pct_v2 - The peak-to-trough figure. `null` when no
 *   drawdown can be measured — never 0.0 as a stand-in.
 * @property {number|null} open_positions_count - BC-2: an `int` when the positions were counted
 *   and `null` when they could not be read. Never `0` as a stand-in, because a count of zero is
 *   the safest-looking reading a broken positions read could publish.
 * @property {number|null} risk_score - `null` alongside a `null` `open_positions_count`.
 * @property {DashboardDegraded|null} degraded - The same marker as the response-level `degraded`,
 *   repeated here for a consumer that reads only this section.
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
 * BC-2's degradation marker (`backend_app/backend/dashboard_aggregation_service.py`).
 *
 * The response-level `degraded` key is `null` when every read behind the response succeeded, and
 * this shape when the positions read failed. `positions` is `[]` in BOTH cases — the list itself
 * does not lie, it is simply empty — so this marker is the only thing that distinguishes "the
 * account holds no open positions" from "the positions read failed". A client MUST consult it
 * before rendering an empty positions state, or it publishes an outage as a fact about the
 * account (design.md §1.6, Requirement 14.5).
 *
 * `reason` is prose written for a reader and is rendered verbatim. Composing a client-side
 * sentence in its place produces a second, drifting account of the same event.
 *
 * @typedef {Object} DashboardDegraded
 * @property {'unreadable'} positions
 * @property {string} environment - The environment whose read failed.
 * @property {string} reason - Renderable prose. Show this, do not paraphrase it.
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
 * @property {Array} positions - Normalised open positions. `[]` when none are open AND when the
 *   read failed; `degraded` is what tells the two apart.
 * @property {Array} equity_curve
 * @property {DashboardDegraded|null} degraded - BC-2. `null` when every read succeeded.
 * @property {string} generated_at
 */

/**
 * ---------------------------------------------------------------------------
 * `getOverview` REMOVED — vyomquant-ui-redesign task 13.2
 * design.md §1.6, §7.1. Requirements 3.6, 14.5.
 * ---------------------------------------------------------------------------
 * `GET /api/dashboard/overview` is no longer read from the frontend, and this
 * module no longer offers a method for it. It had no call site, so nothing was
 * re-pointed.
 *
 * The endpoint used to swallow every exception and answer
 * `total_value: 0.0, today_pnl: 0.0, …`, which showed a trader with a broken read
 * a zeroed portfolio instead of an error — a direct Requirement 14.5 conflict.
 * BC-2 fixed that: it now raises 503, because every figure it returns is a
 * headline money figure and there is no partial truth left to carry. So the
 * removal is not about dishonesty any more; it is §7.1's single-dashboard-read
 * rule. A second endpoint serving a subset of the same figures is a second thing
 * to keep correct, and the subset it served is already on `GET /api/dashboard`.
 */
export const dashboardApi = {
  /**
   * Get complete dashboard data in one optimized call.
   *
   * The single dashboard read (§7.1). Raises 503 `DASHBOARD_FETCH_FAILED` rather
   * than answering zeros, and carries `degraded` for the one read that can fail
   * without taking the rest of the response down with it.
   *
   * @param {Object} params
   * @param {number} [params.equity_days=30] - Number of days for equity curve data
   * @param {string} [params.environment='live'] - Trading environment ('live' or 'paper')
   * @returns {Promise<DashboardData>}
   */
  getDashboard: (params = {}) => {
    const { equity_days = 30, environment = "live" } = params;
    return get(`/api/dashboard?equity_days=${equity_days}&environment=${environment}`);
  },

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
