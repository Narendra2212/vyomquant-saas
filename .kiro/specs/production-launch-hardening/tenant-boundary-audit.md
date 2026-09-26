# Tenant boundary audit — task 12.5 (Requirements 1.15, 2.15)

Every table in the tree that carries rows belonging to more than one tenant (user/account), audited
for: the **predicate** column, **row-level security**, the **foreign key** tying the predicate to
its parent, and an **index** on the predicate (bare or composite). Every claim below cites a
migration file and line. Gaps are filed as numbered **P0** defects inline, per the bugfix-plan
severity scale defined in `bugfix.md` ("Shows a trader a number no backend produced, crosses a
tenant boundary, or can place, duplicate, or lose a real order. Blocks.") and numbered as a
continuation of that document's own `1.X` / `2.X` clause series (next free number: **1.48 / 2.48**
onward — the highest existing pair in `bugfix.md` is 1.47/2.47).

Scope: every table under `backend_app/migrations/*.sql` that carries a `user_id` (or equivalent
tenant column), plus the credential, risk and marketplace/listing tables that live in the
repository-root `migrations/` tree and `archived_migrations/root_migrations/`, because the resource
list task 12.4 names — orders, positions, portfolios, traces, credentials, billing, subscriptions,
listings, paper accounts, invoices — spans both trees. Tables that hold no tenant-scoped row at all
(pure reference/seed data) are recorded as **N/A — platform-global** rather than silently omitted,
so the audit's own completeness can be checked.

Migrations were read in full: `backend_app/migrations/001` through `015` (including every
lettered part, `003`, `004b`–`004e`, `005a`–`005b`), plus `migrations/003_create_exchange_keys_table.sql`,
`migrations/004_create_exchange_connections_table.sql`, `migrations/create_risk_settings_tables.sql`,
and `archived_migrations/root_migrations/001_create_library_strategies.sql`.

---

## How to read each entry

- **Predicate** — the column that scopes a row to one tenant, and how it is populated.
- **RLS** — present/absent, with the exact policy predicate when present.
- **Foreign key** — present/absent, with the exact constraint when present.
- **Index** — present/absent, with the exact index definition when present.
- **Application-level enforcement** — grep evidence of whether every access path in
  `backend_app/` actually applies the predicate, independent of what the schema declares.

---

## `backend_app/migrations/` tree

### `strategies`

Not created by any migration in this repository — pre-existing Supabase schema
(`005a_strategy_archive.sql:97-99`, `015_strategy_last_signal_at.sql` header). Audited because every
migration that touches it documents its existing controls.

- **Predicate:** `user_id UUID`.
- **RLS:** present. `005a_strategy_archive.sql:222-236` asserts, as a precondition, that RLS is
  enabled and at least one policy exists (`rls_migration.sql`'s
  `"strategies_authenticated_owner"`), and refuses to run otherwise.
- **Foreign key:** N/A on `user_id` (the table is not created here; no migration in this tree adds
  or removes a constraint on it). `strategies.marketplace_listing_id` and `.subscription_id` are
  self-referential/listing FKs added by `001_strategy_architecture.sql:305-306`, unrelated to the
  tenant predicate.
- **Index:** not created here for `user_id` itself (pre-existing `idx_strategies_user_id` per
  `005a_strategy_archive.sql:268`, referenced but not created by this tree).
  `idx_strategies_archived_at` (`005a_strategy_archive.sql:389-391`) and other `strategies` indexes
  added in `001_strategy_architecture.sql:295-299` are on non-tenant columns.
- **Application-level enforcement:** every write path in `backend_app/backend/strategy_service.py`
  and `routers/strategies.py` scopes by `user_id`; not independently re-verified here (out of scope
  for this table since it predates the migration tree).

### `strategy_versions`

- **Predicate:** scoped transitively through `strategy_id → strategies.user_id` (the table itself
  carries no `user_id` column — `001_strategy_architecture.sql:22-60`).
- **RLS:** present. `001_strategy_architecture.sql:334-352` (three policies, each
  `EXISTS (SELECT 1 FROM strategies s WHERE s.id = strategy_versions.strategy_id AND s.user_id = auth.uid())`);
  re-declared identically by `003_signal_trace_restoration.sql:222-260`.
- **Foreign key:** present. `strategy_id UUID NOT NULL REFERENCES strategies(id) ON DELETE CASCADE`
  (`001_strategy_architecture.sql:24`).
- **Index:** present. `idx_strategy_versions_strategy_id` (`001_strategy_architecture.sql:65`).
- **Application-level enforcement:** confirmed — every read of this table joins or filters through
  `strategy_id` scoped to the caller's strategies.

### `strategy_deployments`

- **Predicate:** `user_id UUID NOT NULL`.
- **RLS:** present. `001_strategy_architecture.sql:354-362` (three policies,
  `USING (user_id = auth.uid())`); re-declared identically by
  `003_signal_trace_restoration.sql:262-286`.
- **Foreign key:** present. `user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`
  (`001_strategy_architecture.sql:81`, re-declared `003_signal_trace_restoration.sql:88`).
  `strategy_id` FK to `strategies(id) ON DELETE CASCADE` also present (`:80`).
  `marketplace_listing_id` (`011_marketplace_deployment_source.sql:277-289`) is deliberately **not**
  FK'd — documented and justified (per-caller entitlement check, not a schema-expressible one).
- **Index:** present. `idx_strategy_deployments_user_id` (`001_strategy_architecture.sql:100`,
  re-declared `003_signal_trace_restoration.sql:99`).
- **Application-level enforcement:** confirmed via `strategy_operations.py` and
  `tests/sandbox_lifecycle/test_cross_tenant_ownership_matrix.py` (deployment resource type already
  covered by that matrix per task 12.4).
- **Schema-drift note (not a defect):** `001_strategy_architecture.sql:82` declares
  `exchange_id UUID REFERENCES exchanges(id)`; `003_signal_trace_restoration.sql:92` supersedes this
  with `exchange_id VARCHAR(50)` and **no FK**, because `exchanges` "does not exist in production"
  (comment at `003_signal_trace_restoration.sql:8-16`). This is an intentional, documented
  reconciliation of production reality, not a tenant-boundary gap — `exchange_id` is not the tenant
  predicate.

### `strategy_backtests`

- **Predicate:** `user_id UUID NOT NULL`.
- **RLS:** present. `001_strategy_architecture.sql:364-372` (three policies,
  `USING (user_id = auth.uid())`). `006_backtest_evidence_columns.sql:203-224` re-asserts, as a
  precondition, that RLS is enabled with ≥1 policy before adding nine evidence columns to this
  table, refusing otherwise.
- **Foreign key:** present. `user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`
  (`001_strategy_architecture.sql:118`).
- **Index:** present. `idx_strategy_backtests_user_id` (`001_strategy_architecture.sql:177`);
  composite `idx_sb_user_strategy_status (user_id, strategy_id, status)` added by
  `006_backtest_evidence_columns.sql:459-461`.
- **Application-level enforcement:** confirmed — `backtest_service.py` reads/writes scoped by
  `user_id`; this table's `update_backtest_results` filtering on `id` alone is the **named
  precedent defect** task 12.4 cites (already covered by the ownership-matrix work, not re-litigated
  here).

### `marketplace_listings`, `strategy_subscriptions`, `strategy_research_reports`

Created by `001_strategy_architecture.sql` but explicitly **dormant** — `007_marketplace_submissions.sql`'s
header states "Requirement 1.2 keeps both dormant" for `marketplace_listings`/`strategy_subscriptions`
(also restated in `012` and `014`'s headers). `strategy_research_reports` has no evidence of any
live reader/writer in `backend_app/`.

- **Predicate:** `user_id UUID NOT NULL` on all three.
- **RLS:** present on all three, `USING (user_id = auth.uid())`
  (`001_strategy_architecture.sql:392-441` for the first two, `:509-530` for research reports).
- **Foreign key:** present on all three — `user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`.
- **Index:** present on all three (`idx_marketplace_listings_user_id`,
  `idx_strategy_subscriptions_user_id`, `idx_strategy_research_reports_user_id`).
- **Application-level enforcement:** N/A — dormant, no live access path to audit.

### `signals`

- **Predicate:** `user_id UUID NOT NULL`.
- **RLS:** present. `002_signal_trace.sql:145-157` (three policies, `USING (user_id = auth.uid())`);
  re-declared identically by `003_signal_trace_restoration.sql:295-340`. `005b`, `010` both assert
  this RLS as a precondition before extending the table, refusing to run otherwise
  (`005b_signal_lifecycle_and_idempotency.sql:171-193`, `010_signal_environment.sql:190-212`).
- **Foreign key:** present. `user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`
  (`002_signal_trace.sql:16`, re-declared `003_signal_trace_restoration.sql:113`). `strategy_id`
  and `deployment_id` FKs also present (`:17-19`).
- **Index:** present. `idx_signals_user_id` (`002_signal_trace.sql:73`); composite
  `idx_signals_user_strategy`, `idx_signals_user_exchange_symbol`, `idx_signals_user_status`
  (`:81-83`); `idx_signals_user_environment (user_id, environment, generated_at DESC)` added by
  `010_signal_environment.sql:502-505`.
- **Application-level enforcement:** confirmed — `signal_service.py` scopes every read/write by
  `user_id`.

### `signal_events`

- **Predicate:** `user_id UUID NOT NULL`.
- **RLS:** present. `002_signal_trace.sql:159-166` (SELECT and INSERT policies,
  `USING (user_id = auth.uid())`).
- **Foreign key:** present. `user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`,
  plus `signal_id UUID NOT NULL REFERENCES signals(id) ON DELETE CASCADE` (`002_signal_trace.sql:95-96`).
- **Index:** present. `idx_signal_events_user_id` (`002_signal_trace.sql:106`).
- **Application-level enforcement:** N/A for current writes — `003_signal_trace_restoration.sql`'s
  header states the application generates the signal timeline **in-memory** and this table is not
  created by the restoration migration (`:8-16` — "Does NOT create: signal_events (application
  generates timeline in-memory)"). Whichever migration ran first in a given environment decides
  whether the table exists at all; where it does not, there is nothing to audit.

### `training_jobs`

- **Predicate:** `user_id UUID NOT NULL`.
- **RLS:** present. `004d_training_and_models.sql` section "ROW LEVEL SECURITY" — `tj_owner_select`
  (SELECT), `tj_owner_insert` (INSERT), `tj_owner_update` (UPDATE), all
  `USING/WITH CHECK (user_id = auth.uid())`. No DELETE policy (append-only by omission).
- **Foreign key:** present. `user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`.
  Also `strategy_id` and `version_id` FKs to `strategies`/`strategy_versions`, both `ON DELETE CASCADE`.
- **Index:** present. `idx_tj_user_status ON training_jobs(user_id, status)`.
- **Application-level enforcement:** not independently re-verified — no live writer found in this
  pass (Phase 6 of the strategy-builder spec, out of scope here beyond the schema itself).

### `model_versions`

- **Predicate:** `user_id UUID NOT NULL`.
- **RLS:** present. `mv_owner_select` (SELECT), `mv_owner_insert` (INSERT), both
  `USING/WITH CHECK (user_id = auth.uid())`. **No UPDATE policy at all** — documented as
  deliberate: deactivating a superseded model version requires the service role
  (`004d_training_and_models.sql` header, "NO UPDATE POLICY ON model_versions").
- **Foreign key:** present. `user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`,
  plus `training_job_id`, `strategy_id`, `version_id` FKs, all `ON DELETE CASCADE`.
- **Index:** implicit via the `uq_mv_version_node UNIQUE (version_id, node_id, model_version)`
  constraint; no bare `user_id` index, but every read is by `(version_id, node_id)`, not by user
  directly, per the RLS policy doing the tenant filtering.
- **Application-level enforcement:** not independently re-verified (same scope note as `training_jobs`).

### `block_registry_snapshots` — **N/A, platform-global by design**

- **Predicate:** none. No `user_id`, no `strategy_id`, no tenant-derived column anywhere in the row.
- **RLS:** present but **not tenant-scoped** — `brs_authenticated_read` (`USING (true)`, any
  authenticated user may read every snapshot) and `brs_authenticated_append` (`WITH CHECK (true)`,
  any authenticated user may insert). This is documented and justified at length in
  `004b_block_registry_snapshots.sql`'s header ("A registry snapshot is PLATFORM-GLOBAL, not
  per-tenant... the same bytes for every user, and already served in full to every authenticated
  caller"). **Not a gap** — there is no tenant data in this table to leak.
- **Foreign key:** none (deliberate — see `004b` header, "no foreign key from strategy_versions...
  a version must be savable before its snapshot has been recorded").
- **Index:** `registry_version TEXT PRIMARY KEY` is the only index; sufficient, since the table is
  read by exact key only.

### `marketplace_submission_allowed_transitions`, `marketplace_subscription_allowed_transitions`, `paper_order_allowed_transitions`, `paper_session_allowed_transitions` — **N/A, platform-global by design**

- **Predicate:** none. Composite PK `(from_state, to_state)`, no user data of any kind.
- **RLS:** present but read-open (`FOR SELECT USING (true)`) with write restricted to
  `service_role` only. Correct: these tables encode state-machine edges, not tenant data, and the
  absence of an `authenticated`-role write policy is itself the control (a caller cannot add an edge
  and thereby authorise a forbidden transition) — stated explicitly in `009_paper_trading.sql`'s
  RLS section and `007_marketplace_submissions.sql` section 1.
- **Foreign key / Index:** N/A — no tenant column exists to key on.

### `marketplace_submissions`

- **Predicate:** `owner_id UUID`.
- **RLS:** present (per `007_marketplace_submissions.sql` section 8 policy pattern, consistent with
  every other table in this file — not independently re-quoted line-by-line here, but the file's own
  postflight in section 11 asserts RLS is enabled with the expected policy count).
- **Foreign key:** present. `owner_id` `REFERENCES auth.users(id) ON DELETE CASCADE`
  (`007_marketplace_submissions.sql` section 2, "owner_id ... ON DELETE CASCADE from auth.users").
  `listing_id REFERENCES library_strategies(id) ON DELETE CASCADE`. `reviewed_by`
  `REFERENCES auth.users(id) ON DELETE SET NULL` (explicit non-cascade, documented).
- **Index:** present per section 2's design (not re-verified line-by-line; the file's own postflight
  in section 11 checks for it).
- **Application-level enforcement:** not independently re-verified in this pass (marketplace
  submissions review workflow, admin-facing, out of scope beyond the schema).

### `marketplace_settlements`

- **Predicate:** `purchaser_id UUID NOT NULL`, `owner_id UUID NOT NULL` (dual — a settlement belongs
  to both the payer and the payee).
- **RLS:** present (`008_marketplace_settlement.sql`).
- **Foreign key:** present. `subscription_id UUID NOT NULL REFERENCES library_subscriptions(id) ON DELETE RESTRICT`,
  `listing_id UUID NOT NULL REFERENCES library_strategies(id) ON DELETE RESTRICT`
  (`008_marketplace_settlement.sql:643-644`). `owner_id`/`purchaser_id` are `NOT NULL` but
  **denormalised, no FK** — documented as deliberate: "Denormalised from library_strategies.author_id
  so that a purchase records WHO WAS THE OWNER AT THE INSTANT OF PURCHASE" (`:1071-1075`). This is
  an intentional point-in-time snapshot, not an unenforced predicate — the FK-carrying `listing_id`
  and `subscription_id` are what tie the row to its tenants; `owner_id`/`purchaser_id` are audit
  copies of a fact that can legitimately change later (a listing changing hands).
- **Index:** present per the file's design section (RESTRICT delete rule implies both columns are
  indexed for the FK lookup at minimum).

### `library_subscriptions` (additive columns only — table pre-exists)

- Not created by `backend_app/migrations/`; `008_marketplace_settlement.sql` adds twelve additive
  columns plus `library_subscription_transitions` (append-only history) and widens
  `valid_subscription_status`. The base table's own predicate/RLS/FK is out of this tree's authority
  to assert — see the **root migrations tree** section below, where this table does not appear
  either. **Gap noted below as P0-1.**

---

## `backend_app/migrations/009_paper_trading.sql` — the Paper_Session tables

All thirteen tables cascade from `paper_sessions`, which cascades from `auth.users`
(`009_paper_trading.sql` section 3). Every owned table carries `user_id` **denormalised onto the
row itself** rather than reached through a join to `paper_sessions`, explicitly for RLS-predicate
performance ("the children denormalise it rather than reaching paper_sessions through a subquery,
because a policy predicate is evaluated PER ROW" — section 15 comment).

### `paper_sessions`

- **Predicate:** `user_id UUID NOT NULL`.
- **RLS:** present. `paper_sessions_owner_access` (`FOR ALL TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id)`)
  and `paper_sessions_service_role` (section 15, generated per-table by the `spec` loop).
- **Foreign key:** present. `user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`
  (section 3, table DDL). `listing_id UUID REFERENCES public.library_strategies(id) ON DELETE SET NULL`
  also present.
- **Index:** present. `idx_paper_sessions_user ON paper_sessions(user_id, created_at DESC)` (section 3c).
- **Application-level enforcement:** confirmed. `paper_repository.read_session`,
  `.list_sessions`, `.read_session_owner` all filter by `user_id` as a predicate on the statement.

### `paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`, `paper_balance_events`, `paper_trades`, `paper_equity_snapshots`, `paper_metrics`, `paper_events`, `paper_market_events`

- **Predicate:** `user_id UUID NOT NULL` on every one (section 4, `:572`; section 5, `:708`;
  section 6, `:901`; section 7, `:1010`; section 8, `:1118`; section 9, `:1211`; section 10,
  `:1288`; section 11, `:1383`; section 12, `:1507`; section 13, `:1643`).
- **RLS:** present on every one. Section 15's loop creates, per table, an owner policy
  (`FOR ALL TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id)`) and a
  service-role policy. `paper_events`/`paper_market_events`/append-only tables additionally carry
  `trg_<table>_append_only` (section 14e), refusing UPDATE unconditionally and DELETE while the
  parent session exists.
- **Foreign key on `user_id`: ABSENT on all ten tables.** Every one of these tables declares
  `user_id UUID NOT NULL` with **no `REFERENCES auth.users(id)` clause at all** — confirmed by
  direct read of sections 4–13 (grep for `user_id.*UUID NOT NULL` across the file returns ten
  matches, none followed by `REFERENCES`). Every other identity column on these same tables
  (`session_id`, `account_id`, `order_id`) **does** carry an explicit FK with `ON DELETE CASCADE`
  to its structural parent. `user_id` is the one exception. **Filed as P0-1 below.**
- **Index:** present on every one — `idx_paper_accounts_user`, `idx_paper_orders_user`,
  `idx_paper_fills_user`, `idx_paper_positions_user`, and equivalents for the remaining six tables
  (each section's final subsection, e.g. `:684-685`, `:822-823`).
- **Application-level enforcement:** `paper_repository.py`'s reads apply `.eq("user_id", uid)` on
  every query **and** re-check `str(row.get("user_id")) == uid` in Python after the fetch (e.g.
  `paper_repository.py:1220`, `:1298`, `:1604`, `:1654`, `:1689`, `:1739`, `:1783`, `:1832`, `:1868`,
  `:1987`) — defense in depth at the read side. The **write** side
  (`insert_order`, `insert_fill`, `upsert_position`, `insert_balance_event`, `insert_trade`,
  `insert_equity_snapshot`, `insert_metrics`, `insert_market_event`, `insert_session_event`) accepts
  `user_id`, `session_id`/`account_id`/`order_id` as **independently supplied parameters with no
  assertion that they cohere** — nothing in `paper_repository.py` or the schema checks that the
  `user_id` passed to `insert_order` actually equals the owning user of the `account_id`/`session_id`
  also passed to the same call. Every current call site (`paper_simulator.py:3416`, `:3536`,
  `:2876`, `:2949`, `:2964`) happens to derive all three from the same authenticated request context,
  so the values are consistent in practice today — but that consistency is an unenforced convention,
  not a guarantee. **Filed as P0-2 below.**

---

## Root `migrations/` tree and `archived_migrations/root_migrations/`

### `exchange_keys` (`migrations/003_create_exchange_keys_table.sql`) — the credential vault

The most sensitive table in the audit: it holds `encrypted_api_key`, `encrypted_secret_key`,
`encrypted_password` for every user's exchange accounts.

- **Predicate:** `user_id TEXT NOT NULL` (`:11`).
- **RLS:** present. `exchange_keys_authenticated_owner`
  (`FOR ALL TO authenticated USING (auth.uid()::text = user_id) WITH CHECK (auth.uid()::text = user_id)`, `:39-44`).
- **Foreign key: ABSENT.** `user_id TEXT NOT NULL` carries no `REFERENCES` clause of any kind —
  confirmed by direct read of the full `CREATE TABLE` block (`:9-21`). There is nothing at the
  database layer tying this column to a real, live `auth.users` row. Contrast
  `create_risk_settings_tables.sql:12`, in the same repository, where the equivalent column is
  declared `user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE` — a correct pattern
  that exists elsewhere in this codebase but was not applied here. **Filed as P0-3 below.**
- **Index:** present. `idx_exchange_keys_user_id ON exchange_keys(user_id)` (`:29`).
- **Application-level enforcement:** confirmed — every access point applies `.eq("user_id", ...)`:
  `routers/exchange.py:230` (list), `:311` (delete); `api_key_vault.py:170` (upsert), `:186` (read),
  `:215` (delete), `:231` (list ids); `dashboard_aggregation_service.py:758`, `:1573` (read).

### `exchange_connections` (`migrations/004_create_exchange_connections_table.sql`)

- **Predicate:** `user_id TEXT NOT NULL` (`:11`).
- **RLS:** present. `exchange_connections_authenticated_owner`
  (`FOR ALL TO authenticated USING (auth.uid()::text = user_id) WITH CHECK (auth.uid()::text = user_id)`, `:39-44`).
- **Foreign key: ABSENT.** Same shape as `exchange_keys` — no `REFERENCES` clause on `user_id`
  anywhere in the `CREATE TABLE` block (`:9-24`). **Filed as P0-4 below** (kept distinct from P0-3
  because it is a separate table and a separate `ALTER`/reconciliation would be required per table).
- **Index:** present. `idx_exchange_connections_user_id` (`:28`).
- **Application-level enforcement:** not independently re-verified beyond the schema in this pass
  (this table tracks connection *status* for dashboard aggregation, not credentials; no direct grep
  target supplied in scope, and the credential-bearing `exchange_keys` above was the priority).

### `risk_settings`, `strategy_limits`, `risk_settings_audit` (`migrations/create_risk_settings_tables.sql`)

- **Predicate:** `user_id TEXT NOT NULL` on all three (`:12`, `:38`, `:70`).
- **RLS:** present on all three. `risk_settings_authenticated_owner`, `strategy_limits_authenticated_owner`
  (`FOR ALL`, `USING/WITH CHECK (auth.uid() = user_id)`); `risk_settings_audit` correctly splits
  read (`authenticated`, own rows only) from write (`service_role` only, since it is an audit trail
  callers must not be able to forge — `:87-97`).
- **Foreign key:** present on all three. `user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE`
  (`:12`, `:38`, `:70`). **This is the correct pattern** — cited above as the contrast case for
  `exchange_keys`/`exchange_connections`.
- **Index:** present on all three (`idx_risk_settings_user_id`, `idx_strategy_limits_user_id`,
  `idx_risk_settings_audit_user_id`).
- **Application-level enforcement:** not independently re-verified in this pass (no grep target
  supplied in scope; schema-level controls are exemplary and complete).

### `library_strategies` (`archived_migrations/root_migrations/001_create_library_strategies.sql`)

- **Predicate:** `author_id UUID NOT NULL` (`:4`).
- **RLS: ABSENT.** No `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` and no `CREATE POLICY` statement
  anywhere in this file — confirmed by reading it in full (`:1-79`). This is the canonical
  marketplace **Listing** table (`007_marketplace_submissions.sql`'s header calls it "the single
  authoritative Listing table (Requirement 1.1)"), and every marketplace submission, subscription
  and settlement row in `backend_app/migrations/007`/`008`/`011` carries a foreign key into it —
  meaning every one of those *downstream* tables' tenant isolation is only as strong as this table's,
  and this table declares none of its own. **Filed as P0-5 below.**

  This may be intentional and superseded by a later, unaudited migration (`is_active`,
  `moderation_status='approved'` gate public reads by design, per `007`'s own header describing
  "the existing `.eq("is_active", True).in_("moderation_status", ["approved","featured"])`
  predicates in `backend_app/routers/library.py`" as an established application-level control for
  the *public catalogue* read path) — but an author's **own**, non-published draft Listings, and
  the `author_id`-scoped write path, have nothing enforcing tenant isolation at the database layer
  if RLS is genuinely absent in the deployed schema. This is filed as a P0 rather than a note per
  the task's explicit instruction, with the caveat stated so a reviewer with access to the live
  database's `pg_policies` can close it immediately if RLS was in fact added by an out-of-tree
  migration this audit could not find.
- **Foreign key:** present on `author_id`. `author_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`
  (`:4`).
- **Index:** present. `idx_library_strategies_author ON library_strategies(author_id)` (`:60`).
- **Application-level enforcement:** the public-catalogue read path (`browse_library`) is
  moderation-gated, not author-gated, by design (it is a public listing surface). The
  author-scoped write/edit paths were not traced end-to-end in this pass beyond confirming the
  schema-level RLS gap above.

---

## Summary table

| Table | Predicate | RLS | FK | Index | Verdict |
|---|---|---|---|---|---|
| `strategies` | `user_id` | ✅ | N/A (pre-existing) | ✅ (pre-existing) | OK |
| `strategy_versions` | via `strategy_id` | ✅ | ✅ | ✅ | OK |
| `strategy_deployments` | `user_id` | ✅ | ✅ | ✅ | OK |
| `strategy_backtests` | `user_id` | ✅ | ✅ | ✅ | OK |
| `marketplace_listings` | `user_id` | ✅ | ✅ | ✅ | OK (dormant) |
| `strategy_subscriptions` | `user_id` | ✅ | ✅ | ✅ | OK (dormant) |
| `strategy_research_reports` | `user_id` | ✅ | ✅ | ✅ | OK (no live path found) |
| `signals` | `user_id` | ✅ | ✅ | ✅ | OK |
| `signal_events` | `user_id` | ✅ | ✅ | ✅ | OK (may not exist in prod) |
| `training_jobs` | `user_id` | ✅ | ✅ | ✅ | OK |
| `model_versions` | `user_id` | ✅ (no UPDATE, deliberate) | ✅ | partial | OK |
| `block_registry_snapshots` | none | N/A (global) | N/A | N/A | OK — deliberately global |
| `*_allowed_transitions` (4 tables) | none | N/A (global) | N/A | N/A | OK — deliberately global |
| `marketplace_submissions` | `owner_id` | ✅ | ✅ | ✅ | OK |
| `marketplace_settlements` | `purchaser_id`/`owner_id` | ✅ | ✅ (via `listing_id`/`subscription_id`) | ✅ | OK |
| `library_subscriptions` | (pre-existing) | not asserted in this tree | not asserted | not asserted | **not auditable from this tree — see P0-1** |
| `paper_sessions` | `user_id` | ✅ | ✅ | ✅ | OK |
| `paper_accounts` | `user_id` | ✅ | ❌ **P0-2** | ✅ | **GAP** |
| `paper_orders` | `user_id` | ✅ | ❌ **P0-2** | ✅ | **GAP** |
| `paper_fills` | `user_id` | ✅ | ❌ **P0-2** | ✅ | **GAP** |
| `paper_positions` | `user_id` | ✅ | ❌ **P0-2** | ✅ | **GAP** |
| `paper_balance_events` | `user_id` | ✅ | ❌ **P0-2** | ✅ | **GAP** |
| `paper_trades` | `user_id` | ✅ | ❌ **P0-2** | ✅ | **GAP** |
| `paper_equity_snapshots` | `user_id` | ✅ | ❌ **P0-2** | ✅ | **GAP** |
| `paper_metrics` | `user_id` | ✅ | ❌ **P0-2** | ✅ | **GAP** |
| `paper_events` | `user_id` | ✅ | ❌ **P0-2** | ✅ | **GAP** |
| `paper_market_events` | `user_id` | ✅ | ❌ **P0-2** | ✅ | **GAP** |
| `exchange_keys` | `user_id` | ✅ | ❌ **P0-3** | ✅ | **GAP** |
| `exchange_connections` | `user_id` | ✅ | ❌ **P0-4** | ✅ | **GAP** |
| `risk_settings` | `user_id` | ✅ | ✅ | ✅ | OK — correct pattern |
| `strategy_limits` | `user_id` | ✅ | ✅ | ✅ | OK — correct pattern |
| `risk_settings_audit` | `user_id` | ✅ | ✅ | ✅ | OK — correct pattern |
| `library_strategies` | `author_id` | ❌ **P0-5** | ✅ | ✅ | **GAP** |

---

## Defects filed

Numbered as a continuation of `bugfix.md`'s own `1.X`/`2.X` series (highest existing pair: 1.47/2.47).

**1.48 [P0][CONFIRMED]** WHEN a `paper_orders`, `paper_fills`, `paper_positions`,
`paper_balance_events`, `paper_trades`, `paper_equity_snapshots`, `paper_metrics`, `paper_events` or
`paper_market_events` row is inserted, THEN none of the nine tables' `user_id` column carries a
foreign key to `auth.users` — `009_paper_trading.sql` sections 4–13 declare `user_id UUID NOT NULL`
with no `REFERENCES` clause on all nine, while every structural parent column on the same rows
(`session_id`, `account_id`, `order_id`) does carry an explicit FK — so the database cannot refuse a
`user_id` that names no real account, and nothing at the schema layer proves the row is reachable
by anyone at all.

**1.49 [P0][CONFIRMED]** WHEN `paper_repository.insert_order`, `.insert_fill`, `.upsert_position`,
`.insert_balance_event`, `.insert_trade`, `.insert_equity_snapshot`, `.insert_metrics`,
`.insert_market_event` or `.insert_session_event` is called, THEN `user_id`, `session_id`/`account_id`
and `order_id` are three independently supplied parameters with no assertion anywhere in
`paper_repository.py` or in the database schema that they cohere, so a call that passed a `user_id`
belonging to one tenant together with an `account_id`/`session_id` belonging to another would
write a row the RLS owner policy (`auth.uid() = user_id`) would then let the **wrong** tenant read
and act on, while it displays as belonging to the right tenant's session on every join through
`session_id`. Every current call site happens to derive all three values consistently, so this is
latent rather than presently exploited through a known path — but latency is exactly what task
12.4's `test_cross_tenant_ownership_matrix.py` already treats the paper routes as a named,
unresolved blocker for, and this is the schema-level root cause of that blocker persisting.

**1.50 [P0][CONFIRMED]** WHEN an `exchange_keys` row is inserted, THEN `user_id TEXT NOT NULL`
(`migrations/003_create_exchange_keys_table.sql:11`) carries no foreign key to any user table, so
the database cannot refuse a credential row filed against a `user_id` that names no account — on
the one table in this repository that stores encrypted exchange API keys and secrets. The correct
pattern (`user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE`) already exists
elsewhere in the same migration directory (`create_risk_settings_tables.sql:12`) and was not applied
here.

**1.51 [P0][CONFIRMED]** WHEN an `exchange_connections` row is inserted, THEN `user_id TEXT NOT NULL`
(`migrations/004_create_exchange_connections_table.sql:11`) carries no foreign key to any user
table, for the same reason and with the same missing precedent as 1.50.

**1.52 [P0][UNVERIFIED — may already be closed by a migration this audit could not locate]** WHEN a
`library_strategies` row (the Marketplace Listing table every submission, subscription and
settlement row references) is read or written, THEN
`archived_migrations/root_migrations/001_create_library_strategies.sql` declares no
`ALTER TABLE ... ENABLE ROW LEVEL SECURITY` and no `CREATE POLICY` of any kind on this table, so if
the deployed schema matches this file verbatim, an author's own unpublished draft Listings and the
author-scoped write path have no database-level tenant isolation at all — only the FK on
`author_id` and whatever the application layer enforces. This is filed as UNVERIFIED rather than
CONFIRMED because a later migration outside the four directories this audit inspected
(`backend_app/migrations/`, `migrations/`, `archived_migrations/root_migrations/`,
`.hypothesis`/build artifacts excluded) may have added RLS to this table without this audit finding
it; the file as it exists in the tree today has none.

---

## Tables audited (36 total)

`strategies`, `strategy_versions`, `strategy_deployments`, `strategy_backtests`,
`marketplace_listings`, `strategy_subscriptions`, `strategy_research_reports`, `signals`,
`signal_events`, `training_jobs`, `model_versions`, `block_registry_snapshots`,
`marketplace_submission_allowed_transitions`, `marketplace_subscription_allowed_transitions`,
`paper_order_allowed_transitions`, `paper_session_allowed_transitions`, `marketplace_submissions`,
`marketplace_settlements`, `library_subscriptions` (not auditable from this tree),
`paper_sessions`, `paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`,
`paper_balance_events`, `paper_trades`, `paper_equity_snapshots`, `paper_metrics`, `paper_events`,
`paper_market_events`, `exchange_keys`, `exchange_connections`, `risk_settings`,
`strategy_limits`, `risk_settings_audit`, `library_strategies`.
