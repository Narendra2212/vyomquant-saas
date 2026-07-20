# MASTER_ENCRYPTION_KEYS — Rotation Procedure

> **Sprint 1 · F-02** — Key management procedures for the Fernet-based AES-256 exchange credential vault.

---

## Background

Exchange credentials are encrypted at rest using **Fernet symmetric encryption** (AES-128-CBC + HMAC-SHA256).  
The `MASTER_ENCRYPTION_KEYS` environment variable holds a **comma-separated list of base64url Fernet keys**.  
`MultiFernet` is used so that:
- The **first** key in the list always **encrypts** new data.
- **All** keys in the list can **decrypt** existing data (rotation window).

---

## Security Rules

| Rule | Detail |
|------|--------|
| Never commit keys to source control | Keys must only live in env vars / secrets managers |
| Never log keys | Never print, log, or expose in error messages |
| Minimum 1 active key at all times | Never remove all keys simultaneously |
| Old key stays during rotation | Remove only after all rows re-encrypted |
| Key length | Exactly 44 characters (32 bytes base64url-encoded) |

---

## Generate a New Key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Or via the vault utility:

```python
from aerora_quant_backend_updated_final1.backend.security_vault import SecurityVault
SecurityVault.generate_new_master_key()
```

---

## Zero-Downtime Rotation Procedure

### Step 1 — Generate a new key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Output: <NEW_KEY_HERE>
```

### Step 2 — Prepend the new key (do NOT remove the old key yet)

Update `MASTER_ENCRYPTION_KEYS` in your secrets manager / `.env`:

```
# Before:
MASTER_ENCRYPTION_KEYS=OLD_KEY

# After (new key first, old key second):
MASTER_ENCRYPTION_KEYS=NEW_KEY,OLD_KEY
```

### Step 3 — Deploy with the new config

Restart all backend instances so they pick up the new `MASTER_ENCRYPTION_KEYS`.  
At this point:
- **New data** → encrypted with `NEW_KEY`
- **Existing data** → still decryptable using `OLD_KEY`

### Step 4 — Re-encrypt all existing rows

Run the following script to rotate every user's stored credentials to the new key:

```bash
# Set env vars first, then run:
python scripts/rotate_all_keys.py
```

> The script iterates all rows in `exchange_keys`, decrypts with `MultiFernet` (which tries all keys),
> then re-encrypts with `NEW_KEY` (the current first key) and upserts back.

### Step 5 — Remove the old key

Once all rows are re-encrypted, remove `OLD_KEY` from `MASTER_ENCRYPTION_KEYS`:

```
MASTER_ENCRYPTION_KEYS=NEW_KEY
```

Redeploy. Old key is now decommissioned.

---

## Rotation Script (scripts/rotate_all_keys.py)

```python
"""
scripts/rotate_all_keys.py — Bulk re-encryption of all exchange credentials.
Run after prepending a new key to MASTER_ENCRYPTION_KEYS.
"""
import os
import sys
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

from supabase import create_client
from cryptography.fernet import Fernet, MultiFernet

SUPABASE_URL = os.environ["SUPABASE_URL"]
SERVICE_KEY  = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
KEYS_STRING  = os.environ["MASTER_ENCRYPTION_KEYS"]

admin  = create_client(SUPABASE_URL, SERVICE_KEY)
keys   = [Fernet(k.strip().encode()) for k in KEYS_STRING.split(",") if k.strip()]
cipher = MultiFernet(keys)  # First key encrypts, all can decrypt

rows = admin.table("exchange_keys").select("*").execute().data or []
print(f"Rotating {len(rows)} credential rows...")

rotated = 0
failed  = 0
for row in rows:
    try:
        # Decrypt with any of the current keys
        api_key    = cipher.decrypt(row["encrypted_api_key"].encode()).decode()   if row.get("encrypted_api_key")    else None
        secret_key = cipher.decrypt(row["encrypted_secret_key"].encode()).decode() if row.get("encrypted_secret_key") else None
        password   = cipher.decrypt(row["encrypted_password"].encode()).decode()   if row.get("encrypted_password")   else None

        # Re-encrypt with NEW primary key (first in list)
        update = {
            "encrypted_api_key":    cipher.encrypt(api_key.encode()).decode()    if api_key    else None,
            "encrypted_secret_key": cipher.encrypt(secret_key.encode()).decode() if secret_key else None,
            "encrypted_password":   cipher.encrypt(password.encode()).decode()   if password   else None,
        }
        admin.table("exchange_keys").update(update).eq("user_id", row["user_id"]).eq("exchange_id", row["exchange_id"]).execute()
        rotated += 1
    except Exception as e:
        print(f"  FAILED: user={row.get('user_id')} exchange={row.get('exchange_id')}: {e}")
        failed += 1

print(f"Done. {rotated} rotated, {failed} failed.")
if failed:
    sys.exit(1)
```

---

## Emergency Key Compromise Response

If a master key is suspected to be compromised:

1. **Immediately** generate a new key and prepend it to `MASTER_ENCRYPTION_KEYS`.
2. Deploy the new config to all instances.
3. Run `scripts/rotate_all_keys.py` to re-encrypt all data.
4. Remove the compromised key from `MASTER_ENCRYPTION_KEYS`.
5. **Notify affected users** to rotate their exchange API keys on the respective exchange.
6. Audit the `exchange_keys` table for any unauthorized access via Supabase logs.

---

## Environment Variable Reference

| Variable | Format | Example |
|----------|--------|---------|
| `MASTER_ENCRYPTION_KEYS` | `KEY1,KEY2,...` (newest first) | `abc123...=,xyz456...=` |
| `SUPABASE_URL` | `https://<ref>.supabase.co` | — |
| `SUPABASE_SERVICE_ROLE_KEY` | JWT string | — (admin background jobs only) |
| `SUPABASE_ANON_KEY` | JWT string | — (user request path) |

---

*Sprint 1 · F-02 — Committed: 2026-06-06*
