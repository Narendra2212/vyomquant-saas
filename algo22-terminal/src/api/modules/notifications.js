/**
 * ═══════════════════════════════════════════════════════════════════════════
 * NOTIFICATIONS API MODULE
 * ═══════════════════════════════════════════════════════════════════════════
 * Authoritative client methods for user notifications and settings.
 */

import { get, put, del } from '../../apiClient';

export const notificationsApi = {
  /**
   * List notifications with pagination and filters
   * @param {Object} [params] - Query params { limit, offset, unread_only, category }
   */
  list: (params = {}) => {
    const query = new URLSearchParams();
    if (params.limit !== undefined) query.append('limit', params.limit);
    if (params.offset !== undefined) query.append('offset', params.offset);
    if (params.unread_only !== undefined) query.append('unread_only', params.unread_only);
    if (params.category && params.category !== 'all') query.append('category', params.category);
    const qs = query.toString();
    return get(`/api/notifications${qs ? `?${qs}` : ''}`);
  },

  /**
   * Get unread notification count
   */
  getUnreadCount: () => get('/api/notifications/unread-count'),

  /**
   * Mark a single notification as read
   * @param {string} id - Notification ID
   */
  markRead: (id) => put(`/api/notifications/${id}/read`),

  /**
   * Mark all notifications as read
   */
  markAllRead: () => put('/api/notifications/read-all'),

  /**
   * Delete a single notification
   * @param {string} id - Notification ID
   */
  delete: (id) => del(`/api/notifications/${id}`),

  /**
   * Delete all notifications
   */
  deleteAll: () => del('/api/notifications'),

  /**
   * Get notification preferences
   */
  getSettings: () => get('/api/notifications/settings'),

  /**
   * Update notification preferences
   * @param {Object} data
   */
  updateSettings: (data) => put('/api/notifications/settings', data),
};
