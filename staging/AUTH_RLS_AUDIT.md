# AUTH / RLS AUDIT

**Scope:** JWT, sessions, WebSocket auth, MFA, password reset, email verification, privilege escalation, role
enforcement, tenant ownership, admin endpoints, service-role paths — plus Row Level Security policies, grants,
`FORCE RLS`, `SECURITY DEFINER` functions and cross-tenant join risk.
**Date:** 2026-10-07
**Method:** static reading of code and migration SQL, plus behavioural isolation probes against real PostgreSQL
16.2. No production access.

---

## 1. SUMMARY

Authentication is well built. Authorization is well built *where it is applied*. The recurring problem is not
weak controls but **controls that are implemented and then wired to nothing**, and a database layer whose
policies are mostly correct but have two confirmed holes and one large unknown.

| Area | Verdict |
|---|---|
| JWT verification | **PASS** |
| Session model (stateless) | **PASS** |
| WebSocket auth — primary path | **PASS** |
| WebSocket auth — two legacy sockets | **FAIL** (JWT in query string) |
| MFA / AAL2 | **FAIL** — defined, enforced nowhere |
| Password reset | **NOT IMPLEMENTED (backend)** — unauditable here |
| Email verification gate | **AMBER** — no backend backstop |
| Role enforcement / privilege escalation | **PASS** |
| Admin endpoint coverage | **PASS** |
| Tenant ownership (sampled routers) | **PASS** |
| Service-role usage | **PASS** |
| Invalid / expired / tampered token handling | **PASS** |
| RLS — marketplace tables | **PASS** except BUG-057 |
| RLS — `library_strategies` UPDATE | **FAIL** (BUG-057) |
| RLS — three UPDATE policies without explicit `WITH CHECK` | **AMBER** |
| `FORCE ROW LEVEL SECURITY` | **AMBER** — absent everywhere, deliberately |
| `SECURITY DEFINER` functions | **FAIL (design) / NOT CLASSIFIED (ACL)** |
| Raw-SQL tenant scoping | **PASS** (but RLS never consulted — see §12) |
| Grants (`authenticated`, `anon`) | **PASS** except `library_strategies` |
| Which migration tree is live in production | **NOT CLASSIFIED** |

---

## 2. JWT — PASS

**Primary verifier:** `core/auth_middleware.py::decode_token_local()` (`:140-192`), used by `verify_token()`
(`:218-253`) and by the actually-wired `core/dependencies.py::get_current_user` (`:300-363`, calling
`decode_token_local` at ~`:310`).

- **Algorithm is fixed server-side.** `_get_jwks_client()` (`:52-69`) builds a `PyJWKClient` from
  `SUPABASE_URL + /auth/v1/.well-known/jwks.json`; `jwt.decode(..., algorithms=["ES256"], audience="authenticated",
  options={"verify_exp": True})` (`:155-162`). Because `algorithms` is hard-coded, **the token's own `alg` header
  can never select the verification method** — an explicit CWE-347 algorithm-confusion fix, documented at
  `:141-144`.
- **The HS256 fallback fails closed.** Reached only in the `except` branch (`:169-191`), gated by
  `_test_token_fallback_allowed()` (`:88-141`), which reads **only** raw `os.environ["ENV"]`/`["ENVIRONMENT"]` —
  deliberately bypassing `settings.ENV`, whose default of `"development"` previously made the fallback live in
  production whenever `ENV` was unset. The allow-list is now `{"testing", "test"}` only; an unset or blank value
  returns `False`. Within the fallback the token must additionally carry `iss == "algo22-test"` (`:174`) and the
  signature is still verified. `_decode_test_hs256_token()` (`:193-216`) re-checks the same gate at `:207` rather
  than trusting a cached decision, so the two HS256 call sites cannot drift.
- **Secret strength is a boot gate, not a warning.** `config.py:50-66` only logs warnings, but
  `main.py:282` calls `settings.assert_jwt_secret_is_production_grade()` **before any service is initialised or
  any port is bound** (`main.py:243-282`), so a deployment with a weak or short secret never accepts a request.
  The raised message names every problem and no part of the secret.

Regression coverage referenced: `tests/test_test_token_fallback_fails_closed.py`,
`tests/test_no_hs256_fallback_in_production.py` (passed in the 85-test money/auth batch),
`tests/test_jwt_secret_is_production_grade.py`, `tests/test_algorithm_confusion_fix.py`.

---

## 3. SESSIONS AND WEBSOCKETS

Auth is **fully stateless JWT**. No server-side session store for request auth; `get_current_user` derives all
state from the token plus a cached profile freeze check (`dependencies.py:345-364`).

### 3.1 Primary WebSocket path — PASS

A **ticket** system, not a raw token:

- `POST /api/auth/ws-ticket` (`routers/auth.py:288-343`), authenticated, rate-limited `30/minute`, issues
  `secrets.token_urlsafe(32)` stored in Redis via `setex` (`:317`). **If the Redis write is not acknowledged it
  raises 503** rather than issuing an unredeemable ticket (`:319-336`) — a deliberate fail-closed replacement for
  a prior silent fallback.
- `api_ws/ws_routes.py:364-382` documents that the `token=` query-string arm **was removed** (task 13.25) because
  a JWT in a URL lands in access logs and browser history. `_resolve_ws_credential` (~`:419`) has *"exactly one
  arm"*: `ticket` → `verify_ws_ticket(ticket)` (`:423`). `:449-450` states "No route takes a `token` query
  parameter any more."

The reasoning recorded there is worth preserving: a ticket that appears in a log is single-use and already
spent, whereas redaction of a URL-borne JWT depends on a filter staying correct forever.

### 3.2 FINDING AUTH-02 — the removal is incomplete (P2)

That claim is **not true of the whole codebase**. Two other WebSocket endpoints still accept a bearer-equivalent
JWT as a URL query parameter:

| Location | Code |
|---|---|
| `routers/dag_tasks.py:302` | `websocket.query_params.get("token") or websocket.headers.get("x-auth-token")`, decoded at `:308-310` |
| `backend/dag_event_loop.py:2123` | `token = websocket.query_params.get("token")`, decoded at `:2131` |

Both fail closed on missing/invalid tokens (4001/4003 close codes) and both check tenant ownership of the
resource (`dag_tasks.py:344`, `dag_event_loop.py:2156`), so this is **not** an authorization bypass. The defect
is the credential-in-URL exposure itself — the exact pattern documented as removed elsewhere.

---

## 4. FINDING AUTH-01 — MFA IS DEFINED AND ENFORCED NOWHERE (P1)

`core/dependencies.py:456-489`:

```python
def require_aal2(user: dict = Depends(get_current_user)):
    aal = user.get("app_metadata", {}).get("aal") or user.get("aal", "aal1")
    if aal != "aal2": raise HTTPException(403, ...)
```

Its own docstring (`:465-470`) states it **MUST** be applied to: exchange API key mutation (create/delete), admin
endpoints, password change, and billing/payment mutations.

**Grepped for `Depends(require_aal2)` and for any `require_aal2` usage across `backend_app/routers/`: zero
results.** The only occurrences of the string are its own definition and the comments about it. The code says so
itself at `:355-358`: *"`require_aal2` is still applied to zero endpoints, so nothing reads this value yet."*

Confirmed against the endpoint its own docstring names first: `POST /api/exchanges/keys`
(`routers/exchange.py::store_keys`, ~`:309-320`) depends only on `get_current_user`, `get_vault`,
`get_redis_manager`, `get_request_supabase`. **Creating an exchange API key — the highest-value credential
operation in the product — is not step-up protected.**

Note that the `aal` claim propagation was itself a real, now-fixed bug (`dependencies.py:330-358`):
`get_current_user` previously dropped the top-level `aal` claim, which would have made `require_aal2` reject
*everyone* who had completed TOTP, had it ever been applied. So the infrastructure is correct and tested; it is
simply not attached.

**No bypass mechanism was found** — no code path sets `aal=aal2` without a real Supabase MFA challenge. The
defect is omission, not a defeatable check. Classified **FAIL** because the stated security posture and the
enforced posture differ, and the gap covers credential creation and billing mutation.

---

## 5. PASSWORD RESET — NOT IMPLEMENTED (BACKEND)

Exhaustive search of `routers/auth.py` (which contains exactly `signout`, `google`, `me`, `register`, `login`,
`ws-ticket`, `:54-343`) and codebase-wide regex for `reset_password`, `forgot`, `reset_token`,
`recover.*password` found **zero** matches outside unrelated recovery features (kill-switch recovery, task
recovery, deployment recovery — different domains).

Password reset is handled entirely by the Supabase client SDK on the frontend, bypassing this backend's control
plane. Consequences, stated plainly:

- **Rate limiting:** none at the application layer. This backend's `slowapi` limiter (`5/minute` on `/login` and
  `/register`, `auth.py:151-152`, `:251-252`) cannot apply to a flow that never reaches it.
- **Enumeration, token single-use, token TTL:** cannot be assessed from this codebase.

**This is not a code defect — it is a scope boundary.** It means the backend audit **cannot vouch for
password-reset security at all**. It must be verified directly against the Supabase project auth settings (reset
link TTL, single-use enforcement, enumeration protection, rate limits), which live outside this repository.
Recorded as `NOT CLASSIFIED` for that control, not PASS.

---

## 6. EMAIL VERIFICATION — AMBER

`routers/auth.py::register()` (`:150-247`): a prior `admin.create_user(email_confirm=True)` bypass was removed
(Phase 7B F-01). Registration now uses `supabase.auth.sign_up()` (`:174`), which honours the project's
email-confirmation setting (`:157-160`). If `res.session` is returned, confirmation is disabled project-wide and
`verification_required: False` (`:232-237`); otherwise `verification_required: True` with no access token
(`:240-244`).

**But there is no application-level enforcement.** `get_current_user` (`:300-363`) checks `is_frozen` (`:348`)
and does **not** check `email_confirmed_at` or any equivalent claim. No grep hit for a
`require_email_verified` dependency analogous to `require_aal2`. So whether an unverified account can call
protected trading/marketplace endpoints depends **entirely on Supabase project configuration**, with no backend
backstop.

AMBER rather than FAIL: the registration path is correct and the outcome is likely fine under the intended
Supabase config — but the property is not enforced where it could be, and the audit cannot confirm the config.

---

## 7. ROLE ENFORCEMENT AND PRIVILEGE ESCALATION — PASS

- `get_admin_user` (`dependencies.py:403-427`) reads `user["app_metadata"]["role"]` and requires `admin`,
  `support` or `operator`. It **explicitly excludes** `user_metadata.role` (client-writable via
  `supabase.auth.updateUser()`) and the top-level JWT `role` claim (the Postgres RLS role, not an app role),
  documented at `:408-412`.
- `get_operator_user` (`:434-452`) requires `operator` specifically, for *"high-blast-radius actions:
  set_user_status, global_kill_switch."*
- **Mass-assignment to role: none found.** Grepped for any endpoint writing `app_metadata` with
  client-controlled values. `register()` never writes it. No router calls
  `auth.admin.update_user_by_id(..., {"app_metadata": {"role": …}})` from a user-reachable path. Per
  `:415-417`, `app_metadata` is settable only via the Supabase Dashboard or an admin/service-role client.

### 7.1 Admin endpoint coverage — PASS

Every admin-labelled endpoint found carries an admin/operator dependency; none depends only on
`get_current_user`:

| Router | Endpoints |
|---|---|
| `admin.py` | `/health` (67), `/metrics` (94), user search (111), `set_user_status` → **operator** (143), `global_kill_switch` → **operator** (188), `/reinitialize` (231), `/fleet-status` (248) |
| `dag_tasks.py` | worker stats (259), start (269), stop (287), recovery stats (428), recovery trigger (470), dead-letter (509) |
| `distributed_execution.py` | `/system/status` (219) |
| `library.py` | moderation (2673), pending list (2765), submission list/detail (4460, 5044), approve/reject/publish/suspend/unpublish (5107-5195), subscription reinstate (5192) |
| `support.py` | ticket admin list (820), staff reply (867) |

---

## 8. TENANT OWNERSHIP — PASS (sampled)

The house convention is to filter by the caller's own id **at the query level**, not merely to check
authentication. Sampled `routers/strategies.py`:

| Operation | Filter |
|---|---|
| list | `.eq("user_id", user["id"])` (`:1079`) |
| fetch | `load_owned_strategy(sb, user["id"], strategy_id)` (`:1551`, `:1677`); raw `.eq("id",…).eq("user_id",…)` (`:1367`) |
| rename / update | `.eq("id",…).eq("user_id", user["id"])` (`:1571`, `:1761`) |
| deploy | explicit extra check `if user_id_from_request and str(user["id"]) != str(user_id_from_request): raise 403` (`:1896-1899`), plus owner-scoped query (`:1911`) |
| stop | `.eq("id",…).eq("user_id",…)` (`:2082`) |

`routers/exchange.py` scopes through `user["id"]` into the vault write. `routers/library.py` gates ratings and
clone status on `.eq("library_id", lib_id).eq("user_id", caller_id)` (`:1829-1832`) and checks
`is_verified_clone` server-side (`:2604`) rather than trusting a client flag.

**No IDOR found in the sampled endpoints.** Not exhaustive — `routers/billing.py` and the orders/positions
endpoints were not checked line-by-line. Behavioural backing: `tests/test_cross_tenant_read_sweep_postgres.py`
(6 passed), `tests/test_tenant_isolation_rls_postgres.py` (29 passed),
`tests/test_billing_data_isolation_postgres.py` (27 passed) — all green in `staging/pg_audit.log`.

---

## 9. SERVICE-ROLE PATHS — PASS

Four deliberate usages, each with an application-level control substituting for the RLS it bypasses:

| Site | Substituting control |
|---|---|
| `referral.py:66-82`, `:650-654` | RLS is intentionally "own-rows-only"; the service-role query is scoped to exactly one caller-supplied referral code — the exact-match filter **is** the authorization, by design |
| `library.py:150-159` (`_get_service_client`) | Used only for cross-user DAG reads in the clone flow (module docstring `:23`), gated on the strategy being published |
| `auth.py:196-199` | Post-registration referral-code processing only |
| `billing.py:189-193` | **Webhook** handler — correct, since a webhook has no caller JWT for RLS to run as; paired with HMAC signature verification (`hmac.compare_digest`, `:1954-1958`) |

No instance was found where a service-role client serves a general user-facing read/write without a narrowing
filter or signature check. Not exhaustive — every file was not grepped for `SUPABASE_SERVICE_ROLE_KEY`.

---

## 10. INVALID / EXPIRED / TAMPERED TOKENS — PASS

`core/auth_middleware.py::verify_token()` (`:218-253`) maps:

| Condition | Response |
|---|---|
| `ExpiredSignatureError` | 401 "Token expired" (`:243-244`) |
| `InvalidAudienceError` | 401 "Invalid token audience" (`:245-246`) |
| `InvalidTokenError` (malformed, bad signature) | 401 "Invalid token" (`:247-249`) |
| any other `Exception` | **401** "Authentication failed" (`:250-253`) — still 401, not 500 |

`dependencies.py::get_current_user` adds its own wrapping try/except (`:358-363`) converting anything to 401, so
an unanticipated exception type cannot surface as a 500 or pass silently.

A tampered signature fails inside `jwt.decode` before any claim is read — verification and claim extraction are
sequential, not interleaved. `validate_security.py:250-273`, `:309-313` exercises forged-signature and
tampered-payload tokens against live endpoints.

---

## 11. RLS POLICY AUDIT

### 11.1 `FORCE ROW LEVEL SECURITY` — absent everywhere (AMBER)

Grepped every `*.sql` across all three migration trees for `FORCE ROW LEVEL SECURITY`: **zero matches.** Every
RLS table uses plain `ENABLE`.

This is **deliberate and documented**, not an oversight:
`backend_app/migrations/020_declare_pre_existing_tables.sql:42-45` states *"RLS is ENABLED on all three and
FORCED on none"*, and `migrations/hardening/999_privilege_hardening.sql:118-122` explains why — the backend's own
PostgreSQL connections (the SQLAlchemy pool) connect **as the table owner (`postgres`)**, and `FORCE` would
break those direct-SQL paths.

Confirmed on the live instance: `relrowsecurity = true`, `relforcerowsecurity = false` on all marketplace tables
checked.

The accepted consequence: the table owner and superuser universally bypass RLS. That is the intended behaviour
for service-role/owner connections. The residual question is whether any owner-privileged connection is reachable
by an end user — see §12.

### 11.2 BUG-057 — `library_strategies` UPDATE (FAIL, P1)

```sql
CREATE POLICY "Users can update their own strategies"
  ON public.library_strategies FOR UPDATE TO authenticated
  USING (author_id = auth.uid());
```

`archived_migrations/root_migrations/004_rls_library_strategies.sql:21-27`. No `WITH CHECK`, no column
restriction, and `authenticated` holds a full `INSERT, SELECT, UPDATE, DELETE` grant. `library_strategies`
carries **no transition-guard trigger** (only `trg_library_strategies_updated_at` and `trg_ls_price_mirror`).

Reproduced: a creator sets their own listing to `moderation_status='approved'`, `is_active=true`,
`price_minor=1`, `is_featured=true` via a direct UPDATE. Full detail in
`staging/MARKETPLACE_SECURITY_AUDIT.md` §2.2.

**This is the canonical instance of the broad-grant-plus-weak-policy failure mode**, and the audit specifically
checked whether it repeats elsewhere. It does not (§11.4).

### 11.3 FINDING RLS-02 — three UPDATE policies without explicit `WITH CHECK` (AMBER, P2)

| Table | Location |
|---|---|
| `strategy_deployments` | `backend_app/migrations/001_strategy_architecture.sql:491-494`; `003_signal_trace_restoration.sql:340-343` |
| `signals` | `002_signal_trace.sql:148-151` |
| `strategy_versions` | `001:472-481`; `003:297-316` |

All three are `FOR UPDATE … USING (user_id = auth.uid())` with no `WITH CHECK`.

**Materially milder than BUG-057, and the reason is PostgreSQL semantics:** when `WITH CHECK` is omitted on an
UPDATE policy, PostgreSQL **reuses the `USING` clause as the check for the new row**. So reassigning `user_id` to
another user's UUID is refused — the new row would fail `USING`. These are therefore a **hygiene** gap (the
repository's own convention, followed consistently in `006` and `009`, is to state `WITH CHECK` explicitly), not
an open door.

Classified AMBER. Whether the implicit fallback was relied on deliberately or simply missed is not determinable
from the text — these files do not document the decision the way `008`/`009` do.

### 11.4 Tables confirmed correctly guarded

RLS enabled, policy command-scoped, `USING` + matching `WITH CHECK` where applicable, grants paired narrowly:

`risk_settings`, `notification_settings`, `notifications`, `billing_invoices`, `exchange_connections`,
`referral_wallets`, `referral_profiles`, `invoices`, `subscriptions`, `payment_methods`, `creator_analytics`,
`marketplace_analytics`, `paper_*` tables (`009_paper_trading.sql:2093-2096` documents the matching
USING/WITH CHECK), `model_versions`, `training_jobs`, `training_governance_decisions`,
`marketplace_submission_transitions`, `marketplace_backtest_evidence`, `marketplace_price_evaluations`,
`library_subscription_transitions`.

Append-only tables (`marketplace_submission_transitions`, `marketplace_backtest_evidence`,
`marketplace_price_evaluations`) hold `SELECT` + `INSERT` grants only — there is no UPDATE policy to need a
`WITH CHECK`, which is the cleanest possible form of this control.

`transaction_rollbacks`: RLS enabled with **no policy at all**, and `REVOKE ALL` from both browser roles
(`999_privilege_hardening.sql:169-177`). Unreachable by `anon`/`authenticated` regardless of policy. Noted as
**deny-by-privilege, not deny-by-policy** — functionally fine, but it should be recorded accurately.

### 11.5 Not determinable from static analysis

| Item | Why |
|---|---|
| `strategies` single pre-existing policy | `020_declare_pre_existing_tables.sql` header says "1 policy"; the policy text (lines 120-434) was not read |
| `exchange_keys` policy body | created in `migrations/003_create_exchange_keys_table.sql`, not read. A prior artifact (`PHASE_7A_AUTH_FORENSIC_AUDIT.md:419`) records `auth.uid() = user_id` FOR ALL — **not independently verified here** |
| `copilot_sessions` / `copilot_messages` policy bodies | `018_copilot_and_waitlist_tables.sql` ~`:250-345` not read; only the GRANTs (`:376-391`) and `ENABLE` (`:246-248`) confirmed |

Recorded as **BLOCKED / NOT VERIFIED**, not PASS.

---

## 12. SECURITY DEFINER FUNCTIONS — FAIL (design) / NOT CLASSIFIED (ACL)

Seven found. Three are safe; four are not.

### 12.1 Self-validating — PASS

`fn_has_user_cloned`, `fn_get_user_library_status`, `fn_get_library_recent_ratings`
(`archived_migrations/root_migrations/005_rls_library_ratings.sql:29-81`) — all `SECURITY DEFINER STABLE SQL`,
and all scope internally by `auth.uid()` in their `WHERE` clauses. A caller cannot pass an arbitrary identity.

Caveat: this file is in `archived_migrations/`, and `999_privilege_hardening.sql` §3b independently rewrites
`library_ratings`'s SELECT policy — suggesting the file may be superseded on disk while its functions could
still exist in production unless dropped. **Not resolvable statically.**

### 12.2 FINDING RLS-01 — four functions with no internal caller validation (P1/P2)

| Function | Location | Caller-supplied identity | Internal `auth.uid()` check |
|---|---|---|---|
| `increment_ml_addon(target_user_id UUID)` | `migrations/006_reconcile_production_database.sql:835-844` | `target_user_id` | **none** |
| `create_referral_code_for_user(user_uuid UUID)` | `:847-882` | `user_uuid` | **none** |
| `process_referral_commission(...)` | `:905-944` | `p_referred_user_id`, `p_payment_id` | **none** |
| `reverse_referral_commission(...)` | `:946-977` | `p_payment_id` | **none** |

`increment_ml_addon` is the sharpest: it runs `UPDATE profiles SET ml_addons_purchased = … WHERE id =
target_user_id` with no check that the caller *is* that user. The two referral functions have direct monetary
side effects (crediting/debiting `referral_wallets`).

**The exploitability hinges on `EXECUTE` privilege, and that cannot be determined from the repository.** Grepped
`GRANT EXECUTE` / `REVOKE EXECUTE` across all three trees: **no hits for any of these four functions.**
PostgreSQL's default is `EXECUTE` to `PUBLIC`, which PostgREST/Supabase maps to both `anon` and `authenticated`
unless explicitly revoked. If that default stands, a direct RPC
(`POST /rest/v1/rpc/increment_ml_addon {"target_user_id": "<victim-uuid>"}`) with any valid `authenticated` JWT
bypasses the backend entirely.

The one in-application call site is itself safe — `billing.py:1053` calls it through a service-role client with a
server-derived `user_id` from a verified webhook.

```
SECURITY DEFINER design:  FAIL  (no caller validation, by reading)
EXECUTE ACL state:        NOT CLASSIFIED  (live-catalogue fact)
```

This independently corroborates a P1 already recorded in `PHASE_7A_AUTH_FORENSIC_AUDIT.md:425`. Closing it
requires `information_schema.routine_privileges` or `\df+` against the live database. **The safe remediation is
version-independent and should be applied regardless of what the ACL turns out to be:** add
`REVOKE EXECUTE … FROM PUBLIC, anon, authenticated` + `GRANT EXECUTE … TO service_role`, **and** add an internal
`IF target_user_id <> auth.uid() THEN RAISE` guard for the two that are ever user-scoped.

---

## 13. RAW SQL AND CROSS-TENANT JOINS — PASS, with a structural caveat

Grepped `backend_app/backend/**` and `backend_app/routers/**` for `cursor.execute`, `create_engine`,
`session.execute`, `text(...)`, `psycopg2`.

- **All raw SQL uses SQLAlchemy `text()` with named bound parameters** (`:tenant_id`, `:user_id`, `:task_id`) —
  no string interpolation of values. Every query inspected is explicitly filtered by `tenant_id` or `task_id`:
  `backend/order_watchdog.py:88-112`, `backend/atomic_persistence_coordinator.py`,
  `backend/replay_safe_transaction_guard.py`, `routers/dag_tasks.py:539-552`.
- `order_watchdog.py`'s `_position_lock_sql` (`:88-96`) carries `# nosec: B608` on a **dynamic dialect-specific
  column expression**, not on unescaped input — verified by reading the string construction, which varies only
  the JSON-access operator syntax, never user data.
- `dag_tasks.py:539-552` has no tenant filter but is `Depends(get_admin_user)` and is a global dead-letter
  inspection tool — appropriate for its purpose.

**The structural caveat, and it follows from §11.1:** these connections run **as the table owner**, and no table
has `FORCE ROW LEVEL SECURITY`. So RLS is **never consulted** for any of these queries, regardless of how correct
the `WHERE` clause is. The tenant scoping in raw SQL is therefore the *only* control on those paths — application
correctness, not database enforcement. A missing or wrong filter in any future raw-SQL addition would not be
caught by the database.

That is not a defect today (every query checked is correctly scoped). It is a statement about where the safety
margin is, and it argues for keeping raw-SQL additions rare and reviewed.

---

## 14. GRANTS — PASS except `library_strategies`

The `library_strategies` failure mode (broad grant + weak policy) was specifically checked against every table
on the audit list. **It does not repeat.**

| Table | `authenticated` | `anon` | Paired policy |
|---|---|---|---|
| `invoices`, `subscriptions` | `SELECT` only (`999:108-125` revokes I/U/D) | `REVOKE ALL` | owner-scoped SELECT (`006` PART 11) |
| `payment_methods`, `creator_analytics` | `REVOKE ALL` (`999:134-157`) | `REVOKE ALL` | exists but unusable by browser roles — deliberate |
| `plans`, `plan_prices` | `SELECT` only | `SELECT` only (`999:180-197`) | RLS deliberately not enabled — no owning customer (`:183-187`) |
| marketplace append-only tables | `SELECT` + `INSERT` | — | no UPDATE policy needed |
| **`library_strategies`** | **full I/S/U/D** | — | **UPDATE policy has no `WITH CHECK`** |

### 14.1 `999_privilege_hardening.sql`'s backstops — a genuine strength

Three self-aborting assertions that fail provisioning rather than warn:

- `:230-260` — aborts if any browser role still holds `TRUNCATE`, `REFERENCES` or `TRIGGER`;
- `:200-228` — aborts if any per-customer table is still `anon`-readable with RLS off;
- `:263-295` — aborts if any per-customer table has an unconstrained (`USING (true)`) policy.

Behaviourally corroborated: `staging/probes_audit.log` shows `truncate_probe.py PASS`
("the privileges row level security cannot filter"), `permissive_policy_probe.py PASS` ("RLS ON but policy
predicate is TRUE"), `exposure_check.py PASS` ("per-customer tables with RLS switched OFF"),
`anon_read_sweep.py PASS` for both `anon` and `authenticated`.

**However:** this file's own header (`:1-37`) states it supersedes an earlier failed in-place attempt inside
`006` and was *"verified by execution"* to fix 22 live exposures. Its correctness was established by a live probe,
not by text review. **Whether it was re-run after later schema additions is not derivable from the repository**,
and `backend_app/migrations/` is a separate provisioning pipeline from `scripts/provision_database.py`.

---

## 15. MIGRATION PROVENANCE — NOT CLASSIFIED

Three parallel trees define this schema:

| Tree | Character |
|---|---|
| `backend_app/migrations/` | modern, heavily commented, numbered `001`–`022`, the only tree with provable idempotency/guard discipline |
| `migrations/` | older; includes the large forensic `006_reconcile_production_database.sql` and `hardening/999_privilege_hardening.sql` (applied last by `scripts/provision_database.py` STEP 4) |
| `archived_migrations/root_migrations/` | explicitly superseded — but it is where BUG-056's broken trigger **and** BUG-057's weak policy both originate |

No `migrations/` or `archived_migrations/` file is referenced by the alembic chain. **Which tree was actually
applied to production, and in what order, cannot be determined from static analysis** — and this is not an
inference: `backend_app/migrations/020_declare_pre_existing_tables.sql:1-30` states that the repository "cannot
provision its own database" and that production's schema was partly applied out-of-band.

That both confirmed RLS defects in this audit trace to the `archived_migrations/` tree is the point worth
carrying forward: "archived" describes the file's location, not whether its objects are live in production.

---

## 16. AUTH / RLS VERDICT

```
AUTH:  AMBER   (strong primitives; MFA enforced nowhere; two legacy WS sockets leak JWT in URL)
RLS:   RED     (BUG-057 confirmed; 4 SECURITY DEFINER functions unguarded with unknown ACL)
```

**Blockers:**

| # | Item |
|---|---|
| 1 | BUG-057 — `library_strategies` creator self-approval (confirmed, reproduced) |
| 2 | `require_aal2` applied to zero endpoints, including exchange API key creation |
| 3 | Four `SECURITY DEFINER` functions accept a caller-supplied identity with no internal check; `EXECUTE` ACL unknown |

**Should ship with the above:**

| # | Item |
|---|---|
| 4 | WebSocket JWT in query string at `dag_tasks.py:302`, `dag_event_loop.py:2123` |
| 5 | Add explicit `WITH CHECK` to the three UPDATE policies (hygiene; PG's fallback currently covers it) |
| 6 | Read the three unverified policy bodies (`strategies`, `exchange_keys`, `copilot_*`) |
| 7 | Add an application-level email-verification gate, or document the reliance on Supabase config |
| 8 | Verify Supabase project password-reset settings externally — the backend cannot |
| 9 | Establish and record which migration tree is live in production |


---

# ADDENDUM (REMEDIATION) — AUTHORIZATION STATUS

Historical analysis above is retained.

## Fixed

| ID | Finding | Resolution |
|---|---|---|
| BUG-057 | A listing creator could approve and feature their own listing. The RLS UPDATE policy had `check_expr = NULL`, and no RLS predicate can express "the new row's `moderation_status` must equal the old row's" | **FIXED** — migration `023`: `REVOKE INSERT, UPDATE ON library_strategies FROM authenticated`, then `GRANT` only the 39 creator-editable columns, computed from the catalogue. Moderation and social-proof columns are not granted. 40 tests, including an authorised moderator approving **and** featuring, so the legitimate operation is proven still to work |
| RLS-01 | `increment_ml_addon` held PUBLIC EXECUTE | **FIXED** — revoked. Chosen over an internal `auth.uid()` guard, which would have broken the only legitimate caller (a service-role client from a signature-verified webhook, where `auth.uid()` is NULL) |
| RLS-03 | `anon` held INSERT/UPDATE/DELETE on `library_strategies` | **FIXED** — revoked. Inert in practice (every policy is `TO authenticated`) but removed anyway |
| API-01 | Mass assignment on `PATCH /api/strategies/{id}` | **FIXED** — allowlist + 422. Allowlist not denylist, so a future column is not silently caller-writable |
| AUTH-01 | `require_aal2` existed and was wired to nothing | **FIXED** — applied to `POST /api/exchange/keys` and `DELETE /api/exchange/{id}`. 16 tests exercising the real dependency in both directions |

**PostgreSQL lesson worth keeping:** column privileges are **additive** to table
privileges. A column-level `REVOKE` is a no-op while a table-wide `GRANT` stands. The
first version of migration `023` made exactly that mistake and its own postflight caught
it.

## Open

| ID | Sev | Finding |
|---|---|---|
| **TENANT-01** | **P1** | The entire tenant-isolation dependency family is unwired. `TenantMiddleware` (`core/tenant_middleware.py:29`) is never added to the app, so `request.state.tenant` is never set; `get_tenant` (`:308`) would 401 on every request; `require_admin` (`:321`) and `TenantQuotaDependency` (`:333-347`) appear in **zero** `Depends(...)`. Independently corroborated by `core/tenant_db_context.py:6-10`: nothing sets `app.current_tenant_id`, so the Postgres RLS policies that compare against it have no tenant to compare. **NOT attempted this phase.** The mapping instruction (which mechanism actually guarantees isolation — `auth.uid()`, explicit ownership checks, or RLS) and the cross-tenant negative matrix were not performed |
| AUTH-03 | P2 | `require_aal2` is carried only at `exchange.py:315` and `:608`. Its own docstring names billing-payment mutations as MUST-apply. Not gated: `billing.py:1668` checkout, `:1946` verify-payment, `:2624` payment-methods, `referral.py:688` payouts, `library.py:2826` checkout, `:3468` cancel, `:3560` renew. No password-change endpoint exists (Supabase-side). **NOT attempted this phase** |

**Important caveat on the marketplace isolation claim.** BUG-057's fix does not depend on
tenant context: the RLS policies are `TO authenticated` and use `auth.uid()`, which
Supabase populates from the JWT. TENANT-01 concerns a *separate, unused* tenancy
mechanism (`app.current_tenant_id`). The two should not be conflated — but TENANT-01
remains a P1 and no cross-tenant negative test matrix has been executed.

---
---

# PHASE 6 ADDENDUM — ENVIRONMENT-GATED AUTH AND EXPOSURE SURFACES

Everything above stands. Phase 6 swept every environment switch in `backend_app/` for the
RUNTIME-01 shape — a secondary flag that downgrades a declared production environment. Full
measurement: `staging/ENVIRONMENT_PRECEDENCE_AUDIT.md`. The auth-relevant results:

## There is no auth-bypass environment variable, and that is measured

`staging/probe_env_precedence_inventory.py` extracted **every** environment read under
`backend_app/` from the AST — **119 distinct variables across 265 read sites** — and then
selected the briefed switch families from what was found rather than from a guessed list.

| Family | Present |
|---|---|
| auth bypass (`SKIP_AUTH`, `DISABLE_AUTH`, `AUTH_BYPASS`, `ALLOW_ANON`, `NO_AUTH`, …) | **none exists** |
| rate-limit bypass | **none exists** — `RATE_LIMIT_PER_MINUTE` is a limit, not a bypass |
| local mode (`LOCAL_MODE`, `OFFLINE`, `USE_LOCAL`) | **none exists** |

This is a positive result and it is stated as one. The only environment-gated authentication
path in the codebase is the test-token fallback, below.

## POSITIVE CONTROL — `_test_token_fallback_allowed` is the model for the rest

`core/auth_middleware.py:135` is the one auth decision keyed on the environment, and it is
correct in every respect the rest of the codebase gets wrong somewhere:

```python
raw = os.environ.get("ENV") or os.environ.get("ENVIRONMENT")
if raw is None or not raw.strip():
    # Unset is NOT a test environment. This single line is the production fix.
    return False
return raw.strip().lower() in _TEST_TOKEN_ENVS
```

* It reads the **real** variables, never `settings.ENV`, with the reason stated in its own
  docstring: a default "is indistinguishable from a deliberate setting, and conflating 'nobody
  configured this' with 'this is a development box' is precisely the defect."
* **Unset fails closed.**
* It admits only `testing` and `test` — `development`, `dev` and `local` were removed.
* It is read **per call**, not cached at import, so a gate cannot depend on module import
  order.

What it is guarding against is worth restating: the pre-fix fallback accepted a token signed
with `JWT_SECRET` carrying `iss="algo22-test"` with an **arbitrary `sub`** and an arbitrary
`app_metadata.role`. Since `sub` is the user id every router scopes its queries by, and
`get_admin_user`/`get_operator_user` trust `app_metadata.role`, that was impersonation of any
account plus the global kill switch — total authentication collapse, keyed on an unset
variable.

## ENV-02 (P3) — a service-role client degraded instead of refusing · **FIXED**

`routers/library.py:_get_service_client` and `routers/referral.py:_get_service_client` decided
whether to return a degraded `None` with `os.getenv("DEV_MODE", "false") == "true"` and
**nothing else** — `ENV` never entered the expression. Measured
(`staging/probe_lib01_service_client.py`, output `staging/raw_P7L3.txt`):

```
ENV=production + DEV_MODE=true   ->  library._build_service_client() RETURNED None
```

`_build_service_client`'s docstring says it *"raises 500 if env is not configured"*. The
privilege dimension is why this belongs in the auth record rather than only in the precedence
audit: `_get_service_client` returns a **`service_role`** client, i.e. the handle that
deliberately bypasses RLS. `routers/referral.py`'s own docstring explains the design — the
referral-code lookup runs as `service_role` **because** the alternative was an RLS policy
(`USING (user_id = auth.uid() OR is_active = true)`) that reduced to TRUE and let any logged-in
customer enumerate every referral code and its owner.

So the function that hands out the RLS-bypassing handle was deciding its own failure mode from
a flag that never looked at the environment. The outcome was fail-*closed* in effect — a `None`
dereferenced into `AttributeError` into a 500, so no data was served and no write performed —
but it was fail-closed by accident, and it reported a configuration failure as an application
defect.

**Fixed:** both sites now call `runtime_env.local_fallbacks_are_acceptable()`, so a declared
non-local `ENV` wins and the ignored `DEV_MODE` is logged at CRITICAL. Regression:
`tests/test_environment_precedence_is_centralised.py` (**55 passed**), falsified 6/6
(`staging/falsify_env_precedence_fixes.py`).

**Still open as LIB-01:** the `None` return remains for local environments and callers still
dereference it unchecked. Disposition and owner:
`staging/FULL_REGRESSION_FAILURE_DISPOSITION.md` § 6.

## ENV-05 (P3) — a trading mode decided whether the interactive docs were served · **FIXED**

`main.py`'s `_get_docs_url` / `_get_redoc_url` computed:

```python
is_prod = os.getenv("ENV", get_vyomquant_mode("development")).lower() in ("production", "prod", "live")
```

With `ENV` unset and `VYOMQUANT_MODE=paper` — the value **every** production task definition in
this repository sets — `/docs` and `/redoc` were served. `ENV=staging` served them too, because
`staging` is not in the tuple. Now routed through
`runtime_env.local_state_is_acceptable()`; `ENABLE_DOCS` remains an explicit operator override,
consulted first. Never looser than before in any configuration.

## API-05 (P3) — `/openapi.json` is served in every environment · **OPEN**

`_get_openapi_url()` consults **no** environment at all and returns `"/openapi.json"` unless
`ENABLE_OPENAPI` is set. No task definition in this repository sets it —
`ecs-task-definition-full.json`, `new_task_def.json`, `td15.json`, `td22.json`,
`infra/rollback/taskdef-vyomquant-api-145-pre-domain.json` all checked.

So production hides `/docs` and publishes the schema `/docs` is rendered from: the full
351-endpoint surface with its request bodies and parameters. Not a credential exposure; an
information-disclosure inconsistency that reads as an oversight rather than a decision.

**Reported, not changed.** Unmounting `/openapi.json` can break a generated client, a contract
test or an API-gateway import. One-line fix available once somebody confirms nothing in
production reads it.

## Systemic — unset `ENV` means "development" at 20 production guards

`os.getenv("ENV", "development")` appears at **20** sites, each inside its own private
`_is_production()` — `exchange_executor.py:58`, `credential_vault.py:43`,
`database_pool.py:79`, `distributed_idempotency.py:199`, `hard_quota_enforcer.py:41`,
`risk_manager.py:30`, `safety_monitor.py:24`, `usage_ledger.py:77`,
`subscription_dependencies.py:72`, and others.

Absence therefore means *development* at all twenty, which is the wrong default direction for a
guard whose job is to tighten in production. `runtime_env`, `auth_middleware`,
`config._is_deployed_environment` and `dependencies._is_production_safe` get it right by
defaulting to `None`/`""`.

Not changed in this phase: inverting twenty independent production guards at once exceeds the
risk this audit should take, and the production task definitions all set `ENV=production`
explicitly, so the twenty defaults are not load-bearing today. Recorded as the consolidation
work to do, with `runtime_env` already in place as the destination.

---

# PHASE 7 ADDENDUM — LIB-01 AND API-05 CLOSED

> Everything above is the Phase 6 record, preserved as written. The current state of LIB-01
> and API-05 is this section.

---

## LIB-01 — `library.clone_strategy` answered 500 naming the wrong cause · **FIXED**

### The chain, traced end to end

`request → authentication → authorization → source lookup → publication/verification rules →
clone creation → ownership assignment → transaction commit`. The break was between
*authorization* and *source lookup*, and it was not where the error message pointed.

1. `_get_service_client()` (`library.py:147`) returns **`None`** when the credentials are
   absent *and* `local_fallbacks_are_acceptable()` is true.
2. `_build_service_client()` (`library.py:293`) carried the docstring *"raises 500 if env is
   not configured"*, and that was **false in two independent ways**:
   * it caught only `RuntimeError`, so the `None` from (1) passed straight through it;
   * `create_client` raises `SupabaseException`, not `RuntimeError`, so a construction
     failure in a non-local environment escaped it too.
3. `clone_strategy` acquired `svc` at line 2275 **unguarded** and dereferenced it at 2280.
4. `svc.table(...)` on `None` raises `AttributeError`.
5. The route's broad `except Exception` relabelled it
   `500 "Failed to look up library entry."` — a statement about a row that was not the
   problem. `'NoneType' object has no attribute 'table'` appeared **only in the log**.

15 of 39 call sites in the module already checked the result (`if not svc:`). 24 did not.

### The fix — and the error code is not a new one

**`MarketplaceError(MARKETPLACE_READ_FAILED)` → 503.** This is the code `get_categories`,
in the same module, already raises for exactly this condition (`library.py:880-885`). The
catalogue has no "server not configured" code, and adding one would mean touching
`MARKETPLACE_CODES`, `HTTP_STATUS_FOR_CODE`, `PUBLIC_MESSAGE_FOR_CODE` and
`tests/test_marketplace_error_surface.py`'s invariants — a catalogue change to deliver a
message the catalogue can already express.

503 is also the more truthful status: a deployment whose service-role credentials are absent
has a read that **cannot complete** and that may succeed after reconfiguration. That is a
dependency condition, not a server fault in the 500 sense, and
`ALLOWED_HTTP_STATUS_FOR_CODE` already permits `{500, 503}` for this code.

**A name pair, not a changed function.** `_require_service_client()` was added *alongside*
`_build_service_client()`:

* Making `_build_service_client` itself raise was rejected: it would turn
  `get_featured_strategies`, `get_trending_strategies` and `browse_library` from
  empty-200 into 503. Those are a **deliberate** browse-surface choice — an empty page on a
  developer machine with no service key — and several suites rely on it.
* Guarding all 24 unguarded sites was rejected as a large, risky diff inside an
  error-handling repair.

The pair makes the choice **visible at every call site**: a reader can see which behaviour
was selected. `_build_service_client`'s docstring was rewritten to the honest contract
(*returns the client or `None`*), and its two failure paths now raise rather than leaking.

**Scope: `clone_strategy` and `unpublish_strategy` only.** `unpublish_strategy` was included
because it was the reason `library.unpublish_strategy` sat in
`tests/test_validation_sweep.py`'s `_ENVIRONMENT_GAP_ROUTE_KEYS`; fixing it converted a skip
into a pass, following the precedent the sweep's own comment records for
`billing.create_portal_session`. The other ~22 unguarded sites are **reported, not changed**.
`checkout` (≈ line 2927) is deliberately unguarded and documented, and was left alone.

Four broad `except Exception` handlers on the clone path were narrowed to
`_PostgrestAPIError`, so a programming error is no longer relabelled as a statement about
the data. The "insert returned no row" case keeps its 500 deliberately — that *is* a server
fault.

### LIB-03 (new, found while fixing LIB-01) — `.single()` made two 404 branches unreachable · **FIXED**

`clone_strategy` steps 1 and 6 both read with `.single()`, which asks PostgREST for
`application/vnd.pgrst.object+json`. That content type answers **406 `PGRST116`** for zero
rows, and supabase-py turns a 406 into an `APIError`. So:

* a listing that does not exist **raised** instead of returning an empty result;
* the `if not resp.data: → 404` branch below it was therefore **unreachable for the one case
  it was written for**;
* the caller got a 5xx for a plain "no such listing" — a 500 before this phase, a 503 after
  the error-handling repair. Neither is true.

Step 6 is the reachable-not-theoretical case: a listing can outlive its source strategy,
because `strategies_source_library_id_fkey` is `ON DELETE SET NULL` on the clone side and
nothing cascades the other way.

Replaced with `.limit(1)` plus list indexing, keeping an `isinstance(rows, dict)` tolerance
for a client that still answers a single object. "No such row" is now a 404 about the
caller's request, and only a read that **did not complete** is the 503.

**Two test doubles lagged the client surface and were extended, not loosened.**
`tests/test_entitlement_matrix.py::_Query` and
`tests/property/test_protected_logic_containment.py::_CloneQuery` had no `.limit()`, so the
product change surfaced as `AttributeError: '_Query' object has no attribute 'limit'` across
14 tests. Adding the method the real client has is a fake catching up with reality. The
`CloneClient` double also had to stop discriminating its two `strategies` reads by the
`.single()` flag and start discriminating them **by the filter** — `id` for the source
lookup, `(source_library_id, user_id)` for the existing-clone lookup — which is how the
server tells them apart anyway, and is a more faithful model than the flag ever was.

### The seventeen tests

`tests/test_lib01_clone_strategy_postgres.py` — **17 passed in 14s**, against real
PostgreSQL. The 14 the brief required, plus three controls:

valid clone · source does not exist · source belongs to another user · unpublished source ·
unauthorized clone · malformed source · duplicate destination/name · transaction failure ·
refusal writes nothing · destination belongs to the caller · source remains unchanged ·
cloned strategy cannot inherit moderator/admin fields · cloned strategy cannot inherit
source ownership · concurrent clone requests.

`FORBIDDEN_CLONE_COLUMNS` is a 13-name tuple checked against the created row, so
"cannot inherit moderator/admin fields" is an enumeration rather than a spot check. The
clone payload is exactly 13 keys and `ml_model_path` is deliberately **not** among them
(`tests/test_ml_model_path_containment.py`).

### One injected verdict, named, with a negative control

`_entitlement_resolver.resolve` reads the Subscription as a PostgREST **embedded resource**
(`library_subscriptions!library_subscriptions_library_id_fkey(...)` off `library_strategies`).
The psycopg2 shim used by these tests issues plain `SELECT *` and cannot express an embed,
and `marketplace_subscriptions` is **absent** from the provisioned test schema.

So the entitlement verdict is injected (`Entitlement(entitling=True, reason="SUBSCRIBED")`)
and **everything else is real**: real PostgreSQL rows, real FKs, real CHECK constraints,
real `clone_strategy`. Rejected alternatives: hand-rolling an embed parser (a second place
for the projection to be wrong) and reusing the existing `CloneClient` double (contradicts
the brief's "real PostgreSQL" requirement).

`test_5_an_unentitled_caller_writes_nothing` is the negative control, so the suite can tell
entitling from non-entitling rather than passing because the gate never ran.

### "Rollback" is not claimed

There is no multi-statement transaction over PostgREST. What the refusal tests assert is
that a refusal **issues no statement at all** against `strategies`, `library_strategies` or
`library_ratings` — verified from an independent connection. The word "rollback" is avoided
rather than borrowed, because borrowing it would imply a transaction boundary this
architecture does not have.

### LIB-02 (new, P3) — no unique constraint behind a read-then-write

`clone_strategy` step 5 reads "has this user already cloned this listing?" and step 7
inserts. Measured (`staging/probe_lib01_schema.py`): the **only** unique index on
`strategies` is `strategies_pkey`. There is no constraint on
`strategies(user_id, source_library_id)` in any migration, so two concurrent clones can both
read "no existing clone" and both insert. `clone_count`'s read-modify-write has the same
lost-update shape.

**Reported, not fixed:** the repair is a migration adding the constraint, and adding one is a
schema change to apply deliberately rather than inside an error-handling fix. What this
phase does guarantee, and what the concurrency test asserts, is that the duplicate is **two
rows the caller owns** — not a cross-tenant write and not a corrupted listing. The test names
that outcome instead of asserting a constraint that is absent.

### Correction to an earlier claim

An earlier phase recorded that the step-9 `library_ratings` upsert would violate a NOT NULL
on `rating`. Measured in the live test schema: `library_ratings.rating` is **NULLABLE**, and
`library_ratings_rating_range` is `CHECK ((rating IS NULL) OR (rating >= 1 AND rating <= 5))`.
The earlier claim was wrong.

### Measured, including the regression it caused and closed

| Scope | Result |
|---|---|
| `tests/test_lib01_clone_strategy_postgres.py` | **17 passed** (14 s) |
| `tests/test_validation_sweep.py` alone | **383 passed, 10 skipped** (was 381 passed + 1 failed + 11 skipped) |
| 11 library/marketplace suites, before the `.single()` repair | 1 failed, 1072 passed, 12 skipped |
| 11 library/marketplace suites, after `.single()` with doubles not yet updated | **15 failed**, 1112 passed — 14 of them caused by the repair |
| 11 library/marketplace suites, after extending the doubles | **1 failed, 1126 passed, 11 skipped** |

The single remaining failure is the pre-existing TEST-01 false positive, already in the
Phase 6 baseline of nine.

**One further regression was caught only by running a neighbouring suite.**
`tests/test_environment_precedence_is_centralised.py::test_the_documented_contract_of_build_service_client_now_holds`
asserted `HTTPException` with `status_code == 500` — the contract the *old, false* docstring
promised. It was updated to assert `MarketplaceError(MARKETPLACE_READ_FAILED)` and that the
catalogue maps it to 503. **The property it guards is unchanged**: absence is *raised* rather
than handed back as a dereferenceable `None`. Only the exception type moved, and it moved
onto the module's existing catalogue instead of a bare `HTTPException` (`MarketplaceError`
is deliberately not an `HTTPException` subclass, which is why the old assertion stopped
catching it).

---

## API-05 — `/openapi.json` is no longer published in every environment · **FIXED**

Full reasoning, the evidence that made it safe to change, and the 60-cell environment matrix
are in `staging/ENVIRONMENT_PRECEDENCE_AUDIT.md`'s Phase 7 addendum. In summary:

* `/docs` and `/redoc` are the **interactive consoles**; `/openapi.json` is the
  **machine-readable schema they render from**. Production hid the first two and published
  the third, so the hiding and the publishing withheld and disclosed the same surface.
* Both now follow `runtime_env.local_state_is_acceptable()`, with `ENABLE_DOCS` and
  `ENABLE_OPENAPI` as independent explicit overrides consulted first, in both directions.
* Development and test environments are unchanged — documentation is **not** disabled
  everywhere.
* Nothing in this repository fetches `/openapi.json` over HTTP (the frontend mentions it
  only in comments; no CI workflow references it; `tests/test_route_contract.py` reads
  `app.openapi()` in process). The CloudFront `/openapi.json` cache behaviour and the two
  middleware public-path allowlists are left in place and now forward to a 404 — they
  predate the ENV-05 change and existed because `/docs` used to be served in production.
* `ENABLE_OPENAPI=true` restores the previous behaviour with one variable.

**Measured:** `tests/test_api05_documentation_exposure_matrix.py` plus
`tests/test_environment_precedence_is_centralised.py`,
`tests/test_marketplace_error_surface.py`, `tests/test_jwt_secret_is_production_grade.py`,
`tests/test_env03_*`, `tests/test_env04_*` and `tests/test_exchange_safety_fix.py` together:
**887 passed** (`staging/suite_API5B.txt`).

---

## LIB-04 (new, P2) — the LIB-01 narrowing turned an unreachable database into a 500 · **FIXED**

Found by the **PostgreSQL lane** of the Phase 7 full regression, not by reading the code.

### What the narrowing cost

The LIB-01 repair replaced this module's broad `except Exception` handlers with
`except _PostgrestAPIError`. That direction was right — a broad handler had been relabelling
an `AttributeError` from a `None` client as *"Failed to look up library entry."*, a statement
about a row that was not the problem — and it meant that **any failure which is not a
PostgREST `APIError` escaped the handler entirely** and reached the application's generic
500.

That is not a theoretical gap. `supabase-py` performs the request with `httpx`, so the
commonest real read failure — DNS, TLS, connection reset, read timeout — arrives as
`httpx.ConnectError` / `httpx.ReadTimeout` / `httpx.RemoteProtocolError`, **none of which is
an `APIError`**. The narrowing turned "the database was unreachable" from a retryable 503
into a 500 that blames nothing in particular.

### How it surfaced

Eleven failures across four files in the PostgreSQL lane, all `500 INTERNAL_SERVER_ERROR` on
`POST /api/library/{id}/clone` or `DELETE /api/library/{id}` — including the two
`test_validation_sweep` cells this phase had just turned green. The escaping exception:

```
psycopg2.InterfaceError: connection already closed
```

A dead client, left reachable by the route because
`tests/test_lib01_clone_strategy_postgres.py::run_clone` did a save/restore on a module
global and `test_14` called it from two threads at once. That test-isolation leak is written
up in `staging/REGRESSION_ORDER_ISOLATION.md`; it is a test defect. **What it revealed is
not** — the same escape happens for `httpx.ConnectError` in production.

Every one of those four files passes in isolation and in every grouping that excludes the
fifth. Only a full-suite run **with the PostgreSQL lane enabled** could show it: the CI lane
skips the suites that reach `clone_strategy` with a database configured, so running one lane
and reporting it as the regression would have shipped this.

### The fix

A second handler at every client call on both the clone and unpublish paths, via
`_read_did_not_complete(operation, exc)`:

* logs the exception **type** with `exc_info=True` — so a programming error is fully visible
  to an operator, which the original broad handler was not, since it logged only
  `f"...: {exc}"` with no traceback;
* raises the same `MarketplaceError(MARKETPLACE_READ_FAILED)` → **503**.

The `_PostgrestAPIError` handler stays **first** at every site, because when the database
answers, its answer is the diagnosis; the broad handler is for when nothing answered at all.
Both produce the same status, which is the point: from outside, a read that did not complete
is a 503 regardless of which layer failed, and the exception type belongs in the log rather
than in the status code.

Two sites are deliberately **best-effort and swallow instead**: the `clone_count` increment
and the verified-clone rating marker. The clone row already exists and is owned by the
caller at that point, so failing the request would report a failure for an operation that
succeeded. Both now log with a traceback.

### Measured

`tests/test_lib04_a_broken_client_is_503_not_500.py` — **19 passed**, no database, no
network, no credential. Parametrised over `httpx.ConnectError`, `httpx.ReadTimeout`,
`httpx.RemoteProtocolError`, `psycopg2.InterfaceError`, `RuntimeError`, `AttributeError`,
`ValueError` and `KeyError`, for both routes, **because the property is that the answer does
not depend on the exception type** — which is the whole lesson of the finding.

Three further assertions beyond the parametrised matrix:

* `test_the_public_sentence_names_no_internals` — the injected message carries a hostname,
  a port and a driver name, and none of them may appear in the 503 a customer receives.
* `test_a_postgrest_api_error_still_takes_its_own_handler` — per `try`, via `ast`, the
  narrow handler must precede the broad one. (A first version compared string offsets over
  the whole function and failed on step 3's unrelated audit-logging `except Exception`,
  which sits before any client call — measured as `3586 < 5240`. Fixed by checking handler
  order **within each `try`**.)
* `test_every_client_call_on_the_clone_path_is_guarded` — every `.execute()` inside
  `clone_strategy` must sit in a `try` carrying **both** handlers, because a new unguarded
  call is how this comes back.

Verified in the ordering that produced the original failures:
`test_lib01_clone_strategy_postgres.py` + the four victim files together — **718 passed, 10
skipped, 1 xfailed**.

---

# PHASE 8 — 2026-10-09. ROW SECURITY ADDENDUM

**Substrate: LOCAL PostgreSQL 16.2 (pgserver). No production DSN existed in any phase, so
every statement here is about the local substrate.**

## TENANT-02 — `fee_records` — CLOSED

Full treatment in `TENANT_ISOLATION_MAP.md`'s Phase 8 block. The row-security facts, for
this document's record:

| Property | Before | After (migration 026) |
|---|---|---|
| `relrowsecurity` | `false` | `true` |
| `relforcerowsecurity` | `false` | `false` (deliberate — see below) |
| policies | 0 | 2: `fee_records_owner_access` (SELECT, `authenticated`), `fee_records_service_role` (ALL) |
| `anon` privileges | `DELETE, INSERT, SELECT, UPDATE` | none |
| `authenticated` privileges | `DELETE, INSERT, SELECT, UPDATE` | none |
| `service_role` privileges | all | `SELECT, INSERT, UPDATE` |

`ENABLE` rather than `FORCE` is the established convention in this repository and the
reason is worth restating here because it is counter-intuitive: the application connects as
the table **owner**, and an owner is exempt from its own policies unless `FORCE` is set.
`ENABLE` therefore closes the PostgREST surface — which is where the exposure was — while
leaving the SQLAlchemy API untouched. `FORCE` would have broken all six `FeeEngine`
methods, and it would have broken them in production as "fee recording is down", not as a
test failure.

### Why the owner policy had to be exercised rather than counted

026 revokes every privilege from both browser roles, because nothing in this repository
reaches `fee_records` other than as the table owner through SQLAlchemy. That is the right
posture and it has a measurement consequence: with no privilege, the policy has nothing to
filter, so "two policies exist with non-trivial predicates" is the strongest claim the
catalogues alone can support.

So the cross-tenant tests `GRANT SELECT` to `authenticated` **inside a transaction**, set
`request.jwt.claim.sub` to tenant A, `SET LOCAL ROLE authenticated`, assert A sees exactly
their own row and B sees exactly theirs, and roll the whole thing back. A separate test
then confirms the temporary grant did not survive. That is the only construction that
proves the predicate isolates tenants rather than merely existing.

### `service_role` holds `BYPASSRLS`

Measured: `rolbypassrls = true`. Row security does not apply to it at all, so
`fee_records_service_role` is defence in depth for a future path rather than the mechanism
that lets the backend work. Recorded explicitly so no reader infers that the policy is
load-bearing.

## The gap this closed in the hardening model

`migrations/hardening/999_privilege_hardening.sql` section 4 already refuses to complete if
any table carries a per-customer column without row security, and `tenant_id` is already in
its column list. It did not catch `fee_records` because it enumerates `pg_class` during
provisioning **step 4**, and `fee_records` is created later — at application startup, by
`Base.metadata.create_all()`.

**A table created by `create_all()` is invisible to every schema-time control in this
repository.** `fee_records` was the instance found; the class of problem is not closed by
026. A test asserting that no ORM model exists without a corresponding migration-declared
table would close it, and is not written.

## Residual, carried forward

`FEE-01` (P2, OPEN). `FeeEngine`'s tenant-resolution helpers fall back to `UUID(int=0)`,
so an unattributable fee row is owned by a tenant that does not exist. Post-026 it is
visible only to `service_role` — fail-closed for isolation, still wrong for accounting.
See `TENANT_ISOLATION_MAP.md`.


# PHASE 9 — 2026-10-10. NEW RLS SURFACES FROM THE SAFETY/MONEY MIGRATIONS

Phase 9 did not re-open any AUTH or RLS finding in this document; its scope was operational
safety and financial integrity. It did add **two new row-level-security surfaces**, both via
migrations applied to the local substrate only, and both with append-only postflights, so they
are recorded here for completeness.

* **`safety_control_audit`** (migration `028_safety_control_audit.sql`) — the durable
  kill-switch transition log introduced for KILL-02. Append-only: its postflight **first
  `REVOKE ALL FROM service_role`** and then grants only `SELECT, INSERT`, because the
  substrate's `ALTER DEFAULT PRIVILEGES` grants `service_role` ALL on new `public` tables, so a
  bare grant would have left UPDATE/DELETE in place. This was a real gap the postflight caught,
  not a theoretical one. 89 public tables now (was 87).
* **`referral_obligations`** (migration `029_referral_obligations.sql`) — the durable
  owed-commission ledger introduced for MONEY-06, with the same append-only RLS discipline.

Both postflights strip SQL comments before their substring self-checks, to avoid the kind of
comment-versus-code false positive this engagement repeatedly had to correct elsewhere.

**TENANT-02 note.** The `fee_records` RLS finding (TENANT-02) was closed in Phase 8 and is
unchanged here. One operational discrepancy is recorded rather than silently carried: TENANT-02's
own suite resolves its DSN via a fallback to `staging/pg_dsn.txt`, whereas every **new** Phase 9
PostgreSQL suite gates strictly on `AERORA_TEST_DATABASE_URL` naming a postgres database with no
such fallback (enforced by a `test_the_postgres_requirement_is_explicit` in each). The TENANT-02
fallback is left as a documented discrepancy, out of Phase 9's scope.

No live credential, no production DSN, and no auth-path change was involved in Phase 9. The
AUTH/RLS classifications in this document are unchanged.


# PHASE 10 — 2026-10-10. TENANT-01 (coverage), CONN-01 (fixed)

Phase 10 did not change the TENANT-01 architectural decision. It added executed coverage for
one of its residuals and fixed an adjacent connection-lifecycle defect. Verified by
`staging/verify_phase10_open_findings.py`.

## TENANT-01 — STILL OPEN; one residual now has negative-control coverage

The documented decision (do NOT install TenantMiddleware) stands, and FORCE ROW LEVEL
SECURITY is still set on ZERO tables (grepped across `backend_app/migrations/*.sql` with
comments stripped — zero matches), so the SQLAlchemy owner path still bypasses RLS. Those
residuals are deliberate and unchanged, so TENANT-01 **remains OPEN**.

What Phase 10 added:
* The credential vault's tenant isolation is an in-code Python comparison (the store is
  Redis, so there is no RLS behind it). It now has an executed negative control:
  `tests/test_tenant01_credential_vault_isolation.py` (4 passed) — a different tenant cannot
  read through the tenant-scoped key (layer 1), and a row whose STORED tenant disagrees with
  the key it is filed under is refused by the re-check (layer 2), with the refusal
  indistinguishable from an absence. A positive control confirms the owning tenant still reads
  its own credential.
* The cross-tenant breadth sweep now covers `referral_obligations` (Phase 9 table 029), which
  had dropped out as NOT PROVEN for want of a seed override; the authenticated attacker reads
  0 of the owner's rows, so RLS is working on it.

## CONN-01 — FIXED (credential rotation evicts the pooled client)

Not a tenant-isolation breach (the pool key still separates users), but recorded here because
it is a credential-lifecycle control. `store_keys` now calls `release_exchange` after the
vault write, so a rotation evicts the stale pooled client and the new credential takes effect
in-process. Evidence: `tests/test_conn01_rotation_evicts_pooled_client.py` (4 passed).

No live credential, no production DSN. AUTH/RLS classifications unchanged.
