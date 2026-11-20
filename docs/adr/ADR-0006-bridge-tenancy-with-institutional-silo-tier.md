# ADR-0006: Bridge Tenancy with a Silo Tier Reserved for Institutional Tenants

## Status
Accepted

## Context

Algo22 serves retail algorithmic traders today. Subscription tiers in
`backend_app/core/schemas.py` (`SubscriptionTier`) are `free`, `starter` ("Trader"),
`pro` ("Pro Quant"), `enterprise` ("Business", the ₹2,499 tier) and `scale`
("Enterprise", sales-quoted). Every one of these is an individual or small-team
retail plan: thousands of low-revenue-per-tenant accounts sharing one deployment.

We intend to onboard institutional trading firms later. That changes the isolation
question, because an institutional tenant brings three things a retail tenant does not:
an internal audit function that will ask how its order flow is separated from other
tenants', a plausible competitor sitting in the same database, and strategy IP whose
leakage is a commercial loss rather than a privacy incident.

The naive reading is that this forces a platform-wide choice now: stay pooled and be
unable to sell to institutions, or silo everything and destroy the unit economics of a
₹2,499/month retail plan. It does not. Tenancy is a per-service decision, and the
binding constraint is not which model we run today — it is whether adding a dedicated
store for one tenant later requires rewriting every caller.

Current state in the codebase:

- `tenant_id` is already carried on the execution-path tables: `orders`, `positions`,
  `fills`, `idempotency_keys`, `transaction_records`, `transaction_checkpoints`,
  `transaction_rollbacks`, `execution_records`, `reconciliation_mismatches`, `dag_tasks`.
- Postgres RLS policies key off a session variable:
  `USING (tenant_id = current_setting('app.current_tenant_id', true))`
  (`alembic/versions/implement_rls_policies_20260817.py`). The variable is set via
  `SET LOCAL app.current_tenant_id` in `backend/transactional_execution_manager.py`
  and `backend/tenant_rls_validator.py`.
- `payment_methods` (`alembic/versions/d97ffff9c3bb_execution_schema_rebuild.py`) is
  indexed on `user_id` only, with no `tenant_id` column.
- Callers reach Supabase/Postgres directly rather than through a storage-location
  abstraction, so a table's physical home is part of each call site's knowledge.

Regulatory context: billing is INR-primary (`PRICE_BASE_CURRENCY = "INR"` in
`core/subscription_engine.py`, with INR routed to Razorpay and USD to Stripe per
`CheckoutRequest`). Our supervisory regime is therefore SEBI and the exchanges, not
FINRA or the SEC. This matters for how we justify the decision — see Rationale.

## Decision

Adopt **bridge tenancy as the platform default**, with **silo available as a tier**,
and treat **storage-location abstraction as the prerequisite that unblocks the tier**.

1. **Bridge for all tenant financial data.** Shared compute on ECS Fargate; tenant
   data logically partitioned by `tenant_id` and enforced below the application layer.
   This covers orders, positions, fills, executions, ledger and reconciliation data.

2. **Pool for reference and control-plane data.** Instruments, exchanges, calendars,
   rate tables, product catalogue, and the control plane itself (tenant management,
   identity, billing, subscription state, tier enforcement). The control plane is
   shared for all tenants regardless of their tier, by design.

3. **Silo reserved, not built.** No silo infrastructure is provisioned now. The `scale`
   tier becomes the carrier for silo when the first institutional tenant signs:
   dedicated Postgres instance or schema, dedicated encryption key, and — if the tenant
   is a competitor to another tenant — a dedicated AWS account.

4. **Storage-location abstraction is a precondition, and is the work this ADR
   authorises.** All tenant-scoped reads and writes must route through a data access
   layer that resolves a tenant's physical storage target from the tenant record,
   rather than hardcoding it. Call sites must not know whether a tenant lives in the
   shared store or a dedicated one.

5. **`payment_methods` gets a `tenant_id` column** so every tenant-scoped table shares
   one partition key. Mixed `user_id`/`tenant_id` keying is the specific thing that
   makes a later migration a rewrite.

6. **RLS is a backstop, not the isolation boundary.** It stays, and we add defence
   above it: tenant context resolved once at request ingress from the verified JWT,
   propagated explicitly through sync calls, async jobs, Redis pub/sub messages and
   worker invocations, and asserted in the data access layer before a query is issued.

### Implementation status

Two pieces of this decision exist in the tree; the rest is not started.

| Piece | State | Where |
|---|---|---|
| Enforcement verification | Done | `scripts/verify_rls_enforcement.py` - read-only; answers whether RLS binds for the connected role, which `scripts/verify_rls.py` cannot |
| Tenant context propagation | Mechanism done, adoption not started | `backend_app/core/tenant_db_context.py`, tested in `tests/test_tenant_db_context.py` |
| Storage-location abstraction | Not started | - |
| `tenant_id` on `payment_methods` | Not started | - |
| `referrals` write policies | Not started | - |
| FORCE ROW LEVEL SECURITY | Blocked on the three rows above | - |

`tenant_db_context` is deliberately additive: `get_db` is unchanged, so adoption is a
per-router decision and nothing currently working is altered by the module landing. It
uses `set_config('app.current_tenant_id', :tenant_id, true)` rather than
`SET LOCAL app.current_tenant_id = :tenant_id`. `SET` accepts no bind parameters, so the
existing form works only because psycopg2 interpolates client-side; `set_config` is an
ordinary function call, so the id binds through the protocol. The `true` third argument
is load-bearing: `core/database_pool.py` forces `pool_mode=transaction`, so a
session-scoped setting would survive the connection's return to the pool and be
inherited by the next tenant to check it out.

Order of work: adopt `tenant_db_context` on the tenant-scoped routers and workers →
run `verify_rls_enforcement.py` to confirm context now lands → add `referrals` write
policies → then `FORCE ROW LEVEL SECURITY` one table at a time, re-running the verifier
between each.

## Rationale

Bridge is the correct default because the cost of pooling financial data is paid at
audit time, not at runtime. Retail tenants will not ask how their positions are
separated from another trader's. An institutional tenant's audit team will, and
"a `WHERE` clause and an RLS policy" is a weaker answer than "a dedicated store with
its own key". Bridge lets us give the strong answer for the data that matters while
keeping one shared compute fleet, which is what makes a ₹2,499 plan viable.

Silo-as-a-tier rather than silo-now follows from tenant count and revenue. Silo is
justified at high revenue per tenant and becomes operationally unmanageable past
roughly 50-100 tenants without heavy automation. Our retail base is the opposite
shape on both axes. Provisioning silo infrastructure before a tenant exists who needs
it means paying its operational cost for zero benefit.

On the regulatory basis: generic capital-markets SaaS guidance says silo is
non-negotiable for competing broker-dealers because FINRA and SEC rules on record
separation demand it. **That reasoning does not apply to us** — we are SEBI-regulated,
not FINRA-regulated. We are adopting a materially similar outcome for a different and
narrower reason: competing institutional tenants in a shared store is a commercial and
contractual risk around strategy IP, and the isolation question will be asked by our
tenants' own audit functions. When we document this for an institutional prospect we
must cite SEBI and exchange requirements, not US ones. The SEBI algorithmic trading
framework and exchange-level strategy approval obligations have not been assessed in
this ADR and remain open (see Risks).

PCI-DSS, by contrast, does apply to us despite the region, because it is a card-brand
standard enforced worldwide rather than US law. Our current position is already sound:
Razorpay and Stripe hosted checkout means no PAN reaches our servers, and
`core/observability.py` deny-lists `pan`, `card`, `stripe_signature` and
`x_razorpay_signature` with Luhn-based redaction.

`payment_methods` holds a provider token plus `brand`, `last4`, `expiry_month` and
`expiry_year`. To be precise: `last4` and expiry date *are* cardholder data elements
under PCI-DSS, but they are permitted to be stored in readable form specifically
because they are not accompanied by the full PAN — it is the PAN that must be rendered
unreadable, and sensitive authentication data (CVV, full track) that must never be
stored at all. We store neither. Adding `tenant_id` to this table does not change CDE
scope. What *would* change it is accepting a raw card number server-side, which must
stay off the table. Any PCI assertion here requires validation against the appropriate
SAQ by a qualified assessor; this ADR is an engineering position, not a compliance
determination.

## Alternatives Considered

### Option A: Stay pooled, defer the whole question until an institutional tenant signs
- **Pros:** Zero work now. Maximum short-term velocity.
- **Cons:** The abstraction work does not get cheaper, it gets more expensive — every
  new call site written against a hardcoded storage location is another one to migrate.
  Worse, the bill arrives at the worst possible moment: after a contract is signed,
  under a delivery deadline, with an auditor waiting.
- **Rejected because** it converts a manageable refactor into a blocking rewrite, and
  does so precisely when we have least room to absorb it.

### Option B: Silo everything now, per tenant
- **Pros:** Strongest isolation. Simplest story to any auditor. Clean per-tenant cost
  attribution.
- **Cons:** Economically impossible at retail scale. A dedicated store and key per
  free-tier user is absurd, and the operational surface grows linearly with signups.
- **Rejected because** it is unaffordable for the tenant population we actually have.

### Option C: Bridge everything including the control plane
- **Pros:** Uniform model, nothing to reason about per service.
- **Cons:** The control plane exists to operate across all tenants. Partitioning tenant
  management, billing and tier enforcement per tenant makes the platform's own
  operation harder with no isolation benefit, since this data is not tenant-sensitive
  in the way position data is.
- **Rejected because** it isolates the wrong thing at real operational cost.

### Option D: Rely on Supabase RLS alone as the isolation boundary
- **Pros:** Already implemented. No further work.
- **Cons:** RLS is bypassed by the table owner and by Supabase's `service_role` key.
  Our migrations issue `ENABLE ROW LEVEL SECURITY` and never
  `FORCE ROW LEVEL SECURITY`, so owner-role connections are not subject to the
  policies at all. Any backend path holding the service-role key sees every tenant.
- **Rejected as a sole boundary.** Retained as a backstop layer.

## Consequences

### Positive
- One shared compute fleet keeps retail unit economics intact while tenant financial
  data stays logically isolated and separately keyable.
- An institutional tenant can be onboarded onto a dedicated store without a schema
  migration or a call-site rewrite, because storage location is resolved at runtime.
- Tier upgrades stop being a data-migration project and become a tenant-record change.
- Uniform `tenant_id` keying across every tenant-scoped table gives one coherent
  partition key to reason about, test, and later split on.
- PCI scope stays minimal and unaffected.

### Negative (accepted trade-offs)
- The data access abstraction is real work with no user-visible feature attached. It
  will be hard to prioritise against product work and needs explicit protection.
- An indirection layer between callers and storage costs some directness when
  debugging a query path.
- Bridge is more complex than pool: per-tenant key management and storage resolution
  are machinery that pool simply does not have.
- Retail tenants fund none of this. The payoff is entirely in the institutional
  segment, so the investment is speculative until the first such contract closes.

### Risks
- **The SQLAlchemy path has no database-level tenant isolation.** This is the largest
  live gap, and it is worth stating precisely because two separate mechanisms are
  easily conflated.

  The nine tenant tables have RLS enabled and a `FOR ALL` policy each
  (`alembic/versions/implement_rls_policies_20260817.py`). Those policies are
  nevertheless inert on the SQLAlchemy path, because PostgreSQL exempts a table's
  owner from its own policies unless `FORCE ROW LEVEL SECURITY` is set, and the
  migration issues only `ENABLE`. `.env.example` and `core/database_pool.py` show the
  backend connecting as `postgres`, which on Supabase owns tables created by
  migrations. Isolation on this path therefore rests entirely on application-level
  `WHERE` clauses today.

  Separately, the Supabase `service_role` key is outside RLS because `service_role`
  carries the `BYPASSRLS` role attribute. **`FORCE ROW LEVEL SECURITY` does not change
  this** — FORCE removes only the owner's implicit exemption, never a role attribute.
  Service-role exposure is reduced by using the key less, not by forcing RLS. Most
  current service-role usage is legitimately cross-tenant anyway (billing webhooks
  where no user session exists, the marketplace expiry sweep, TEE reconciliation), and
  the server-authoritative billing writes in `routers/billing.py` arguably *should*
  hold privilege the caller does not.

  The PostgREST path is the exception: `core/dependencies.py` builds anon-key clients
  that execute as `authenticated`, a non-owner role, so RLS does bind there. That is
  the path `validate_security.py` exercises, which is why its isolation tests pass
  while the SQLAlchemy path is unprotected.

- **Forcing RLS now would break reads rather than secure them.** `SET LOCAL
  app.current_tenant_id` exists at two call sites only
  (`backend/transactional_execution_manager.py`, `backend/tenant_rls_validator.py`),
  neither on a request path; `get_db` and `get_db_context` set nothing, and no
  middleware or FastAPI dependency sets it. With the setting unset,
  `current_setting(..., true)` returns NULL and the policy comparison yields NULL
  rather than TRUE, so an enforced policy matches zero rows. Under FORCE that means
  silently empty result sets on orders, positions and fills — a worse failure mode
  than the current one, because it is invisible. Tenant context propagation is the
  prerequisite, not a follow-up. Async workers, Redis pub/sub consumers and background
  jobs all need an audit for whether they carry tenant context at all.

- **`referrals` has a SELECT-only policy.**
  `alembic/versions/6f1b3d9c8a7e_add_referrals.py` defines
  `referrals_referrer_select` (`FOR SELECT TO authenticated`) and no INSERT, UPDATE or
  DELETE policy. Any role that stops being exempt loses the ability to write that
  table. The referral commission RPCs called from `routers/billing.py` write referral
  data; if they are `SECURITY DEFINER` owned by `postgres`, they begin failing the
  moment FORCE is applied. Their definitions have not been reviewed.

- **Five tables are policied outside the Alembic chain.**
  `alembic/versions/e88f9911b5a2_consolidate_full_schema.py` enables RLS on
  `copilot_sessions`, `copilot_messages`, `waitlist`, `library_strategies` and
  `library_ratings` but defines no policies for them; those live in the parallel
  raw-SQL migration system (`backend_app/migrations/*.sql`, `archived_migrations/`).
  RLS enabled with no policy denies all access to non-exempt roles, so an environment
  that ran only the Alembic chain has five deny-all tables. The ordering dependency
  between the two migration systems is undocumented.

- **The live database state cannot be read from source.** Every statement in
  `implement_rls_policies_20260817.py` is wrapped in a `try`/`except` that only
  `print`s the failure, so the migration reports success even when an `ENABLE` or a
  `CREATE POLICY` did not apply. `scripts/verify_rls.py` does not close this gap: it
  reads `pg_tables.rowsecurity`, which reports `ENABLE` only and has no visibility of
  `relforcerowsecurity`, and its policy check initialises `all_pass = True` and never
  reassigns it, so it cannot fail. `scripts/verify_rls_enforcement.py` was added to
  answer the enforcement question directly, and must be run before any FORCE decision.

- **`positions.tenant_id` type divergence.** `core/position_model.py` declares
  `PG_UUID(as_uuid=True)`; `alembic/versions/d97ffff9c3bb_execution_schema_rebuild.py`
  creates `String(36)`. The ORM model and the migration disagree, and which one is live
  is unverified. This is *not* a policy defect: the seven uncast policies cover
  `varchar(36)` columns and the two that cast with `::text` cover precisely the two
  `uuid` columns (`execution_records`, `reconciliation_mismatches`), so the policy
  predicates are type-correct as written.
- **SEBI obligations are unassessed.** Algorithmic trading approval, exchange-level
  strategy registration, order-audit-trail retention and any colocation rules have not
  been evaluated. They may impose isolation or record-keeping requirements stricter
  than this ADR assumes, and they are the requirements that actually govern us.
- **Competing-tenant metadata leakage.** Even under bridge, aggregate signals — tenant
  names, strategy counts, volume figures visible in shared dashboards or logs — can be
  competitively sensitive between institutional tenants. Zero cross-tenant visibility
  needs designing before the second institutional tenant, not after.
- **Abstraction drift.** If the data access layer is introduced but new code continues
  to bypass it, we will hold the cost without the benefit and believe we are portable
  when we are not. This needs a lint or review gate to stay true.

## Related
- [ADR-0001](./ADR-0001-extract-unified-execution-engine.md) — unified execution engine
  that owns the tenant-scoped execution path.
- [ADR-0005](./ADR-0005-state-management-write-through-cache.md) — Redis/Postgres
  write-through cache; its Redis keys are a tenant-scoped surface that the storage
  abstraction must also cover.
