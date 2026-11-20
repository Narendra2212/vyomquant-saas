"""TENANT-01 residual, given explicit negative-control coverage: the credential vault's
tenant isolation is a Python ownership check, and it actually denies cross-tenant reads.

WHY THIS TEST EXISTS
--------------------
TENANT-01 (P2, OPEN) records several residual isolation gaps. One is that
`backend_app/core/credential_vault.py::CredentialVault.get_credential` reaches encrypted
credentials in Redis with "a Python argument as the only isolation" -- there is no RLS
policy behind it, because the store is Redis, not Postgres. The audit's decision to keep
TENANT-01 open stands; this file does NOT change that architecture. What it does is give
that specific residual a real, executed negative control, so "the Python check denies a
cross-tenant read" is a tested fact rather than a code comment -- and so a regression that
weakened the check (e.g. dropping the stored-tenant re-verification) fails loudly.

THE TWO ISOLATION LAYERS, BOTH EXERCISED
----------------------------------------
1. KEY SCOPING. The Redis key is `tenant:{tenant_id}:user:{user_id}:credential:{id}`, so a
   different tenant's read targets a different key and misses -- a cross-tenant read gets
   None without the stored row ever being consulted.
2. THE STORED-TENANT RE-CHECK. Even when a row's STORED tenant_id disagrees with the key it
   is filed under -- the state a mis-scoped write would leave behind -- `get_credential`
   re-verifies `credential_dict["tenant_id"] == tenant_id` and refuses on mismatch. A
   refusal returns None, INDISTINGUISHABLE from an absence, so the caller learns nothing
   about whether the credential exists.

No real credential, no network, no production. Redis is an in-memory double, exactly as
`tests/regression/capture_baseline.py::capture_credential_vault` and
`tests/test_exchange_credential_log_redaction.py` use. Fixtures are obviously fake.
"""

from __future__ import annotations

import asyncio
import json
from typing import Dict, List, Optional

import pytest

FAKE_SECRET_A = "FAKE-CANARY-TENANT-A-SECRET-not-a-real-credential-0001"
FAKE_SECRET_B = "FAKE-CANARY-TENANT-B-SECRET-not-a-real-credential-0002"

TENANT_A = "11111111-1111-4111-8111-aaaaaaaaaaaa"
TENANT_B = "22222222-2222-4222-8222-bbbbbbbbbbbb"
USER_A = "aaaa1111-1111-4111-8111-aaaaaaaaaaaa"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _RecordingRedis:
    """In-memory Redis double covering exactly the calls CredentialVault makes."""

    def __init__(self) -> None:
        self.store: Dict[str, str] = {}

    async def get(self, key: str) -> Optional[str]:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def lpush(self, key: str, value: str) -> None:
        pass

    async def keys(self, pattern: str) -> List[str]:
        return [k for k in self.store if k.startswith(pattern.rstrip("*"))]


@pytest.fixture
def vault_and_redis(monkeypatch):
    from backend_app.core import credential_vault as vault_module

    redis_double = _RecordingRedis()
    monkeypatch.setattr(vault_module, "redis_manager", redis_double)
    vault = vault_module.CredentialVault(
        encryption_key="tenant01-isolation-master-material-0123456789abcd",
        salt="tenant01-isolation-salt",
    )
    return vault_module, vault, redis_double


def _store(vault, vault_module, *, user_id, tenant_id, secret):
    return _run(
        vault.store_credential(
            user_id=user_id,
            tenant_id=tenant_id,
            exchange_id="binance",
            credential_type=vault_module.CredentialType.API_SECRET,
            value=secret,
        )
    )


def _get(vault, vault_module, *, user_id, tenant_id):
    return _run(
        vault.get_credential(
            user_id=user_id,
            tenant_id=tenant_id,
            exchange_id="binance",
            credential_type=vault_module.CredentialType.API_SECRET,
        )
    )


def test_the_owning_tenant_can_read_its_own_credential(vault_and_redis):
    """Positive control: without it, a vault that denied everything would look isolated."""
    vault_module, vault, _redis = vault_and_redis
    _store(vault, vault_module, user_id=USER_A, tenant_id=TENANT_A, secret=FAKE_SECRET_A)
    got = _get(vault, vault_module, user_id=USER_A, tenant_id=TENANT_A)
    assert got == FAKE_SECRET_A, "the owning tenant must get its own plaintext back"


def test_a_different_tenant_cannot_read_through_the_scoped_key(vault_and_redis):
    """Layer 1: the key is tenant-scoped, so tenant B's read targets a different key.

    Tenant A stores a credential. Tenant B, using the SAME user_id and exchange but its OWN
    tenant_id, reads None -- the key `tenant:B:user:...:credential:...` does not exist.
    """
    vault_module, vault, _redis = vault_and_redis
    _store(vault, vault_module, user_id=USER_A, tenant_id=TENANT_A, secret=FAKE_SECRET_A)

    leaked = _get(vault, vault_module, user_id=USER_A, tenant_id=TENANT_B)
    assert leaked is None, (
        "tenant B read tenant A's credential through the user+exchange pair -- the "
        "tenant-scoped key did not isolate it"
    )
    assert leaked != FAKE_SECRET_A


def test_a_mis_scoped_row_is_refused_by_the_stored_tenant_recheck(vault_and_redis):
    """Layer 2: even when the STORED tenant disagrees with the key, the re-check refuses.

    This is the state a mis-scoped write would leave behind: a row filed under tenant A's
    key but whose body claims tenant B. `get_credential` re-verifies the stored tenant_id
    against the requesting tenant_id and refuses when they disagree. The refusal is None --
    indistinguishable from the credential being absent -- so no existence oracle leaks.
    """
    vault_module, vault, redis = vault_and_redis

    _store(vault, vault_module, user_id=USER_A, tenant_id=TENANT_A, secret=FAKE_SECRET_A)
    # Locate the CREDENTIAL key (the vault also writes access-log keys) by its documented
    # prefix shape `tenant:{tenant_id}:user:{user_id}:credential:{id}`, and corrupt its
    # body's tenant_id to a DIFFERENT tenant, leaving it filed under tenant A's key -- the
    # mis-scoped-write state the second isolation layer defends against.
    credential_keys = [
        k for k in redis.store
        if k.startswith(f"tenant:{TENANT_A}:user:{USER_A}:credential:")
        and ":credential_access_log:" not in k
    ]
    assert len(credential_keys) == 1, (
        f"expected exactly one credential key under tenant A, found {credential_keys}"
    )
    key = credential_keys[0]
    body = json.loads(redis.store[key])
    body["tenant_id"] = TENANT_B
    redis.store[key] = json.dumps(body)

    # Tenant A requests it (A owns the KEY), but the body now claims tenant B.
    got = _get(vault, vault_module, user_id=USER_A, tenant_id=TENANT_A)
    assert got is None, (
        "the stored-tenant re-check did not refuse a row whose body claims a different "
        "tenant than the key it is filed under -- the second isolation layer is gone"
    )
    # And the refusal is indistinguishable from an absence (both return None), so a caller
    # cannot use the response to infer that a credential exists for another tenant.
    absent = _get(vault, vault_module, user_id="no-such-user", tenant_id=TENANT_A)
    assert absent is None
    assert got == absent


def test_two_tenants_credentials_do_not_bleed_into_each_other(vault_and_redis):
    """Both tenants store under the same user+exchange shape; each reads only its own."""
    vault_module, vault, _redis = vault_and_redis

    _store(vault, vault_module, user_id=USER_A, tenant_id=TENANT_A, secret=FAKE_SECRET_A)
    _store(vault, vault_module, user_id=USER_A, tenant_id=TENANT_B, secret=FAKE_SECRET_B)

    assert _get(vault, vault_module, user_id=USER_A, tenant_id=TENANT_A) == FAKE_SECRET_A
    assert _get(vault, vault_module, user_id=USER_A, tenant_id=TENANT_B) == FAKE_SECRET_B
