/**
 * paperApi.test.js — the contract of `src/api/modules/paper.js`.
 *
 * The same three assertions `libraryApi.test.js` makes, plus one this module needs specifically:
 * the eight pre-existing methods (`getAccount`, `resetAccount`, `getPositions`, `getOrders`,
 * `placeOrder`, `cancelOrder`, `getTrades`, `getSummary`) are pinned to their current paths.
 * `Portfolio.jsx` and `TradeHistory.jsx` call three of them today, so the `sessions` object
 * added beside them must be additive and nothing more.
 *
 * `../../apiClient` is mocked here and nowhere near a production path.
 *
 * Requirements: 20.2, 29.2
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  assertTravelsOnlyTheSharedClient,
  collectMethodPaths,
  methodAt,
} from './apiSourceContract';

vi.mock('../../../apiClient', () => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  publicGet: vi.fn(),
  getMetrics: vi.fn(),
  logout: vi.fn(),
  clearApiCache: vi.fn(),
  isAuthenticated: vi.fn(),
  getToken: vi.fn(),
  testConnection: vi.fn(),
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
  },
}));

import * as client from '../../../apiClient';
import { paperApi } from '../paper';

const VERBS = ['get', 'post', 'put', 'del', 'patch', 'publicGet'];

/** A session id that must be percent-encoded to survive a path segment. */
const ID = 'sess/1 a';
const ENC = 'sess%2F1%20a';

/** A plain order id: `cancelOrder` predates this task and interpolates its argument raw. */
const ORDER_ID = 'ord-1';

const START_BODY = {
  listing_id: 'lst-1',
  symbol: 'BTC/USDT',
  timeframe: '1m',
  initial_capital_minor: 10000000,
  currency: 'USD',
};
const ORDER_BODY = {
  symbol: 'BTC/USDT',
  side: 'BUY',
  order_type: 'LIMIT',
  quantity: 1,
  price: 65000,
};

/**
 * One row per method: the arguments, the shared verb, and the exact argument list that verb
 * must receive.
 *
 * @type {Record<string, {args: any[], verb: string, call: any[]}>}
 */
const EXPECTED = {
  // ── The eight pre-existing methods, pinned. ────────────────────────────────────────────
  getAccount: { args: [], verb: 'get', call: ['/api/paper/account'] },
  resetAccount: {
    args: [],
    verb: 'post',
    call: ['/api/paper/account/reset', { capital: 100000.0 }],
  },
  getPositions: { args: [], verb: 'get', call: ['/api/paper/positions'] },
  getOrders: { args: ['OPEN'], verb: 'get', call: ['/api/paper/orders?status=OPEN'] },
  placeOrder: { args: [ORDER_BODY], verb: 'post', call: ['/api/paper/orders', ORDER_BODY] },
  cancelOrder: { args: [ORDER_ID], verb: 'del', call: [`/api/paper/orders/${ORDER_ID}`] },
  getTrades: { args: [], verb: 'get', call: ['/api/paper/trades?limit=50'] },
  getSummary: { args: [], verb: 'get', call: ['/api/paper/summary'] },

  // ── The fourteen Paper_Session routes. ─────────────────────────────────────────────────
  'sessions.create': { args: [START_BODY], verb: 'post', call: ['/api/paper/sessions', START_BODY] },
  'sessions.list': {
    args: [{ session_state: 'RUNNING', limit: 25 }],
    verb: 'get',
    call: ['/api/paper/sessions?session_state=RUNNING&limit=25'],
  },
  'sessions.get': { args: [ID], verb: 'get', call: [`/api/paper/sessions/${ENC}`] },

  // The four operations take no request body: the state machine decides the transition, not a
  // field the caller sends.
  'sessions.pause': { args: [ID], verb: 'post', call: [`/api/paper/sessions/${ENC}/pause`] },
  'sessions.resume': { args: [ID], verb: 'post', call: [`/api/paper/sessions/${ENC}/resume`] },
  'sessions.stop': { args: [ID], verb: 'post', call: [`/api/paper/sessions/${ENC}/stop`] },
  'sessions.reset': { args: [ID], verb: 'post', call: [`/api/paper/sessions/${ENC}/reset`] },

  'sessions.orders': { args: [ID], verb: 'get', call: [`/api/paper/sessions/${ENC}/orders`] },
  'sessions.fills': { args: [ID], verb: 'get', call: [`/api/paper/sessions/${ENC}/fills`] },
  'sessions.positions': {
    args: [ID],
    verb: 'get',
    call: [`/api/paper/sessions/${ENC}/positions`],
  },
  'sessions.trades': { args: [ID], verb: 'get', call: [`/api/paper/sessions/${ENC}/trades`] },
  'sessions.equity': { args: [ID], verb: 'get', call: [`/api/paper/sessions/${ENC}/equity`] },
  'sessions.metrics': { args: [ID], verb: 'get', call: [`/api/paper/sessions/${ENC}/metrics`] },

  // `since_sequence` is this endpoint's spelling of the socket's `last_sequence`.
  'sessions.events': {
    args: [ID, 42],
    verb: 'get',
    call: [`/api/paper/sessions/${ENC}/events?since_sequence=42`],
  },
};

beforeEach(() => {
  for (const verb of VERBS) client[verb].mockReset();
});

describe('paperApi — every method is covered', () => {
  it('exposes exactly the methods this file has expectations for', () => {
    // Add a method to paper.js without adding a row to EXPECTED and this fails, naming it.
    expect(collectMethodPaths(paperApi)).toEqual(Object.keys(EXPECTED).sort());
  });

  it('keeps the eight pre-existing methods on the object beside sessions', () => {
    for (const name of [
      'getAccount',
      'resetAccount',
      'getPositions',
      'getOrders',
      'placeOrder',
      'cancelOrder',
      'getTrades',
      'getSummary',
    ]) {
      expect(paperApi[name], `${name} must remain — existing pages call it`).toBeTypeOf(
        'function',
      );
    }
    expect(Object.keys(paperApi.sessions)).toHaveLength(14);
  });
});

describe('paperApi — each method calls the shared client with the expected path', () => {
  for (const [name, { args, verb, call }] of Object.entries(EXPECTED)) {
    it(`${name} → ${verb}(${JSON.stringify(call[0])})`, () => {
      methodAt(paperApi, name)(...args);

      expect(client[verb]).toHaveBeenCalledTimes(1);
      expect(client[verb]).toHaveBeenCalledWith(...call);

      const total = VERBS.reduce((sum, v) => sum + client[v].mock.calls.length, 0);
      expect(total, `${name} must make exactly one shared-client call`).toBe(1);
    });
  }
});

describe('paperApi — default and omitted arguments', () => {
  it('omits the orders status filter entirely when none is given', () => {
    paperApi.getOrders();
    expect(client.get).toHaveBeenCalledWith('/api/paper/orders');
  });

  it('defaults resetAccount to 100000 and getTrades to limit=50', () => {
    paperApi.resetAccount();
    expect(client.post).toHaveBeenCalledWith('/api/paper/account/reset', { capital: 100000.0 });

    paperApi.getTrades();
    expect(client.get).toHaveBeenCalledWith('/api/paper/trades?limit=50');
  });

  it('sends no blank session_state — an empty label is a 422, not a wildcard', () => {
    paperApi.sessions.list();
    expect(client.get).toHaveBeenCalledWith('/api/paper/sessions');

    client.get.mockReset();
    paperApi.sessions.list({ session_state: '' });
    expect(client.get).toHaveBeenCalledWith('/api/paper/sessions');

    client.get.mockReset();
    paperApi.sessions.list({ limit: 5 });
    expect(client.get).toHaveBeenCalledWith('/api/paper/sessions?limit=5');
  });

  it('replays the whole retained log when no since_sequence is given', () => {
    paperApi.sessions.events(ID);
    expect(client.get).toHaveBeenCalledWith(
      `/api/paper/sessions/${ENC}/events?since_sequence=0`,
    );
  });
});

describe('paperApi — no transport of its own', () => {
  it('builds no client, base URL or host of its own', () => {
    assertTravelsOnlyTheSharedClient('paper.js');
  });
});

describe('api.paper.sessions is reachable from src/api/index.js', () => {
  it('registers the same paperApi object, alongside api.library', async () => {
    const { api } = await import('../../index');
    expect(api.paper).toBe(paperApi);
    expect(api.paper.sessions).toBe(paperApi.sessions);
    expect(api.library).toBeTypeOf('object');
  });
});
