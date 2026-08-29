"""`/api/market/symbols` and `/registry/timeframes` are tested as *contracts*, not as code.

Spec: strategy-builder task 7.2. Requirements 11.1, 11.5, 11.6, 11.8.

WHAT THIS FILE IS DEFENDING
---------------------------
Task 7.2 removed four defects from one handler, and each one is asserted *against* here
rather than assumed gone:

1. ``ccxt.binance().load_markets()`` inside the request. Asserted on the AST and on the
   module's imports - not on a comment - so a future re-import of ccxt into
   ``routers/market.py`` is a test failure.
2. the ``/USDT``-only filter. Asserted by serving a universe with ``USD``-quoted markets in
   it and requiring them in the body.
3. the first-fifty alphabetical truncation. Asserted by serving more than fifty markets and
   requiring all of them, and by requiring a market whose symbol sorts after the fiftieth.
4. the ten-symbol hardcoded fallback. Asserted by emptying the cache and requiring
   ``503 ASSET_UNIVERSE_UNAVAILABLE`` with **no** symbol-shaped string anywhere in the
   serialised body - a substring search over the whole response, because a substitute list
   nested under a new key would be the same lie.

Nothing here mocks the code under test. The substitutions are inputs only: CCXT-shaped
market dictionaries instead of a live exchange, and a seeded process-local cache instead of
a Redis server. Every filter, header, status code and timeframe intersection is the real one.

The timeframe half asserts the served set *is* the intersection of the pipeline's own
vocabularies, recomputed in the test from the same modules the router names. A test that
compared against a written-out list would be re-introducing the defect it is checking for.
"""

import ast
import inspect
import json
import re
import time

import pytest

from backend_app.backend import asset_universe as au
from backend_app.core.dependencies import get_current_user, get_request_supabase


SYMBOLS_ENDPOINT = "/api/market/symbols"
TIMEFRAMES_ENDPOINT = "/api/strategy-operations/registry/timeframes"

#: A market-pair-shaped literal. Used to prove the fallback list has not reappeared.
SYMBOL_LITERAL = re.compile(r"\b[A-Z0-9]{2,10}/[A-Z0-9]{2,10}\b")


def _docstring_node_ids(tree):
    """The ``id()`` of every docstring constant in ``tree``."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", None) or []
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _code_without_docstrings(obj):
    """``obj``'s source with every docstring removed.

    Prose that *describes* a removed defect is not the defect, so every structural
    assertion in this file runs against executable code only.
    """
    tree = ast.parse(inspect.cleandoc(inspect.getsource(obj)))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


# ---------------------------------------------------------------------------
# CCXT-shaped inputs
# ---------------------------------------------------------------------------


def market(symbol, base, quote, market_type="spot", active=True):
    return {
        "id": symbol.replace("/", "").replace(":", ""),
        "symbol": symbol,
        "base": base,
        "quote": quote,
        "type": market_type,
        market_type: True,
        "active": active,
        "precision": {"price": 2, "amount": 6},
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 10.0}},
    }


#: Deliberately more than fifty markets, deliberately not all ``/USDT``, and deliberately
#: containing symbols that sort *after* the fiftieth alphabetically. Both retired defects -
#: the quote filter and the `[:50]` cut - are visible as missing rows if either returns.
def _wide_market_map():
    bases = [
        "AAA", "AAB", "AAC", "AAD", "AAE", "AAF", "AAG", "AAH", "AAI", "AAJ",
        "AAK", "AAL", "AAM", "AAN", "AAO", "AAP", "AAQ", "AAR", "AAS", "AAT",
        "AAU", "AAV", "AAW", "AAX", "AAY", "AAZ", "ABA", "ABB", "ABC", "ABD",
        "ABE", "ABF", "ABG", "ABH", "ABI", "ABJ", "ABK", "ABL", "ABM", "ABN",
        "ABO", "ABP", "ABQ", "ABR", "ABS", "ABT", "ABU", "ABV", "ABW", "ABX",
        "ZZA", "ZZB", "ZZC", "ZZD", "ZZE",
    ]
    markets = {}
    for base in bases:
        markets[f"{base}/USDT"] = market(f"{base}/USDT", base, "USDT")
    # Non-USDT quotes: invisible under the retired `endswith('/USDT')` filter.
    markets["ZZE/USD"] = market("ZZE/USD", "ZZE", "USD")
    markets["ZZE/EUR"] = market("ZZE/EUR", "ZZE", "EUR")
    markets["ZZF/BTC"] = market("ZZF/BTC", "ZZF", "BTC")
    # Inactive: excluded by default, reachable with active_only=false.
    markets["ZZG/USDT"] = market("ZZG/USDT", "ZZG", "USDT", active=False)
    # One symbol, two market types: two universe records, one symbol string.
    markets["ZZH/USDT"] = market("ZZH/USDT", "ZZH", "USDT")
    markets["ZZH/USDT:USDT"] = market("ZZH/USDT:USDT", "ZZH", "USDT", market_type="swap")
    return markets


def build_universe():
    """A real :class:`AssetUniverse`, built through the real merge."""
    assets, _stats = au.merge_markets([("binance", _wide_market_map())])
    return au.AssetUniverse(
        assets=assets,
        generated_at=time.time(),
        exchanges=["binance"],
        exchanges_failed=[],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def offline_cache(monkeypatch):
    """No Redis, no exchange, no leaked module state between tests.

    ``supported_exchange_ids`` is emptied so a refresh scheduled by the code under test
    fails immediately and offline instead of dialling real venues; the scheduling itself
    stays real.
    """
    au.reset_asset_universe_state_for_tests()

    async def no_redis():
        return None

    monkeypatch.setattr(au, "_redis_cache", no_redis)
    monkeypatch.setattr(au, "supported_exchange_ids", lambda: [])
    yield
    au.reset_asset_universe_state_for_tests()


@pytest.fixture
def warm_cache():
    universe = build_universe()
    au._local_universe = universe
    return universe


@pytest.fixture
def user():
    return {
        "id": "usr_symbols_reader",
        "email": "symbols@example.com",
        "role": "authenticated",
        "access_token": "token_symbols",
    }


@pytest.fixture
def client(user):
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client():
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides.clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. The four defects are gone, asserted structurally
# ---------------------------------------------------------------------------


class TestTheRetiredDefects:
    def test_the_module_imports_ccxt_nowhere(self):
        """Defect 1. A `load_markets()` needs ccxt; ccxt is no longer reachable here."""
        from backend_app.routers import market as market_router

        tree = ast.parse(inspect.getsource(market_router))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "ccxt" not in imported, (
            "routers/market.py imports ccxt again — a synchronous load_markets() on a "
            "request path is what task 7.2 removed"
        )

    def test_no_handler_in_this_router_calls_load_markets(self):
        """Defect 1, on the AST of every route handler rather than on the docstring."""
        from backend_app.routers import market as market_router

        tree = ast.parse(inspect.getsource(market_router))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                name = getattr(target, "attr", None) or getattr(target, "id", None)
                if name in {"load_markets", "loadMarkets"}:
                    offenders.append(name)
        assert not offenders, f"load_markets called in routers/market.py: {offenders}"

    def test_the_usdt_filter_and_the_fifty_cut_are_gone(self):
        """Defects 2 and 3, on the handler's executable source.

        Docstrings are stripped first: the handler's prose *names* the two defects it
        replaced, and naming a defect is not committing it.
        """
        from backend_app.routers.market import get_symbols

        code = _code_without_docstrings(get_symbols)
        assert "endswith" not in code
        assert "[:50]" not in code

    def test_the_handler_holds_no_symbol_literal(self):
        """Defect 4. Docstrings excluded: prose about a defect is not the defect."""
        from backend_app.routers import market as market_router

        tree = ast.parse(inspect.getsource(market_router))
        docstrings = _docstring_node_ids(tree)
        offenders = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and SYMBOL_LITERAL.search(node.value)
        ]
        assert not offenders, f"symbol-shaped literals in routers/market.py: {offenders}"

    def test_the_handler_reads_the_cached_universe(self):
        """The replacement is the cache, not a second assembly path."""
        from backend_app.routers.market import get_symbols

        source = inspect.getsource(get_symbols)
        assert "discover_assets" in source


# ---------------------------------------------------------------------------
# 2. The served answer
# ---------------------------------------------------------------------------


class TestSymbolsEndpoint:
    def test_the_response_is_still_a_bare_array_of_strings(self, client, warm_cache):
        """The contract `typed-client.ts` and `DataPipelineContext.jsx` depend on."""
        response = client.get(SYMBOLS_ENDPOINT)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert isinstance(payload, list)
        assert payload
        assert all(isinstance(entry, str) for entry in payload)

    def test_every_active_market_is_served_not_the_first_fifty(self, client, warm_cache):
        """Defect 3, on the wire."""
        payload = client.get(SYMBOLS_ENDPOINT).json()
        active = {a.symbol for a in warm_cache.assets if a.active}
        assert set(payload) == active
        assert len(payload) > 50, "a fifty-row answer means the truncation came back"
        assert "ZZE/USDT" in payload, "a symbol past the fiftieth alphabetically is missing"

    def test_non_usdt_quotes_are_served(self, client, warm_cache):
        """Defect 2, on the wire."""
        payload = client.get(SYMBOLS_ENDPOINT).json()
        assert {"ZZE/USD", "ZZE/EUR", "ZZF/BTC"} <= set(payload)

    def test_the_old_view_is_reproducible_as_an_explicit_filter(self, client, warm_cache):
        payload = client.get(SYMBOLS_ENDPOINT, params={"quote": "USDT"}).json()
        assert payload
        assert all(entry.split(":")[0].endswith("/USDT") for entry in payload)
        assert "ZZE/USD" not in payload

    def test_one_symbol_on_two_market_types_appears_once(self, client, warm_cache):
        payload = client.get(SYMBOLS_ENDPOINT).json()
        assert len(payload) == len(set(payload))
        assert payload.count("ZZH/USDT") == 1

    def test_inactive_markets_are_excluded_by_default_and_reachable_on_request(
        self, client, warm_cache
    ):
        default = client.get(SYMBOLS_ENDPOINT).json()
        assert "ZZG/USDT" not in default

        everything = client.get(SYMBOLS_ENDPOINT, params={"active_only": False}).json()
        assert "ZZG/USDT" in everything

    def test_filters_narrow_monotonically(self, client, warm_cache):
        everything = set(client.get(SYMBOLS_ENDPOINT).json())
        searched = set(client.get(SYMBOLS_ENDPOINT, params={"search": "ZZE"}).json())
        based = set(client.get(SYMBOLS_ENDPOINT, params={"base": "ZZE"}).json())
        assert searched <= everything
        assert based <= searched

    @pytest.mark.parametrize(
        "params",
        [
            {},
            {"quote": "USDT"},
            {"base": "ZZE"},
            {"search": "ZZ"},
            {"market_type": "swap", "active_only": False},
            {"active_only": False},
        ],
    )
    def test_the_answer_agrees_with_the_canonical_discovery_surface(
        self, client, warm_cache, params
    ):
        """Two surfaces, one universe: the projection cannot disagree with its source.

        Stands in for a generated sweep. Task 7.8 owns the property-based
        ``(universe, filters, page size)`` exploration; what is covered here is that no
        filter combination makes the legacy endpoint answer something
        ``/api/strategy-operations/assets`` would not.
        """
        legacy = set(client.get(SYMBOLS_ENDPOINT, params=params).json())
        canonical = client.get(
            "/api/strategy-operations/assets", params={**params, "limit": 500}
        ).json()
        assert legacy == {record["symbol"] for record in canonical["assets"]}

    def test_a_filter_matching_nothing_is_an_empty_200(self, client, warm_cache):
        """Distinct from an unavailable universe, and it has to stay distinct."""
        response = client.get(SYMBOLS_ENDPOINT, params={"base": "NOSUCHCOIN"})
        assert response.status_code == 200, response.text
        assert response.json() == []
        assert response.headers["X-Asset-Total"] == "0"
        assert response.headers["X-Asset-Truncated"] == "false"


# ---------------------------------------------------------------------------
# 3. Provenance and disclosed truncation
# ---------------------------------------------------------------------------


class TestProvenanceHeaders:
    def test_the_universe_is_identified_and_dated(self, client, warm_cache):
        response = client.get(SYMBOLS_ENDPOINT)
        assert response.headers["X-Asset-Universe-Hash"] == warm_cache.universe_hash
        assert response.headers["X-Asset-Universe-Stale"] == "false"
        assert float(response.headers["X-Asset-Universe-Age-Seconds"]) >= 0
        assert "private" in response.headers["Cache-Control"]
        assert "strategy-operations/assets" in response.headers["Link"]

    def test_the_default_answer_is_not_truncated(self, client, warm_cache):
        response = client.get(SYMBOLS_ENDPOINT)
        assert response.headers["X-Asset-Truncated"] == "false"
        assert int(response.headers["X-Asset-Returned"]) == len(response.json())

    def test_a_requested_limit_truncates_and_says_so(self, client, warm_cache):
        response = client.get(SYMBOLS_ENDPOINT, params={"limit": 5})
        assert response.status_code == 200, response.text
        assert len(response.json()) == 5
        assert response.headers["X-Asset-Truncated"] == "true"
        # The real total is still stated, so the cut is visible rather than implied.
        assert int(response.headers["X-Asset-Total"]) > 5

    def test_a_stale_universe_is_served_and_labelled(self, client):
        """Requirement 11.6's condition is an *empty* cache, not an old one."""
        assets, _stats = au.merge_markets([("binance", _wide_market_map())])
        au._local_universe = au.AssetUniverse(
            assets=assets,
            generated_at=time.time() - (au.UNIVERSE_TTL_SECONDS + 60),
            exchanges=["binance"],
            exchanges_failed=[],
        )
        response = client.get(SYMBOLS_ENDPOINT)
        assert response.status_code == 200, response.text
        assert response.json()
        assert response.headers["X-Asset-Universe-Stale"] == "true"

    def test_the_requested_limit_is_bounded_server_side(self, client, warm_cache):
        assert client.get(SYMBOLS_ENDPOINT, params={"limit": 0}).status_code == 422
        assert client.get(SYMBOLS_ENDPOINT, params={"limit": 10_000_000}).status_code == 422


# ---------------------------------------------------------------------------
# 4. An unavailable universe is a 503 carrying no substitute
# ---------------------------------------------------------------------------


class TestUnavailableUniverse:
    def test_an_empty_cache_is_503_and_names_itself(self, client):
        """Requirement 11.6."""
        au._local_universe = None
        response = client.get(SYMBOLS_ENDPOINT)
        assert response.status_code == 503, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "ASSET_UNIVERSE_UNAVAILABLE"
        assert response.headers["Retry-After"] == str(au.RETRY_AFTER_SECONDS)

    def test_the_503_body_carries_no_symbol_under_any_key(self, client):
        """A nested substitute list would be the same lie under a new name."""
        au._local_universe = None
        response = client.get(SYMBOLS_ENDPOINT)
        body = json.dumps(response.json())
        assert not SYMBOL_LITERAL.search(body), body

    def test_it_is_not_an_empty_array_either(self, client):
        """`[]` would read to a client as 'this platform lists nothing'."""
        au._local_universe = None
        response = client.get(SYMBOLS_ENDPOINT)
        assert response.status_code != 200
        assert response.json() != []

    def test_the_cause_is_reported_rather_than_left_to_be_reproduced(self, client):
        au._local_universe = None
        detail = client.get(SYMBOLS_ENDPOINT).json()["detail"]
        assert "last_refresh_error" in detail
        assert "refresh_in_flight" in detail
        assert detail["retry_after_seconds"] == au.RETRY_AFTER_SECONDS


# ---------------------------------------------------------------------------
# 5. Controls — none weakened
# ---------------------------------------------------------------------------


class TestControls:
    def test_an_unauthenticated_caller_is_refused(self, anonymous_client):
        response = anonymous_client.get(SYMBOLS_ENDPOINT)
        assert response.status_code in (401, 403), response.text

    def test_the_handler_still_depends_on_the_current_user(self):
        from backend_app.routers import market as market_router

        params = inspect.signature(market_router.get_symbols).parameters
        assert "get_current_user" in str(params["user"].default)

    def test_the_rate_limit_is_unchanged(self):
        from backend_app.routers import market as market_router

        source = inspect.getsource(market_router)
        assert '@limiter.limit("60/minute")' in source


# ---------------------------------------------------------------------------
# 6. Timeframes: the published set is an intersection, not a list
# ---------------------------------------------------------------------------


def _vocabularies():
    """Each named pipeline vocabulary, read from the module the router names."""
    import importlib

    from backend_app.routers.strategy_operations import _TIMEFRAME_SOURCES

    return {
        f"{module_name}.{attribute}": {
            str(key) for key in getattr(importlib.import_module(module_name), attribute)
        }
        for module_name, attribute in _TIMEFRAME_SOURCES
    }


class TestPublishedTimeframes:
    @pytest.fixture
    def payload(self, client):
        response = client.get(TIMEFRAMES_ENDPOINT)
        assert response.status_code == 200, response.text
        return response.json()

    def test_the_served_set_is_exactly_the_intersection(self, payload):
        """Requirement 11.8. Recomputed from the sources, never compared to a list."""
        vocabularies = list(_vocabularies().values())
        assert len(vocabularies) >= 3, "task 7.2 widened this to the full pipeline set"

        intersection = set(vocabularies[0])
        for vocabulary in vocabularies[1:]:
            intersection &= vocabulary

        from backend_app.routers.strategy_operations import _timeframe_seconds

        expected = {
            label for label in intersection if _timeframe_seconds(label) is not None
        }
        assert {entry["id"] for entry in payload["timeframes"]} == expected
        assert expected, "an empty timeframe selector is the same lie as an empty palette"

    def test_the_market_data_path_is_one_of_the_named_sources(self, payload):
        """The row-coverage gate's own table, which is what task 7.2 added."""
        assert (
            "backend_app.backend.market_data_validation.TIMEFRAME_MINUTES"
            in payload["sources"]
        )

    def test_an_interval_no_stage_can_measure_is_not_published(self, payload):
        """The intersection has to actually bite, or it is decoration.

        A label carried by one vocabulary and missing from another must not be served.
        """
        vocabularies = _vocabularies()
        union = set().union(*vocabularies.values())
        served = {entry["id"] for entry in payload["timeframes"]}
        for label in union - served:
            missing_from = [
                name for name, vocab in vocabularies.items() if label not in vocab
            ]
            assert missing_from, (
                f"{label} is in every vocabulary yet is not served — the intersection "
                "dropped something it should have kept"
            )

    def test_the_data_descriptor_publishes_no_competing_timeframe_list(self):
        """Recorded as a test: the DATA block states no vocabulary of its own.

        ``ohlcv_feed``'s ``timeframe`` param is required with ``default=None`` and no
        ``options`` tuple (SB-06), so the selector's only source is this endpoint. A future
        hand-written ``options`` list on that param would be a second, drift-prone
        vocabulary, so it is asserted absent.
        """
        from backend_app.backend.strategy_dag.block_specs import ohlcv_feed_descriptor

        spec = {param.key: param for param in ohlcv_feed_descriptor().params}["timeframe"]
        assert spec.required is True
        assert spec.default is None
        assert not spec.options

    def test_the_hoisted_table_is_the_one_the_gate_uses(self):
        """The market-data constant was hoisted, not copied: one table, one consumer."""
        from backend_app.backend import market_data_validation as mdv

        source = inspect.getsource(mdv.MarketDataValidator._validate_row_count)
        assert "TIMEFRAME_MINUTES.get(timeframe, 60)" in source
        assert mdv.TIMEFRAME_MINUTES["1h"] == 60
        assert mdv.TIMEFRAME_MINUTES["1d"] == 1440
