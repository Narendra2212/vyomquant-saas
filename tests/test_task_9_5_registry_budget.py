# -*- coding: utf-8 -*-
"""The registry payload budget, measured from the bytes actually served.

Spec: strategy-builder task 9.5. Requirement 4.15 - "THE Block_Registry SHALL serve the
response with a registry version value and an entity tag header, and SHALL keep the gzipped
response at 250 KB or smaller" - and Requirement 25.3 / `design.md` -> Performance:
"Registry payload | < 250 KB gzipped, cached by ``ETag`` | fetched once per session".

Why this file exists when `tests/test_registry_endpoints.py` already has a size assertion
-----------------------------------------------------------------------------------------
That assertion (task 3.1) gzips ``json.dumps(response.json())`` *in the test*. It measures a
recompression of a reparse of the body, at whatever separators `json.dumps` defaults to,
against a transfer that was not compressed at all - the router served 172 400 bytes of
identity JSON and the test asserted that a hypothetical compression of a differently
serialised copy would have fit. It is a useful proxy for "the catalogue has not exploded",
and it is left in place, but it is not a measurement of the served response.

This file measures the response. Every figure asserted here is read from the wire: the
``Content-Length`` of a real gzipped ``200`` from a real `TestClient` request through the
real router, with the real assembled registry. Nothing is mocked; the only override is the
authenticated identity, because Requirement 21.1 keeps `Depends(get_current_user)` on every
one of these endpoints and this file re-asserts that it is still there.

What is actually asserted
-------------------------
* **The measured gzipped size of every registry resource is at or below 250 KB**, and the
  figure and its headroom are printed (`-s`) so the number is reviewable and not merely
  compared. Measured: `/blocks` serves 15 863 bytes, 6.2% of the ceiling.
* **Negotiation, not assumption.** A caller that advertises gzip gets
  ``Content-Encoding: gzip``; a caller that says ``identity``, says ``gzip;q=0``, or sends no
  ``Accept-Encoding`` at all gets uncompressed JSON. `Vary: Accept-Encoding` travels on the
  ``200`` **and** on the ``304``, because the body now depends on a request header.
* **The two representations are the same registry.** The gzipped bytes decode to the
  identity body byte for byte - not "equal after `json.loads`", byte for byte - which is what
  rules out the compressed branch having reserialised the payload through a second
  `json.dumps` that could drift in separators, key order or float repr. The compressed bytes
  are also reproducible: compressing the identity body with the router's own level and
  ``mtime=0`` reproduces the served ``Content-Length`` exactly.
* **The ``ETag`` cache is what makes it cheap per session, and it still works.** A matching
  ``If-None-Match`` yields ``304`` with an empty body, no ``Content-Encoding``, and the
  validators intact - so the second and every later request in a session transfers no
  payload and compresses nothing. The 304 is still counted as both a request and a cache hit
  (task 9.1), so Requirement 25.3's hit ratio is unaffected by this change.
* **One response path.** `gzip` appears in exactly one function of the router, that function
  is `_registry_response`, and every registry handler returns through it - asserted from the
  AST, so a sixth projection cannot be added with its own compression, its own ``ETag`` or no
  budget at all.
* **Nothing was weakened.** All five endpoints still carry `Depends(get_current_user)` and a
  `slowapi` limit, still refuse an anonymous caller, and the assembly-failure guard still
  answers a named 5xx rather than a gzipped empty palette.

The "fetched once per session" half of the requirement is a client property and is asserted
against the client that implements it, in
`algo22-terminal/tests/unit/registryBudget.test.js`, over the real `conditionalGet` seam.
"""

import ast
import gzip
import inspect
import json
from pathlib import Path

import pytest

from backend_app.core.dependencies import get_current_user, get_request_supabase


REGISTRY_BASE = "/api/strategy-operations/registry"

#: Every registry resource, each of which must fit the budget on its own.
ALL_ENDPOINTS = (
    f"{REGISTRY_BASE}/blocks",
    f"{REGISTRY_BASE}/indicators",
    f"{REGISTRY_BASE}/features",
    f"{REGISTRY_BASE}/models",
    f"{REGISTRY_BASE}/timeframes",
)

REGISTRY_HANDLERS = (
    "get_registry_blocks",
    "get_registry_indicators",
    "get_registry_features",
    "get_registry_models",
    "get_registry_timeframes",
)

#: Requirement 4.15. Kibibytes, because the requirement says "250 KB" of a payload and every
#: other size ceiling in `design.md` (`graph_json` "well under 1 MB") is read the same way.
#: The interpretation is only load-bearing within 2.4% of the limit and the measured figure
#: is an order of magnitude below it, so nothing here turns on it.
BUDGET_BYTES = 250 * 1024

ROUTER_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend_app"
    / "routers"
    / "strategy_operations.py"
)


# ---------------------------------------------------------------------------
# Fixtures - the real router, the real registry, only the identity supplied
# ---------------------------------------------------------------------------


@pytest.fixture
def user():
    return {
        "id": "usr_budget_reader",
        "email": "budget@example.com",
        "role": "authenticated",
        "access_token": "token_budget",
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


@pytest.fixture
def report(request, capsys):
    """Print measured figures only when output is not being captured (`pytest -s`).

    The numbers are the point of this file, so they are emitted rather than merely compared -
    but on demand, so a full-suite run is not six lines longer per resource.
    """
    enabled = request.config.getoption("capture") == "no"

    def emit(line: str) -> None:
        if not enabled:
            return
        with capsys.disabled():
            print(line)

    return emit


def served_bytes(response) -> int:
    """The number of bytes the response put on the wire.

    ``response.content`` is useless for this: httpx transparently decodes
    ``Content-Encoding``, so it reports the *decompressed* length. ``Content-Length`` is the
    encoded length the router itself set, which is the figure the budget is about.
    """
    length = response.headers.get("content-length")
    assert length is not None, "the router served no Content-Length to measure"
    return int(length)


# ---------------------------------------------------------------------------
# 1. The measured budget
# ---------------------------------------------------------------------------


class TestTheGzippedPayloadFitsTheBudget:
    @pytest.mark.parametrize("path", ALL_ENDPOINTS)
    def test_the_served_gzipped_size_is_within_250_kb(self, client, path, report):
        """Requirement 4.15, measured rather than recomputed."""
        response = client.get(path, headers={"Accept-Encoding": "gzip"})

        assert response.status_code == 200, response.text
        assert response.headers.get("content-encoding") == "gzip"

        encoded = served_bytes(response)
        identity = len(response.content)  # httpx has already decoded it
        report(
            f"\n  {path:<52} gzipped {encoded:>7,} B "
            f"({encoded / BUDGET_BYTES:6.1%} of budget)  "
            f"identity {identity:>7,} B  ratio {identity / encoded:5.1f}x"
        )

        assert encoded <= BUDGET_BYTES, (
            f"{path} served {encoded} gzipped bytes, over the {BUDGET_BYTES}-byte "
            f"budget by {encoded - BUDGET_BYTES}"
        )

    def test_the_full_registry_is_the_largest_resource_and_still_fits(self, client):
        """The projections are subsets, so `/blocks` is the resource the budget is about.

        Asserted rather than assumed: if a projection ever served *more* than the full
        registry, the budget would be being measured against the wrong response.
        """
        sizes = {
            path: served_bytes(client.get(path, headers={"Accept-Encoding": "gzip"}))
            for path in ALL_ENDPOINTS
        }
        blocks = sizes[f"{REGISTRY_BASE}/blocks"]
        assert blocks == max(sizes.values()), sizes
        assert blocks <= BUDGET_BYTES

    def test_the_budget_has_real_headroom_rather_than_a_trimmed_field(
        self, client, report
    ):
        """Nothing was dropped from a descriptor to make the number come out right.

        The descriptor set served is the assembled registry verbatim - `test_registry_
        endpoints.py::test_blocks_serves_the_assembled_registry_verbatim` pins that - so this
        asserts the *other* half: that the fit is comfortable, not marginal. A payload
        squeezed to 249 KB would pass a ceiling check and be one indicator away from failing.
        """
        from backend_app.backend.strategy_dag.registry import get_registry

        response = client.get(
            f"{REGISTRY_BASE}/blocks", headers={"Accept-Encoding": "gzip"}
        )
        encoded = served_bytes(response)
        payload = response.json()

        assert len(payload["blocks"]) == len(get_registry())
        # Every block carries its full descriptor: params, ports, both category edges.
        for block in payload["blocks"]:
            assert {"inputs", "outputs", "params", "runtime_ref"} <= set(block)

        report(
            f"\n  headroom: {BUDGET_BYTES - encoded:,} B free over "
            f"{len(payload['blocks'])} blocks "
            f"({(BUDGET_BYTES - encoded) / max(encoded, 1):.0f}x the served size)"
        )
        # A tenfold headroom. This is a claim about comfort, not a second ceiling.
        assert encoded * 10 <= BUDGET_BYTES


# ---------------------------------------------------------------------------
# 2. Content coding is negotiated, and the two representations agree
# ---------------------------------------------------------------------------


class TestContentCodingIsNegotiated:
    @pytest.mark.parametrize("path", ALL_ENDPOINTS)
    def test_vary_accept_encoding_travels_on_the_200(self, client, path):
        """The body depends on a request header, so a shared cache must be told."""
        response = client.get(path, headers={"Accept-Encoding": "gzip"})
        assert response.headers.get("vary") == "Accept-Encoding"

    @pytest.mark.parametrize("path", ALL_ENDPOINTS)
    def test_vary_accept_encoding_travels_on_the_304_as_well(self, client, path):
        etag = client.get(path).headers["etag"]
        response = client.get(
            path, headers={"If-None-Match": etag, "Accept-Encoding": "gzip"}
        )
        assert response.status_code == 304
        assert response.headers.get("vary") == "Accept-Encoding"

    @pytest.mark.parametrize(
        "accept_encoding",
        ["identity", "gzip;q=0", "deflate", "br", "gzip;q=0, deflate"],
    )
    def test_a_caller_that_did_not_ask_for_gzip_gets_none(self, client, accept_encoding):
        """``gzip;q=0`` is a refusal, not a preference (RFC 9110 12.5.3)."""
        response = client.get(
            f"{REGISTRY_BASE}/blocks", headers={"Accept-Encoding": accept_encoding}
        )
        assert response.status_code == 200
        assert response.headers.get("content-encoding") is None
        assert response.json()["blocks"]

    def test_the_gzipped_body_decodes_to_the_identity_body_byte_for_byte(self, client):
        """Not "equal after json.loads" - byte for byte.

        This is what rules out the compressed branch having reserialised the payload. Both
        branches render one `JSONResponse` and the compressed one gzips *its* body, so a
        drift in separators, key order or float repr between the two is impossible; this
        asserts it rather than trusting the reading.
        """
        compressed = client.get(
            f"{REGISTRY_BASE}/blocks", headers={"Accept-Encoding": "gzip"}
        )
        identity = client.get(
            f"{REGISTRY_BASE}/blocks", headers={"Accept-Encoding": "identity"}
        )

        assert compressed.headers.get("content-encoding") == "gzip"
        assert identity.headers.get("content-encoding") is None
        # httpx decodes the gzip stream; `identity.content` was never encoded.
        assert compressed.content == identity.content
        assert served_bytes(compressed) < served_bytes(identity)
        assert compressed.headers["etag"] == identity.headers["etag"]

    def test_the_compressed_bytes_are_reproducible(self, client):
        """``mtime=0``, so the served ``Content-Length`` is a function of the payload alone.

        A gzip header carries a timestamp by default, which would make two byte-identical
        payloads produce two different lengths and an unreproducible measurement.
        """
        from backend_app.routers.strategy_operations import _REGISTRY_GZIP_LEVEL

        identity = client.get(
            f"{REGISTRY_BASE}/blocks", headers={"Accept-Encoding": "identity"}
        )
        compressed = client.get(
            f"{REGISTRY_BASE}/blocks", headers={"Accept-Encoding": "gzip"}
        )

        recompressed = gzip.compress(
            identity.content, compresslevel=_REGISTRY_GZIP_LEVEL, mtime=0
        )
        assert served_bytes(compressed) == len(recompressed)

        again = client.get(f"{REGISTRY_BASE}/blocks", headers={"Accept-Encoding": "gzip"})
        assert served_bytes(again) == served_bytes(compressed)

    def test_a_body_below_the_floor_is_not_compressed(self):
        """A tiny payload is served as-is: the envelope plus the CPU is not repaid.

        Exercised through the real `_registry_response` with a real `Request`, because no
        served resource is currently small enough to reach this branch - `/registry/timeframes`
        is 675 bytes - and an unexercised branch is an unmeasured one.
        """
        from starlette.requests import Request

        from backend_app.routers import strategy_operations as ops

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"{REGISTRY_BASE}/blocks",
                "headers": [(b"accept-encoding", b"gzip")],
            }
        )
        small = {"registry_version": "r_00000000", "blocks": []}
        assert len(json.dumps(small)) < ops._REGISTRY_GZIP_MIN_BYTES

        response = ops._registry_response(request, small, "blocks")
        assert response.status_code == 200
        assert "content-encoding" not in {k.lower() for k in response.headers}
        assert response.headers["ETag"] == '"r_00000000-blocks"'


# ---------------------------------------------------------------------------
# 3. The ETag cache is what makes it once-per-session, and it is intact
# ---------------------------------------------------------------------------


class TestTheCacheStillCostsNothingOnARepeat:
    @pytest.mark.parametrize("path", ALL_ENDPOINTS)
    def test_a_revalidated_request_transfers_no_payload_and_no_encoding(
        self, client, path
    ):
        """The 304 is why "fetched once per session" is affordable on the server side."""
        first = client.get(path, headers={"Accept-Encoding": "gzip"})
        second = client.get(
            path,
            headers={"If-None-Match": first.headers["etag"], "Accept-Encoding": "gzip"},
        )

        assert second.status_code == 304
        assert second.content == b""
        assert second.headers.get("content-encoding") is None
        assert second.headers["etag"] == first.headers["etag"]
        assert second.headers["cache-control"] == first.headers["cache-control"]
        assert (
            second.headers["x-registry-version"] == first.headers["x-registry-version"]
        )

    def test_a_304_is_still_counted_as_both_a_request_and_a_hit(self, client, monkeypatch):
        """Requirement 25.3's hit ratio is unchanged by the transfer coding (task 9.1)."""
        from backend_app.backend import metrics as M

        fresh = M.MetricsCollector()
        monkeypatch.setattr(M, "metrics_collector", fresh)

        first = client.get(f"{REGISTRY_BASE}/blocks", headers={"Accept-Encoding": "gzip"})
        second = client.get(
            f"{REGISTRY_BASE}/blocks",
            headers={"If-None-Match": first.headers["etag"], "Accept-Encoding": "gzip"},
        )

        assert second.status_code == 304
        assert fresh.builder_registry_requests.get(resource="blocks") == 2
        assert fresh.builder_registry_cache_hits.get(resource="blocks") == 1

    def test_one_response_records_exactly_one_request(self, client, monkeypatch):
        """The compressed branch must not double-count: it is a return, not a re-entry."""
        from backend_app.backend import metrics as M

        fresh = M.MetricsCollector()
        monkeypatch.setattr(M, "metrics_collector", fresh)

        client.get(f"{REGISTRY_BASE}/blocks", headers={"Accept-Encoding": "gzip"})

        assert fresh.builder_registry_requests.get(resource="blocks") == 1
        assert fresh.builder_registry_cache_hits.get(resource="blocks") == 0


# ---------------------------------------------------------------------------
# 4. One response path, and it is the one task 3.1 built
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def router_tree():
    """The router's AST. Parsed once; nothing in this file mutates it."""
    return ast.parse(ROUTER_PATH.read_text(encoding="utf-8"))


class TestCompressionLivesInTheOneSeam:
    def test_gzip_is_used_in_exactly_one_function_and_it_is_registry_response(
        self, router_tree
    ):
        """A second compression site would be a second budget nobody measures.

        Parsed rather than grepped: this module's prose names `gzip` and `GZipMiddleware`
        while explaining why the middleware was not used, and a comment must not read as a
        call.
        """
        owners = set()
        for node in ast.walk(router_tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Attribute)
                    and isinstance(child.value, ast.Name)
                    and child.value.id == "gzip"
                ):
                    owners.add(node.name)

        assert owners == {"_registry_response"}, owners

    def test_every_registry_handler_returns_through_registry_response(self, router_tree):
        """So a sixth projection is compressed, budgeted and counted without being told."""
        handlers = {
            node.name: node
            for node in ast.walk(router_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in REGISTRY_HANDLERS
        }
        assert set(handlers) == set(REGISTRY_HANDLERS), sorted(handlers)

        for name, node in handlers.items():
            returns = [child for child in ast.walk(node) if isinstance(child, ast.Return)]
            assert returns, name
            for statement in returns:
                called = statement.value
                assert isinstance(called, ast.Call), f"{name} returns a non-call"
                assert (
                    isinstance(called.func, ast.Name)
                    and called.func.id == "_registry_response"
                ), f"{name} returns {ast.unparse(called.func)}, not _registry_response"

    def test_no_gzip_middleware_was_installed_on_the_application(self):
        """The budget is one payload's; compressing every response is a different change.

        Order fills, risk decisions and deployment mutations do not need compressing to
        close a ceiling stated for a read-only reference payload, and a global middleware
        would also strip the deterministic ``Content-Length`` this file measures from.
        """
        from backend_app.main import app

        installed = [middleware.cls.__name__ for middleware in app.user_middleware]
        assert "GZipMiddleware" not in installed, installed

    def test_the_negotiation_helper_reads_the_header_it_claims_to(self):
        from starlette.requests import Request

        from backend_app.routers.strategy_operations import _accepts_gzip

        def request_with(value):
            headers = [] if value is None else [(b"accept-encoding", value.encode())]
            return Request({"type": "http", "method": "GET", "headers": headers})

        assert _accepts_gzip(request_with("gzip")) is True
        assert _accepts_gzip(request_with("br, gzip;q=0.8")) is True
        assert _accepts_gzip(request_with("GZIP")) is True
        assert _accepts_gzip(request_with("x-gzip")) is True
        assert _accepts_gzip(request_with("gzip;q=0")) is False
        assert _accepts_gzip(request_with("gzip;q=0.000")) is False
        assert _accepts_gzip(request_with("identity")) is False
        assert _accepts_gzip(request_with("deflate, br")) is False
        assert _accepts_gzip(request_with("")) is False
        assert _accepts_gzip(request_with(None)) is False


# ---------------------------------------------------------------------------
# 5. Nothing was weakened to serve fewer bytes
# ---------------------------------------------------------------------------


class TestNoControlWasWeakened:
    @pytest.mark.parametrize("path", ALL_ENDPOINTS)
    def test_an_unauthenticated_caller_is_still_refused(self, anonymous_client, path):
        """Requirement 21.1. A cheaper payload is not a public one."""
        response = anonymous_client.get(path, headers={"Accept-Encoding": "gzip"})
        assert response.status_code in (401, 403), response.status_code

    @pytest.mark.parametrize("handler_name", REGISTRY_HANDLERS)
    def test_every_handler_still_depends_on_the_current_user(self, handler_name):
        from backend_app.routers import strategy_operations as ops

        handler = getattr(ops, handler_name)
        params = inspect.signature(handler).parameters
        assert "get_current_user" in str(params["user"].default), handler_name

    @pytest.mark.parametrize("handler_name", REGISTRY_HANDLERS)
    def test_every_handler_still_carries_a_slowapi_limit(self, handler_name):
        """Requirement 21.8. slowapi rewrites the callable, so the source is asserted."""
        from backend_app.routers import strategy_operations as ops

        source = inspect.getsource(ops)
        marker = f"async def {handler_name}("
        assert marker in source
        preamble = source[: source.index(marker)]
        decorators = preamble.rsplit("@router.get", 1)[-1]
        assert "@limiter.limit(" in decorators, handler_name

    def test_the_cache_control_header_is_unchanged(self, client):
        """`private` still, so an auth-gated payload cannot land in a shared cache."""
        response = client.get(
            f"{REGISTRY_BASE}/blocks", headers={"Accept-Encoding": "gzip"}
        )
        assert response.headers["cache-control"] == "private, max-age=0, must-revalidate"

    def test_an_assembly_failure_is_still_a_named_5xx_not_a_gzipped_empty_palette(
        self, client, monkeypatch
    ):
        """Requirement 4.12, the SB-03 guard, through the compressed path."""
        from backend_app.backend.strategy_dag import registry as registry_module
        from backend_app.backend.strategy_dag.schema import BlockCategory

        def _raise():
            raise registry_module.EmptyCategoryError([BlockCategory.FEATURE_ENGINEERING])

        monkeypatch.setattr(registry_module, "get_registry", _raise)

        response = client.get(
            f"{REGISTRY_BASE}/blocks", headers={"Accept-Encoding": "gzip"}
        )
        assert response.status_code >= 500
        assert response.headers.get("content-encoding") is None
        detail = response.json()["detail"]
        assert detail["error"] == "REGISTRY_CATEGORY_EMPTY"
        assert "FEATURE_ENGINEERING" in detail["message"]

    def test_no_identity_or_venue_vocabulary_reached_the_payload(self, client, user):
        """The registry is global reference data. It must not carry the caller."""
        body = client.get(
            f"{REGISTRY_BASE}/blocks", headers={"Accept-Encoding": "gzip"}
        ).text
        for secret in (user["id"], user["email"], user["access_token"]):
            assert secret not in body
