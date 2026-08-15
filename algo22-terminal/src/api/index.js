/**
 * ═══════════════════════════════════════════════════════════════════════════
 * UNIFIED API LAYER - Single Source of Truth
 * ═══════════════════════════════════════════════════════════════════════════
 * 
 * Consolidates api.js and endpoints.js into one clean architecture.
 * 
 * Usage:
 *   import { api } from './api';
 *   const data = await api.strategies.backtest(payload);
 * 
 * Or import specific modules:
 *   import { strategiesApi } from './api/modules/strategies';
 * 
 * ═══════════════════════════════════════════════════════════════════════════
 */

// Re-export HTTP client for direct access
export { get, post, put, del, publicGet, getMetrics, logout, isAuthenticated, getToken } from '../apiClient';

// Export all API modules
export { authApi } from './modules/auth';
export { exchangeApi } from './modules/exchange';
export { marketApi } from './modules/market';
export { ordersApi } from './modules/orders';
export { strategiesApi } from './modules/strategies';
export { portfolioApi } from './modules/portfolio';
export { riskApi } from './modules/risk';
export { billingApi } from './modules/billing';
export { userApi } from './modules/user';
export { referralApi } from './modules/referral';
export { dashboardApi } from './modules/dashboard';
export { healthApi } from './modules/health';
export { supportApi } from './modules/support';

// Import all modules for consolidated export
import { authApi } from './modules/auth';
import { exchangeApi } from './modules/exchange';
import { marketApi } from './modules/market';
import { ordersApi } from './modules/orders';
import { strategiesApi } from './modules/strategies';
import { portfolioApi } from './modules/portfolio';
import { riskApi } from './modules/risk';
import { billingApi } from './modules/billing';
import { userApi } from './modules/user';
import { referralApi } from './modules/referral';
import { dashboardApi } from './modules/dashboard';
import { healthApi } from './modules/health';
import { supportApi } from './modules/support';

/**
 * Consolidated API object - single entry point for all API calls
 */
export const api = {
  auth: authApi,
  exchange: exchangeApi,
  market: marketApi,
  orders: ordersApi,
  strategies: strategiesApi,
  portfolio: portfolioApi,
  risk: riskApi,
  billing: billingApi,
  user: userApi,
  referral: referralApi,
  dashboard: dashboardApi,
  health: healthApi,
  support: supportApi,
};

/**
 * Legacy compatibility export - maintains backward compatibility
 * @deprecated Use 'api' or specific module imports instead
 */
const endpoints = api;
export { endpoints };
export default api;
