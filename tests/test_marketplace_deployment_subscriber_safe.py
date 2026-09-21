"""
tests/test_marketplace_deployment_subscriber_safe.py

Focused tests for ``routers/library.deploy_marketplace_strategy`` after task 17.3 repointed it
at the subscriber-safe path.

WHAT CHANGED, AND THEREFORE WHAT THESE ASSERT
---------------------------------------------
Before 17.3 the handler answered a subscriber by INSERTing a ``strategies`` row that carried the
owner's ``buy_logic``, ``sell_logic``, ``risk``, ``indicators`` and ``ml_model_path`` into a row
the SUBSCRIBER owned, gated only on ``check_deployment_permission``. That is a full
Protected_Logic transfer (Requirement 7.1) behind an admission check that trusted a stored
``status`` label and treated a missing expiry as perpetual.

It now:

1. accepts from the subscriber only ``symbol``, ``timeframe``, ``capital`` and
   ``session_options``; any other field is a 422 that echoes **no** supplied value
   (Requirements 7.5, 7.6);
2. admits on the single ``entitlement_resolver.resolve`` decision — with
   ``EntitlementReadFailed`` surfacing as ``MARKETPLACE_READ_FAILED`` (503) rather than as a
   refusal (Requirements 7.10, 11.10, 30.5);
3. writes a ``strategy_deployments`` row bound to the OWNER's ``strategy_id`` / ``version_id``
   with ``marketplace_listing_id`` recorded and ``user_id`` = the SUBSCRIBER, and writes
   **nothing** to ``strategies`` (Requirements 7.1, 7.5);
4. audits every refusal as ``MARKETPLACE_ACCESS_REFUSED`` with no Protected_Logic
   (Requirement 7.12).

HOW
---
The handler is called directly with a fake ``Request`` and a fake Supabase double, the same
injected-client pattern ``tests/test_entitlement_resolver.py`` uses, so the whole path is
checkable without a database or a TestClient. The double records every table it was asked to
write, which is what makes "no ``strategies`` row is created" an assertion rather than a claim.

Requirements: 7.1, 7.5, 7.6, 7.8, 7.12, 11.10.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.marketplace import COLUMN_CONTRACT
from backend_app.backend.marketplace import entitlement_resolver as er
from backend_app.backend.marketplace.errors import (
    MARKETPLACE_NOT_SUBSCRIBED,
    MARKETPLACE_READ_FAILED,
    MARKETPLACE_STRATEGY_UNAVAILABLE,
    MARKETPLACE_SUBSCRIPTION_EXPIRED,
    MarketplaceError,
)
from backend_app.routers import library as lib

NOW_FUTURE = datetime.now(timezone.utc) + timedelta(days=10)
NOW_PAST = datetime.now(timezone.utc) - timedelta(days=10)

SUBSCRIBER_ID = str(uuid4())
OWNER_ID = str(uuid4())
LISTING_ID = str(uuid4())
OWNER_STRATEGY_ID = str(uuid4())
OWNER_VERSION_ID = str(uuid4())
OWNER_VERSION_LABEL = "v3.1"

#: The five columns that carry the owner's Protected_Logic. The pre-17.3 handler copied every
#: one of them into a subscriber-owned ``strategies`` row; none of them may appear in any
#: payload this path writes, nor in any response it returns.
PROTECTED_LOGIC_COLUMNS = (
    "buy_logic",
    "sell_logic",
    "risk",
    "indicators",
    "ml_model_path",
)


# ══════════════════════════════════════════════════════════════════════════
# Doubles
# ══════════════════════════════════════════════════════════════════════════


class _Resp:
    def __init__(self, data: Any):
        self.data = data


class _Query:
    """A recording query builder mimicking the supabase-py fluent chain."""

    def __init__(self, table: str, client: "FakeSupabase"):
        self.table_name = table
        self.client = client
        self.filters: List[tuple] = []
        self.cols: Optional[str] = None
        self.payload: Optional[Any] = None
        self.op = "select"

    def select(self, cols):
        self.cols = cols
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def single(self):
        return self

    def execute(self):
        return self.client._execute(self)


class FakeSupabase:
    """Scripted reads plus a recording writer."""

    def __init__(self, *, listing_rows=None, version_rows=None, raise_on=None):
        self._listing_rows = listing_rows if listing_rows is not None else []
        self._version_rows = version_rows if version_rows is not None else []
        self.raise_on = set(raise_on or ())
        self.calls: List[_Query] = []

    def table(self, name):
        return _Query(name, self)

    def _execute(self, q: _Query):
        self.calls.append(q)
        if q.table_name in self.raise_on:
            raise RuntimeError(f"read failed on {q.table_name}")
        if q.op == "insert":
            row = dict(q.payload)
            row.setdefault("id", str(uuid4()))
            return _Resp([row])
        if q.table_name == "library_strategies":
            return _Resp(list(self._listing_rows))
        if q.table_name == "strategy_versions":
            return _Resp(list(self._version_rows))
        return _Resp([])

    # -- assertions helpers ------------------------------------------------
    def writes_to(self, table: str) -> List[_Query]:
        return [c for c in self.calls if c.op in ("insert", "update") and c.table_name == table]

    def all_writes(self) -> List[_Query]:
        return [c for c in self.calls if c.op in ("insert", "update")]


class FakeRequest:
    """The two things the handler uses off ``Request``: ``json()`` and nothing else."""

    def __init__(self, body: Any = None, *, malformed: bool = False):
        self._body = body
        self._malformed = malformed

    async def json(self):
        if self._malformed:
            raise ValueError("not json")
        return self._body


class _RecordedAudit:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeAuditLogger:
    def __init__(self):
        self.entries: List[_RecordedAudit] = []

    async def log(self, action, **kwargs):
        self.entries.append(_RecordedAudit(action=action, **kwargs))
        return None


# ══════════════════════════════════════════════════════════════════════════
# Fixtures / row builders
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _rate_limiter_off(monkeypatch):
    """Stand the rate-limit decorator down for the duration of each direct handler call.

    ``deploy_marketplace_strategy`` carries ``@limiter.limit("10/60second",
    key_func=caller_or_address)`` (task 33.2). ``slowapi``'s wrapper raises
    ``parameter `request` must be an instance of starlette.requests.Request`` before the handler
    body runs whenever ``limiter.enabled`` is true, so a test that calls the handler as a
    function — which is what makes the injected Supabase double and the recorded writes possible
    — cannot get past the decorator at all.

    Disabling rather than feeding it a real ``Request`` is deliberate: with the limit armed, the
    twenty-odd calls this module makes under one caller key would exhaust 10/60s part-way
    through the module and turn later tests into 429s, i.e. the unit tests' answers would depend
    on how many tests ran before them.

    No rate-limit coverage is lost. The decorator's presence and its exact limit are asserted
    from the limiter's own registry by
    ``tests/test_task_33_2_rate_limit_keys.py`` (``"deploy_marketplace_strategy": "10 per 60
    second"``), and enforcement is exercised there against the real application.

    ``monkeypatch`` restores the previous value after every test, so this leaks nothing into the
    rest of the suite.
    """
    from backend_app.core.rate_limit import limiter

    monkeypatch.setattr(limiter, "enabled", False, raising=False)


def _listing_row(*, subscription=None, submission_state="PUBLISHED", author_id=OWNER_ID):
    return {
        "id": LISTING_ID,
        "author_id": author_id,
        "source_strategy_id": OWNER_STRATEGY_ID,
        "source_cloning_enabled": False,
        "marketplace_submissions": (
            [] if submission_state is None else [{"submission_state": submission_state}]
        ),
        "library_subscriptions": [] if subscription is None else [subscription],
    }


def _subscription(*, status="active", period_expiry=NOW_FUTURE, user_id=SUBSCRIBER_ID):
    return {
        "id": str(uuid4()),
        "user_id": user_id,
        "status": status,
        "period_expiry": (
            period_expiry.isoformat() if isinstance(period_expiry, datetime) else period_expiry
        ),
    }


def _version_rows():
    return [
        {
            "id": OWNER_VERSION_ID,
            "strategy_id": OWNER_STRATEGY_ID,
            "version": OWNER_VERSION_LABEL,
            "is_draft": False,
        }
    ]


@pytest.fixture
def audit(monkeypatch):
    """Route the handler's Audit_Log writes into a recorder."""
    recorder = FakeAuditLogger()
    monkeypatch.setattr(lib, "get_strategy_audit_logger", lambda: recorder)
    return recorder


@pytest.fixture
def entitled_client(monkeypatch):
    """A double whose Listing entitles ``SUBSCRIBER_ID`` with an ACTIVE, unexpired Subscription."""
    client = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription())],
        version_rows=_version_rows(),
    )
    monkeypatch.setattr(lib, "_build_service_client", lambda: client)
    return client


def _deploy(request_body=None, *, malformed=False):
    return asyncio.run(
        lib.deploy_marketplace_strategy(
            FakeRequest(request_body, malformed=malformed),
            LISTING_ID,
            user={"id": SUBSCRIBER_ID},
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. The write is a deployment bound to the owner's version, not a logic copy
#    (Requirements 7.1, 7.5)
# ══════════════════════════════════════════════════════════════════════════


def test_entitled_subscriber_gets_a_deployment_bound_to_the_owners_version(
    entitled_client, audit
):
    result = _deploy({"symbol": "BTC/USDT", "timeframe": "1h", "capital": 500})

    inserts = entitled_client.writes_to("strategy_deployments")
    assert len(inserts) == 1, "exactly one strategy_deployments row is written"
    row = inserts[0].payload

    # user_id is the SUBSCRIBER; strategy_id / version_id are the OWNER's.
    assert row["user_id"] == SUBSCRIBER_ID
    assert row["strategy_id"] == OWNER_STRATEGY_ID
    assert row["version_id"] == OWNER_VERSION_ID

    # The Listing is recorded, which is what distinguishes this from a first-party deployment.
    assert row["marketplace_listing_id"] == LISTING_ID

    # `version` is the owner's LABEL, not the version UUID: the column is VARCHAR(20).
    assert row["version"] == OWNER_VERSION_LABEL
    assert len(row["version"]) <= 20

    # The subscriber's own execution parameters land where the schema has room for them.
    assert row["exchange_symbol"] == "BTC/USDT"
    assert row["initial_capital"] == 500

    assert result["granted_via"] == er.EntitlementReason.SUBSCRIBED.value
    assert result["library_id"] == LISTING_ID
    assert result["deployment_id"]


def test_no_strategies_row_is_created_and_no_logic_column_is_written(entitled_client, audit):
    _deploy({"symbol": "ETH/USDT"})

    assert entitled_client.writes_to("strategies") == [], (
        "the subscriber-safe path must create no `strategies` row — that INSERT is what "
        "carried the owner's buy_logic/sell_logic/risk/indicators/ml_model_path into a "
        "subscriber-owned row (Requirement 7.1)"
    )
    for write in entitled_client.all_writes():
        for column in PROTECTED_LOGIC_COLUMNS:
            assert column not in write.payload, (
                f"{column} appears in a payload written to {write.table_name}"
            )


def test_the_response_discloses_no_owner_identifier_or_logic(entitled_client, audit):
    result = _deploy({})

    forbidden = set(PROTECTED_LOGIC_COLUMNS) | {
        "strategy_id",
        "version_id",
        "author_id",
        "owner_id",
        "source_strategy_id",
        "blueprint",
        "execution_graph",
    }
    assert forbidden.isdisjoint(result.keys()), (
        f"response leaks server-side identifiers: {sorted(forbidden & set(result))}"
    )


def test_the_owner_version_read_selects_no_protected_logic_column(entitled_client, audit):
    _deploy({})

    version_reads = [
        c for c in entitled_client.calls
        if c.table_name == "strategy_versions" and c.op == "select"
    ]
    assert version_reads, "the owner's version label must be read for the NOT NULL column"
    for read in version_reads:
        assert "blueprint" not in (read.cols or "")
        assert "execution_graph" not in (read.cols or "")


# ══════════════════════════════════════════════════════════════════════════
# 2. Only the four execution parameters are accepted (Requirements 7.5, 7.6)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "field, value",
    [
        ("buy_logic", {"_nodes": [{"type": "ml", "id": "secret-node"}]}),
        ("strategy_definition", {"graph": "secret-graph"}),
        ("graph", "secret-graph"),
        ("compiled_plan", {"dag_hash": "deadbeefdeadbeef"}),
        ("version_id", "11111111-1111-1111-1111-111111111111"),
        ("owner_id", OWNER_ID),
        ("tenant_id", "tenant-secret"),
        ("subscription_id", "sub-secret"),
    ],
)
def test_any_field_beyond_the_four_is_a_422_echoing_no_value(
    entitled_client, audit, field, value
):
    with pytest.raises(HTTPException) as caught:
        _deploy({"symbol": "BTC/USDT", field: value})

    assert caught.value.status_code == 422
    detail = caught.value.detail
    assert detail["error"] == lib.DEPLOY_REQUEST_INVALID
    assert field in detail["unexpected_fields"]

    # Requirement 7.6: the error names the field and echoes NO supplied value.
    rendered = repr(detail)
    for token in ("secret-node", "secret-graph", "deadbeefdeadbeef", "tenant-secret",
                  "sub-secret", OWNER_ID, "11111111-1111-1111-1111-111111111111"):
        assert token not in rendered, f"the 422 body echoed {token!r}"

    # Nothing was created, and no admission read was even issued.
    assert entitled_client.all_writes() == []
    assert entitled_client.calls == []


def test_the_four_execution_parameters_are_accepted(entitled_client, audit):
    result = _deploy(
        {
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "capital": 1000,
            "session_options": {"speed": "realtime"},
        }
    )
    assert result["deployment_id"]


def test_an_absent_body_is_a_valid_all_optional_request(entitled_client, audit):
    result = _deploy(None)
    assert result["deployment_id"]


def test_a_non_object_body_is_a_422_and_creates_nothing(entitled_client, audit):
    with pytest.raises(HTTPException) as caught:
        _deploy(["not", "an", "object"])
    assert caught.value.status_code == 422
    assert entitled_client.all_writes() == []


def test_the_422_is_audited_with_no_supplied_value(entitled_client, audit):
    with pytest.raises(HTTPException):
        _deploy({"buy_logic": {"_nodes": ["secret-node"]}})

    assert len(audit.entries) == 1, "Requirement 7.12: one refusal, one Audit_Log entry"
    entry = audit.entries[0].kwargs
    assert entry["actor_id"] == SUBSCRIBER_ID
    assert entry["resource_id"] == LISTING_ID
    assert entry["reason"] == lib.DEPLOY_REQUEST_INVALID
    assert entry["metadata"]["operation"] == "deploy"
    assert entry["metadata"]["unexpected_fields"] == ["buy_logic"]
    assert "secret-node" not in repr(entry), "the audit entry carries no supplied value"


# ══════════════════════════════════════════════════════════════════════════
# 3. Admission is the resolver's decision, and only the resolver's
#    (Requirements 7.10, 11.10)
# ══════════════════════════════════════════════════════════════════════════


def test_no_subscription_is_the_resolvers_not_subscribed_code(monkeypatch, audit):
    client = FakeSupabase(listing_rows=[_listing_row()], version_rows=_version_rows())
    monkeypatch.setattr(lib, "_build_service_client", lambda: client)

    with pytest.raises(MarketplaceError) as caught:
        _deploy({})

    assert caught.value.code == MARKETPLACE_NOT_SUBSCRIBED
    assert client.all_writes() == []


def test_an_expired_period_blocks_a_new_deployment_even_when_status_still_reads_active(
    monkeypatch, audit
):
    """Requirement 11.10 / 11.7: the resolver's sweep-independent expiry is the whole gate.

    The row still reads ``status = 'active'`` — exactly the state the old
    ``check_deployment_permission`` admitted, and the state a dead expiry sweep leaves behind.
    """
    client = FakeSupabase(
        listing_rows=[
            _listing_row(subscription=_subscription(status="active", period_expiry=NOW_PAST))
        ],
        version_rows=_version_rows(),
    )
    monkeypatch.setattr(lib, "_build_service_client", lambda: client)

    with pytest.raises(MarketplaceError) as caught:
        _deploy({})

    assert caught.value.code == MARKETPLACE_SUBSCRIPTION_EXPIRED
    assert client.all_writes() == [], "a lapsed Subscription creates no deployment"


def test_a_null_period_expiry_is_not_a_perpetual_subscription(monkeypatch, audit):
    """The behaviour ``check_deployment_permission``'s "perpetual subscription" branch had."""
    client = FakeSupabase(
        listing_rows=[
            _listing_row(subscription=_subscription(status="active", period_expiry=None))
        ],
        version_rows=_version_rows(),
    )
    monkeypatch.setattr(lib, "_build_service_client", lambda: client)

    with pytest.raises(MarketplaceError) as caught:
        _deploy({})

    assert caught.value.code == MARKETPLACE_SUBSCRIPTION_EXPIRED
    assert client.all_writes() == []


def test_an_unresolvable_owner_version_is_a_409_not_a_403(monkeypatch, audit):
    client = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription())],
        version_rows=[],
    )
    monkeypatch.setattr(lib, "_build_service_client", lambda: client)

    with pytest.raises(MarketplaceError) as caught:
        _deploy({})

    assert caught.value.code == MARKETPLACE_STRATEGY_UNAVAILABLE
    assert caught.value.http_status == 409
    assert client.all_writes() == []


def test_a_refusal_is_audited_with_the_resolvers_reason_and_no_logic(monkeypatch, audit):
    client = FakeSupabase(listing_rows=[_listing_row()], version_rows=_version_rows())
    monkeypatch.setattr(lib, "_build_service_client", lambda: client)

    with pytest.raises(MarketplaceError):
        _deploy({})

    assert len(audit.entries) == 1
    entry = audit.entries[0].kwargs
    assert entry["actor_id"] == SUBSCRIBER_ID
    assert entry["resource_id"] == LISTING_ID
    assert entry["reason"] == MARKETPLACE_NOT_SUBSCRIBED
    assert entry["metadata"]["entitlement_reason"] == er.EntitlementReason.NOT_SUBSCRIBED.value
    rendered = repr(entry)
    for column in PROTECTED_LOGIC_COLUMNS:
        assert column not in rendered


def test_the_handler_no_longer_consults_check_deployment_permission(entitled_client, monkeypatch):
    """There is ONE admission decision. A second check would be a second answer to disagree with.

    ``check_deployment_permission`` is still the implementation of
    ``GET /{library_id}/deploy/check`` (task 18.2 owns its "no expiry means perpetual" branch),
    so it is not removed — but the POST path must not call it.
    """
    called: List[Any] = []
    monkeypatch.setattr(
        lib,
        "check_deployment_permission",
        lambda *a, **k: called.append((a, k)) or {"has_permission": True},
    )
    monkeypatch.setattr(lib, "get_strategy_audit_logger", lambda: FakeAuditLogger())

    _deploy({})

    assert called == [], (
        "deploy_marketplace_strategy must admit on entitlement_resolver.resolve alone"
    )


# ══════════════════════════════════════════════════════════════════════════
# 4. A read that did not complete is a 503, never a refusal (Requirement 30.5)
# ══════════════════════════════════════════════════════════════════════════


def test_a_failed_admission_read_is_503_read_failed_not_a_403(monkeypatch, audit):
    client = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription())],
        version_rows=_version_rows(),
        raise_on={"library_strategies"},
    )
    monkeypatch.setattr(lib, "_build_service_client", lambda: client)

    with pytest.raises(MarketplaceError) as caught:
        _deploy({})

    assert caught.value.code == MARKETPLACE_READ_FAILED
    assert caught.value.http_status == 503
    assert client.all_writes() == []
    assert audit.entries == [], (
        "a broken read is not an access refusal, so it writes no "
        "MARKETPLACE_ACCESS_REFUSED entry"
    )


def test_a_failed_version_label_read_is_503_read_failed(monkeypatch, audit):
    client = FakeSupabase(
        listing_rows=[_listing_row(subscription=_subscription())],
        version_rows=_version_rows(),
        raise_on={"strategy_versions"},
    )
    monkeypatch.setattr(lib, "_build_service_client", lambda: client)

    with pytest.raises(MarketplaceError) as caught:
        _deploy({})

    assert caught.value.code == MARKETPLACE_READ_FAILED
    assert client.all_writes() == []


# ══════════════════════════════════════════════════════════════════════════
# 5. The written columns sit inside the COLUMN_CONTRACT manifest (task 11.6)
# ══════════════════════════════════════════════════════════════════════════


def test_the_deployment_manifest_entry_covers_every_column_the_handler_writes(
    entitled_client, audit
):
    """The 42703 / PGRST204 guard: what the handler writes is what the manifest declares.

    ``tests/test_marketplace_paper_schema_contract.py`` asserts every manifest pair is created
    by a migration; this asserts the handler stays inside the manifest, so the two together
    cover "the handler writes only columns the database has".
    """
    entry = COLUMN_CONTRACT.get("marketplace_deployment")
    assert entry is not None, "COLUMN_CONTRACT has no 'marketplace_deployment' entry"

    _deploy({"symbol": "BTC/USDT", "capital": 250})

    declared: Dict[str, set] = {
        table: set(columns) for table, columns in entry["tables"].items()
    }
    for write in entitled_client.all_writes():
        allowed = declared.get(write.table_name)
        assert allowed is not None, (
            f"the handler writes {write.table_name}, which the manifest does not name"
        )
        undeclared = set(write.payload) - allowed - {"id"}
        assert not undeclared, (
            f"columns written to {write.table_name} but absent from the manifest: "
            f"{sorted(undeclared)}"
        )

    assert "marketplace_listing_id" in declared["strategy_deployments"]
