"""The server must answer the browser client's liveness ping with a pong.

THE DEFECT THESE PIN, which was a live one on every socket the app opens.

`algo22-terminal/src/websocketClient.js` is the client every realtime feature
uses (App.jsx, Dashboard, PaperTrading, SignalTrace, RiskSettings, Billing,
NotificationCenter and seven hooks all import it). On open it calls
``startHeartbeat()``; every 30s that timer sends ``{action: 'ping'}`` and arms a
10s timer whose expiry calls ``ws.close()``::

    this.send({ action: 'ping', timestamp: Date.now() });
    this.heartbeatTimeout = setTimeout(() => {
      console.warn('Heartbeat pong timeout, closing connection');
      if (this.ws) { this.ws.close(); }
    }, this.heartbeatTimeoutMs);        // 10_000

The ONLY thing that disarms it is an inbound frame whose `type` or `action` is
``pong``. No handler in `backend_app/api_ws/ws_routes.py` ever sent one: the
receive loops consumed the ping and dropped it, and the loops that do mention
pong only handle an INBOUND pong, treating the client as the responder.

So: client connects, pings at t=30s, hears nothing, and closes at t=40s. Then
reconnects with backoff and repeats, indefinitely. Notifications, dashboard
updates, signal trace, paper prices and the connection-status indicator all
flapped on a ~40 second cycle.

All nine paths the frontend opens are served by this module -- /ws/telemetry,
/ws/ticker/{symbol}, /ws/orderbook/{symbol}, /ws/candles/{symbol}/{timeframe},
/ws/user/{user_id}, /ws/pnl/{user_id}, /ws/dashboard, /ws/strategy/{id},
/ws/signal-trace -- so this was not a corner case.
"""
import ast
import asyncio
import inspect
import json
import pathlib

import pytest
from fastapi.websockets import WebSocketState

from backend_app.api_ws import ws_routes

WS_ROUTES_PATH = pathlib.Path(inspect.getsourcefile(ws_routes))


class _FakeWebSocket:
    """Records what the server sent, and hands over scripted client frames."""

    def __init__(self, frames, state=WebSocketState.CONNECTED):
        self._frames = list(frames)
        self.client_state = state
        self.sent = []

    async def receive_text(self):
        if not self._frames:
            raise AssertionError("handler asked for more frames than were scripted")
        return self._frames.pop(0)

    async def send_text(self, data):
        self.sent.append(data)


# ── what counts as a client ping ────────────────────────────────────────────

@pytest.mark.parametrize("frame", [
    '{"action": "ping", "timestamp": 1730000000000}',  # what the live client sends
    '{"action": "ping"}',
    '{"type": "ping"}',
    '{"type": "ping", "timestamp": 1}',
])
def test_recognises_a_client_ping(frame):
    assert ws_routes._is_client_ping(frame) is True


@pytest.mark.parametrize("frame", [
    '{"type": "pong"}',            # an inbound pong is not a ping
    '{"type": "subscribe"}',
    '{"action": "subscribe"}',
    "not json at all",
    "",
    "[]",                          # valid json, wrong shape
    "null",
    '"ping"',                      # a bare string, not an object
    "123",
])
def test_does_not_mistake_other_frames_for_a_ping(frame):
    assert ws_routes._is_client_ping(frame) is False


# ── answering behaviour ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_client_ping_is_answered_with_a_pong():
    ws = _FakeWebSocket(['{"action": "ping", "timestamp": 1730000000000}'])

    raw = await ws_routes._receive_answering_pings(ws, 30)

    assert len(ws.sent) == 1, "the client's ping must be answered, or it closes the socket"
    answer = json.loads(ws.sent[0])
    # websocketClient.js: eventType === 'pong' || message.action === 'pong'
    assert answer.get("type") == "pong" or answer.get("action") == "pong"
    # The frame is still handed back, so each route's own handling is unchanged.
    assert raw == '{"action": "ping", "timestamp": 1730000000000}'


@pytest.mark.asyncio
async def test_the_pong_is_the_shape_the_live_client_accepts():
    """Pinned against the client's actual check rather than a guess."""
    answer = json.loads(ws_routes._PONG_FRAME)
    event_type = answer.get("type") or answer.get("event_type")
    assert event_type == "pong" or answer.get("action") == "pong"


@pytest.mark.asyncio
async def test_a_non_ping_frame_is_returned_untouched_and_unanswered():
    ws = _FakeWebSocket(['{"type": "subscribe", "channel": "dashboard"}'])

    raw = await ws_routes._receive_answering_pings(ws, 30)

    assert ws.sent == [], "only pings get a pong; anything else must pass through"
    assert raw == '{"type": "subscribe", "channel": "dashboard"}'


@pytest.mark.asyncio
async def test_a_malformed_frame_does_not_raise():
    """A client sending junk must not kill the receive loop."""
    ws = _FakeWebSocket(["<<<not json>>>"])

    raw = await ws_routes._receive_answering_pings(ws, 30)

    assert ws.sent == []
    assert raw == "<<<not json>>>"


@pytest.mark.asyncio
async def test_no_pong_is_sent_on_a_closed_socket():
    """Answering a disconnected socket would raise inside the receive loop."""
    ws = _FakeWebSocket(['{"action": "ping"}'], state=WebSocketState.DISCONNECTED)

    raw = await ws_routes._receive_answering_pings(ws, 30)

    assert ws.sent == []
    assert raw == '{"action": "ping"}'


@pytest.mark.asyncio
async def test_the_receive_timeout_is_honoured():
    """The helper replaced `asyncio.wait_for(..., timeout=N)`, so it must still
    time out rather than block a route's loop forever."""

    class _Silent:
        client_state = WebSocketState.CONNECTED

        async def receive_text(self):
            await asyncio.sleep(10)

        async def send_text(self, data):  # pragma: no cover - never reached
            raise AssertionError("nothing should be sent")

    with pytest.raises(asyncio.TimeoutError):
        await ws_routes._receive_answering_pings(_Silent(), 0.01)


# ── the ratchet: no route may receive a client frame directly ───────────────

def _receive_text_call_owners():
    """Names of the functions containing a `websocket.receive_text()` call.

    AST rather than text matching: an earlier remediation in this repository was
    fooled twice by a pattern that matched a COMMENT describing the thing it was
    looking for, so the structure is walked instead of the source being grepped.
    """
    tree = ast.parse(WS_ROUTES_PATH.read_text(encoding="utf-8"))
    owners = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def _visit_func(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = _visit_func
        visit_AsyncFunctionDef = _visit_func

        def visit_Call(self, node):
            func = node.func
            if (isinstance(func, ast.Attribute)
                    and func.attr == "receive_text"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "websocket"):
                owners.append(self.stack[-1] if self.stack else "<module>")
            self.generic_visit(node)

    Visitor().visit(tree)
    return owners


def test_only_the_helper_receives_client_frames_directly():
    """A new route added with a bare `websocket.receive_text()` would silently
    stop answering pings for that path and put it back on the 40-second
    disconnect cycle. This fails instead."""
    owners = _receive_text_call_owners()

    assert owners, "found no websocket.receive_text() call at all -- the ratchet is not looking at the right module"
    assert set(owners) == {"_receive_answering_pings"}, (
        f"these functions call websocket.receive_text() directly instead of going "
        f"through _receive_answering_pings: "
        f"{sorted(set(owners) - {'_receive_answering_pings'})}. Route them through "
        f"the helper, or the browser client will close those sockets every ~40s."
    )


def test_every_route_receive_loop_goes_through_the_helper():
    """The counterpart to the test above: prove the helper is actually WIRED IN
    at more than zero call sites, so deleting the calls cannot pass both tests."""
    tree = ast.parse(WS_ROUTES_PATH.read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_receive_answering_pings"
    ]
    assert len(calls) >= 7, (
        f"only {len(calls)} receive loop(s) route through the ping-answering helper; "
        f"every route that reads client frames must"
    )
