/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Support API Module
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Wraps all /api/support/* endpoints.
 *
 * Backend router: backend_app/routers/support.py
 *   GET  /api/support/faqs
 *   GET  /api/support/categories
 *   GET  /api/support/tickets
 *   POST /api/support/tickets
 *   GET  /api/support/tickets/{ticket_id}
 *   POST /api/support/tickets/{ticket_id}/comments
 *   PUT  /api/support/tickets/{ticket_id}
 *   GET  /api/support/admin/tickets
 *   POST /api/support/admin/tickets/{ticket_id}/reply
 */

import { get, post, put } from '../../apiClient';

export const supportApi = {
  /**
   * Get categorized FAQs with search.
   * @param {{ category?: string, search?: string }} params
   */
  getFaqs: (params = {}) => {
    const query = new URLSearchParams();
    if (params.category) query.set('category', params.category);
    if (params.search) query.set('search', params.search);
    const qs = query.toString();
    return get(`/api/support/faqs${qs ? `?${qs}` : ''}`);
  },

  /**
   * Get supported ticket categories and priorities.
   */
  getCategories: () => get('/api/support/categories'),

  /**
   * List all support tickets for the authenticated user.
   * @param {Object} params - Optional query params: { status, category, priority, search, limit, offset }
   */
  getTickets: (params = {}) => {
    const query = new URLSearchParams();
    if (params.status && params.status !== 'all') query.set('status', params.status);
    if (params.category && params.category !== 'all') query.set('category', params.category);
    if (params.priority && params.priority !== 'all') query.set('priority', params.priority);
    if (params.search) query.set('search', params.search);
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
   * @param {{ subject: string, description: string, category: string, priority?: string, related_feature?: string, strategy_id?: string, order_id?: string, attachment?: object }} data
   */
  createTicket: (data) => post('/api/support/tickets', data),

  /**
   * Add a comment to an existing ticket.
   * @param {string} ticketId
   * @param {{ message: string, attachment?: object }} data
   */
  addComment: (ticketId, data) =>
    post(`/api/support/tickets/${ticketId}/comments`, data),

  /**
   * Update ticket status (e.g. close or reopen).
   * @param {string} ticketId
   * @param {string} status - One of: 'open' | 'in_progress' | 'waiting_for_user' | 'waiting_for_support' | 'resolved' | 'closed' | 'reopen'
   */
  updateTicket: (ticketId, status) =>
    put(`/api/support/tickets/${ticketId}`, { status }),

  /**
   * Staff / Admin: List all tickets across tenants.
   */
  getAdminTickets: (params = {}) => {
    const query = new URLSearchParams();
    if (params.status) query.set('status', params.status);
    if (params.category) query.set('category', params.category);
    if (params.priority) query.set('priority', params.priority);
    if (params.search) query.set('search', params.search);
    const qs = query.toString();
    return get(`/api/support/admin/tickets${qs ? `?${qs}` : ''}`);
  },

  /**
   * Staff / Admin: Reply to user ticket.
   */
  staffReply: (ticketId, data) =>
    post(`/api/support/admin/tickets/${ticketId}/reply`, data),
};
