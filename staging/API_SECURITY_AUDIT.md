# API SECURITY AUDIT

**Scope:** IDOR/BOLA, SQL injection, XSS, SSRF, path traversal, command injection, mass assignment, privilege
escalation, rate-limit bypass, malformed/oversized payloads, type confusion, authentication bypass, CORS, error
surface.
**Date:** 2026-10-07
**Method:** static audit. Runtime exercise of the HTTP surface was **not** performed — see §12.

---

## 1. SUMMARY

| Class | Verdict |
|---|---|
| SQL injection | **PASS** |
| Command injection | **PASS** |
| SSRF | **PASS** |
| Path traversal | **PASS** |
| CORS | **PASS** |
| IDOR / BOLA | **PASS** (sampled) |
| XSS | **PASS** (marketplace reviews, with regression guard) |
| **Mass assignment** | **FAIL** — `PUT /api/strategies/{id}` |
| Rate limiting | **AMBER** — fails closed only if `ENV` is set |
| Error surface | **AMBER** — 422 can echo input; catch-all prints raw traceback |
| `/metrics` exposure | **FAIL** — unauthenticated |
| Authentication bypass | **PASS** |
| Runtime/dynamic testing | **BLOCKED — TOOLING** |

---

## 2. FINDING API-01 — MASS ASSIGNMENT ON `PUT /api/strategies/{id}` (P1)

**The highest-severity API finding, and the handler's own source says so.**

`backend_app/routers/strategies.py:1696-1766`. The handler takes a **raw `body` dict** — not a Pydantic model —
and does:

```python
update_payload = dict(body)          # :1710
...
sb.table("strategies").update(update_payload).eq("id", strategy_id).eq("user_id", user["id"])   # :1755-1762
```

The source comment at `:1696-1699` states it directly: the payload *"carries EVERY key the caller sent straight
into the UPDATE — there is no column allowlist."*

Only two field-specific guards exist:

| Guard | Line |
|---|---|
| `status` blocked from `running`/`deployed` | `:1684-1694` |
| `ml_model_path` → `_assert_model_path_storable` | `:1702-1703` |

**Every other column on `strategies` is caller-writable**, including `tenant_id`, `marketplace_listing_id`,
`archived_at`, `backtest_result`, `exchange_id`.

**Scope limit that keeps this P1 rather than P0:** the `.eq("user_id", user["id"])` filter (`:1760`) confines
writes to the caller's **own** row, so this is not cross-tenant. What it permits is a user corrupting ownership
and linkage columns on their own strategy — e.g. writing `archived_at` to bypass the archive workflow, or
setting `marketplace_listing_id` to associate their row with a listing, or writing `tenant_id` (which in a
multi-tenant model is precisely the column that must never be client-supplied).

Note the irony that the two guards present are both the result of prior incidents (`ml_model_path` is P1-06, a
column the live bot hands to `joblib.load`; `status` bypassed the deploy gate). The fix shape is already
demonstrated by those two: an explicit column allowlist in the same place.

### 2.1 Secondary mass-assignment findings (P2)

| Model | Config | Effect |
|---|---|---|
| `BacktestRequest` (`strategies.py:2707-2722`) | `ConfigDict(extra="allow")` | Accepts arbitrary fields **deliberately** — the DAG graph carries node/edge shapes the router does not own and forwards untouched. The scalars that bound compute cost (`initial_capital` ≤ 1e9, `trade_size_pct` ≤ 1.0, `symbols`/`strategies` ≤ 50, `timeframe` ≤ 16) **are** pinned. Documented reasoning; accepted. |
| `routers/risk.py` models — `RiskSettingsUpdateRequest` (`:107`), `RiskValidateRequest` (`:115`), `UserKillSwitchRequest` (`:121`), `StrategyLimitItem` (`:597`), `StrategyLimitsPayload` (`:606`) | **no `model_config`** → Pydantic v2 default `extra="ignore"` | Unknown fields silently **dropped**, not persisted. Lower severity than the strategies gap, but a caller who misspells `max_daily_loss` receives **200 with no limit change** — a silent no-op on a risk control. |

### 2.2 The pattern to copy — PASS

The marketplace and paper routers do this correctly and are worth naming as the in-repo reference:
`library.py:3052`, `paper_trading.py:640`, `strategy_operations.py:707, 1272, 3202, 4479, 5675, 5713, 5751`,
`paper_events.py:298` all use `ConfigDict(extra="forbid")` — **and** hand-roll the 422 ahead of Pydantic
(`_parse_deploy_request`, `_parse_session_start`) so the error names the offending field while **echoing no
supplied value** (`library.py:3043-3046`).

### 2.3 Not audited

`routers/billing.py` and `routers/exchange.py` request models were **not located** (no module-level `BaseModel`
subclasses matched). **Mass-assignment status for those two routers: NOT VERIFIED.** Given API-01, they should be
checked before launch.

---

## 3. SQL INJECTION — PASS

No user-controlled string reaches SQL. Every `text(f"...")` site interpolates only a **table identifier** that
has passed `_validate_table_name()`, with all *values* bound:

```python
tbl = _validate_table_name(table_name)
text(f"SELECT * FROM {tbl} WHERE id = :primary_key AND tenant_id = :tenant_id")
```

`backend/transactional_execution_manager.py:488-497, 513-522, 585-595, 607-617, 629-639`, marked
`# nosec: B608`.

Alembic DDL interpolates table/policy names from module-level literal tuples or from `pg_policies` rows passed
through `_validate_table_name` (`a1b2c3d4e5f6_…:157-158`, `d4e5f6a7b8c9_…:241-242`,
`b2c3d4e5f6a7_…:213-214`); `implement_rls_policies_20260817.py:48, 180, 200` interpolates from a hardcoded list.
None is request-reachable.

Routers use the Supabase query builder, not raw SQL. Corroborated by
`tests/test_sql_injection_prevention.py` (passing in baseline) and `tests/test_bandit_sast_gate_is_green.py`.

---

## 4. COMMAND INJECTION — PASS

No `os.system`. No `shell=True`. The only `subprocess` call is `backend_app/certify_runtime.py:34-37`, a fixed
argument list with no user input. `backend/strategy_builder.py:27-30` uses a `SAFE_OPERATORS` whitelist, with its
own comment noting it *"prevents code injection via eval()"*.

**Hardening note, not a vulnerability:** `ast.literal_eval` on Redis-sourced data at
`backend/execution_guard.py:899, 1264, 1377, 1445`, `distributed_execution/execution_deduplication.py:305`,
`exchange_validation/sandbox_execution_runner.py:168` — legacy Python-literal fallbacks. `literal_eval` is not
arbitrary execution, but these are untyped deserializations of a store that anything with Redis access can write.

---

## 5. SSRF — PASS

One user-supplied URL exists: the marketplace cover image. `library.py:1908-1910` →
`_media.validate_cover_reference(payload.cover_image)` → `backend/marketplace/media.py:328+`.

The validator **parses** the reference rather than using `startswith` — the docstring at `:55-57` records that a
naive `startswith` would have accepted both pinned attacks. Specifically it:

- refuses `@` anywhere in the authority **before** comparison (so credential-bearing refs never reach it);
- requires an exact scheme + origin match against configured prefixes;
- caps length at 500 (`:144`);
- rejects non-ASCII (homograph/IDN hosts) (`:147`);
- rejects dot-dot traversal in literal **and** percent-encoded form.

Refusal is 422 `MARKETPLACE_COVER_REFERENCE_REJECTED` carrying a stable label only — never the allow-list, which
would disclose storage layout (`errors.py:126-131, 275`).

**The decisive property:** nothing server-side ever dereferences the value, which is why a write-time check
suffices.

Other outbound calls are operator-configured, not caller-chosen: `core/country_detection.py:426-432`
(`GEOIP_API_URL` env, client IP in path, `is_private_ip` filtering upstream at `:283-301`, 1.5s timeout) and
`core/fx_service.py:252-255`.

---

## 6. PATH TRAVERSAL — PASS at the API boundary

`strategies.py:125-179` `_assert_model_path_storable` → `resolve_model_artifact_path(ml_model_path,
must_exist=False)`, raising `UnsafeModelPath` → 422 `ML_MODEL_PATH_INVALID`. Enforced on create (`:1211`) and
update (`:1702-1703`). Requires the path to sit inside the server's configured model root
(`MODEL_ARTIFACT_ROOT_ENV`) with an allowed extension.

The docstring (`:128-129`) is explicit that the **authoritative** control is at the *load* boundary, with this as
defence in depth — and gives a good reason for having both: `fleet_manager._boot_and_run` catches a boot failure
and drops the bot, but the deploy handler has already returned 200 and written `strategies.status`, so a refusal
discovered at load time leaves the row reading `running` with nothing running.

Related fix verified present: `library.py:2411-2435` does **not** copy `ml_model_path` when cloning (it used to,
placing the author's filesystem path onto the cloning user's row).

**`resolve_model_artifact_path` itself was not read**, so the containment logic is **unverified at source**.
`tests/test_ml_model_path_containment.py` exists; its concrete assertions were not read.

---

## 7. IDOR / BOLA — PASS (sampled)

Full detail in `staging/AUTH_RLS_AUDIT.md` §8. Summary: the house convention is to filter by the caller's own
id **at the query level** rather than merely checking authentication, and it is followed consistently in the
sampled routers (`strategies.py`, `exchange.py`, `library.py`, `referral.py`).

Marketplace-specific IDOR is structurally closed: no endpoint accepts a `subscription_id`, so there is nothing
to substitute (`staging/MARKETPLACE_SECURITY_AUDIT.md` §8).

**Not exhaustive:** `routers/billing.py` and the orders/positions endpoints were not checked line-by-line.

---

## 8. XSS — PASS (where user content is rendered)

The only user-authored content rendered to other users is the marketplace review. React renders
`{review.review_text}` as text content (`StrategyMarketplace.jsx:1459-1464`) — auto-escaped, no
`dangerouslySetInnerHTML`.

There **is** a `dangerouslySetInnerHTML` elsewhere (`TwoFA.jsx:449`, for a QR-code SVG) — unrelated to user
content.

A regression guard exists rather than just a current-state pass: `StrategyMarketplace.test.jsx:22-43, 193-196,
884-894` feeds `<img src=x onerror=…>` through `review_text`, asserts `querySelector('img') === null`, and
separately fails if `dangerouslySetInnerHTML` is introduced into that file.

Backend stores review text length-bounded (`max_length=500`) and unsanitized, which is correct for a
text-rendered-as-text field — safety depends on the render path staying escaped, which is what the test pins.

`tests/test_csp_tightening.py` and `tests/test_tauri_csp_configuration.py` exist (passing in baseline);
their assertions were not read.

---

## 9. RATE LIMITING — AMBER

`core/rate_limit.py:11-50`. Decision tree, verified by reading:

| Condition | Result |
|---|---|
| dev/test env **and** no `REDIS_URL` | `memory://` (`:23-25`) |
| non-dev env, no `REDIS_URL` | **`RuntimeError` — process will not start** (`:26-34`) |
| `REDIS_URL` set | `redis.from_url(..., socket_connect_timeout=1, socket_timeout=1)` + `ping()` (`:37-41`) |
| ping fails, dev/test | warn, fall back to memory (`:44-45`) |
| ping fails, non-dev | **`RuntimeError` — process will not start** (`:47-50`) |

Wired at `main.py:591-608`: `app.state.limiter`, then a replacement `RateLimitExceeded` handler
(`marketplace_rate_limit_handler`) so the 429 body carries a stable code and **no figures** — not the limit, not
the remaining allowance, not the window (slowapi's default printed the limit back at the caller), then
`SlowAPIMiddleware`.

### 9.1 FINDING API-03 — fail-closed depends on `ENV` being set (P2)

```python
env_raw = os.getenv("ENV") or get_vyomquant_mode(default="safe")       # :13
DEV_TEST_ENVS = {"testing","test","development","dev","local","safe","paper","ambiguous"}   # :16
```

`DEV_TEST_ENVS` includes **`"safe"`, `"paper"` and `"ambiguous"`**. If `ENV` is unset and
`VYOMQUANT_MODE=paper` — which is the documented ECS value and the value the live-trading gate verification
showed in use — `env` resolves to `"paper"`, lands in `DEV_TEST_ENVS`, and the process uses **in-memory** rate
limiting across N pods.

This is the **same env-vocabulary confusion** that caused the Redis mock defect (see
`staging/FAILURE_INJECTION_AUDIT.md` §3): a comparison spanning two different environment variables'
value spaces.

**`ENV=production` in the deployed task definition is therefore load-bearing for rate limiting, and was not
verifiable.** Classified AMBER / needs runtime confirmation, not FAIL — the code fails closed correctly *when
`ENV` is set*.

### 9.2 Five parallel rate-limit implementations

`core/rate_limit.py` (slowapi — confirmed installed), `core/distributed_rate_limiter.py`,
`core/rate_limit_middleware.py` + `core/rate_limiter.py`, `core/exchange_rate_limiter.py`, plus tenant quotas in
`core/tenant_middleware.py:223-227` and `core/hard_quota_enforcer.py:330-331`.

Only the slowapi one appears in `main.py`'s middleware stack. **`rate_limit_middleware` is not installed.**
Which limits are actually live is not readable from the code — another instance of the
"implemented but not wired" pattern (`staging/FINAL_FULL_SYSTEM_AUDIT.md` §7).

---

## 10. ERROR SURFACE — AMBER

Three global handlers, all reading `request.scope["path"]` rather than `request.url.path`. That choice is a
deliberate mitigation documented at `main.py:773-787`: the pinned starlette 0.41.0 reconstructs `request.url` by
joining the `Host` header to the path and re-parsing, so a `Host` of `app.vyomquant.in/health` makes
`request.url.path` return `/health/<real path>`. Consequences that were live: the Prometheus endpoint label
became attacker-controlled (unbounded label cardinality from one client) and error bodies reflected the poisoned
value back. `TrustedHostMiddleware` was considered and rejected because the ALB health-checks by IP.
`tests/test_badhost_path_poisoning_regression.py` covers it.

| Handler | Line | Assessment |
|---|---|---|
| `HTTPException` | `:788-811` | **PASS.** Canonical `create_api_error_response`; forwards `exc.headers` (previously discarded, which silently dropped `Retry-After` on 503 and `WWW-Authenticate` on 401) |
| `RequestValidationError` | `:813-827` | **AMBER.** `detail_or_msg=exc.errors()` preserved verbatim. Pydantic v2 error dicts include an `input` field for several error types, so **submitted values can echo back**. This is exactly what the marketplace path hand-rolls its own 422 to avoid (`library.py:3043-3046`); the global handler has no such care. |
| catch-all `Exception` | `:829-843` | **Body is PASS** — always `"Internal server error"`, no traceback to the client. **But** `:833-836` does `print(f"TRACEBACK: {traceback.format_exc()}")` **and** `traceback.print_exc()` to stdout, bypassing the logging pipeline entirely and therefore the redaction filter. See `staging/FAILURE_INJECTION_AUDIT.md` §6. |

Marketplace structured handlers (`main.py:606-607`) resolve before the catch-all via `__mro__`; body is
`{"error": {code, message, details}, "request_id"}` with no stack trace, DB error string, query, internal path or
foreign identifier.

---

## 11. CORS — PASS

`main.py:657-675`. Origins from `CORS_ORIGINS` (comma-split, stripped), defaulting to a **localhost list**, not
`*`.

The important guard at `:667-668`: if `*` is present **or the list is empty**, `allow_credentials` is forced to
`False`. So wildcard-with-credentials is **structurally impossible**, which is the configuration that actually
matters. `allow_methods=["*"]` / `allow_headers=["*"]` are broad but inert without a wildcard origin.

Two non-security notes: no validation that configured origins are `https`; and a production deploy that forgets
`CORS_ORIGINS` silently gets localhost-only CORS — a frontend outage, not a hole, but it fails silently.

---

## 12. FINDING OBS-03 — `/metrics` IS UNAUTHENTICATED (P2)

`routers/metrics.py:35-58` serves `GET /metrics`, mounted with **no prefix and no dependencies** at
`main.py:711`. `routers/metrics.py:4` imports only `APIRouter, HTTPException, Response` — **no `Depends`**.

It merges `prometheus_client.generate_latest()` with `backend/metrics.py`'s
`metrics_collector.get_prometheus_metrics()` into one exposition (`:31-32`) — deliberately, since *"a second
registry with a second endpoint is what an operator would have to remember to scrape, and would not"*
(`:20-21`). It returns 500 on failure rather than fabricating a healthy payload (`:40-42`, `:53-58`) — correct.

If the ALB exposes `/metrics`, route names, tenant-scoped counters and error rates are public. **Whether the ALB
exposes it was not verifiable** — the finding is the missing auth, which is unambiguous.

---

## 13. WHAT WAS NOT TESTED DYNAMICALLY

```
RUNTIME API EXERCISE: BLOCKED — TOOLING
```

No live HTTP request was issued against a running application instance during this phase. The PowerShell
execution channel became unreliable (returning stale, out-of-order output and timing out on trivial commands;
see `staging/FINAL_FULL_SYSTEM_AUDIT.md` §3), and bringing up the full app plus dependencies to drive it was not
achievable under that constraint.

Consequently the following are **static-only** and are **not** claimed as behaviourally verified:

| Class | Status |
|---|---|
| IDOR probed with a real second-tenant JWT over HTTP | **BLOCKED — TOOLING** (but DB-layer isolation *was* verified behaviourally — 5/5 ISOLATED, 0 LEAK) |
| Malformed payloads / type confusion / oversized bodies | **BLOCKED — TOOLING** |
| Rate-limit bypass attempts | **BLOCKED — TOOLING** |
| Authentication bypass attempts over the wire | **BLOCKED — TOOLING** (static review PASS; `validate_security.py` contains forged-token tests but was not run this phase) |
| 422 echo-back of submitted input | **BLOCKED — TOOLING** (identified by reading `exc.errors()` usage) |

What *is* behaviourally verified, from the existing green baseline: `tests/test_api_error_handling_safety.py`,
`tests/test_error_schema.py`, `tests/test_cors_configuration.py`, `tests/test_sql_injection_prevention.py`,
`tests/test_cover_reference_ssrf.py`, `tests/test_ml_model_path_containment.py`,
`tests/test_rate_limiter_fails_closed_in_production.py` all passed as part of the suite. **Their concrete
assertions were not read**, so they are recorded as "suite-green" rather than as independent confirmation of the
specific properties above.

---

## 14. API SECURITY VERDICT

```
API SECURITY: AMBER  (static) / BLOCKED — TOOLING (dynamic)
```

The injection classes — SQL, command, SSRF, path traversal — are genuinely well handled, and in each case the
defence is structural (parameterization, parse-not-prefix, containment root) rather than a blocklist. CORS is
correct in the way that matters. XSS has a regression guard.

Not launch-ready:

| # | Item | Severity |
|---|---|---|
| 1 | `PUT /api/strategies/{id}` — no column allowlist; raw dict into UPDATE | P1 |
| 2 | `/metrics` unauthenticated | P2 |
| 3 | `RequestValidationError` handler can echo submitted input | P2 |
| 4 | Rate-limit fail-closed depends on `ENV` being set; `paper` falls into `DEV_TEST_ENVS` | P2 |
| 5 | `risk.py` models default to `extra="ignore"` → silent no-op on misspelled risk fields | P2 |
| 6 | `billing.py` / `exchange.py` request models not audited for mass assignment | unknown |
| 7 | No dynamic API testing performed | BLOCKED |

**Minimum to reach GO:** fix (1) with an explicit allowlist; add auth or network restriction to (2); stop
preserving `exc.errors()` verbatim in (3); set `ENV=production` explicitly and/or remove `paper`/`safe` from
`DEV_TEST_ENVS` for (4); add `extra="forbid"` to the risk models for (5); audit (6); and run a dynamic pass for
(7) once the execution environment is reliable.
