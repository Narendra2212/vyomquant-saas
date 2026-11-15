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
 * @property {"razorpay"} provider - always "razorpay"; it is the only gateway
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
   * Create a checkout session. Razorpay is the only gateway.
   *
   * Backend generates the session server-side — no price data trusted from frontend.
   *
   * This used to read "Stripe for USD, Razorpay for INR". There is no second gateway: the
   * only decision left is the CHARGE CURRENCY, and INR is the only currency the platform can
   * charge. A request in any other supported currency is quoted in that currency for display
   * and charged in INR, which is why the response carries `checkout_currency` alongside
   * `currency` and reports `is_direct_checkout: false` for those buyers. Render the charge,
   * do not imply the displayed figure is what the card is debited.
   *
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

  /*
   * NO `openPortal`. REMOVED BY BILLING-PORTAL-02 (2026-11-15).
   *
   * It wrapped `POST /api/billing/portal`, and its own docblock here opened with "THIS ALWAYS
   * REJECTS. THERE IS NO PORTAL, AND THERE IS NO LONGER A CASE WHERE THERE IS." Razorpay is
   * the only gateway and publishes no hosted self-serve billing portal, so the server answers
   * `501 PROVIDER_HAS_NO_BILLING_PORTAL` (or `503 PAYMENT_PROVIDER_NOT_CONFIGURED`), never a
   * URL. Stripe's `billing_portal.Session` branch went with Stripe, so the handler has had no
   * success path at all.
   *
   * The reason it was kept — that `Billing.jsx` rendered the server's structured
   * `detail.message` verbatim, which is better than the bare 404 a deleted route would give —
   * argued for good copy on a refusal, not for keeping the buttons. Both call sites are gone
   * (*Manage billing* in the current-plan panel, *Update payment method* in the payment-failed
   * alert), so this wrapper has no callers and a client method whose only contract is to
   * reject is a trap for the next one.
   *
   * THE BACKEND ROUTE IS DELIBERATELY UNTOUCHED. `routers/billing.py:2995` still serves it and
   * still answers 501. This is the removal of a dead frontend control, not of the payment
   * provider integration: `createCheckout`, `setCurrency`, `cancelSubscription`,
   * `resumeSubscription` and webhook processing all stay exactly as they were.
   *
   * The sentence the refusal used to carry now lives where it is actually useful — in the
   * payment-failed alert in `Billing.jsx`, stated before the user presses anything, telling
   * them the instrument is collected by Razorpay Checkout at purchase, is never stored here,
   * and is changed by paying again with a different instrument.
   */

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
