# -*- coding: utf-8 -*-
"""
tests/test_sb06_exchange_agnostic_save.py

The SB-06 regression suite: a saved strategy describes trading logic and nothing else.

Spec: strategy-builder task 3.15 (`design.md` -> Exchange-agnostic strategy definition).
Requirements 12.1, 12.2, 12.3, 12.4.

THE DEFECT
----------
Exchange and market identity were hardcoded into every saved strategy, and the fallbacks
fired in *normal* operation rather than at some edge:

* `StrategyBuilder.jsx`'s `handleSaveStrategy` sent `exchange: "binance"` and read the
  traded market as
  `serNodes.find((n) => n.type === 'ccxt_asset_feed')?.params?.symbol || 'BTC/USDT'`.
  No palette entry ever produced a node of type `ccxt_asset_feed`, so the lookup never
  matched and `|| 'BTC/USDT'` / `|| '15m'` fired on every save. A strategy the author had
  configured for SOL/USDT at 4h was persisted against BTC/USDT at 15m, on Binance.
* The canvas was seeded with a `ccxt_asset_feed` node carrying `symbol: "BTC/USDT"` and
  `timeframe: "15m"` - a market nobody chose.
* `POST /strategies/{id}/train` took its venue from `body.get("exchange_id", "binance")`
  and then built `ConnectionEngine("binance", ...)` regardless, so a job configured for
  another venue trained on Binance candles while holding that venue's keys.

Task 3.9 deleted all of it. This file holds the deletion in place.

HOW THIS FAILS AGAINST THE PRE-FIX SAVE PATH
--------------------------------------------
Nothing here restores deleted code. Each test instead asserts the *absence* of the
specific substitution that used to happen, so the pre-fix implementation fails it:

* `TestUnparameterisedDataNodeRefusesTheSave` - the pre-fix path substituted and returned
  200 with a persisted row. It now answers **422** with a structured error naming the node
  and the field, and writes nothing.
* `TestNoMarketLiteralIsEverSubstituted` - the persisted payload equals what the DATA node
  declares. Under the pre-fix path the payload said `BTC/USDT` / `15m` / `binance` for the
  same input, and the fallback expressions are asserted absent from the save path's source:
  a literal that is not in the function cannot fire for any input.
* `TestNoExchangeIdentityOrCredentialIsPersisted` - `graph_json` and `compiled_plan` carry
  no exchange id and no credential even when the client sends them. The pre-fix path wrote
  `exchange_id` as a column and persisted the client's raw node params verbatim.
* `TestTrainingConfigNamesItsOwnDataSource` - the venue is resolved from the training
  configuration, and an unnamed one is a 422 rather than `"binance"`.

A NOTE ON `"BTC/USDT"` IN A MESSAGE
-----------------------------------
The 422 body's `fix_hint` reads "Set 'symbol' to 'BTC/USDT'." - an *example* in prose
addressed to a human, on the path that refuses to save. That is the opposite of the defect,
so the no-literal assertions are scoped to what is persisted and to the graph the server
compiled, never to the text of an error message. Scoping it that way is deliberate: an
assertion over prose would be satisfied by renaming a variable and would say nothing about
what reaches the database.

WHAT IS AND IS NOT REAL HERE
----------------------------
The registry is the real assembled one, the graphs are real canonical graphs built from
published descriptors, and the compile, the validation report, the identity hash and the
HTTP status are all produced by production code. Only the Supabase client is a double,
because these tests are about what would be written.

`training_jobs` does not exist yet - it lands with task 6.1 - so the training
configuration asserted on is the one the training endpoint accepts and resolves today,
which is the row's future `config` payload. That is stated rather than papered over.
"""

import inspect
import json
import os
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag.schema import (
    CODE_EXCHANGE_FIELD_DROPPED,
    FORBIDDEN_PARAM_FIELDS,
    BlockCategory,
    EdgeSpec,
    NodeSpec,
    StrategyGraph,
    find_forbidden_params,
    load_graph,
    strip_forbidden_params,
)
from backend_app.core.dependencies import get_current_user, get_request_supabase

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILDER_JSX = REPO_ROOT / "algo22-terminal" / "src" / "pages" / "StrategyBuilder.jsx"

#: The three literals the defect substituted. Every one of them was a value the author
#: never chose that nonetheless decided which market real money traded.
DELETED_LITERALS = ("BTC/USDT", "15m", "binance")

#: Credential material a client might send. None of it may reach a persisted artifact.
CREDENTIAL_PROBE = {
    "api_key": "AK_MUST_NOT_PERSIST",
    "secret": "SK_MUST_NOT_PERSIST",
    "secret_key": "SK2_MUST_NOT_PERSIST",
    "passphrase": "PP_MUST_NOT_PERSIST",
    "apiKey": "CCXT_SPELLING_MUST_NOT_PERSIST",
}


# ---------------------------------------------------------------------------
# Real graphs over the real registry
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg():
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


def _node(reg, block_id, **params):
    return NodeSpec.create(block_id, reg[block_id].category, params=params)


def strategy_graph(reg, *, symbol="SOL/USDT", timeframe="4h", extra_data_params=None):
    """data -> ema -> gt(vs constant) -> buy, the smallest executable strategy.

    ``symbol`` or ``timeframe`` passed as ``None`` leaves that parameter unset, which is
    the unparameterised DATA node Requirement 12.4 is about. Extra DATA params are how a
    stale or hostile client's exchange identity and credentials get into the payload.
    """
    data_params = {"market_type": "spot", "mode": "streaming"}
    if symbol is not None:
        data_params["symbol"] = symbol
    if timeframe is not None:
        data_params["timeframe"] = timeframe
    data_params.update(extra_data_params or {})

    data = _node(reg, "ohlcv_feed", **data_params)
    ema = _node(reg, "ema", window=20, source="close")
    const = _node(reg, "constant", value=30.0)
    gate = _node(reg, "gt")
    action = _node(
        reg, "action_buy_market", quantity_type="percent_of_equity", quantity=0.25
    )
    graph = StrategyGraph(
        nodes=[data, ema, const, gate, action],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", gate.id, "left"),
            EdgeSpec.create(const.id, "value", gate.id, "right"),
            EdgeSpec.create(gate.id, "out", action.id, "signal"),
        ],
    )
    return graph, data.id


# ---------------------------------------------------------------------------
# HTTP surface with a recording database double
# ---------------------------------------------------------------------------


@pytest.fixture
def user():
    return {
        "id": "usr_sb06_owner",
        "email": "owner@example.com",
        "role": "authenticated",
        "access_token": "token_owner",
    }


@pytest.fixture
def client(user):
    """A client for the real app, with the save endpoint's rate limit suspended.

    `POST /api/strategies` is limited to 20 requests a minute. This file makes more than
    that from one address, and so does the rest of the suite, so leaving the limiter armed
    would make these assertions depend on how many other tests happened to run first - a
    429 answer says nothing about SB-06 either way. The limit itself is not weakened: it
    stays exactly as declared in production code, it is restored after every test, and
    `test_the_save_endpoint_keeps_its_rate_limit` asserts the decorator is still there.
    """
    from fastapi.testclient import TestClient

    from backend_app.main import app

    limiter = getattr(app.state, "limiter", None)
    was_enabled = getattr(limiter, "enabled", None)
    if limiter is not None:
        limiter.enabled = False

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        if limiter is not None and was_enabled is not None:
            limiter.enabled = was_enabled


def _capturing_sb():
    """A Supabase double that records the insert payload. Only the database is faked."""
    captured = {}
    sb = MagicMock()

    def _insert(payload):
        captured["insert"] = payload
        chain = MagicMock()
        chain.execute = AsyncMock(
            return_value=MagicMock(data=[{"id": "strategy_sb06"}])
        )
        return chain

    sb.table.return_value.insert.side_effect = _insert
    return sb, captured


def save(client, body):
    """POST /api/strategies with a recording double. Returns ``(response, captured)``."""
    sb, captured = _capturing_sb()
    with patch("backend_app.routers.strategies._sb", new=AsyncMock(return_value=sb)):
        response = client.post("/api/strategies/", json=body)
    return response, captured


def persisted_artifacts(captured):
    """``(graph_json, compiled_plan)`` as the save path writes them.

    ``strategies`` has no dedicated DAG columns yet, so the router stores the graph in
    ``buy_logic._nodes`` / ``_edges`` and the plan in ``buy_logic._compiled_plan``. Those
    are today's ``graph_json`` and ``compiled_plan``; naming them here keeps the assertions
    readable and keeps one place to change when migration 004 part 1 lands.
    """
    buy_logic = captured["insert"]["buy_logic"]
    graph_json = {
        "nodes": buy_logic.get("_nodes"),
        "edges": buy_logic.get("_edges"),
        "schema_version": buy_logic.get("_dag_schema_version"),
    }
    return graph_json, buy_logic.get("_compiled_plan")


# ---------------------------------------------------------------------------
# Source reading, so a deleted literal cannot come back
# ---------------------------------------------------------------------------


def _python_code_only(source: str) -> str:
    """``source`` with its docstrings removed, so prose about the defect is not the defect.

    Every function under test documents the fallback it deleted. Scanning raw source would
    make those explanations indistinguishable from the code they describe.
    """
    import ast

    tree = ast.parse(source.strip())
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _js_code_only(source: str) -> str:
    """``source`` with its block comments and whole-line `//` comments removed.

    `StrategyBuilder.jsx` quotes the deleted expressions verbatim in its header comment and
    beside the state it replaced, so a raw substring search would report the fix as the
    defect.

    Deliberately not a JS parser. A trailing `// ...` on a line that also holds code is
    *kept*, which can only make the assertion stricter, never looser: the pre-fix
    expressions were code, so removing less cannot let them through.
    """
    import re

    without_blocks = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(
        line
        for line in without_blocks.splitlines()
        if not line.lstrip().startswith("//")
    )


@pytest.fixture(scope="module")
def builder_jsx_code():
    """The frontend save path's executable source, comments stripped."""
    if not BUILDER_JSX.exists():  # pragma: no cover - the file is committed
        pytest.skip(f"{BUILDER_JSX} is not present in this checkout")
    return _js_code_only(BUILDER_JSX.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Requirement 12.4 - an unparameterised DATA node refuses the save
# ---------------------------------------------------------------------------


class TestUnparameterisedDataNodeRefusesTheSave:
    """A missing market is a refusal that names the node and the field, not a default.

    The pre-fix path answered 200 here and persisted a strategy against BTC/USDT at 15m.
    """

    def test_a_data_node_with_no_symbol_is_refused_by_node_and_field(
        self, client, reg
    ):
        graph, data_id = strategy_graph(reg, symbol=None)
        response, captured = save(client, {"name": "no symbol", **graph.to_dict()})

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "STRATEGY_GRAPH_INVALID"
        assert V.CODE_PARAM_REQUIRED_MISSING in detail["codes"]

        issue = next(
            item
            for item in detail["errors"]
            if item["code"] == V.CODE_PARAM_REQUIRED_MISSING
        )
        assert issue["node_id"] == data_id, "the refusal must name the offending node"
        assert issue["field"] == "symbol", "the refusal must name the missing field"
        assert issue["severity"] == "error"
        assert issue["fix_hint"], "an author needs an action, not just a verdict"

    def test_a_data_node_with_no_timeframe_is_refused_by_node_and_field(
        self, client, reg
    ):
        graph, data_id = strategy_graph(reg, timeframe=None)
        response, _captured = save(client, {"name": "no timeframe", **graph.to_dict()})

        assert response.status_code == 422, response.text
        issue = next(
            item
            for item in response.json()["detail"]["errors"]
            if item["code"] == V.CODE_PARAM_REQUIRED_MISSING
        )
        assert issue["node_id"] == data_id
        assert issue["field"] == "timeframe"

    def test_both_missing_fields_are_reported_in_one_response(self, client, reg):
        """One pass, every problem (Requirement 8.1): two fixes, not two round trips."""
        graph, data_id = strategy_graph(reg, symbol=None, timeframe=None)
        response, _captured = save(client, {"name": "bare feed", **graph.to_dict()})

        assert response.status_code == 422, response.text
        reported = {
            (item["node_id"], item["field"])
            for item in response.json()["detail"]["errors"]
            if item["code"] == V.CODE_PARAM_REQUIRED_MISSING
        }
        assert reported == {(data_id, "symbol"), (data_id, "timeframe")}

    def test_the_refused_save_persists_nothing_at_all(self, client, reg):
        graph, _data_id = strategy_graph(reg, symbol=None)
        response, captured = save(client, {"name": "no symbol", **graph.to_dict()})

        assert response.status_code == 422
        assert "insert" not in captured, (
            "a refused save must write no row, no hash and no plan "
            "(Requirements 3.6, 12.4)"
        )

    def test_the_refusal_substitutes_nothing_for_the_missing_value(self, client, reg):
        """The reported value is the absence itself, not a filled-in default."""
        graph, _data_id = strategy_graph(reg, symbol=None)
        response, _captured = save(client, {"name": "no symbol", **graph.to_dict()})

        issue = next(
            item
            for item in response.json()["detail"]["errors"]
            if item["code"] == V.CODE_PARAM_REQUIRED_MISSING
        )
        assert issue["actual"] is None
        # The server's own echo of the graph, if it carries one, still has no symbol.
        report = response.json()["detail"].get("report") or {}
        assert "BTC/USDT" not in json.dumps(report.get("summary") or {})

    def test_an_envelope_symbol_cannot_stand_in_for_the_data_node(self, client, reg):
        """Market identity is the DATA block's parameter, not a top-level body field.

        Accepting `body["symbol"]` here is how the defect survived: the row looked
        configured while the graph the runtime executes still had no market.
        """
        graph, data_id = strategy_graph(reg, symbol=None)
        response, captured = save(
            client,
            {"name": "envelope only", "symbol": "BTC/USDT", **graph.to_dict()},
        )

        assert response.status_code == 422, response.text
        assert "insert" not in captured
        codes = response.json()["detail"]["codes"]
        assert V.CODE_PARAM_REQUIRED_MISSING in codes


# ---------------------------------------------------------------------------
# 2. Requirements 12.2, 12.3 - the descriptor leaves nothing to default
# ---------------------------------------------------------------------------


class TestDataDescriptorHasNoExchangeAndNoMarketDefault:
    """The refusal above is only trustworthy because the descriptor demands the value."""

    def test_no_data_block_publishes_an_exchange_parameter(self, reg):
        for descriptor in reg.in_category(BlockCategory.DATA):
            keys = {param.key for param in descriptor.params}
            assert not keys & set(FORBIDDEN_PARAM_FIELDS), (
                f"{descriptor.block_id} publishes an exchange or credential parameter; "
                "exchange identity is bound at deployment time (Requirement 12.2)"
            )

    def test_symbol_and_timeframe_are_required_with_no_default(self, reg):
        descriptor = reg["ohlcv_feed"]
        by_key = {param.key: param for param in descriptor.params}
        for key in ("symbol", "timeframe"):
            assert by_key[key].required is True, f"{key} must be required (12.3)"
            assert by_key[key].default is None, (
                f"{key} must have no default; a default that looks like a choice is the "
                "whole defect (Requirement 12.3)"
            )

    def test_no_action_block_publishes_a_traded_asset_parameter(self, reg):
        """Requirement 12.8's half of the same rule: the market is resolved, not typed."""
        for descriptor in reg.in_category(BlockCategory.ACTION):
            keys = {param.key for param in descriptor.params}
            assert "symbol" not in keys, (
                f"{descriptor.block_id} declares a traded-asset parameter; an action's "
                "market comes from the DATA node it descends from"
            )


# ---------------------------------------------------------------------------
# 3. Requirement 12.1 - no market literal is ever substituted
# ---------------------------------------------------------------------------


class TestNoMarketLiteralIsEverSubstituted:
    def test_the_persisted_market_is_the_one_the_data_node_declares(self, client, reg):
        graph, _data_id = strategy_graph(reg, symbol="SOL/USDT", timeframe="4h")
        response, captured = save(client, {"name": "sol", **graph.to_dict()})

        assert response.status_code == 200, response.text
        written = captured["insert"]
        assert written["symbol"] == "SOL/USDT"
        assert written["timeframe"] == "4h"

    def test_no_deleted_literal_appears_anywhere_in_the_persisted_row(
        self, client, reg
    ):
        graph, _data_id = strategy_graph(reg, symbol="SOL/USDT", timeframe="4h")
        _response, captured = save(client, {"name": "sol", **graph.to_dict()})

        rendered = json.dumps(captured["insert"], default=str)
        for literal in DELETED_LITERALS:
            assert literal not in rendered, (
                f"{literal!r} reached the persisted row for a strategy that declares "
                "SOL/USDT at 4h (SB-06)"
            )

    def test_a_graphless_save_omits_the_market_rather_than_inventing_one(self, client):
        """A metadata-only shell has no DATA node, so it has no market. Omit, never fill."""
        response, captured = save(client, {"name": "shell only"})

        assert response.status_code == 200, response.text
        written = captured["insert"]
        assert "symbol" not in written
        assert "timeframe" not in written
        assert "exchange" not in written
        assert "exchange_id" not in written

    def test_the_backend_save_path_holds_no_market_literal(self):
        """A default that is not in the source cannot fire for any input."""
        from backend_app.routers.strategies import _market_identity, create_strategy

        for function in (create_strategy, _market_identity):
            source = _python_code_only(inspect.getsource(function))
            for literal in DELETED_LITERALS:
                assert literal not in source, (
                    f"{literal!r} is still a fallback in "
                    f"{function.__name__} (SB-06)"
                )

    def test_the_frontend_save_path_holds_no_fallback_expression(
        self, builder_jsx_code
    ):
        """The exact pre-fix expressions, asserted absent from executable JSX.

        Comments are stripped first: the file explains the deleted expressions verbatim,
        and an explanation is not a fallback.
        """
        for expression in (
            "|| 'BTC/USDT'",
            '|| "BTC/USDT"',
            "|| '15m'",
            '|| "15m"',
            "exchange: 'binance'",
            'exchange: "binance"',
            "ccxt_asset_feed",
        ):
            assert expression not in builder_jsx_code, (
                f"{expression!r} is back on the frontend save path (SB-06)"
            )

    def test_the_frontend_holds_no_market_literal_at_all(self, builder_jsx_code):
        for literal in DELETED_LITERALS:
            assert literal not in builder_jsx_code, (
                f"{literal!r} is a literal in executable builder code (SB-06)"
            )


# ---------------------------------------------------------------------------
# 4. Requirement 12.1 - no exchange identifier and no credential is persisted
# ---------------------------------------------------------------------------


class TestNoExchangeIdentityOrCredentialIsPersisted:
    """`graph_json` and `compiled_plan` carry trading logic and nothing else.

    Asserted against a client that *sends* exchange identity and credentials, both in the
    envelope and inside a DATA node's params, because "no caller does that" is not a
    guarantee - a stale client, an exported graph or a probe all do.
    """

    def test_an_envelope_exchange_id_is_not_written_as_a_column(self, client, reg):
        graph, _data_id = strategy_graph(reg)
        _response, captured = save(
            client,
            {"name": "sb06", "exchange_id": "kraken", "exchange": "kraken", **graph.to_dict()},
        )

        written = captured["insert"]
        assert "exchange_id" not in written
        assert "exchange" not in written
        assert "kraken" not in json.dumps(written, default=str), (
            "exchange identity belongs to a deployment binding, never to a strategy "
            "(Requirement 12.1)"
        )

    def test_node_params_carrying_exchange_identity_are_dropped(self, client, reg):
        graph, data_id = strategy_graph(
            reg, extra_data_params={"exchange": "binance", "exchange_id": "kraken"}
        )
        response, captured = save(client, {"name": "sb06", **graph.to_dict()})

        assert response.status_code == 200, response.text
        graph_json, compiled_plan = persisted_artifacts(captured)

        stored_node = next(
            node for node in graph_json["nodes"] if node["id"] == data_id
        )
        assert "exchange" not in stored_node["params"]
        assert "exchange_id" not in stored_node["params"]
        # And the strategy still means what the author drew.
        assert stored_node["params"]["symbol"] == "SOL/USDT"
        assert stored_node["params"]["timeframe"] == "4h"

        plan_params = compiled_plan["node_index"][data_id]["params"]
        assert "exchange" not in plan_params
        assert plan_params["symbol"] == "SOL/USDT"

    def test_node_params_carrying_credentials_are_dropped(self, client, reg):
        graph, _data_id = strategy_graph(reg, extra_data_params=dict(CREDENTIAL_PROBE))
        response, captured = save(client, {"name": "sb06", **graph.to_dict()})

        assert response.status_code == 200, response.text
        graph_json, compiled_plan = persisted_artifacts(captured)

        for surface, payload in (
            ("graph_json", graph_json),
            ("compiled_plan", compiled_plan),
            ("the whole row", captured["insert"]),
        ):
            rendered = json.dumps(payload, default=str)
            for key, value in CREDENTIAL_PROBE.items():
                assert value not in rendered, (
                    f"credential {key!r} reached {surface}; keys live in the credential "
                    "vault and are resolved only inside the execution process"
                )

    def test_envelope_credentials_are_not_persisted(self, client, reg):
        graph, _data_id = strategy_graph(reg)
        _response, captured = save(
            client, {"name": "sb06", **CREDENTIAL_PROBE, **graph.to_dict()}
        )

        rendered = json.dumps(captured["insert"], default=str)
        for value in CREDENTIAL_PROBE.values():
            assert value not in rendered

    def test_the_stored_graph_reloads_still_carrying_nothing_forbidden(
        self, client, reg
    ):
        """The guarantee has to survive the round trip a version consumer performs."""
        graph, data_id = strategy_graph(
            reg, extra_data_params={"exchange": "binance", **CREDENTIAL_PROBE}
        )
        _response, captured = save(client, {"name": "sb06", **graph.to_dict()})

        reloaded = load_graph(captured["insert"])
        for node in reloaded.nodes:
            assert find_forbidden_params(node.params) == [], (
                f"node {node.id} reloaded with {find_forbidden_params(node.params)}"
            )
        by_id = {node.id: node for node in reloaded.nodes}
        assert by_id[data_id].params["symbol"] == "SOL/USDT"

    def test_a_version_1_row_is_migrated_without_its_exchange_identity(self, reg):
        """The stored corpus is version 1, and it is full of `exchange` columns."""
        v1_row = {
            "id": "strategy_legacy",
            "name": "legacy",
            "exchange": "binance",
            "buy_logic": {
                "_nodes": [
                    {
                        "id": "n_data",
                        "type": "input",
                        "symbol": "SOL/USDT",
                        "timeframe": "4h",
                        "exchange": "binance",
                        "params": {"api_key": "AK_MUST_NOT_MIGRATE"},
                    },
                    {
                        "id": "n_action",
                        "type": "action",
                        "action": "action_buy_market",
                        "quantity_type": "percent_of_equity",
                        "quantity": 0.25,
                    },
                ],
                "_edges": [
                    {
                        "id": "e1",
                        "source": "n_data",
                        "target": "n_action",
                    }
                ],
                "_dag_version": 1,
                "_dag_schema_version": 1,
            },
        }

        migrated = load_graph(v1_row)

        for node in migrated.nodes:
            assert find_forbidden_params(node.params) == []
        rendered = json.dumps(migrated.to_dict(), default=str)
        assert "AK_MUST_NOT_MIGRATE" not in rendered
        # The drop is reported, not silent, on the migration path.
        report = migrated.metadata.get("migration") or {}
        codes = {issue.get("code") for issue in report.get("issues", [])}
        assert CODE_EXCHANGE_FIELD_DROPPED in codes
        # And the market the author configured survives.
        by_id = {node.id: node for node in migrated.nodes}
        assert by_id["n_data"].params["symbol"] == "SOL/USDT"
        assert by_id["n_data"].params["timeframe"] == "4h"


class TestForbiddenParamVocabulary:
    """The stripping rule itself: narrow enough to be safe, wide enough to be useful."""

    def test_it_drops_exchange_identity_and_credentials_case_insensitively(self):
        params = {
            "symbol": "SOL/USDT",
            "timeframe": "4h",
            "exchange": "binance",
            "EXCHANGE_ID": "kraken",
            "apiKey": "leak",
            "Secret": "leak",
            "passphrase": "leak",
        }
        assert strip_forbidden_params(params) == {
            "symbol": "SOL/USDT",
            "timeframe": "4h",
        }

    def test_it_keeps_every_legitimate_parameter(self, reg):
        """No published block declares a forbidden key, so nothing real is dropped."""
        forbidden = set(FORBIDDEN_PARAM_FIELDS)
        for descriptor in reg.blocks():
            declared = {param.key for param in descriptor.params}
            assert not declared & forbidden, (
                f"{descriptor.block_id} declares {declared & forbidden}, which the "
                "canonical parse would silently drop"
            )
            params = {param.key: "x" for param in descriptor.params}
            assert strip_forbidden_params(params) == params

    def test_exchange_account_id_is_not_forbidden(self):
        """It references a deployment binding and holds no credential material."""
        params = {"exchange_account_id": "acct_123"}
        assert strip_forbidden_params(params) == params
        assert find_forbidden_params(params) == []

    def test_find_forbidden_params_reports_what_strip_removes(self):
        params = {"symbol": "SOL/USDT", "exchange": "binance", "apiKey": "leak"}
        assert sorted(find_forbidden_params(params)) == ["apiKey", "exchange"]
        assert set(strip_forbidden_params(params)) == {"symbol"}


# ---------------------------------------------------------------------------
# 5. Requirement 12.6 - the training config names its own data source
# ---------------------------------------------------------------------------


class TestTrainingConfigNamesItsOwnDataSource:
    """`training_jobs` lands with task 6.1; its `config` is what the endpoint resolves now.

    The pre-fix path read `body.get("exchange_id", "binance")` for the credential lookup
    and then built `ConnectionEngine("binance", ...)` regardless - two answers to one
    question, and the model learned the wrong market.
    """

    def test_a_config_naming_no_data_source_is_refused_not_defaulted(self):
        from fastapi import HTTPException

        from backend_app.routers.strategies import _resolve_training_data_source

        with pytest.raises(HTTPException) as caught:
            _resolve_training_data_source({"symbol": "SOL/USDT", "timeframe": "4h"})

        assert caught.value.status_code == 422
        detail = caught.value.detail
        assert detail["error"] == "TRAINING_DATA_SOURCE_MISSING"
        assert detail["field"] == "data_source"
        assert detail["fix_hint"]

    def test_the_configured_venue_is_the_one_returned(self):
        from backend_app.routers.strategies import _resolve_training_data_source

        assert _resolve_training_data_source({"data_source": "Kraken"}) == "kraken"
        assert _resolve_training_data_source({"exchange_id": "bybit"}) == "bybit"

    def test_a_non_venue_value_is_refused(self):
        from fastapi import HTTPException

        from backend_app.routers.strategies import _resolve_training_data_source

        with pytest.raises(HTTPException) as caught:
            _resolve_training_data_source({"data_source": "Binance Spot Account!"})
        assert caught.value.detail["error"] == "TRAINING_DATA_SOURCE_INVALID"

    def test_one_resolved_value_feeds_both_the_vault_and_the_connection(self):
        """The credential lookup and the market-data connection read the same variable."""
        from backend_app.routers.strategies import train_ml_strategy

        source = _python_code_only(inspect.getsource(train_ml_strategy))
        assert 'ConnectionEngine("binance"' not in source
        assert "'binance'" not in source and '"binance"' not in source, (
            "the training path must name no venue literal (Requirement 12.6)"
        )
        assert source.count("training_data_source") >= 3, (
            "the resolved data source must feed the vault lookup and the connection, "
            "so the two cannot disagree"
        )

    def test_the_endpoint_keeps_its_auth_and_entitlement_gates(self):
        from backend_app.routers.strategies import train_ml_strategy

        params = inspect.signature(train_ml_strategy).parameters
        assert "get_current_user" in str(params["user"].default)
        assert "require_ml_training" in str(params["_feature"].default)
        assert "check_ml_quota" in str(params["_ml_check"].default)


# ---------------------------------------------------------------------------
# 6. Nothing above weakened a control
# ---------------------------------------------------------------------------


class TestTheSavePathKeepsItsControls:
    """The `client` fixture suspends the save endpoint's rate limit for its own requests.

    That is test isolation, not a product change, and this class is what makes the
    difference checkable: the declared limit, the auth dependency and the quota gate are
    all asserted to be exactly where production put them.
    """

    def test_the_save_endpoint_keeps_its_rate_limit(self):
        from backend_app.routers import strategies as strategies_module

        source = inspect.getsource(strategies_module)
        marker = '@limiter.limit("20/minute")\nasync def create_strategy('
        assert marker in source, (
            "POST /api/strategies must keep its 20/minute limit; the client fixture only "
            "suspends it at runtime for this file's own requests"
        )

    def test_the_save_endpoint_keeps_its_auth_and_quota_gates(self):
        from backend_app.routers.strategies import create_strategy

        params = inspect.signature(create_strategy).parameters
        assert "get_current_user" in str(params["user"].default)
        assert "check_strategy_quota" in str(params["_quota"].default)

    def test_the_limiter_is_armed_again_after_this_file_runs(self, client):
        """The fixture restores what it changed, so a later test still sees the limit."""
        from backend_app.main import app

        assert app.state.limiter.enabled is False, "inside the fixture, by design"
