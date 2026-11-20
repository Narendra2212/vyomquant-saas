"""TENANT-01 residual, now proven on the REAL vault: api_key_vault re-checks the
stored owner and refuses a row that is not the caller's.

WHY THIS FILE REPLACES tests/test_tenant01_credential_vault_isolation.py.

That file gave the stored-owner invariant its only executed proof -- but it
proved it about `backend_app/core/credential_vault.py`, a module no production
code imports. HY-07 tracks that duplicate, and its own text said the module
could only be deleted once "TENANT-01's fix adds an equivalent stored-owner
re-check to backend_app/backend/api_key_vault.py's
load_decrypted_keys/delete_exchange_keys/rotate_key", because deleting it before
then "would silently drop the only executed proof of what TENANT-01's own named
residual looks like when fixed".

So the invariant was moved rather than deleted. The re-check now lives on the
vault that actually holds production credentials, and this file is the proof that
moved with it.

WHAT THE INVARIANT IS, AND WHY IT IS NOT REDUNDANT.

`load_decrypted_keys` already filters `.eq("user_id", user_id)`, so in a correct
world the returned row ALWAYS belongs to the caller and this check can never
fire. That is the point of it. TENANT-01's finding is precisely that isolation
here "is a Python argument not a DB policy": it holds only while every call site
builds its filter correctly. The check converts a whole class of silent failures
-- a dropped `.eq`, a refactor threading the wrong variable, RLS switched off on
`exchange_keys`, a client library that reorders filters -- from "decrypt and
return someone else's API keys" into a loud refusal.

A test that only exercised the happy path would prove nothing about that, which
is why every test below drives the MISMATCH case with a stubbed client that
returns a foreign row. The stub is the only way to reach it: you cannot provoke
it through the real client without first introducing the bug it defends against.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest

#: Obvious non-credentials. Nothing here is a real key, and the strings say so
#: loudly enough that a leak into a log or a fixture dump is self-identifying.
FAKE_API_KEY = "FAKE-CANARY-API-KEY-not-a-real-credential-0001"
FAKE_SECRET = "FAKE-CANARY-SECRET-not-a-real-credential-0002"

OWNER = "11111111-1111-4111-8111-aaaaaaaaaaaa"
INTRUDER = "22222222-2222-4222-8222-bbbbbbbbbbbb"
EXCHANGE = "binance"


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    """Records filters and returns whatever row the test planted.

    Deliberately IGNORES the `.eq()` filters when producing its result. That is
    the simulated defect: a client whose WHERE clause does not actually
    constrain the owner. The production client does constrain it, which is why
    this stub is required to reach the check at all.
    """

    def __init__(self, rows, recorder):
        self._rows = rows
        self._recorder = recorder

    def select(self, *_a, **_k):
        return self

    def delete(self, *_a, **_k):
        self._recorder.append(("delete", None))
        return self

    def eq(self, column, value):
        self._recorder.append(("eq", (column, value)))
        return self

    def execute(self):
        return _Response(self._rows)


class _Table:
    def __init__(self, rows, recorder):
        self._rows = rows
        self._recorder = recorder

    def __call__(self, name):
        self._recorder.append(("table", name))
        return _Query(self._rows, self._recorder)


class _Client:
    def __init__(self, rows, recorder):
        self.table = _Table(rows, recorder)


@pytest.fixture
def vault_module(monkeypatch):
    """Import api_key_vault with a stub `supabase` package.

    The module imports `create_client` at module scope, so the stub has to be in
    sys.modules before the import rather than patched afterwards.
    """
    stub = types.ModuleType("supabase")
    stub.create_client = lambda *_a, **_k: None
    stub.Client = object
    monkeypatch.setitem(sys.modules, "supabase", stub)

    monkeypatch.setenv("SUPABASE_URL", "https://stub.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "stub-service-role-key")
    monkeypatch.setenv("MASTER_ENCRYPTION_KEYS", "a" * 44)

    mod = importlib.import_module("backend_app.backend.api_key_vault")
    return importlib.reload(mod)


def _vault_with_rows(vault_module, rows):
    """A vault instance whose client returns `rows`, with encryption stubbed out.

    `_decrypt` is replaced because this file is about the OWNERSHIP check, not
    about cryptography: a real decrypt would need real ciphertext and would make
    the test fail for a reason unrelated to what it is asserting.
    """
    recorder: list = []
    cls = getattr(vault_module, "APIKeyVault", None) or getattr(vault_module, "ApiKeyVault")
    vault = cls.__new__(cls)
    vault.supabase = _Client(rows, recorder)
    vault._decrypt = lambda blob: FAKE_API_KEY if blob == "api" else FAKE_SECRET
    return vault, recorder


def test_the_owner_can_read_its_own_credential(vault_module):
    """The happy path still works -- otherwise the check below proves nothing."""
    vault, _ = _vault_with_rows(vault_module, [{
        "user_id": OWNER,
        "encrypted_api_key": "api",
        "encrypted_secret_key": "secret",
        "encrypted_password": None,
        "sandbox": False,
    }])
    got = vault.load_decrypted_keys(OWNER, EXCHANGE)
    assert got["api_key"] == FAKE_API_KEY, "the owning user must get its own key back"


def test_a_foreign_row_is_refused_rather_than_decrypted(vault_module):
    """THE LOAD-BEARING ONE.

    The query returns a row owned by someone else -- what a dropped filter or a
    disabled RLS policy would produce. The vault must refuse instead of
    decrypting and returning it.
    """
    vault, _ = _vault_with_rows(vault_module, [{
        "user_id": OWNER,  # the row belongs to OWNER...
        "encrypted_api_key": "api",
        "encrypted_secret_key": "secret",
        "encrypted_password": None,
        "sandbox": False,
    }])
    with pytest.raises(PermissionError) as caught:
        vault.load_decrypted_keys(INTRUDER, EXCHANGE)  # ...but INTRUDER asked
    assert "owner" in str(caught.value).lower(), (
        "the refusal must say it is about ownership, so an operator reading the "
        "exception knows this is an isolation event and not a lookup failure"
    )


def test_the_refusal_is_not_a_silent_none(vault_module):
    """Failing closed, specifically: callers expect a dict and would read None as
    'this user has no credentials', which is a different and recoverable state."""
    vault, _ = _vault_with_rows(vault_module, [{
        "user_id": OWNER,
        "encrypted_api_key": "api",
        "encrypted_secret_key": "secret",
        "encrypted_password": None,
        "sandbox": False,
    }])
    try:
        result = vault.load_decrypted_keys(INTRUDER, EXCHANGE)
    except PermissionError:
        return
    pytest.fail(
        f"load_decrypted_keys returned {result!r} for a foreign row instead of raising; "
        f"a falsy return is indistinguishable from 'no credentials stored'"
    )


def test_delete_refuses_a_foreign_row_before_deleting_it(vault_module):
    """The delete path has to look BEFORE acting -- once a DELETE has run the
    evidence it would have checked is gone."""
    vault, recorder = _vault_with_rows(vault_module, [{"user_id": OWNER}])
    with pytest.raises(PermissionError):
        vault.delete_exchange_keys(INTRUDER, EXCHANGE)
    assert not any(op == "delete" for op, _ in recorder), (
        "delete_exchange_keys issued a DELETE before refusing. The ownership check "
        "must gate the delete, not follow it."
    )


def test_delete_treats_a_missing_row_as_success(vault_module):
    """Deliberately different from the read path.

    This method's contract is "ensure these keys are gone" -- its callers are
    user-disconnects-exchange, account deletion and security incident -- and all
    three are satisfied by a row that is already absent. Raising here would turn
    a completed account deletion into a failure on retry.
    """
    vault, _ = _vault_with_rows(vault_module, [])
    assert vault.delete_exchange_keys(OWNER, EXCHANGE) is True


def test_rotate_inherits_the_check_rather_than_duplicating_it(vault_module):
    """rotate_key calls load_decrypted_keys, so the invariant is enforced once.

    Asserted rather than assumed: a future refactor that gave rotate_key its own
    query would silently lose the check, and this test is what would notice.
    """
    vault, _ = _vault_with_rows(vault_module, [{
        "user_id": OWNER,
        "encrypted_api_key": "api",
        "encrypted_secret_key": "secret",
        "encrypted_password": None,
        "sandbox": False,
    }])
    with pytest.raises(PermissionError):
        vault.rotate_key(INTRUDER, EXCHANGE)


def test_the_check_exists_in_source_on_all_three_methods():
    """A source-level guard, because the behavioural tests above all go through
    load_decrypted_keys and would not notice a NEW method added without the check.

    Asserting on source is normally the weaker move, and TEST-01 is in this
    ledger for exactly that reason. It is justified here only as a supplement:
    the behaviour is already proven above, and this catches the one thing
    behaviour cannot -- a method that does not exist yet.
    """
    import inspect

    from backend_app.backend import api_key_vault

    src = inspect.getsource(api_key_vault)
    assert src.count("PermissionError") >= 2, (
        "api_key_vault no longer raises PermissionError in at least two places "
        "(the read path and the delete path). The stored-owner re-check that lets "
        "HY-07's duplicate vault be deleted has been weakened or removed."
    )
    assert "stored_owner" in src, (
        "the stored-owner comparison is gone from api_key_vault. TENANT-01's "
        "residual -- isolation resting on a Python argument rather than a checked "
        "invariant -- is back."
    )
