/**
 * Support API Module
 * 
 * Endpoints: /api/support/*
 */
import { get, post } from '../../apiClient';

/**
 * @typedef {Object} TicketCreateRequest
 * @property {string} subject
 * @property {string} message
 * @property {string} [priority] - "Low", "Medium", "High", "Critical"
 */

/**
 * @typedef {Object} TicketResponse
 * @property {string} id
 * @property {string} subject
 * @property {string} priority
 * @property {string} status
 * @property {string} created_at
 */

export const supportApi = {
  /**
   * Create a support ticket
   * @param {TicketCreateRequest} ticket
   * @returns {Promise<TicketResponse>}
   */
  createTicket: (ticket) => post('/api/support/tickets', ticket),

  /**
   * Get user's support tickets
   * @returns {Promise<TicketResponse[]>}
   */
  getTickets: () => get('/api/support/tickets'),
};

// Legacy compatibility
export const supportEndpoints = supportApi;
