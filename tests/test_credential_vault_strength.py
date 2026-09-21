"""
tests/test_credential_vault_strength.py

Unit tests for ``_validate_credential_strength`` and for the call ordering inside
``CredentialVault.store_credential``.

WHY THIS FILE EXISTS
--------------------
``store_credential`` carried two undefined names that made it raise on every call, so the
live-trading onboarding path - storing an exchange API key - was completely dead:

  * ``_validate_credential_strength(value, credential_type)`` was called but never defined.
  * ``_validate_key_rotation_policy(credential_id, exchange_id)`` read ``credential_id``
    two lines before it was assigned.

WHAT IS TESTED
--------------
1. An absent, empty or whitespace-only value is rejected for every ``CredentialType``.
2. A value below the per-type floor is rejected.
3. Realistic venue-issued key material is ACCEPTED - the tests below carry the shapes the
   supported exchanges actually issue, including Coinbase's PEM private key with newlines
   and Kraken's base64 with ``+/=``. This direction matters more than the rejection cases:
   the check had never once executed, so a floor or format rule set too aggressively would
   lock existing traders out of connecting a working account.
4. The rejection message never contains the credential value.
5. ``store_credential`` completes without ``NameError`` or ``UnboundLocalError`` and
   returns a credential whose id was derived before the rotation-policy check read it.
"""

import inspect

import pytest
from cryptography.fernet import Fernet

from backend_app.core import credential_vault as vault_module
from backend_app.core.credential_vault import (
    CredentialType,
    CredentialVault,
    _minimum_credential_length,
    _validate_credential_strength,
)


# Shapes real venues issue. Lengths and character sets are the point of each entry.
REAL_WORLD_CREDENTIALS = [
    # Binance: 64-character alphanumeric key and secret.
    (CredentialType.API_KEY, "vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A"),
    (CredentialType.SECRET_KEY, "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"),
    # Bybit: the shortest key observed across supported venues.
    (CredentialType.API_KEY, "XXXXXXXXXXXXXXXXXX"),
    (CredentialType.API_SECRET, "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"),
    # OKX: UUID key plus a user-chosen passphrase.
    (CredentialType.API_KEY, "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"),
    (CredentialType.PASSPHRASE, "Tr@d3r"),
    # Kraken: base64 private key, so + / = must all pass.
    (
        CredentialType.SECRET_KEY,
        "kQH+NnFz6iyqTqJ9Eo1nq6Ck3nJ2t0kZ8s4/ZkLmNpQrStUvWxYz0123456789ab+cd/ef==",
    ),
    # Coinbase Advanced Trade: an EC private key in PEM, newlines and all. A maximum-length
    # or charset rule would reject this.
    (
        CredentialType.SECRET_KEY,
        "-----BEGIN EC PRIVATE KEY-----\n"
        "MHcCAQEEIBx3kQfLmNoPqRsTuVwXyZ0123456789abcdefghijklmnoAoGCCqGSM49\n"
        "AwEHoUQDQgAEabcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQR\n"
        "-----END EC PRIVATE KEY-----\n",
    ),
]


class TestValidateCredentialStrengthRejects:
    @pytest.mark.parametrize("credential_type", list(CredentialType))
    @pytest.mark.parametrize("blank", ["", "   ", "\t", "\n", "  \r\n  "])
    def test_blank_value_rejected_for_every_type(self, credential_type, blank):
        with pytest.raises(ValueError) as exc_info:
            _validate_credential_strength(blank, credential_type)
        assert "empty or whitespace-only" in str(exc_info.value)

    @pytest.mark.parametrize("credential_type", list(CredentialType))
    def test_none_rejected_for_every_type(self, credential_type):
        with pytest.raises(ValueError):
            _validate_credential_strength(None, credential_type)

    @pytest.mark.parametrize("credential_type", list(CredentialType))
    def test_value_one_character_below_floor_rejected(self, credential_type):
        minimum = _minimum_credential_length(credential_type)
        too_short = "a" * (minimum - 1)
        with pytest.raises(ValueError) as exc_info:
            _validate_credential_strength(too_short, credential_type)
        assert "too short" in str(exc_info.value)

    @pytest.mark.parametrize("credential_type", list(CredentialType))
    def test_message_never_contains_the_credential_value(self, credential_type):
        secret = "s3cr3t" [: max(1, _minimum_credential_length(credential_type) - 1)]
        with pytest.raises(ValueError) as exc_info:
            _validate_credential_strength(secret, credential_type)
        assert secret not in str(exc_info.value)
        assert credential_type.value in str(exc_info.value)


class TestValidateCredentialStrengthAccepts:
    @pytest.mark.parametrize(
        "credential_type,value",
        REAL_WORLD_CREDENTIALS,
        ids=[f"{t.value}-{len(v)}chars" for t, v in REAL_WORLD_CREDENTIALS],
    )
    def test_real_venue_issued_material_accepted(self, credential_type, value):
        # Returns None on success; any raise here is a trader locked out of onboarding.
        assert _validate_credential_strength(value, credential_type) is None

    @pytest.mark.parametrize("credential_type", list(CredentialType))
    def test_value_exactly_at_floor_accepted(self, credential_type):
        minimum = _minimum_credential_length(credential_type)
        assert _validate_credential_strength("a" * minimum, credential_type) is None

    def test_floors_stay_below_the_shortest_real_key(self):
        """No floor may climb above the shortest key any supported venue issues."""
        shortest_venue_key = 18  # Bybit
        for credential_type in (
            CredentialType.API_KEY,
            CredentialType.SECRET_KEY,
            CredentialType.API_SECRET,
        ):
            assert _minimum_credential_length(credential_type) < shortest_venue_key


class TestStoreCredentialOrdering:
    def test_credential_id_is_assigned_before_the_rotation_policy_reads_it(self):
        """The use-before-assignment that made every store raise cannot come back."""
        source = inspect.getsource(CredentialVault.store_credential)
        assignment = source.index("credential_id = self._generate_credential_id")
        policy_call = source.index("_validate_key_rotation_policy(credential_id")
        assert assignment < policy_call, (
            "_validate_key_rotation_policy reads credential_id before it is assigned; "
            "store_credential raises UnboundLocalError on every call"
        )

    def test_strength_check_runs_before_anything_is_encrypted_or_written(self):
        source = inspect.getsource(CredentialVault.store_credential)
        strength_call = source.index("_validate_credential_strength(value")
        encrypt_call = source.index("self._encrypt(")
        assert strength_call < encrypt_call

    def test_both_module_level_validators_exist(self):
        assert callable(getattr(vault_module, "_validate_credential_strength", None))
        assert callable(getattr(vault_module, "_validate_key_rotation_policy", None))

    @pytest.mark.asyncio
    async def test_store_credential_completes_and_returns_a_credential(self, monkeypatch):
        monkeypatch.setenv("MASTER_ENCRYPTION_KEYS", Fernet.generate_key().decode())
        monkeypatch.setenv("CREDENTIAL_VAULT_SALT", "store_credential_ordering_salt")

        vault = CredentialVault()
        credential = await vault.store_credential(
            user_id="user-ordering-1",
            tenant_id="tenant-ordering-1",
            exchange_id="binance",
            credential_type=CredentialType.API_KEY,
            value="vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A",
        )

        assert credential.credential_id == vault._generate_credential_id(
            "user-ordering-1", "binance", CredentialType.API_KEY
        )
        assert credential.tenant_id == "tenant-ordering-1"
        # The plaintext must not be recoverable from the stored record without the vault.
        assert "vmPUZE6mv9SD" not in credential.encrypted_value
        assert vault._decrypt(credential.encrypted_value) == (
            "vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A"
        )

    @pytest.mark.asyncio
    async def test_store_credential_refuses_a_blank_value(self, monkeypatch):
        monkeypatch.setenv("MASTER_ENCRYPTION_KEYS", Fernet.generate_key().decode())
        monkeypatch.setenv("CREDENTIAL_VAULT_SALT", "store_credential_blank_salt")

        vault = CredentialVault()
        with pytest.raises(ValueError):
            await vault.store_credential(
                user_id="user-ordering-2",
                tenant_id="tenant-ordering-2",
                exchange_id="binance",
                credential_type=CredentialType.API_KEY,
                value="   ",
            )
