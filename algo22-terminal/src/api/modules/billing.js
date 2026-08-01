/**
 * Billing API Module
 * 
 * Endpoints: /api/billing/*
 */
import { get, post } from '../../apiClient';

/**
 * @typedef {Object} BillingPlan
 * @property {string} id
 * @property {string} name
 * @property {number} priceUSD
 * @property {number} priceINR
 * @property {string[]} features
 * @property {string} nextBillingDate
 * @property {boolean} autoRenew
 */

/**
 * @typedef {Object} Invoice
 * @property {string} id
 * @property {string} date
 * @property {number} amtUSD
 * @property {number} amtINR
 * @property {string} status
 */

/**
 * @typedef {Object} PaymentMethod
 * @property {string} id
 * @property {string} brand
 * @property {string} last4
 * @property {string} expiry
 * @property {boolean} isDefault
 */

/**
 * @typedef {Object} CheckoutRequest
 * @property {string} tier
 * @property {string} currency - "USD" or "INR"
 * @property {boolean} [is_addon]
 */

/**
 * @typedef {Object} CheckoutResponse
 * @property {string} checkoutUrl
 * @property {string} provider - "stripe" or "razorpay"
 * @property {string} [order_id]
 */

export const billingApi = {
  /**
   * Get all available plans
   * @returns {Promise<{plans: Array}>}
   */
  getPlans: () => get('/api/billing/plans'),

  /**
   * Get user's current entitlements (plan, features, quotas, usage)
   * @returns {Promise<{plan: string, features: string[], quotas: object, usage: object}>}
   */
  getEntitlements: () => get('/api/billing/entitlements'),

  /**
   * Get current billing plan (legacy)
   * @returns {Promise<BillingPlan>}
   * @deprecated Use getEntitlements instead
   */
  getInvoices: () => get('/api/billing/invoices'),
  getPaymentMethods: () => get('/api/billing/payment-methods'),
  getCurrency: () => get('/api/billing/currency'),
  setCurrency: (currency) => post('/api/billing/currency', { currency }),
  /**
   * Create checkout session
   * @param {CheckoutRequest} request
   * @returns {Promise<CheckoutResponse>}
   */
  createCheckout: (request) => post('/api/billing/checkout', request),
};

// Legacy compatibility
export const billingEndpoints = billingApi;
