/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Support API Module
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Wraps all /api/support/* endpoints.
 *
 * Backend router: backend_app/routers/support.py
 *   GET  /api/support/tickets
 *   POST /api/support/tickets
 *   GET  /api/support/tickets/{ticket_id}
 *   POST /api/support/tickets/{ticket_id}/comments
 *   PUT  /api/support/tickets/{ticket_id}
 */

import { get, post, put } from '../../apiClient';

export const supportApi = {
  /**
   * List all support tickets for the authenticated user.
   * @param {Object} params - Optional query params: { status, limit, offset }
   */
  getTickets: (params = {}) => {
    const query = new URLSearchParams();
    if (params.status) query.set('status', params.status);
    if (params.limit != null) query.set('limit', String(params.limit));
    if (params.offset != null) query.set('offset', String(params.offset));
    const qs = query.toString();
    return get(`/api/support/tickets${qs ? `?${qs}` : ''}`);
  },

  /**
   * Get a single ticket with its comments.
   * @param {string} ticketId
   */
  getTicket: (ticketId) => get(`/api/support/tickets/${ticketId}`),

  /**
   * Create a new support ticket.
   * @param {{ subject: string, description: string, category: string, priority: string }} data
   */
  createTicket: (data) => post('/api/support/tickets', data),

  /**
   * Add a comment to an existing ticket.
   * @param {string} ticketId
   * @param {{ message: string }} data
   */
  addComment: (ticketId, data) =>
    post(`/api/support/tickets/${ticketId}/comments`, data),

  /**
   * Update ticket status (e.g. close or reopen).
   * @param {string} ticketId
   * @param {string} status - One of: 'open' | 'in_progress' | 'resolved' | 'closed' | 'reopen'
   */
  updateTicket: (ticketId, status) =>
    put(`/api/support/tickets/${ticketId}`, { status }),
};
