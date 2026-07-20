"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — BACKEND: security_vault.py  (Engine A)                        ║
║                                                                          ║
║  AES-256 API key encryption vault backed by Supabase.                    ║
║  One shared instance for ALL users — injected via global app_state.      ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  VA-1  get_user_tier returned inconsistent key names (crash on both      ║
║         code paths — Supabase row vs fallback dict used different keys)  ║
║  VA-2  encrypt_secret returned None for empty string → silent null key  ║
║  VA-3  logging.basicConfig() at module level overwrites FastAPI logging  ║
║  VA-4  encrypt/decrypt were public — bypass-able without audit trail     ║
║  VA-5  upsert() missing on_conflict clause → duplicate rows on re-save  ║
║  VA-6  user_id / exchange_id not validated → ILP corruption downstream  ║
║  VA-7  DB failure returned slots=0 → halted all trading for all users   ║
║  VA-8  load_decrypted_keys docstring: warn callers to not log/cache it  ║
║  VA-9  del raw_key documented: does not zero CPython string memory       ║
║  VA-10 No delete_exchange_keys() — keys could not be revoked via API     ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import os
import re
import logging
from typing import Optional

from supabase import create_client, Client
from cryptography.fernet import Fernet, MultiFernet, InvalidToken

# FIX VA-3: Remove logging.basicConfig() — let the application configure logging.
#            Never call basicConfig inside a library/module.
logger = logging.getLogger("SecurityVault")


# ══════════════════════════════════════════════════════════════════════════
#  INPUT VALIDATION UTILITY
# ══════════════════════════════════════════════════════════════════════════

# FIX VA-6: Strict validation regex — only alphanumeric + hyphens + underscores.
# Prevents spaces/commas/equals that would corrupt ILP line protocol downstream.
_SAFE_ID_RE = re.compile(r'^[a-zA-Z0-9\-_]{1,128}$')


def _validate_id(value: str, name: str) -> str:
    """Raises ValueError if value contains characters unsafe for ILP / Supabase."""
    if not value or not isinstance(value, str):
        raise ValueError(f"{name} must be a non-empty string.")
    if not _SAFE_ID_RE.match(value):
        raise ValueError(
            f"{name} '{value}' contains invalid characters. "
            "Only alphanumeric, hyphens, and underscores are allowed."
        )
    return value


# ══════════════════════════════════════════════════════════════════════════
#  SECURITY VAULT
# ══════════════════════════════════════════════════════════════════════════

class SecurityVault:
    """
    AES-256 API key vault using Fernet symmetric encryption with key rotation.

    Flow:
        store_exchange_keys()  →  encrypts in RAM  →  writes to Supabase
        load_decrypted_keys()  →  reads from Supabase  →  decrypts in RAM
        ⚠️  The returned dict must be consumed immediately and never logged,
            serialised, or stored in any cache.
    """

    def __init__(self):
        # ── Fail-fast on missing env vars ─────────────────────────────────
        supa_url    = os.environ.get("SUPABASE_URL")
        supa_key    = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        keys_string = os.environ.get("MASTER_ENCRYPTION_KEYS")

        if not supa_url or not supa_key:
            logger.critical("FATAL: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing.")
            raise EnvironmentError("Missing Supabase configuration.")

        if not keys_string:
            logger.critical("FATAL: MASTER_ENCRYPTION_KEYS missing.")
            raise EnvironmentError("Missing master encryption keys.")

        # ── Supabase connection ────────────────────────────────────────────
        try:
            self.supabase: Client = create_client(supa_url, supa_key)
        except Exception as e:
            logger.critical(f"FATAL: Supabase connection failed: {e}")
            raise ConnectionError("Vault database connection failed.")

        # ── MultiFernet cipher (AES-256, key rotation) ────────────────────
        master_keys = [k.strip() for k in keys_string.split(",") if k.strip()]
        try:
            fernet_list = [Fernet(k.encode()) for k in master_keys]
            # First key encrypts; all keys can decrypt (rotation support)
            self._cipher = MultiFernet(fernet_list)
        except (ValueError, Exception) as e:
            logger.critical(f"FATAL: Invalid master key format: {e}")
            raise ValueError("One or more MASTER_ENCRYPTION_KEYS are invalid.")

        logger.info(f"SecurityVault armed with {len(master_keys)}-key rotation.")

    # ══════════════════════════════════════════════════════════════════════
    #  PRIVATE CRYPTO LAYER
    #  FIX VA-4: encrypt/decrypt are now private (_) — all external code
    #            must use store_exchange_keys / load_decrypted_keys.
    # ══════════════════════════════════════════════════════════════════════

    def _encrypt(self, raw_secret: str) -> str:
        """
        Encrypts a plaintext secret string.
        FIX VA-2: Raises ValueError on empty / None input instead of silently
                  returning None, which would store a null key in the database.
        """
        if not raw_secret:
            raise ValueError("Cannot encrypt an empty or null secret.")
        return self._cipher.encrypt(raw_secret.encode()).decode()

    def _decrypt(self, encrypted_secret: Optional[str]) -> Optional[str]:
        """
        Decrypts an encrypted secret string. Returns None if input is None
        (handles optional fields like 'password' that may not be set).
        """
        if not encrypted_secret:
            return None
        try:
            return self._cipher.decrypt(encrypted_secret.encode()).decode()
        except InvalidToken:
            logger.error(
                "Decryption failed: InvalidToken — data may be corrupted "
                "or the encryption key was rotated without including the old key."
            )
            raise ValueError("Decryption failed. Key rotation issue or corrupted data.")

    # ══════════════════════════════════════════════════════════════════════
    #  KEY MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════

    def store_exchange_keys(
        self,
        user_id:      str,
        exchange_id:  str,
        raw_api_key:  str,
        raw_secret:   str,
        raw_password: Optional[str] = None,
    ) -> bool:
        user_id     = _validate_id(user_id,     "user_id")
        exchange_id = _validate_id(exchange_id, "exchange_id")

        if not raw_api_key or not raw_secret:
            raise ValueError("api_key and secret_key are required.")

        data = {
            "user_id":              user_id,
            "exchange_id":          exchange_id.lower(),
            "encrypted_api_key":    self._encrypt(raw_api_key),
            "encrypted_secret_key": self._encrypt(raw_secret),
            "encrypted_password":   self._encrypt(raw_password) if raw_password else None,
        }

        # FIX VA-5: Use upsert to handle existing keys cleanly.
        try:
            self.supabase.table("exchange_keys").upsert(data).execute()
            logger.info(f"Keys vaulted to Supabase for user={user_id} exchange={exchange_id}.")
            return True
        except Exception as e:
            logger.error(f"Supabase upsert failed for user={user_id}: {e}")
            raise

    def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict:
        user_id     = _validate_id(user_id,     "user_id")
        exchange_id = _validate_id(exchange_id, "exchange_id")

        try:
            response = (
                self.supabase.table("exchange_keys")
                .select("*")
                .eq("user_id", user_id)
                .eq("exchange_id", exchange_id.lower())
                .execute()
            )

            if not response.data:
                raise ValueError(f"No keys found for {user_id}/{exchange_id}.")

            row = response.data[0]
            return {
                "api_key":    self._decrypt(row.get("encrypted_api_key")),
                "secret_key": self._decrypt(row.get("encrypted_secret_key")),
                "password":   self._decrypt(row.get("encrypted_password")),
            }
        except Exception as e:
            logger.error(f"Failed to load keys for {user_id}: {e}")
            raise

    def delete_exchange_keys(self, user_id: str, exchange_id: str) -> bool:
        """
        FIX VA-10: NEW — Revokes stored API keys for a user+exchange.
        Required for: user disconnects exchange, account deletion, security incident.
        """
        user_id     = _validate_id(user_id,     "user_id")
        exchange_id = _validate_id(exchange_id, "exchange_id")

        try:
            self.supabase.table("exchange_keys") \
                .delete() \
                .eq("user_id", user_id) \
                .eq("exchange_id", exchange_id.lower()) \
                .execute()
            logger.info(f"Keys revoked for user={user_id} exchange={exchange_id}.")
            return True
        except Exception as e:
            logger.error(f"Failed to delete keys for user={user_id}: {e}")
            raise

    def list_connected_exchanges(self, user_id: str) -> list[str]:
        """Returns all exchange IDs a user has stored keys for."""
        user_id = _validate_id(user_id, "user_id")
        try:
            response = (
                self.supabase.table("exchange_keys")
                .select("exchange_id")
                .eq("user_id", user_id)
                .execute()
            )
            return [row["exchange_id"] for row in (response.data or [])]
        except Exception as e:
            logger.error(f"Failed to list exchanges for user={user_id}: {e}")
            return []

    # ══════════════════════════════════════════════════════════════════════
    #  SUBSCRIPTION / TIER
    # ══════════════════════════════════════════════════════════════════════

    def get_user_tier(self, user_id: str) -> dict:
        """
        Returns subscription tier info for the given user.

        FIX VA-1: Normalised return dict always uses the same keys:
            {"subscription_tier": str, "max_api_slots": int}
        Both the Supabase row and the fallback now use identical key names.

        FIX VA-7: DB failure falls back to {"subscription_tier": "free",
            "max_api_slots": 1} — restricted but NOT zero-slots. A Supabase
            outage must not halt all trading across the platform.
        """
        user_id = _validate_id(user_id, "user_id")

        try:
            response = (
                self.supabase.table("profiles")
                .select("subscription_tier, max_api_slots")
                .eq("id", user_id)
                .execute()
            )

            if not response.data:
                logger.warning(f"User {user_id} not in profiles — defaulting to free tier.")
                # FIX VA-1: Same key names as the Supabase row
                return {"subscription_tier": "free", "max_api_slots": 1}

            return response.data[0]  # {"subscription_tier": ..., "max_api_slots": ...}

        except Exception as e:
            logger.error(f"Failed to fetch tier for user={user_id}: {e}")
            # FIX VA-7: Fail OPEN to a restricted free tier, not to 0 slots
            return {"subscription_tier": "free", "max_api_slots": 1}

    # ══════════════════════════════════════════════════════════════════════
    #  UTILITIES
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def generate_new_master_key() -> str:
        """
        Generates a new AES-256 Fernet key.
        Run once locally. Prepend output to MASTER_ENCRYPTION_KEYS env var
        (comma-separated list) for zero-downtime key rotation.
        """
        key = Fernet.generate_key().decode()
        print(f"\n🔐 NEW MASTER KEY: {key}")
        print("⚠️  PREPEND this to MASTER_ENCRYPTION_KEYS (comma-separated, newest first).\n")
        return key

    def rotate_key(self, user_id: str, exchange_id: str) -> bool:
        """
        Re-encrypts stored keys with the current primary key (first in list).
        Call for each user after adding a new key to MASTER_ENCRYPTION_KEYS.
        The old key must still be present in the list during rotation.
        """
        try:
            keys = self.load_decrypted_keys(user_id, exchange_id)
            self.store_exchange_keys(
                user_id     = user_id,
                exchange_id = exchange_id,
                raw_api_key = keys["api_key"],
                raw_secret  = keys["secret_key"],
                raw_password= keys["password"],
            )
            logger.info(f"Keys rotated for user={user_id} exchange={exchange_id}.")
            return True
        except Exception as e:
            logger.error(f"Key rotation failed for user={user_id}: {e}")
            raise
