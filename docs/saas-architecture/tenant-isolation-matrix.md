# Tenant Isolation Matrix

**Product:** Algo22 / VyomQuant Quant Platform
(the repository and frontend use "Algo22"; every deployed AWS resource is named
`vyomquant-*`. Both names refer to this platform.)
**Date:** 3 October 2026
**Segment:** Capital markets & trading - retail algorithmic trading SaaS
**Tenancy decision:** [ADR-0006](../adr/ADR-0006-bridge-tenancy-with-institutional-silo-tier.md)
- bridge as default, silo reserved as a tier for institutional tenants

## How to read this document

Two columns describe tenancy, not one. **Current** is what the code and infrastructure
actually do today; **Target** is what ADR-0006 decided. Collapsing them into a single
"Tenancy" column would describe a platform that does not exist, and this document's main
job is to be the one an institutional tenant's auditor can trust.

Where a mechanism is listed as enforcing isolation, "enforced" means the database or IAM
refuses a cross-tenant access independently of application code. Application `WHERE`
clauses are listed as `app-level` - necessary, and not sufficient on their own.

**Verified from source** unless a row says otherwise. Three things could not be
determined from the repository and are marked `unverified`: whether the worker ECS
services are actually running, the live `pg_class` RLS state, and the physical type of
`positions.tenant_id`. Run `scripts/verify_rls_enforcement.py` and
`aws ecs list-services --cluster vyomquant-cluster --region ap-southeast-1` to close
those.

## Platform context

| Aspect | Value |
|---|---|
| Cloud | AWS, `ap-southeast-1`, account `273709947018` |
| Compute | ECS Fargate, cluster `vyomquant-cluster`, image `vyomquant-api:latest` |
| Primary datastore | Supabase PostgreSQL via Supavisor pooler, port 6543, `pool_mode=transaction` |
| Cache / queues | ElastiCache Redis (`rediss://`), master + replica, DBs separated by number |
| Secrets | AWS Secrets Manager, `vyomquant/production/*` |
| Connection role | `postgres` - owns tables created by migrations, which is why RLS is inert on this path |
| Billing | INR base (`PRICE_BASE_CURRENCY`), Razorpay for INR and Stripe for USD |

Data residency is an open question, not a finding: compute and secrets are in Singapore
while the platform bills in INR. Whether India's payment-data localisation expectations
reach this architecture has not been assessed and is outside what ADR-0006 covers.

## Service-level tenancy

| Service | Deployment | Current tenancy | Target tenancy | Compute | Storage | Isolation mechanism | Rationale |
|---|---|---|---|---|---|---|---|
| **API** (`backend_app.main:app`, 27 routers) | `vyomquant-api-service-cjema2sl`, Fargate, gunicorn + 2 uvicorn workers | Pool | Pool compute, bridge data | Shared | None directly | JWT at ingress; `app-level` filtering; RLS binds only on the PostgREST path | Shared compute is what makes a ₹2,499 retail plan viable. Isolation belongs in the data layer, not the fleet. |
| **Order execution** (`routers/orders.py`, `transactional_execution_manager`) | In-API | Pool + `app-level` | Bridge | Shared | `orders`, `fills`, `positions` - shared tables, `tenant_id varchar(36)` | `tenant_id` column + RLS policy (**inert**, see gaps) + `app-level` | Execution data is the most competitively sensitive surface. Silo per tenant is unaffordable retail-side; dedicated stores arrive with the institutional tier. |
| **Trade execution engine (TEE)** | `vyomquant-tee-service`, `backend_app.tee.main` | Pool | Bridge | Shared | `strategies` scan across all tenants | `service_role` (`BYPASSRLS`); no tenant scoping | Cross-tenant by construction - the reconciler loop sweeps every running strategy. Legitimately shared; needs per-tenant alert partitioning before institutional onboarding. |
| **DAG / strategy execution** | `vyomquant-worker-dag` (`unverified` whether running) | Pool + `app-level` | Bridge | Shared, 256 CPU / 512 MB | `dag_tasks` (`tenant_id varchar(36)`), `execution_records` (`tenant_id uuid`) | `tenant_id` + RLS policy (**inert**) + `TenantContext` quota objects | One task belongs to one tenant, so this is the clearest candidate for context propagation via `tenant_db_context`. |
| **Backtest** | `vyomquant-worker-backtest` (`unverified`) | Pool + `app-level` | Bridge | Shared, 256/512 | Redis Streams job queue; `tenant_id` on results | Redis key prefix `user:{user_id}:*`; `app-level` | Burst-shaped workload. Per-tenant concurrency caps already exist via `TenantQuota.max_concurrent_backtests`. |
| **Command bus** | `vyomquant-worker-command` (`unverified`) | Pool | Pool | Shared, 256/512 | Redis stream, consumer group `command_workers` | Message-level `tenant_id`; `app-level` | Transport. Isolation is the payload's responsibility, enforced by the handler it dispatches to. |
| **Market data (MDS)** | `vyomquant-mds-service`, `backend_app.mds.main` | Pool | Pool | Shared | Instruments, exchanges, candles | None required | Reference and market data is identical for every tenant. Pool is correct and should stay pool. |
| **WebSocket fan-out** | `vyomquant-websocket-service`, port 8002 | Pool | Pool compute, per-tenant channels | Shared | Redis pub/sub | Single-use Redis-backed ticket (30 s TTL, redeemed with `GETDEL`); per-tenant channel keys (`TenantKeyBuilder.websocket_connection`) | Connection caps per tenant via `TenantQuota.max_websocket_connections`. Channel naming is the isolation boundary. |
| **Strategy library / marketplace** | In-API (`routers/library.py`) | Pool | Bridge | Shared | `library_strategies`, `library_ratings`, `library_subscriptions` | RLS via `authenticated` role (**binds**); `service_role` for cross-user reads and clone counts | Strategy IP is the asset tenants care most about. The service-role client is scoped to listing reads and metric increments, and its own comment says so. |
| **Paper trading** | In-API (`routers/paper_trading.py`, `backend/paper/*`) | Pool + `app-level` | Bridge | Shared | `paper_sessions`, paper accounts and ledger | RLS via `authenticated`; `app-level` | No real money, but simulated P&L is still tenant-private. |
| **Billing / subscriptions** | In-API (`routers/billing.py`) | Pool (control plane) | Pool (control plane) | Shared | `profiles`, `billing_invoices`, `payment_methods` | `service_role` for server-authoritative writes; `authenticated` for reads | Correctly shared - the control plane operates across all tenants by definition. Service-role here is a feature: a tenant must not be able to write its own `subscription_status`. |
| **Exchange credentials** | In-API (`routers/exchange.py` via `get_vault`) | Pool storage, platform-wide key set | Bridge, per-tenant keys ([ADR-0007](../adr/ADR-0007-per-tenant-credential-encryption-keys.md)) | Shared | `exchange_keys`, keyed on `user_id` | `backend/api_key_vault.py`: `MultiFernet` over the comma-separated `MASTER_ENCRYPTION_KEYS` list; `service_role` client | **Highest-value secret on the platform** - broker credentials grant account access, not just data. Key *rotation* works correctly (first key encrypts, all decrypt). What is platform-wide is the key *set*: every tenant's credentials sit under the same key material, so rotation bounds the exposure window but not the blast radius. |
| **Referrals / payouts** | In-API (`routers/referral.py`) | Pool | Bridge | Shared | `referrals` | RLS `SELECT` only - **no write policy** | Writes go through `SECURITY DEFINER` RPCs. The missing write policies are a FORCE-RLS blocker. |
| **Copilot / AI** | In-API (`routers/copilot.py`) | Pool | Bridge | Shared | `copilot_sessions`, `copilot_messages` | RLS policied in the raw-SQL migration system, not Alembic | Conversation history is tenant-private and may contain strategy logic. |
| **Subscription expiry sweep** | **Not deployed** | n/a | Pool (cross-tenant by design) | n/a | `library_subscriptions`, `library_subscription_transitions`, `strategy_deployments`, `paper_sessions` | `service_role` | `MarketplaceExpiryWorker` exists and nothing starts it. Subscriptions past `period_expiry` are never expired. See `tests/test_worker_deployment_coverage.py`. |
| **Reconciliation worker** | **Not deployed** | n/a | Delete or host | n/a | `reconciliation_mismatches` (no reader found) | none | `ReconciliationWorker` is instantiated as a module global and never started. Probably superseded by the TEE's own `reconciler_loop()`. |
| **Control plane** (tenant config, identity, tiering) | In-API + Supabase Auth | Pool | Pool | Shared | `profiles`, Supabase Auth | JWT issuance; `authenticated` RLS | Always shared regardless of any tenant's tier. This is correct and is not a gap. |

## Storage partitioning detail

| Store | Partition key | Isolation enforced by | Per-tenant encryption |
|---|---|---|---|
| 9 execution tables (`orders`, `positions`, `fills`, `dag_tasks`, `execution_records`, `idempotency_keys`, `transaction_records`, `transaction_checkpoints`, `reconciliation_mismatches`) | `tenant_id` | RLS policy on `app.current_tenant_id` - **not currently binding** | No. Supabase-managed encryption at rest, single key. |
| Supabase app tables (`strategies`, `profiles`, `library_*`, `paper_*`, `copilot_*`) | `user_id` / `author_id` | RLS via `authenticated` role - **binding** | No |
| `exchange_keys` | `user_id` | **`app-level` only.** `backend/api_key_vault.py` connects with `SUPABASE_SERVICE_ROLE_KEY`, which carries `BYPASSRLS`, so RLS cannot apply. Isolation is the vault's own `.eq("user_id", user_id)` plus `_validate_id`. | Fernet via `MultiFernet`, platform-wide key set - see gap 3 |
| `payment_methods` | `user_id` **only - no `tenant_id`** | `app-level` | n/a. Provider token + `brand` + `last4` + expiry; no PAN. |
| `referrals` | `referrer_id` | RLS `SELECT` only | No |
| Redis | Key prefix `user:{user_id}:*` (`TenantKeyBuilder`) | Naming convention, `app-level` | No. TLS in transit. |
| Redis DB separation | DB 0 cache, DB 3 idempotency | Logical, not tenant-related | n/a |

Two keying inconsistencies matter for the silo migration ADR-0006 authorises. The nine
execution tables key on `tenant_id`; the Supabase application tables key on `user_id`;
`payment_methods` has no `tenant_id` column at all. Today `tenant_id` and `user_id` hold
the same value - `routers/orders.py` and `routers/portfolio.py` both derive
`tenant_id = UUID(user["id"])` - so the divergence is invisible. It stops being
invisible the moment one tenant's rows need to move to a dedicated store.

## Tier mapping

Tiers are the real ones from `core/schemas.py` and `core/subscription_engine.py`.
`scale` is sales-quoted and has no published price.

| Tier | Display name | Pool services | Bridge services | Silo services |
|---|---|---|---|---|
| `free` | Free | All | None | None |
| `starter` | Trader | All | None | None |
| `pro` | Pro Quant | All | None | None |
| `enterprise` | Business (₹2,499) | All | None | None |
| `scale` | Enterprise (quoted) | All | None | None |

Every tier is pool today - tiers differentiate quota, not isolation. `TenantQuota.for_plan`
varies DAG sessions, positions, capital, leverage and retention; no tier changes where
data lives.

Target state per ADR-0006:

| Tier | Pool services | Bridge services | Silo services |
|---|---|---|---|
| `free` - `pro` | Market data, reference data, command bus, control plane | Execution tables, library, paper trading, credentials (per-tenant keys) | None |
| `enterprise` | Same as above | Same as above | None |
| `scale` (institutional) | Market data, reference data, control plane | Library, paper trading | Order execution, positions, ledger - dedicated store; dedicated AWS account where a tenant competes with another tenant |

Moving a tenant between these without a data migration is what the storage-location
abstraction in ADR-0006 exists to make possible. It is not built.

## Known isolation gaps

Ranked by exposure. Gap 1 is the one an institutional auditor would find first.

| # | Gap | Exposure | Blocks FORCE RLS |
|---|---|---|---|
| 1 | **RLS does not bind on the SQLAlchemy path.** `ENABLE` without `FORCE`, and the app connects as `postgres`, the table owner. All nine execution-table policies are inert; isolation there is `app-level` only. | Any missing `WHERE tenant_id` is a cross-tenant read with no database backstop. | Is the thing to fix |
| 2 | **No tenant context on request paths.** `SET LOCAL app.current_tenant_id` exists at two call sites, neither on a request path. `get_db`/`get_db_context` set nothing; no middleware does. | None today (policies are inert). Under FORCE, becomes silently empty reads. | Yes - prerequisite |
| 3 | **Credential encryption keys are platform-wide, not per-tenant.** `backend/api_key_vault.py` builds one `MultiFernet` from `MASTER_ENCRYPTION_KEYS` and uses it for every tenant. Rotation is implemented correctly; the key set is still shared. | One key compromise plus table access exposes every tenant's broker credentials. Highest blast radius of any gap listed, though lower probability than gaps 1 and 2. | No |
| 3b | **An orphaned second credential vault exists with weaker crypto.** `core/credential_vault.py` is a complete parallel implementation - `store_credential`, `rotate_credential`, audit log, cross-tenant checks - imported by nothing outside tests and scripts. It derives a single `Fernet` via PBKDF2 from `MASTER_ENCRYPTION_KEYS.split(",")[0]`, **discarding every other key in the list**. | Not live, so no current exposure. It is a trap: it is the module a developer would search for by name, and wiring it up would make the documented rotation procedure destroy all existing credentials, because the retired key would no longer decrypt. | No |
| 4 | **`referrals` has no write policy.** `SELECT TO authenticated` only. | None today. Writes break the moment any role stops being exempt. | Yes |
| 5 | **Five tables RLS-enabled in Alembic, policied only in raw SQL.** `copilot_sessions`, `copilot_messages`, `waitlist`, `library_strategies`, `library_ratings`. | An Alembic-only environment has five deny-all tables. | Yes |
| 6 | **`payment_methods` has no `tenant_id`.** Keyed on `user_id` alone while the execution tables key on `tenant_id`. | None today. Turns the silo migration into a rewrite. | No |
| 7 | **No per-tenant visibility controls on aggregate data.** Tenant names, strategy counts and volumes are not partitioned in dashboards or logs. | Competitive leakage between institutional tenants. | No |
| 8 | **`positions.tenant_id` type divergence.** `PG_UUID` in `core/position_model.py`, `String(36)` in the rebuild migration. `unverified` which is live. | Schema drift; the ORM and migrations disagree. | Worth settling first |

## `scripts/verify_rls.py` reports green for things it does not check

Recorded separately because an isolation review that cites a passing verification script
inherits that script's blind spots. This one has three, and they compound:

1. **It reads `pg_tables.rowsecurity`,** which reports `ENABLE` and has no column for
   `relforcerowsecurity`. It therefore prints a pass for all nine execution tables while
   the owner exemption leaves every policy inert (gap 1).
2. **`check_rls_policies()` cannot fail.** It initialises `all_pass = True` and never
   reassigns it. A table with zero policies prints `SKIP` and still returns a pass.
3. **It checks a table that does not exist.** `CRITICAL_TABLES` includes
   `exchange_credentials`. No migration creates that table; the real one is
   `exchange_keys`, created in `migrations/003_create_exchange_keys_table.sql`. The
   missing table hits the `SKIP` branch, which per point 2 still passes - so the most
   sensitive table on the platform is reported green without being examined, and the
   table that actually holds credentials is never named.

`exchange_keys` could not be meaningfully checked by that script anyway: the vault
reaches it with `SUPABASE_SERVICE_ROLE_KEY`, which carries `BYPASSRLS`, so no RLS policy
on it would ever apply.

`scripts/verify_rls_enforcement.py` was added to answer the enforcement question
directly. It does not supersede `verify_rls.py` - the cross-tenant JWT test there is
genuinely useful - but the two answer different questions and only one of them is about
enforcement.

Related, and the likely root cause of several findings in this document: there are
**three** migration systems in this repository - `backend_app/alembic/versions/`,
`backend_app/migrations/*.sql`, and `migrations/*.sql` at the repository root. Schema
truth is distributed across all three with no documented ordering between them. Gap 5 is
one symptom; the phantom table above is another.

## Orphaned modules that read as live infrastructure

Three separate components were found during this inventory that appear to be part of the
running system and are not. None was found by looking for dead code; each surfaced while
tracing something else, which suggests the real number may be higher.

| Module | Looks like | Actually |
|---|---|---|
| `backend_app/workers/marketplace_expiry_worker.py` | The deployed subscription expiry sweep, specced to run every 30 s | In no deployment manifest. Never started. Subscriptions are never expired. |
| `backend_app/backend/reconciliation_worker.py` | The order/position drift reconciler | Instantiated as a module global; `.start()` never called; `get_reconciliation_worker()` never called outside tests and scripts. Probably superseded by the TEE's own `reconciler_loop()`. |
| `backend_app/core/credential_vault.py` | The credential vault - it is the module the name suggests | Imported by nothing. The live vault is `backend_app/backend/api_key_vault.py`. The orphan's crypto is weaker and contradicts the rotation procedure. |

This matters for an isolation review specifically because each one invites a wrong
conclusion. A reviewer who reads `core/credential_vault.py` concludes the platform
derives keys with PBKDF2 and cannot rotate; one who reads `api_key_vault.py` concludes
it uses `MultiFernet` and can. Both are reading real code in the repository. Only the
second describes production.

The suggested treatment is deletion rather than documentation, with one exception:
`marketplace_expiry_worker.py` should be deployed, not deleted, because the feature it
implements is required and currently absent.
`tests/test_worker_deployment_coverage.py` guards the worker case against recurrence.

## Mechanisms that are working

Worth recording, because an isolation review that lists only gaps misrepresents the
platform.

- **RLS does bind on the PostgREST path.** `core/dependencies.py` builds anon-key clients
  that execute as `authenticated`, a non-owner role. This is the path
  `backend_app/validate_security.py` exercises, and its cross-tenant tests pass.
- **PCI scope is minimal and deliberately so.** Razorpay and Stripe hosted checkout means
  no PAN reaches the platform. `payment_methods` holds a provider token with `brand`,
  `last4` and expiry - all permitted in readable form precisely because the PAN is not
  stored alongside them. CVV and track data are not stored at all.
- **Log redaction is real.** `core/observability.py` deny-lists `pan`, `card`,
  `stripe_signature` and `x_razorpay_signature` by token sequence, and runs Luhn-checked
  redaction on candidate card numbers.
- **Service-role usage is mostly justified.** Billing webhooks have no user session, the
  expiry sweep and TEE reconciliation are cross-tenant by design, and the
  server-authoritative billing writes should hold privilege the caller does not.
- **Idempotency is tenant-scoped.** `idempotency_keys` has a composite primary key of
  `(idempotency_key, tenant_id)`, so one tenant cannot consume another's key.
- **The live credential vault fails closed and rotates properly.**
  `backend/api_key_vault.py` raises `EnvironmentError` at construction when
  `MASTER_ENCRYPTION_KEYS` is absent rather than falling back to a generated key, and
  builds a `MultiFernet` over the whole key list so the first key encrypts while every
  key still decrypts. That makes the zero-downtime rotation in
  `docs/KEY_ROTATION_PROCEDURE.md` genuinely executable, including the
  suspected-compromise path. Gap 3 is that the key set is shared across tenants, not
  that the vault is weak.
- **WebSocket auth is single-use and revocable.** `POST /auth/ws-ticket` mints
  `secrets.token_urlsafe(32)` into Redis with a 30-second TTL;
  `verify_ws_ticket` redeems it with `GETDEL`, so a ticket cannot be replayed. The
  socket routes fail closed when neither ticket nor token is presented. A stateless
  signed token would have been easier and weaker.
- **Quota isolation exists independently of data isolation.** `TenantQuota` and
  `HardQuotaEnforcer` cap DAG sessions, order rates, positions, capital, leverage,
  concentration, storage and WebSocket connections per tenant - the noisy-neighbour
  controls are in place even though data isolation is not.

## Verification

| Question | Command | Status |
|---|---|---|
| Does RLS bind for the app's role? | `python scripts/verify_rls_enforcement.py` | Not yet run against a live database |
| Which services actually run? | `aws ecs list-services --cluster vyomquant-cluster --region ap-southeast-1` | Not yet run |
| Is tenant context reaching queries? | `scripts/verify_rls_enforcement.py` section 5 | Blocked on the above |
| Can a tenant read another's rows via PostgREST? | `python backend_app/validate_security.py` | Exists and passes - and it exercises the one path where RLS does bind, so it is meaningful |
| Is any worker undeployable? | `python -m pytest tests/test_worker_deployment_coverage.py` | Passes; one strict xfail documents two unhosted workers |

## Related

- [ADR-0006](../adr/ADR-0006-bridge-tenancy-with-institutional-silo-tier.md) - the tenancy
  and tiering decision this matrix describes the implementation state of
- `backend_app/core/tenant_db_context.py` - the context propagation mechanism for gap 2
- `scripts/verify_rls_enforcement.py` - enforcement verification for gaps 1, 4, 5
- `tests/test_worker_deployment_coverage.py` - the unhosted-worker guard
