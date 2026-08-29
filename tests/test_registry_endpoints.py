# -*- coding: utf-8 -*-
"""The registry endpoints are the palette contract, so they are tested as a contract.

Spec: strategy-builder task 3.1. `design.md` -> API surface and -> Registry response
shape. Requirements 4.1, 4.11, 4.15, 5.1, 6.9, 11.8, 21.1, 21.8.

What is actually asserted
-------------------------
* **The shape is the design's shape, not a restatement of it.** `/registry/blocks` is
  compared field-for-field against `get_registry().to_dict()`. If the router ever starts
  assembling its own response, this fails - which is the point: two assemblers is how
  SB-03 and SB-04 became possible in the first place.
* **All seven categories, none of them empty** (Requirements 4.1, 4.2), with
  `categories[].order` strictly ascending so the palette's section order is served, not
  guessed by the client.
* **The projections are projections.** Every block in `/registry/indicators`,
  `/registry/features` and `/registry/models` is the *identical object* found in
  `/registry/blocks`, and no block of that category is missing from either side. A
  projection that filtered a different source could disagree; this makes that
  disagreement a test failure.
* **`ETag` / `304`** (Requirement 4.15): present, quoted, derived from
  `registry_version`, stable across identical requests, honoured through `If-None-Match`
  with an empty `304` body that still carries the validator, and *different* when the
  assembled registry differs.
* **Auth on every endpoint** (Requirements 21.1, 21.8): each of the five refuses an
  unauthenticated caller, and each carries `Depends(get_current_user)` plus a `slowapi`
  limit in the source.
* **Failure is loud** (Requirement 4.12, the SB-03 guard): when assembly raises, the
  endpoint answers 5xx naming the offending category or block. A 200 with an empty
  `blocks` list would be the defect this endpoint exists to close, so that shape is
  explicitly asserted *against*.

Only the failure-injection tests replace anything real, and what they inject is the real
`EmptyCategoryError` / a really-assembled smaller registry - not a stub response.
"""

import inspect
import json

import pytest

from backend_app.core.dependencies import get_current_user, get_request_supabase


REGISTRY_BASE = "/api/strategy-operations/registry"

BLOCK_ENDPOINTS = (
    f"{REGISTRY_BASE}/blocks",
    f"{REGISTRY_BASE}/indicators",
    f"{REGISTRY_BASE}/features",
    f"{REGISTRY_BASE}/models",
)

ALL_ENDPOINTS = BLOCK_ENDPOINTS + (f"{REGISTRY_BASE}/timeframes",)

#: endpoint -> the category it projects.
PROJECTIONS = {
    f"{REGISTRY_BASE}/indicators": "INDICATOR",
    f"{REGISTRY_BASE}/features": "FEATURE_ENGINEERING",
    f"{REGISTRY_BASE}/models": "ML_DL",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry():
    from backend_app.backend.strategy_dag.registry import get_registry

    return get_registry()


@pytest.fixture
def user():
    return {
        "id": "usr_registry_reader",
        "email": "reader@example.com",
        "role": "authenticated",
        "access_token": "token_reader",
    }


@pytest.fixture
def client(user):
    """An authenticated client. Only the identity is supplied; the payload is real."""
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
    """No identity supplied and no override: the real auth dependency runs."""
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides.clear()
    return TestClient(app)


@pytest.fixture
def blocks_payload(client):
    response = client.get(f"{REGISTRY_BASE}/blocks")
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 1. The response shape is the design's shape
# ---------------------------------------------------------------------------


class TestRegistryResponseShape:
    def test_blocks_serves_the_assembled_registry_verbatim(self, blocks_payload, registry):
        """`design.md` -> Registry response shape, produced once by `to_dict()`."""
        assert blocks_payload == registry.to_dict()

    def test_blocks_publishes_the_documented_keys(self, blocks_payload):
        assert set(blocks_payload) == {
            "registry_version",
            "registry_schema_version",
            "port_types",
            "categories",
            "blocks",
            "compatibility_matrix",
        }

    def test_registry_version_is_the_assembled_version(self, blocks_payload, registry):
        assert blocks_payload["registry_version"] == registry.registry_version
        assert blocks_payload["registry_version"].startswith("r_")

    def test_all_seven_categories_are_served_and_none_is_empty(self, blocks_payload):
        """Requirements 4.1, 4.2 - an empty palette section is SB-03."""
        from backend_app.backend.strategy_dag.schema import BlockCategory

        served = [entry["id"] for entry in blocks_payload["categories"]]
        assert served == [c["id"] for c in blocks_payload["categories"]]
        assert set(served) == {category.value for category in BlockCategory}
        assert len(served) == 7

        populated = {}
        for block in blocks_payload["blocks"]:
            populated[block["category"]] = populated.get(block["category"], 0) + 1
        empty = [category for category in served if not populated.get(category)]
        assert not empty, f"categories served with zero blocks: {empty}"

    def test_category_order_is_ascending_and_gapless(self, blocks_payload):
        orders = [entry["order"] for entry in blocks_payload["categories"]]
        assert orders == sorted(orders)
        assert orders == list(range(1, len(orders) + 1))
        for entry in blocks_payload["categories"]:
            assert entry["display_name"]

    def test_blocks_are_served_in_category_order(self, blocks_payload):
        """The palette renders top to bottom from this list; order is part of the answer."""
        rank = {entry["id"]: entry["order"] for entry in blocks_payload["categories"]}
        ranks = [rank[block["category"]] for block in blocks_payload["blocks"]]
        assert ranks == sorted(ranks)

    def test_compatibility_matrix_is_total_over_port_type(self, blocks_payload):
        """Requirement 6.9 - the client can look up any type without special-casing."""
        from backend_app.backend.strategy_dag.schema import PortType

        matrix = blocks_payload["compatibility_matrix"]
        assert set(matrix) == {port_type.value for port_type in PortType}
        for targets in matrix.values():
            assert set(targets) <= set(matrix)

    def test_port_types_cover_the_vocabulary(self, blocks_payload):
        from backend_app.backend.strategy_dag.schema import PortType

        assert blocks_payload["port_types"] == [p.value for p in PortType]

    def test_every_block_publishes_its_own_parameter_specs(self, blocks_payload):
        """Requirement 5.1 - the form is generated from these, not from a generic stub."""
        for block in blocks_payload["blocks"]:
            assert {"block_id", "display_name", "category", "inputs", "outputs", "params"} <= set(
                block
            )
            for param in block["params"]:
                assert param["key"] and param["type"]
                assert "required" in param and "default" in param

    def test_the_response_is_json_serialisable_and_within_the_size_budget(self, blocks_payload):
        """Requirement 4.15 - 250 KB gzipped ceiling."""
        import gzip

        encoded = json.dumps(blocks_payload).encode("utf-8")
        assert len(gzip.compress(encoded)) <= 250 * 1024


# ---------------------------------------------------------------------------
# 2. The projections cannot disagree with /blocks
# ---------------------------------------------------------------------------


class TestProjectionsAreProjections:
    @pytest.mark.parametrize("path", sorted(PROJECTIONS))
    def test_projection_holds_exactly_its_category_from_blocks(
        self, client, blocks_payload, path
    ):
        category = PROJECTIONS[path]
        response = client.get(path)
        assert response.status_code == 200, response.text
        payload = response.json()

        expected = [b for b in blocks_payload["blocks"] if b["category"] == category]
        assert payload["category"] == category
        assert payload["blocks"] == expected, (
            f"{path} is not a projection of /blocks for {category}"
        )
        assert payload["total"] == len(expected)

    @pytest.mark.parametrize("path", sorted(PROJECTIONS))
    def test_no_block_is_present_in_one_and_absent_from_the_other(
        self, client, blocks_payload, path
    ):
        category = PROJECTIONS[path]
        payload = client.get(path).json()

        in_projection = {b["block_id"] for b in payload["blocks"]}
        in_full = {b["block_id"] for b in blocks_payload["blocks"] if b["category"] == category}
        assert in_projection == in_full
        assert in_projection <= {b["block_id"] for b in blocks_payload["blocks"]}

    @pytest.mark.parametrize("path", sorted(PROJECTIONS))
    def test_projection_carries_the_shared_type_system(self, client, blocks_payload, path):
        payload = client.get(path).json()
        assert payload["registry_version"] == blocks_payload["registry_version"]
        assert payload["port_types"] == blocks_payload["port_types"]
        assert payload["compatibility_matrix"] == blocks_payload["compatibility_matrix"]
        assert [c["id"] for c in payload["categories"]] == [PROJECTIONS[path]]

    @pytest.mark.parametrize("path", sorted(PROJECTIONS))
    def test_projection_is_never_empty(self, client, path):
        payload = client.get(path).json()
        assert payload["blocks"], f"{path} served an empty category (SB-03)"

    def test_the_feature_projection_covers_the_engine(self, client):
        """Requirement 4.3 - at least 15 FEATURE_ENGINEERING descriptors."""
        payload = client.get(f"{REGISTRY_BASE}/features").json()
        assert payload["total"] >= 15

    def test_the_model_projection_carries_the_training_gate_figures(self, client):
        """Requirement 14.1 - minimums and caps travel with the descriptor."""
        payload = client.get(f"{REGISTRY_BASE}/models").json()
        for block in payload["blocks"]:
            assert "needs_model" in block["capability_flags"]
            assert block["metadata"].get("model"), block["block_id"]


# ---------------------------------------------------------------------------
# 3. Timeframes come from the pipeline, not from a hand-written list
# ---------------------------------------------------------------------------


class TestTimeframes:
    @pytest.fixture
    def payload(self, client):
        response = client.get(f"{REGISTRY_BASE}/timeframes")
        assert response.status_code == 200, response.text
        return response.json()

    def test_documented_keys(self, payload, blocks_payload):
        assert set(payload) == {
            "registry_version",
            "registry_schema_version",
            "timeframes",
            "sources",
            "total",
        }
        assert payload["registry_version"] == blocks_payload["registry_version"]

    def test_the_set_is_non_empty_and_ordered_by_bar_duration(self, payload):
        """Requirement 11.8 - an empty selector is the same lie as an empty palette."""
        seconds = [entry["seconds"] for entry in payload["timeframes"]]
        assert seconds
        assert seconds == sorted(seconds)
        assert len(set(seconds)) == len(seconds)
        assert payload["total"] == len(payload["timeframes"])

    def test_every_served_timeframe_is_accepted_by_every_named_source(self, payload):
        """The served set is the intersection of the real pipeline vocabularies."""
        import importlib

        from backend_app.routers.strategy_operations import _TIMEFRAME_SOURCES

        assert payload["sources"], "the endpoint must name where its answer came from"
        served = {entry["id"] for entry in payload["timeframes"]}
        for module_name, attribute in _TIMEFRAME_SOURCES:
            module = importlib.import_module(module_name)
            vocabulary = {str(key) for key in getattr(module, attribute)}
            assert served <= vocabulary, (
                f"{sorted(served - vocabulary)} is served but {module_name}.{attribute} "
                "cannot process it"
            )

    def test_labels_and_seconds_agree(self, payload):
        from backend_app.routers.strategy_operations import _timeframe_seconds

        for entry in payload["timeframes"]:
            assert entry["label"] == entry["id"]
            assert _timeframe_seconds(entry["id"]) == entry["seconds"]

    def test_no_timeframe_is_invented(self, payload, monkeypatch):
        """With one source emptied the answer shrinks; it is never topped up."""
        from backend_app.routers import strategy_operations as ops

        monkeypatch.setattr(
            ops, "_TIMEFRAME_SOURCES", (("backend_app.backend.master_executor", "TF_SEC"),)
        )
        narrowed, sources = ops._pipeline_timeframes()
        assert sources == ["backend_app.backend.master_executor.TF_SEC"]
        served = {entry["id"] for entry in payload["timeframes"]}
        assert served <= {entry["id"] for entry in narrowed}

    def test_an_unreadable_vocabulary_is_refused_not_faked(self, monkeypatch):
        from fastapi import HTTPException

        from backend_app.routers import strategy_operations as ops

        monkeypatch.setattr(
            ops, "_TIMEFRAME_SOURCES", (("backend_app.does.not.exist", "NOPE"),)
        )
        with pytest.raises(HTTPException) as excinfo:
            ops._pipeline_timeframes()
        assert excinfo.value.status_code == 503
        assert excinfo.value.detail["error"] == "TIMEFRAME_VOCABULARY_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 4. ETag and 304
# ---------------------------------------------------------------------------


class TestEntityTagAndConditionalGet:
    @pytest.mark.parametrize("path", ALL_ENDPOINTS)
    def test_etag_is_present_quoted_and_carries_the_registry_version(
        self, client, path, registry
    ):
        response = client.get(path)
        assert response.status_code == 200
        etag = response.headers.get("etag")
        assert etag, f"{path} served no ETag"
        assert etag.startswith('"') and etag.endswith('"')
        assert registry.registry_version in etag
        assert response.headers.get("x-registry-version") == registry.registry_version

    @pytest.mark.parametrize("path", ALL_ENDPOINTS)
    def test_etag_is_stable_across_identical_requests(self, client, path):
        first = client.get(path)
        second = client.get(path)
        assert first.headers["etag"] == second.headers["etag"]
        assert first.json() == second.json()

    def test_each_resource_has_its_own_etag(self, client):
        """A cached `/blocks` tag must not validate an `/indicators` copy."""
        tags = {path: client.get(path).headers["etag"] for path in ALL_ENDPOINTS}
        assert len(set(tags.values())) == len(ALL_ENDPOINTS), tags

    @pytest.mark.parametrize("path", ALL_ENDPOINTS)
    def test_if_none_match_yields_304_with_no_body(self, client, path):
        etag = client.get(path).headers["etag"]

        response = client.get(path, headers={"If-None-Match": etag})
        assert response.status_code == 304
        assert response.content == b""
        assert response.headers.get("etag") == etag

    @pytest.mark.parametrize("path", ALL_ENDPOINTS)
    def test_a_stale_or_absent_validator_yields_200(self, client, path):
        assert client.get(path, headers={"If-None-Match": '"r_00000000-blocks"'}).status_code == 200
        assert client.get(path).status_code == 200

    def test_a_weak_validator_and_a_list_are_honoured(self, client):
        path = f"{REGISTRY_BASE}/blocks"
        etag = client.get(path).headers["etag"]

        assert client.get(path, headers={"If-None-Match": f"W/{etag}"}).status_code == 304
        assert (
            client.get(path, headers={"If-None-Match": f'"r_deadbeef-blocks", {etag}'}).status_code
            == 304
        )
        assert client.get(path, headers={"If-None-Match": "*"}).status_code == 304

    def test_a_changed_registry_yields_a_different_etag(self, client, monkeypatch, registry):
        """`registry_version` is a content hash, so a different catalogue cannot reuse a tag."""
        from backend_app.backend.strategy_dag import registry as registry_module

        before = client.get(f"{REGISTRY_BASE}/blocks").headers["etag"]

        sources = registry_module.default_sources()
        smaller = registry_module.build_registry(
            registry_module.DescriptorSources(
                indicators=sources.indicators[:-1],
                features=sources.features,
                models=sources.models,
                static_blocks=sources.static_blocks,
                actions=sources.actions,
            )
        )
        assert smaller.registry_version != registry.registry_version

        monkeypatch.setattr(registry_module, "get_registry", lambda: smaller)
        response = client.get(f"{REGISTRY_BASE}/blocks")

        assert response.status_code == 200
        assert response.headers["etag"] != before
        assert smaller.registry_version in response.headers["etag"]
        # The previously-held validator must no longer satisfy the request.
        assert (
            client.get(f"{REGISTRY_BASE}/blocks", headers={"If-None-Match": before}).status_code
            == 200
        )

    def test_the_etag_helper_is_derived_from_the_version(self):
        from backend_app.routers.strategy_operations import _registry_etag

        assert _registry_etag("r_abc12345", "blocks") == '"r_abc12345-blocks"'
        assert _registry_etag("r_abc12345", "blocks") != _registry_etag(
            "r_abc12346", "blocks"
        )


# ---------------------------------------------------------------------------
# 5. Authorization and rate limits on every endpoint
# ---------------------------------------------------------------------------


class TestAuthAndLimits:
    @pytest.mark.parametrize("path", ALL_ENDPOINTS)
    def test_an_unauthenticated_caller_is_refused(self, anonymous_client, path):
        """Requirement 21.1 - every registry endpoint requires an authenticated user."""
        response = anonymous_client.get(path)
        assert response.status_code in (401, 403), (
            f"{path} answered {response.status_code} without an identity"
        )
        assert "blocks" not in response.text or response.status_code != 200

    @pytest.mark.parametrize(
        "handler_name",
        [
            "get_registry_blocks",
            "get_registry_indicators",
            "get_registry_features",
            "get_registry_models",
            "get_registry_timeframes",
        ],
    )
    def test_every_handler_depends_on_the_current_user(self, handler_name):
        from backend_app.routers import strategy_operations as ops

        handler = getattr(ops, handler_name)
        params = inspect.signature(handler).parameters
        assert "get_current_user" in str(params["user"].default), handler_name

    @pytest.mark.parametrize(
        "handler_name",
        [
            "get_registry_blocks",
            "get_registry_indicators",
            "get_registry_features",
            "get_registry_models",
            "get_registry_timeframes",
        ],
    )
    def test_every_handler_carries_a_slowapi_limit(self, handler_name):
        """Requirement 21.8. slowapi rewrites the callable, so the source is asserted."""
        from backend_app.routers import strategy_operations as ops

        source = inspect.getsource(ops)
        marker = f"async def {handler_name}("
        assert marker in source
        preamble = source[: source.index(marker)]
        decorators = preamble.rsplit("@router.get", 1)[-1]
        assert "@limiter.limit(" in decorators, handler_name

    def test_the_five_endpoints_are_mounted_where_the_design_says(self):
        from backend_app.main import app

        mounted = {route.path for route in app.routes}
        for path in ALL_ENDPOINTS:
            assert path in mounted, path

    def test_the_deprecated_alias_advertises_a_path_that_exists(self):
        """The replacement named by `GET /api/strategies/blocks` must resolve."""
        from backend_app.main import app
        from backend_app.routers.strategies import BLOCKS_REPLACEMENT_ENDPOINT

        assert BLOCKS_REPLACEMENT_ENDPOINT in {route.path for route in app.routes}
        assert BLOCKS_REPLACEMENT_ENDPOINT == f"{REGISTRY_BASE}/blocks"


# ---------------------------------------------------------------------------
# 6. Assembly failure is loud, never an empty palette
# ---------------------------------------------------------------------------


class TestAssemblyFailureIsLoud:
    @pytest.mark.parametrize("path", ALL_ENDPOINTS)
    def test_an_empty_category_becomes_a_named_5xx(self, client, monkeypatch, path):
        """Requirements 4.9, 4.12 - SB-03 must not be servable with a 200."""
        from backend_app.backend.strategy_dag import registry as registry_module
        from backend_app.backend.strategy_dag.schema import BlockCategory

        def _raise():
            raise registry_module.EmptyCategoryError([BlockCategory.FEATURE_ENGINEERING])

        monkeypatch.setattr(registry_module, "get_registry", _raise)

        response = client.get(path)
        assert response.status_code >= 500
        detail = response.json()["detail"]
        assert detail["error"] == "REGISTRY_CATEGORY_EMPTY"
        assert detail["failure"] == "EmptyCategoryError"
        assert "FEATURE_ENGINEERING" in detail["message"]
        assert "blocks" not in response.json()

    def test_an_unrunnable_block_becomes_a_named_5xx(self, client, monkeypatch):
        """Requirement 4.8 - the offending block is named, SB-04's half of the guard."""
        from backend_app.backend.strategy_dag import registry as registry_module

        def _raise():
            raise registry_module.UnrunnableBlockError("hma", "indicators_backend.hma")

        monkeypatch.setattr(registry_module, "get_registry", _raise)

        response = client.get(f"{REGISTRY_BASE}/blocks")
        assert response.status_code >= 500
        detail = response.json()["detail"]
        assert detail["error"] == "BLOCK_RUNTIME_UNRESOLVED"
        assert "hma" in detail["message"]

    def test_an_unexpected_assembly_failure_is_still_not_an_empty_list(
        self, client, monkeypatch
    ):
        from backend_app.backend.strategy_dag import registry as registry_module

        def _raise():
            raise RuntimeError("indicator module import exploded")

        monkeypatch.setattr(registry_module, "get_registry", _raise)

        response = client.get(f"{REGISTRY_BASE}/blocks")
        assert response.status_code >= 500
        detail = response.json()["detail"]
        assert detail["error"] == "REGISTRY_UNAVAILABLE"
        assert "indicator module import exploded" in detail["message"]
