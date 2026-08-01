/**
 * Referral API Module
 * 
 * Complete referral system API client
 * Handles referral codes, commissions, and payouts
 */
import { get, post } from '../../apiClient';

/**
 * @typedef {Object} ReferralProfile
 * @property {string} referral_code
 * @property {string} referral_link
 * @property {number} total_referrals
 * @property {number} active_referrals
 * @property {number} pending_earnings
 * @property {number} approved_earnings
 * @property {number} paid_earnings
 * @property {number} lifetime_earnings
 */

/**
 * @typedef {Object} ReferralStats
 * @property {string} referral_code
 * @property {string} referral_link
 * @property {number} total_referrals
 * @property {number} active_referrals
 * @property {number} pending_earnings
 * @property {number} approved_earnings
 * @property {number} paid_earnings
 * @property {number} lifetime_earnings
 * @property {Array} commission_history
 * @property {Array} payout_history
 */

/**
 * @typedef {Object} CommissionRecord
 * @property {string} id
 * @property {string} referred_user_id
 * @property {string} payment_id
 * @property {string} subscription_tier
 * @property {number} payment_amount_usd
 * @property {number} commission_amount_usd
 * @property {string} status
 * @property {string} created_at
 * @property {string|null} paid_at
 * @property {string|null} reversal_reason
 */

/**
 * @typedef {Object} PayoutRecord
 * @property {string} id
 * @property {number} amount_usd
 * @property {string} status
 * @property {string|null} payment_method
 * @property {string} created_at
 * @property {string|null} approved_at
 * @property {string|null} paid_at
 * @property {string|null} rejection_reason
 */

export const referralApi = {
  /**
   * Get referral profile information
   * @returns {Promise<ReferralProfile>}
   */
  getProfile: () => get('/api/referral/profile'),

  /**
   * Get comprehensive referral statistics
   * @returns {Promise<ReferralStats>}
   */
  getStats: () => get('/api/referral/stats'),

  /**
   * Get commission history
   * @param {Object} params
   * @param {number} [params.limit=50]
   * @param {number} [params.offset=0]
   * @param {string} [params.status]
   * @returns {Promise<{commissions: CommissionRecord[], count: number, limit: number, offset: number}>}
   */
  getCommissions: (params = {}) => get('/api/referral/commissions', { params }),

  /**
   * Get payout history
   * @param {Object} params
   * @param {number} [params.limit=50]
   * @param {number} [params.offset=0]
   * @param {string} [params.status]
   * @returns {Promise<{payouts: PayoutRecord[], count: number, limit: number, offset: number}>}
   */
  getPayouts: (params = {}) => get('/api/referral/payouts', { params }),

  /**
   * Validate a referral code (for signup)
   * @param {string} referralCode
   * @returns {Promise<{valid: boolean, referrer_id: string|null, message: string}>}
   */
  validateCode: (referralCode) => post('/api/referral/validate', { referral_code: referralCode }),

  /**
   * Create a payout request
   * @param {Object} payoutData
   * @param {number} payoutData.amount_usd
   * @param {string} payoutData.payment_method
   * @param {Object} payoutData.payment_details
   * @returns {Promise<{status: string, payout_id: string, amount_usd: number, status: string}>}
   */
  createPayout: (payoutData) => post('/api/referral/payouts', payoutData),
};

// Legacy compatibility - keep old endpoint name
export const getReferralStats = () => referralApi.getStats();
