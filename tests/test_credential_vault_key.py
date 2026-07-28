"""
tests/test_credential_vault_key.py

Unit tests for CredentialVault initialization, environment variable resolution, salt requirements, and encrypt/decrypt roundtrip.

WHAT IS TESTED
-------------
1. CredentialVault raises RuntimeError when MASTER_ENCRYPTION_KEYS is unset.
2. CredentialVault raises RuntimeError when CREDENTIAL_VAULT_SALT is unset.
3. CredentialVault initializes successfully when both MASTER_ENCRYPTION_KEYS and CREDENTIAL_VAULT_SALT are set.
4. CredentialVault correctly parses comma-separated keys in MASTER_ENCRYPTION_KEYS.
5. CredentialVault successfully encrypts and decrypts credential values using the deployment-unique salt.
6. Different salt values produce different Fernet ciphers, making credentials encrypted with one salt unreadable under another.
"""

import os
import pytest
from cryptography.fernet import Fernet, InvalidToken

from backend_app.core.credential_vault import CredentialVault, CredentialType


class TestCredentialVaultKeyResolution:
    def test_missing_master_key_raises_runtime_error(self, monkeypatch):
        monkeypatch.delenv("MASTER_ENCRYPTION_KEYS", raising=False)
        monkeypatch.delenv("CREDENTIAL_VAULT_KEY", raising=False)
        monkeypatch.setenv("CREDENTIAL_VAULT_SALT", "test_deployment_salt_123")
        
        with pytest.raises(RuntimeError) as exc_info:
            CredentialVault()
        assert "MASTER_ENCRYPTION_KEYS environment variable is missing" in str(exc_info.value)

    def test_missing_salt_raises_runtime_error(self, monkeypatch):
        test_key = Fernet.generate_key().decode()
        monkeypatch.setenv("MASTER_ENCRYPTION_KEYS", test_key)
        monkeypatch.delenv("CREDENTIAL_VAULT_SALT", raising=False)
        
        with pytest.raises(RuntimeError) as exc_info:
            CredentialVault()
        assert "CREDENTIAL_VAULT_SALT environment variable is missing" in str(exc_info.value)

    def test_valid_master_key_and_salt_initialization(self, monkeypatch):
        test_key = Fernet.generate_key().decode()
        test_salt = "prod_secret_salt_xyz987"
        monkeypatch.setenv("MASTER_ENCRYPTION_KEYS", test_key)
        monkeypatch.setenv("CREDENTIAL_VAULT_SALT", test_salt)
        
        vault = CredentialVault()
        assert vault.encryption_key == test_key
        assert vault.salt == test_salt.encode("utf-8")

    def test_comma_separated_master_keys_uses_first(self, monkeypatch):
        key1 = Fernet.generate_key().decode()
        key2 = Fernet.generate_key().decode()
        monkeypatch.setenv("MASTER_ENCRYPTION_KEYS", f"{key1},{key2}")
        monkeypatch.setenv("CREDENTIAL_VAULT_SALT", "test_deployment_salt_123")
        
        vault = CredentialVault()
        assert vault.encryption_key == key1

    def test_encrypt_decrypt_roundtrip(self, monkeypatch):
        test_key = Fernet.generate_key().decode()
        monkeypatch.setenv("MASTER_ENCRYPTION_KEYS", test_key)
        monkeypatch.setenv("CREDENTIAL_VAULT_SALT", "unique_env_salt_456")
        
        vault = CredentialVault()
        salt = vault._generate_salt()
        original_secret = "sk_live_1234567890abcdef"
        
        encrypted = vault._encrypt(original_secret, salt)
        decrypted = vault._decrypt(encrypted)
        
        assert decrypted == original_secret
        assert encrypted != original_secret

    def test_changing_salt_changes_derived_key_preventing_decryption(self, monkeypatch):
        test_key = Fernet.generate_key().decode()
        original_secret = "super_secret_exchange_api_key"
        
        # Deploy 1 with salt A
        vault_a = CredentialVault(encryption_key=test_key, salt="deployment_salt_A")
        cred_salt = vault_a._generate_salt()
        encrypted_a = vault_a._encrypt(original_secret, cred_salt)
        
        # Deploy 2 with salt B (same master key, different salt)
        vault_b = CredentialVault(encryption_key=test_key, salt="deployment_salt_B")
        
        # Attempting to decrypt encrypted_a using vault_b should raise InvalidToken
        with pytest.raises(InvalidToken):
            vault_b._decrypt(encrypted_a)

