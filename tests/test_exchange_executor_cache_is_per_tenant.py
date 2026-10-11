"""The executor cache must never serve one account's API credentials to another.

WHAT THIS GUARDS
----------------
``backend_app.backend.exchange_executor.get_exchange_executor`` memoises executors in a
process-global dict. Its key used to be ``f"{exchange_id}:{sandbox}"``, which names the
venue and the mode but *not the account*. The only production caller,
``BotRunner._recover_live_state`` (``backend/master_executor.py``), passes the api key and
secret it has just decrypted out of the vault for one specific user. So in a worker process
that had already started a binance bot for trader A, trader B's call matched A's cache entry
and was handed back A's executor — still holding A's api key and secret.

``CCXTExchangeExecutor.place_order`` signs with ``self._api_key`` / ``self._api_secret``.
The consequence was therefore not a leak of data but a leak of FUNDS: every order trader B's
strategy produced would have been placed on trader A's exchange account, against A's balance,
and would have appeared in A's trade history.

The defect is a cache-key bug, so these tests assert on cache identity rather than on
anything that needs a live venue: no network, no credentials, no ccxt client is constructed
beyond the in-process object.
"""

import pytest

from backend_app.backend import exchange_executor as ee
from backend_app.backend.exchange_executor import (get_exchange_executor,
                                                   release_exchange_executor)

USER_A = "aaaaaaaa-0000-0000-0000-000000000001"
USER_B = "bbbbbbbb-0000-0000-0000-000000000002"

A_KEY, A_SECRET = "USER_A_KEY", "USER_A_SECRET"
B_KEY, B_SECRET = "USER_B_KEY", "USER_B_SECRET"


@pytest.fixture(autouse=True)
def _isolate_executor_cache():
    """Each test gets an empty cache and leaves one behind.

    The cache is a module global, so without this a test that populates it changes the
    starting conditions of every later test in the session — which is the same class of
    shared-state defect the subject of this file is.
    """
    saved = dict(ee._executors)
    ee._executors.clear()
    try:
        yield
    finally:
        ee._executors.clear()
        ee._executors.update(saved)


def test_two_users_on_the_same_exchange_get_separate_executors():
    """THE DEFECT. Same venue, same mode, different accounts -> must not be shared."""
    a = get_exchange_executor(
        exchange_id="binance",
        api_key=A_KEY,
        api_secret=A_SECRET,
        sandbox=False,
        user_id=USER_A,
    )
    b = get_exchange_executor(
        exchange_id="binance",
        api_key=B_KEY,
        api_secret=B_SECRET,
        sandbox=False,
        user_id=USER_B,
    )

    assert a is not b, (
        "get_exchange_executor returned one executor for two different accounts on the "
        "same exchange. Trader B's orders would be signed with trader A's credentials."
    )


def test_second_user_never_receives_the_first_users_credentials():
    """The consequence, asserted directly on the credentials the executor would sign with."""
    get_exchange_executor(
        exchange_id="binance",
        api_key=A_KEY,
        api_secret=A_SECRET,
        sandbox=False,
        user_id=USER_A,
    )
    b = get_exchange_executor(
        exchange_id="binance",
        api_key=B_KEY,
        api_secret=B_SECRET,
        sandbox=False,
        user_id=USER_B,
    )

    assert b._api_key == B_KEY
    assert b._api_secret == B_SECRET
    assert b._api_key != A_KEY, "executor for user B holds user A's api key"
    assert b._api_secret != A_SECRET, "executor for user B holds user A's secret"


def test_the_same_user_still_reuses_one_executor():
    """The cache must still CACHE. A fix that returns a new client per order would
    defeat the purpose of the pool and multiply the venue connection count per tick."""
    first = get_exchange_executor(
        exchange_id="binance",
        api_key=A_KEY,
        api_secret=A_SECRET,
        sandbox=False,
        user_id=USER_A,
    )
    second = get_exchange_executor(
        exchange_id="binance",
        api_key=A_KEY,
        api_secret=A_SECRET,
        sandbox=False,
        user_id=USER_A,
    )

    assert first is second
    assert len(ee._executors) == 1


def test_rotating_credentials_replaces_the_cached_executor():
    """A trader who rotates a compromised key must not keep being served the revoked one.

    Without the credential component in the cache key, the executor built from the old
    secret would be returned until the worker restarted, so every order would be rejected
    by the venue and the trader would have no way to recover.
    """
    old = get_exchange_executor(
        exchange_id="binance",
        api_key=A_KEY,
        api_secret=A_SECRET,
        sandbox=False,
        user_id=USER_A,
    )
    rotated = get_exchange_executor(
        exchange_id="binance",
        api_key="USER_A_KEY_ROTATED",
        api_secret="USER_A_SECRET_ROTATED",
        sandbox=False,
        user_id=USER_A,
    )

    assert rotated is not old
    assert rotated._api_key == "USER_A_KEY_ROTATED"


def test_sandbox_and_live_are_separate_for_one_user():
    """Testnet and mainnet credentials are different credentials on different endpoints."""
    live = get_exchange_executor(
        exchange_id="binance",
        api_key=A_KEY,
        api_secret=A_SECRET,
        sandbox=False,
        user_id=USER_A,
    )
    test = get_exchange_executor(
        exchange_id="binance",
        api_key=A_KEY,
        api_secret=A_SECRET,
        sandbox=True,
        user_id=USER_A,
    )

    assert live is not test
    assert live.sandbox is False
    assert test.sandbox is True


@pytest.mark.parametrize("missing", ["", "   ", None])
def test_a_missing_user_id_is_refused_rather_than_defaulted(missing):
    """Fails CLOSED. A default would silently restore the shared-cache defect for any
    caller that forgot the argument, which is exactly how the original bug survived."""
    with pytest.raises(ValueError, match="user_id is required"):
        get_exchange_executor(
            exchange_id="binance",
            api_key=A_KEY,
            api_secret=A_SECRET,
            sandbox=False,
            user_id=missing,
        )


def test_user_id_cannot_be_passed_positionally_into_a_credential_slot():
    """``user_id`` is keyword-only, so it cannot land in the ``sandbox``/``password``
    position and be mistaken for one."""
    with pytest.raises(TypeError):
        get_exchange_executor("binance", A_KEY, A_SECRET, False, None, USER_A)


def test_releasing_an_executor_unpins_the_decrypted_secret():
    """A stopped bot should not keep its decrypted api secret reachable in the cache
    for the remaining lifetime of the worker process."""
    get_exchange_executor(
        exchange_id="binance",
        api_key=A_KEY,
        api_secret=A_SECRET,
        sandbox=False,
        user_id=USER_A,
    )
    assert len(ee._executors) == 1

    removed = release_exchange_executor(
        user_id=USER_A,
        exchange_id="binance",
        sandbox=False,
        api_key=A_KEY,
        api_secret=A_SECRET,
    )

    assert removed is True
    assert ee._executors == {}


def test_cache_key_fields_cannot_be_rearranged_into_a_collision():
    """The key is a length-prefixed digest, so ("a","bc") and ("ab","c") must differ.

    A plain concatenation would let an attacker-chosen user id and exchange id collide
    with another pair, which would reintroduce the cross-account leak through the back
    door.
    """
    one = ee._executor_cache_key(
        user_id="a", exchange_id="bc", sandbox=False, api_key="k", api_secret="s", password=None
    )
    two = ee._executor_cache_key(
        user_id="ab", exchange_id="c", sandbox=False, api_key="k", api_secret="s", password=None
    )
    assert one != two


def test_the_production_caller_passes_a_user_id():
    """``BotRunner._recover_live_state`` is the only caller, and it is the one that holds
    the decrypted vault keys. If it ever stops passing ``user_id`` the call now raises —
    this test says so at the source level so the failure is a named regression rather
    than a runtime ValueError inside a live bot's startup.

    Parsed with ``ast`` rather than by string slicing: the call's arguments contain their
    own parentheses (``self.blueprint.get("testnet", False)``), so a scan for the first
    ``)`` truncates the argument list halfway and the check silently stops meaning
    anything.
    """
    import ast
    import inspect

    from backend_app.backend import master_executor

    tree = ast.parse(inspect.getsource(master_executor))

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_exchange_executor"
    ]

    assert calls, "no call to get_exchange_executor found in master_executor"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords if kw.arg}
        assert "user_id" in kwargs, (
            f"master_executor line {call.lineno} calls get_exchange_executor without "
            "user_id; the executor cache would not be able to distinguish accounts."
        )
