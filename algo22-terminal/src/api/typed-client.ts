/**
 * ═══════════════════════════════════════════════════════════════════════════
 * TYPE-SAFE API CLIENT
 * ═══════════════════════════════════════════════════════════════════════════
 * 
 * Fully typed API client with request/response validation.
 * All endpoints typed with proper request/response schemas.
 * 
 * Usage:
 *   import { api } from './typed-client';
 *   import type { BacktestRequest, BacktestResponse } from '../types/api.types';
 *   
 *   const request: BacktestRequest = {
 *     dag: { nodes: [...], edges: [...], ... }
 *   };
 *   
 *   const response: BacktestResponse = await api.strategies.backtest(request);
 */

import { 
  get, post, put, del, patch, ApiError 
} from '../apiClient';
import type {
  // Auth
  SignInRequest,
  SignUpRequest,
  TokenResponse,
  UserProfile,
  // Common
  EquityPoint,
  // Exchange
  ExchangeKeysRequest,
  TestConnectionRequest,
  ExchangeResponse,
  // Orders
  ExecuteOrderRequest,
  StopLossRequest,
  TakeProfitRequest,
  OrderResponse,
  OrderHistoryResponse,
  // Market
  Candle,
  Ticker,
  OrderBook,
  FundingRate,
  // Strategies
  DAGConfig,
  BacktestRequest,
  BacktestResponse,
  Strategy,
  // Notifications
  NotificationSettings,
  Notification,
  NotificationListResponse,
  StrategyListResponse,
  DeployRequest,
  DeployResponse,
  // Portfolio
  PortfolioSummary,
  Position,
  PortfolioAllocation,
  HeatmapPoint,
  RecentTransactionsResponse,
  CloseAllPositionsRequest,
  CloseAllResponse,
  // Risk
  RiskSettings,
  RiskSettingsRequest,
  MarginHealth,
  AccountHealth,
  StrategyLimit,
  StrategyLimitsRequest,
  StrategyLimitsResponse,
  KillSwitchRequest,
  KillSwitchResponse,
  // Billing
  BillingPlan,
  Invoice,
  PaymentMethod,
  PaymentMethodsResponse,
  AddPaymentMethodRequest,
  CheckoutRequest,
  CheckoutResponse,
  // Support
  CreateTicketRequest,
  Ticket,
  TicketsResponse,
  AddCommentRequest,
} from '../types/api.types';

import {
  validateBacktestRequest,
  validateExecuteOrderRequest,
  validateCreateTicketRequest,
  ValidationError,
} from '../types/api.types';

// ═══════════════════════════════════════════════════════════════════════════
// TYPE-SAFE WRAPPER
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Generic type-safe API call wrapper with validation
 */
async function apiCall<TRequest, TResponse>(
  method: 'get' | 'post' | 'put' | 'delete' | 'patch',
  url: string,
  data?: TRequest,
  validator?: (req: TRequest) => void
): Promise<TResponse> {
  // Validate request if validator provided
  if (validator && data !== undefined && data !== null) {
    try {
      validator(data);
    } catch (error) {
      if (error instanceof ValidationError) {
        throw new ApiError(
          `Validation failed: ${error.message}`,
          { url, method: method.toUpperCase(), status: 400, statusText: 'Validation Error', data: error }
        );
      }
      throw error;
    }
  }
  
  // Make API call
  let response;
  switch (method) {
    case 'get':
      response = await get(url);
      break;
    case 'post':
      response = await post(url, data as Object | undefined);
      break;
    case 'put':
      response = await put(url, data as Object | undefined);
      break;
    case 'delete':
      response = await del(url);
      break;
    case 'patch':
      response = await patch(url, data as Object | undefined);
      break;
    default:
      throw new Error(`Unknown method: ${method}`);
  }
  
  return response as TResponse;
}

// ═══════════════════════════════════════════════════════════════════════════
// AUTH API
// ═══════════════════════════════════════════════════════════════════════════

export const auth = {
  signIn: (data: SignInRequest): Promise<TokenResponse> =>
    apiCall<SignInRequest, TokenResponse>('post', '/api/auth/signin', data),
  
  signUp: (data: SignUpRequest): Promise<TokenResponse> =>
    apiCall<SignUpRequest, TokenResponse>('post', '/api/auth/signup', data),
  
  signOut: (): Promise<{ status: string }> =>
    apiCall<void, { status: string }>('post', '/api/auth/signout'),
  
  getMe: (): Promise<UserProfile> =>
    apiCall<void, UserProfile>('get', '/api/auth/me'),
  
  refreshToken: (): Promise<TokenResponse> =>
    apiCall<void, TokenResponse>('post', '/api/auth/refresh'),
};

// ═══════════════════════════════════════════════════════════════════════════
// EXCHANGE API
// ═══════════════════════════════════════════════════════════════════════════

export const exchange = {
  getSupported: (): Promise<string[]> =>
    apiCall<void, string[]>('get', '/api/exchanges/supported'),
  
  list: (): Promise<ExchangeResponse[]> =>
    apiCall<void, ExchangeResponse[]>('get', '/api/exchanges'),
  
  saveKeys: (data: ExchangeKeysRequest): Promise<ExchangeResponse> =>
    apiCall<ExchangeKeysRequest, ExchangeResponse>('post', '/api/exchanges/keys', data),
  
  testConnection: (data: TestConnectionRequest): Promise<{ status: string; message: string }> =>
    apiCall<TestConnectionRequest, { status: string; message: string }>('post', '/api/exchanges/test', data),
  
  delete: (exchangeId: string): Promise<{ status: string; deleted: string }> =>
    apiCall<void, { status: string; deleted: string }>('delete', `/api/exchanges/${exchangeId}`),
};

// ═══════════════════════════════════════════════════════════════════════════
// MARKET DATA API
// ═══════════════════════════════════════════════════════════════════════════

export const market = {
  getCandles: (symbol: string, timeframe: string, limit: number = 100): Promise<Candle[]> =>
    apiCall<void, Candle[]>('get', `/api/market/candles?symbol=${symbol}&timeframe=${timeframe}&limit=${limit}`),
  
  getTicker: (symbol: string): Promise<Ticker> =>
    apiCall<void, Ticker>('get', `/api/market/ticker?symbol=${symbol}`),
  
  getOrderBook: (symbol: string, depth: number = 10): Promise<OrderBook> =>
    apiCall<void, OrderBook>('get', `/api/market/orderbook?symbol=${symbol}&depth=${depth}`),
  
  getFundingRate: (symbol: string): Promise<FundingRate> =>
    apiCall<void, FundingRate>('get', `/api/market/funding-rate?symbol=${symbol}`),
  
  getSymbols: (): Promise<string[]> =>
    apiCall<void, string[]>('get', '/api/market/symbols'),
};

// ═══════════════════════════════════════════════════════════════════════════
// ORDERS API
// ═══════════════════════════════════════════════════════════════════════════

export const orders = {
  /**
   * 🔴 MANUAL EXECUTION BLOCKED - PURE ALGO TRADING ONLY
   * All execution must flow through: Strategy → DAG → Signal → BotRunner → Exchange
   */
  execute: (): Promise<never> =>
    Promise.reject(new Error('MANUAL_EXECUTION_BLOCKED: Use api.strategies.deploy() instead.')),
  
  /**
   * 🔴 MANUAL EXECUTION BLOCKED - PURE ALGO TRADING ONLY
   */
  create: (): Promise<never> =>
    Promise.reject(new Error('MANUAL_EXECUTION_BLOCKED: Use api.strategies.deploy() instead.')),
  
  getHistory: (limit?: number): Promise<OrderHistoryResponse> =>
    apiCall<void, OrderHistoryResponse>('get', `/api/orders/history${limit ? `?limit=${limit}` : ''}`),
  
  getOpen: (): Promise<OrderResponse[]> =>
    apiCall<void, OrderResponse[]>('get', '/api/orders/open'),
  
  cancel: (orderId: string): Promise<{ status: string; canceled: string }> =>
    apiCall<void, { status: string; canceled: string }>('delete', `/api/orders/${orderId}`),
  
  cancelAll: (): Promise<{ status: string; canceled_count: number }> =>
    apiCall<void, { status: string; canceled_count: number }>('post', '/api/orders/cancel-all'),
  
  stopLoss: (data: StopLossRequest): Promise<OrderResponse> =>
    apiCall<StopLossRequest, OrderResponse>('post', '/api/orders/stop-loss', data),
  
  takeProfit: (data: TakeProfitRequest): Promise<OrderResponse> =>
    apiCall<TakeProfitRequest, OrderResponse>('post', '/api/orders/take-profit', data),
};

// ═══════════════════════════════════════════════════════════════════════════
// STRATEGIES API
// ═══════════════════════════════════════════════════════════════════════════

export const strategies = {
  list: (): Promise<StrategyListResponse> =>
    apiCall<void, StrategyListResponse>('get', '/api/strategies'),
  
  getById: (id: string): Promise<Strategy> =>
    apiCall<void, Strategy>('get', `/api/strategies/${id}`),
  
  create: (data: Partial<Strategy>): Promise<Strategy> =>
    apiCall<Partial<Strategy>, Strategy>('post', '/api/strategies', data),
  
  update: (id: string, data: Partial<Strategy>): Promise<Strategy> =>
    apiCall<Partial<Strategy>, Strategy>('put', `/api/strategies/${id}`, data),
  
  delete: (id: string): Promise<{ status: string; deleted: string }> =>
    apiCall<void, { status: string; deleted: string }>('delete', `/api/strategies/${id}`),
  
  backtest: (data: BacktestRequest): Promise<BacktestResponse> =>
    apiCall<BacktestRequest, BacktestResponse>('post', '/api/strategies/backtest', data, validateBacktestRequest),
  
  deploy: (id: string, data: DeployRequest): Promise<DeployResponse> =>
    apiCall<DeployRequest, DeployResponse>('post', `/api/strategies/${id}/deploy`, data),
  
  start: (id: string): Promise<{ status: string; message: string }> =>
    apiCall<void, { status: string; message: string }>('post', `/api/strategies/${id}/start`),
  
  stop: (id: string): Promise<{ status: string; message: string }> =>
    apiCall<void, { status: string; message: string }>('post', `/api/strategies/${id}/stop`),
  
  pause: (id: string): Promise<{ status: string; message: string }> =>
    apiCall<void, { status: string; message: string }>('post', `/api/strategies/${id}/pause`),
  
  trainMl: (id: string, data: { indicators: string[]; days?: number }): Promise<{ status: string; message: string }> =>
    apiCall<{ indicators: string[]; days?: number }, { status: string; message: string }>('post', `/api/strategies/${id}/train`, data),
};

// ═══════════════════════════════════════════════════════════════════════════
// PORTFOLIO API
// ═══════════════════════════════════════════════════════════════════════════

export const portfolio = {
  getSummary: (): Promise<PortfolioSummary> =>
    apiCall<void, PortfolioSummary>('get', '/api/portfolio/summary'),
  
  getEquityCurve: (days?: number): Promise<EquityPoint[]> =>
    apiCall<void, EquityPoint[]>('get', `/api/portfolio/equity-curve${days ? `?days=${days}` : ''}`),
  
  getAllocation: (): Promise<PortfolioAllocation[]> =>
    apiCall<void, PortfolioAllocation[]>('get', '/api/portfolio/allocation'),
  
  getHeatmap: (months?: number): Promise<HeatmapPoint[]> =>
    apiCall<void, HeatmapPoint[]>('get', `/api/portfolio/heatmap${months ? `?months=${months}` : ''}`),
  
  getRecentTransactions: (limit?: number, days?: number): Promise<RecentTransactionsResponse> =>
    apiCall<void, RecentTransactionsResponse>('get', 
      `/api/portfolio/recent-transactions?${limit ? `limit=${limit}&` : ''}${days ? `days=${days}` : ''}`),
  
  getPositions: (): Promise<Position[]> =>
    apiCall<void, Position[]>('get', '/api/portfolio/positions'),
  
  closeAllPositions: (data: CloseAllPositionsRequest): Promise<CloseAllResponse> =>
    apiCall<CloseAllPositionsRequest, CloseAllResponse>('post', '/api/portfolio/close-all', data),
};

// ═══════════════════════════════════════════════════════════════════════════
// RISK API
// ═══════════════════════════════════════════════════════════════════════════

export const risk = {
  getConfig: (): Promise<RiskSettings> =>
    apiCall<void, RiskSettings>('get', '/api/risk/settings'),
  
  updateConfig: (data: RiskSettingsRequest): Promise<RiskSettings> =>
    apiCall<RiskSettingsRequest, RiskSettings>('put', '/api/risk/settings', data),
  
  getStrategyLimits: (): Promise<StrategyLimitsResponse> =>
    apiCall<void, StrategyLimitsResponse>('get', '/api/risk/strategy-limits'),
  
  updateStrategyLimit: (data: StrategyLimitsRequest): Promise<StrategyLimitsResponse> =>
    apiCall<StrategyLimitsRequest, StrategyLimitsResponse>('put', '/api/risk/strategy-limits', data),
  
  deleteStrategyLimit: (strategyId: string): Promise<{ status: string; deleted: string }> =>
    apiCall<void, { status: string; deleted: string }>('delete', `/api/risk/strategy-limits/${strategyId}`),
  
  getMarginHealth: (): Promise<MarginHealth> =>
    apiCall<void, MarginHealth>('get', '/api/risk/margin-health'),
  
  getAccountHealth: (): Promise<AccountHealth> =>
    apiCall<void, AccountHealth>('get', '/api/risk/account-health'),
  
  killSwitch: (data: KillSwitchRequest): Promise<KillSwitchResponse> =>
    apiCall<KillSwitchRequest, KillSwitchResponse>('post', '/api/risk/kill-switch', data),
};

// ═══════════════════════════════════════════════════════════════════════════
// BILLING API
// ═══════════════════════════════════════════════════════════════════════════

export const billing = {
  getPlan: (): Promise<BillingPlan> =>
    apiCall<void, BillingPlan>('get', '/api/billing/plan'),
  
  getInvoices: (limit?: number): Promise<Invoice[]> =>
    apiCall<void, Invoice[]>('get', `/api/billing/invoices${limit ? `?limit=${limit}` : ''}`),
  
  getPaymentMethods: (): Promise<PaymentMethodsResponse> =>
    apiCall<void, PaymentMethodsResponse>('get', '/api/billing/payment-methods'),
  
  addPaymentMethod: (data: AddPaymentMethodRequest): Promise<{ status: string; method_id: string }> =>
    apiCall<AddPaymentMethodRequest, { status: string; method_id: string }>('post', '/api/billing/payment-methods', data),
  
  deletePaymentMethod: (methodId: string): Promise<{ status: string; deleted: string }> =>
    apiCall<void, { status: string; deleted: string }>('delete', `/api/billing/payment-methods/${methodId}`),
  
  createCheckout: (data: CheckoutRequest): Promise<CheckoutResponse> =>
    apiCall<CheckoutRequest, CheckoutResponse>('post', '/api/billing/checkout', data),
};

// ═══════════════════════════════════════════════════════════════════════════
// USER API
// ═══════════════════════════════════════════════════════════════════════════

export const user = {
  getProfile: (): Promise<UserProfile> =>
    apiCall<void, UserProfile>('get', '/api/user/profile'),
  
  updateProfile: (data: Partial<UserProfile>): Promise<UserProfile> =>
    apiCall<Partial<UserProfile>, UserProfile>('put', '/api/user/profile', data),
  
  getStats: (): Promise<{ total_trades: number; win_rate: number; total_pnl: number }> =>
    apiCall<void, { total_trades: number; win_rate: number; total_pnl: number }>('get', '/api/user/stats'),
  
  getReferralStats: (): Promise<{ code: string; referrals: number; earnings: number }> =>
    apiCall<void, { code: string; referrals: number; earnings: number }>('get', '/api/user/referrals'),
  
  getSecurityLogs: (): Promise<{ logins: any[]; api_calls: number; failed_attempts: number }> =>
    apiCall<void, { logins: any[]; api_calls: number; failed_attempts: number }>('get', '/api/user/security-logs'),
};

// ═══════════════════════════════════════════════════════════════════════════
// SUPPORT API
// ═══════════════════════════════════════════════════════════════════════════

export const support = {
  getTickets: (status?: string, limit?: number, offset?: number): Promise<TicketsResponse> =>
    apiCall<void, TicketsResponse>('get', 
      `/api/support/tickets?${status ? `status=${status}&` : ''}${limit ? `limit=${limit}&` : ''}${offset ? `offset=${offset}` : ''}`),
  
  getTicket: (ticketId: string): Promise<Ticket> =>
    apiCall<void, Ticket>('get', `/api/support/tickets/${ticketId}`),
  
  createTicket: (data: CreateTicketRequest): Promise<{ status: string; ticket_id: string }> =>
    apiCall<CreateTicketRequest, { status: string; ticket_id: string }>('post', '/api/support/tickets', data, validateCreateTicketRequest),
  
  addComment: (ticketId: string, data: { message: string }): Promise<{ status: string; comment_id: string }> =>
    apiCall<{ message: string }, { status: string; comment_id: string }>('post', `/api/support/tickets/${ticketId}/comments`, data),
  
  updateTicket: (ticketId: string, status: 'closed' | 'reopen'): Promise<{ status: string; new_status: string }> =>
    apiCall<void, { status: string; new_status: string }>('put', `/api/support/tickets/${ticketId}?status=${status}`),
};

// ═══════════════════════════════════════════════════════════════════════════
// NOTIFICATIONS API
// ═══════════════════════════════════════════════════════════════════════════

export const notifications = {
  // Settings (legacy - moved to user profile)
  getSettings: (): Promise<NotificationSettings> =>
    apiCall<void, NotificationSettings>('get', '/api/notifications/settings'),
  
  updateSettings: (data: Partial<NotificationSettings>): Promise<NotificationSettings> =>
    apiCall<Partial<NotificationSettings>, NotificationSettings>('put', '/api/notifications/settings', data),
  
  // Notification CRUD
  list: (params?: { limit?: number; offset?: number; unread_only?: boolean; category?: string }): Promise<NotificationListResponse> => {
    const queryParams = new URLSearchParams();
    if (params?.limit) queryParams.append('limit', params.limit.toString());
    if (params?.offset) queryParams.append('offset', params.offset.toString());
    if (params?.unread_only !== undefined) queryParams.append('unread_only', params.unread_only.toString());
    if (params?.category) queryParams.append('category', params.category);
    const url = `/api/notifications${queryParams.toString() ? `?${queryParams.toString()}` : ''}`;
    return apiCall<void, NotificationListResponse>('get', url);
  },
  
  markRead: (id: string): Promise<{ status: string }> =>
    apiCall<void, { status: string }>('put', `/api/notifications/${id}/read`),
  
  markAllRead: (): Promise<{ status: string }> =>
    apiCall<void, { status: string }>('put', '/api/notifications/read-all'),
  
  delete: (id: string): Promise<{ status: string }> =>
    apiCall<void, { status: string }>('delete', `/api/notifications/${id}`),
  
  deleteAll: (): Promise<{ status: string }> =>
    apiCall<void, { status: string }>('delete', '/api/notifications'),
};

// ═══════════════════════════════════════════════════════════════════════════
// EXPORT ALL
// ═══════════════════════════════════════════════════════════════════════════

export const api = {
  auth,
  exchange,
  market,
  orders,
  strategies,
  portfolio,
  risk,
  billing,
  user,
  support,
  notifications,
};

export default api;

// Re-export types for convenience
export * from '../types/api.types';
