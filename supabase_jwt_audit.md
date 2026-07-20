# Supabase Project Audit Report

**Date:** 2026-06-18
**Project Reference:** `wrkexcjqnidkdrayhlsi`
**Supabase URL:** `https://YOUR_PROJECT_REF.supabase.co`

This report audits the active Supabase project's JWT signing config and authentication provider settings.

## JWT Key Metadata

- **Project Reference in Keys:** `wrkexcjqnidkdrayhlsi`
- **Anon Key Issue Time (iat):** `1774789292`
- **Anon Key Expiration (exp):** `2090365292`
- **Service Role Role:** `service_role`

## Signature Verification Check
Verification of the active production Supabase keys signature using Railway's configured `SUPABASE_JWT_SECRET`:

### Raw SUPABASE_JWT_SECRET (Railway)
- **Anon Key Signature Valid:** `Yes` (Error: `None`)
- **Service Role Key Signature Valid:** `Yes` (Error: `None`)

### Base64 Decoded SUPABASE_JWT_SECRET (Railway)
- **Anon Key Signature Valid:** `No` (Error: `Signature verification failed`)
- **Service Role Key Signature Valid:** `No` (Error: `Signature verification failed`)

## Authentication Provider Configuration
- **Mailer Enabled:** `No`
- **SAML Enabled:** `No`
- **Enabled External OAuth Providers:** `email`

> [!NOTE]
> If the signature check shows 'No' for both raw and base64 decoded secrets, it confirms that the Railway secret does not match the active signing secret of the production Supabase project.