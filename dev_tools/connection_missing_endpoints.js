/**
 * ═══════════════════════════════════════════════════════════════════════════
 * MISSING API WRAPPERS — Connection Layer for Uncovered Backend Endpoints
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * This file contains wrapper functions for backend endpoints that are not
 * currently covered in api.js. These should be integrated into the main
 * api.js file after review and testing.
 *
 * EXCLUDED ENDPOINTS (Intentionally Not Implemented):
 * - Admin endpoints (/api/admin/*) - God mode functions not exposed to UI
 * - Security logs (/api/security/logs) - Security audit trail, admin-only
 *
 * ═══════════════════════════════════════════════════════════════════════════
 */

import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

export const api = axios.create({
  baseURL: API_BASE,
  headers: {
    "Content-Type": "application/json",
  },
});

// ═══════════════════════════════════════════════════════════════════════════
// TYPE DEFINITIONS (JSDoc)
// ═══════════════════════════════════════════════════════════════════════════

/**
 * @typedef {Object} SendOtpRequest
 * @property {string} email - User email address
 * @property {string} phone_number - User phone number (optional)
 * @property {string} [method] - OTP delivery method ("email", "sms", or "both")
 */

/**
 * @typedef {Object} SendOtpResponse
 * @property {string} status - "success"
 * @property {string} message - Response message
 */

/**
 * @typedef {Object} SignUpRequest
 * @property {string} email - User email address
 * @property {string} username - Display username
 * @property {string} password - User password
 * @property {string} [phoneNumber] - Phone number (optional)
 * @property {string} [emailOtp] - Email OTP code (optional)
 * @property {string} [smsOtp] - SMS OTP code (optional)
 */

/**
 * @typedef {Object} SignUpResponse
 * @property {string} status - "success"
 * @property {string} token - JWT access token
 * @property {Object} user - User information
 * @property {string} user.email - User email
 * @property {string} user.tier - Subscription tier
 */

/**
 * @typedef {Object} BacktestPayload
 * @property {string} strategy_name - Name of the strategy
 * @property {Object[]} nodes - DAG nodes
 * @property {Object[]} edges - DAG edges
 * @property {string} timeframe - Timeframe (e.g., "15m")
 * @property {number} initial_capital - Starting capital
 * @property {number} trade_size_pct - Trade size percentage
 * @property {number} ml_threshold - ML confidence threshold
 * @property {number} stop_loss_pct - Stop loss percentage
 * @property {number} take_profit_pct - Take profit percentage
 */

/**
 * @typedef {Object} DeployDagResponse
 * @property {string} status - "success" or "error"
 * @property {string} message - Deployment message
 * @property {string} bot_key - Bot identifier
 * @property {string} symbol - Trading pair symbol
 */

// ═══════════════════════════════════════════════════════════════════════════
// AUTH SIGNUP API — /api/auth/signup/*
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Auth Signup API — User registration flow with OTP verification
 */
export const authSignupApi = {
  /**
   * Send OTP codes for user registration
   * Triggers OTP delivery to email/SMS for verification
   * @param {SendOtpRequest} payload
   * @returns {Promise<SendOtpResponse>}
   */
  sendOtp: (payload) =>
    api.post('/api/auth/signup/send-otp', payload).then(r => r.data),

  /**
   * Verify OTP codes and create user account
   * Completes registration after OTP verification
   * @param {SignUpRequest} payload
   * @returns {Promise<SignUpResponse>}
   */
  verifyAndCreate: (payload) =>
    api.post('/api/auth/signup/verify-create', payload).then(r => r.data),
};

// ═══════════════════════════════════════════════════════════════════════════
// STRATEGIES DAG DEPLOYMENT — /api/strategies/deploy
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Extended Strategies API — DAG-based deployment
 * Note: This complements the existing strategiesApi which has ID-based deployment
 */
export const strategiesDagApi = {
  /**
   * Deploy a DAG-based strategy to live execution
   * Accepts full BacktestPayload with nodes, edges, and parameters
   * @param {BacktestPayload} payload - Complete DAG strategy configuration
   * @returns {Promise<DeployDagResponse>}
   */
  deployDag: (payload) =>
    api.post('/api/strategies/deploy', payload).then(r => r.data),
};

// ═══════════════════════════════════════════════════════════════════════════
// ADMIN API — /api/admin/* (Intentionally Excluded)
// ═══════════════════════════════════════════════════════════════════════════
//
// The following endpoints are intentionally NOT wrapped as they are admin/god-mode
// functions that should not be exposed to the standard user interface:
//
// - GET /health - System health check
// - GET /metrics - System metrics
// - GET /users - List all users
// - POST /users/{user_id}/status - Set user freeze status
// - POST /kill-all - Global kill switch
// - POST /reinitialize - Platform reinitialization
// - GET /fleet-status - Fleet status
//
// If an admin dashboard is needed, these should be wrapped in a separate
// adminApi.js file with proper authentication checks.
//
// ═══════════════════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════════════════
// SECURITY API — /api/security/logs (Intentionally Excluded)
// ═══════════════════════════════════════════════════════════════════════════
//
// Security logs endpoint is intentionally excluded as it contains sensitive
// audit trail information that should only be accessible via admin interfaces.
//
// ═══════════════════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════════════════
// INTEGRATION INSTRUCTIONS
// ═══════════════════════════════════════════════════════════════════════════
//
// To integrate these wrappers into the main api.js file:
//
// 1. Add the type definitions to the JSDoc section in api.js
// 2. Add authSignupApi to the authApi object or create as separate export
// 3. Add deployDag to strategiesApi as a new method
// 4. Update the default export to include the new APIs
//
// Example integration in api.js:
//
// export const authApi = {
//   // ... existing methods ...
//   sendOtp: (payload) => api.post('/api/auth/signup/send-otp', payload).then(r => r.data),
//   verifyAndCreate: (payload) => api.post('/api/auth/signup/verify-create', payload).then(r => r.data),
// };
//
// export const strategiesApi = {
//   // ... existing methods ...
//   deployDag: (payload) => api.post('/api/strategies/deploy', payload).then(r => r.data),
// };
//
// ═══════════════════════════════════════════════════════════════════════════
