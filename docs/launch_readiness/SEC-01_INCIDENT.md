# SEC-01 — Live production secrets published in a public repository

**Status:** ACTIVE INCIDENT. Discovered 2026-10-23 while scoping SEC-01's rotation.
**Repository visibility:** `PUBLIC` (`gh repo view` → `"visibility": "PUBLIC"`, 0 forks, 1 collaborator, 0 deploy keys).

## Why this was not found earlier

SEC-01 was filed as "production Postgres credential hardcoded in a tracked script",
naming one file: `scripts/run_schema_migration_prod.py`. That file was deleted and a
test (`tests/test_migration_tooling.py::test_sec01_leaking_script_is_deleted_and_never_returns`)
keeps it deleted. The finding's remaining work was recorded as "rotate the password".

Scoped to that one file, the evidence said the exposure was already closed: the password
in that file is 23 characters, the live one is 17, and PostgreSQL stores one password per
role, so the leaked value could no longer authenticate. **That conclusion was correct about
that file and wrong about the repository.** A full-history sweep against every readable
Secrets Manager value found three *other* live secrets, in files SEC-01 never named.

The lesson is about the shape of the finding, not the fix: a leak finding scoped to a
single path cannot be closed by remediating that path.

## Confirmed exposures

All three are absent from `HEAD` and present in history, so they are invisible to a
working-tree scan and fully recoverable by `git clone`.

| Secret | Exposed since | Days public | Where | Severity |
|---|---|---|---|---|
| `vyomquant/production/master_encryption_keys` | 2026-07-22 (`4428a4da`) | ~93 | `aws_secrets_deploy.py` | **CRITICAL** |
| `vyomquant/production/jwt_secret` | 2026-07-22 (`4428a4da`) | ~93 | `aws_secrets_deploy.py` | **CRITICAL** |
| `vyomquant/production/database_url` (password) | 2026-08-14 (`12a799e6`) | ~70 | 15 files under `scripts/` | **CRITICAL** |

Commits carrying the database password: `12a799e603`, `8992f2eda7`, `659c04df77`, `fbaf266fdb`.
The last of those is titled *"Remediate SEC-01, …"* — the remediation commit moved the
literal rather than removing it from history.

### Not an incident

`vyomquant/production/supabase_anon_key` also matched. Supabase anon keys are **public by
design**: shipped in the browser bundle, stored as the `VITE_SUPABASE_ANON_KEY` CI secret
precisely so it can be published, and protected by RLS rather than by secrecy. Reporting it
alongside the three above would make the real findings easier to dismiss.

### Coverage caveat

20 of 21 production secrets were readable and compared. `vyomquant/production/supabase_key`
returned `ResourceNotFound` and is therefore **unknown, not clean**.

`SUPABASE_JWT_SECRET` is **clean**. This matters: user login tokens are Supabase-issued and
verified with that secret, so existing user sessions are *not* forgeable from what leaked.

## What each leaked secret grants

**`master_encryption_keys`** — the Fernet key(s) for `APIKeyVault`
(`backend_app/backend/api_key_vault.py:96-99`), which encrypts
`exchange_keys.encrypted_api_key / encrypted_secret_key / encrypted_password`.
Anyone with this key *and* a copy of those rows can decrypt customer exchange API keys.
Blast radius is bounded: **`exchange_keys` holds 2 rows.**

**`jwt_secret`** — HMAC key for tokens the platform mints itself, e.g. the model-download
token in `backend_app/routers/strategy_operations.py:5704`. Distinct from
`SUPABASE_JWT_SECRET`, which verifies user logins and did **not** leak. Rotating
`JWT_SECRET` invalidates outstanding short-lived platform tokens; it does **not** log users out.

**`database_url` password** — direct connection as `postgres` to the production database
via the Supavisor pooler (`aws-1-ap-southeast-1.pooler.supabase.com:6543`). Full read/write
on all 182 user profiles, 198 execution records and everything else. **This is the one that
matters most**, because it also yields the ciphertext that `master_encryption_keys` decrypts.

## Rotation mechanics, verified before recommending anything

**`MASTER_ENCRYPTION_KEYS` rotation is non-destructive**, but only because of which vault
is actually in use:

* `api_key_vault.py:96-99` builds a **`MultiFernet`** from a comma-separated list — first
  key encrypts, *all* keys decrypt. Prepending a new key leaves existing ciphertext
  readable. This is the vault holding the 2 live rows. Helpers already exist:
  `APIKeyVault.generate_new_master_key()` and `APIKeyVault.rotate_key(user_id, exchange_id)`.
* `core/credential_vault.py:285` takes **only the first key** and runs it through PBKDF2 to
  derive a single `Fernet`. Prepending a new key there would silently make its ciphertext
  undecryptable. **It has no production callers** (referenced only by its own module, tests,
  and `test_strategy_dag_architecture.py`, which treats importing it as a violation) and it
  stores to Redis, not Postgres. So this path carries no data and no rotation risk today —
  but it is a live trap for any future rotation.

## Required actions, in order

Rotation must come first. Purging history without rotating leaves every existing clone
holding a working credential; rotating without purging leaves the dead value published.

1. **Rotate the database password.** `scripts/rotate_database_password.py --stage / --redeploy / --verify`.
   Pre-flight confirmed: rolling deploy at `minimumHealthyPercent=100`, circuit breaker
   `rollback=true`, so a mismatched password self-corrects with the old task still serving.
2. **Rotate `JWT_SECRET`.** New 64-byte random value; must satisfy
   `Settings.assert_jwt_secret_is_production_grade()` (≥32 bytes, ≥8 distinct characters,
   no placeholder substrings) or the application refuses to boot.
3. **Rotate `MASTER_ENCRYPTION_KEYS`.** Generate a key, **prepend** it comma-separated,
   redeploy, re-encrypt both `exchange_keys` rows with `rotate_key()`, then drop the old key
   in a second deploy.
4. **Delete the orphaned `/vyomquant/production/DATABASE_URL`** secret. Nothing references
   it; it holds a third, different password for the same role and is a trap for a future
   operator.
5. **Purge history** (`git filter-repo` + force-push). Destructive and rewrites published
   history; requires explicit sign-off. Note this does not reach existing clones, forks
   (currently 0) or GitHub's cached views, which is why rotation is the control that matters.
6. **Consider making the repository private**, which reduces exposure of anything not yet found.
7. **Resolve `vyomquant/production/supabase_key`** (`ResourceNotFound`) so coverage is complete.

## Tooling added

All read-only and printing no secret material (names, lengths, salted digests only):

* `sec01_full_sweep.py` — every readable production secret vs all of history. Retries
  throttling and **fails loudly** on unreadable secrets. Replaces a first attempt that
  silently compared only 4 of 11 secrets and still printed a verdict.
* `sec01_locate_all.py` — per-secret commit/path/first-publication map with severity.
* `sec01_preflight.py` — read-only rotation preconditions.
* `sec01_is_leak_live.py`, `sec01_leak_target.py` — the single-file analysis, retained
  because its narrow conclusion is the thing that needed correcting.

These live in the session scratch directory and should be moved under `scripts/` if the
sweep is to be run periodically, which it should be.
