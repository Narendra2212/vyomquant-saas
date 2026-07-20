/**
 * Authentication API Module
 * 
 * Endpoints: /api/auth/*
 */
import { get, post } from '../../apiClient';

/**
 * @typedef {Object} SignInRequest
 * @property {string} email
 * @property {string} password
 */

/**
 * @typedef {Object} TokenResponse
 * @property {string} access_token
 * @property {string} [user_id]
 * @property {string} [email]
 */

/**
 * @typedef {Object} UserInfo
 * @property {string} id
 * @property {string} email
 * @property {string} role
 */

export const authApi = {
  /**
   * Sign out
   * @param {Object} [body] - Optional body with token
   * @returns {Promise<{status: string}>}
   */
  signOut: (body = {}) => post('/api/auth/signout', body),

  /**
   * Get current user info
   * @returns {Promise<UserInfo>}
   */
  getMe: () => get('/api/auth/me'),

  /**
   * Sign in with Google OAuth
   * @param {Object} data
   * @param {string} data.id_token - Google ID token
   * @returns {Promise<TokenResponse>}
   */
  googleAuth: (data) => post('/api/auth/google', data),
};

// Legacy compatibility
export const authEndpoints = authApi;
