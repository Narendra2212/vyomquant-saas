# Railway Environment Audit Report

**Date:** 2026-06-18
**Environment:** Production (Railway)

This report audits the presence and properties of the required JWT environment variables on Railway.

## Variable Audit Table

| Variable | Exists | Length | Prefix (4 chars) | SHA-256 Fingerprint (First 8 chars) |
|---|---|---|---|---|
| `SUPABASE_URL` | Yes | 40 | `http` | `4976e364` |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | 219 | `eyJh` | `df7b2a1a` |
| `SUPABASE_ANON_KEY` | Yes | 208 | `eyJh` | `910821f2` |
| `SUPABASE_JWT_SECRET` | Yes | 88 | `gl0U` | `5085eec4` |
| `JWT_SECRET` | Yes | 128 | `f277` | `ef23613c` |

## Details
- **SUPABASE_URL**: `https://YOUR_PROJECT_REF.supabase.co`

> [!NOTE]
> The secret values themselves are never exposed. The SHA-256 fingerprint allows matching with client/server-side components without risk of credential exposure.