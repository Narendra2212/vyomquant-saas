# Secrets Scan Report

**Generated:** 2026-06-08  
**Tools:**
- gitleaks v8.21.2 (scan in progress — results will be appended when complete)
- Manual grep inspection of `.env`, `*.json`, `*.py`, `*.js` files

---

## CRITICAL — Plaintext Credentials in Committed `.env` File

The `.env` file is committed to the repository and contains **live production credentials**.

### Finding 1 — Supabase Service Role Key (LIVE)

| Field | Value |
|-------|-------|
| File | `.env` |
| Line | 5 |
| Secret Type | Supabase `service_role` JWT |
| Value (masked) | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...gni9QFZ...` |
| Status | **LIVE** — references project `wrkexcjqnidkdrayhlsi.supabase.co` |
| Risk | Full database access bypassing Row Level Security |

```
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### Finding 2 — Supabase Anon Key (LIVE)

| Field | Value |
|-------|-------|
| File | `.env` |
| Line | 4 |
| Secret Type | Supabase `anon` JWT |
| Value (masked) | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...NTXqian...` |
| Status | **LIVE** — same project reference |

### Finding 3 — Supabase JWT Secret (LIVE)

| Field | Value |
|-------|-------|
| File | `.env` |
| Line | 6 |
| Secret Type | UUID JWT Signing Secret |
| Value (masked) | `YOUR_SUPABASE_JWT_SECRET` |
| Risk | Allows forging valid Supabase JWTs |

### Finding 4 — Master Encryption Key (LIVE)

| Field | Value |
|-------|-------|
| File | `.env` |
| Line | 13 |
| Secret Type | Fernet Encryption Key (base64) |
| Value (masked) | `YOUR_FERNET_ENCRYPTION_KEY` |
| Risk | Decrypts all exchange API keys stored in the vault |

### Finding 5 — Redis Cloud URL with Password (LIVE)

| Field | Value |
|-------|-------|
| File | `.env` |
| Line | 21 |
| Secret Type | Redis connection string with credentials |
| Value (masked) | `redis://default:GX8WtmjR...@redis-10512.crce292.ap-south-1-2.ec2.cloud.redislabs.com:10512` |
| Risk | Full Redis access |

### Finding 6 — Test User Credentials (Plaintext)

| Field | Value |
|-------|-------|
| File | `.env` |
| Lines | 9–10, 32–33 |
| Secret Type | Email + Password (plaintext, duplicated) |
| Value (masked) | `nt3441449@gmail.com` / `Narendra@221203` |
| Risk | User account compromise |

---

## HIGH — Encrypted Exchange API Keys in Repository

| Field | Value |
|-------|-------|
| File | `exchange_keys.json` |
| Line | 1 |
| Secret Type | Fernet-encrypted Binance API Key + Secret |
| Value (masked) | `gAAAAABqIuyp...` (Fernet ciphertext) |
| Risk | Decryptable with MASTER_ENCRYPTION_KEYS from `.env` |
| Note | Encryption key and ciphertext committed in the same repository |

---

## MEDIUM — apiClient.js: Token Logging in Console

| Field | Value |
|-------|-------|
| File | `algo22-terminal/src/apiClient.js` |
| Line | 621 |
| Issue | `console.log("🔐 TOKEN USED:", token.substring(0, 20) + "...")` — partial JWT token logged to browser console on every request |
| Tool | Manual source inspection |

```javascript
// apiClient.js line 621
console.log("🔐 TOKEN USED:", token.substring(0, 20) + "...");
```

---

## Gitleaks Automated Scan Results

**Status:** Complete  
**Duration:** 3m 4s  
**Total leaks found:** 46  
**Output file:** `gitleaks_raw.json`

### Leak Summary by Rule

| Rule | Count | Description |
|------|-------|-------------|
| `generic-api-key` | 25 | API keys, encryption keys, UUID secrets |
| `jwt` | 10 | JWT tokens (Supabase anon, service_role) |
| `curl-auth-header` | 9 | Bearer token examples in markdown docs |
| `sidekiq-secret` | 1 | Random string flagged in README |
| `private-key` | 1 | Private TLS key file |

### Critical Production Findings (non-archive/non-node_modules)

| Rule | File | Line | Description |
|------|------|------|-------------|
| `generic-api-key` | `.env` | 13 | Master encryption key (Fernet) |
| `generic-api-key` | `.env` | 6 | Supabase JWT Secret |
| `generic-api-key` | `exchange_keys.json` | 1 | Encrypted Binance API key (×2 entries) |
| `jwt` | `.env` | 5 | Supabase service_role JWT |
| `jwt` | `.env` | 4 | Supabase anon JWT |
| `jwt` | `list_rpcs.py` | 6 | Hardcoded JWT in source file |
| `jwt` | `check_supabase_keys.py` | 8 | Hardcoded JWT in source file |
| `jwt` | `algo22-terminal/.env` | 2 | JWT in frontend env file |
| `private-key` | `nginx/ssl/aerora.key` | 1 | TLS private key committed to repository |

---

## .gitignore Status

| File | Status |
|------|--------|
| `.env` | **COMMITTED** — must be added to `.gitignore` and rotated |
| `exchange_keys.json` | **COMMITTED** — must be removed from repository |
| `nginx/ssl/aerora.key` | **COMMITTED** — TLS private key in repository |
| `algo22-terminal/.env` | **COMMITTED** — frontend secrets in repository |
| `list_rpcs.py` | **COMMITTED** — hardcoded JWT |
| `check_supabase_keys.py` | **COMMITTED** — hardcoded JWT |
