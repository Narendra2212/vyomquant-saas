/**
 * ═══════════════════════════════════════════════════════════════════════════
 * TYPE-SAFE API TYPES - OpenAPI Generated
 * ═══════════════════════════════════════════════════════════════════════════
 * 
 * Auto-generated from backend Pydantic models.
 * Provides request/response validation and type-safe API usage.
 * 
 * Usage:
 *   import { api } from './api';
 *   import type { BacktestRequest, BacktestResponse } from './types/api.types';
 *   
 *   const result: BacktestResponse = await api.strategies.backtest(payload);
 */

// ═══════════════════════════════════════════════════════════════════════════
// BASE TYPES
// ═══════════════════════════════════════════════════════════════════════════

export type OrderSide = 'buy' | 'sell';
export type OrderType = 'market' | 'limit' | 'stop' | 'take_profit';
export type NodeType = 'indicator' | 'ml' | 'logic' | 'action' | 'input';
export type LogicOperator = 'AND' | 'OR' | 'NOT' | 'GT' | 'LT' | 'EQ' | 'GTE' | 'LTE';
export type TicketStatus = 'open' | 'in_progress' | 'resolved' | 'closed';
export type TicketPriority = 'low' | 'medium' | 'high' | 'urgent';
export type TicketCategory = 'general' | 'technical' | 'billing' | 'security' | 'feature';

// ═══════════════════════════════════════════════════════════════════════════
// AUTH ENDPOINTS
// ═══════════════════════════════════════════════════════════════════════════

export interface SignInRequest {
  email: string;
  password: string;
}

export interface SignUpRequest {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: 'bearer';
  user?: {
    id: string;
    email: string;
    created_at?: string;
  } | null;
}

export interface UserProfile {
  id: string;
  email: string;
  created_at: string;
  updated_at?: string;
  subscription_tier?: string;
  is_active: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════
// EXCHANGE ENDPOINTS
// ═══════════════════════════════════════════════════════════════════════════

export interface ExchangeKeysRequest {
  exchange_id: string;
  api_key: string;
  secret_key: string;
  password?: string | null;
}

export interface TestConnectionRequest {
  exchange_id: string;
}

export interface ExchangeResponse {
  exchange_id: string;
  masked_key: string;
  permissions: string[];
  status: string;
  volume_usd?: number | null;
}

// ═══════════════════════════════════════════════════════════════════════════
// ORDER ENDPOINTS
// ═══════════════════════════════════════════════════════════════════════════

export interface ExecuteOrderRequest {
  symbol: string;
  order_type: OrderType;
  side: OrderSide;
  amount: number; // > 0
  price?: number | null;
  params?: Record<string, unknown>; // CCXT params
}

export interface StopLossRequest {
  symbol: string;
  side: OrderSide;
  amount: number; // > 0
  stop_price: number; // > 0
}

export interface TakeProfitRequest {
  symbol: string;
  side: OrderSide;
  amount: number; // > 0
  take_profit_price: number; // > 0
}

export interface OrderResponse {
  id: string;
  symbol: string;
  side: OrderSide;
  amount: number;
  price?: number;
  status: 'pending' | 'filled' | 'partial' | 'canceled' | 'rejected';
  filled_amount?: number;
  remaining_amount?: number;
  fee?: number;
  timestamp: string;
}

export interface OrderHistoryResponse {
  orders: OrderResponse[];
  count: number;
  total?: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// MARKET DATA ENDPOINTS
// ═══════════════════════════════════════════════════════════════════════════

export interface Candle {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Ticker {
  symbol: string;
  last: number;
  bid: number;
  ask: number;
  change: number;
  change_pct: number;
  volume: number;
  high: number;
  low: number;
}

export interface OrderBookEntry {
  price: number;
  size: number;
}

export interface OrderBook {
  symbol: string;
  bids: OrderBookEntry[];
  asks: OrderBookEntry[];
  timestamp: number;
}

export interface FundingRate {
  symbol: string;
  rate: number;
  timestamp: string;
  next_funding_time?: string;
}

// ═══════════════════════════════════════════════════════════════════════════
// STRATEGY BUILDER - DAG TYPES
// ═══════════════════════════════════════════════════════════════════════════

export interface DAGNode {
  id: string;
  type: NodeType;
  label?: string | null;
  
  // Indicator specific
  indicator?: string | null; // rsi, macd, sma, etc.
  params?: Record<string, unknown>; // period, fast, slow, etc.
  
  // ML specific
  model_id?: string | null;
  confidence_threshold?: number; // 0.0-1.0, default 0.7
  
  // Logic specific
  operator?: LogicOperator | null;
  
  // Action specific
  action?: 'buy' | 'sell' | 'hold' | null;
  order_type?: string | null; // default: market
  amount?: number | null;
  
  // Input specific
  symbol?: string | null;
  timeframe?: string | null;
}

export interface DAGEdge {
  id: string;
  source: string;
  target: string;
  label?: string | null;
  condition?: string | null;
}

export interface DAGConfig {
  nodes: DAGNode[];
  edges: DAGEdge[];
  strategy_name: string;
  symbols: string[];
  timeframe: string;
}

export interface BacktestRequest {
  // PRIMARY: DAG configuration
  dag?: DAGConfig | null;
  
  // BACKWARD COMPATIBILITY: Legacy strategies
  strategies?: string[] | null;
  strategy_id?: string | null;
  
  // Trading parameters
  symbols?: string[];
  timeframe?: string;
  
  // Backtest parameters (decimals, not percentages)
  initial_capital?: number; // default: 10000
  trade_size_pct?: number; // 0.1 = 10%, default: 0.1
  stop_loss_pct?: number; // 0.02 = 2%, default: 0.02
  take_profit_pct?: number; // 0.04 = 4%, default: 0.04
  ml_threshold?: number; // 0.0-1.0, default: 0.0
  
  // Extended parameters
  params?: Record<string, unknown>;
}

export interface EquityPoint {
  time: number;
  value: number;
}

export interface DAGNodeResult {
  samples: number[];
  mean: number;
  std: number;
}

export interface DAGResults {
  nodes_count: number;
  edges_count: number;
  execution_order: string[];
  action_nodes: string[];
  node_results: Record<string, DAGNodeResult>;
}

export interface BacktestResponse {
  // Core metrics
  total_return_pct: number;
  final_equity: number;
  total_trades: number;
  win_rate_pct: number;
  total_pnl: number;
  max_drawdown_pct: number;
  total_fees: number;
  symbols_traded: number;
  
  // Extended metrics
  profit_factor: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  
  // Equity curve
  equity: EquityPoint[];
  
  // DAG results (if DAG mode used)
  dag_results?: DAGResults | null;
  execution_mode: 'dag' | 'legacy';
  
  // Metadata
  timeframe: string;
  initial_capital: number;
  
  // Error (if failed)
  error?: string;
  traceback?: string;
  error_type?: string;
}

export interface Strategy {
  id: string;
  name: string;
  description?: string;
  symbol: string;
  timeframe: string;
  created_at: string;
  updated_at?: string;
  is_active: boolean;
  config?: DAGConfig;
}

export interface StrategyListResponse {
  strategies: Strategy[];
  count: number;
}

export interface DeployRequest {
  exchange_id: string;
}

export interface DeployResponse {
  status: 'deployed' | 'failed';
  strategy_id: string;
  exchange_id: string;
  message: string;
}

// ═══════════════════════════════════════════════════════════════════════════
// PORTFOLIO ENDPOINTS
// ═══════════════════════════════════════════════════════════════════════════

export interface PortfolioSummary {
  total_value: number;
  available_balance: number;
  unrealized_pnl: number;
  realized_pnl: number;
  total_positions: number;
  margin_used: number;
  margin_ratio: number;
}

export interface Position {
  symbol: string;
  side: 'long' | 'short';
  size: number;
  entry_price: number;
  mark_price: number;
  unrealized_pnl: number;
  margin: number;
  leverage: number;
  liquidation_price?: number;
}

export interface PortfolioAllocation {
  symbol: string;
  allocation_pct: number;
  value: number;
}

export interface HeatmapPoint {
  date: string;
  pnl_usd: number;
}

export interface Transaction {
  timestamp: string;
  symbol: string;
  side: OrderSide;
  amount: number;
  price: number;
  pnl?: number;
  fee?: number;
  order_type: string;
  type: 'trade' | 'deposit' | 'withdrawal';
}

export interface RecentTransactionsResponse {
  transactions: Transaction[];
  count: number;
  period_days: number;
  generated_at: string;
}

export interface CloseAllPositionsRequest {
  symbol?: string | null;
  exchange_id: string;
}

export interface ClosedPosition {
  symbol: string;
  contracts_closed: number;
  realized_pnl: number;
  order_id: string;
  error?: string;
}

export interface CloseAllResponse {
  status: 'completed' | 'partial' | 'failed';
  closed_count: number;
  failed_count: number;
  total_realized_pnl: number;
  positions: ClosedPosition[];
  timestamp: string;
}

// ═══════════════════════════════════════════════════════════════════════════
// RISK ENDPOINTS
// ═══════════════════════════════════════════════════════════════════════════

export interface RiskSettings {
  max_daily_loss: number;
  max_positions: number;
  max_leverage: number;
  kill_switches: {
    loss?: boolean;
    blackswan?: boolean;
    streak?: boolean;
    [key: string]: boolean | undefined;
  };
}

export interface RiskSettingsRequest {
  max_daily_loss?: number;
  max_positions?: number;
  max_leverage?: number;
  kill_switches?: Record<string, boolean>;
}

export interface MarginHealth {
  margin_ratio: number;
  free_margin: number;
  risk_score: number;
}

export interface AccountHealth {
  current_drawdown_pct: number;
  daily_pnl_pct: number;
  total_exposure: number;
}

export interface StrategyLimit {
  strategy_id: string;
  max_position_size: number;
  max_daily_trades: number;
  allowed_symbols: string[];
  max_drawdown_pct: number;
  enabled: boolean;
}

export interface StrategyLimitsRequest {
  limits: StrategyLimit[];
}

export interface StrategyLimitsResponse {
  limits: StrategyLimit[];
  count: number;
  user_id: string;
}

export interface KillSwitchRequest {
  scope: 'user' | 'global';
  confirm_code: string;
}

export interface KillSwitchResponse {
  status: 'activated' | 'deactivated';
  scope: string;
  timestamp: string;
}

// ═══════════════════════════════════════════════════════════════════════════
// BILLING ENDPOINTS
// ═══════════════════════════════════════════════════════════════════════════

export interface BillingPlan {
  tier: string;
  name: string;
  price_monthly: number;
  price_yearly: number;
  features: string[];
  max_strategies: number;
  max_exchanges: number;
  supports_ml: boolean;
}

export interface Invoice {
  id: string;
  amount: number;
  currency: string;
  status: 'pending' | 'paid' | 'failed' | 'refunded';
  description: string;
  created_at: string;
  paid_at?: string;
}

export interface PaymentMethod {
  id: string;
  type: 'card' | 'bank_transfer' | 'crypto';
  last4?: string;
  brand?: string;
  expiry_month?: number;
  expiry_year?: number;
  is_default: boolean;
  created_at?: string;
}

export interface PaymentMethodsResponse {
  methods: PaymentMethod[];
  count: number;
  default_method?: PaymentMethod | null;
}

export interface AddPaymentMethodRequest {
  payment_method_id: string;
  set_as_default?: boolean;
}

export interface CheckoutRequest {
  plan_tier: string;
  billing_cycle: 'monthly' | 'yearly';
  payment_method_id?: string;
}

export interface CheckoutResponse {
  checkout_url?: string;
  session_id?: string;
  status: 'created' | 'completed' | 'failed';
}

// ═══════════════════════════════════════════════════════════════════════════
// SUPPORT ENDPOINTS
// ═══════════════════════════════════════════════════════════════════════════

export interface CreateTicketRequest {
  subject: string; // min 5, max 200 chars
  description: string; // min 20, max 5000 chars
  category: TicketCategory;
  priority?: TicketPriority;
}

export interface TicketComment {
  id: string;
  message: string;
  is_staff: boolean;
  created_at: string;
}

export interface Ticket {
  id: string;
  subject: string;
  description: string;
  category: TicketCategory;
  priority: TicketPriority;
  status: TicketStatus;
  created_at: string;
  updated_at?: string;
  resolved_at?: string;
  has_unread?: boolean;
  comment_count?: number;
  comments?: TicketComment[];
}

export interface TicketsResponse {
  tickets: Ticket[];
  count: number;
  total: number;
  offset: number;
  limit: number;
}

export interface AddCommentRequest {
  ticket_id: string;
  message: string; // min 1, max 2000 chars
}

// ═══════════════════════════════════════════════════════════════════════════
// LEADERBOARD / ANALYTICS
// ═══════════════════════════════════════════════════════════════════════════

export interface LeaderboardEntry {
  rank: number;
  user_id: string;
  username?: string;
  total_return_pct: number;
  win_rate_pct: number;
  total_trades: number;
  sharpe_ratio: number;
}

export interface LeaderboardResponse {
  entries: LeaderboardEntry[];
  period: 'daily' | 'weekly' | 'monthly' | 'all_time';
  generated_at: string;
}

// ═══════════════════════════════════════════════════════════════════════════
// NOTIFICATIONS
// ═══════════════════════════════════════════════════════════════════════════

export interface NotificationSettings {
  email_enabled: boolean;
  push_enabled: boolean;
  trade_alerts: boolean;
  price_alerts: boolean;
  security_alerts: boolean;
  marketing_emails: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════
// API ERROR TYPES
// ═══════════════════════════════════════════════════════════════════════════

export type ApiErrorCategory = 
  | 'SERVER_ERROR' 
  | 'AUTH_ERROR' 
  | 'CLIENT_ERROR' 
  | 'NETWORK_ERROR' 
  | 'UNKNOWN_ERROR';

export interface ApiError {
  name: 'ApiError';
  message: string;
  category: ApiErrorCategory;
  status?: number;
  statusText?: string;
  url?: string;
  method?: string;
  requestId?: string;
  timestamp: string;
  data?: unknown;
  userMessage: string;
  retryable: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════
// REQUEST/RESPONSE WRAPPERS
// ═══════════════════════════════════════════════════════════════════════════

export interface ApiResponse<T> {
  data: T;
  status: number;
  headers?: Record<string, string>;
}

export interface PaginatedRequest {
  limit?: number;
  offset?: number;
}

export interface PaginatedResponse<T> {
  data: T[];
  count: number;
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

// ═══════════════════════════════════════════════════════════════════════════
// TYPE GUARDS (Runtime Validation)
// ═══════════════════════════════════════════════════════════════════════════

export function isValidOrderSide(value: unknown): value is OrderSide {
  return typeof value === 'string' && ['buy', 'sell'].includes(value);
}

export function isValidOrderType(value: unknown): value is OrderType {
  return typeof value === 'string' && ['market', 'limit', 'stop', 'take_profit'].includes(value);
}

export function isValidNodeType(value: unknown): value is NodeType {
  return typeof value === 'string' && ['indicator', 'ml', 'logic', 'action', 'input'].includes(value);
}

export function isValidDAGNode(node: unknown): node is DAGNode {
  if (!node || typeof node !== 'object') return false;
  const n = node as DAGNode;
  return (
    typeof n.id === 'string' &&
    isValidNodeType(n.type) &&
    (n.confidence_threshold === undefined || 
     (typeof n.confidence_threshold === 'number' && n.confidence_threshold >= 0 && n.confidence_threshold <= 1))
  );
}

export function isValidDAGEdge(edge: unknown): edge is DAGEdge {
  if (!edge || typeof edge !== 'object') return false;
  const e = edge as DAGEdge;
  return (
    typeof e.id === 'string' &&
    typeof e.source === 'string' &&
    typeof e.target === 'string'
  );
}

export function isValidBacktestRequest(req: unknown): req is BacktestRequest {
  if (!req || typeof req !== 'object') return false;
  const r = req as BacktestRequest;
  
  // Must have either DAG or strategies
  const hasDag = r.dag !== undefined && r.dag !== null && 
                 Array.isArray(r.dag.nodes) && r.dag.nodes.length > 0;
  const hasStrategies = Array.isArray(r.strategies) && r.strategies.length > 0;
  
  if (!hasDag && !hasStrategies) return false;
  
  // Validate percentages are decimals (0.1 not 10)
  if (r.trade_size_pct !== undefined && (r.trade_size_pct <= 0 || r.trade_size_pct > 1)) return false;
  if (r.stop_loss_pct !== undefined && (r.stop_loss_pct < 0 || r.stop_loss_pct > 1)) return false;
  if (r.take_profit_pct !== undefined && (r.take_profit_pct < 0 || r.take_profit_pct > 1)) return false;
  
  return true;
}

export function isValidExecuteOrderRequest(req: unknown): req is ExecuteOrderRequest {
  if (!req || typeof req !== 'object') return false;
  const r = req as ExecuteOrderRequest;
  return (
    typeof r.symbol === 'string' &&
    isValidOrderSide(r.side) &&
    isValidOrderType(r.order_type) &&
    typeof r.amount === 'number' &&
    r.amount > 0
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// VALIDATION HELPERS
// ═══════════════════════════════════════════════════════════════════════════

export class ValidationError extends Error {
  constructor(
    message: string,
    public field?: string,
    public value?: unknown
  ) {
    super(message);
    this.name = 'ValidationError';
  }
}

export function validateBacktestRequest(req: BacktestRequest): void {
  if (!isValidBacktestRequest(req)) {
    throw new ValidationError(
      'Invalid backtest request. Must provide either dag.nodes or strategies.',
      'dag|strategies'
    );
  }
  
  if (req.dag) {
    // Validate DAG nodes
    for (let i = 0; i < req.dag.nodes.length; i++) {
      const node = req.dag.nodes[i];
      if (!isValidDAGNode(node)) {
        throw new ValidationError(
          `Invalid DAG node at index ${i}`,
          `dag.nodes[${i}]`,
          node
        );
      }
    }
    
    // Validate DAG edges
    for (let i = 0; i < req.dag.edges.length; i++) {
      const edge = req.dag.edges[i];
      if (!isValidDAGEdge(edge)) {
        throw new ValidationError(
          `Invalid DAG edge at index ${i}`,
          `dag.edges[${i}]`,
          edge
        );
      }
      
      // Validate edge references exist
      const sourceExists = req.dag.nodes.some(n => n.id === edge.source);
      const targetExists = req.dag.nodes.some(n => n.id === edge.target);
      
      if (!sourceExists) {
        throw new ValidationError(
          `Edge references non-existent source node: ${edge.source}`,
          `dag.edges[${i}].source`
        );
      }
      if (!targetExists) {
        throw new ValidationError(
          `Edge references non-existent target node: ${edge.target}`,
          `dag.edges[${i}].target`
        );
      }
    }
  }
}

export function validateExecuteOrderRequest(req: ExecuteOrderRequest): void {
  if (!isValidExecuteOrderRequest(req)) {
    throw new ValidationError(
      'Invalid order request. Required: symbol, side, order_type, amount>0',
      'order'
    );
  }
  
  // Limit orders require price
  if (req.order_type === 'limit') {
    if (req.price === undefined || req.price === null || req.price <= 0) {
      throw new ValidationError(
        'Limit orders require a valid price',
        'price',
        req.price
      );
    }
  }
}

export function validateCreateTicketRequest(req: CreateTicketRequest): void {
  if (!req || typeof req !== 'object') {
    throw new ValidationError('Request must be an object');
  }
  
  if (typeof req.subject !== 'string' || req.subject.length < 5 || req.subject.length > 200) {
    throw new ValidationError(
      'Subject must be 5-200 characters',
      'subject',
      req.subject
    );
  }
  
  if (typeof req.description !== 'string' || req.description.length < 20 || req.description.length > 5000) {
    throw new ValidationError(
      'Description must be 20-5000 characters',
      'description',
      req.description
    );
  }
  
  const validCategories: TicketCategory[] = ['general', 'technical', 'billing', 'security', 'feature'];
  if (!validCategories.includes(req.category)) {
    throw new ValidationError(
      `Category must be one of: ${validCategories.join(', ')}`,
      'category',
      req.category
    );
  }
}
