# ENVIRONMENT PRECEDENCE AUDIT

Phase 6, section 8. Scope: every environment-variable switch that can change what this
process is **allowed to do**, measured against one invariant.

> **The invariant.** An explicitly declared production environment must never be
> downgraded to local/test semantics by a secondary flag.

RUNTIME-01 (Phase 5) was the first instance: `core/runtime_env.local_state_is_acceptable()`
returned on `DEV_MODE` before it ever read `ENV`, so `ENV=production` plus a leftover
`DEV_MODE=true` selected a per-process rate-limit counter and a mock cache behind the
distributed lock. This audit asks whether that was the only one. It was not.

---

## How the inventory was built

**Discovery, not a guess list.** `staging/probe_env_precedence_inventory.py` parses every
module under `backend_app/` and extracts every environment read — `os.getenv(name[, default])`,
`os.environ.get(name[, default])`, `os.environ[name]` — then selects the switch families
named in the brief *from what was actually found*. A hand-written list of variable names
would only have proved that the names I guessed exist.

For each site it records the file and line, the supplied default, and whether the
**enclosing function also reads `ENV`/`ENVIRONMENT`**. That last column is the whole audit:
a flag that decides behaviour without ever learning which environment it is in cannot
honour a precedence policy, because it never asks.

Measured (`staging/raw_P7Q7.txt`):

| | |
|---|---|
| Distinct environment variables read | **119** |
| Total read sites | **265** |
| Sites in a briefed switch family | **119** |
| Reads of `ENV` / `ENVIRONMENT` | **40** |

### Family coverage — including the families that do not exist

The brief names fourteen families. Reporting the empty ones matters as much as the full
ones, because "no auth-bypass flag was found" is a finding.

| Family | Present | Variables found |
|---|---|---|
| deployment environment | yes | `ENV`, `ENVIRONMENT` |
| dev mode | yes | `DEV_MODE` |
| trading mode | yes | `VYOMQUANT_MODE`, `AERORA_MODE` |
| live-trading enable | yes | `VYOMQUANT_ENABLE_LIVE_TRADING`, `AERORA_ENABLE_LIVE_TRADING`, `ENABLE_LIVE_TRADING` |
| paper / live flag | **no** | paper vs live is a database and parameter concept here, not an environment switch |
| test / sandbox | yes | `SANDBOX_MODE` |
| debug | yes | `DEBUG` |
| local mode | **no** | no `LOCAL_MODE` / `OFFLINE` / `USE_LOCAL` switch exists |
| rate-limit bypass | yes | `RATE_LIMIT_PER_MINUTE` (a limit, not a bypass — there is no bypass) |
| auth bypass | **no** | no `SKIP_AUTH` / `DISABLE_AUTH` / `ALLOW_ANON` switch exists |
| webhook bypass | yes | `ENABLE_WEBHOOK_IP_VALIDATION`, `RAZORPAY_WEBHOOK_SECRET`, `STRIPE_WEBHOOK_SECRET` |
| Redis dependency | yes | `REDIS_URL`, `REDIS_REPLICA_URL`, `REDIS_HOST/PORT/PASSWORD`, `REDIS_RECONNECT_COOLDOWN` |
| database fallback | yes | `DATABASE_URL`, `SUPABASE_URL` |
| worker local mode | yes | `WORKER_NAME`, `API_WORKERS`, `WS_SERVER_WORKERS` (identity and concurrency, not mode) |
| execution router / path flags | yes | `PRODUCTION_ROUTER_ENABLED`, `DAG_TRADING_ENABLED`, `EVENT_LOOP_TRADING_ENABLED` |

**There is no auth-bypass environment variable and no rate-limit-bypass environment
variable in this codebase.** The one auth fallback that exists is keyed on `ENV` directly
and is covered below as a positive control.

---

## Findings

### ENV-02 — `DEV_MODE` downgraded a service-role client in a declared production environment · **FIXED**

`routers/library.py:_get_service_client` and `routers/referral.py:_get_service_client` both
decided whether to degrade with:

```python
if os.getenv("DEV_MODE", "false").lower() == "true":
    return None
raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
```

`ENV` is not in that expression. Measured (`staging/probe_lib01_service_client.py`,
output `staging/raw_P7L3.txt` and `staging/raw_P7M4.txt`):

```
DEV_MODE=true,  no SUPABASE_SERVICE_ROLE_KEY   -> RETURNED None
DEV_MODE=false, no SUPABASE_SERVICE_ROLE_KEY   -> RAISED SupabaseException: Invalid API key
ENV=production + DEV_MODE=true                 -> RETURNED None
library._build_service_client()                -> RETURNED None
  LIB01_BUILD_RETURNS_NONE_UNDER_DEV_MODE=True
  LIB01_DEV_MODE_OVERRIDES_ENV_PRODUCTION=True
```

`_build_service_client`'s own docstring says *"Returns a service-role client; raises 500 if
env is not configured"*. It returned `None`, because it catches only `RuntimeError` and the
`None` never raises. Every caller dereferences immediately, so the `None` becomes
`AttributeError: 'NoneType' object has no attribute 'table'`, caught by the route's broad
`except Exception` and answered as **500 "Failed to look up library entry."** A deployment
with no service-role key therefore presents as an application defect, and the real cause
appears only in a log line.

**Fix.** Both sites now call `runtime_env.local_fallbacks_are_acceptable()` — the same
single rule RUNTIME-01 created, under a name that reads correctly at this kind of call
site, delegating to `local_state_is_acceptable()` so the two cannot drift. A declared
non-local `ENV` wins and the ignored `DEV_MODE` is logged at CRITICAL.

**Not fixed, and deliberately so:** the `None` return still exists for local environments
and the callers still dereference it unchecked. That half is **LIB-01**, owned by the
library router — see `staging/FULL_REGRESSION_FAILURE_DISPOSITION.md`. Changing it here
would alter the behaviour of every `/api/library` route under the test suite's own
environment, which is more than a precedence fix. The pre-existing sweep failure it causes
is **unchanged** by this work, verified: `tests/test_validation_sweep.py` → 1 failed, 381
passed, 11 skipped, same single failure, same message (`staging/raw_P8G7.txt`).

---

### ENV-03 — the application does not load the `.env` file anyone is looking at · **REPORTED**

`backend_app/core/config.py:17` calls `load_dotenv()` with no argument. python-dotenv then
calls `find_dotenv()`, which walks **up from the calling module's own directory** — not from
the repository root and not from the working directory. From `backend_app/core/` the first
`.env` it meets is `backend_app/.env`.

Measured (`staging/probe_dotenv_resolution.py`, output `staging/raw_P7P6.txt`):

```
find_dotenv(usecwd=True)         -> C:\aerora_quant_backend_updated_final1\.env
walk up from backend_app/core/   -> C:\aerora_quant_backend_updated_final1\backend_app\.env
```

Confirmed independently by what lands in the process: after importing
`backend_app.routers.library`, `SUPABASE_KEY` — which exists **only** in the repository-root
`.env` — is still unset, while `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY` — which
exist only in `backend_app/.env` — are both set. The discriminator is decisive.

What `backend_app/.env` injects (names and lengths only; no value is reproduced):

| Variable | Value |
|---|---|
| `SUPABASE_URL` | PLACEHOLDER (len 36) |
| `SUPABASE_ANON_KEY` | PLACEHOLDER (len 22) |
| `SUPABASE_SERVICE_ROLE_KEY` | PLACEHOLDER (len 30) |
| `SUPABASE_JWT_SECRET` | PLACEHOLDER (len 24) |
| `JWT_SECRET` | PLACEHOLDER (len 33) |
| `MASTER_ENCRYPTION_KEYS` | PLACEHOLDER (len 26) |
| `DATABASE_URL` | PLACEHOLDER (len 100) |
| `AERORA_MODE` | len 5 |
| `PRODUCTION_ROUTER_ENABLED` | len 4 |
| `REDIS_URL` | len 22 |

Three consequences.

1. **A placeholder is truthy.** `core/config.py:validate()` and
   `core/supabase_connection.py` both decide "configured" with `if not
   settings.SUPABASE_SERVICE_ROLE_KEY`. `bool('YOUR_SUPABASE_SERVICE_ROLE_KEY')` is `True`,
   so a required-credential check written as a truthiness test is satisfied by exactly the
   value it exists to reject. The failure then surfaces at first use as
   `SupabaseException: Invalid API key` — or, before ENV-02 was fixed, as a silent `None`.

2. **It governs every local and non-containerised CI boot**, from a file that is
   git-ignored and that no runbook mentions. `AERORA_MODE` (the deprecated trading-mode
   alias `get_vyomquant_mode` still reads) and `PRODUCTION_ROUTER_ENABLED` are both
   supplied from it. Observed directly in this audit's own process output
   (`staging/raw_P8B2.txt`): `INFO:Algo22: Production execution router ENABLED (verify
   safety before production)`.

3. **It does not ship.** `.dockerignore` excludes `.env`, `.env.*`, `backend_app/.env` and
   `algo22-terminal/.env` explicitly, so the file is not in the Docker build context, and
   `.gitignore` keeps it out of the repository. The production ECS task definitions supply
   `ENV=production` and `VYOMQUANT_MODE=paper` as container environment variables. **So
   ENV-03 is a local and CI-fidelity finding, not a live production exposure.** It is
   recorded at this length because it means local and CI results are shaped by an invisible
   file, which is a hazard for an audit that measures locally.

> **CORRECTION-006.** Earlier in this engagement I reported fixing "`.env` missing
> `ENV=development`" by editing the **repository-root** `.env`. `core/config.py` does not
> load that file. The root `.env` is the file `find_dotenv(usecwd=True)` resolves, and no
> module in `backend_app/` passes `usecwd=True`. That edit is therefore inert for
> `config.py`, and the claim as written was wrong. The practical effect is in the safe
> direction — with `ENV` unset, the API-03 rate limiter refuses to start rather than
> fail-opening — but the statement needs correcting rather than quietly resting on a
> favourable accident.

---

### ENV-04 — `PRODUCTION_ROUTER_ENABLED` defaults to mounting the execution router · **REPORTED**

```python
# core/feature_flags.py:39   AND   core/safety_config.py:131  (two copies of one line)
PRODUCTION_ROUTER_ENABLED: bool = os.getenv("PRODUCTION_ROUTER_ENABLED", "True").lower() == "true"
```

`main.py:717` mounts `execution_router` at `/api/execution` when it is true. Three things
disagree with each other:

| Source | Says |
|---|---|
| `core/feature_flags.py` docstring | `PRODUCTION_ROUTER_ENABLED: False (unverified safety)` |
| the code on the next line | default **`"True"`** |
| `ecs-task-definition-full.json`, `new_task_def.json`, `td15.json`, `infra/rollback/…` | `"False"` |
| `deployment_go_no_go.md` | `PRODUCTION_ROUTER_ENABLED=True` |

So the flag's **absence mounts the router**, which is the fail-open direction for a switch
whose own documentation calls it unverified; the deployed configuration is safe only because
four task definitions say so; and the go/no-go runbook contradicts all four.

It is also the only switch in the inventory with **two independent definitions of the same
line** in two modules, which can diverge.

**Reported, not changed.** Flipping the default alters which routes are mounted, and that
is a product decision with a blast radius outside an audit's remit. The recommendation is:
default `"False"`, delete one of the two copies, and reconcile `deployment_go_no_go.md`
with the task definitions.

Related and already recorded in Phase 1 — not re-raised here:
`validate_safety_config_integrity()` has **zero call sites** and compares `VYOMQUANT_MODE`
against `"live_trading"`, a value the variable never holds (the vocabulary is
`safe`/`paper`/`live`), so even if wired its production assertion could not fire. See
TRADE-06 in `staging/TRADING_SAFETY_AUDIT.md`. Note the second-order consequence: that dead
function is also the only thing that would have *required* `PRODUCTION_ROUTER_ENABLED=true`
in production, so the contradiction above has never been exercised.

---

### ENV-05 — a trading mode supplied the deployment environment for the docs surface · **FIXED**

```python
# main.py, pre-fix, in both _get_docs_url and _get_redoc_url
is_prod = os.getenv("ENV", get_vyomquant_mode("development")).lower() in ("production", "prod", "live")
```

This is the API-03 defect verbatim, in a module API-03 did not touch. `VYOMQUANT_MODE` is a
*trading* mode — what the system may do with money — and it was being used as the default
for a *deployment* environment. Both directions were wrong:

* `ENV` unset + `VYOMQUANT_MODE=paper` (the value **every** production task definition in
  this repository sets) → `"paper" in ("production","prod","live")` is `False` → `/docs` and
  `/redoc` **served**.
* `ENV` unset + `VYOMQUANT_MODE=live` → `is_prod` true → docs hidden on a machine that had
  declared no environment at all.
* `ENV=staging` → served, because `staging` is not in the tuple.

**Fix.** A new `_interactive_docs_allowed()` delegates to
`runtime_env.local_state_is_acceptable()`. `ENABLE_DOCS` remains an explicit operator
override, consulted first, in both directions — an operator who names the behaviour outranks
an inference. Never looser than before, case by case:

| Configuration | Before | After |
|---|---|---|
| `ENV=development` | served | served |
| `ENV=production` | hidden | hidden |
| `ENV=production` + `DEV_MODE=true` | hidden | hidden |
| `ENV=staging` | **served** | hidden |
| `ENV` unset + `VYOMQUANT_MODE=paper` | **served** | hidden |
| `ENV` unset, no `DEV_MODE` | **served** | hidden |
| `ENV` unset + `DEV_MODE=true` | served | served |

---

### ENV-06 — Sentry is tagged with the trading mode, not the deployment environment · **REPORTED**

```python
# main.py:258-262, inside lifespan
initialize_sentry(
    dsn=os.getenv("SENTRY_DSN"),
    environment=get_vyomquant_mode("production"),   # <-- a trading mode
    release="2.4.0",
)
```

`get_vyomquant_mode` returns `safe`, `paper` or `live`. So every Sentry event from the
production deployment is filed under the environment **`paper`**, because that is what the
task definitions set `VYOMQUANT_MODE` to. The default argument `"production"` is only reached
when both `VYOMQUANT_MODE` and `AERORA_MODE` are unset, which is the one case where the tag
would be right.

Same conflation as ENV-05, different consequence: not an exposure, an observability defect —
production and staging events land in the same bucket whenever both run in paper mode, and
nothing in Sentry distinguishes them.

**Reported, not changed.** Changing the environment tag re-buckets every existing Sentry
issue and alert rule, which is an operations decision, not a code fix.

**Stated explicitly because the regression sweep does not catch it.**
`test_no_site_defaults_the_deployment_environment_from_a_trading_mode` matches
`os.getenv("ENV", <trading mode>)` — an `ENV` read with a trading-mode default. This site
never reads `ENV` at all; it passes the trading mode directly. The sweep being green does not
mean there are no instances of the conflation left, only none of *that shape*, and saying so
is the difference between a guard and a claim.

---

### API-05 — `/openapi.json` is served in every environment · **REPORTED**

`_get_openapi_url()` consults no environment at all:

```python
enable_openapi = os.getenv("ENABLE_OPENAPI")
if enable_openapi is not None:
    return "/openapi.json" if enable_openapi.lower() in ("true", "1", "yes") else None
return "/openapi.json"
```

No task definition in this repository sets `ENABLE_OPENAPI` — checked
`ecs-task-definition-full.json`, `new_task_def.json`, `td15.json`, `td22.json`,
`infra/rollback/taskdef-vyomquant-api-145-pre-domain.json`. So production serves the
schema that the deliberately-hidden `/docs` page is rendered from: the same 351-endpoint
surface, its request bodies and its parameters.

Not a credential exposure and not a precedence violation in the `ENV`-vs-flag sense — it is
the degenerate case of the same family, a surface whose exposure is environment-independent.
Recorded here because no existing artifact contained it (`staging/*.md` grep for
`openapi|/docs|redoc`: no matches before this file).

**Reported, not changed.** Unmounting `/openapi.json` can break a generated client, a
contract test or an API-gateway import. The fix is one line — `ENABLE_OPENAPI` defaulting to
`_interactive_docs_allowed()` — once somebody confirms nothing in production reads it.

---

## Positive controls — the sites that already get this right

An audit that lists only failures invites the conclusion that nothing here works. These
were measured and are correct; two are models for the rest.

| Site | Why it is right |
|---|---|
| `core/auth_middleware.py:135 _test_token_fallback_allowed` | Reads only the real `ENV`/`ENVIRONMENT`, never `settings.ENV`, because "a default is indistinguishable from a deliberate setting". Unset returns `False`. Admits only `testing` and `test`. Read per call, not cached. |
| `core/runtime_env.py` | The centralised rule itself. Unset `ENV` fails closed; a declared non-local `ENV` overrides `DEV_MODE` and logs the ignored flag at CRITICAL; no trading mode is ever read. |
| `core/config.py:340 _is_deployed_environment`, `core/dependencies.py:71 _is_production_safe`, `core/state.py:31 _is_production_safe` | Read `DEV_MODE` **and** `ENV`/`ENVIRONMENT` in the same function, with `None` defaults, so absence is distinguishable from a declaration. |
| `core/deployment_config.py:131` | `SANDBOX_MODE` defaults to `'true'` — sandbox on by default is the fail-safe direction. |
| `routers/billing.py:419` | `ENABLE_WEBHOOK_IP_VALIDATION` defaults to `'true'` — validation on by default, and the function is environment-aware. |
| `core/execution_engine.py:53 _synthetic_strategy_ids_allowed` | `ENV`/`ENVIRONMENT` with `None` defaults. |

### The one systemic weakness left, stated plainly

**`os.getenv("ENV", "development")` appears at 20 separate sites**, each inside its own
private `_is_production()` — `exchange_executor.py:58`, `credential_vault.py:43`,
`database_pool.py:79`, `distributed_idempotency.py:199`, `hard_quota_enforcer.py:41`,
`risk_manager.py:30`, `safety_monitor.py:24`, `usage_ledger.py:77`,
`subscription_dependencies.py:72`, `queue_manager.py:27`, `execution_worker.py:40`, and
others (full list in `staging/raw_P7Q7.txt`).

An **unset `ENV` therefore means "development" at all twenty**, which is the wrong default
direction for a guard whose job is to tighten in production. `runtime_env`,
`auth_middleware`, `config._is_deployed_environment` and `dependencies._is_production_safe`
get this right by defaulting to `None`/`""` and failing closed.

Not changed in this phase: inverting twenty independent production guards at once, in an
audit whose charter forbids weakening controls and whose remaining budget is a full
regression run, is a change whose risk exceeds the finding. The production task definitions
all set `ENV=production` explicitly, so the twenty defaults are not load-bearing today.
Recorded as the consolidation work this audit recommends, with `runtime_env` already in
place as the destination.

---

## Regression

`tests/test_environment_precedence_is_centralised.py` — **55 passed**
(`staging/raw_P8A1.txt`).

Structure, and why each part is there:

* **The conflicting variable is never cleared.** Every negative case sets `DEV_MODE=true`
  and *leaves it set* while asserting the refusal, and calls `_assert_dev_mode_is_still_set()`
  inside the assertion to prove it. RUNTIME-01 survived 131 API-03 tests precisely because
  those tests deleted `DEV_MODE` before exercising the detector, so the conflict they were
  meant to resolve was never present.
* **The four declared environments the brief names** — `production`, `staging`, `prod`,
  `live` — each against `DEV_MODE=true`, for the rule, both routers, `_build_service_client`
  and the docs URLs.
* **Positive controls**: `development`, `dev`, `local`, `test`, `testing` still get local
  semantics; an unset `ENV` with `DEV_MODE=true` still does; `ENABLE_DOCS` still overrides in
  both directions; `library._get_service_client()` still returns `None` under `ENV=testing`,
  which is why the rest of the suite is unaffected. Without these, every refusal above could
  be satisfied by refusing unconditionally.
* **Two AST sweeps that retire the class rather than the instances.**
  `test_no_site_decides_behaviour_from_dev_mode_without_consulting_the_environment` fails if
  any function reads `DEV_MODE` without also reading `ENV`/`ENVIRONMENT`, unless its file is
  on an allow-list with a stated reason (the rule's own module; module-level captures whose
  users are env-aware; `/health` and `/readiness`, which report the flag and grant nothing).
  `test_no_site_defaults_the_deployment_environment_from_a_trading_mode` fails if any `ENV`
  read takes its default from `get_vyomquant_mode`, `VYOMQUANT_MODE` or `AERORA_MODE`. Both
  sweeps are there because the two router defects were found by inventory, not by reasoning,
  and a fourth site would be found the same way or not at all.

### Falsification

A passing test proves nothing about a fix unless it fails when the fix is absent.
`staging/falsify_env_precedence_fixes.py` reinstates each **pre-fix predicate at its real
call site** — not inside `runtime_env`, so what is under test is whether the site asks the
right question — and asserts the old behaviour returns (`staging/raw_P8B2.txt`):

```
DETECTED  library._get_service_client, missing service-role key     -> returned None (the defect)
DETECTED  referral._get_service_client, missing service-role key    -> returned None (the defect)
DETECTED  library._get_service_client, create_client raises         -> returned None (the defect)
DETECTED  library._build_service_client, documented to raise        -> returned None (the defect)
DETECTED  main._get_docs_url, ENV unset + VYOMQUANT_MODE=paper      -> docs='/docs' redoc='/redoc'
DETECTED  main._get_docs_url, ENV=staging                           -> docs='/docs'

VERDICT: 6/6 defects reproduced by removing the fix
FALSIFICATION=ALL_DETECTED
```

### No existing test was weakened

* No pre-existing test asserts the docs/redoc/openapi URL behaviour (grep over `tests/`:
  only the new file).
* Targeted regression over the library, referral, rate-limit, Redis-fallback,
  test-token-fallback and execution-router-gate suites: **270 passed, 1 failed**
  (`staging/raw_P8D4.txt`) — the one failure being TEST-04 below, which is order-dependent,
  pre-existing, and not triggered by the repository's own collection order.

---

## TEST-04 — a tenth order-dependent failure, found while verifying this change

`tests/test_api03_rate_limit_storage_is_fail_closed.py::test_the_module_exposes_the_decision_it_actually_applied`

| Order | Result |
|---|---|
| that file alone | **34 passed** |
| `test_rate_limiter_fails_closed_in_production.py` then `test_api03_…` | **1 failed**, 51 passed |
| `test_api03_…` then `test_rate_limiter_fails_closed_in_production.py` (alphabetical — the repository's own order) | **52 passed** |

Mechanism: `test_rate_limiter_fails_closed_in_production.py` calls `importlib.reload` on
`backend_app.core.rate_limit`, which creates a **new `RateLimitConfig` class object**. The
asserting test imported `RateLimitConfig` at its own module-import time and so holds the
*old* class, while `rate_limit.config` is now an instance of the new one — so
`isinstance(rate_limit.config, RateLimitConfig)` is `False`. The failure message shows it
exactly: a well-formed `RateLimitConfig(env='testing', …)` that is "not a `RateLimitConfig`".

Same family as the eight in `staging/REGRESSION_ORDER_ISOLATION.md` (state left behind by a
module reload) but a different mechanism (class identity, not a live `Limiter` instance), so
the Task 6 conftest guard does not address it and was not extended to: resetting
`sys.modules` would break the reloading tests, which need the reload.

**Not fixed.** It does not occur in the repository's collection order, and the fix belongs
in the asserting test — compare the dataclass's fields or its `__name__` rather than its
identity. Recorded so it is not rediscovered as a mystery.

---

## Summary

| ID | Finding | Status |
|---|---|---|
| RUNTIME-01 | `DEV_MODE` overrode a declared production `ENV` in the rate limiter and lock | FIXED (Phase 5) |
| ENV-02 | `DEV_MODE` downgraded the service-role client; `ENV` never consulted | **FIXED** |
| ENV-03 | `load_dotenv()` resolves `backend_app/.env`; 7 placeholder credentials; placeholder passes truthiness validation; does not ship | REPORTED |
| ENV-04 | `PRODUCTION_ROUTER_ENABLED` defaults to mounting the execution router; code, docstring, task definitions and runbook disagree | REPORTED |
| ENV-05 | A trading mode supplied the deployment environment for `/docs` and `/redoc` | **FIXED** |
| ENV-06 | Sentry's `environment` tag is the trading mode, so production events are filed under `paper` | REPORTED |
| API-05 | `/openapi.json` served in every environment, including production | REPORTED |
| TEST-04 | Tenth order-dependent failure; not in the repository's collection order | REPORTED |
| — | 20 copies of `os.getenv("ENV", "development")`, so unset means development | REPORTED (consolidation recommended) |
| — | No auth-bypass and no rate-limit-bypass environment switch exists | POSITIVE |

---

# PHASE 7 ADDENDUM — ENV-01, ENV-03, ENV-04, ENV-06 AND API-05 CLOSED

> Everything above this line is the Phase 6 record and is left exactly as written,
> including the `REPORTED` statuses. Those statuses were true when they were written. The
> current state is this section, and the summary table at the very end of it supersedes the
> one above.

---

## ENV-03 — a non-empty string is not a credential · **FIXED**

### What the finding actually was

Two separate defects were recorded under one ID, and both are now closed.

**(a) The file that wins is not the file an operator edits.** `core/config.py` called a bare
`load_dotenv()`. With no argument that calls `find_dotenv()`, which walks up from the
**calling module's** directory — `backend_app/` — so it resolves `backend_app/.env` and
never reaches the repository-root `.env`. Both files exist in this working tree.

This is not hypothetical, and the proof is in this engagement's own record: in Phase 6,
`ENV=development` was added to the **root** `.env` to close a finding, the fix was recorded
as applied (CORRECTION-006), and it was **inert**. Corrected below.

**(b) A placeholder satisfied every credential check.** Every credential gate in this
codebase was spelled `if not value:`, and `bool("YOUR_SUPABASE_SERVICE_ROLE_KEY")` is
`True`. So the seven placeholders committed in `backend_app/.env` passed
`Settings.validate_required()`, `validate_config()` and the vault's own
`if not keys_string:` without complaint.

The sharpest instance was not in that file at all. `.env.example` ships
`STRIPE_SECRET_KEY=sk_live_your-stripe-secret-key`, and `billing._validate_keys` accepts a
Stripe key **only** when it starts with `sk_live_` — written precisely so a test-mode key
cannot grant a paid entitlement. The template's placeholder starts with `sk_live_`. A
prefix is not a credential either.

### The fix

**New module `backend_app/core/secret_validation.py`.** Four verdicts instead of two:

| Verdict | Meaning |
|---|---|
| `MISSING` | the variable is not set at all |
| `EMPTY` | set, but to nothing or whitespace |
| `PLACEHOLDER` | set to a template value nobody replaced |
| `USABLE` | not recognisably a fake |

`USABLE` is deliberately the weakest of the four claims, and the module says so: it
recognises *fakes*. It cannot tell a real Supabase service-role key from a random string of
the same shape and does not pretend to.

**`EMPTY` is kept distinct from `MISSING` on purpose.** They are different operator
mistakes: a missing variable was forgotten; an empty one was *set to nothing*, which in a
deployed environment almost always means a secret-manager reference that resolved to
nothing — a broken deployment wearing the costume of an unconfigured one. Collapsing both
into "falsy" hides the second.

**One vocabulary, not two.** `Settings.WEAK_JWT_SECRET_PATTERNS` was a private six-entry
tuple while the rest of the codebase had no placeholder vocabulary at all. It now *points
at* `PLACEHOLDER_SUBSTRINGS`. Two lists for one concept is how `detect_ml_nodes` ended up
with two spellings of "model node" and a gate that missed half of them. The entries the JWT
tests depend on — `default`, `password`, `insecure`, `dev-secret`, `changeme` — are all
still present; bare `example` was narrowed to `example.com` / `.org` / `.net`, because as a
substring it would condemn any real credential containing those letters and every
placeholder it was written for is a domain.

**Two tiers, from the code that already refuses.** Not a wish list:

* `REQUIRED_DEPLOYED_CREDENTIALS` — `MISSING`, `EMPTY` and `PLACEHOLDER` are all fatal:
  `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`,
  `JWT_SECRET`, `MASTER_ENCRYPTION_KEYS`. Evidence: `api_key_vault.py:80` refuses to arm the
  vault without the first and third; `api_key_vault.py:84` raises `EnvironmentError` and
  `credential_vault.py:267` raises `RuntimeError` without `MASTER_ENCRYPTION_KEYS`;
  `validate_required()` already required the rest.
* `PLACEHOLDER_REJECTED_CREDENTIALS` — optional, but a placeholder or an empty value is
  fatal: `DATABASE_URL`, `CREDENTIAL_VAULT_SALT`, `STRIPE_SECRET_KEY`,
  `STRIPE_WEBHOOK_SECRET`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`,
  `RAZORPAY_WEBHOOK_SECRET`.

**`Settings.assert_credentials_are_production_grade()`**, called from `main.lifespan`
immediately after the JWT check and **before any port is bound**. Production fails closed;
`_is_deployed_environment()` gates it exactly as the JWT check is gated, so a declared
development/test environment or an explicit `DEV_MODE=true` is unaffected. An unset `ENV`
counts as **deployed** — the same default as the JWT check, and deliberately the opposite of
the authentication gate, where defaulting to "development" was itself the defect
(BUG-002 / RUNTIME-01).

**Development keeps its documented behaviour, and keeps it visible.** The placeholders are
still *reported* locally — `print_config_status()` prints each unfit variable and its
verdict, with `(local environment — reported, not fatal)` beside them — they are simply not
fatal. The same configuration is a warning locally and a refusal in production.

**The dotenv resolution is unchanged, and now visible.** `load_dotenv(find_dotenv())` is
byte-for-byte what `load_dotenv()` already did: same frame, same walk, same file. Switching
the winner to the root `.env` would silently change which variables are set in every local
and CI process — a behaviour change dressed up as a documentation fix. What changed is that
the resolved path is now a named constant, `config.DOTENV_PATH`, printed at boot:

```
 Dotenv file: C:\aerora_quant_backend_updated_final1\backend_app\.env
```

`override=False` (dotenv's default) is retained: a real environment variable always beats
the file, which together with `.dockerignore` — it excludes `.env`, `.env.*` and
`backend_app/.env` — is what keeps the committed placeholders out of any image.

### No secret value can leak through the check

`describe_verdict(name, verdict)` **does not receive the value**, so it cannot disclose it;
that is asserted structurally as well as textually. The refusal names the variable and the
verdict, and explicitly declines to name the pattern it matched, because naming the pattern
writes a known substring of the configured value into the boot log and the crash reporter.
`test_the_refusal_names_no_part_of_any_value` checks every 9-character window of the
configured value against the message.

### Measured

`tests/test_env03_placeholder_secrets_are_invalid.py` + the pre-existing
`tests/test_jwt_secret_is_production_grade.py`: **170 passed** (`staging/suite_ENV3A.txt`).

The matrix the brief asked for, each variable × each verdict, with everything else left
configured so a refusal names exactly one variable:

| | `MISSING` | `EMPTY` | `PLACEHOLDER` | configured |
|---|---|---|---|---|
| each of the 6 required | refused | refused | refused | accepted |
| each of the 7 optional | accepted | refused | refused | accepted |

Three further measurements worth naming:

* `test_a_realistically_shaped_credential_is_accepted` is parametrised over all 13
  provider-shaped values (a Supabase project URL, two Supabase JWTs, a Fernet key, a pooler
  DSN, an `sk_live_` key, a `whsec_` secret, an `rzp_live_` id). It is the vocabulary's
  false-positive test, which is why it uses realistic shapes and not short random tokens.
* `test_no_committed_dotenv_file_contains_a_value_that_would_be_accepted` parses
  `backend_app/.env` and `.env.example` and requires **every** governed variable in them to
  classify `PLACEHOLDER`. Two things follow and both are wanted: no real credential is
  committed in either file, and the vocabulary genuinely covers this repository's own
  conventions, so a future template using a spelling it misses fails here instead of
  shipping.
* `test_validate_production_config_is_still_uncalled_and_says_so` — see below.

### CORRECTION-007 — a Phase 6 correction that was itself inert

Phase 6's CORRECTION-006 records adding `ENV=development` to the repository-root `.env`.
That edit had **no effect on any process**, because `backend_app/.env` is the file
`core/config.py` loads. The Phase 6 record is left in place; this is the correction to it.
The root `.env` is still not read, and the fix for the underlying confusion is the printed
`DOTENV_PATH` plus the test that pins which file wins.

### A second dead validator, reported not wired

`config.validate_production_config()` has **zero call sites**. Nothing in the application,
the entrypoints or CI invokes it; it has been dead since it was written. It is the second
instance of this shape in the audit — `safety_config.validate_safety_config_integrity`
(TRADE-06) was the first. It would also not have caught the finding it looks like it
covers: its test is `if not value`, satisfied by `YOUR_SUPABASE_SERVICE_ROLE_KEY`, and its
placeholder check applies to `JWT_SECRET` alone and only `logger.warning`s.

**Not wired up.** Its docstring now records that it is uncalled, and a test enforces the
pair: if somebody adds a call site the test fails and they must update the docstring; if
somebody removes the note while it is still uncalled, the test fails too. A validator
nothing calls is not a control, however correct its body is.

---

## ENV-04 — `PRODUCTION_ROUTER_ENABLED` requires explicit enablement · **FIXED**

### The defect, restated precisely

```python
# core/safety_config.py:131   AND   core/feature_flags.py:39  — two copies of one line
PRODUCTION_ROUTER_ENABLED: bool = os.getenv("PRODUCTION_ROUTER_ENABLED", "True").lower() == "true"
```

The flag's **absence mounted** `execution_router` at `/api/execution`. That is the
fail-**open** direction for a switch that exists to keep an execution surface off, and
"absent" is the normal state of a new environment, a rollback to an older task definition,
or any local process.

Nine sources stated an answer and they did not agree:

| Source | Said |
|---|---|
| `core/feature_flags.py` docstring | `PRODUCTION_ROUTER_ENABLED: False (unverified safety)` |
| the code one line below it | default **`"True"`** |
| `ecs-task-definition-full.json` | `"False"` |
| `new_task_def.json` | `"False"` |
| `td15.json` | `"False"` |
| `infra/rollback/taskdef-vyomquant-api-145-pre-domain.json` | `"False"` |
| `.env.example` | `False` |
| `deployment_go_no_go.md` | **`True`** |
| `safety_config.validate_safety_config_integrity()` | raised in production unless the router was **ENABLED** |

That last row is the one that settles the question. It demanded the exact opposite of every
other source — it treated a *disabled* execution router as evidence of environment-variable
tampering and refused to start — and it has **zero call sites**, so it had never run. Had it
ever been wired, it would have refused to boot all four task definitions this repository
ships.

### The measurement that changed the fix

The code default was **not** what mounted the router on this machine. `backend_app/.env` —
git-ignored, loaded by `core/config.py` *before* `safety_config` reads the flag — contained
`PRODUCTION_ROUTER_ENABLED=True`. Measured directly, with the variable popped from the
process environment:

```
staging/probe_env04_mount.py unset
  SETTING=unset
  PREDICATE=True
  EXECUTION_SIGNAL_MOUNTED=True
  ANY_EXECUTION_PATH=['/api/execution/signal']
```

So flipping the code default alone would have changed nothing about what this machine does,
and the local lane would have gone on logging
`INFO:Algo22: Production execution router ENABLED` while the report claimed the opposite.
ENV-03 and ENV-04 are the same file, seen twice.

### The fix

**One authoritative definition**, `safety_config.production_router_enabled()`, read by both
`ExecutionFlags` classes. Off unless `PRODUCTION_ROUTER_ENABLED=true` is set explicitly.

**`ENV` does not enter into it**, on purpose. Making the default depend on the environment
would give the flag three behaviours to reason about instead of one, and it is exactly the
shape that produced RUNTIME-01. The flag is the only switch and it is read the same way
everywhere.

**The enabling vocabulary was NOT widened.** `1`, `yes` and `on` are *not* enabling
spellings — widening them is a permissiveness increase on the one switch whose purpose is
explicit enablement. They are not silently ignored either: an unrecognised value is refused
**and logged by name**, so `PRODUCTION_ROUTER_ENABLED=1` leaves the router off and says so
rather than leaving an operator to believe they enabled something.

**The contradictory assertion was removed** from `validate_safety_config_integrity`, with
the reasoning recorded in its docstring. The function stays uncalled and stays documented as
uncalled; it must not be wired as-is, because its live-trading branch compares
`VYOMQUANT_MODE` / `AERORA_MODE` against `"live_trading"` — the `SystemMode` *enum* value,
not a value either variable ever holds (TRADE-06).

**Every stating source now states the same value.** `deployment_go_no_go.md` corrected from
`True` to `False` with a dated note; `backend_app/.env` aligned to `False` with a comment
explaining that it was the actual cause locally; the four task definitions already said
`"False"` and are now pinned by test; the `feature_flags` docstring corrected.

### What is actually behind the flag — stated accurately

`execution_router` exposes exactly one endpoint, `POST /api/execution/signal`, and it
constructs `ExecutionEngine()` with **no `portfolio_state`** on the line *before* its
`try`, so it raises `ValueError` and returns **500 on every request** without reaching any
venue. That is not a new measurement — `tests/test_execution_paths_that_claim_an_exchange.py`
established it in an earlier phase.

**So ENV-04 is a configuration-coherence and fail-open-direction defect, not a live-order
exposure, and it is not reported as one.** The direction still matters: the endpoint is an
order-submission surface by construction, and one repair to that constructor is all that
separates it from `execute_trade`.

### Measured

`tests/test_env04_production_router_requires_explicit_enablement.py`: **45 passed**
(`staging/suite_ENV4B.txt`).

The nine cells the brief asked for — three environments × unset / `false` / `true`:

| `ENV` | unset | `false` | `true` |
|---|---|---|---|
| `production` | **off** | off | on |
| `staging` | **off** | off | on |
| `development` | **off** | off | on |

Before the fix every cell in the `unset` column was **on**.

Four layers, because a correct predicate next to an unconditional mount would still be
wrong:

1. The nine-cell predicate matrix (in-process, variable monkeypatched away *after* dotenv
   has run — which is how `unset` is measured at all).
2. `test_there_is_exactly_one_place_the_default_is_decided` — an AST sweep over
   `backend_app/**/*.py` for any `os.getenv("PRODUCTION_ROUTER_ENABLED", ...)` outside
   `production_router_enabled`.
3. `test_the_router_is_mounted_only_under_the_flag` — every `include_router(execution_router,
   …)` in `main.py` must sit inside an `if` whose test is the flag, and the counts must
   match.
4. End-to-end, in a subprocess, booting the real `app` and reading the route table:
   `false` → `/api/execution/signal` absent, `true` → present.

**Two honest limits on layer 4, both measured rather than assumed:**

* The `unset` cell is not measurable end-to-end, because `core/config.py`'s `load_dotenv()`
  injects `backend_app/.env` into `os.environ` before the flag is read — so removing the
  variable produces *whatever that file says*, not an unset flag. `unset → off` is proven
  by layers 1 and 3 instead.
* `ENV=production` cannot be booted on this machine, and the reason is a control working
  correctly: `core/rate_limit` refuses to start outside an explicitly local environment
  with no shared store, because in-memory rate limiting is per-process and would multiply
  the effective limit by the replica count (API-03). Measured:
  `RuntimeError: FATAL: rate limiting has no shared store. REDIS_URL is set but unreachable
  (Timeout connecting to server). (ENV=production, REDIS_URL=set)`. Environment-independence
  of the mount decision is established by layer 1 rather than assumed here.

---

## API-05 — the schema follows the pages it feeds · **FIXED**

### The intended policy, determined

There are two surfaces and they were being governed by two different rules by accident
rather than by decision:

* `/docs` and `/redoc` — the **interactive, browser-executable consoles**. Restricted to
  `runtime_env.local_state_is_acceptable()` by the ENV-05 fix.
* `/openapi.json` — the **machine-readable schema those consoles render from**. It
  enumerates every path, method, parameter and model in the application.

`_get_openapi_url()` consulted **no** environment variable at all, and no task definition
sets `ENABLE_OPENAPI`. So production hid the console and published the schema the console
renders from: the hiding and the publishing withheld and disclosed the same surface.

**Policy: one rule for both, with a separate explicit override per surface.**
`local_state_is_acceptable()` decides; `ENABLE_DOCS` and `ENABLE_OPENAPI` are consulted
first and work in both directions. The two overrides are independent, because "publish the
schema for a machine consumer but keep the browser console off" is a legitimate
configuration and has to be expressible.

**Documentation is not disabled everywhere.** A declared development or test environment —
and any machine that says `DEV_MODE=true` — serves all three exactly as before. The change
is production and staging, and only by default.

### Why it was safe to change now, when the earlier pass declined

The earlier pass deferred it because unmounting a schema can break a generated client, a
contract test or an API-gateway import. Measured since:

* **Nothing in this repository fetches `/openapi.json` over HTTP.** The frontend mentions it
  only in comments (`algo22-terminal/.env.production` and two test docstrings); no CI
  workflow references it; and `tests/test_route_contract.py` reads `app.openapi()`
  **in process**, so it is unaffected by this URL.
* CloudFront distribution `EEOXECPHQ8SR0` **does** carry an explicit `/openapi.json` cache
  behaviour (`infra/rollback/cloudfront-EEOXECPHQ8SR0-pre-domain.json`,
  `scripts/update_cloudfront_policies.py`, `infra/dns.md`), and `core/tenant_middleware.py`
  and `core/subscription_middleware.py` both list the path as public. **Those are routing
  and auth facts, and they predate the ENV-05 change**: they exist because `/docs` used to
  be served in production and had to be reachable unauthenticated for the page to work.
  Once the console became local-only, the schema's publication lost its reason.

**Consequence, spelled out.** In production `/openapi.json` now answers 404 and the
CloudFront behaviour for it becomes a no-op forwarding to a 404. Neither the CDN behaviour
nor the middleware allowlists are removed here — deleting a deployed cache behaviour is an
infrastructure change, and a routed path that answers 404 is harmless.
`ENABLE_OPENAPI=true` restores the previous behaviour with one variable.

### Measured

`tests/test_api05_documentation_exposure_matrix.py`, the complete matrix: 10 environments
(9 declared + unset) × 2 `DEV_MODE` × 3 trading modes = **60 cells**, each asserting all
three URLs. The expected value is computed from `local_state_is_acceptable()` rather than
tabulated by hand, because a 60-row hand-written table is a second implementation of the
policy and would drift from it.

| | `/docs` | `/redoc` | `/openapi.json` |
|---|---|---|---|
| `production` / `staging` / `prod` / `live`, any `DEV_MODE`, any trading mode | — | — | **—** (was `/openapi.json`) |
| `development` / `dev` / `local` / `test` / `testing` | `/docs` | `/redoc` | `/openapi.json` |
| `ENV` unset + `DEV_MODE=true` | `/docs` | `/redoc` | `/openapi.json` |
| `ENV` unset, no `DEV_MODE` | — | — | — |

`DEV_MODE=true` is left **set** in the negative cells on purpose: a declared non-local `ENV`
must beat it, and a test that cleared it would be asserting something else — which is
exactly how RUNTIME-01 survived 131 API-03 tests.

`test_no_documentation_helper_reads_a_trading_mode` is structural: none of the four helpers
may mention `VYOMQUANT_MODE`, `AERORA_MODE` or `get_vyomquant_mode` in an executable
statement (docstrings are excluded, because they discuss the defect by name).

---

## ENV-06 — Sentry records two dimensions, not one · **FIXED**

```python
# main.lifespan, before
initialize_sentry(dsn=..., environment=get_vyomquant_mode("production"), release="2.4.0")
```

A **trading mode** supplying Sentry's **`environment`** tag. With the documented production
setting `VYOMQUANT_MODE=paper`, every production event was filed under an environment called
`paper` — mixed in with developer machines in paper mode — and **no environment called
`production` existed** for an alert rule, a release-health query or an issue filter to
select. The same substitution as API-03 (`core/rate_limit`) and the `redis_manager` fix, on
the reporting side.

| Dimension | Values | What it says |
|---|---|---|
| deployment environment | `development` / `testing` / `staging` / `production` | where the process runs, how many replicas, whose data |
| trading mode | `safe` / `paper` / `live` | what the process may do with money |

**Both are now recorded, independently.** `environment` comes from
`runtime_env.deployment_environment()`, which reads `ENV` / `ENVIRONMENT` and never a
trading mode. The trading mode is attached as its own global scope tag,
`trading_mode`, so every event and transaction carries it and it stays filterable.

Nothing is lost by separating them, which is the point: an event in
`environment=production, trading_mode=paper` is a different thing from one in
`environment=development, trading_mode=paper`, and the single-tag version could not say
which it had.

`initialize_sentry`'s `environment` default changed from `"production"` to `"unknown"`. A
caller that does not say where it runs should produce a visibly unlabelled stream rather
than a convincing-looking production one — an alert rule cannot tell a mislabelled event
from a real one, but it can tell `unknown`.

### Measured

In `tests/test_api05_documentation_exposure_matrix.py` (same file, because it is the same
"separate the two dimensions" property):

* 12 cells (4 environments × 3 trading modes) asserting that `environment` carries the
  deployment environment and the `trading_mode` tag carries the trading mode, captured
  from a spy on `sentry_sdk.init` / `sentry_sdk.set_tag` — nothing is sent anywhere.
* `test_startup_passes_the_deployment_environment_not_the_trading_mode` parses
  `main.lifespan` with `ast`: the `environment=` keyword must contain
  `deployment_environment()` and must **not** contain `get_vyomquant_mode`, and
  `trading_mode=` must be where `get_vyomquant_mode` appears. A correct function called with
  the wrong argument is still the defect.
* `test_nothing_else_passes_a_trading_mode_as_a_deployment_environment` sweeps every
  `environment=` keyword under `backend_app/**/*.py` for `get_vyomquant_mode` /
  `VYOMQUANT_MODE` — the class, not this one instance. Currently zero.

---

## ENV-01 — connection pooling, proven without a venue · **FIXED**

Not an environment-precedence finding, but recorded here because it was carried in this
document's summary table.

`tests/test_exchange_safety_fix.py::test_connection_pooling_safety` had **no mocking at
all**. `get_or_create_exchange("user_123", "binance", "key1", "secret1")` constructs a real
`ccxt.pro.binance` and `ConnectionEngine.connect()` awaits `load_markets()`, which for a
credentialed binance client issues
`GET https://api.binance.com/sapi/v1/capital/config/getall` with `X-MBX-APIKEY: key1`. The
test made an **outbound authenticated request to a live production venue from the unit
lane**, using a credential invented in the test body, and was red either way: with network
it got `AuthenticationError: binance {"code":-2008,"msg":"Invalid Api-Key ID."}`, without
network `ExchangeNotAvailable`. Neither outcome says anything about pooling.

### The boundary, named

Two things were tangled in one assertion:

1. **Connection-pooling correctness** — does `_exchange_pool` key, reuse, isolate and evict
   correctly? Pure in-process bookkeeping: a SHA-256 of `[user_id, exchange_id]`, a dict, an
   `asyncio.Lock`, and `pop` + `close` on release. No venue, no credential, no network, and
   fully decidable in the unit lane. **Tested.**
2. **Venue authentication / connectivity** — will Binance accept this key, is the API
   reachable, does `load_markets()` succeed? A statement about a third party and a secret,
   not decidable in the unit lane, and not makeable deterministic without shipping a
   credential or depending on the Internet. **Not tested, and not claimed.**

Conflating them made a pooling regression indistinguishable from an expired key.

### The fix

The real `get_or_create_exchange`, the real `ConnectionEngine.connect()` (config assembly,
sandbox branch, retry loop, failure teardown) and the real `release_exchange` all run. Only
the venue *client class* is replaced, by `_ControlledVenueClient` — a controlled in-process
transport that receives the same assembled `config` dict the real class does, so **which
credentials reached which client is observable rather than assumed**, and answers
`load_markets()` locally. The substitution is made on `connection_engine.ccxt` rather than
by mutating `ccxt.pro`, so it is local to the module and the test. `_exchange_pool` is
snapshotted and restored, because a leaked entry is the order-dependence class of defect
this suite already found once in Phase 6.

Nothing skipped, xfailed, credentialed or weakened. The assertions are **stronger** than the
originals, which only compared object identity — identity alone cannot distinguish "isolated
correctly" from "both users got the same wrong client", so the test now reads back the
credentials each pooled client was constructed with, and counts how many venue clients were
ever constructed.

Three tests added beside it:

* `test_a_refused_venue_handshake_is_never_pooled` — the one place the two concerns
  legitimately touch. The venue's verdict is an **injected input**, and what the pool does
  with a refusal is the measurement: nothing is pooled, and the client that was constructed
  is closed rather than leaked (the CE-3 "Unclosed client session" leak). This is exactly
  the separation the original test collapsed, which took a *real* `AuthenticationError`
  from a *real* venue as its accidental outcome.
* `test_concurrent_first_calls_create_exactly_one_pooled_client` — eight concurrent callers
  for one `(user, exchange)` pair, with a `load_markets` that actually suspends. One client
  constructed, one handshake. The `_pool_lock` is only observable with concurrency.
* `test_pool_key_is_the_user_exchange_pair_not_the_credential` — see CONN-01 below.

**Measured:** `tests/test_exchange_safety_fix.py` + two neighbouring exchange suites:
**36 passed in 34.56s** (`staging/suite_ENV1A.txt`), no network.

### CONN-01 (new, P2) — a rotated credential does not take effect until restart

Found while writing the deterministic test, and recorded as a characterisation test rather
than an endorsement.

The pool key is `sha256(json.dumps([user_id, exchange_id]))` — it does **not** include the
credential. So once a client is pooled for `(user, exchange)`, a later call passing
different credentials for that pair is served the **already-pooled** client and the new
credentials are discarded. Measured: after
`get_or_create_exchange("user_rot", "binance", "NEW-key", "NEW-secret")`, the returned
client still carries `old-key`, and no client with `NEW-key` was ever constructed.

`routers/exchange.py::delete_connection` calls `release_exchange` (its comment: *"Evict from
the exchange pool so the stale socket is closed"*). `store_keys` — the `POST /api/exchange/keys`
create-or-rotate handler — **does not**. So a credential rotation does not take effect
in-process until the connection is deleted or the process restarts.

**What this is not.** It is not cross-tenant credential mixing: the key still separates
users, and `test_connection_pooling_safety` proves each user's client carries only its own
secret. A superseded key is also rejected by the venue itself. The exposure is "rotation is
silently ineffective until restart" — an operational correctness defect, not a tenant-isolation
breach. **Reported, not fixed:** the fix is one `release_exchange` call in `store_keys`, but
it closes a live socket on a path that currently cannot, and belongs with whoever owns the
key-rotation flow.

---

## Phase 7 summary table — supersedes the one above

| ID | Finding | Status |
|---|---|---|
| RUNTIME-01 | `DEV_MODE` overrode a declared production `ENV` in the rate limiter and lock | FIXED (Phase 5) |
| ENV-02 | `DEV_MODE` downgraded the service-role client; `ENV` never consulted | FIXED (Phase 6) |
| ENV-05 | A trading mode supplied the deployment environment for `/docs` and `/redoc` | FIXED (Phase 6) |
| **ENV-01** | Unmocked authenticated request to `api.binance.com` from the unit lane | **FIXED (Phase 7)** |
| **ENV-03** | `backend_app/.env` wins silently; placeholders satisfy every credential check | **FIXED (Phase 7)** |
| **ENV-04** | `PRODUCTION_ROUTER_ENABLED` absence mounted the execution router; nine sources disagreed | **FIXED (Phase 7)** |
| **ENV-06** | Sentry's `environment` tag was the trading mode | **FIXED (Phase 7)** |
| **API-05** | `/openapi.json` served in every environment, including production | **FIXED (Phase 7)** |
| **CONN-01** | Exchange pool key omits the credential; rotation ineffective until restart | **REPORTED (new, P2)** |
| TEST-04 | Tenth order-dependent failure; not in the repository's collection order | REPORTED |
| — | `config.validate_production_config()` has zero call sites (2nd dead validator) | REPORTED (Phase 7) |
| — | 20 copies of `os.getenv("ENV", "development")`, so unset means development | REPORTED (consolidation recommended) |
| — | No auth-bypass and no rate-limit-bypass environment switch exists | POSITIVE |
