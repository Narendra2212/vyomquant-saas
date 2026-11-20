"""SEC-03 -- webhook delivery idempotency no longer lives only in Redis for 24h.

THE DEFECT
    backend_app/routers/billing.py's webhook handlers tracked
    delivery idempotency ENTIRELY in Redis: a "processing" key (60s TTL) that
    transitioned to "completed" (86400s TTL) on success. core/cache/redis_manager.py
    is deliberately fail-soft (every method swallows its own exception and returns a
    falsy default), so a Redis outage during the initial check silently read as "not
    a duplicate" and the handler proceeded to reprocess a REAL duplicate delivery
    undetected. And even with Redis healthy, a redelivery more than 24 hours after the
    first (both providers redeliver for days, and an operator can manually resend an
    old webhook from either dashboard for reconciliation) found no record at all once
    the "completed" key had expired.

THE FIX
    public.webhook_events (migration 045) is now a DURABLE claim layered UNDERNEATH
    billing.py's existing Redis fast path -- core/webhook_idempotency.py's
    acquire_webhook_processing / mark_webhook_completed / release_webhook_processing.
    Postgres's own UNIQUE (provider, event_id) constraint is the atomicity primitive;
    there is no TTL to outlive. A 'processing' row older than
    PROCESSING_STALE_AFTER_SECONDS is treated as abandoned and may be reclaimed, so one
    crashed delivery cannot block every future retry forever -- the same crash-safety
    property the Redis-only version had.

    (The OTHER half of this finding -- "refunds do not revoke plan entitlement" -- was
    already fixed by BILL-01's _revoke_plan_for_refunded_razorpay_payment, VERIFIED_FIXED
    earlier this session.)

WHAT THIS FILE PROVES
    1. A fresh (provider, event_id): acquire succeeds (the INSERT lands).
    2. A second acquire for the SAME (provider, event_id), while the first is still
       'processing' and not stale: refused.
    3. A second acquire after mark_webhook_completed: refused -- forever, no TTL.
    4. A second acquire after the 'processing' row has gone stale (older than
       PROCESSING_STALE_AFTER_SECONDS): succeeds (reclaim), the exact crash-recovery
       property the old 60s Redis TTL gave.
    5. release_webhook_processing deletes a 'processing' row so an immediate retry can
       re-acquire without waiting out the staleness window; it does NOT delete a
       'completed' row (a late failure in unrelated code after the durable record was
       already written must never un-mark a genuinely completed delivery).
    6. When the durable store is simply NOT CONFIGURED (get_service_supabase_async()
       returns None -- the normal shape of DEV_MODE/local/test), acquire returns True
       (defer entirely to Redis) rather than refusing every webhook as a duplicate.
    7. When the store IS configured but a call raises (a real outage), acquire fails
       CLOSED (returns False).
    8. The Razorpay webhook handler -- the only webhook route billing.py exposes --
       calls all three functions, and no call site keys a durable row to any provider
       other than "razorpay". An AST walk over billing.py, scoped to the handler's own
       body; see that test's docstring for why the previous substring count was unsound.

A NOTE ON PROVIDERS
    When this finding was written billing.py had two webhook handlers, Stripe's and
    Razorpay's. Stripe has since been removed from the product entirely: Razorpay is
    the only payment gateway, and POST /api/billing/webhook/razorpay is the only
    webhook route. core/webhook_idempotency.py is still keyed on (provider, event_id)
    and treats `provider` as an opaque string it never validates, so the historical
    Stripe rows already in public.webhook_events stay readable and the claim layer
    itself needed no change.
"""
from __future__ import annotations

import ast
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend_app.core import webhook_idempotency as wi

REPO = Path(__file__).resolve().parents[1]
BILLING_SOURCE = REPO / "backend_app" / "routers" / "billing.py"

IDEMPOTENCY_CALLS = (
    "acquire_webhook_processing",
    "mark_webhook_completed",
    "release_webhook_processing",
)


class _FakeWebhookEventsTable:
    """An in-memory stand-in for the `webhook_events` table, reproducing just enough
    PostgREST + Postgres unique-constraint behaviour for these tests: INSERT raises an
    APIError-shaped exception with .code == '23505' on a (provider, event_id) that
    already exists; SELECT/UPDATE/DELETE filter by whatever .eq()/.lt() chain was
    built.
    """

    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}
        self._op = None
        self._filters: list[tuple[str, str, object]] = []
        self._payload: dict = {}

    # -- query-builder chain --------------------------------------------------
    def insert(self, payload):
        self._op = "insert"
        self._payload = dict(payload)
        return self

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = dict(payload)
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, field, value):
        self._filters.append(("eq", field, value))
        return self

    def lt(self, field, value):
        self._filters.append(("lt", field, value))
        return self

    def limit(self, _n):
        return self

    def _matches(self, row: dict, filters) -> bool:
        for kind, field, value in filters:
            if kind == "eq" and row.get(field) != value:
                return False
            if kind == "lt" and not (row.get(field) is not None and row[field] < value):
                return False
        return True

    async def execute(self):
        resp = MagicMock()
        filters = self._filters
        self._filters = []
        op = self._op
        self._op = None

        if op == "insert":
            key = (self._payload["provider"], self._payload["event_id"])
            if key in self.rows:
                err = Exception("duplicate key value violates unique constraint")
                err.code = "23505"
                raise err
            self.rows[key] = dict(self._payload)
            resp.data = [dict(self._payload)]
            return resp

        matching = [r for r in self.rows.values() if self._matches(r, filters)]
        if op == "select":
            resp.data = [dict(r) for r in matching]
        elif op == "update":
            for r in matching:
                r.update(self._payload)
            resp.data = [dict(r) for r in matching]
        elif op == "delete":
            for r in matching:
                key = (r["provider"], r["event_id"])
                del self.rows[key]
            resp.data = [dict(r) for r in matching]
        else:  # pragma: no cover - defensive
            resp.data = []
        return resp


@pytest.fixture
def fake_table():
    return _FakeWebhookEventsTable()


@pytest.fixture
def fake_client(fake_table):
    client = MagicMock()
    client.table.return_value = fake_table
    return client


def _patched(fake_client):
    return patch(
        "backend_app.core.dependencies.get_service_supabase_async",
        new=AsyncMock(return_value=fake_client),
    )


@pytest.mark.asyncio
async def test_a_fresh_delivery_acquires(fake_client):
    with _patched(fake_client):
        assert await wi.acquire_webhook_processing("razorpay", "evt_1") is True


@pytest.mark.asyncio
async def test_a_second_acquire_while_still_processing_and_fresh_is_refused(fake_client):
    with _patched(fake_client):
        assert await wi.acquire_webhook_processing("razorpay", "evt_1") is True
        assert await wi.acquire_webhook_processing("razorpay", "evt_1") is False


@pytest.mark.asyncio
async def test_a_completed_delivery_is_refused_forever_no_ttl(fake_client):
    with _patched(fake_client):
        assert await wi.acquire_webhook_processing("razorpay", "evt_1") is True
        await wi.mark_webhook_completed("razorpay", "evt_1")
        # Not a TTL -- there is nothing here that expires. Re-check repeatedly.
        for _ in range(3):
            assert await wi.acquire_webhook_processing("razorpay", "evt_1") is False


@pytest.mark.asyncio
async def test_a_stale_processing_row_is_reclaimed(fake_client, fake_table):
    with _patched(fake_client):
        assert await wi.acquire_webhook_processing("razorpay", "evt_1") is True

    # Simulate the crash: the row is still 'processing' but old.
    stale_at = datetime.now(timezone.utc) - timedelta(
        seconds=wi.PROCESSING_STALE_AFTER_SECONDS + 5
    )
    fake_table.rows[("razorpay", "evt_1")]["created_at"] = stale_at.isoformat()

    with _patched(fake_client):
        assert await wi.acquire_webhook_processing("razorpay", "evt_1") is True, (
            "a 'processing' row older than PROCESSING_STALE_AFTER_SECONDS must be "
            "reclaimable -- otherwise one crashed delivery blocks every future retry "
            "forever, the exact regression the old 60s Redis TTL prevented"
        )


@pytest.mark.asyncio
async def test_release_deletes_a_processing_row_so_retry_is_immediate(fake_client, fake_table):
    with _patched(fake_client):
        await wi.acquire_webhook_processing("razorpay", "evt_1")
        await wi.release_webhook_processing("razorpay", "evt_1")
        assert ("razorpay", "evt_1") not in fake_table.rows
        # Released -> an immediate retry acquires again, with no need to wait out the
        # staleness window.
        assert await wi.acquire_webhook_processing("razorpay", "evt_1") is True


@pytest.mark.asyncio
async def test_release_does_not_touch_an_already_completed_row(fake_client, fake_table):
    with _patched(fake_client):
        await wi.acquire_webhook_processing("razorpay", "evt_1")
        await wi.mark_webhook_completed("razorpay", "evt_1")
        await wi.release_webhook_processing("razorpay", "evt_1")
        assert fake_table.rows[("razorpay", "evt_1")]["status"] == "completed", (
            "release must only delete a 'processing' row -- a late failure in "
            "unrelated code after the durable record was already written must never "
            "un-mark a genuinely completed delivery"
        )


@pytest.mark.asyncio
async def test_not_configured_defers_to_redis_rather_than_refusing(monkeypatch):
    """DEV_MODE/local/test with no SUPABASE_SERVICE_ROLE_KEY: this durability layer
    is simply absent, so acquire must not treat every webhook as a duplicate."""
    with patch(
        "backend_app.core.dependencies.get_service_supabase_async",
        new=AsyncMock(return_value=None),
    ):
        assert await wi.acquire_webhook_processing("razorpay", "evt_1") is True


@pytest.mark.asyncio
async def test_configured_but_unreachable_fails_closed():
    broken_client = MagicMock()
    broken_table = MagicMock()
    broken_table.insert.side_effect = Exception("connection reset")
    broken_client.table.return_value = broken_table

    with patch(
        "backend_app.core.dependencies.get_service_supabase_async",
        new=AsyncMock(return_value=broken_client),
    ):
        assert await wi.acquire_webhook_processing("razorpay", "evt_1") is False


def _billing_functions() -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(BILLING_SOURCE.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _decorator_route_paths(fn) -> list[str]:
    """The string argument of each @router.<verb>("/path") decorator on fn."""
    paths = []
    for dec in fn.decorator_list:
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.args
            and isinstance(dec.args[0], ast.Constant)
            and isinstance(dec.args[0].value, str)
        ):
            paths.append(dec.args[0].value)
    return paths


def test_razorpay_is_the_only_webhook_route_billing_exposes():
    routes = {
        fn.name: path
        for fn in _billing_functions()
        for path in _decorator_route_paths(fn)
        if "webhook" in path
    }
    assert routes == {"razorpay_webhook": "/webhook/razorpay"}, (
        "Razorpay is the only payment gateway, so billing.py must expose exactly one "
        f"webhook route. Found: {routes}. A second gateway's webhook is a second way "
        "to grant entitlement and move money, and it would need its own durable claim "
        "and its own entry in this file before it could be trusted."
    )


def test_the_webhook_handler_claims_completes_and_releases():
    """All three calls must sit INSIDE the handler, each keyed to "razorpay".

    An AST walk over the handler's own body, not a count of substrings in the file.
    The count this replaces (`source.count(name) >= 3`) could not distinguish a call
    from the `from core.webhook_idempotency import ...` line that names all three, so
    it would have read "wired" for a file that imported the three and called none of
    them; and its threshold of 3 silently encoded "two handlers exist" as arithmetic,
    so deleting Stripe's handler broke the test without anything being wrong.
    """
    handlers = [fn for fn in _billing_functions() if fn.name == "razorpay_webhook"]
    assert len(handlers) == 1, f"expected one razorpay_webhook, found {len(handlers)}"

    providers_by_call: dict[str, list[object]] = {}
    for node in ast.walk(handlers[0]):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in IDEMPOTENCY_CALLS:
            continue
        first = node.args[0] if node.args else None
        providers_by_call.setdefault(node.func.id, []).append(
            first.value if isinstance(first, ast.Constant) else ast.dump(first)
        )

    for name in IDEMPOTENCY_CALLS:
        assert name in providers_by_call, (
            f"{name} is never called inside razorpay_webhook(). The durable claim in "
            "public.webhook_events is only load-bearing if the handler actually "
            "acquires it, marks it completed on success, and releases it on failure -- "
            "importing it is not using it."
        )
        passed = set(providers_by_call[name])
        assert passed == {"razorpay"}, (
            f"{name} is called with provider argument(s) {sorted(map(str, passed))}. "
            'Every call site in this handler must key its row to the literal "razorpay": '
            "a mismatched provider writes a row under a key no redelivery will ever "
            "collide with, which silently disables idempotency instead of failing."
        )
