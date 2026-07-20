/**
 * Notifications API Module
 * 
 * Endpoints: /api/notifications/*
 */
import { get, put } from '../../apiClient';

/**
 * @typedef {Object} NotificationChannel
 * @property {boolean} active
 * @property {string} val
 */

/**
 * @typedef {Object} NotificationSettings
 * @property {number} max_alerts_per_minute
 * @property {Object} channels
 * @property {NotificationChannel} channels.email
 * @property {NotificationChannel} channels.telegram
 * @property {NotificationChannel} channels.webhook
 * @property {NotificationChannel} channels.desktop
 * @property {Object} triggers
 * @property {boolean} triggers.trade
 * @property {boolean} triggers.stop
 * @property {boolean} triggers.margin
 * @property {boolean} triggers.kill
 * @property {boolean} triggers.daily
 * @property {boolean} triggers.bot
 * @property {boolean} triggers.login
 * @property {boolean} triggers.api
 */

export const notificationsApi = {
  /**
   * Get notification settings
   * @returns {Promise<NotificationSettings>}
   */
  getSettings: () => get('/api/notifications/settings'),

  /**
   * Update notification settings
   * @param {Partial<NotificationSettings>} settings
   * @returns {Promise<{status: string, message: string}>}
   */
  updateSettings: (settings) => put('/api/notifications/settings', settings),
};

// Legacy compatibility
export const notificationsEndpoints = notificationsApi;
