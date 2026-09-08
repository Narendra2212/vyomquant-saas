"""A backtest job identifier is useless to another tenant.

``POST /api/strategies/backtest`` and ``GET /api/strategies/backtest/{job_id}`` were, before
this change, the two unauthenticated routes of ``backend_app/routers/strategies.py``:
``router = APIRouter()`` carries no ``dependencies=``, and ``main.py`` includes it with none
either, so neither handler resolved an identity at all. The POST dispatched CPU-bound work
onto the shared default executor for any caller, and the GET returned any tenant's backtest
status to anybody holding — or guessing — a ``job_id``.

Both now take ``user: dict = Depends(get_current_user)``. The POST records ``user_id`` into
the status hash *before* ``publish_backtest_job``, and the GET refuses any job whose recorded
``user_id`` is not the authenticated identity.

WHAT IS ASSERTED HERE
    That a stranger holding a *valid* ``job_id`` receives 404 and not 200; that the owner of
    that same job still receives 200; that the stranger's refusal is indistinguishable from
    the answer a ``job_id`` belonging to no tenant gets; that a status hash carrying no owner
    at all is refused rather than grandfathered in; and that the recorded owner is taken from
    the session rather than from anything the request body says.

    Validates: Requirements 21.1, 21.4, 21.6, 22.1
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterator, List, Optional

import pytest

os.environ.setdefault("ENV", "testing")
os.environ.setdefault("DEV_MODE", "true")

from backend_app.core.dependencies import (  # noqa: E402
    get_current_user,
    get_request_supabase,
)
from backend_app.worker import _status_key  # noqa: E402

OWNER = {
    "id": "usr_backtest_job_owner",
    "email": "owner@example.com",
    "role": "authenticated",
    "access_token": "token_owner",
}

INTRUDER = {
    "id": "usr_backtest_job_intruder",
    "email": "intruder@example.com",
    "role": "authenticated",
    "access_token": "token_intruder",
}

#: A well-formed body. The endpoint forwards it untouched, and nothing here executes it:
#: every test queues, so ``backtest_internal`` is never reached.
BODY: Dict[str, Any] = {
    "sync": False,
    "initial_capital": 10_000.0,
    "symbols": ["BTC/USDT"],
    "timeframe": "1h",
    "dag": {"nodes": [], "edges": []},
}

STATUS_PATH = "/api/strategies/backtest/{job_id}"
QUEUE_PATH = "/api/strategies/backtest"


# ---------------------------------------------------------------------------
# A Redis stand-in that records what the handlers actually wrote
# ---------------------------------------------------------------------------


class _FakePool:
    def __init__(self, hashes: Dict[str, Dict[str, str]]) -> None:
        self._hashes = hashes
        self.expiries: Dict[str, int] = {}

    async def hset(self, key: str, mapping: Optional[Dict[str, Any]] = None) -> None:
        entry = self._hashes.setdefault(key, {})
        for field, value in (mapping or {}).items():
            entry[field] = str(value)

    async def expire(self, key: str, seconds: int) -> None:
        self.expiries[key] = seconds


class _FakeRedis:
    """Only the two operations these handlers use: ``pool.hset``/``expire`` and ``hgetall``."""

    def __init__(self) -> None:
        self.hashes: Dict[str, Dict[str, str]] = {}
        self.pool = _FakePool(self.hashes)

    async def hgetall(self, key: str) -> Dict[str, str]:
        return dict(self.hashes.get(key, {}))


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr("backend_app.core.cache.redis_manager", fake, raising=True)
    return fake


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis) -> List[Dict[str, Any]]:
    """Records each publish, together with the status hash *as it stood at publish time*.

    The ordering matters on its own: a job that reached the stream before its owner was
    recorded would be readable by any caller for that window.
    """
    seen: List[Dict[str, Any]] = []

    async def _publish(job_id: str, payload: Dict[str, Any]) -> str:
        seen.append(
            {
                "job_id": job_id,
                "payload": payload,
                "hash_at_publish": dict(redis.hashes.get(_status_key(job_id), {})),
            }
        )
        return "1-0"

    monkeypatch.setattr("backend_app.core.event_bus.publish_backtest_job", _publish)
    return seen


# ---------------------------------------------------------------------------
# One client, two identities
#
# The identity is resolved per request from a mutable holder rather than by a second
# ``TestClient``: two clients over the same application would each install their own
# ``dependency_overrides[get_current_user]``, and the last one installed would answer for
# both — which is exactly how a cross-tenant test can pass while proving nothing.
# ---------------------------------------------------------------------------


class _Api:
    def __init__(self, client: Any, holder: Dict[str, Any]) -> None:
        self._client = client
        self._holder = holder

    def _as(self, user: Dict[str, Any]) -> None:
        self._holder["user"] = user

    def queue(self, user: Dict[str, Any], body: Optional[Dict[str, Any]] = None) -> Any:
        self._as(user)
        return self._client.post(QUEUE_PATH, json={**BODY, **(body or {})})

    def status(self, user: Dict[str, Any], job_id: str) -> Any:
        self._as(user)
        return self._client.get(STATUS_PATH.format(job_id=job_id))

    def queued_job_id(self, user: Dict[str, Any], body: Optional[Dict[str, Any]] = None) -> str:
        response = self.queue(user, body)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "queued", payload
        return payload["job_id"]


@pytest.fixture
def api() -> Iterator[_Api]:
    from fastapi.testclient import TestClient

    from backend_app.core.rate_limit import limiter
    from backend_app.main import app

    holder: Dict[str, Any] = {"user": OWNER}
    was_enabled = limiter.enabled
    limiter.enabled = False
    app.dependency_overrides[get_current_user] = lambda: holder["user"]
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        # Not entered as a context manager: that would run the application's lifespan, which
        # starts the real background workers and Redis connections this test has no need of.
        yield _Api(TestClient(app, raise_server_exceptions=False), holder)
    finally:
        app.dependency_overrides.clear()
        limiter.enabled = was_enabled


def _comparable(body: Dict[str, Any], job_id: str) -> Dict[str, Any]:
    """A refusal body with the two members that cannot be identical normalised.

    ``timestamp`` is the moment of the refusal and ``path`` echoes the identifier the caller
    itself supplied, so the job id is substituted rather than dropped: everything else has to
    match for Requirement 21.4's "byte-identical in shape and content" to hold.
    """
    normalised = {key: value for key, value in body.items() if key != "timestamp"}
    if isinstance(normalised.get("path"), str):
        normalised["path"] = normalised["path"].replace(job_id, "{job_id}")
    return normalised


# ---------------------------------------------------------------------------
# The owner's own answer is unchanged
# ---------------------------------------------------------------------------


def test_the_owner_of_a_queued_job_still_reads_its_status(api, redis, published):
    """Requirement 25.4: the surface still works for the caller it always worked for."""
    job_id = api.queued_job_id(OWNER)

    response = api.status(OWNER, job_id)

    assert response.status_code == 200, response.text
    assert response.json() == {"job_id": job_id, "status": "queued"}


def test_the_queued_status_hash_records_the_session_identity_before_publishing(
    api, redis, published
):
    """Validates: Requirements 21.1, 21.6"""
    job_id = api.queued_job_id(OWNER)

    assert redis.hashes[_status_key(job_id)]["user_id"] == OWNER["id"]
    assert published[0]["hash_at_publish"].get("user_id") == OWNER["id"]


def test_an_owner_identity_in_the_request_body_is_not_the_recorded_owner(api, redis, published):
    """A client-supplied identity buys nothing. Validates: Requirement 21.1"""
    job_id = api.queued_job_id(
        OWNER, {"user_id": INTRUDER["id"], "owner_id": INTRUDER["id"]}
    )

    assert redis.hashes[_status_key(job_id)]["user_id"] == OWNER["id"]


# ---------------------------------------------------------------------------
# The cross-tenant read
# ---------------------------------------------------------------------------


def test_a_stranger_holding_a_valid_job_id_is_refused_404_and_not_200(api, redis, published):
    """The regression this test exists for. Validates: Requirements 21.4, 22.1"""
    job_id = api.queued_job_id(OWNER)
    # The job is genuinely there: the refusal below is authorisation, not absence.
    assert redis.hashes[_status_key(job_id)]["user_id"] == OWNER["id"]

    response = api.status(INTRUDER, job_id)

    assert response.status_code == 404, (
        f"a second account read tenant {OWNER['id']}'s backtest job at "
        f"{STATUS_PATH.format(job_id=job_id)} and got {response.status_code}: {response.text}"
    )
    assert OWNER["id"] not in response.text
    assert "queued" not in response.text


def test_the_stranger_is_told_exactly_what_an_unknown_job_id_is_told(api, redis, published):
    """Validates: Requirement 21.4"""
    owners_job = api.queued_job_id(OWNER)
    unknown_job = "00000000-0000-4000-8000-00000000dead"
    assert _status_key(unknown_job) not in redis.hashes

    refused = api.status(INTRUDER, owners_job)
    absent = api.status(INTRUDER, unknown_job)

    assert refused.status_code == absent.status_code == 404
    assert _comparable(refused.json(), owners_job) == _comparable(absent.json(), unknown_job)


def test_the_owners_job_is_unchanged_by_the_refused_read(api, redis, published):
    """Validates: Requirement 21.4 — "SHALL leave the referenced record ... unchanged"."""
    job_id = api.queued_job_id(OWNER)
    before = dict(redis.hashes[_status_key(job_id)])

    api.status(INTRUDER, job_id)

    assert redis.hashes[_status_key(job_id)] == before
    assert api.status(OWNER, job_id).status_code == 200


def test_a_status_hash_carrying_no_owner_is_refused_rather_than_grandfathered(api, redis):
    """A job queued before ``user_id`` was recorded is denied, not opened to everyone.

    Fail-open is the wrong default for this fix; the hash carries a 24h TTL, so the set of
    ownerless jobs drains without intervention.

    Validates: Requirements 21.4, 22.1
    """
    legacy_job = "00000000-0000-4000-8000-0000000000aa"
    redis.hashes[_status_key(legacy_job)] = {"status": "completed", "result": "{}"}

    response = api.status(OWNER, legacy_job)

    assert response.status_code == 404, response.text
    assert "completed" not in response.text
