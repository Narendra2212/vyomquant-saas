"""
tests/test_mkt05_marketplace_live_deploy.py

MKT-05: the live marketplace-deploy path.

Spec: ``docs/launch_readiness/REMEDIATION_LEDGER.json``, finding MKT-05. The subscriber-safe
PAPER marketplace-deploy path (``POST /api/library/{id}/deploy``) already existed and is
covered by ``tests/test_marketplace_deployment_subscriber_safe.py``. This file covers the NEW
capability this finding adds: the LIVE path, built by extending
``backend_app.backend.deployment_binding.evaluate_binding`` — the SAME gate a first-party live
deploy (``strategy_operations.deploy_version``) runs through — rather than re-implementing it.

THE ONE SUBTLETY THIS FILE EXISTS TO PROVE
-------------------------------------------
``deployment_binding._active_model_rows`` filters a version's model rows to those owned by the
DEPLOYING user. For a first-party deploy that is correct: the deploying user trained the model.
For a marketplace deploy the deploying user is the SUBSCRIBER, but the model was trained by, and
is owned by, the LISTING OWNER. ``evaluate_binding``'s new ``model_owner_user_id`` parameter is
the fix. ``test_a_subscriber_can_live_deploy_a_listing_with_an_ml_node`` is written FIRST and
is the one a naive implementation (passing the subscriber's id where the owner's belongs) gets
wrong SILENTLY: it does not crash, it raises ``MODEL_NODE_NOT_BOUND`` as if the owner's
already-verified model did not exist.

Nothing under test is mocked except the PostgREST client (this environment has no instance of
one) and the asset-universe cache (a process-local cache, seeded exactly as
``tests/test_task_8_2_deployment_binding.py`` seeds it). The compiler, the registry, the real
artifact store, the checksum routine and the feature-schema comparison for the ML-node test are
all the real ones — reusing ``tests/test_task_8_6_model_readiness_gates.py``'s own plan/model
builders so a model-gate defect cannot pass here by having a laxer double than that file's own
suite already holds.
"""

from __future__ import annotations

import json
import os
import sys
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import asset_universe as au
from backend_app.backend import deployment_binding as db_binding
from backend_app.backend import model_versioning as MV
from backend_app.backend.marketplace import entitlement_resolver as er
from backend_app.backend.strategy_builder import LIFECYCLE_READY
from backend_app.routers import library as lib
from tests.test_task_8_6_model_readiness_gates import (
    ARTIFACT_BYTES,
    build_plan,
    model_row,
)

SUBSCRIBER_ID = str(uuid4())
OWNER_ID = str(uuid4())
OTHER_USER_ID = str(uuid4())
LISTING_ID = str(uuid4())
OWNER_STRATEGY_ID = str(uuid4())
OWNER_VERSION_ID = str(uuid4())
OWNER_VERSION_LABEL = "v3.1"

SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
MARKET_TYPE = "spot"
VENUE = "binance"

SUBSCRIBER_ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_USER_ACCOUNT_ID = "22222222-2222-4222-8222-222222222222"
SUBSCRIBER_RISK_ID = "33333333-3333-4333-8333-333333333333"
OTHER_USER_RISK_ID = "44444444-4444-4444-8444-444444444444"


# ══════════════════════════════════════════════════════════════════════════
# A minimal, hand-built compiled plan (no ML node) — the same shape
# tests/test_task_8_2_deployment_binding.py's own `_plan()` builds, which needs no real
# registry or compiler: a plan's market comes from its DATA node's own params (SB-06), and
# `resolve_binding_market` reads nothing else.
# ══════════════════════════════════════════════════════════════════════════


def _plan_no_ml(dag_hash="deadbeefcafe0001"):
    return {
        "dag_hash": dag_hash,
        "execution_order": ["n_data"],
        "data_nodes": ["n_data"],
        "node_index": {
            "n_data": {
                "id": "n_data",
                "block_id": "data.market_data",
                "category": "data",
                "params": {
                    "symbol": SYMBOL,
                    "timeframe": TIMEFRAME,
                    "market_type": MARKET_TYPE,
                },
            }
        },
    }


def _owner_version_row(*, compiled_plan, dag_hash, version_id=OWNER_VERSION_ID):
    return {
        "id": version_id,
        "strategy_id": OWNER_STRATEGY_ID,
        "version": OWNER_VERSION_LABEL,
        "lifecycle_state": LIFECYCLE_READY,
        "dag_hash": dag_hash,
        "compiled_plan": compiled_plan,
        "is_current": True,
        "is_draft": False,
    }


def _universe(symbol=SYMBOL, market_type=MARKET_TYPE, venues=(VENUE,)):
    return au.AssetUniverse(
        assets=[
            au.AssetRef(
                symbol=symbol,
                base=symbol.split("/")[0],
                quote=symbol.split("/")[-1],
                market_type=market_type,
                active=True,
                price_precision=2,
                amount_precision=6,
                min_notional=10.0,
                min_amount=0.0001,
                available_on=tuple(venues),
                precision_source=venues[0] if venues else None,
            )
        ],
        generated_at=time.time(),
        exchanges=list(venues),
    )


@pytest.fixture(autouse=True)
def _clean_module_state():
    db_binding.reset_binding_column_support()
    db_binding.reset_venue_timeframe_cache()
    au.reset_asset_universe_state_for_tests()
    MV.reset_artifact_store()
    yield
    db_binding.reset_binding_column_support()
    db_binding.reset_venue_timeframe_cache()
    au.reset_asset_universe_state_for_tests()
    MV.reset_artifact_store()


@pytest.fixture
def seeded_universe():
    au._local_universe = _universe()  # noqa: SLF001 - reset_..._for_tests is the inverse
    return au._local_universe  # noqa: SLF001


@pytest.fixture(autouse=True)
def _rate_limiter_off(monkeypatch):
    """Same seam ``test_marketplace_deployment_subscriber_safe.py`` uses — see that file's
    own fixture docstring for why the decorator must be disarmed for a direct handler call."""
    from backend_app.core.rate_limit import limiter

    monkeypatch.setattr(limiter, "enabled", False, raising=False)


# ══════════════════════════════════════════════════════════════════════════
# A generic, filter-honouring PostgREST double — every table this path touches lives in one
# instance, unlike test_marketplace_deployment_subscriber_safe.py's narrower one, because
# MKT-05's live path reads exchange_keys/risk_settings/model_versions too.
# ══════════════════════════════════════════════════════════════════════════


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, parent, table):
        self._parent = parent
        self._table = table
        self._filters = {}
        self._mode = "select"
        self._payload = None

    def select(self, *_a, **_kw):
        return self

    def insert(self, payload, *_a, **_kw):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload, *_a, **_kw):
        self._mode = "update"
        self._payload = payload
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def limit(self, *_a, **_kw):
        return self

    def execute(self):
        self._parent.calls.append((self._table, self._mode, dict(self._filters)))
        if self._table in self._parent.absent_tables:
            raise RuntimeError(f'relation "{self._table}" does not exist (42P01)')
        if self._mode == "insert":
            row = dict(self._payload)
            row.setdefault("id", str(uuid4()))
            self._parent.inserts.append((self._table, dict(row)))
            self._parent.rows.setdefault(self._table, []).append(row)
            return _Result([row])
        if self._mode == "update":
            self._parent.updates.append((self._table, dict(self._payload), dict(self._filters)))
            return _Result([])
        rows = [
            row
            for row in self._parent.rows.get(self._table, [])
            if all(str(row.get(k)) == str(v) for k, v in self._filters.items())
        ]
        return _Result(rows)


class FakeSupabase:
    def __init__(self, rows=None, absent_tables=()):
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.absent_tables = set(absent_tables)
        self.calls = []
        self.inserts = []
        self.updates = []

    def table(self, name):
        return _Query(self, name)

    def writes_to(self, table):
        return [p for (t, p) in self.inserts if t == table]


class FakeRequest:
    def __init__(self, body=None):
        self._body = body

    async def json(self):
        return self._body


class FakeAuditLogger:
    def __init__(self):
        self.entries = []

    async def log(self, action, **kwargs):
        self.entries.append({"action": action, **kwargs})
        return None


def _listing_row(owner_id=OWNER_ID):
    return {
        "id": LISTING_ID,
        "author_id": owner_id,
        "source_strategy_id": OWNER_STRATEGY_ID,
        "source_cloning_enabled": False,
        "marketplace_submissions": [{"submission_state": "PUBLISHED"}],
        "library_subscriptions": [
            {
                "id": str(uuid4()),
                "user_id": SUBSCRIBER_ID,
                "status": "active",
                "period_expiry": None,  # overwritten below for a real future expiry
            }
        ],
    }


def _subscribed_listing_row(owner_id=OWNER_ID):
    from datetime import datetime, timedelta, timezone

    row = _listing_row(owner_id)
    row["library_subscriptions"][0]["period_expiry"] = (
        datetime.now(timezone.utc) + timedelta(days=10)
    ).isoformat()
    return row


def _base_rows(*, owner_id=OWNER_ID, version_row, owner_model_rows=None):
    return {
        "library_strategies": [_subscribed_listing_row(owner_id)],
        "strategy_versions": [version_row],
        "exchange_keys": [
            {"id": SUBSCRIBER_ACCOUNT_ID, "user_id": SUBSCRIBER_ID, "exchange_id": VENUE},
            {"id": OTHER_USER_ACCOUNT_ID, "user_id": OTHER_USER_ID, "exchange_id": VENUE},
        ],
        "exchange_connections": [],
        "risk_settings": [
            {"id": SUBSCRIBER_RISK_ID, "user_id": SUBSCRIBER_ID, "max_drawdown_pct": 0.2},
            {"id": OTHER_USER_RISK_ID, "user_id": OTHER_USER_ID, "max_drawdown_pct": 0.2},
        ],
        "model_versions": list(owner_model_rows or []),
        "strategy_deployments": [],
    }


def _patch_service_client(monkeypatch, client):
    monkeypatch.setattr(lib, "_build_service_client", lambda: client)
    monkeypatch.setattr(lib, "get_strategy_audit_logger", lambda: FakeAuditLogger())
    return client


def _deploy(body, *, client=None):
    import asyncio

    return asyncio.run(
        lib.deploy_marketplace_strategy(
            FakeRequest(body), LISTING_ID, user={"id": SUBSCRIBER_ID}
        )
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. A subscriber can live-deploy a listing WITH an ML node — written FIRST, the one a naive
#    `model_owner_user_id` implementation gets wrong silently (MODEL_NODE_NOT_BOUND).
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def ml_registry():
    from backend_app.backend.strategy_dag import registry as registry_module

    return registry_module.build_registry()


@pytest.fixture
def ml_store(tmp_path):
    local = MV.LocalArtifactStore(tmp_path / "artifacts")
    MV.register_artifact_store(local)
    return local


def test_a_subscriber_can_live_deploy_a_listing_with_an_ml_node(
    monkeypatch, seeded_universe, ml_registry, ml_store
):
    """The fix: the model row is owned by the OWNER, never the subscriber, and the deploy
    still succeeds — proving `model_owner_user_id` is threaded all the way from the router
    into `assert_models_verified`'s `_active_model_rows` ownership filter."""
    uri = ml_store.put("owner/strategy/version/node/model.joblib", ARTIFACT_BYTES)
    checksum = ml_store.checksum(uri)
    plan, ml_node_id = build_plan(ml_registry, lags=(1, 2))

    version_row = _owner_version_row(
        compiled_plan=json.loads(json.dumps(plan.to_dict())), dag_hash=plan.dag_hash
    )
    owner_model_row = model_row(
        uri, checksum, node_id=ml_node_id, feature_names=("lag_1", "lag_2"),
        user_id=OWNER_ID,  # owned by the LISTING OWNER, never the subscriber
        version_id=OWNER_VERSION_ID,
    )

    client = FakeSupabase(
        _base_rows(version_row=version_row, owner_model_rows=[owner_model_row])
    )
    _patch_service_client(monkeypatch, client)

    result = _deploy({"mode": "live", "exchange_account_id": SUBSCRIBER_ACCOUNT_ID})

    assert result["mode"] == "live"
    (row,) = client.writes_to("strategy_deployments")
    assert row["mode"] == "live"
    assert row["environment"] == "live"
    assert row["exchange_account_id"] == SUBSCRIBER_ACCOUNT_ID
    assert row["user_id"] == SUBSCRIBER_ID
    assert row["strategy_id"] == OWNER_STRATEGY_ID
    assert row["version_id"] == OWNER_VERSION_ID
    assert row["marketplace_listing_id"] == LISTING_ID


def test_without_the_owner_identity_fix_the_same_deploy_would_be_refused(
    monkeypatch, seeded_universe, ml_registry, ml_store
):
    """Negative control: calling the gate with the SUBSCRIBER as the model-owner identity
    (the naive, wrong implementation) reproduces MODEL_NODE_NOT_BOUND for the owner's own,
    perfectly valid, active model version — demonstrating the defect `model_owner_user_id`
    fixes, directly against `evaluate_binding`."""
    uri = ml_store.put("owner/strategy/version/node/model.joblib", ARTIFACT_BYTES)
    checksum = ml_store.checksum(uri)
    plan, ml_node_id = build_plan(ml_registry, lags=(1, 2))
    version_row = _owner_version_row(
        compiled_plan=json.loads(json.dumps(plan.to_dict())), dag_hash=plan.dag_hash
    )
    owner_model_row = model_row(
        uri, checksum, node_id=ml_node_id, feature_names=("lag_1", "lag_2"), user_id=OWNER_ID,
        version_id=OWNER_VERSION_ID,
    )
    client = FakeSupabase(
        _base_rows(version_row=version_row, owner_model_rows=[owner_model_row])
    )

    import asyncio

    request = db_binding.BindingRequest(mode="live", exchange_account_id=SUBSCRIBER_ACCOUNT_ID)
    with pytest.raises(db_binding.DeployRejected) as exc:
        asyncio.run(
            db_binding.evaluate_binding(
                client,
                {"id": SUBSCRIBER_ID},
                version_row,
                OWNER_STRATEGY_ID,
                OWNER_VERSION_LABEL,
                request,
                model_owner_user_id=SUBSCRIBER_ID,  # the bug: subscriber instead of owner
            )
        )
    assert exc.value.code == "MODEL_NODE_NOT_BOUND"

    # And the SAME call, with the owner's identity, succeeds.
    binding = asyncio.run(
        db_binding.evaluate_binding(
            client,
            {"id": SUBSCRIBER_ID},
            version_row,
            OWNER_STRATEGY_ID,
            OWNER_VERSION_LABEL,
            request,
            model_owner_user_id=OWNER_ID,
        )
    )
    assert binding.mode == "live"


# ══════════════════════════════════════════════════════════════════════════
# 2. A subscriber can live-deploy a listing with NO ML node, using their own account/risk
#    config — asserts the full row shape MKT-05 promises.
# ══════════════════════════════════════════════════════════════════════════


def test_a_subscriber_can_live_deploy_a_listing_with_no_ml_node(monkeypatch, seeded_universe):
    version_row = _owner_version_row(compiled_plan=_plan_no_ml(), dag_hash="deadbeefcafe0001")
    client = FakeSupabase(_base_rows(version_row=version_row))
    _patch_service_client(monkeypatch, client)

    result = _deploy(
        {
            "mode": "live",
            "exchange_account_id": SUBSCRIBER_ACCOUNT_ID,
            "risk_config_id": SUBSCRIBER_RISK_ID,
        }
    )

    (row,) = client.writes_to("strategy_deployments")
    assert row["mode"] == "live"
    assert row["environment"] == "live"
    assert row["exchange_account_id"] == SUBSCRIBER_ACCOUNT_ID
    assert row["risk_config_id"] == SUBSCRIBER_RISK_ID
    assert row["marketplace_listing_id"] == LISTING_ID
    assert row["user_id"] == SUBSCRIBER_ID
    assert row["strategy_id"] == OWNER_STRATEGY_ID
    assert row["version_id"] == OWNER_VERSION_ID
    assert row["exchange_id"] == VENUE
    assert row["exchange_symbol"] == SYMBOL
    assert result["deployment_id"]
    assert result["mode"] == "live"
    assert result["exchange_account_id"] == SUBSCRIBER_ACCOUNT_ID
    assert result["risk_config_id"] == SUBSCRIBER_RISK_ID


# ══════════════════════════════════════════════════════════════════════════
# 3. The first-party model-ownership refusal is unweakened by the new default
# ══════════════════════════════════════════════════════════════════════════


def test_first_party_model_ownership_refusal_is_unchanged_by_the_new_default(
    seeded_universe, ml_registry, ml_store
):
    """`model_owner_user_id` defaults to the deploying user when omitted — exactly
    `strategy_operations.deploy_version`'s call shape. A model row genuinely owned by
    someone else must still be refused for a first-party deploy, unchanged."""
    import asyncio

    uri = ml_store.put("a/b/c/node/model.joblib", ARTIFACT_BYTES)
    checksum = ml_store.checksum(uri)
    plan, ml_node_id = build_plan(ml_registry, lags=(1, 2))
    version_row = _owner_version_row(
        compiled_plan=json.loads(json.dumps(plan.to_dict())), dag_hash=plan.dag_hash
    )
    stray_row = model_row(
        uri, checksum, node_id=ml_node_id, feature_names=("lag_1", "lag_2"),
        user_id=OTHER_USER_ID,
        version_id=OWNER_VERSION_ID,
    )
    client = FakeSupabase(_base_rows(version_row=version_row, owner_model_rows=[stray_row]))

    request = db_binding.BindingRequest(mode="live", exchange_account_id=SUBSCRIBER_ACCOUNT_ID)
    with pytest.raises(db_binding.DeployRejected) as exc:
        asyncio.run(
            db_binding.evaluate_binding(
                client,
                {"id": SUBSCRIBER_ID},
                version_row,
                OWNER_STRATEGY_ID,
                OWNER_VERSION_LABEL,
                request,
                # No model_owner_user_id passed at all -- the first-party call shape.
            )
        )
    assert exc.value.code == "MODEL_NODE_NOT_BOUND"


# ══════════════════════════════════════════════════════════════════════════
# 4. Live with no exchange_account_id -> 422, audited
# ══════════════════════════════════════════════════════════════════════════


def test_live_mode_with_no_exchange_account_is_a_422_and_is_audited(monkeypatch, seeded_universe):
    version_row = _owner_version_row(compiled_plan=_plan_no_ml(), dag_hash="deadbeefcafe0001")
    client = _patch_service_client(
        monkeypatch, FakeSupabase(_base_rows(version_row=version_row))
    )
    recorder = FakeAuditLogger()
    monkeypatch.setattr(lib, "get_strategy_audit_logger", lambda: recorder)

    with pytest.raises(HTTPException) as caught:
        _deploy({"mode": "live"})

    assert caught.value.status_code == 422
    assert caught.value.detail["error"] == "LIVE_REQUIRES_EXCHANGE_ACCOUNT"
    assert client.writes_to("strategy_deployments") == []

    assert len(recorder.entries) == 1
    entry = recorder.entries[0]
    assert entry["reason"] == "LIVE_REQUIRES_EXCHANGE_ACCOUNT"
    assert entry["actor_id"] == SUBSCRIBER_ID
    assert entry["resource_id"] == LISTING_ID
    assert entry["metadata"]["operation"] == "deploy"


# ══════════════════════════════════════════════════════════════════════════
# 5. Someone else's exchange_account_id / risk_config_id -> 404, no leak of whose it is
# ══════════════════════════════════════════════════════════════════════════


def test_another_users_exchange_account_is_a_404_naming_neither_owner(
    monkeypatch, seeded_universe
):
    version_row = _owner_version_row(compiled_plan=_plan_no_ml(), dag_hash="deadbeefcafe0001")
    client = _patch_service_client(
        monkeypatch, FakeSupabase(_base_rows(version_row=version_row))
    )

    with pytest.raises(HTTPException) as caught:
        _deploy({"mode": "live", "exchange_account_id": OTHER_USER_ACCOUNT_ID})

    assert caught.value.status_code == 404
    assert caught.value.detail["error"] == "EXCHANGE_ACCOUNT_NOT_FOUND"
    rendered = repr(caught.value.detail)
    assert SUBSCRIBER_ID not in rendered
    assert OTHER_USER_ID not in rendered
    assert client.writes_to("strategy_deployments") == []


def test_another_users_risk_config_is_a_404(monkeypatch, seeded_universe):
    version_row = _owner_version_row(compiled_plan=_plan_no_ml(), dag_hash="deadbeefcafe0001")
    client = _patch_service_client(
        monkeypatch, FakeSupabase(_base_rows(version_row=version_row))
    )

    with pytest.raises(HTTPException) as caught:
        _deploy(
            {
                "mode": "live",
                "exchange_account_id": SUBSCRIBER_ACCOUNT_ID,
                "risk_config_id": OTHER_USER_RISK_ID,
            }
        )

    assert caught.value.status_code == 404
    assert caught.value.detail["error"] == "RISK_CONFIG_NOT_FOUND"
    assert client.writes_to("strategy_deployments") == []


# ══════════════════════════════════════════════════════════════════════════
# 6. The allow-list is expanded, but the Protected_Logic boundary is unaffected
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "field,value",
    [
        ("buy_logic", {"_nodes": ["secret"]}),
        ("strategy_definition", {"graph": "secret"}),
        ("version_id", OWNER_VERSION_ID),
    ],
)
def test_a_field_outside_the_expanded_allow_list_is_still_a_422_echoing_no_value(
    monkeypatch, seeded_universe, field, value
):
    version_row = _owner_version_row(compiled_plan=_plan_no_ml(), dag_hash="deadbeefcafe0001")
    client = _patch_service_client(
        monkeypatch, FakeSupabase(_base_rows(version_row=version_row))
    )

    with pytest.raises(HTTPException) as caught:
        _deploy({"mode": "live", field: value})

    assert caught.value.status_code == 422
    detail = caught.value.detail
    assert detail["error"] == lib.DEPLOY_REQUEST_INVALID
    assert field in detail["unexpected_fields"]
    assert repr(value) not in repr(detail)
    assert client.writes_to("strategy_deployments") == []


def test_the_five_binding_fields_are_all_in_the_expanded_allow_list():
    for field in (
        "mode",
        "exchange_account_id",
        "risk_config_id",
        "execution_config",
        "gap_strategy",
    ):
        assert field in lib._DEPLOY_ALLOWED_FIELDS  # noqa: SLF001


def test_the_request_model_still_forbids_protected_logic_and_extra_fields():
    assert lib.DeployMarketplaceStrategyRequest.model_config.get("extra") == "forbid"
    fields = set(lib.DeployMarketplaceStrategyRequest.model_fields)
    for forbidden in ("buy_logic", "sell_logic", "risk", "indicators", "ml_model_path",
                      "strategy_definition", "graph", "compiled_plan", "owner_id",
                      "tenant_id", "subscription_id"):
        assert forbidden not in fields


# ══════════════════════════════════════════════════════════════════════════
# 7. Paper mode (and mode unset) is untouched -- delegated to the existing suite, plus one
#    direct check here that the live-only code path is not entered at all.
# ══════════════════════════════════════════════════════════════════════════


def test_paper_mode_never_calls_evaluate_binding(monkeypatch, seeded_universe):
    version_row = _owner_version_row(compiled_plan=_plan_no_ml(), dag_hash="deadbeefcafe0001")
    client = _patch_service_client(
        monkeypatch, FakeSupabase(_base_rows(version_row=version_row))
    )
    called = []
    monkeypatch.setattr(
        db_binding, "evaluate_binding",
        lambda *a, **k: called.append((a, k)),
    )

    result = _deploy({"symbol": "BTC/USDT", "capital": 500})

    assert called == []
    (row,) = client.writes_to("strategy_deployments")
    assert row["environment"] == "paper"
    assert "mode" not in row
    assert "exchange_account_id" not in row
    assert result["deployment_id"]


def test_mode_unset_is_identical_to_mode_paper(monkeypatch, seeded_universe):
    version_row = _owner_version_row(compiled_plan=_plan_no_ml(), dag_hash="deadbeefcafe0001")
    client = _patch_service_client(
        monkeypatch, FakeSupabase(_base_rows(version_row=version_row))
    )

    _deploy({})
    (row,) = client.writes_to("strategy_deployments")
    assert row["environment"] == "paper"
    assert "mode" not in row


# ══════════════════════════════════════════════════════════════════════════
# 8. RT-15 / the kill switch engage identically for a marketplace-sourced live row — they are
#    keyed off `strategy_deployments`/the global kill switch, never off
#    `marketplace_listing_id`, so a marketplace-created row is indistinguishable at the venue
#    boundary from a first-party one. Asserted structurally (the real gate functions), and by
#    running THIS row through the real `live_execution_gate`.
# ══════════════════════════════════════════════════════════════════════════


def test_live_execution_gate_engages_for_a_marketplace_sourced_deployment(
    monkeypatch, seeded_universe
):
    """The row this handler just created is fed straight into the REAL
    `live_execution_gate.authorize_live_order` / `require_live_authorization` pair RT-15
    added — no marketplace-specific branch exists in that module (`marketplace_listing_id`
    does not appear in it at all), so the SAME refusal a first-party live order gets
    (platform not in live mode) is what a marketplace-sourced one gets too."""
    from backend_app.core import live_execution_gate as gate

    version_row = _owner_version_row(compiled_plan=_plan_no_ml(), dag_hash="deadbeefcafe0001")
    client = _patch_service_client(
        monkeypatch, FakeSupabase(_base_rows(version_row=version_row))
    )
    monkeypatch.delenv("VYOMQUANT_MODE", raising=False)
    monkeypatch.delenv("VYOMQUANT_ENABLE_LIVE_TRADING", raising=False)

    result = _deploy({"mode": "live", "exchange_account_id": SUBSCRIBER_ACCOUNT_ID})
    (row,) = client.writes_to("strategy_deployments")
    assert row["mode"] == "live"

    # `marketplace_listing_id` never appears in the gate module at all (RT-15 adds no
    # marketplace-specific bypass of it).
    import inspect

    assert "marketplace_listing_id" not in inspect.getsource(gate)
    assert "marketplace" not in inspect.getsource(gate).lower()

    with pytest.raises(gate.LiveExecutionRefused) as exc:
        gate.authorize_live_order(
            execution_id=result["deployment_id"],
            tenant_id=row["user_id"],
            strategy_id=row["strategy_id"],
            symbol=row["exchange_symbol"],
            side="buy",
            size="0.01",
            exchange_id=row["exchange_id"],
            checks=gate.REQUIRED_CHECKS,  # even a COMPLETE check set is refused: the
                                          # platform itself is not in live mode.
        )
    assert exc.value.code == "LIVE_TRADING_DISABLED_BY_PLATFORM_MODE"


def test_kill_switch_module_carries_no_marketplace_specific_branch():
    """The kill switch (`core.global_safety.GlobalKillSwitch`) is a single global Redis key
    (`system:kill_switch`), consulted by `execution_engine` before every order regardless of
    a deployment's source table or `marketplace_listing_id`. A marketplace bypass would have
    to name either; neither appears."""
    import inspect

    from backend_app.core import global_safety

    source = inspect.getsource(global_safety)
    assert "marketplace" not in source.lower()
    assert "marketplace_listing_id" not in source
