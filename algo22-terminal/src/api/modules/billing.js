/**
 * Billing API Module
 *
 * Endpoints: /api/billing/*
 * Server-authoritative: all plan, pricing, subscription state comes from backend.
 * The frontend NEVER decides subscription state or plan entitlements.
 */
import { get, post, del } from '../../apiClient';

/**
 * @typedef {Object} BillingEntitlements
 * @property {string} plan
 * @property {string[]} features
 * @property {Object} quotas
 * @property {Object} usage
 * @property {string} subscription_status - active|cancelled|past_due|trial|expired
 * @property {string|null} renewal_date
 * @property {boolean} cancel_at_period_end
 */

/**
 * @typedef {Object} Invoice
 * @property {string} id
 * @property {string} date
 * @property {number} amtUSD
 * @property {number} amtINR
 * @property {string} status
 * @property {string} currency
 */

/**
 * @typedef {Object} PaymentMethod
 * @property {string} id
 * @property {string} brand
 * @property {string} last4
 * @property {number} expiry_month
 * @property {number} expiry_year
 * @property {boolean} is_default
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
   * Get all available plans with server-authoritative FX localized pricing
   * @param {string} [currency] - Optional currency override code (e.g. "USD", "INR", "EUR", "GBP", "JPY")
   * @returns {Promise<{plans: Array, country: string, currency: string, currency_symbol: string, fx_rate: number, checkout_currency: string}>}
   */
  getPlans: (currency) => get(currency ? `/api/billing/plans?currency=${encodeURIComponent(currency)}` : '/api/billing/plans'),

  /**
   * Get user's current entitlements (plan, features, quotas, usage, subscription status)
   * @returns {Promise<BillingEntitlements>}
   */
  getEntitlements: () => get('/api/billing/entitlements'),

  /**
   * Get invoice/payment history
   * @returns {Promise<Invoice[]>}
   */
  getInvoices: () => get('/api/billing/invoices'),

  /**
   * Get saved payment methods
   * @returns {Promise<PaymentMethod[]>}
   */
  getPaymentMethods: () => get('/api/billing/payment-methods'),

  /**
   * Get user's complete currency and country context
   * @returns {Promise<{country: string, country_name: string, currency: string, currency_symbol: string, currency_source: string, checkout_currency: string, supported_currencies: Array}>}
   */
  getCurrency: () => get('/api/billing/currency'),

  /**
   * Set user's manual currency preference
   * @param {string} currency - e.g. "USD", "INR", "EUR", "GBP", "JPY"
   */
  setCurrency: (currency) => post('/api/billing/currency', { currency }),

  /**
   * Create a checkout session (Stripe for USD, Razorpay for INR)
   * Backend generates the session server-side — no price data trusted from frontend.
   * @param {CheckoutRequest} request
   * @returns {Promise<CheckoutResponse>}
   */
  createCheckout: (request) => post('/api/billing/checkout', request),

  /**
   * Cancel subscription at period end.
   * Server-authoritative: sets cancel_at_period_end=true on profiles.
   * @returns {Promise<{status: string, detail: string, cancel_at_period_end: boolean}>}
   */
  cancelSubscription: () => post('/api/billing/cancel', {}),

  /**
   * Resume (reverse) a pending cancellation.
   * Server-authoritative: clears cancel_at_period_end, restores active status.
   * @returns {Promise<{status: string, detail: string, cancel_at_period_end: boolean}>}
   */
  resumeSubscription: () => post('/api/billing/resume', {}),

  /**
   * Open Stripe Billing Portal for payment method management.
   * Returns a redirect URL to Stripe's hosted portal.
   * @returns {Promise<{url: string}>}
   */
  openPortal: () => post('/api/billing/portal', {}),

  /**
   * Delete a payment method by ID
   * @param {string} methodId
   */
  deletePaymentMethod: (methodId) => del(`/api/billing/payment-methods/${methodId}`),
};

// Legacy compatibility
export const billingEndpoints = billingApi;
