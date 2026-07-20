"""
scripts/rotate_all_keys.py — Bulk re-encryption of all exchange credentials.

F-02: Run after prepending a new key to MASTER_ENCRYPTION_KEYS.
      The old key must still be present in the comma-separated list during rotation.

Usage:
    python scripts/rotate_all_keys.py

Required env vars:
    MASTER_ENCRYPTION_KEYS  — comma-separated Fernet keys (newest first)
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
"""
import os
import sys
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
KEYS_STRING  = os.environ.get("MASTER_ENCRYPTION_KEYS")

if not SUPABASE_URL or not SERVICE_KEY or not KEYS_STRING:
    print(
        "ERROR: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, and "
        "MASTER_ENCRYPTION_KEYS must all be set."
    )
    sys.exit(1)

from supabase import create_client
from cryptography.fernet import Fernet, MultiFernet

admin  = create_client(SUPABASE_URL, SERVICE_KEY)
keys   = [Fernet(k.strip().encode()) for k in KEYS_STRING.split(",") if k.strip()]

if len(keys) < 2:
    print(
        "WARNING: Only one key found in MASTER_ENCRYPTION_KEYS. "
        "For rotation, you should have NEW_KEY,OLD_KEY."
    )

cipher = MultiFernet(keys)  # First key encrypts (new), all keys can decrypt (old too)

rows = admin.table("exchange_keys").select("*").execute().data or []
print(f"Rotating {len(rows)} credential rows...")

rotated = 0
failed  = 0

for row in rows:
    uid = row.get("user_id", "?")
    ex  = row.get("exchange_id", "?")
    try:
        # Decrypt with MultiFernet (tries all keys)
        def _dec(val):
            if not val:
                return None
            return cipher.decrypt(val.encode()).decode()

        api_key    = _dec(row.get("encrypted_api_key"))
        secret_key = _dec(row.get("encrypted_secret_key"))
        password   = _dec(row.get("encrypted_password"))

        # Re-encrypt with the primary (newest) key
        def _enc(val):
            if not val:
                return None
            return cipher.encrypt(val.encode()).decode()

        update = {
            "encrypted_api_key":    _enc(api_key),
            "encrypted_secret_key": _enc(secret_key),
            "encrypted_password":   _enc(password),
        }

        admin.table("exchange_keys") \
             .update(update) \
             .eq("user_id", uid) \
             .eq("exchange_id", ex) \
             .execute()

        rotated += 1
        print(f"  ✅  Rotated: user={uid} exchange={ex}")

    except Exception as e:
        print(f"  ❌  FAILED: user={uid} exchange={ex}: {e}")
        failed += 1

print(f"\nDone. {rotated} rotated, {failed} failed.")
if failed:
    print("❌ Some rotations failed — do NOT remove the old key yet.")
    sys.exit(1)
else:
    print("✅ All rows rotated. You may now remove the old key from MASTER_ENCRYPTION_KEYS.")
    sys.exit(0)
