/**
 * tests/unit/helpers/wsTicketStub.js — production-launch-hardening task 8.2.
 *
 * WHAT THIS IS
 * ------------
 * The double for `POST /api/auth/ws-ticket`, the one HTTPS request
 * `websocketClient.connect()` now makes before it opens a socket. Every suite that opens a
 * socket for a session that has a JWT needs it: without a ticket there is no credential to
 * present, and the client — correctly — does not fall back to putting the JWT in the URL.
 *
 * WHY IT IS SHARED RATHER THAN INLINED
 * -----------------------------------
 * Seven suites stub `WebSocket`. The `RecordingWebSocket` double is inlined in each of them
 * because each was written before the others existed; this one is shared from the start
 * because there is exactly one request being doubled and exactly one contract for it
 * (`backend_app/routers/auth.py:277-332` — `{"ticket": "<43-char opaque>", "ttl_seconds":
 * 30}`, or 503 `WS_TICKET_STORE_UNAVAILABLE`). A per-suite copy would be seven places to
 * change when that contract moves.
 *
 * THE TICKETS IT ISSUES
 * --------------------
 * Opaque, distinct per request, and deliberately **not** JWT-shaped — no dots, so the
 * `jwtShapedSubstring` check in `socketCredential.test.js` cannot be satisfied by the
 * stub's own output. Distinctness is what lets a test assert the thing single-use tickets
 * make load-bearing: that every reconnect asked for a new one.
 *
 * No real credential is in this file. The `Authorization` header it records is recorded by
 * presence and prefix, never by value.
 */

import { afterEach, beforeEach, vi } from 'vitest';

/** The endpoint, as `websocketClient` builds it. Matched by suffix, not by origin. */
export const WS_TICKET_PATH = '/api/auth/ws-ticket';

/**
 * Install the ticket endpoint for every test in the calling file.
 *
 * Call it once at module scope, above the suite's own `beforeEach`, so the endpoint is in
 * place before anything mounts.
 *
 * @returns {{
 *   issued: string[],
 *   requests: Array<{method: string, authorized: boolean}>,
 *   serve: () => void,
 *   fail: (status?: number) => void,
 * }} `issued` is every ticket handed out, in order.
 */
export const useWsTicketStub = () => {
  const handle = {
    issued: [],
    requests: [],
    serve() {
      status = 200;
    },
    /** Answer with an HTTP failure instead — 503 is the store-unavailable case. */
    fail(nextStatus = 503) {
      status = nextStatus;
    },
  };

  let status = 200;
  let minted = 0;
  let previousFetch;

  beforeEach(() => {
    handle.issued.length = 0;
    handle.requests.length = 0;
    status = 200;
    minted = 0;
    previousFetch = globalThis.fetch;

    const stub = vi.fn(async (input, init = {}) => {
      const url = String(input);
      if (!url.endsWith(WS_TICKET_PATH)) {
        // Loud rather than silent: a suite that reaches the network for anything else has
        // a boundary this helper is not the double for.
        throw new Error(`wsTicketStub: unexpected fetch to ${url}`);
      }

      const headers = init.headers || {};
      handle.requests.push({
        method: init.method || 'GET',
        authorized: typeof headers.Authorization === 'string'
          && headers.Authorization.startsWith('Bearer '),
      });

      if (status !== 200) {
        return {
          ok: false,
          status,
          json: async () => ({
            detail: { error: 'WS_TICKET_STORE_UNAVAILABLE' },
          }),
        };
      }

      minted += 1;
      // 43 characters of urlsafe alphabet, the length `secrets.token_urlsafe(32)` yields,
      // with the sequence number in it so a test can see which request produced it.
      const ticket = `tkt${String(minted).padStart(3, '0')}${'ABCDEFGHJKLMNPQRSTUVWXYZ23456789abcdefgh'.slice(0, 40)}`;
      handle.issued.push(ticket);
      return {
        ok: true,
        status: 200,
        json: async () => ({ ticket, ttl_seconds: 30 }),
      };
    });

    globalThis.fetch = stub;
    if (typeof window !== 'undefined') window.fetch = stub;
  });

  afterEach(() => {
    globalThis.fetch = previousFetch;
    if (typeof window !== 'undefined') window.fetch = previousFetch;
  });

  return handle;
};

export default useWsTicketStub;
