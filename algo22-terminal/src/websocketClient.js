/**
 * ═══════════════════════════════════════════════════════════════════════════
 * CENTRALIZED WEBSOCKET CLIENT
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Provides a centralized WebSocket client with built-in:
 * - Automatic reconnection
 * - Authentication token injection
 * - Event subscription management
 * - Type-safe message handling
 * - Connection status tracking
 *
 * ═══════════════════════════════════════════════════════════════════════════
 */

import { CONFIG } from './config';
const WS_BASE = CONFIG.wsBaseUrl;

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * THE SOCKET CREDENTIAL — production-launch-hardening task 8.2
 * Requirements 1.21, 2.21, 3.9.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * The session JWT is no longer put in the socket URL. It is exchanged, over HTTPS where
 * request headers exist, for a **single-use opaque ticket** with a ≤ 30 s lifetime
 * (`POST /api/auth/ws-ticket`, `backend_app/routers/auth.py`), and that ticket is what
 * travels in the query string as `?ticket=`.
 *
 * WHY THE URL AT ALL. The browser `WebSocket` constructor cannot set request headers —
 * there is no `Authorization` on a handshake it builds — so a credential in the query
 * string is the only credential a socket can present. The question is therefore not
 * *whether* something is logged but *what*: a JWT in a CloudFront or ALB access log is
 * replayable for as long as it is valid, a redeemed ticket is replayable for nothing.
 * `verify_ws_ticket` reads it with `getdel`, so the first connection to present a ticket
 * is the only connection that can.
 *
 * WHICH MEANS EVERY RECONNECT NEEDS ITS OWN TICKET. Single-use is not a detail that can
 * be worked around by caching: a reconnect that re-presented the ticket of the connection
 * it is replacing would be refused, and the socket would never come back. `_open` below
 * mints a new one on every attempt, including every attempt the backoff schedules, and
 * `this.connectPath` — not `this.url` — is what the backoff reads back, so a spent ticket
 * cannot be resurrected out of the previous URL.
 *
 * WHAT IS *NOT* CHANGED HERE. The in-band `{ action: 'auth', token }` frame in
 * `handleOpen` still carries the JWT. A WebSocket message body is not access-logged, not
 * in browser history and not in a `Referer`, so it is not the exposure 1.21 names, and the
 * server still reauthenticates a live connection from it (Requirement 23.3). The store the
 * JWT is read from is not changed either — that is task 8.3.
 */
const WS_TICKET_URL = `${CONFIG.apiBaseUrl}/api/auth/ws-ticket`;

/** The session JWT, or `''`. Storage access is guarded: a blocked store is not a crash. */
const readSessionToken = () => {
  try {
    return sessionStorage.getItem('token') || '';
  } catch (error) {
    console.error('WebSocket: session token unreadable:', error);
    return '';
  }
};

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ┏━━━ WS_TICKET_FALLBACK ━━━ TEMPORARY. DELETE THIS BLOCK. ━━━━━━━━━━━━━━━┓
 * production-launch-hardening task 8.2c — DEPLOY-ORDERING COMPATIBILITY SHIM
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * WHY THIS EXISTS. It exists for exactly one release: the release in which this frontend
 * can reach a backend that predates `verify_ws_ticket`. Two workflows ship the two halves
 * of task 8.2 at different speeds:
 *
 *   `.github/workflows/06-frontend-deploy.yml`  push to `main`, `algo22-terminal/**`
 *                                               → an S3 sync. Seconds.
 *   `.github/workflows/03-deploy.yml`           after `02 Build` completes
 *                                               → an ECS rollout. Minutes.
 *
 * So on merge there is a window in which the new bundle is live and the old task definition
 * is still serving sockets. `POST /api/auth/ws-ticket` is NOT the thing that breaks in that
 * window — `issue_ws_ticket` is already on `main`, so the mint succeeds and the client gets
 * a real ticket. What is missing is the redemption half: `verify_ws_ticket` is not there,
 * the routes read only `token`, and a handshake presenting `?ticket=` finds no credential.
 * Every socket for every user closes 4001 until the rollout lands.
 *
 * WHAT IT DOES. On the first socket that presents a ticket and is refused, it switches this
 * session's credential to the legacy `?token=` — the same one-release grace that task 8.2a
 * gave the backend by keeping the `token` query parameter accepting. The switch happens
 * once, sticks for the session, and is never switched back: `ticket → token → ticket` would
 * pay a doomed ticket round-trip on every reconnect for the whole window.
 *
 * WHAT IT COSTS, STATED PLAINLY. The fallback URL carries the session JWT, which is the
 * exposure Requirements 1.21 / 2.21 exist to remove — a JWT in a CloudFront or ALB access
 * log is replayable while it is valid. That is the trade: one release of the old exposure
 * for one session, against every socket in the product being dead for the length of an ECS
 * rollout. It is bounded by being at most one switch per session, by never firing on a
 * connection that reached `open`, and by this block being deleted.
 *
 * WHEN IT IS DELETED. Once ECS is confirmed running the task definition that contains
 * `verify_ws_ticket`, this block, the `credentialMode` state in the constructor, the
 * `WS_CREDENTIAL_MODE_TOKEN` branch in `_open`, `_maybeFallBackToLegacyToken`, and the
 * `§5` tests in `tests/unit/lib/socketCredential.test.js` all go together. The marker below
 * is pinned by a test, so the shim cannot be forgotten and cannot be removed by accident.
 */
export const WS_TICKET_FALLBACK = Object.freeze({
  /** Grep this to find every part of the shim. Pinned by `socketCredential.test.js` §5. */
  marker: 'production-launch-hardening-8.2c-deploy-ordering-shim',
  /** The one-line kill switch, for the release after this one. */
  enabled: true,
  /** The condition that ends its life. Not a date: a deployment fact. */
  removeWhen: 'ECS is confirmed running the task definition that contains verify_ws_ticket',
  /**
   * The close codes that mean "this backend does not redeem tickets".
   *
   * `4001` is what every route in `api_ws/ws_routes.py` closes with on a missing or
   * unresolvable credential. `1006` is what the *browser* reports for the same refusal,
   * because those routes call `websocket.close(code=4001)` **before** `websocket.accept()`
   * — an ASGI server turns that into an HTTP 403 on the handshake, so no close frame is
   * ever sent and the 4001 never reaches JavaScript. Without `1006` the shim would pass
   * its own unit test and do nothing in production.
   */
  refusalCloseCodes: Object.freeze([4001, 1006]),
});

/** Credential this session presents. `ticket` is the fix; `token` is the shim's fallback. */
const WS_CREDENTIAL_MODE_TICKET = 'ticket';
const WS_CREDENTIAL_MODE_TOKEN = 'token';
/* ┗━━━ end WS_TICKET_FALLBACK ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛ */

class WebSocketClient {
  constructor() {
    this.ws = null;
    this.url = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
    this.reconnectDelay = 1000;
    this.subscriptions = new Map(); // event_type -> Set of callbacks
    this.connectionStatus = 'disconnected';
    this.messageQueue = []; // Queue messages while disconnected
    this.reconnectEnabled = true; // Flag to control reconnection
    this.reconnectTimeoutId = null; // Track reconnect timeout for cleanup
    this.reconnectJitterMs = 250; // Requirement 23.4: added jitter, so no thundering herd

    // ── strategy-builder task 8.5 ────────────────────────────────────────
    // Requirements 23.1-23.6. Channel-level state, kept beside the event-type
    // subscriptions rather than in a second socket layer: `subscriptions` above routes
    // by `message.type`, and these route by `message.channel`, over the SAME socket.
    this.channelSubscriptions = new Map(); // channel -> Set of handlers
    this.channelRefusals = new Map(); // channel -> the last refusal frame
    this.statusListeners = new Set(); // status transitions, for the disconnected poll
    this.openListeners = new Set(); // (re)connect, for resubscribe + snapshot
    this.refcount = 0; // one intended connection per session
    this.acquiredPath = null;
    this.savedReconnectPolicy = null;
    this.heartbeatInterval = null; // Heartbeat timer
    this.heartbeatTimeout = null; // Heartbeat response timeout
    this.lastPongTime = Date.now(); // Track last pong received
    this.heartbeatIntervalMs = 30000; // 30 seconds heartbeat
    this.heartbeatTimeoutMs = 10000; // 10 seconds to expect pong
    this.maxReconnectDelay = 30000; // Max 30 seconds between reconnects
    
    // 🔴 STEP 4: WebSocket reliability with sequence numbers
    this.lastSequenceNumber = 0; // Last received sequence number
    this.missedMessages = []; // Track missed messages for replay
    this.expectedSequence = 1; // Expected next sequence number
    this.messageBuffer = new Map(); // Buffer out-of-order messages
    this.MAX_BUFFER_SIZE = 100; // Max messages to buffer

    // ── production-launch-hardening task 8.2 ─────────────────────────────
    // The path last asked for, kept apart from `this.url`. The backoff reads this rather
    // than parsing `this.url` back: `this.url` carries a ticket that has already been
    // redeemed, and a reconnect presenting it would be refused.
    this.connectPath = null;
    // Bumped by every `_open` and by `disconnect`, so a ticket that arrives after the
    // attempt that asked for it was superseded or torn down opens nothing.
    this.connectGeneration = 0;

    // ── WS_TICKET_FALLBACK (task 8.2c) — delete with the block above ──────
    // `ticket` unless a refused ticket handshake proves this backend predates
    // `verify_ws_ticket`. Sticky for the session once it moves: see
    // `_maybeFallBackToLegacyToken`.
    this.credentialMode = WS_CREDENTIAL_MODE_TICKET;
    // Which credential the live socket presented, and whether it ever reached `open`.
    // Both are the evidence `_maybeFallBackToLegacyToken` reads; neither is a policy.
    this.presentedCredential = null;
    this.socketOpened = false;
    this.credentialFallbackLogged = false;
  }

  /**
   * Connect to the WebSocket server.
   *
   * Asynchronous since task 8.2: the credential is a ticket minted over HTTPS, so the
   * socket cannot be constructed until it arrives. Callers that do not care — `acquire`,
   * the top bar's retry — may ignore the promise; it **never rejects**, because a ticket
   * that cannot be obtained is a connection failure the backoff already knows how to
   * handle, not an exception for a caller to catch.
   *
   * @param {string} path - WebSocket endpoint path (default: /ws/telemetry)
   * @returns {Promise<void>} resolves once the socket has been constructed, or once a
   *   failed attempt has been handed to `scheduleReconnect`
   */
  connect(path = '/ws/telemetry') {
    // Re-enable reconnection whenever connect is explicitly called
    this.reconnectEnabled = true;
    this.reconnectAttempts = 0;
    return this._open(path);
  }

  /**
   * One connection attempt: mint a ticket if this session has a credential, then open.
   *
   * Separate from `connect` so the backoff can re-attempt **without** resetting
   * `reconnectAttempts`. `connect` resets it because an explicit connect is a fresh
   * intent; a reconnect must not, or the delay never grows past the first step and a
   * ticket endpoint that is refusing would be asked once a second against a 30/minute
   * rate limit.
   *
   * @param {string} path
   * @returns {Promise<void>} never rejects
   */
  _open(path) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      console.log('WebSocket already connected');
      return Promise.resolve();
    }

    this.connectPath = path;
    const generation = (this.connectGeneration += 1);
    const token = readSessionToken();

    if (!token) {
      /*
        No credential, no ticket to ask for — and the socket is still attempted. The
        client does not decide authorisation: every route in `ws_routes.py` fails closed,
        so the server refuses this connection. Refusing it here instead would turn a
        server-side decision into a client-side one, and this branch is also what keeps
        `connect()` synchronous for a session that has nothing to present.
      */
      this._openSocket(path, '', null);
      return Promise.resolve();
    }

    /* ┏━━━ WS_TICKET_FALLBACK (task 8.2c) — delete this branch with the block above ━━━┓
       This session has already seen a backend that refused a ticket, so there is nothing
       to be gained by minting another one: the ticket endpoint answers, the handshake
       does not. Straight to the legacy credential, synchronously, no round trip.        */
    if (WS_TICKET_FALLBACK.enabled && this.credentialMode === WS_CREDENTIAL_MODE_TOKEN) {
      const legacySeparator = path.includes('?') ? '&' : '?';
      this._openSocket(
        path,
        `${legacySeparator}token=${encodeURIComponent(token)}`,
        WS_CREDENTIAL_MODE_TOKEN,
      );
      return Promise.resolve();
    }
    /* ┗━━━ end WS_TICKET_FALLBACK ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛ */

    this._setStatus('connecting');

    return this.requestTicket(token).then(
      (ticket) => {
        // Superseded by a later attempt, or torn down while the request was in flight.
        if (generation !== this.connectGeneration) return;
        const hasQuery = path.includes('?');
        this._openSocket(
          path,
          `${hasQuery ? '&' : '?'}ticket=${encodeURIComponent(ticket)}`,
          WS_CREDENTIAL_MODE_TICKET,
        );
      },
      (error) => {
        if (generation !== this.connectGeneration) return;
        /*
          A handled failure. 503 `WS_TICKET_STORE_UNAVAILABLE`, a 401 on an expired
          session, or no network at all are the same thing from here: this attempt did
          not produce a socket. It goes to the same capped, jittered backoff a dropped
          connection goes to, which is what bounds the request rate — five attempts over
          ~31 s on the default policy, against an endpoint that allows 30 a minute.

          The ticket value is never logged, and there is none to log on this path.
        */
        console.error(`WebSocket ticket request failed: ${error?.message || 'unknown error'}`);
        this._setStatus('error');
        this.scheduleReconnect();
      },
    );
  }

  /**
   * Exchange the session JWT for a single-use socket ticket.
   *
   * Over `fetch` rather than the app's axios client on purpose: this is the one request
   * whose *headers* are the point. The JWT travels in `Authorization`, which is not
   * written to an access log, and what comes back is the only thing that goes in a URL.
   *
   * @param {string} token the session JWT, by reference — never logged, never returned
   * @returns {Promise<string>} the ticket
   */
  async requestTicket(token) {
    const response = await fetch(WS_TICKET_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: '{}',
    });

    if (!response || !response.ok) {
      // The status, not the body: a 503 body names `WS_TICKET_STORE_UNAVAILABLE` and a
      // 401's names nothing useful, and neither changes what this client does next.
      throw new Error(`ticket endpoint returned ${response ? response.status : 'no response'}`);
    }

    const payload = await response.json();
    const ticket = payload && typeof payload.ticket === 'string' ? payload.ticket : '';
    if (!ticket) throw new Error('ticket endpoint returned no ticket');
    return ticket;
  }

  /**
   * Construct the socket and wire its handlers.
   *
   * @param {string} path
   * @param {string} credentialParam `?ticket=…`, `&ticket=…`, or `''`
   * @param {string|null} presented which credential `credentialParam` carries, or `null`
   *   for none. Recorded, not acted on: `_maybeFallBackToLegacyToken` (task 8.2c) needs to
   *   know what a refused handshake had presented, and goes with that shim.
   */
  _openSocket(path, credentialParam, presented = WS_CREDENTIAL_MODE_TICKET) {
    this.presentedCredential = presented;
    this.socketOpened = false;
    this.url = `${WS_BASE}${path}${credentialParam}`;
    /*
      Logged without the query string. There is no `token=[REDACTED]` rewrite here any
      more, and deliberately not: a redaction would imply a credential is still in the URL
      when what is there is a ticket that is single-use and about to be spent. The ticket
      value is not logged either — it is a credential until it is redeemed.
    */
    console.log(`Connecting to WebSocket: ${WS_BASE}${path}`);

    try {
      this.ws = new WebSocket(this.url);
      this._setStatus('connecting');

      this.ws.onopen = () => this.handleOpen();
      this.ws.onmessage = (event) => this.handleMessage(event);
      this.ws.onerror = (error) => this.handleError(error);
      // The `CloseEvent` is threaded through rather than dropped: `handleClose` already
      // logs `event.code`, and task 8.2c's fallback is decided by that code.
      this.ws.onclose = (event) => this.handleClose(event);
    } catch (error) {
      console.error('WebSocket connection error:', error);
      this._setStatus('error');
      // Clean up failed connection attempt
      if (this.ws) {
        try {
          this.ws.close();
        } catch (e) {
          // Ignore close errors
        }
        this.ws = null;
      }
      this.scheduleReconnect();
    }
  }

  /**
   * Handle WebSocket open event
   */
  handleOpen() {
    console.log('🟢 WebSocket connected');
    this._setStatus('connected');
    this.reconnectAttempts = 0;
    // task 8.2c: a handshake that completed is not a credential refusal, whatever code it
    // closes with later. Goes with the WS_TICKET_FALLBACK block.
    this.socketOpened = true;
    this.lastPongTime = Date.now();

    // Clear any pending reconnect timeout
    if (this.reconnectTimeoutId) {
      clearTimeout(this.reconnectTimeoutId);
      this.reconnectTimeoutId = null;
    }

    // 🔴 STEP 4: Request message replay on reconnect to catch missed updates
    if (this.expectedSequence > 1) {
      this.requestMessageReplay(this.expectedSequence);
    }

    // Authenticate if token exists
    const token = sessionStorage.getItem('token');
    if (token && token !== 'dev_bypass') {
      this.send({ action: 'auth', token });
    }

    // Send queued messages
    this.flushMessageQueue();

    /*
      strategy-builder task 8.5, Requirement 23.5: resubscribe every channel this
      session had, then let the callers request a snapshot.

      Resubscribing is not optional and not an optimisation. The server clears a
      connection's authorised subscriptions on disconnect *on purpose* — a new
      connection is authorised again from scratch — so a client that did not resubscribe
      would sit on an open socket receiving nothing and reporting itself connected. A
      channel that was refused before is resubscribed too: a refusal is answered per
      connection, and a token refresh or an applied migration can legitimately change
      the answer. The server refuses again for a few bytes if nothing changed.
    */
    this.channelRefusals.clear();
    for (const channel of this.channelSubscriptions.keys()) {
      this.send({ action: 'subscribe', channel });
    }
    this._notifyOpen();

    // Start heartbeat
    this.startHeartbeat();
  }

  /**
   * Record a connection-status transition and tell anyone watching.
   *
   * strategy-builder task 8.5. The listeners exist so the builder can report Feed_State
   * `DISCONNECTED` the moment the socket drops (Requirement 23.4) and run its 30 s
   * safety poll only while it is down (Requirement 23.6) — neither of which is
   * answerable by polling `getStatus()` on a timer.
   *
   * @param {string} status
   */
  _setStatus(status) {
    if (this.connectionStatus === status) return;
    this.connectionStatus = status;
    for (const listener of Array.from(this.statusListeners)) {
      try {
        listener(status);
      } catch (error) {
        console.error('WebSocket status listener error:', error);
      }
    }
  }

  /** Tell anyone watching that the socket is open, so they can snapshot. */
  _notifyOpen() {
    for (const listener of Array.from(this.openListeners)) {
      try {
        listener();
      } catch (error) {
        console.error('WebSocket open listener error:', error);
      }
    }
  }

  /**
   * Watch connection-status transitions. Returns an unsubscribe function.
   * @param {Function} listener
   * @returns {Function}
   */
  onStatusChange(listener) {
    if (typeof listener !== 'function') return () => {};
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  /**
   * Watch (re)connections. Returns an unsubscribe function.
   *
   * Fires after the channels have been resubscribed, which is the order Requirement
   * 23.5 asks for: resubscribe, *then* request a snapshot. A snapshot requested before
   * the resubscribe could be answered while the connection is still receiving nothing.
   *
   * @param {Function} listener
   * @returns {Function}
   */
  onOpen(listener) {
    if (typeof listener !== 'function') return () => {};
    this.openListeners.add(listener);
    return () => this.openListeners.delete(listener);
  }

  /**
   * Handle incoming WebSocket message
   * @param {MessageEvent} event 
   */
  handleMessage(event) {
    try {
      const message = JSON.parse(event.data);
      
      // 🔴 STEP 4: Message validation - reject invalid messages
      if (!message || typeof message !== 'object') {
        console.error('🔴 WebSocket: Invalid message format (not an object)');
        return;
      }
      
      if (!message.type && !message.event_type) {
        console.error('🔴 WebSocket: Invalid message - missing type/event_type');
        return;
      }
      
      const eventType = message.type || message.event_type;

      // Handle pong response
      if (eventType === 'pong' || message.action === 'pong') {
        this.lastPongTime = Date.now();
        clearTimeout(this.heartbeatTimeout);
        this.heartbeatTimeout = null;
        return;
      }

      /*
        strategy-builder task 8.5, Requirement 21.6: a refused subscription is REPORTED.

        The server refuses the subscription and keeps the connection, so this is
        recorded and handed to that channel's own handlers rather than logged and
        forgotten. Whoever asked for the channel is the one who has to be able to say
        "this is not yours" on screen; a refusal that only reached the console would be
        indistinguishable from a channel that is simply quiet.
      */
      if (eventType === 'subscription_refused') {
        const refused = typeof message.channel === 'string' ? message.channel : '';
        console.warn(`WebSocket: subscription refused for ${refused}: ${message.code}`);
        if (refused) {
          this.channelRefusals.set(refused, message);
          this._deliverToChannel(refused, message);
        }
        return;
      }

      // 🔴 STEP 4: Sequence number validation for ordered delivery
      if (message.seq !== undefined) {
        const msgSeq = message.seq;
        
        if (msgSeq < this.expectedSequence) {
          // Duplicate or old message - ignore
          console.warn(`WebSocket: Out-of-order message (seq ${msgSeq}, expected ${this.expectedSequence})`);
          return;
        }
        
        if (msgSeq > this.expectedSequence) {
          // Gap detected - buffer and request replay
          console.error(`🔴 WebSocket: Message gap detected! Missing ${this.expectedSequence} to ${msgSeq - 1}`);
          this.messageBuffer.set(msgSeq, { message, eventType });
          this.requestMessageReplay(this.expectedSequence);
          return;
        }
        
        // Correct sequence - process and check buffer
        this.expectedSequence = msgSeq + 1;
        this.processMessage(message, eventType);
        
        // Process any buffered messages that are now in order
        this.processBufferedMessages();
      } else {
        // No sequence number - process normally (backward compatibility)
        this.processMessage(message, eventType);
      }

      // Log all messages for debugging (except frequent heartbeats)
      if (eventType !== 'ping' && eventType !== 'pong') {
        console.log('📨 WebSocket message:', eventType, message);
      }
    } catch (error) {
      console.error('WebSocket message parse error:', error, 'Raw data:', event.data?.substring(0, 200));
    }
  }

  /**
   * 🔴 STEP 4: Process validated message
   */
  processMessage(message, eventType) {
    // Route message to subscribed callbacks
    if (eventType && this.subscriptions.has(eventType)) {
      const callbacks = this.subscriptions.get(eventType);
      callbacks.forEach(callback => {
        try {
          callback(message);
        } catch (error) {
          console.error(`Error in callback for ${eventType}:`, error);
        }
      });
    }

    // strategy-builder task 8.5: and to whoever subscribed to the channel it names.
    // Both routings run: an event-type subscriber and a channel subscriber are two
    // different questions about the same frame, and one arriving must not cancel the
    // other.
    if (typeof message.channel === 'string' && message.channel !== '') {
      this._deliverToChannel(message.channel, message);
    }
  }

  /**
   * Hand one frame to the handlers registered for `channel`.
   * @param {string} channel
   * @param {Object} message
   */
  _deliverToChannel(channel, message) {
    const handlers = this.channelSubscriptions.get(channel);
    if (!handlers) return;
    handlers.forEach((handler) => {
      try {
        handler(message);
      } catch (error) {
        console.error(`Error in channel handler for ${channel}:`, error);
      }
    });
  }

  /**
   * 🔴 STEP 4: Process buffered messages that are now in order
   */
  processBufferedMessages() {
    while (this.messageBuffer.has(this.expectedSequence)) {
      const { message, eventType } = this.messageBuffer.get(this.expectedSequence);
      this.messageBuffer.delete(this.expectedSequence);
      this.processMessage(message, eventType);
      this.expectedSequence++;
    }
    
    // Prevent buffer from growing too large
    if (this.messageBuffer.size > this.MAX_BUFFER_SIZE) {
      console.error('🔴 WebSocket: Message buffer overflow - clearing old messages');
      const oldestSeq = Math.min(...this.messageBuffer.keys());
      this.messageBuffer.delete(oldestSeq);
    }
  }

  /**
   * 🔴 STEP 4: Request replay of missed messages
   */
  requestMessageReplay(fromSequence) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.send({
        action: 'replay',
        from_sequence: fromSequence
      });
      console.log(`📨 WebSocket: Requesting message replay from sequence ${fromSequence}`);
    }
  }

  /**
   * Handle WebSocket error
   * @param {Event} error 
   */
  handleError(error) {
    console.error('WebSocket error:', error);
    this._setStatus('error');
    // Error often precedes close, but trigger reconnect if not already closing
    if (this.ws && this.ws.readyState !== WebSocket.CLOSING && this.ws.readyState !== WebSocket.CLOSED) {
      console.log('Forcing reconnect due to WebSocket error');
      this.ws.close();
    }
  }

  /**
   * Handle WebSocket close
   */
  handleClose(event) {
    console.log(`🔴 WebSocket disconnected (code: ${event?.code || 'unknown'}, reason: ${event?.reason || 'none'})`);
    this._setStatus('disconnected');
    this.stopHeartbeat();
    this.ws = null;

    // task 8.2c. Read before the reconnect is scheduled, so the attempt the backoff makes
    // is the one that presents the fallback credential. Goes with the shim.
    this._maybeFallBackToLegacyToken(event);

    // Attempt reconnection if enabled
    if (this.reconnectEnabled) {
      this.scheduleReconnect();
    }
  }

  /* ┏━━━ WS_TICKET_FALLBACK (task 8.2c) ━━━ TEMPORARY. DELETE THIS METHOD. ━━━━━━━━━━━┓ */
  /**
   * Degrade this session's credential to the legacy `?token=` — once, and only on the one
   * signal that means "this backend cannot redeem a ticket".
   *
   * WHY THE SIGNAL IS READ AT THE SOCKET AND NOT AT THE TICKET FETCH. `issue_ws_ticket` is
   * already on `main`, so during the deploy window `POST /api/auth/ws-ticket` returns 200
   * with a perfectly good ticket. The mint tells you nothing. The only place the mismatch
   * is observable is the handshake that presents the ticket and is refused.
   *
   * THE FOUR CONDITIONS, AND WHY EACH ONE IS THERE:
   *
   * - `presentedCredential === 'ticket'` — a refused `token` attempt is condition 4 below,
   *   a real auth failure, and must not switch anything.
   * - `!socketOpened` — a credential refusal happens *at* the handshake. A connection that
   *   opened and later dropped is a network event, and treating it as a credential problem
   *   would put the JWT in a URL on every blip.
   * - the close code is in `refusalCloseCodes` — an explicit numeric code, so a close with
   *   no code at all (which is what a test double or a torn-down socket produces) is never
   *   read as a refusal.
   * - `!credentialFallbackLogged` — once per session. This is what forbids
   *   `ticket → token → ticket`.
   *
   * @param {CloseEvent|undefined} event
   * @returns {boolean} whether this session was degraded by this close
   */
  _maybeFallBackToLegacyToken(event) {
    if (!WS_TICKET_FALLBACK.enabled) return false;
    if (this.credentialMode !== WS_CREDENTIAL_MODE_TICKET) return false;
    if (this.presentedCredential !== WS_CREDENTIAL_MODE_TICKET) return false;
    if (this.socketOpened) return false;

    const code = typeof event?.code === 'number' ? event.code : null;
    if (code === null || !WS_TICKET_FALLBACK.refusalCloseCodes.includes(code)) return false;

    // Nothing to fall back *to* without a session credential, and the no-credential path
    // already attempts the socket and lets the server refuse it.
    if (!readSessionToken()) return false;

    this.credentialMode = WS_CREDENTIAL_MODE_TOKEN;

    if (!this.credentialFallbackLogged) {
      this.credentialFallbackLogged = true;
      /*
        Loud, once, and named for what it is. No ticket value and no JWT value: the close
        code and the path are the whole diagnostic, and both are already non-secret.
      */
      console.warn(
        '⚠️ WebSocket COMPATIBILITY FALLBACK ' +
          `(${WS_TICKET_FALLBACK.marker}): the handshake for ${this.connectPath || 'the socket'} ` +
          `presented a single-use ticket and was refused with close code ${code}. ` +
          'This backend has no ticket redemption — it predates `verify_ws_ticket`, which ' +
          'means an ECS rollout is still in flight. Falling back to the legacy `token` ' +
          'query credential for the rest of this session so live data keeps flowing. ' +
          'The session JWT is in the socket URL while this fallback is active, which is ' +
          `the exposure Requirements 1.21 / 2.21 remove. Remove this shim when: ${WS_TICKET_FALLBACK.removeWhen}.`,
      );
    }

    return true;
  }
  /* ┗━━━ end WS_TICKET_FALLBACK ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛ */

  /**
   * Schedule reconnection attempt
   */
  scheduleReconnect() {
    if (!this.reconnectEnabled) {
      console.log('Reconnection disabled, skipping reconnect');
      return;
    }

    // Clear any existing reconnect timeout
    if (this.reconnectTimeoutId) {
      clearTimeout(this.reconnectTimeoutId);
      this.reconnectTimeoutId = null;
    }

    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error(`Max reconnection attempts (${this.maxReconnectAttempts}) reached. Giving up.`);
      this._setStatus('failed');
      return;
    }

    this.reconnectAttempts++;
    /*
      Exponential backoff, capped, plus jitter — Requirement 23.4's three clauses.

      The cap was already here (`maxReconnectDelay` = 30 s). The jitter is task 8.5's
      addition and it is the clause that matters most in aggregate: without it, every
      browser that lost the same backend restart retries on the same schedule and the
      server is hit by a thundering herd at 1 s, 2 s, 4 s, ... A quarter-second of spread
      is small next to the delay it perturbs and enough to decorrelate the fleet.
    */
    const delay = Math.min(
      this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1),
      this.maxReconnectDelay
    ) + Math.floor(Math.random() * (this.reconnectJitterMs + 1));

    console.log(`⏱️ Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);

    this.reconnectTimeoutId = setTimeout(() => {
      this.reconnectTimeoutId = null;
      /*
        production-launch-hardening task 8.2. The path is read from `connectPath`, not
        parsed out of `this.url`, for two reasons. The ticket in `this.url` has been
        redeemed — `verify_ws_ticket` reads it with `getdel` — so reusing that URL would
        present a spent credential and the socket would never recover. And
        `new URL(this.url).pathname` dropped the query string outright, so a reconnect on
        `/ws/dashboard?user_id=…` lost the parameter the route requires.

        `_open` rather than `connect`: a reconnect must not reset `reconnectAttempts`, or
        the delay never grows.
      */
      this._open(this.connectPath || '/ws/telemetry');
    }, delay);
  }

  /**
   * Start heartbeat/ping-pong to detect stale connections
   */
  startHeartbeat() {
    this.stopHeartbeat(); // Clear any existing heartbeat
    
    this.heartbeatInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        // Check if we haven't received a pong in too long
        const timeSinceLastPong = Date.now() - this.lastPongTime;
        if (timeSinceLastPong > this.heartbeatIntervalMs + this.heartbeatTimeoutMs) {
          console.warn(`No pong received for ${timeSinceLastPong}ms, connection may be stale`);
          // Force reconnection
          this.ws.close();
          return;
        }

        // Send ping
        this.send({ action: 'ping', timestamp: Date.now() });
        
        // Set timeout for pong response
        this.heartbeatTimeout = setTimeout(() => {
          console.warn('Heartbeat pong timeout, closing connection');
          if (this.ws) {
            this.ws.close();
          }
        }, this.heartbeatTimeoutMs);
      }
    }, this.heartbeatIntervalMs);
  }

  /**
   * Stop heartbeat timers
   */
  stopHeartbeat() {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
    if (this.heartbeatTimeout) {
      clearTimeout(this.heartbeatTimeout);
      this.heartbeatTimeout = null;
    }
  }

  /**
   * Send message to WebSocket server
   * @param {Object} data - Message data to send
   */
  send(data) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    } else {
      // Queue message for when connection is established
      this.messageQueue.push(data);
      console.log('Message queued (not connected)');
    }
  }

  /**
   * Flush queued messages
   */
  flushMessageQueue() {
    while (this.messageQueue.length > 0) {
      const message = this.messageQueue.shift();
      this.send(message);
    }
  }

  /**
   * Subscribe to an event type
   * @param {string} eventType - Event type to subscribe to
   * @param {Function} callback - Callback function for messages
   * @returns {Function} Unsubscribe function
   */
  subscribe(eventType, callback) {
    if (!this.subscriptions.has(eventType)) {
      this.subscriptions.set(eventType, new Set());
    }
    
    this.subscriptions.get(eventType).add(callback);
    console.log(`Subscribed to: ${eventType}`);

    // Return unsubscribe function
    return () => this.unsubscribe(eventType, callback);
  }

  /**
   * Unsubscribe from an event type
   * @param {string} eventType - Event type to unsubscribe from
   * @param {Function} callback - Callback function to remove
   */
  unsubscribe(eventType, callback) {
    if (this.subscriptions.has(eventType)) {
      const callbacks = this.subscriptions.get(eventType);
      callbacks.delete(callback);
      
      if (callbacks.size === 0) {
        this.subscriptions.delete(eventType);
        console.log(`Unsubscribed from: ${eventType}`);
      }
    }
  }

  /**
   * ═════════════════════════════════════════════════════════════════════════
   * CHANNEL SUBSCRIPTIONS, REF-COUNTED SESSION, AND REAUTHENTICATION
   * strategy-builder task 8.5. Requirements 21.6, 23.1-23.6.
   * ═════════════════════════════════════════════════════════════════════════
   *
   * `subscribe(eventType, cb)` above is a client-side routing table only: it never
   * told the server anything. The builder's channels are server-side subscriptions
   * that have to be *asked for* and can be *refused*, so they need their own pair of
   * methods — over the same socket, in the same class. A second socket layer was the
   * alternative and is exactly what `design.md` forbids ("Do not open a second
   * socket"): two sockets means two authentications, two reconnect schedules, and a
   * token refresh that reauthenticates one of them.
   */

  /**
   * Subscribe to a server-side channel.
   *
   * Several callers may hold the same channel; the server is told once. The returned
   * function releases this caller's hold, and the server is told to unsubscribe only
   * when the last hold goes (Requirement 23.2). Handlers receive every frame naming
   * the channel, including the `subscription_refused` frame when it is refused
   * (Requirement 21.6).
   *
   * @param {string} channel
   * @param {Function} handler
   * @returns {Function} release
   */
  subscribeChannel(channel, handler) {
    if (typeof channel !== 'string' || channel === '' || typeof handler !== 'function') {
      return () => {};
    }

    const first = !this.channelSubscriptions.has(channel);
    if (first) this.channelSubscriptions.set(channel, new Set());
    this.channelSubscriptions.get(channel).add(handler);

    if (first) {
      // Sent only on an open socket. Deliberately NOT queued: `handleOpen` resubscribes
      // everything in this map, so queueing would put the same subscribe frame on the wire
      // twice — once from the queue and once from the resubscribe. Nothing is lost by
      // waiting, because the map is the thing `handleOpen` reads.
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.send({ action: 'subscribe', channel });
      }
    } else {
      // A late joiner to an already-refused channel is told immediately rather than
      // waiting for a reconnect to hear about a refusal that has already happened.
      const refusal = this.channelRefusals.get(channel);
      if (refusal) {
        try {
          handler(refusal);
        } catch (error) {
          console.error(`Error in channel handler for ${channel}:`, error);
        }
      }
    }

    return () => this.unsubscribeChannel(channel, handler);
  }

  /**
   * Release one hold on `channel`. The server is told when the last one goes.
   * @param {string} channel
   * @param {Function} handler
   */
  unsubscribeChannel(channel, handler) {
    const handlers = this.channelSubscriptions.get(channel);
    if (!handlers) return;
    handlers.delete(handler);
    if (handlers.size > 0) return;

    this.channelSubscriptions.delete(channel);
    this.channelRefusals.delete(channel);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.send({ action: 'unsubscribe', channel });
    }
    // Deliberately NOT queued while disconnected: the server drops this connection's
    // subscriptions when it closes, and `handleOpen` resubscribes only what is still in
    // the map. Queueing an unsubscribe for a subscription that no longer exists would
    // send the server a message about nothing.
  }

  /** The channels this session currently holds, in insertion order. */
  subscribedChannels() {
    return Array.from(this.channelSubscriptions.keys());
  }

  /** The last refusal frame for `channel`, or null. */
  refusalFor(channel) {
    return this.channelRefusals.get(channel) || null;
  }

  /**
   * Reauthenticate the EXISTING connection with a refreshed token (Requirement 23.3).
   *
   * No reconnect, and that is the entire point: a session that refreshes its token
   * every hour would otherwise drop and rebuild its socket every hour, losing every
   * subscription and taking a snapshot round-trip each time. Nothing is sent when the
   * socket is down — a closed connection has no identity to refresh, and the next
   * `connect()` reads the current token from `sessionStorage` anyway.
   *
   * @param {string} token
   * @returns {boolean} whether the frame was sent
   */
  reauthenticate(token) {
    if (!token || typeof token !== 'string') return false;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.send({ action: 'auth', token });
    return true;
  }

  /**
   * Replace the reconnect policy, returning the previous one.
   *
   * A builder session must keep trying for as long as it is open, so it raises
   * `maxAttempts`; the default of 5 is right for a page that can be reloaded and is
   * left alone for every existing caller. Unbounded attempts are only safe *because*
   * the delay is capped at 30 s and jittered, which is why the two live in one policy
   * rather than being set independently.
   *
   * @param {{maxAttempts?: number, baseDelayMs?: number, maxDelayMs?: number, jitterMs?: number}} policy
   * @returns {Object} the previous policy
   */
  setReconnectPolicy(policy = {}) {
    const previous = {
      maxAttempts: this.maxReconnectAttempts,
      baseDelayMs: this.reconnectDelay,
      maxDelayMs: this.maxReconnectDelay,
      jitterMs: this.reconnectJitterMs,
    };
    if (Number.isFinite(policy.maxAttempts) || policy.maxAttempts === Infinity) {
      this.maxReconnectAttempts = policy.maxAttempts;
    }
    if (Number.isFinite(policy.baseDelayMs)) this.reconnectDelay = policy.baseDelayMs;
    if (Number.isFinite(policy.maxDelayMs)) this.maxReconnectDelay = policy.maxDelayMs;
    if (Number.isFinite(policy.jitterMs)) this.reconnectJitterMs = policy.jitterMs;
    return previous;
  }

  /**
   * Take a hold on the one connection for this browser session (Requirement 23.1).
   *
   * The first hold connects and installs the builder's reconnect policy; later holds
   * are counted and nothing else happens. This is what makes "one connection per
   * session" a property of the code rather than a convention: three builder views open
   * at once take three holds on one socket.
   *
   * @param {string} path
   * @param {Object} policy passed to `setReconnectPolicy` on the first hold
   * @returns {number} the new hold count
   */
  acquire(path = '/ws/telemetry', policy = null) {
    this.refcount += 1;
    if (this.refcount === 1) {
      this.acquiredPath = path;
      if (policy) this.savedReconnectPolicy = this.setReconnectPolicy(policy);
      this.connect(path);
    } else if (this.connectionStatus === 'failed' || this.connectionStatus === 'disconnected') {
      // A later hold on a socket that had given up is a reason to try again: something
      // in the app just decided it needs live data.
      this.connect(this.acquiredPath || path);
    }
    return this.refcount;
  }

  /**
   * Release one hold. The socket closes only when the last hold goes.
   * @returns {number} the remaining hold count
   */
  release() {
    if (this.refcount === 0) return 0;
    this.refcount -= 1;
    if (this.refcount > 0) return this.refcount;

    if (this.savedReconnectPolicy) {
      this.setReconnectPolicy(this.savedReconnectPolicy);
      this.savedReconnectPolicy = null;
    }
    this.acquiredPath = null;
    this.disconnect();
    return 0;
  }

  /** How many holds are outstanding on the session connection. */
  holdCount() {
    return this.refcount;
  }

  /**
   * Disconnect WebSocket
   */
  disconnect() {
    // Disable reconnection first to prevent auto-reconnect
    this.reconnectEnabled = false;

    // task 8.2: invalidate any ticket request still in flight, so a ticket that arrives
    // after the teardown opens nothing. Without this, a `release()` during the exchange
    // would be followed by a socket a moment later that nobody holds.
    this.connectGeneration += 1;
    this.connectPath = null;
    
    // Clear any pending reconnect
    if (this.reconnectTimeoutId) {
      clearTimeout(this.reconnectTimeoutId);
      this.reconnectTimeoutId = null;
    }
    
    // Stop heartbeat
    this.stopHeartbeat();
    
    // Close WebSocket
    if (this.ws) {
      try {
        this.ws.close();
      } catch (e) {
        // Ignore close errors
      }
      this.ws = null;
    }
    
    this._setStatus('disconnected');
    this.subscriptions.clear();
    // strategy-builder task 8.5: the channel registry goes with it. Keeping it would
    // mean the next `connect()` resubscribed channels nobody is holding any more.
    this.channelSubscriptions.clear();
    this.channelRefusals.clear();
    this.messageQueue = [];
    this.reconnectAttempts = 0;
    
    console.log('🔌 WebSocket disconnected cleanly');
  }

  /**
   * Disable reconnection attempts
   */
  disableReconnect() {
    this.reconnectEnabled = false;
  }

  /**
   * Enable reconnection attempts
   */
  enableReconnect() {
    this.reconnectEnabled = true;
  }

  /**
   * Get current connection status
   * @returns {string} Connection status
   */
  getStatus() {
    return this.connectionStatus;
  }

  /**
   * Check if connected
   * @returns {boolean}
   */
  isConnected() {
    return this.connectionStatus === 'connected';
  }
}

// Create singleton instance
const wsClient = new WebSocketClient();

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * CONVENIENCE SUBSCRIPTION METHODS
 * ═══════════════════════════════════════════════════════════════════════════
 */

/**
 * 🔴 STEP 8: Subscribe to order updates (matches backend event type)
 * @param {Function} callback - Callback function for order updates
 * @returns {Function} Unsubscribe function
 */
wsClient.subscribeOrders = (callback) => {
  return wsClient.subscribe('orders', callback);
};

/**
 * 🔴 STEP 8: Subscribe to PnL updates (matches backend event type)
 * @param {Function} callback - Callback function for PnL updates
 * @returns {Function} Unsubscribe function
 */
wsClient.subscribePnL = (callback) => {
  return wsClient.subscribe('pnl', callback);
};

/**
 * 🔴 STEP 8: Subscribe to position updates (matches backend event type)
 * @param {Function} callback - Callback function for position updates
 * @returns {Function} Unsubscribe function
 */
wsClient.subscribePositions = (callback) => {
  return wsClient.subscribe('positions', callback);
};

/**
 * Subscribe to strategy status events
 * @param {Function} callback - Callback function for strategy status changes
 * @returns {Function} Unsubscribe function
 */
wsClient.subscribeStrategyStatus = (callback) => {
  return wsClient.subscribe('STRATEGY_STATUS', callback);
};

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * TELEMETRY ADAPTER LAYER
 * ═══════════════════════════════════════════════════════════════════════════
 */

/**
 * Adapts strict backend WebSocket telemetry payloads into the flat format expected by the UI.
 * @param {Object} event - The raw WebSocket event
 * @returns {Object} The flattened event
 */
export function normalizeTelemetryEvent(event) {
  if (!event || !event.payload) return event;
  
  const payload = event.payload;
  const normalized = {
    ...event,
    ...payload, // Flatten payload keys into root
    original_payload: payload // Preserve original
  };
  
  // Specific key mappings requested by the dashboard
  if (payload.signal !== undefined) normalized.signal_type = payload.signal;
  if (payload.uptime !== undefined) normalized.uptime_seconds = payload.uptime;
  
  return normalized;
}

export default wsClient;
