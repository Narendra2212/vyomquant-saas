/**
 * Billing API Module
 *
 * Endpoints: /api/billing/*
 * Server-authoritative: all plan, pricing, subscription state comes from backend.
 * The frontend NEVER decides subscription state or plan entitlements.
 */
import { get, post, del, publicGet } from '../../apiClient';

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
 * @property {string|null} checkoutUrl - Razorpay hosted link, or null when only the modal is available
 * @property {string} provider - "stripe" or "razorpay"
 * @property {string} [order_id] - Razorpay order id, for Standard Checkout
 * @property {string} [key_id] - Razorpay PUBLISHABLE key id. Never the key secret.
 * @property {string} [razorpay_key] - Same value as key_id, the spelling the marketplace endpoint uses
 * @property {number} [amount] - Minor units (paise for INR)
 * @property {string} [currency]
 */

/**
 * @typedef {Object} VerificationResult
 * @property {boolean} verified - True only when the server recomputed a matching signature
 * @property {string} order_id
 * @property {string} payment_id
 * @property {boolean} entitlement_pending - Always true: the plan activates on the gateway webhook, not here
 * @property {string} detail - Human-readable sentence safe to show the trader
 */

export const billingApi = {
  /**
   * Get all available plans with server-authoritative FX localized pricing
   * @param {string} [currency] - Optional currency override code (e.g. "USD", "INR", "EUR", "GBP", "JPY")
   * @returns {Promise<{plans: Array, country: string, currency: string, currency_symbol: string, fx_rate: number, checkout_currency: string}>}
   */
  getPlans: (currency) => get(currency ? `/api/billing/plans?currency=${encodeURIComponent(currency)}` : '/api/billing/plans'),

  /**
   * The same catalogue, read WITHOUT a session.
   *
   * `GET /api/billing/plans` is public — no auth dependency, no rate limit — because the pricing
   * page has to price itself for a visitor who has no account yet. This variant exists so that
   * surface does not go through the authenticated transport: `get` attaches whatever token is in
   * `sessionStorage` and routes a 401 into the shell's `auth:expired` handler, which for an
   * anonymous visitor on a marketing page would be a sign-out prompt triggered by reading a price
   * list.
   *
   * Server-resolved currency, exactly as the authenticated read: the response's `currency` is
   * decided from the caller's IP (or the explicit override), and `plans[].localized_price` is
   * already in it. The caller renders; it does not convert.
   *
   * @param {string} [currency] Optional display-currency override, e.g. "EUR".
   * @returns {Promise<{plans: Array, currency: string, currency_symbol: string, base_currency: string, supported_currencies: Array}>}
   */
  getPublicPlans: (currency) =>
    publicGet('/api/billing/plans', currency ? { currency } : {}),

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
   * Ask the server to confirm a Razorpay Standard Checkout callback is authentic.
   *
   * The server recomputes HMAC-SHA256 over `order_id|payment_id` with the key SECRET, which
   * never reaches this bundle. A rejected signature is a 400 and nothing is recorded as paid.
   *
   * A `verified: true` answer does NOT mean the plan is active — `entitlement_pending` is
   * always true. The gateway's `payment.captured` webhook is what moves the subscription, so
   * the UI should say "activating shortly" and refetch entitlements rather than assuming the
   * new tier. Anything that reads this as "access granted" is reading it wrong.
   *
   * @param {{razorpay_order_id: string, razorpay_payment_id: string, razorpay_signature: string}} payload
   * @returns {Promise<VerificationResult>}
   */
  verifyPayment: (payload) => post('/api/billing/verify-payment', payload),

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
   * Ask the server for a hosted provider billing-portal session.
   *
   * PROVIDER-DEPENDENT, AND ON RAZORPAY THERE IS NO PORTAL AT ALL. This used to be documented
   * as "Open Stripe Billing Portal… Returns a redirect URL to Stripe's hosted portal", which
   * described a provider this platform does not bill through: Razorpay is the configured one
   * and it publishes no hosted self-serve portal. The server answers accordingly —
   * `501 PROVIDER_HAS_NO_BILLING_PORTAL` when the live provider has no portal, and
   * `503 PAYMENT_PROVIDER_NOT_CONFIGURED` when no provider holds live credentials. Neither
   * returns a URL, because there is none to return. A `{ url }` comes back only from a
   * genuinely Stripe-configured deployment.
   *
   * So a caller must treat a rejection as the NORMAL answer and render the server's own
   * `detail.message`, not assume a URL and not present this as a working control. No caller
   * may synthesise a portal URL of its own.
   *
   * @returns {Promise<{url: string}>} on a Stripe deployment only; rejects otherwise
   */
  openPortal: () => post('/api/billing/portal', {}),

  /**
   * Delete a stored payment method by ID.
   *
   * VyomQuant stores no payment instrument (Razorpay collects it inside Checkout), so this
   * answers `404 PAYMENT_METHOD_NOT_FOUND` for any id on a Razorpay deployment. There is no
   * `addPaymentMethod` counterpart here because the server has no honest one:
   * `POST /api/billing/payment-methods` answers `501 PAYMENT_METHOD_STORAGE_UNSUPPORTED`.
   *
   * @param {string} methodId
   */
  deletePaymentMethod: (methodId) => del(`/api/billing/payment-methods/${methodId}`),
};

// Legacy compatibility
export const billingEndpoints = billingApi;
